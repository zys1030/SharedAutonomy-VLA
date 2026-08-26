"""Tests for pure demonstration-collection runtime resolution."""

from __future__ import annotations

import pytest
from sharedautonomy.control.collection_runtime import resolve_collection_runtime

pytestmark = pytest.mark.core


def test_no_motion_defaults_preserve_preview_profile() -> None:
    runtime = resolve_collection_runtime(
        motion_enabled=False,
        control_hz=None,
        duration_s=40.0,
        steps=None,
        max_linear_speed_m_s=None,
        move_increment_m=None,
    )

    assert runtime.control_hz == 50.0
    assert runtime.period_s == pytest.approx(0.02)
    assert runtime.duration_s == 40.0
    assert runtime.total_steps == 2000
    assert runtime.move_increment_m == pytest.approx(0.001)
    assert runtime.max_linear_speed_m_s == pytest.approx(0.05)
    assert runtime.max_speed_m_s_per_axis is None
    assert runtime.max_acceleration_m_s2 == pytest.approx(2.5)
    assert runtime.input_timeout_s == pytest.approx(0.1)
    assert runtime.robot_state_timeout_s == pytest.approx(0.05)
    assert runtime.speed_source == "default_no_motion_0.05"


def test_motion_defaults_preserve_anisotropic_collection_profile() -> None:
    runtime = resolve_collection_runtime(
        motion_enabled=True,
        control_hz=None,
        duration_s=40.0,
        steps=None,
        max_linear_speed_m_s=None,
        move_increment_m=None,
    )

    assert runtime.control_hz == 10.0
    assert runtime.period_s == pytest.approx(0.1)
    assert runtime.total_steps == 400
    assert runtime.move_increment_xy_m == pytest.approx(0.02)
    assert runtime.move_increment_z_m == pytest.approx(0.01)
    assert runtime.max_speed_m_s_per_axis == pytest.approx((0.2, 0.2, 0.1))
    assert runtime.max_linear_speed_m_s == pytest.approx(0.2)
    assert runtime.max_acceleration_m_s2 == pytest.approx(2.0)
    assert runtime.input_timeout_s == pytest.approx(0.2)
    assert runtime.robot_state_timeout_s == pytest.approx(0.05)


def test_steps_override_duration_and_explicit_speed_is_isotropic() -> None:
    runtime = resolve_collection_runtime(
        motion_enabled=True,
        control_hz=20.0,
        duration_s=999.0,
        steps=5,
        max_linear_speed_m_s=0.04,
        move_increment_m=None,
    )

    assert runtime.duration_s == pytest.approx(0.25)
    assert runtime.total_steps == 5
    assert runtime.move_increment_m == pytest.approx(0.002)
    assert runtime.max_speed_m_s_per_axis is None
    assert runtime.speed_report_fields() == {
        "move_increment_mm": 2.0,
        "move_increment_xy_mm": 2.0,
        "move_increment_z_mm": 2.0,
        "max_linear_speed_mm_s": 40.0,
        "max_linear_speed_xy_mm_s": 40.0,
        "max_linear_speed_z_mm_s": 40.0,
    }
    assert runtime.startup_report_fields()["max_linear_speed_source"] == (
        "cli --max-linear-speed-m-s"
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"control_hz": 0.0}, "control-hz must be positive"),
        ({"duration_s": 0.0}, "duration-s must be positive"),
        ({"steps": 0}, "steps must be >= 1"),
        ({"max_linear_speed_m_s": -0.1}, "max-linear-speed-m-s must be positive"),
        ({"motion_enabled": True, "move_increment_m": 0.0}, "move-increment-m must be positive"),
        (
            {"max_linear_speed_m_s": 0.1, "move_increment_m": 0.01},
            "Pass only one",
        ),
    ],
)
def test_invalid_runtime_settings_fail_closed(overrides: dict[str, object], message: str) -> None:
    kwargs: dict[str, object] = {
        "motion_enabled": False,
        "control_hz": None,
        "duration_s": 40.0,
        "steps": None,
        "max_linear_speed_m_s": None,
        "move_increment_m": None,
    }
    kwargs.update(overrides)

    with pytest.raises(ValueError, match=message):
        resolve_collection_runtime(**kwargs)  # type: ignore[arg-type]
