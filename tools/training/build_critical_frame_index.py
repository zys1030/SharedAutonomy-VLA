"""Build a weighted chunk-start index from a LeRobot dataset.

Grasp mode reads only the raw LeRobot ``action`` column (first open-to-close).
Episode-start mode uses episode boundaries only.  Neither decodes images,
connects to hardware, or modifies the dataset.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sharedautonomy.data.critical_frames import (
    WINDOW_KIND_EPISODE_START,
    WINDOW_KIND_GRASP,
    CriticalFrameConfig,
    EpisodeFrameRange,
    build_critical_frame_index,
    build_episode_start_frame_index,
    save_critical_frame_index,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build weighted chunk-start windows from a LeRobot dataset."
    )
    parser.add_argument("--dataset-repo-id", required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--window-mode",
        choices=(WINDOW_KIND_GRASP, WINDOW_KIND_EPISODE_START),
        default=WINDOW_KIND_GRASP,
        help="grasp: first close±pre/post. episode_start: first start_frames of each episode.",
    )
    parser.add_argument("--pre-frames", type=int, default=20)
    parser.add_argument("--post-frames", type=int, default=10)
    parser.add_argument("--start-frames", type=int, default=40)
    parser.add_argument("--close-threshold", type=float, default=0.5)
    parser.add_argument("--weight", type=float, default=5.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Keep the optional LeRobot dependency lazy so ordinary project imports
    # and pure index tests do not require dataset decoding support.
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    dataset = LeRobotDataset(args.dataset_repo_id, root=args.dataset_root)
    from_indices = dataset.meta.episodes["dataset_from_index"]
    to_indices = dataset.meta.episodes["dataset_to_index"]
    episode_ranges = tuple(
        EpisodeFrameRange(
            episode_index=episode_index,
            from_index=int(from_index),
            to_index=int(to_index),
        )
        for episode_index, (from_index, to_index) in enumerate(zip(from_indices, to_indices, strict=True))
    )

    if args.window_mode == WINDOW_KIND_EPISODE_START:
        index = build_episode_start_frame_index(
            dataset_repo_id=args.dataset_repo_id,
            dataset_root=args.dataset_root,
            num_frames=int(dataset.num_frames),
            episode_ranges=episode_ranges,
            config=CriticalFrameConfig(
                pre_frames=args.pre_frames,
                post_frames=0,
                close_threshold=args.close_threshold,
                weight=args.weight,
                window_kind=WINDOW_KIND_EPISODE_START,
                start_frames=args.start_frames,
            ),
        )
    else:
        index = build_critical_frame_index(
            dataset_repo_id=args.dataset_repo_id,
            dataset_root=args.dataset_root,
            num_frames=int(dataset.num_frames),
            episode_ranges=episode_ranges,
            action_reader=lambda frame_index: dataset.get_raw_item(frame_index)["action"],
            config=CriticalFrameConfig(
                pre_frames=args.pre_frames,
                post_frames=args.post_frames,
                close_threshold=args.close_threshold,
                weight=args.weight,
            ),
        )
    save_critical_frame_index(index, args.output)

    print(
        json.dumps(
            {
                "output": str(args.output),
                "dataset_repo_id": index.dataset_repo_id,
                "dataset_root": index.dataset_root,
                "num_episodes": index.num_episodes,
                "num_frames": index.num_frames,
                "episodes_with_close": index.episodes_with_close,
                "episodes_without_close": index.num_episodes - index.episodes_with_close,
                "window_frames": index.num_window_frames,
                "config": index.to_dict()["config"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
