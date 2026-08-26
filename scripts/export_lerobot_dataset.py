"""Export native SharedAutonomy episodes to a local LeRobot dataset.

Read-only with respect to robot hardware; reads episode artifacts from disk only.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from sharedautonomy.data.lerobot_export import (
    LeRobotExportError,
    export_lerobot_dataset,
)

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export one or more native episode directories to a LeRobot v3.0 dataset. "
            "Does not connect to robot hardware."
        )
    )
    parser.add_argument(
        "episode_dirs",
        nargs="+",
        type=Path,
        help="Native episode directories (each containing metadata.json and steps.jsonl)",
    )
    parser.add_argument(
        "--out-root",
        type=Path,
        required=True,
        help="Output LeRobot dataset root (must not exist unless --resume)",
    )
    parser.add_argument(
        "--repo-id",
        default="local/shape_pick_place_v1",
        help="LeRobot repo id stored in dataset metadata (default: local/shape_pick_place_v1)",
    )
    parser.add_argument(
        "--robot-type",
        default="rm65",
        help="Robot type string stored in dataset metadata (default: rm65)",
    )
    parser.add_argument(
        "--no-videos",
        action="store_true",
        help="Store RGB frames as images in parquet instead of MP4 videos",
    )
    parser.add_argument(
        "--no-diag",
        action="store_true",
        help="Omit diag.* columns from the exported dataset",
    )
    parser.add_argument(
        "--allow-aborted",
        action="store_true",
        help="Allow exporting aborted or unsuccessful episodes",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Append episodes to an existing LeRobot dataset at --out-root",
    )
    parser.add_argument(
        "--no-parallel-encoding",
        action="store_true",
        help=(
            "Disable parallel per-camera video encoding on save_episode. "
            "On Windows, parallel encoding can interleave SVT/ffmpeg logs in the "
            "terminal; use this flag for readable output (slightly slower)."
        ),
    )
    return parser.parse_args()


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _warn_windows_parallel_encoding(*, use_videos: bool, parallel_encoding: bool) -> None:
    if not use_videos or not parallel_encoding:
        return
    if sys.platform != "win32":
        return
    logger.warning(
        "Windows parallel video encoding may interleave SVT/ffmpeg logs in this terminal. "
        "Export data is unaffected; pass --no-parallel-encoding for cleaner output."
    )


def main() -> int:
    args = parse_args()
    _configure_logging()
    use_videos = not args.no_videos
    parallel_encoding = not args.no_parallel_encoding
    _warn_windows_parallel_encoding(use_videos=use_videos, parallel_encoding=parallel_encoding)
    try:
        out_path = export_lerobot_dataset(
            [path.resolve() for path in args.episode_dirs],
            out_root=args.out_root.resolve(),
            repo_id=args.repo_id,
            robot_type=args.robot_type,
            use_videos=use_videos,
            include_diag=not args.no_diag,
            allow_aborted=args.allow_aborted,
            parallel_encoding=parallel_encoding,
            resume=args.resume,
        )
    except LeRobotExportError as exc:
        logger.error("%s", exc)
        return 1
    except Exception:
        logger.exception("Export failed with an unexpected error")
        return 1

    print(f"Exported LeRobot dataset to {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
