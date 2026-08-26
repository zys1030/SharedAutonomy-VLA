"""Cube wrap90 yaw and start-of-episode bin resolution."""

from __future__ import annotations

import math

import numpy as np
import pytest
from sharedautonomy.perception.cube_yaw import (
    CubeYawEstimate,
    StartYawError,
    cube_gripper_wrap90_error_deg,
    gripper_jaw_table_heading_deg,
    resolve_start_yaw_bin,
    should_measure_start_yaw,
    wrap90_j6_error_deg,
    wrap_square_yaw_deg,
)

pytestmark = pytest.mark.core


def _estimate(wrap90: float, *, table: float | None = None, delta: float | None = None) -> CubeYawEstimate:
    yaw = float(wrap90)
    return CubeYawEstimate(
        yaw_table_deg=float(table if table is not None else yaw),
        yaw_wrap90_deg=yaw,
        delta_j6_deg=float(delta if delta is not None else yaw),
        center_uv=(0.0, 0.0),
        box_uv=np.zeros((4, 2), dtype=np.float64),
        area_px=100.0,
    )


def test_wrap_square_keeps_plus_45() -> None:
    assert wrap_square_yaw_deg(45.0) == 45.0
    assert wrap_square_yaw_deg(-45.0) == 45.0


def test_wrap_square_folds_90_period() -> None:
    assert wrap_square_yaw_deg(0.0) == 0.0
    assert wrap_square_yaw_deg(90.0) == 0.0
    assert wrap_square_yaw_deg(46.0) == pytest.approx(-44.0)


def test_resolve_start_yaw_uses_measured_wrap90() -> None:
    yaw, extras = resolve_start_yaw_bin(
        cli_yaw_bin_deg=None,
        estimate=_estimate(42.0, table=42.0, delta=41.0),
        required=True,
    )
    assert yaw == pytest.approx(42.0)
    assert extras["start_yaw_source"] == "measured"
    assert extras["start_delta_j6_deg"] == pytest.approx(41.0)


def test_resolve_start_yaw_cli_overrides_measurement() -> None:
    yaw, extras = resolve_start_yaw_bin(
        cli_yaw_bin_deg=45.0,
        estimate=_estimate(12.0),
        required=True,
    )
    assert yaw == pytest.approx(45.0)
    assert extras["start_yaw_source"] == "cli_override"
    assert extras["start_yaw_wrap90_deg"] == pytest.approx(12.0)


def test_resolve_start_yaw_required_without_measurement_raises() -> None:
    with pytest.raises(StartYawError, match="start cube yaw is required"):
        resolve_start_yaw_bin(cli_yaw_bin_deg=None, estimate=None, required=True)


def test_resolve_start_yaw_optional_can_be_null() -> None:
    yaw, extras = resolve_start_yaw_bin(cli_yaw_bin_deg=None, estimate=None, required=False)
    assert yaw is None
    assert extras == {}


def test_wrap90_j6_error_is_wrap90_of_target_minus_joint() -> None:
    assert wrap90_j6_error_deg(20.0, 0.0) == pytest.approx(20.0)
    assert wrap90_j6_error_deg(20.0, 20.0) == pytest.approx(0.0)
    assert wrap90_j6_error_deg(20.0, 25.0) == pytest.approx(-5.0)


def test_ready_pose_jaw_heading_tracks_table_yaw_not_j6_encoder() -> None:
    lock = gripper_jaw_table_heading_deg((math.pi, 0.0, 0.0))
    assert lock == pytest.approx(0.0)
    plus_j6 = gripper_jaw_table_heading_deg((math.pi, 0.0, math.radians(-20.0)))
    assert plus_j6 == pytest.approx(-20.0)
    assert cube_gripper_wrap90_error_deg(20.0, lock, lock) == pytest.approx(20.0)
    assert cube_gripper_wrap90_error_deg(20.0, plus_j6, lock) == pytest.approx(0.0)
    assert cube_gripper_wrap90_error_deg(0.0, plus_j6, lock) == pytest.approx(-20.0)


def test_should_measure_start_yaw_for_assist_and_rotated_manual() -> None:
    assert should_measure_start_yaw(
        cli_measure_start_yaw=None,
        recording=True,
        allow_tool_yaw=False,
        enable_yaw_assist=True,
    )
    assert should_measure_start_yaw(
        cli_measure_start_yaw=None,
        recording=True,
        allow_tool_yaw=True,
        enable_yaw_assist=False,
    )
    assert not should_measure_start_yaw(
        cli_measure_start_yaw=None,
        recording=True,
        allow_tool_yaw=False,
        enable_yaw_assist=False,
    )
    assert not should_measure_start_yaw(
        cli_measure_start_yaw=False,
        recording=True,
        allow_tool_yaw=False,
        enable_yaw_assist=True,
    )
