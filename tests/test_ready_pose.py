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


def test_load_ready_pose_from_explicit_local_yaml(tmp_path: Path) -> None:
    path = tmp_path / "manual_cartesian.local.yaml"
    path.write_text(
        "\n".join(
            [
                "ready_pose:",
                "  joint_position_deg: [0, 0, 90, 0, 90, 0]",
                "  gripper_open_fraction: 1.0",
                "  canfd_follow: false",
                "  canfd_smoothing: 50",
                "  settle_s: 2.0",
            ]
        ),
        encoding="utf-8",
    )
    config = load_ready_pose_config(config_path=path)
    assert config.joint_position_deg == (0.0, 0.0, 90.0, 0.0, 90.0, 0.0)
    assert config.gripper_open_fraction == 1.0
    assert config.canfd_follow is False
    assert config.canfd_smoothing == 50
    assert config.settle_s == 2.0
    assert config.source == str(path)


def test_move_arm_to_ready_joints_uses_canfd_parameters() -> None:
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


def test_default_ready_pose_requires_machine_local_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError, match="manual_cartesian.local.yaml"):
        load_ready_pose_config()
