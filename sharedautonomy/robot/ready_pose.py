"""Load and execute the collection ready / initial joint pose.

try_sc moves to ``init_state`` on connect and again on ``reset`` via a single
``rm_movej_canfd(follow=False, radio=50)`` call. This module does the same
bring-up step before the SharedAutonomy Cartesian teleop loop starts.
"""

from __future__ import annotations

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
        object.__setattr__(self, "joint_position_deg", joints)
        object.__setattr__(self, "gripper_open_fraction", fraction)
        object.__setattr__(self, "canfd_follow", bool(self.canfd_follow))
        object.__setattr__(self, "canfd_smoothing", smoothing)


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
    """Load ``ready_pose`` from collection YAML (default: manual_cartesian.yaml)."""
    path = Path(config_path or "configs/collection/manual_cartesian.yaml")
    if not path.is_file():
        raise FileNotFoundError(f"Ready-pose config not found: {path}")
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
) -> list[float]:
    """Move to ready joints the same way try_sc does: one ``rm_movej_canfd`` pulse."""
    joints = [float(value) for value in joint_position_deg]
    if len(joints) != JOINT_COUNT:
        raise ValueError(f"joint_position_deg must contain {JOINT_COUNT} values")
    if int(smoothing) < 0:
        raise ValueError("smoothing must be >= 0")
    if not hasattr(arm, "rm_movej_canfd"):
        raise RuntimeError("Connected arm does not expose rm_movej_canfd")
    # try_sc RealManEndEffectorBackend._set_joint_state:
    #   rm_movej_canfd(joint, False, 0, 0, 50)
    status = arm.rm_movej_canfd(
        joints,
        bool(follow),
        0,
        0,
        int(smoothing),
    )
    if int(status) != 0:
        raise RuntimeError(f"rm_movej_canfd to ready pose failed with SDK status {status}")
    return joints
