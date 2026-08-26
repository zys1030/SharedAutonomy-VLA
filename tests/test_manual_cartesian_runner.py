"""Offline unit tests for SpaceMouse mapping and manual Cartesian runner."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import pytest

pytestmark = pytest.mark.core

from sharedautonomy.assistance.safety_filter import (
    CartesianSafetyFilter,
    CartesianSafetyLimits,
    example_cartesian_workspace,
)
from sharedautonomy.control.manual import (
    ManualCartesianConfig,
    ManualCartesianRunner,
    MockJointCommander,
    MockRobotStateSource,
    build_manual_cartesian_runner,
    integrate_cartesian_velocity,
    overlay_tool_yaw_joint,
    passthrough_safety_pipeline,
)
from sharedautonomy.data.schema import CoordinateFrame, HumanAction, SampleTimestamp
from sharedautonomy.devices.spacemouse import (
    VERTICAL_UP_INSTALL_ROTATION,
    VERTICAL_UP_SIGN_CORRECTION,
    MockSpaceMouse,
    SpaceMouseAxes,
    SpaceMouseConfig,
    apply_deadzone,
    apply_transform,
    get_spacemouse_transform,
    map_raw_axes_to_base,
    spacemouse_axes_to_human_action,
)
from sharedautonomy.robot.safety import CartesianWorkspace, MotionDisabledError


def _identity_teleop(*, translation_raw=(0.0, 0.0, 1.0), speed_m_s=0.05, age_ms=0.0) -> MockSpaceMouse:
    identity = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    return MockSpaceMouse(
        SpaceMouseConfig(
            deadzone=0.0,
            max_linear_speed_m_s=speed_m_s,
            mount_orientation="custom",
            translation_transform=identity,
            rotation_transform=identity,
            input_timeout_s=0.1,
        ),
        translation_raw=translation_raw,
        deadman_active=True,
        input_age_ms=age_ms,
    )


def _safe_runner(**kwargs) -> ManualCartesianRunner:
    safety = CartesianSafetyFilter(
        workspace=example_cartesian_workspace(),
        limits=CartesianSafetyLimits(),
    )
    defaults = {
        "config": ManualCartesianConfig(control_rate_hz=50.0, enable_motion=False),
        "teleop": _identity_teleop(),
        "robot_state_source": MockRobotStateSource(),
        "safety_filter": safety,
        "joint_commander": MockJointCommander(),
    }
    defaults.update(kwargs)
    return build_manual_cartesian_runner(**defaults)


def test_vertical_up_transform_matches_try_sc_reference() -> None:
    state = np.array([1.0, 2.0, 3.0])
    legacy = apply_transform(state, get_spacemouse_transform("legacy_horizontal"))
    vertical = apply_transform(state, get_spacemouse_transform("vertical_up"))
    np.testing.assert_allclose(legacy, np.array([1.0, -3.0, 2.0]))
    np.testing.assert_allclose(vertical, np.array([-1.0, -2.0, 3.0]))
    np.testing.assert_allclose(
        vertical,
        VERTICAL_UP_SIGN_CORRECTION @ VERTICAL_UP_INSTALL_ROTATION @ legacy,
    )
    np.testing.assert_allclose(apply_deadzone(np.array([0.05, -0.2, 0.0]), 0.1), np.array([0.0, -0.2, 0.0]))


def test_spacemouse_axes_to_human_action_applies_deadman_and_speed_scale() -> None:
    config = SpaceMouseConfig(deadzone=0.0, max_linear_speed_m_s=0.05, allow_rotation=False)
    translation, rotation = map_raw_axes_to_base((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), config=config)
    axes = SpaceMouseAxes(
        translation=(float(translation[0]), float(translation[1]), float(translation[2])),
        rotation=(float(rotation[0]), float(rotation[1]), float(rotation[2])),
        deadman_active=True,
        gripper_button_edge=False,
        received_monotonic_ns=1_000,
        input_age_ms=5.0,
    )

    action = spacemouse_axes_to_human_action(
        axes,
        config=config,
        timestamp_utc=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        gripper_target_open_fraction=1.0,
    )

    assert action.deadman_active is True
    assert action.linear_velocity_m_s == pytest.approx(tuple(float(v) * 0.05 for v in translation))
    assert action.angular_velocity_rad_s == (0.0, 0.0, 0.0)


def test_spacemouse_zeros_velocity_when_deadman_released_or_stale() -> None:
    config = SpaceMouseConfig(deadzone=0.0, max_linear_speed_m_s=0.05, input_timeout_s=0.1)
    translation, rotation = map_raw_axes_to_base((1.0, 0.0, 0.0), (0.0, 0.0, 0.0), config=config)

    released = spacemouse_axes_to_human_action(
        SpaceMouseAxes(
            translation=(float(translation[0]), float(translation[1]), float(translation[2])),
            rotation=(float(rotation[0]), float(rotation[1]), float(rotation[2])),
            deadman_active=False,
            gripper_button_edge=False,
            received_monotonic_ns=1,
            input_age_ms=1.0,
        ),
        config=config,
        timestamp_utc=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        gripper_target_open_fraction=1.0,
    )
    stale = spacemouse_axes_to_human_action(
        SpaceMouseAxes(
            translation=(float(translation[0]), float(translation[1]), float(translation[2])),
            rotation=(float(rotation[0]), float(rotation[1]), float(rotation[2])),
            deadman_active=True,
            gripper_button_edge=False,
            received_monotonic_ns=1,
            input_age_ms=250.0,
        ),
        config=config,
        timestamp_utc=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        gripper_target_open_fraction=1.0,
    )

    assert released.linear_velocity_m_s == (0.0, 0.0, 0.0)
    assert released.deadman_active is False
    assert stale.linear_velocity_m_s == (0.0, 0.0, 0.0)
    assert stale.deadman_active is True


def test_integrate_cartesian_velocity() -> None:
    assert integrate_cartesian_velocity((-0.3, -0.1, 0.25), (0.05, 0.0, 0.0), dt_s=0.02) == pytest.approx(
        (-0.299, -0.1, 0.25)
    )


def test_manual_cartesian_runner_dry_run_integrates_without_sending() -> None:
    commander = MockJointCommander()
    runner = _safe_runner(joint_commander=commander)
    steps = runner.run_dry_run(steps=3, dt_s=0.02)

    assert len(steps) == 3
    assert commander.commands == []
    assert all(not step.motion_sent for step in steps)
    assert steps[0].requested_ee_position_m == pytest.approx((-0.3, -0.1, 0.251))
    assert steps[0].executed_action.joint_target_deg == pytest.approx((0.0, 15.0, 15.0, 0.0, 120.0, 0.0))
    assert steps[0].executed_action.actual_dt_s == pytest.approx(0.02)
    assert steps[0].executed_action.authority == 0.0
    assert steps[0].executed_action.safety_intervened is False


def test_safety_filter_holds_on_workspace_violation() -> None:
    narrow_workspace = CartesianWorkspace(
        polygon_xy_m=[[-0.2, -1.0], [-0.1, -1.0], [-0.1, 1.0], [-0.2, 1.0]],
        table_z_m=0.0,
        min_tool_clearance_m=0.0,
        tool_tip_offset_base_m=[0.0, 0.0, 0.0],
    )
    runner = _safe_runner(
        teleop=_identity_teleop(translation_raw=(1.0, 0.0, 0.0), speed_m_s=5.0),
        robot_state_source=MockRobotStateSource(ee_position_m=(-0.155, -0.05, 0.25)),
        safety_filter=CartesianSafetyFilter(
            workspace=narrow_workspace,
            limits=CartesianSafetyLimits(max_speed_m_s=5.0, max_acceleration_m_s2=250.0),
        ),
    )

    step = runner.step(now_monotonic_ns=1_000_000, dt_s=0.02)

    assert step.requested_ee_position_m[0] == pytest.approx(-0.055)
    assert step.executed_action.safety_intervened is True
    assert "workspace_violation" in step.executed_action.safety_reasons
    assert step.executed_action.linear_velocity_m_s == pytest.approx((0.0, 0.0, 0.0))


def test_hold_flange_z_pins_height_despite_measured_drop() -> None:
    """Zeroing vz alone ratchets Z down; hold_flange_z_m retargets absolute height."""
    robot = MockRobotStateSource(ee_position_m=(-0.30, -0.10, 0.20))
    runner = _safe_runner(
        config=ManualCartesianConfig(
            control_rate_hz=50.0,
            enable_motion=False,
            hold_flange_z_m=0.25,
        ),
        teleop=_identity_teleop(translation_raw=(1.0, 0.0, 0.0), speed_m_s=0.05),
        robot_state_source=robot,
        safety_filter=CartesianSafetyFilter(
            workspace=example_cartesian_workspace(),
            limits=CartesianSafetyLimits(max_speed_m_s=0.05, max_acceleration_m_s2=100.0),
        ),
    )
    step = runner.step(now_monotonic_ns=1_000_000, dt_s=0.02)
    assert step.requested_ee_position_m[2] == pytest.approx(0.25)
    assert step.robot_state.ee_position_m[2] == pytest.approx(0.20)


def test_safety_filter_holds_on_stale_input() -> None:
    runner = _safe_runner(teleop=_identity_teleop(age_ms=250.0))
    step = runner.step(now_monotonic_ns=1_000_000_000, dt_s=0.02)

    assert step.executed_action.safety_intervened is True
    assert "stale_input" in step.executed_action.safety_reasons
    assert step.executed_action.linear_velocity_m_s == pytest.approx((0.0, 0.0, 0.0))


def test_safety_filter_limits_overspeed_request() -> None:
    runner = _safe_runner(
        teleop=_identity_teleop(translation_raw=(0.0, 0.0, 1.0), speed_m_s=1.0),
        safety_filter=CartesianSafetyFilter(
            workspace=example_cartesian_workspace(),
            limits=CartesianSafetyLimits(max_speed_m_s=0.05, max_acceleration_m_s2=100.0),
        ),
    )
    step = runner.step(now_monotonic_ns=1_000_000, dt_s=0.02)

    assert step.requested_ee_position_m == pytest.approx((-0.3, -0.1, 0.27))
    assert step.executed_action.safety_intervened is True
    assert "speed_or_acceleration_limit" in step.executed_action.safety_reasons
    safe_z = -0.1 * 0 + 0.25 + 0.05 * 0.02
    assert step.robot_state.ee_position_m[2] + step.executed_action.linear_velocity_m_s[
        2
    ] * 0.02 == pytest.approx(safe_z)


def test_manual_cartesian_runner_requires_commander_when_motion_enabled() -> None:
    runner = ManualCartesianRunner(
        config=ManualCartesianConfig(enable_motion=True),
        teleop=_identity_teleop(),
        robot_state_source=MockRobotStateSource(),
        safety_pipeline=passthrough_safety_pipeline,
        joint_commander=None,
    )
    with pytest.raises(MotionDisabledError, match="joint_commander"):
        runner.step(now_monotonic_ns=0, dt_s=0.02)


def test_manual_cartesian_runner_sends_only_when_motion_enabled() -> None:
    commander = MockJointCommander()
    runner = _safe_runner(
        config=ManualCartesianConfig(enable_motion=True),
        teleop=_identity_teleop(translation_raw=(1.0, 0.0, 0.0)),
        joint_commander=commander,
    )

    step = runner.step(now_monotonic_ns=10_000_000, dt_s=0.02)

    assert step.motion_sent is True
    assert len(commander.commands) == 1
    assert commander.commands[0] == pytest.approx([0.0, 15.0, 15.0, 0.0, 120.0, 0.0])


def test_run_dry_run_rejects_enabled_motion() -> None:
    runner = _safe_runner(config=ManualCartesianConfig(enable_motion=True))
    with pytest.raises(MotionDisabledError, match="run_dry_run"):
        runner.run_dry_run(steps=1)


def test_rotation_flags_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        SpaceMouseConfig(allow_rotation=True, allow_tool_yaw=True)
    with pytest.raises(ValueError, match="mutually exclusive"):
        ManualCartesianConfig(allow_rotation=True, allow_tool_yaw=True)


def test_allow_tool_yaw_keeps_wz_drops_tilt() -> None:
    identity = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    config = SpaceMouseConfig(
        deadzone=0.0,
        max_linear_speed_m_s=0.05,
        max_angular_speed_rad_s=0.4,
        mount_orientation="custom",
        translation_transform=identity,
        rotation_transform=identity,
        allow_tool_yaw=True,
    )
    translation, rotation = map_raw_axes_to_base((0.0, 0.0, 0.0), (0.5, -0.8, 1.0), config=config)
    action = spacemouse_axes_to_human_action(
        SpaceMouseAxes(
            translation=(float(translation[0]), float(translation[1]), float(translation[2])),
            rotation=(float(rotation[0]), float(rotation[1]), float(rotation[2])),
            deadman_active=True,
            gripper_button_edge=False,
            received_monotonic_ns=1_000,
            input_age_ms=5.0,
        ),
        config=config,
        timestamp_utc=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
        gripper_target_open_fraction=1.0,
    )
    assert action.angular_velocity_rad_s == pytest.approx((0.0, 0.0, 0.4))
    assert action.linear_velocity_m_s == pytest.approx((0.0, 0.0, 0.0))


def test_compact_yaw_maps_to_base_wz_with_vertical_up_90() -> None:
    config = SpaceMouseConfig(
        deadzone=0.0,
        allow_tool_yaw=True,
        max_angular_speed_rad_s=0.4,
        mount_orientation="vertical_up",
        base_xy_yaw_deg=90.0,
    )
    _, rotation = map_raw_axes_to_base((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), config=config)
    assert rotation[0] == pytest.approx(0.0)
    assert rotation[1] == pytest.approx(0.0)
    assert abs(rotation[2]) == pytest.approx(1.0)


def test_overlay_tool_yaw_joint_adds_delta_on_ik_j6() -> None:
    result = overlay_tool_yaw_joint(
        [0.1, 15.0, 15.0, 0.0, 120.0, 9.0],
        0.4,
        dt_s=0.02,
        sign=1.0,
    )
    assert result[:5] == pytest.approx([0.1, 15.0, 15.0, 0.0, 120.0])
    assert result[5] == pytest.approx(9.0 + math.degrees(0.4) * 0.02)


def test_allow_tool_yaw_overlays_j6_keeps_tilt() -> None:
    identity = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    fixed_rpy = (0.0, 1.5707963267948966, 0.0)
    teleop = MockSpaceMouse(
        SpaceMouseConfig(
            deadzone=0.0,
            max_linear_speed_m_s=0.05,
            max_angular_speed_rad_s=0.4,
            mount_orientation="custom",
            translation_transform=identity,
            rotation_transform=identity,
            allow_tool_yaw=True,
            input_timeout_s=0.1,
        ),
        translation_raw=(0.0, 0.0, 0.0),
        rotation_raw=(0.5, -0.8, 1.0),
        deadman_active=True,
    )
    runner = _safe_runner(
        config=ManualCartesianConfig(
            control_rate_hz=50.0,
            enable_motion=False,
            allow_tool_yaw=True,
            fixed_ee_rpy_rad=fixed_rpy,
        ),
        teleop=teleop,
        robot_state_source=MockRobotStateSource(
            ee_rpy_rad=fixed_rpy,
            joint_position_deg=(0.0, 15.0, 15.0, 0.0, 120.0, 3.0),
        ),
        safety_filter=CartesianSafetyFilter(
            workspace=example_cartesian_workspace(),
            limits=CartesianSafetyLimits(
                fixed_ee_rpy_rad=fixed_rpy,
                fixed_orientation_axes=(0, 1),
            ),
        ),
    )
    step = runner.step(now_monotonic_ns=1_000_000, dt_s=0.02)

    assert step.human_action.angular_velocity_rad_s == pytest.approx((0.0, 0.0, 0.4))
    assert step.requested_ee_rpy_rad[0] == pytest.approx(fixed_rpy[0])
    assert step.requested_ee_rpy_rad[1] == pytest.approx(fixed_rpy[1])
    assert step.executed_action.joint_target_deg[:5] == pytest.approx((0.0, 15.0, 15.0, 0.0, 120.0))
    assert step.executed_action.joint_target_deg[5] == pytest.approx(3.0 + math.degrees(0.4) * 0.02)
    assert step.executed_action.safety_intervened is False


class _HoldYawByShiftingJ6:
    """Stand-in IK that changes J6 to keep world-frame yaw while translating."""

    def solve(self, *, joint_seed_deg, target_position_m, target_rpy_rad):
        del target_position_m, target_rpy_rad
        joints = [float(value) for value in joint_seed_deg]
        joints[5] += 7.0
        return joints


def test_tool_yaw_translation_keeps_ik_j6_counter_rotation() -> None:
    identity = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    fixed_rpy = (0.0, 1.5707963267948966, 0.0)
    teleop = MockSpaceMouse(
        SpaceMouseConfig(
            deadzone=0.0,
            max_linear_speed_m_s=0.05,
            mount_orientation="custom",
            translation_transform=identity,
            rotation_transform=identity,
            allow_tool_yaw=True,
            input_timeout_s=0.1,
        ),
        translation_raw=(1.0, 0.0, 0.0),
        rotation_raw=(0.0, 0.0, 0.0),
        deadman_active=True,
    )
    runner = _safe_runner(
        config=ManualCartesianConfig(
            control_rate_hz=50.0,
            enable_motion=False,
            allow_tool_yaw=True,
            fixed_ee_rpy_rad=fixed_rpy,
            max_joint_step_deg=10.0,
        ),
        teleop=teleop,
        robot_state_source=MockRobotStateSource(
            ee_rpy_rad=fixed_rpy,
            joint_position_deg=(0.0, 15.0, 15.0, 0.0, 120.0, 3.0),
        ),
        safety_filter=CartesianSafetyFilter(
            workspace=example_cartesian_workspace(),
            limits=CartesianSafetyLimits(
                fixed_ee_rpy_rad=fixed_rpy,
                fixed_orientation_axes=(0, 1),
            ),
        ),
        inverse_kinematics=_HoldYawByShiftingJ6(),
    )
    step = runner.step(now_monotonic_ns=1_000_000, dt_s=0.02)

    assert step.human_action.angular_velocity_rad_s == pytest.approx((0.0, 0.0, 0.0))
    assert step.executed_action.joint_target_deg[5] == pytest.approx(10.0)


def test_allow_tool_yaw_false_still_freezes_rpy() -> None:
    identity = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    fixed_rpy = (0.0, 1.5707963267948966, 0.0)
    teleop = MockSpaceMouse(
        SpaceMouseConfig(
            deadzone=0.0,
            max_linear_speed_m_s=0.05,
            max_angular_speed_rad_s=0.4,
            mount_orientation="custom",
            translation_transform=identity,
            rotation_transform=identity,
            allow_tool_yaw=True,
            input_timeout_s=0.1,
        ),
        rotation_raw=(0.0, 0.0, 1.0),
        deadman_active=True,
    )
    runner = _safe_runner(
        config=ManualCartesianConfig(
            control_rate_hz=50.0,
            enable_motion=False,
            allow_tool_yaw=False,
            fixed_ee_rpy_rad=fixed_rpy,
        ),
        teleop=teleop,
        robot_state_source=MockRobotStateSource(
            ee_rpy_rad=fixed_rpy,
            joint_position_deg=(0.0, 15.0, 15.0, 0.0, 120.0, 3.0),
        ),
    )
    step = runner.step(now_monotonic_ns=1_000_000, dt_s=0.02)

    assert step.human_action.angular_velocity_rad_s == (0.0, 0.0, 0.0)
    assert step.requested_ee_rpy_rad == pytest.approx(fixed_rpy)
    assert step.executed_action.joint_target_deg[5] == pytest.approx(3.0)


def test_safety_filter_allows_yaw_when_tilt_axes_only() -> None:
    now_ns = 1_000_000_000
    fixed_rpy = (0.0, 1.5708, 0.0)
    robot = MockRobotStateSource(ee_position_m=(-0.30, -0.10, 0.25), ee_rpy_rad=fixed_rpy)
    safety = CartesianSafetyFilter(
        workspace=example_cartesian_workspace(),
        limits=CartesianSafetyLimits(
            fixed_ee_rpy_rad=fixed_rpy,
            fixed_orientation_axes=(0, 1),
            orientation_tolerance_rad=0.05,
        ),
    )
    state = robot.read_cartesian_state(now_monotonic_ns=now_ns)
    human = HumanAction(
        timestamp=SampleTimestamp(
            timestamp_utc=datetime(2026, 8, 17, tzinfo=UTC),
            received_monotonic_ns=now_ns,
        ),
        linear_velocity_m_s=(0.0, 0.0, 0.0),
        angular_velocity_rad_s=(0.0, 0.0, 0.4),
        gripper_target_open_fraction=1.0,
        deadman_active=True,
        input_age_ms=0.0,
        reference_frame=CoordinateFrame.BASE,
    )
    position, rpy, intervened, reasons = safety(
        state,
        (-0.30, -0.10, 0.25),
        (0.0, 1.5708, 0.5),
        0.02,
        human,
        now_monotonic_ns=now_ns,
    )
    assert intervened is False
    assert reasons == ()
    assert rpy[2] == pytest.approx(0.5)
    assert position == pytest.approx((-0.30, -0.10, 0.25))


def test_safety_filter_tilt_fault_freezes_translation_keeps_current_yaw() -> None:
    now_ns = 1_000_000_000
    fixed_rpy = (0.0, 1.5708, 0.1)
    robot = MockRobotStateSource(ee_position_m=(-0.30, -0.10, 0.25), ee_rpy_rad=fixed_rpy)
    safety = CartesianSafetyFilter(
        workspace=example_cartesian_workspace(),
        limits=CartesianSafetyLimits(
            fixed_ee_rpy_rad=(0.0, 1.5708, 0.0),
            fixed_orientation_axes=(0, 1),
            orientation_tolerance_rad=0.05,
        ),
    )
    state = robot.read_cartesian_state(now_monotonic_ns=now_ns)
    human = HumanAction(
        timestamp=SampleTimestamp(
            timestamp_utc=datetime(2026, 8, 17, tzinfo=UTC),
            received_monotonic_ns=now_ns,
        ),
        linear_velocity_m_s=(0.05, 0.0, 0.0),
        angular_velocity_rad_s=(0.0, 0.0, 0.4),
        gripper_target_open_fraction=1.0,
        deadman_active=True,
        input_age_ms=0.0,
        reference_frame=CoordinateFrame.BASE,
    )
    position, rpy, intervened, reasons = safety(
        state,
        (-0.29, -0.10, 0.25),
        (0.2, 1.5708, 0.5),
        0.02,
        human,
        now_monotonic_ns=now_ns,
    )
    assert intervened is True
    assert "fixed_orientation" in reasons
    assert position == pytest.approx((-0.30, -0.10, 0.25))
    assert rpy[0] == pytest.approx(0.0)
    assert rpy[1] == pytest.approx(1.5708)
    assert rpy[2] == pytest.approx(0.1)
