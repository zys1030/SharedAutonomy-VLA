"""Run LeRobot ACT training with grasp-critical start weighting.

All normal LeRobot training arguments are forwarded unchanged.  The single
extra argument is ``--critical-index``.  The wrapper replaces only the
EpisodeAwareSampler used by LeRobot 0.6; policy, loss, checkpointing,
distributed preparation, and resume behavior remain LeRobot's own code.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_critical_argument() -> tuple[Path, list[str]]:
    parser = argparse.ArgumentParser(
        description="Forward LeRobot training arguments with a critical-frame sampler."
    )
    parser.add_argument(
        "--critical-index",
        type=Path,
        required=True,
        help="JSON index produced by build_critical_frame_index.py",
    )
    args, remaining = parser.parse_known_args()
    return args.critical_index, remaining


def main() -> None:
    critical_index_path, lerobot_args = parse_critical_argument()
    if not critical_index_path.is_file():
        raise FileNotFoundError(f"critical-frame index does not exist: {critical_index_path}")

    # lerobot_train's @parser.wrap() reads sys.argv itself.  Remove only this
    # wrapper's argument before handing control to LeRobot.
    sys.argv = [sys.argv[0], *lerobot_args]

    from lerobot.scripts import lerobot_train
    from sharedautonomy.data.critical_sampler import CriticalFrameSampler

    def sampler_factory(*args, **kwargs):
        return CriticalFrameSampler(
            *args,
            critical_index_path=critical_index_path,
            **kwargs,
        )

    # v0.6 resolves EpisodeAwareSampler through this module global when the
    # dataloader is constructed, so the rest of lerobot_train stays untouched.
    lerobot_train.EpisodeAwareSampler = sampler_factory
    lerobot_train.train()


if __name__ == "__main__":
    main()
