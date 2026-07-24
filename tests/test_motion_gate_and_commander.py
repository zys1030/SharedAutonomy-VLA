"""Tests for motion gating, CAN-FD commander, and workspace loading."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.core

from sharedautonomy.assistance.workspace_config import load_cartesian_workspace, workspace_from_mapping
from sharedautonomy.control.motion_gate import resolve_motion_enabled
from sharedautonomy.robot.canfd_commander import RealManCanfdJointCommander
from sharedautonomy.robot.safety import MotionDisabledError


def test_resolve_motion_enabled_requires_dual_confirm() -> None:
    assert resolve_motion_enabled(config_enable_motion=False, cli_allow_motion=False) is False
    assert resolve_motion_enabled(config_enable_motion=True, cli_allow_motion=False) is False
    assert resolve_motion_enabled(config_enable_motion=True, cli_allow_motion=True) is True
    with pytest.raises(MotionDisabledError, match="enable_motion is false"):
        resolve_motion_enabled(config_enable_motion=False, cli_allow_motion=True)


def test_canfd_commander_refuses_until_armed() -> None:
    class FakeArm:
        def __init__(self) -> None:
            self.moves = []

        def rm_movej_canfd(self, joints, **kwargs):
            self.moves.append((list(joints), kwargs))
            return 0

        def rm_set_arm_slow_stop(self) -> int:
            return 0

    arm = FakeArm()
    commander = RealManCanfdJointCommander(arm, armed=False)
    with pytest.raises(MotionDisabledError, match="not armed"):
        commander.send_joint_target([0.0] * 6)

    commander.arm_motion()
    commander.send_joint_target([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    assert commander.commands_sent == 1
    assert arm.moves[0][0] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert arm.moves[0][1]["follow"] is False
    assert commander.slow_stop() == 0


def test_workspace_from_mapping_and_loader(tmp_path: Path) -> None:
    mapping = {
        "polygon_xy_m": [[-0.456, 0.107], [-0.387, -0.236], [-0.170, -0.420], [-0.150, 0.068]],
        "table_z_m": 0.0,
        "min_tool_clearance_m": 0.0,
        "tool_tip_offset_base_m": [0.0, 0.0, -0.178],
        "max_flange_z_m": 0.45,
    }
    workspace = workspace_from_mapping(mapping)
    assert workspace.max_flange_z_m == pytest.approx(0.45)
    assert workspace.min_flange_z_m == pytest.approx(0.178)

    nested = workspace_from_mapping({"cartesian_safety": {**mapping, "max_flange_z_m": None}})
    assert nested.max_flange_z_m is None
    assert nested.min_flange_z_m == pytest.approx(0.178)

    path = tmp_path / "safety.yaml"
    path.write_text(
        "\n".join(
            [
                "cartesian_safety:",
                "  polygon_xy_m:",
                "    - [-0.456, 0.107]",
                "    - [-0.387, -0.236]",
                "    - [-0.170, -0.420]",
                "    - [-0.150, 0.068]",
                "  table_z_m: 0.0",
                "  min_tool_clearance_m: 0.0",
                "  tool_tip_offset_base_m: [0.0, 0.0, -0.178]",
                "  max_flange_z_m: 0.40",
            ]
        ),
        encoding="utf-8",
    )
    loaded, source = load_cartesian_workspace(path)
    assert source == str(path)
    assert loaded.max_flange_z_m == pytest.approx(0.40)
