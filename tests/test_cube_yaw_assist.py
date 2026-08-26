"""Cube-yaw shared-autonomy assist: rate law, override, and runner overlay."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pytest
from sharedautonomy.assistance.cube_yaw_assist import (
    CubeYawAssistConfig,
    ExternalCubeYawAssistPolicy,
    compute_cube_yaw_assist,
)
from sharedautonomy.assistance.safety_filter import (
    CartesianSafetyFilter,
    CartesianSafetyLimits,
    example_cartesian_workspace,
)
from sharedautonomy.control.manual import (
    ManualCartesianConfig,
    MockJointCommander,
    MockRobotStateSource,
    build_manual_cartesian_runner,
)
from sharedautonomy.data.schema import SampleTimestamp
from sharedautonomy.devices.spacemouse import MockSpaceMouse, SpaceMouseConfig
from sharedautonomy.perception.table_homography import TableHomography

pytestmark = pytest.mark.core


def _stamp() -> SampleTimestamp:
    return SampleTimestamp(
        timestamp_utc=datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
        received_monotonic_ns=1_000_000_000,
    )


def _identity_teleop(
    *,
    translation_raw=(0.0, 0.0, 0.0),
    rotation_raw=(0.0, 0.0, 0.0),
    deadman_active=True,
    gripper_target_open_fraction=1.0,
    allow_tool_yaw=False,
) -> MockSpaceMouse:
    identity = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    return MockSpaceMouse(
        SpaceMouseConfig(
            deadzone=0.0,
            max_linear_speed_m_s=0.05,
            max_angular_speed_rad_s=0.4,
            mount_orientation="custom",
            translation_transform=identity,
            rotation_transform=identity,
            allow_tool_yaw=allow_tool_yaw,
            input_timeout_s=0.1,
        ),
        translation_raw=translation_raw,
        rotation_raw=rotation_raw,
        deadman_active=deadman_active,
        gripper_target_open_fraction=gripper_target_open_fraction,
    )


@dataclass(frozen=True, slots=True)
class _FixedDeltaYawAssist:
    delta_j6_deg: float | None
    config: CubeYawAssistConfig = CubeYawAssistConfig()

    def propose(
        self,
        *,
        color_rgb,
        j6_now_deg: float,
        ee_rpy_rad,
        human_wz_rad_s: float,
        deadman_active: bool,
        gripper_open_fraction: float | None,
        timestamp: SampleTimestamp,
    ):
        del color_rgb, j6_now_deg, ee_rpy_rad
        return compute_cube_yaw_assist(
            delta_j6_deg=self.delta_j6_deg,
            timestamp=timestamp,
            human_wz_rad_s=human_wz_rad_s,
            deadman_active=deadman_active,
            gripper_open_fraction=gripper_open_fraction,
            config=self.config,
        )


def test_assist_clips_large_error_to_max_rate() -> None:
    decision = compute_cube_yaw_assist(delta_j6_deg=45.0, timestamp=_stamp())
    assert decision.reason == "assisting"
    assert decision.authority == pytest.approx(1.0)
    assert decision.desired_yaw_rate_rad_s == pytest.approx(0.4)
    assert decision.assist_action.linear_velocity_m_s == (0.0, 0.0, 0.0)
    assert decision.assist_action.gripper_target_open_fraction is None
    assert decision.assist_action.inferred_target_id == "red_cube"


def test_assist_negative_error_rotates_the_other_way() -> None:
    decision = compute_cube_yaw_assist(delta_j6_deg=-20.0, timestamp=_stamp())
    assert decision.desired_yaw_rate_rad_s == pytest.approx(-0.4)
    assert decision.applied_yaw_rate_rad_s == pytest.approx(-0.4)


def test_assist_deadband_holds_j6() -> None:
    decision = compute_cube_yaw_assist(delta_j6_deg=1.0, timestamp=_stamp())
    assert decision.reason == "aligned"
    assert decision.desired_yaw_rate_rad_s == pytest.approx(0.0)
    assert decision.authority == pytest.approx(1.0)


def test_assist_no_detection_zeros_authority() -> None:
    decision = compute_cube_yaw_assist(delta_j6_deg=None, timestamp=_stamp())
    assert decision.reason == "no_detection"
    assert decision.authority == pytest.approx(0.0)
    assert decision.confidence == pytest.approx(0.0)
    assert decision.assist_action.inferred_target_id is None


def test_assist_human_override_drops_authority() -> None:
    decision = compute_cube_yaw_assist(
        delta_j6_deg=30.0,
        timestamp=_stamp(),
        human_wz_rad_s=0.4,
    )
    assert decision.reason == "human_override"
    assert decision.authority == pytest.approx(0.0)
    assert decision.desired_yaw_rate_rad_s == pytest.approx(0.4)
    assert decision.applied_yaw_rate_rad_s == pytest.approx(0.0)


def test_assist_deadman_released_freezes() -> None:
    decision = compute_cube_yaw_assist(
        delta_j6_deg=30.0,
        timestamp=_stamp(),
        deadman_active=False,
    )
    assert decision.reason == "deadman_released"
    assert decision.applied_yaw_rate_rad_s == pytest.approx(0.0)


def test_assist_gripper_closing_freezes() -> None:
    decision = compute_cube_yaw_assist(
        delta_j6_deg=30.0,
        timestamp=_stamp(),
        gripper_open_fraction=0.0,
    )
    assert decision.reason == "gripper_closing"
    assert decision.authority == pytest.approx(0.0)
    assert decision.confidence == pytest.approx(1.0)


def test_yaw_assist_mutually_exclusive_with_full_rotation() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        ManualCartesianConfig(allow_rotation=True, enable_yaw_assist=True)


def test_enable_yaw_assist_requires_policy() -> None:
    runner = build_manual_cartesian_runner(
        config=ManualCartesianConfig(enable_yaw_assist=True),
        teleop=_identity_teleop(),
        robot_state_source=MockRobotStateSource(),
        safety_filter=CartesianSafetyFilter(
            workspace=example_cartesian_workspace(),
            limits=CartesianSafetyLimits(),
        ),
        joint_commander=MockJointCommander(),
    )
    with pytest.raises(ValueError, match="yaw_assist_policy"):
        runner.step(now_monotonic_ns=1_000_000, dt_s=0.02)


def test_yaw_assist_overlays_j6_without_human_twist() -> None:
    runner = build_manual_cartesian_runner(
        config=ManualCartesianConfig(
            control_rate_hz=50.0,
            enable_yaw_assist=True,
            allow_tool_yaw=False,
            max_joint_step_deg=10.0,
        ),
        teleop=_identity_teleop(translation_raw=(1.0, 0.0, 0.0)),
        robot_state_source=MockRobotStateSource(
            joint_position_deg=(0.0, 15.0, 15.0, 0.0, 120.0, 3.0),
        ),
        safety_filter=CartesianSafetyFilter(
            workspace=example_cartesian_workspace(),
            limits=CartesianSafetyLimits(fixed_orientation_axes=(0, 1)),
        ),
        joint_commander=MockJointCommander(),
        yaw_assist_policy=_FixedDeltaYawAssist(30.0),
    )
    step = runner.step(now_monotonic_ns=1_000_000, dt_s=0.02)

    assert step.human_action.angular_velocity_rad_s == (0.0, 0.0, 0.0)
    assert step.assist_action is not None
    assert step.assist_action.angular_velocity_rad_s[2] == pytest.approx(0.4)
    assert step.executed_action.authority == pytest.approx(1.0)
    assert step.executed_action.joint_target_deg[:5] == pytest.approx((0.0, 15.0, 15.0, 0.0, 120.0))
    assert step.executed_action.joint_target_deg[5] == pytest.approx(13.0)
    assert step.requested_ee_position_m[0] == pytest.approx(-0.3 + 0.05 * 0.02)
    assert step.assist_reason == "assisting"


def test_yaw_assist_uses_joint_positive_sign_even_if_human_sign_flipped() -> None:
    runner = build_manual_cartesian_runner(
        config=ManualCartesianConfig(
            enable_yaw_assist=True,
            allow_tool_yaw=True,
            tool_yaw_sign=-1.0,
            max_joint_step_deg=10.0,
        ),
        teleop=_identity_teleop(),
        robot_state_source=MockRobotStateSource(
            joint_position_deg=(0.0, 15.0, 15.0, 0.0, 120.0, 3.0),
        ),
        safety_filter=CartesianSafetyFilter(
            workspace=example_cartesian_workspace(),
            limits=CartesianSafetyLimits(fixed_orientation_axes=(0, 1)),
        ),
        yaw_assist_policy=_FixedDeltaYawAssist(30.0),
    )
    step = runner.step(now_monotonic_ns=1_000_000, dt_s=0.02)
    assert step.executed_action.joint_target_deg[5] == pytest.approx(13.0)


def test_yaw_assist_no_detection_does_not_spin() -> None:
    runner = build_manual_cartesian_runner(
        config=ManualCartesianConfig(enable_yaw_assist=True, max_joint_step_deg=10.0),
        teleop=_identity_teleop(),
        robot_state_source=MockRobotStateSource(
            joint_position_deg=(0.0, 15.0, 15.0, 0.0, 120.0, 3.0),
        ),
        safety_filter=CartesianSafetyFilter(
            workspace=example_cartesian_workspace(),
            limits=CartesianSafetyLimits(),
        ),
        yaw_assist_policy=_FixedDeltaYawAssist(None),
    )
    step = runner.step(now_monotonic_ns=1_000_000, dt_s=0.02)
    assert step.executed_action.authority == pytest.approx(0.0)
    assert step.executed_action.joint_target_deg[5] == pytest.approx(3.0)
    assert step.assist_reason == "no_detection"


def test_human_twist_overrides_yaw_assist() -> None:
    runner = build_manual_cartesian_runner(
        config=ManualCartesianConfig(
            enable_yaw_assist=True,
            allow_tool_yaw=True,
            max_joint_step_deg=10.0,
        ),
        teleop=_identity_teleop(rotation_raw=(0.0, 0.0, 1.0), allow_tool_yaw=True),
        robot_state_source=MockRobotStateSource(
            joint_position_deg=(0.0, 15.0, 15.0, 0.0, 120.0, 3.0),
        ),
        safety_filter=CartesianSafetyFilter(
            workspace=example_cartesian_workspace(),
            limits=CartesianSafetyLimits(fixed_orientation_axes=(0, 1)),
        ),
        yaw_assist_policy=_FixedDeltaYawAssist(30.0),
    )
    step = runner.step(now_monotonic_ns=1_000_000, dt_s=0.02)
    assert step.human_action.angular_velocity_rad_s[2] == pytest.approx(0.4)
    assert step.executed_action.authority == pytest.approx(0.0)
    assert step.executed_action.joint_target_deg[5] == pytest.approx(3.0 + math.degrees(0.4) * 0.02)


def _dummy_homography() -> TableHomography:
    return TableHomography(
        H=np.eye(3, dtype=np.float64),
        image_width=64,
        image_height=48,
        inner_corners=(8, 11),
        square_m=0.015,
    )


class _HoldYawByShiftingJ6:
    """IK stand-in: change J6 to keep world-frame yaw while translating."""

    def solve(self, *, joint_seed_deg, target_position_m, target_rpy_rad):
        del target_position_m, target_rpy_rad
        joints = [float(value) for value in joint_seed_deg]
        joints[5] += 7.0
        return joints


class _RewindJ6:
    def solve(self, *, joint_seed_deg, target_position_m, target_rpy_rad):
        del target_position_m, target_rpy_rad
        joints = [float(value) for value in joint_seed_deg]
        joints[5] -= 15.0
        return joints


def test_yaw_assist_holds_current_yaw() -> None:
    fixed_rpy = (0.0, 1.5707963267948966, 0.0)
    drifted_rpy = (0.0, 1.5707963267948966, 0.4)
    runner = build_manual_cartesian_runner(
        config=ManualCartesianConfig(
            enable_yaw_assist=True,
            max_joint_step_deg=10.0,
            fixed_ee_rpy_rad=fixed_rpy,
        ),
        teleop=_identity_teleop(),
        robot_state_source=MockRobotStateSource(
            ee_rpy_rad=drifted_rpy,
            joint_position_deg=(0.0, 15.0, 15.0, 0.0, 120.0, 3.0),
        ),
        safety_filter=CartesianSafetyFilter(
            workspace=example_cartesian_workspace(),
            limits=CartesianSafetyLimits(
                fixed_ee_rpy_rad=fixed_rpy,
                fixed_orientation_axes=(0, 1),
            ),
        ),
        yaw_assist_policy=_FixedDeltaYawAssist(1.0),
    )
    step = runner.step(now_monotonic_ns=1_000_000, dt_s=0.02)
    assert step.requested_ee_rpy_rad[0] == pytest.approx(fixed_rpy[0])
    assert step.requested_ee_rpy_rad[1] == pytest.approx(fixed_rpy[1])
    assert step.requested_ee_rpy_rad[2] == pytest.approx(drifted_rpy[2])
    assert step.assist_reason == "aligned"
    assert step.executed_action.joint_target_deg[5] == pytest.approx(3.0)


def test_gripper_close_holds_current_j6() -> None:
    runner = build_manual_cartesian_runner(
        config=ManualCartesianConfig(enable_yaw_assist=True, max_joint_step_deg=10.0),
        teleop=_identity_teleop(gripper_target_open_fraction=0.0),
        robot_state_source=MockRobotStateSource(
            joint_position_deg=(0.0, 15.0, 15.0, 0.0, 120.0, 23.0),
        ),
        safety_filter=CartesianSafetyFilter(
            workspace=example_cartesian_workspace(),
            limits=CartesianSafetyLimits(fixed_orientation_axes=(0, 1)),
        ),
        yaw_assist_policy=ExternalCubeYawAssistPolicy(
            _dummy_homography(),
            locked_wrap90_deg=20.0,
            lock_on_first_detection=False,
        ),
    )
    step = runner.step(now_monotonic_ns=1_000_000, dt_s=0.02)
    assert step.assist_reason == "gripper_closing"
    assert step.executed_action.joint_target_deg[5] == pytest.approx(23.0)


def test_zero_yaw_bin_keeps_ik_j6_pose_lock() -> None:
    runner = build_manual_cartesian_runner(
        config=ManualCartesianConfig(enable_yaw_assist=True, max_joint_step_deg=10.0),
        teleop=_identity_teleop(),
        robot_state_source=MockRobotStateSource(
            joint_position_deg=(0.0, 15.0, 15.0, 0.0, 120.0, 3.0),
        ),
        safety_filter=CartesianSafetyFilter(
            workspace=example_cartesian_workspace(),
            limits=CartesianSafetyLimits(fixed_orientation_axes=(0, 1)),
        ),
        inverse_kinematics=_HoldYawByShiftingJ6(),
        yaw_assist_policy=_FixedDeltaYawAssist(1.0),
    )
    step = runner.step(now_monotonic_ns=1_000_000, dt_s=0.02)
    assert step.assist_reason == "aligned"
    assert step.executed_action.joint_target_deg[5] == pytest.approx(10.0)


def _mock_rpy(*, yaw_deg: float = 0.0) -> tuple[float, float, float]:
    return (0.0, math.pi / 2.0, math.radians(float(yaw_deg)))


def _ready_rpy(*, yaw_deg: float = 0.0) -> tuple[float, float, float]:
    return (math.pi, 0.0, math.radians(float(yaw_deg)))


def _policy_propose(policy, *, j6_now_deg: float = 0.0, ee_rpy_rad=None, **kwargs):
    return policy.propose(
        color_rgb=None,
        j6_now_deg=j6_now_deg,
        ee_rpy_rad=_mock_rpy() if ee_rpy_rad is None else ee_rpy_rad,
        human_wz_rad_s=0.0,
        deadman_active=True,
        gripper_open_fraction=1.0,
        timestamp=_stamp(),
        **kwargs,
    )


def test_ik_rewind_does_not_count_as_cube_alignment() -> None:
    policy = ExternalCubeYawAssistPolicy(
        _dummy_homography(),
        locked_wrap90_deg=20.0,
        lock_on_first_detection=False,
    )
    runner = build_manual_cartesian_runner(
        config=ManualCartesianConfig(enable_yaw_assist=True, max_joint_step_deg=10.0),
        teleop=_identity_teleop(),
        robot_state_source=MockRobotStateSource(
            joint_position_deg=(0.0, 15.0, 15.0, 0.0, 120.0, 10.0),
        ),
        safety_filter=CartesianSafetyFilter(
            workspace=example_cartesian_workspace(),
            limits=CartesianSafetyLimits(fixed_orientation_axes=(0, 1)),
        ),
        inverse_kinematics=_RewindJ6(),
        yaw_assist_policy=policy,
    )
    step = runner.step(now_monotonic_ns=1_000_000, dt_s=0.02)
    assert step.assist_reason == "assisting"
    assert step.executed_action.joint_target_deg[5] == pytest.approx(10.0)
    leftover = _policy_propose(policy, j6_now_deg=10.0)
    assert leftover.reason == "assisting"
    assert leftover.delta_j6_deg == pytest.approx(20.0)


def test_cube_yaw_overlays_error_on_pose_hold_ik() -> None:
    policy = ExternalCubeYawAssistPolicy(
        _dummy_homography(),
        locked_wrap90_deg=20.0,
        lock_on_first_detection=False,
    )
    runner = build_manual_cartesian_runner(
        config=ManualCartesianConfig(enable_yaw_assist=True, max_joint_step_deg=10.0),
        teleop=_identity_teleop(),
        robot_state_source=MockRobotStateSource(
            joint_position_deg=(0.0, 15.0, 15.0, 0.0, 120.0, 3.0),
        ),
        safety_filter=CartesianSafetyFilter(
            workspace=example_cartesian_workspace(),
            limits=CartesianSafetyLimits(fixed_orientation_axes=(0, 1)),
        ),
        inverse_kinematics=_HoldYawByShiftingJ6(),
        yaw_assist_policy=policy,
    )
    step = runner.step(now_monotonic_ns=1_000_000, dt_s=0.02)
    assert step.assist_reason == "assisting"
    assert step.executed_action.joint_target_deg[5] == pytest.approx(20.0)
    leftover = _policy_propose(policy, j6_now_deg=20.0)
    assert leftover.delta_j6_deg == pytest.approx(20.0)


def test_locked_wrap90_error_ignores_present_j6() -> None:
    policy = ExternalCubeYawAssistPolicy(
        _dummy_homography(),
        locked_wrap90_deg=20.0,
        lock_on_first_detection=False,
    )
    first = _policy_propose(policy, j6_now_deg=0.0, ee_rpy_rad=_ready_rpy())
    assert first.reason == "assisting"
    assert first.delta_j6_deg == pytest.approx(20.0)
    drifted = _policy_propose(policy, j6_now_deg=35.0, ee_rpy_rad=_ready_rpy())
    assert drifted.delta_j6_deg == pytest.approx(20.0)
    assert drifted.reason == "assisting"
    done = _policy_propose(policy, j6_now_deg=35.0, ee_rpy_rad=_ready_rpy(yaw_deg=-20.0))
    assert done.reason == "aligned"
    assert done.desired_yaw_rate_rad_s == pytest.approx(0.0)


def test_ready_pose_fk_aligns_when_jaw_heading_matches_cube() -> None:
    policy = ExternalCubeYawAssistPolicy(
        _dummy_homography(),
        locked_wrap90_deg=20.0,
        lock_on_first_detection=False,
    )
    opening = _policy_propose(policy, ee_rpy_rad=_ready_rpy())
    assert opening.delta_j6_deg == pytest.approx(20.0)
    # RealMan ready FK: J6=+20 → rpy yaw = -20°.
    aligned = _policy_propose(policy, ee_rpy_rad=_ready_rpy(yaw_deg=-20.0))
    assert aligned.reason == "aligned"
    assert aligned.delta_j6_deg == pytest.approx(0.0)


def test_near_zero_yaw_bin_is_aligned_even_if_j6_moved() -> None:
    policy = ExternalCubeYawAssistPolicy(
        _dummy_homography(),
        locked_wrap90_deg=-2.0,
        lock_on_first_detection=False,
    )
    start = _policy_propose(policy, j6_now_deg=0.0)
    assert start.reason == "aligned"
    moved = _policy_propose(policy, j6_now_deg=30.0)
    assert moved.reason == "aligned"
    assert moved.desired_yaw_rate_rad_s == pytest.approx(0.0)


def test_unlocked_policy_without_rgb_does_not_spin() -> None:
    policy = ExternalCubeYawAssistPolicy(_dummy_homography(), lock_on_first_detection=True)
    decision = _policy_propose(policy)
    assert decision.reason == "no_detection"
    assert policy.locked_wrap90_deg is None
