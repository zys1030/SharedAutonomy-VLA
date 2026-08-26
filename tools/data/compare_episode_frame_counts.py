"""Compare per-episode frame counts between two exported (or native) pools.

Read-only. Does not load images, videos, or robot hardware. Default pairing
is RQ2 rotation Manual vs SA: totals over all 70, paired diffs on 1-27 and
51-70 (skip random 28-50). Pair by native run number, not LeRobot episode_index.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sharedautonomy.data.recorder import METADATA_FILENAME

MANIFEST_FILENAME = "export_manifest.json"

DEFAULT_MANUAL_ROOT = Path("outputs/datasets/shape_pick_place_block_rot_manual_70ep")
DEFAULT_SA_ROOT = Path("outputs/datasets/shape_pick_place_block_rot_SA_70ep")
DEFAULT_MANUAL_GLOB = "shape-pick-place-block-rot-manual-*"
DEFAULT_SA_GLOB = "shape-pick-place-block-rot-SA-*"
DEFAULT_PAIR_RANGES = ((1, 27), (51, 70))


@dataclass(frozen=True, slots=True)
class EpisodeFrames:
    run_id: str
    run_index: int
    frames: int
    source: str
    duration_s: float | None = None


@dataclass(frozen=True, slots=True)
class PairRow:
    run_index: int
    left_run_id: str
    right_run_id: str
    left_frames: int
    right_frames: int
    diff_frames: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare total and paired episode frame counts for two 70-ep snapshots. "
            "auto: LeRobot meta/episodes length, plus manifest/native run ids."
        )
    )
    parser.add_argument(
        "--left-root",
        type=Path,
        default=DEFAULT_MANUAL_ROOT,
        help="Left dataset root (Manual)",
    )
    parser.add_argument(
        "--right-root",
        type=Path,
        default=DEFAULT_SA_ROOT,
        help="Right dataset root (SA)",
    )
    parser.add_argument("--left-name", default="manual", help="Label for the left pool")
    parser.add_argument("--right-name", default="sa", help="Label for the right pool")
    parser.add_argument("--runs-root", type=Path, default=Path("outputs/runs"))
    parser.add_argument(
        "--left-runs-root",
        type=Path,
        default=None,
        help="Native run root for the left pool (default: --runs-root)",
    )
    parser.add_argument(
        "--right-runs-root",
        type=Path,
        default=None,
        help="Native run root for the right pool (default: --runs-root)",
    )
    parser.add_argument("--left-run-glob", default=DEFAULT_MANUAL_GLOB)
    parser.add_argument("--right-run-glob", default=DEFAULT_SA_GLOB)
    parser.add_argument(
        "--source",
        choices=("auto", "lerobot", "manifest", "native"),
        default="auto",
        help="Default source for both pools: parquet + manifest/native ids",
    )
    parser.add_argument(
        "--left-source",
        choices=("auto", "lerobot", "manifest", "native"),
        default=None,
        help="Override --source for the left pool",
    )
    parser.add_argument(
        "--right-source",
        choices=("auto", "lerobot", "manifest", "native"),
        default=None,
        help="Override --source for the right pool",
    )
    parser.add_argument(
        "--pair-range",
        action="append",
        default=None,
        metavar="A-B",
        help="Inclusive run-index range to pair (repeatable). Default: 1-27 and 51-70",
    )
    parser.add_argument(
        "--hz",
        type=float,
        default=10.0,
        help="Control rate used to convert frames to seconds",
    )
    parser.add_argument("--bin-width", type=int, default=20, help="Histogram bin width in frames")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser.parse_args()


def parse_run_index(run_id: str) -> int:
    suffix = run_id.rsplit("-", 1)[-1]
    if not suffix.isdigit():
        raise ValueError(f"run_id {run_id!r} does not end with a numeric index")
    return int(suffix)


def parse_pair_ranges(raw: list[str] | None) -> tuple[tuple[int, int], ...]:
    if not raw:
        return DEFAULT_PAIR_RANGES
    ranges: list[tuple[int, int]] = []
    for item in raw:
        start_s, sep, end_s = item.partition("-")
        if sep != "-" or not start_s.isdigit() or not end_s.isdigit():
            raise ValueError(f"pair range must look like 1-27, got {item!r}")
        start, end = int(start_s), int(end_s)
        if start > end:
            raise ValueError(f"pair range start > end: {item!r}")
        ranges.append((start, end))
    return tuple(ranges)


def load_info_totals(dataset_root: Path) -> tuple[int, int] | None:
    info_path = dataset_root / "meta" / "info.json"
    if not info_path.is_file():
        return None
    try:
        payload = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    episodes = payload.get("total_episodes")
    frames = payload.get("total_frames")
    if episodes is None or frames is None:
        return None
    return int(episodes), int(frames)


def load_from_manifest(dataset_root: Path) -> dict[int, EpisodeFrames]:
    manifest_path = dataset_root / MANIFEST_FILENAME
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    episodes = payload.get("episodes")
    if not isinstance(episodes, list):
        raise ValueError(f"{manifest_path}: missing episodes list")
    by_index: dict[int, EpisodeFrames] = {}
    for item in episodes:
        if not isinstance(item, dict):
            continue
        run_id = str(item["run_id"])
        run_index = parse_run_index(run_id)
        frames = int(item["step_count"])
        if run_index in by_index:
            raise ValueError(f"{manifest_path}: duplicate run index {run_index}")
        by_index[run_index] = EpisodeFrames(
            run_id=run_id,
            run_index=run_index,
            frames=frames,
            source="manifest",
        )
    return by_index


def _native_duration_s(metadata: dict[str, Any]) -> float | None:
    raw = metadata.get("duration_s")
    if raw is None:
        return None
    return float(raw)


def _load_native_episode(episode_dir: Path) -> EpisodeFrames | None:
    meta_path = episode_dir / METADATA_FILENAME
    try:
        envelope = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if str(envelope.get("status", "")) != "completed":
        return None
    metadata = envelope.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("success") is not True:
        return None
    run_id = str(metadata["run_id"])
    return EpisodeFrames(
        run_id=run_id,
        run_index=parse_run_index(run_id),
        frames=int(envelope.get("step_count", 0)),
        source="native",
        duration_s=_native_duration_s(metadata),
    )


def load_from_lerobot_meta(dataset_root: Path) -> dict[int, EpisodeFrames]:
    episode_root = dataset_root / "meta" / "episodes"
    paths = sorted(episode_root.rglob("*.parquet")) if episode_root.is_dir() else []
    if not paths:
        return {}
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise ImportError("reading meta/episodes parquet requires pyarrow") from exc
    by_index: dict[int, EpisodeFrames] = {}
    for path in paths:
        table = pq.read_table(path, columns=["episode_index", "length"])
        for episode_index, length in zip(
            table.column("episode_index").to_pylist(),
            table.column("length").to_pylist(),
            strict=True,
        ):
            run_index = int(episode_index) + 1
            if run_index in by_index:
                raise ValueError(f"{path}: duplicate episode_index {episode_index}")
            by_index[run_index] = EpisodeFrames(
                run_id=f"run-{run_index:03d}",
                run_index=run_index,
                frames=int(length),
                source="lerobot_meta",
            )
    return by_index


def _overlay_pool(
    base: dict[int, EpisodeFrames],
    overlay: dict[int, EpisodeFrames],
    *,
    label: str,
) -> dict[int, EpisodeFrames]:
    merged = dict(base)
    for run_index, sample in overlay.items():
        current = merged.get(run_index)
        if current is None:
            merged[run_index] = sample
            continue
        if current.frames != sample.frames:
            print(
                f"{label} run {run_index}: {current.source}={current.frames} "
                f"vs {sample.source}={sample.frames}",
                file=sys.stderr,
            )
        merged[run_index] = EpisodeFrames(
            run_id=sample.run_id,
            run_index=run_index,
            frames=current.frames,
            source=f"{current.source}+{sample.source}",
            duration_s=sample.duration_s if sample.duration_s is not None else current.duration_s,
        )
    return merged


def load_from_native(runs_root: Path, run_glob: str) -> dict[int, EpisodeFrames]:
    if not runs_root.is_dir():
        return {}
    by_index: dict[int, EpisodeFrames] = {}
    duplicates: list[str] = []
    for run_dir in sorted(runs_root.glob(run_glob)):
        if not run_dir.is_dir():
            continue
        episode_dir = run_dir / "episode"
        if not (episode_dir / METADATA_FILENAME).is_file():
            continue
        sample = _load_native_episode(episode_dir)
        if sample is None:
            continue
        previous = by_index.get(sample.run_index)
        if previous is None:
            by_index[sample.run_index] = sample
            continue
        duplicates.append(
            f"{sample.run_index}: keep {sample.run_id} ({sample.frames}), drop {previous.run_id}"
        )
        by_index[sample.run_index] = sample
    if duplicates:
        print("native duplicate run indices (kept last completed/success):", file=sys.stderr)
        for line in duplicates:
            print(f"  {line}", file=sys.stderr)
    return by_index


def load_pool(
    *,
    dataset_root: Path,
    runs_root: Path,
    run_glob: str,
    source: str,
) -> tuple[dict[int, EpisodeFrames], str]:
    has_manifest = (dataset_root / MANIFEST_FILENAME).is_file()
    if source == "manifest":
        if not has_manifest:
            raise FileNotFoundError(f"missing {dataset_root / MANIFEST_FILENAME}")
        return load_from_manifest(dataset_root), "manifest"
    if source == "native":
        return load_from_native(runs_root, run_glob), "native"
    if source == "lerobot":
        pool = load_from_lerobot_meta(dataset_root)
        if not pool:
            raise FileNotFoundError(f"missing {dataset_root / 'meta' / 'episodes'} parquet")
        return pool, "lerobot_meta"

    pool = load_from_lerobot_meta(dataset_root)
    used = ["lerobot_meta"] if pool else []
    if has_manifest:
        manifest = load_from_manifest(dataset_root)
        pool = _overlay_pool(pool, manifest, label=str(dataset_root))
        used.append("manifest")
    native = load_from_native(runs_root, run_glob)
    if native:
        pool = _overlay_pool(pool, native, label=str(dataset_root))
        used.append("native")
    if not pool:
        raise FileNotFoundError(f"no episode lengths found under {dataset_root} or {runs_root}/{run_glob}")
    return pool, "+".join(used)


def frames_to_seconds(frames: int, hz: float) -> float:
    return float(frames) / float(hz)


def summarize_ints(values: list[int]) -> dict[str, float | int]:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    n = len(ordered)
    summary: dict[str, float | int] = {
        "n": n,
        "sum": int(sum(ordered)),
        "mean": float(statistics.fmean(ordered)),
        "median": float(statistics.median(ordered)),
        "min": int(ordered[0]),
        "max": int(ordered[-1]),
    }
    if n >= 2:
        summary["stdev"] = float(statistics.stdev(ordered))
    if n >= 4:
        q1, _, q3 = statistics.quantiles(ordered, n=4, method="inclusive")
        summary["q1"] = float(q1)
        summary["q3"] = float(q3)
    return summary


def format_summary(label: str, summary: dict[str, float | int], hz: float) -> list[str]:
    if int(summary.get("n", 0)) == 0:
        return [f"{label}: n=0"]
    lines = [
        (
            f"{label}: n={summary['n']}  sum={int(summary['sum'])} "
            f"({frames_to_seconds(int(summary['sum']), hz):.1f}s)  "
            f"mean={float(summary['mean']):.1f}  median={float(summary['median']):.1f}  "
            f"span=[{int(summary['min'])}, {int(summary['max'])}]"
        )
    ]
    extras: list[str] = []
    if "stdev" in summary:
        extras.append(f"sd={float(summary['stdev']):.1f}")
    if "q1" in summary:
        extras.append(f"q1={float(summary['q1']):.1f}")
        extras.append(f"q3={float(summary['q3']):.1f}")
    if extras:
        lines.append("  " + "  ".join(extras))
    return lines


def histogram(values: list[int], bin_width: int) -> list[str]:
    if not values or bin_width <= 0:
        return ["  (empty)"]
    lo = int(math.floor(min(values) / bin_width) * bin_width)
    hi = int(math.floor(max(values) / bin_width) * bin_width)
    edges = list(range(lo, hi + bin_width, bin_width))
    counts = [0] * len(edges)
    for value in values:
        counts[int((value - lo) // bin_width)] += 1
    peak = max(counts) if counts else 1
    width = 24
    lines: list[str] = []
    for edge, count in zip(edges, counts, strict=True):
        bar = "#" * int(round(width * count / peak)) if peak else ""
        lines.append(f"  [{edge:+5d}, {edge + bin_width:+5d})  {count:2d}  {bar}")
    return lines


def build_pairs(
    left: dict[int, EpisodeFrames],
    right: dict[int, EpisodeFrames],
    start: int,
    end: int,
) -> tuple[list[PairRow], list[int]]:
    rows: list[PairRow] = []
    missing: list[int] = []
    for run_index in range(start, end + 1):
        left_ep = left.get(run_index)
        right_ep = right.get(run_index)
        if left_ep is None or right_ep is None:
            missing.append(run_index)
            continue
        rows.append(
            PairRow(
                run_index=run_index,
                left_run_id=left_ep.run_id,
                right_run_id=right_ep.run_id,
                left_frames=left_ep.frames,
                right_frames=right_ep.frames,
                diff_frames=left_ep.frames - right_ep.frames,
            )
        )
    return rows, missing


def band_totals(pool: dict[int, EpisodeFrames], start: int, end: int) -> list[int]:
    return [pool[index].frames for index in range(start, end + 1) if index in pool]


def format_pairs(rows: list[PairRow], left_name: str, right_name: str, hz: float) -> list[str]:
    lines = [
        f"{'idx':>4}  {left_name:>8}  {right_name:>8}  {'diff':>6}  {'diff_s':>7}",
        "-" * 40,
    ]
    for row in rows:
        lines.append(
            f"{row.run_index:4d}  {row.left_frames:8d}  {row.right_frames:8d}  "
            f"{row.diff_frames:+6d}  {frames_to_seconds(row.diff_frames, hz):+7.1f}"
        )
    return lines


def main() -> int:
    args = parse_args()
    left_runs_root = args.left_runs_root or args.runs_root
    right_runs_root = args.right_runs_root or args.runs_root
    left_source_name = args.left_source or args.source
    right_source_name = args.right_source or args.source
    try:
        pair_ranges = parse_pair_ranges(args.pair_range)
        left, left_source = load_pool(
            dataset_root=args.left_root,
            runs_root=left_runs_root,
            run_glob=args.left_run_glob,
            source=left_source_name,
        )
        right, right_source = load_pool(
            dataset_root=args.right_root,
            runs_root=right_runs_root,
            run_glob=args.right_run_glob,
            source=right_source_name,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not left or not right:
        print(
            f"empty pool: {args.left_name}={len(left)} source={left_source}; "
            f"{args.right_name}={len(right)} source={right_source}",
            file=sys.stderr,
        )
        return 1

    hz = float(args.hz)
    left_frames = [sample.frames for sample in left.values()]
    right_frames = [sample.frames for sample in right.values()]
    left_info = load_info_totals(args.left_root)
    right_info = load_info_totals(args.right_root)

    pair_payloads: list[dict[str, Any]] = []
    text_blocks: list[str] = [
        f"{args.left_name}: {len(left)} ep / {sum(left_frames)} frames  "
        f"({frames_to_seconds(sum(left_frames), hz):.1f}s)  source={left_source}  root={args.left_root}",
        f"{args.right_name}: {len(right)} ep / {sum(right_frames)} frames  "
        f"({frames_to_seconds(sum(right_frames), hz):.1f}s)  source={right_source}  root={args.right_root}",
    ]
    if left_info is not None:
        text_blocks.append(f"  {args.left_name} meta/info.json: {left_info[0]} ep / {left_info[1]} frames")
    if right_info is not None:
        text_blocks.append(f"  {args.right_name} meta/info.json: {right_info[0]} ep / {right_info[1]} frames")
    total_diff = sum(left_frames) - sum(right_frames)
    text_blocks.append(
        f"total {args.left_name}-{args.right_name}: {total_diff:+d} frames "
        f"({frames_to_seconds(total_diff, hz):+.1f}s)"
    )
    text_blocks.extend(format_summary(f"{args.left_name} per-ep", summarize_ints(left_frames), hz))
    text_blocks.extend(format_summary(f"{args.right_name} per-ep", summarize_ints(right_frames), hz))

    band_specs = ((1, 27, "grid 001-027"), (28, 50, "random 028-050"), (51, 70, "targeted 051-070"))
    text_blocks.append("")
    text_blocks.append("band totals (unpaired; 028-050 is coverage-only):")
    for start, end, label in band_specs:
        left_band = band_totals(left, start, end)
        right_band = band_totals(right, start, end)
        left_sum = sum(left_band)
        right_sum = sum(right_band)
        text_blocks.append(
            f"  {label}: {args.left_name} {len(left_band)}/{end - start + 1} {left_sum} "
            f"({frames_to_seconds(left_sum, hz):.1f}s)  "
            f"{args.right_name} {len(right_band)}/{end - start + 1} {right_sum} "
            f"({frames_to_seconds(right_sum, hz):.1f}s)  "
            f"diff {left_sum - right_sum:+d} ({frames_to_seconds(left_sum - right_sum, hz):+.1f}s)"
        )

    for start, end in pair_ranges:
        rows, missing = build_pairs(left, right, start, end)
        diffs = [row.diff_frames for row in rows]
        left_longer = sum(1 for diff in diffs if diff > 0)
        right_longer = sum(1 for diff in diffs if diff < 0)
        tied = sum(1 for diff in diffs if diff == 0)
        text_blocks.append("")
        text_blocks.append(f"paired {start:03d}-{end:03d}  (diff = {args.left_name} - {args.right_name})")
        if missing:
            text_blocks.append(f"  missing both-sides: {missing}")
        text_blocks.extend(format_pairs(rows, args.left_name, args.right_name, hz))
        text_blocks.extend(format_summary("  diff frames", summarize_ints(diffs), hz))
        if diffs:
            mean_diff = float(statistics.fmean(diffs))
            text_blocks.append(
                f"  {args.left_name} longer: {left_longer}/{len(diffs)}  "
                f"{args.right_name} longer: {right_longer}/{len(diffs)}  tie: {tied}"
            )
            text_blocks.append(
                f"  mean {args.left_name} is {mean_diff:+.1f} frames "
                f"({frames_to_seconds(mean_diff, hz):+.1f}s) vs {args.right_name}"
            )
            text_blocks.append(f"  histogram (bin={args.bin_width} frames):")
            text_blocks.extend(histogram(diffs, int(args.bin_width)))
        pair_payloads.append(
            {
                "range": [start, end],
                "missing": missing,
                "pairs": [asdict(row) for row in rows],
                "diff_summary": summarize_ints(diffs),
                "left_longer": left_longer,
                "right_longer": right_longer,
                "tie": tied,
            }
        )

    payload = {
        "hz": hz,
        "left": {
            "name": args.left_name,
            "source": left_source,
            "root": str(args.left_root),
            "episodes": len(left),
            "total_frames": int(sum(left_frames)),
            "info_json": None if left_info is None else {"episodes": left_info[0], "frames": left_info[1]},
            "per_episode": summarize_ints(left_frames),
        },
        "right": {
            "name": args.right_name,
            "source": right_source,
            "root": str(args.right_root),
            "episodes": len(right),
            "total_frames": int(sum(right_frames)),
            "info_json": None if right_info is None else {"episodes": right_info[0], "frames": right_info[1]},
            "per_episode": summarize_ints(right_frames),
        },
        "total_diff_frames": int(sum(left_frames) - sum(right_frames)),
        "pair_groups": pair_payloads,
    }

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=True), flush=True)
    else:
        print("\n".join(text_blocks), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
