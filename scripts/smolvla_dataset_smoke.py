"""Offline SmolVLA checkpoint smoke and same-frame task A/B comparison."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

import numpy as np
from sharedautonomy.policies.smolvla.runtime import (
    SmolVLAInferenceRuntime,
    SmolVLARuntimeConfig,
)
from sharedautonomy.tasks.shape_pick_place_v1 import task_text_for_shape_pick_place

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run SmolVLA on one identical C1 dataset frame with two task texts. "
            "Each task inference resets the policy action queue."
        )
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        action="append",
        required=True,
        help="Completed full or LoRA checkpoint; repeat for checkpoint comparison",
    )
    parser.add_argument(
        "--base-model",
        default=None,
        help="Optional base model path or Hub id for adapter-only checkpoints",
    )
    parser.add_argument("--episode-index", type=int, default=0)
    parser.add_argument("--frame-index", type=int, default=0)
    parser.add_argument(
        "--task-a",
        default=task_text_for_shape_pick_place("red", "up"),
        help="First task text (default: standard red -> up task)",
    )
    parser.add_argument(
        "--task-b",
        default=task_text_for_shape_pick_place("blue", "up"),
        help="Second task text (default: standard blue -> up task)",
    )
    parser.add_argument(
        "--dataset-repo-id",
        default="local/shape_pick_place_v1",
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=Path("outputs/datasets/shape_pick_place_v1_c1"),
    )
    parser.add_argument("--device", default="cuda")
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Optional path for the machine-readable comparison report",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return parser.parse_args()


def _compare_checkpoint(
    *,
    checkpoint: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    runtime = SmolVLAInferenceRuntime(
        SmolVLARuntimeConfig(
            checkpoint_dir=checkpoint.resolve(),
            dataset_repo_id=str(args.dataset_repo_id),
            dataset_root=args.dataset_root.resolve(),
            device=str(args.device),
            base_model=args.base_model,
        )
    )
    runtime.load()
    try:
        observation_a, response_a = runtime.infer_dataset_frame(
            episode_index=int(args.episode_index),
            frame_index=int(args.frame_index),
            reset=True,
            task_override=str(args.task_a),
        )
        observation_b, response_b = runtime.infer_dataset_frame(
            episode_index=int(args.episode_index),
            frame_index=int(args.frame_index),
            reset=True,
            task_override=str(args.task_b),
        )
        action_a = np.asarray(response_a.action, dtype=np.float32)
        action_b = np.asarray(response_b.action, dtype=np.float32)
        delta = action_b - action_a
        return {
            "checkpoint": str(runtime.checkpoint_dir),
            "checkpoint_kind": runtime.describe().get("checkpoint_kind"),
            "base_model": runtime.describe().get("base_model"),
            "device": runtime.describe().get("device"),
            "dataset_root": str(args.dataset_root.resolve()),
            "episode_index": int(args.episode_index),
            "frame_index": int(args.frame_index),
            "state_equal": bool(np.array_equal(observation_a.state, observation_b.state)),
            "task_a": str(args.task_a),
            "task_b": str(args.task_b),
            "action_a": [round(float(value), 6) for value in action_a.tolist()],
            "action_b": [round(float(value), 6) for value in action_b.tolist()],
            "delta_b_minus_a": [round(float(value), 6) for value in delta.tolist()],
            "max_abs_delta": round(float(np.max(np.abs(delta))), 6),
            "l2_delta": round(float(np.linalg.norm(delta)), 6),
            "chunk_size": response_a.chunk_size,
            "n_action_steps": response_a.n_action_steps,
        }
    finally:
        runtime.close()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    report = {
        "mode": "smolvla_dataset_task_ab",
        "comparisons": [
            _compare_checkpoint(checkpoint=checkpoint, args=args) for checkpoint in args.checkpoint
        ],
    }
    rendered = json.dumps(report, indent=2)
    print(rendered, flush=True)
    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(rendered + "\n", encoding="utf-8")
        logger.info("Wrote comparison report to %s", args.output_json)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(json.dumps({"abort": "keyboard_interrupt"}), flush=True)
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        logger.exception("smolvla_dataset_smoke failed")
        print(json.dumps({"error": str(exc)}), flush=True)
        sys.exit(1)
