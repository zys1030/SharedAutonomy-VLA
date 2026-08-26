"""Batch-validate native episode directories via check_episode_dir.

Read-only; does not load robot hardware. Prints a one-line summary per episode
and exits non-zero if any episode has hard issues (same semantics as check_episode.py).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sharedautonomy.data import check_episode_dir, episode_check_report_to_dict
from sharedautonomy.data.recorder import METADATA_FILENAME


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run native episode checks over many run directories and print a compact "
            "PASS/FAIL summary. Warnings alone still count as PASS (same as check_episode.py)."
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
        "episode_dirs",
        nargs="*",
        type=Path,
        help=(
            "Optional explicit episode directories (each with metadata.json). "
            "When provided, --runs-root / --run-glob are ignored."
        ),
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first failing episode",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON array of per-episode check payloads on stdout",
    )
    parser.add_argument(
        "--require-success",
        action="store_true",
        help=(
            "Treat metadata.success != true as a hard failure "
            "(check_episode alone does not enforce task success)"
        ),
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


def resolve_episode_dirs(args: argparse.Namespace) -> list[Path]:
    if args.episode_dirs:
        resolved: list[Path] = []
        for path in args.episode_dirs:
            if path.name != "episode" and (path / "episode" / METADATA_FILENAME).is_file():
                resolved.append(path / "episode")
            else:
                resolved.append(path)
        return resolved
    return discover_episode_dirs(args.runs_root, args.run_glob)


def _read_condition(episode_dir: Path) -> tuple[str | None, str | None, bool | None]:
    meta_path = episode_dir / METADATA_FILENAME
    try:
        envelope = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None, None
    metadata = envelope.get("metadata")
    if not isinstance(metadata, dict):
        return None, None, None
    source = metadata.get("source_object")
    destination = metadata.get("destination")
    success = metadata.get("success")
    return (
        None if source is None else str(source),
        None if destination is None else str(destination),
        None if success is None else bool(success),
    )


def _format_sync(sync_warning_counts: dict[str, int]) -> str:
    if not sync_warning_counts:
        return "none"
    return ",".join(f"{name}={count}" for name, count in sorted(sync_warning_counts.items()))


def main() -> int:
    args = parse_args()
    episode_dirs = resolve_episode_dirs(args)
    if not episode_dirs:
        print("No episode directories to check.", file=sys.stderr)
        return 1

    results: list[dict[str, Any]] = []
    fail_count = 0

    for episode_dir in episode_dirs:
        report = check_episode_dir(episode_dir)
        source_object, destination, success = _read_condition(episode_dir)
        condition = (
            f"{source_object or '?'}->{destination or '?'}"
            if source_object is not None or destination is not None
            else "?"
        )

        extra_issues: list[str] = []
        if args.require_success and success is not True:
            extra_issues.append(f"metadata.success={success!r} (require_success)")

        ok = bool(report.ok) and not extra_issues
        if not ok:
            fail_count += 1

        cam = report.camera_coverage
        payload = episode_check_report_to_dict(report)
        payload["condition"] = condition
        payload["source_object"] = source_object
        payload["destination"] = destination
        payload["metadata_success"] = success
        payload["ok"] = ok
        if extra_issues:
            payload["issues"] = list(payload.get("issues") or []) + extra_issues
        results.append(payload)

        if not args.json:
            status = "PASS" if ok else "FAIL"
            print(
                f"[{status}] {report.run_id:<36} {condition:<14} "
                f"steps={report.step_count:<4} "
                f"wrist={cam.wrist_fraction:.0%} "
                f"ext={cam.external_fraction:.0%} "
                f"sync={_format_sync(report.sync_warning_counts)} "
                f"success={success}",
                flush=True,
            )
            for issue in report.issues:
                print(f"         issue: {issue}", flush=True)
            for issue in extra_issues:
                print(f"         issue: {issue}", flush=True)
            for warning in report.warnings:
                print(f"         warn:  {warning}", flush=True)

        if args.fail_fast and not ok:
            break

    if args.json:
        print(
            json.dumps(
                {
                    "episode_count": len(results),
                    "fail_count": fail_count,
                    "ok": fail_count == 0,
                    "episodes": results,
                },
                indent=2,
                ensure_ascii=True,
            ),
            flush=True,
        )
    else:
        print()
        print(f"checked: {len(results)}  pass: {len(results) - fail_count}  fail: {fail_count}")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
