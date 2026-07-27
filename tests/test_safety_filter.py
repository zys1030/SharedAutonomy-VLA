import math

import pytest

pytestmark = pytest.mark.core

from sharedautonomy.robot.safety import (
    CartesianSafetyError,
    CartesianWorkspace,
    clip_joint_targets,
    limit_cartesian_target,
    validate_cartesian_segment,
    validate_fixed_orientation,
    validate_signal_age,
)


@pytest.fixture
def stamp_workspace() -> CartesianWorkspace:
    return CartesianWorkspace(
        polygon_xy_m=[
            [-0.456, 0.107],
            [-0.387, -0.236],
            [-0.170, -0.420],
            [-0.150, 0.068],
        ],
        table_z_m=0.0,
        min_tool_clearance_m=0.0,
        tool_tip_offset_base_m=[0.0, 0.0, -0.178],
    )


def test_clip_joint_targets_limits_each_step() -> None:
    result = clip_joint_targets(
        present_deg=[0, 0, 0, 0, 0, 0],
        target_deg=[5, -5, 0.5, -0.5, 2, -2],
        max_step_deg=1,
    )

    assert result == [1, -1, 0.5, -0.5, 1, -1]


def test_clip_joint_targets_applies_hard_limits() -> None:
    result = clip_joint_targets(
        present_deg=[0, 0],
        target_deg=[50, -50],
        max_step_deg=[100, 100],
        joint_limits_deg=[[-10, 10], [-20, 20]],
    )

    assert result == [10, -20]


@pytest.mark.parametrize("bad_value", [math.nan, math.inf, -math.inf])
def test_clip_joint_targets_rejects_non_finite_values(bad_value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        clip_joint_targets([0], [bad_value], 1)


def test_stamp_workspace_derives_178_mm_minimum_flange_z(
    stamp_workspace: CartesianWorkspace,
) -> None:
    assert stamp_workspace.min_flange_z_m == pytest.approx(0.178)


def test_cartesian_segment_accepts_inside_workspace(
    stamp_workspace: CartesianWorkspace,
) -> None:
    target = validate_cartesian_segment(
        [-0.35, -0.05, 0.250],
        [-0.30, -0.10, 0.178],
        stamp_workspace,
    )
    assert target == [-0.30, -0.10, 0.178]


def test_cartesian_segment_rejects_xy_outside_workspace(
    stamp_workspace: CartesianWorkspace,
) -> None:
    with pytest.raises(CartesianSafetyError, match="outside"):
        validate_cartesian_segment(
            [-0.35, -0.05, 0.250],
            [-0.10, -0.10, 0.250],
            stamp_workspace,
        )


def test_cartesian_segment_rejects_table_clearance_violation(
    stamp_workspace: CartesianWorkspace,
) -> None:
    with pytest.raises(CartesianSafetyError, match="minimum safe Z"):
        validate_cartesian_segment(
            [-0.35, -0.05, 0.250],
            [-0.30, -0.10, 0.177],
            stamp_workspace,
        )


def test_cartesian_workspace_rejects_non_convex_polygon() -> None:
    with pytest.raises(ValueError, match="convex"):
        CartesianWorkspace(
            polygon_xy_m=[[0, 0], [1, 0], [0.5, 0.25], [1, 1], [0, 1]],
            table_z_m=0,
            min_tool_clearance_m=0,
            tool_tip_offset_base_m=[0, 0, 0],
        )


def test_limit_cartesian_target_uses_measured_dt() -> None:
    target, velocity = limit_cartesian_target(
        [0, 0, 0],
        [1, 0, 0],
        dt_s=0.02,
        max_speed_m_s=0.1,
    )
    assert target == pytest.approx([0.002, 0, 0])
    assert velocity == pytest.approx([0.1, 0, 0])


def test_limit_cartesian_target_limits_acceleration() -> None:
    target, velocity = limit_cartesian_target(
        [0, 0, 0],
        [1, 0, 0],
        dt_s=0.02,
        max_speed_m_s=0.1,
        previous_velocity_m_s=[0, 0, 0],
        max_acceleration_m_s2=0.5,
    )
    assert target == pytest.approx([0.0002, 0, 0])
    assert velocity == pytest.approx([0.01, 0, 0])


def test_limit_cartesian_target_per_axis_clips_xy_and_z_independently() -> None:
    target, velocity = limit_cartesian_target(
        [0, 0, 0],
        [1, 1, 1],
        dt_s=0.1,
        max_speed_m_s=0.2,
        max_speed_m_s_per_axis=(0.2, 0.2, 0.1),
    )
    assert velocity == pytest.approx([0.2, 0.2, 0.1])
    assert target == pytest.approx([0.02, 0.02, 0.01])


def test_fixed_orientation_handles_wrapped_angles() -> None:
    validate_fixed_orientation(
        [0, 0, math.pi],
        [0, 0, -math.pi],
        tolerance_rad=0.01,
    )
    with pytest.raises(CartesianSafetyError, match="fixed reference"):
        validate_fixed_orientation([0, 0, 0], [0.02, 0, 0], tolerance_rad=0.01)


def test_signal_age_rejects_stale_and_future_data() -> None:
    validate_signal_age(1_000_000_000, 1_010_000_000, max_age_s=0.02)
    with pytest.raises(CartesianSafetyError, match="stale"):
        validate_signal_age(1_000_000_000, 1_030_000_000, max_age_s=0.02)
    with pytest.raises(CartesianSafetyError, match="future"):
        validate_signal_age(1_010_000_000, 1_000_000_000, max_age_s=0.02)
