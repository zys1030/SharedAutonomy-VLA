"""One-off Step1 audit for native pilot episodes (not a project entrypoint)."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

TASK_CARD_INSTRUCTIONS = {
    ("red", "up"): "Pick up the red circle and place it in the UP region.",
    ("red", "down"): "Pick up the red circle and place it in the DOWN region.",
    ("yellow", "up"): "Pick up the yellow triangle and place it in the UP region.",
    ("yellow", "down"): "Pick up the yellow triangle and place it in the DOWN region.",
    ("blue", "up"): "Pick up the blue rectangle and place it in the UP region.",
    ("blue", "down"): "Pick up the blue rectangle and place it in the DOWN region.",
}


def audit_episode(episode_dir: Path) -> dict:
    meta = json.loads((episode_dir / "metadata.json").read_text(encoding="utf-8"))
    nested = meta.get("metadata") or {}
    steps_path = episode_dir / "steps.jsonl"

    n = 0
    joint_target_null = 0
    gripper_actual_null = 0
    gripper_target_null = 0
    gripper_cmd_null = 0
    deadman_true = 0
    safety_true = 0
    wrist_missing = 0
    external_missing = 0
    sync_counts: Counter[str] = Counter()
    first_shapes: dict[str, tuple] = {}
    sample_dtypes: dict[str, str] = {}

    with steps_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            n += 1
            obs = payload.get("observation") or {}
            human = payload.get("human_action") or {}
            executed = payload.get("executed_action") or {}

            if executed.get("joint_target_deg") is None:
                joint_target_null += 1
            if obs.get("gripper_actual_open_fraction") is None:
                gripper_actual_null += 1
            if obs.get("gripper_commanded_open_fraction") is None:
                gripper_cmd_null += 1
            if human.get("gripper_target_open_fraction") is None:
                gripper_target_null += 1
            if bool(human.get("deadman_active")):
                deadman_true += 1
            if bool(executed.get("safety_intervened")):
                safety_true += 1

            for warn in payload.get("sync_warnings") or []:
                sync_counts[str(warn)] += 1

            wrist = obs.get("wrist_camera")
            external = obs.get("external_camera")
            if wrist is None:
                wrist_missing += 1
            if external is None:
                external_missing += 1

            if n == 1:
                if wrist is not None:
                    color = np.load(episode_dir / wrist["color_rgb_path"])
                    first_shapes["wrist_color"] = tuple(color.shape)
                    sample_dtypes["wrist_color"] = str(color.dtype)
                    # Channel order heuristic: red block episodes should have R>B in red regions;
                    # here just record mean per channel.
                    sample_dtypes["wrist_channel_means"] = (
                        f"R={float(color[..., 0].mean()):.2f},"
                        f"G={float(color[..., 1].mean()):.2f},"
                        f"B={float(color[..., 2].mean()):.2f}"
                    )
                    if wrist.get("depth_raw_path"):
                        depth = np.load(episode_dir / wrist["depth_raw_path"])
                        first_shapes["wrist_depth"] = tuple(depth.shape)
                        sample_dtypes["wrist_depth"] = str(depth.dtype)
                        sample_dtypes["wrist_depth_scale"] = str(wrist.get("depth_scale_m_per_unit"))
                if external is not None:
                    color = np.load(episode_dir / external["color_rgb_path"])
                    first_shapes["external_color"] = tuple(color.shape)
                    sample_dtypes["external_color"] = str(color.dtype)

    source = nested.get("source_object")
    dest = nested.get("destination")
    task_text = nested.get("task_text")
    expected = TASK_CARD_INSTRUCTIONS.get((source, dest))

    return {
        "episode_dir": episode_dir.as_posix(),
        "run_id": nested.get("run_id"),
        "episode_id": nested.get("episode_id"),
        "status": meta.get("status"),
        "success": nested.get("success"),
        "step_count_meta": meta.get("step_count"),
        "step_count_jsonl": n,
        "task_id": nested.get("task_id"),
        "source_object": source,
        "destination": dest,
        "task_text": task_text,
        "task_text_matches_card": (task_text == expected) if expected is not None else None,
        "expected_task_text": expected,
        "control_rate_hz": nested.get("control_rate_hz"),
        "null_counts": {
            "executed.joint_target_deg": joint_target_null,
            "obs.gripper_actual_open_fraction": gripper_actual_null,
            "obs.gripper_commanded_open_fraction": gripper_cmd_null,
            "human.gripper_target_open_fraction": gripper_target_null,
        },
        "null_fractions": {
            "executed.joint_target_deg": joint_target_null / n if n else None,
            "obs.gripper_actual_open_fraction": gripper_actual_null / n if n else None,
            "obs.gripper_commanded_open_fraction": gripper_cmd_null / n if n else None,
            "human.gripper_target_open_fraction": gripper_target_null / n if n else None,
        },
        "deadman_active_true": deadman_true,
        "deadman_active_fraction": deadman_true / n if n else None,
        "safety_intervened_true": safety_true,
        "camera_missing": {
            "wrist": wrist_missing,
            "external": external_missing,
        },
        "sync_warning_counts": dict(sync_counts),
        "first_frame_shapes": first_shapes,
        "first_frame_dtypes": sample_dtypes,
    }


def main(argv: list[str]) -> int:
    roots = [Path(p) for p in argv] if argv else [
        Path("outputs/runs/shape-pick-place-pilot-001/episode"),
        Path("outputs/runs/shape-pick-place-pilot-003/episode"),
    ]
    # Also pick up any other pilot-* episode dirs if present.
    runs = Path("outputs/runs")
    if runs.is_dir():
        for path in sorted(runs.glob("shape-pick-place-pilot-*/episode")):
            if path not in roots and path.is_dir():
                roots.append(path)

    reports = []
    for root in roots:
        if not root.is_dir():
            print(f"SKIP missing {root}", flush=True)
            continue
        reports.append(audit_episode(root))

    print(json.dumps(reports, indent=2, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
