"""Count native episodes by (source_object, destination) condition.

Read-only; does not load robot hardware. Useful while expanding Manual train data.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from sharedautonomy.data.recorder import METADATA_FILENAME

# Task-card conditions for shape_pick_place_v1 (object_id × destination_id).
EXPECTED_OBJECTS = ("yellow", "red", "blue")
EXPECTED_DESTINATIONS = ("up", "down")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize how many native episodes exist per (source_object, destination). "
            "Scans run directories under --runs-root (default: outputs/runs)."
        )
    )
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=Path("outputs/runs"),
        help="Root directory that contains <run_id>/episode/ folders (default: outputs/runs)",
    )
    parser.add_argument(
        "--run-glob",
        default="shape-pick-place-*",
        help="Glob applied to run directory names under --runs-root (default: shape-pick-place-*)",
    )
    parser.add_argument(
        "--task-id",
        default=None,
        help="Only count episodes whose metadata.task_id matches this value",
    )
    parser.add_argument(
        "--success-only",
        action="store_true",
        help="Only count episodes with metadata.success == true",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a text table",
    )
    return parser.parse_args()


def discover_episode_dirs(runs_root: Path, run_glob: str) -> list[Path]:
    """Return episode directories that contain metadata.json, sorted by path."""
    if not runs_root.is_dir():
        return []
    episode_dirs: list[Path] = []
    for run_dir in sorted(runs_root.glob(run_glob)):
        if not run_dir.is_dir():
            continue
        episode_dir = run_dir / "episode"
        if (episode_dir / METADATA_FILENAME).is_file():
            episode_dirs.append(episode_dir)
    return episode_dirs


def load_episode_meta(episode_dir: Path) -> dict[str, Any] | None:
    """Load the nested metadata object from metadata.json, or None on failure."""
    meta_path = episode_dir / METADATA_FILENAME
    try:
        envelope = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    metadata = envelope.get("metadata")
    if not isinstance(metadata, dict):
        return None
    return metadata


def condition_key(source_object: str | None, destination: str | None) -> str:
    src = source_object if source_object else "?"
    dst = destination if destination else "?"
    return f"{src}->{dst}"


def main() -> int:
    args = parse_args()
    episode_dirs = discover_episode_dirs(args.runs_root, args.run_glob)
    if not episode_dirs:
        print(
            f"No episodes found under {args.runs_root} matching run-glob={args.run_glob!r}",
            file=sys.stderr,
        )
        return 1

    counts: Counter[str] = Counter()
    success_counts: Counter[str] = Counter()
    skipped: list[str] = []
    rows: list[dict[str, Any]] = []

    for episode_dir in episode_dirs:
        metadata = load_episode_meta(episode_dir)
        if metadata is None:
            skipped.append(f"{episode_dir}: unreadable {METADATA_FILENAME}")
            continue
        if args.task_id is not None and metadata.get("task_id") != args.task_id:
            continue
        success = metadata.get("success")
        if args.success_only and success is not True:
            continue

        source_object = metadata.get("source_object")
        destination = metadata.get("destination")
        key = condition_key(
            None if source_object is None else str(source_object),
            None if destination is None else str(destination),
        )
        counts[key] += 1
        if success is True:
            success_counts[key] += 1
        rows.append(
            {
                "run_id": metadata.get("run_id"),
                "episode_id": metadata.get("episode_id"),
                "episode_dir": str(episode_dir),
                "source_object": source_object,
                "destination": destination,
                "condition": key,
                "success": success,
                "task_id": metadata.get("task_id"),
                "collection_mode": metadata.get("collection_mode"),
            }
        )

    expected_keys = [
        condition_key(obj, dst) for obj in EXPECTED_OBJECTS for dst in EXPECTED_DESTINATIONS
    ]
    # Keep expected order first, then any unexpected keys.
    ordered_keys = list(expected_keys)
    for key in sorted(counts):
        if key not in ordered_keys:
            ordered_keys.append(key)

    summary_rows = [
        {
            "condition": key,
            "count": int(counts.get(key, 0)),
            "success_true": int(success_counts.get(key, 0)),
        }
        for key in ordered_keys
        if key in expected_keys or counts.get(key, 0) > 0
    ]

    payload = {
        "runs_root": str(args.runs_root),
        "run_glob": args.run_glob,
        "task_id_filter": args.task_id,
        "success_only": bool(args.success_only),
        "episode_count": len(rows),
        "conditions": summary_rows,
        "episodes": rows,
        "skipped": skipped,
    }

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=True), flush=True)
    else:
        print(f"runs_root: {args.runs_root}")
        print(f"run_glob:  {args.run_glob}")
        if args.task_id is not None:
            print(f"task_id:   {args.task_id}")
        if args.success_only:
            print("filter:    success_only")
        print(f"episodes:  {len(rows)}")
        print()
        print(f"{'condition':<16} {'count':>5} {'success':>8}")
        print("-" * 32)
        for item in summary_rows:
            print(f"{item['condition']:<16} {item['count']:>5} {item['success_true']:>8}")
        print("-" * 32)
        print(f"{'TOTAL':<16} {len(rows):>5} {sum(success_counts.values()):>8}")
        if skipped:
            print()
            print("skipped:")
            for item in skipped:
                print(f"  - {item}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
