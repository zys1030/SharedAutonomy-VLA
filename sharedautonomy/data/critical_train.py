"""Run LeRobot training with grasp-critical start weighting.

Used by ``tools/training/train_act_critical.py`` and the private-only
``scripts/train_smolvla_critical.py`` experiment.
Both wrappers only replace LeRobot 0.6's ``EpisodeAwareSampler``; policy type,
PEFT, checkpointing, and distributed launch stay with LeRobot / accelerate.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_critical_argument(
    argv: list[str] | None = None,
) -> tuple[Path, list[str]]:
    """Parse ``--critical-index`` and leave remaining argv for LeRobot."""

    parser = argparse.ArgumentParser(
        description="Forward LeRobot training arguments with a critical-frame sampler."
    )
    parser.add_argument(
        "--critical-index",
        type=Path,
        required=True,
        help="JSON index produced by tools/training/build_critical_frame_index.py",
    )
    args, remaining = parser.parse_known_args(argv)
    return args.critical_index, remaining


def run_lerobot_train_with_critical_index(
    critical_index_path: Path,
    lerobot_argv: list[str],
    *,
    prog: str | None = None,
) -> None:
    """Patch ``EpisodeAwareSampler`` then hand control to ``lerobot_train.train``."""

    if not critical_index_path.is_file():
        raise FileNotFoundError(f"critical-frame index does not exist: {critical_index_path}")

    # lerobot_train's @parser.wrap() reads sys.argv itself.  Remove only this
    # wrapper's argument before handing control to LeRobot.
    sys.argv = [prog or sys.argv[0], *lerobot_argv]

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
