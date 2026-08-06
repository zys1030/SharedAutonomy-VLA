"""Run LeRobot ACT training with grasp-critical start weighting.

All normal LeRobot training arguments are forwarded unchanged.  The single
extra argument is ``--critical-index``.  The wrapper replaces only the
EpisodeAwareSampler used by LeRobot 0.6; policy, loss, checkpointing,
distributed preparation, and resume behavior remain LeRobot's own code.
"""

from __future__ import annotations

from sharedautonomy.data.critical_train import (
    parse_critical_argument,
    run_lerobot_train_with_critical_index,
)


def main() -> None:
    critical_index_path, lerobot_args = parse_critical_argument()
    run_lerobot_train_with_critical_index(critical_index_path, lerobot_args)


if __name__ == "__main__":
    main()
