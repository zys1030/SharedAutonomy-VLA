"""Load and execute a machine-local collection ready pose."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sharedautonomy.data.schema import JOINT_COUNT


@dataclass(frozen=True, slots=True)
class ReadyPoseConfig:
    """Episode-start joint pose loaded from collection YAML."""

    joint_position_deg: tuple[float, ...]
    gripper_open_fraction: float = 1.0
    canfd_follow: bool = False
    canfd_smoothing: int = 50
    settle_s: float = 2.0
    task_id: str | None = None
    notes: str | None = None
    source: str = ""

    def __post_init__(self) -> None:
        joints = tuple(float(value) for value in self.joint_position_deg)
        if len(joints) != JOINT_COUNT:
            raise ValueError(f"joint_position_deg must contain {JOINT_COUNT} values")
        if not all(value == value and abs(value) != float("inf") for value in joints):
            raise ValueError("joint_position_deg must be finite")
        fraction = float(self.gripper_open_fraction)
        if not 0.0 <= fraction <= 1.0:
            raise ValueError("gripper_open_fraction must be in [0, 1]")
        smoothing = int(self.canfd_smoothing)
        if smoothing < 0:
            raise ValueError("canfd_smoothing must be >= 0")
        settle = float(self.settle_s)
        if not (settle == settle) or settle < 0.0:
            raise ValueError("settle_s must be a finite non-negative value")
        object.__setattr__(self, "joint_position_deg", joints)
        object.__setattr__(self, "gripper_open_fraction", fraction)
        object.__setattr__(self, "canfd_follow", bool(self.canfd_follow))
        object.__setattr__(self, "canfd_smoothing", smoothing)
        object.__setattr__(self, "settle_s", settle)


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load ready-pose config") from exc
    with path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a mapping")
    return payload


def load_ready_pose_config(
    *,
    config_path: str | Path | None = None,
) -> ReadyPoseConfig:
    """Load ``ready_pose`` from explicit or ignored machine-local YAML."""
    path = Path(config_path or "configs/local/manual_cartesian.local.yaml")
    if not path.is_file():
        raise FileNotFoundError(
            f"Ready-pose config not found: {path}. Copy "
            "configs/local/manual_cartesian.example.yaml to "
            "configs/local/manual_cartesian.local.yaml and enter a verified pose."
        )
    payload = _load_yaml_mapping(path)
    ready = payload.get("ready_pose")
    if not isinstance(ready, dict):
        raise ValueError(f"{path} must contain a ready_pose mapping")
    joints = ready.get("joint_position_deg")
    if joints is None:
        raise ValueError(f"{path} ready_pose.joint_position_deg is required")
    return ReadyPoseConfig(
        joint_position_deg=tuple(float(value) for value in joints),
        gripper_open_fraction=float(ready.get("gripper_open_fraction", 1.0)),
        canfd_follow=bool(ready.get("canfd_follow", False)),
        canfd_smoothing=int(ready.get("canfd_smoothing", 50)),
        settle_s=float(ready.get("settle_s", 2.0)),
        task_id=None if ready.get("task_id") is None else str(ready["task_id"]),
        notes=None if ready.get("notes") is None else str(ready["notes"]),
        source=str(path),
    )


def move_arm_to_ready_joints(
    arm: Any,
    joint_position_deg: Sequence[float],
    *,
    follow: bool = False,
    smoothing: int = 50,
    settle_s: float = 2.0,
) -> list[float]:
    """Send ``rm_movej_canfd``, then wait ``settle_s`` before teleop."""
    joints = [float(value) for value in joint_position_deg]
    if len(joints) != JOINT_COUNT:
        raise ValueError(f"joint_position_deg must contain {JOINT_COUNT} values")
    if int(smoothing) < 0:
        raise ValueError("smoothing must be >= 0")
    settle = float(settle_s)
    if not (settle == settle) or settle < 0.0:
        raise ValueError("settle_s must be a finite non-negative value")
    if not hasattr(arm, "rm_movej_canfd"):
        raise RuntimeError("Connected arm does not expose rm_movej_canfd")
    status = arm.rm_movej_canfd(
        joints,
        bool(follow),
        0,
        0,
        int(smoothing),
    )
    if int(status) != 0:
        raise RuntimeError(f"rm_movej_canfd to ready pose failed with SDK status {status}")
    if settle > 0.0:
        time.sleep(settle)
    return joints
