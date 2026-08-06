"""Run LeRobot SmolVLA training with grasp-critical start weighting.

Same mechanism as ``train_act_critical.py``: only ``EpisodeAwareSampler`` is
replaced.  Launch under accelerate the same way as plain ``lerobot-train``,
but point at this script and pass ``--critical-index``.

Hot-start example (new output dir, load existing LoRA adapter, +25k steps)::

    accelerate launch ... scripts/train_smolvla_critical.py \\
      --critical-index outputs/datasets/shape_pick_place_v1_c0_c1/critical_frames.json \\
      --policy.type=smolvla \\
      --policy.pretrained_path=.../checkpoints/050000/pretrained_model \\
      --policy.use_peft=true \\
      ...
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
