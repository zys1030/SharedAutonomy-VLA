"""Scatter EE XY at the first gripper-close step of native episodes.

Read-only helper for C0 spatial coverage. Does not connect to hardware.
Uses executed_action.gripper_target_open_fraction crossing below --close-threshold
as the grasp-close proxy; records observation.ee_position_m at that step.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from sharedautonomy.data import load_recorded_episode
from sharedautonomy.data.recorder import METADATA_FILENAME, EpisodeRecorderError


@dataclass(frozen=True, slots=True)
class GraspCloseSample:
    run_id: str
    episode_id: str
    success: bool | None
    step_index: int
    ee_x_m: float
    ee_y_m: float
    ee_z_m: float
    gripper_after: float
    group: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plot / export EE XY at the first gripper-close transition in native episodes. "
            "Read-only; uses steps.jsonl only (no images)."
        )
    )
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=Path("outputs/runs"),
        help="Root containing <run_id>/episode/ (default: outputs/runs)",
    )
    parser.add_argument(
        "--run-glob",
        default="shape-pick-place-train-*",
        help="Glob for run dirs under --runs-root (default: shape-pick-place-train-*)",
    )
    parser.add_argument(
        "--run-index-min",
        type=int,
        default=61,
        help="Keep only train-NNN with NNN >= this (default: 61 for C0)",
    )
    parser.add_argument(
        "--run-index-max",
        type=int,
        default=100,
        help="Keep only train-NNN with NNN <= this (default: 100 for C0)",
    )
    parser.add_argument(
        "--success-only",
        action="store_true",
        help="Skip episodes whose metadata.success is not true",
    )
    parser.add_argument(
        "--close-threshold",
        type=float,
        default=0.5,
        help="Gripper open_fraction threshold; close = crossing from >=T to <T (default 0.5)",
    )
    parser.add_argument(
        "--which-close",
        choices=("first", "last"),
        default="first",
        help="Use first or last close transition in the episode (default: first)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("outputs/analysis"),
        help="Directory for CSV and PNG (default: outputs/analysis)",
    )
    parser.add_argument(
        "--no-show",
        action="store_true",
        help="Do not open an interactive window; only write files",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Also print a JSON summary to stdout",
    )
    return parser.parse_args()


def discover_episode_dirs(runs_root: Path, run_glob: str) -> list[Path]:
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


def _infer_group(run_id: str) -> str:
    """Heuristic labels for C0 batches; unknown runs stay 'other'."""
    # shape-pick-place-train-061 → 61
    suffix = run_id.rsplit("-", maxsplit=1)[-1]
    try:
        index = int(suffix)
    except ValueError:
        return "other"
    if 61 <= index <= 90:
        return "clean"
    if 91 <= index <= 100:
        return "correction"
    return "other"


def _gripper_open_fraction(step) -> float | None:
    executed = step.executed_action.gripper_target_open_fraction
    if executed is not None:
        return float(executed)
    commanded = step.observation.gripper_commanded_open_fraction
    if commanded is not None:
        return float(commanded)
    return None


def find_close_step_index(
    steps,
    *,
    close_threshold: float,
    which: str,
) -> tuple[int, float] | None:
    """Return (step_index, gripper_after) for first/last open→close crossing."""
    previous: float | None = None
    hits: list[tuple[int, float]] = []
    for step in steps:
        gripper = _gripper_open_fraction(step)
        if gripper is None:
            continue
        if previous is not None and previous >= close_threshold and gripper < close_threshold:
            hits.append((int(step.step_index), gripper))
        previous = gripper
    if not hits:
        return None
    return hits[0] if which == "first" else hits[-1]


def collect_samples(
    episode_dirs: list[Path],
    *,
    success_only: bool,
    close_threshold: float,
    which_close: str,
) -> tuple[list[GraspCloseSample], list[str]]:
    samples: list[GraspCloseSample] = []
    skipped: list[str] = []
    for episode_dir in episode_dirs:
        try:
            episode = load_recorded_episode(episode_dir)
        except (EpisodeRecorderError, OSError, json.JSONDecodeError) as exc:
            skipped.append(f"{episode_dir}: load failed ({exc})")
            continue
        success = episode.metadata.success
        if success_only and success is not True:
            skipped.append(f"{episode.metadata.run_id}: success={success!r}")
            continue
        hit = find_close_step_index(
            episode.steps,
            close_threshold=close_threshold,
            which=which_close,
        )
        if hit is None:
            skipped.append(f"{episode.metadata.run_id}: no close transition")
            continue
        step_index, gripper_after = hit
        step = episode.steps[step_index]
        ee = step.observation.ee_position_m
        samples.append(
            GraspCloseSample(
                run_id=str(episode.metadata.run_id),
                episode_id=str(episode.metadata.episode_id),
                success=success,
                step_index=step_index,
                ee_x_m=float(ee[0]),
                ee_y_m=float(ee[1]),
                ee_z_m=float(ee[2]),
                gripper_after=float(gripper_after),
                group=_infer_group(str(episode.metadata.run_id)),
            )
        )
    return samples, skipped


def write_csv(path: Path, samples: list[GraspCloseSample]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "run_id",
                "episode_id",
                "group",
                "success",
                "step_index",
                "ee_x_m",
                "ee_y_m",
                "ee_z_m",
                "gripper_after",
            ],
        )
        writer.writeheader()
        for sample in samples:
            writer.writerow(
                {
                    "run_id": sample.run_id,
                    "episode_id": sample.episode_id,
                    "group": sample.group,
                    "success": sample.success,
                    "step_index": sample.step_index,
                    "ee_x_m": f"{sample.ee_x_m:.6f}",
                    "ee_y_m": f"{sample.ee_y_m:.6f}",
                    "ee_z_m": f"{sample.ee_z_m:.6f}",
                    "gripper_after": f"{sample.gripper_after:.4f}",
                }
            )


def plot_scatter(path: Path, samples: list[GraspCloseSample], *, show: bool) -> None:
    import matplotlib.pyplot as plt

    path.parent.mkdir(parents=True, exist_ok=True)
    groups = sorted({sample.group for sample in samples})
    colors = {
        "clean": "#1f77b4",
        "correction": "#d62728",
        "other": "#7f7f7f",
    }
    fig, ax = plt.subplots(figsize=(7.5, 6.5))
    for group in groups:
        xs = [s.ee_x_m for s in samples if s.group == group]
        ys = [s.ee_y_m for s in samples if s.group == group]
        ax.scatter(
            xs,
            ys,
            s=42,
            alpha=0.85,
            c=colors.get(group, "#7f7f7f"),
            label=f"{group} (n={len(xs)})",
            edgecolors="white",
            linewidths=0.4,
        )
    ax.set_xlabel("ee_x_m (base)")
    ax.set_ylabel("ee_y_m (base)")
    ax.set_title("EE XY at first gripper-close (native episodes)")
    ax.set_aspect("equal", adjustable="datalim")
    ax.grid(True, linestyle="--", alpha=0.35)
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    if show:
        plt.show()
    else:
        plt.close(fig)


def main() -> int:
    args = parse_args()
    threshold = float(args.close_threshold)
    if not 0.0 < threshold < 1.0:
        print("--close-threshold must be in (0, 1)", file=sys.stderr)
        return 2

    episode_dirs = discover_episode_dirs(args.runs_root, str(args.run_glob))
    index_min = int(args.run_index_min)
    index_max = int(args.run_index_max)
    if index_min > index_max:
        print("--run-index-min must be <= --run-index-max", file=sys.stderr)
        return 2
    filtered: list[Path] = []
    for episode_dir in episode_dirs:
        suffix = episode_dir.parent.name.rsplit("-", maxsplit=1)[-1]
        try:
            index = int(suffix)
        except ValueError:
            continue
        if index_min <= index <= index_max:
            filtered.append(episode_dir)
    episode_dirs = filtered

    if not episode_dirs:
        print(
            f"No episodes under {args.runs_root} matching "
            f"run-glob={args.run_glob!r} indices=[{index_min}, {index_max}]",
            file=sys.stderr,
        )
        return 1

    samples, skipped = collect_samples(
        episode_dirs,
        success_only=bool(args.success_only),
        close_threshold=threshold,
        which_close=str(args.which_close),
    )
    if not samples:
        print("No grasp-close samples found.", file=sys.stderr)
        for line in skipped[:20]:
            print(f"  skip: {line}", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir)
    csv_path = out_dir / "grasp_close_xy.csv"
    png_path = out_dir / "grasp_close_xy.png"
    write_csv(csv_path, samples)
    plot_scatter(png_path, samples, show=not bool(args.no_show))

    xs = [s.ee_x_m for s in samples]
    ys = [s.ee_y_m for s in samples]
    print(
        f"episodes_scanned={len(episode_dirs)} samples={len(samples)} skipped={len(skipped)}\n"
        f"ee_x_m: [{min(xs):.4f}, {max(xs):.4f}]  ee_y_m: [{min(ys):.4f}, {max(ys):.4f}]\n"
        f"csv: {csv_path}\n"
        f"png: {png_path}"
    )
    if args.json:
        payload = {
            "samples": [sample.__dict__ for sample in samples],
            "skipped": skipped,
            "csv": str(csv_path),
            "png": str(png_path),
        }
        print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
