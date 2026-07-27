"""Core tests for collection ready-pose load and move helper."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.core

from sharedautonomy.robot.ready_pose import (
    ReadyPoseConfig,
    load_ready_pose_config,
    move_arm_to_ready_joints,
)


class _FakeArm:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def rm_movej_canfd(self, joints, follow, expand, trajectory_mode, radio) -> int:
        self.calls.append(
            (list(joints), bool(follow), int(expand), int(trajectory_mode), int(radio))
        )
        return 0


def test_load_ready_pose_from_manual_cartesian_yaml() -> None:
    config = load_ready_pose_config()
    assert config.joint_position_deg == (0.0, 0.0, 90.0, 0.0, 90.0, 0.0)
    assert config.gripper_open_fraction == 0.6
    assert config.canfd_follow is False
    assert config.canfd_smoothing == 50
    assert config.settle_s == 2.0
    assert "manual_cartesian.yaml" in config.source.replace("\\", "/")


def test_move_arm_to_ready_joints_matches_try_sc_canfd() -> None:
    arm = _FakeArm()
    joints = move_arm_to_ready_joints(
        arm, [0, 0, 90, 0, 90, 0], follow=False, smoothing=50, settle_s=0.0
    )
    assert joints == [0.0, 0.0, 90.0, 0.0, 90.0, 0.0]
    assert arm.calls == [([0.0, 0.0, 90.0, 0.0, 90.0, 0.0], False, 0, 0, 50)]


def test_ready_pose_config_rejects_bad_joint_count() -> None:
    with pytest.raises(ValueError, match="6 values"):
        ReadyPoseConfig(joint_position_deg=(0.0, 0.0, 90.0))


def test_load_ready_pose_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_ready_pose_config(config_path=tmp_path / "missing.yaml")
