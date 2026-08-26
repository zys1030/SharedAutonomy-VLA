"""Pure runtime-parameter resolution for demonstration collection."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_NO_MOTION_SPEED_M_S = 0.05
DEFAULT_MOTION_MOVE_INCREMENT_M = 0.01
DEFAULT_MOTION_MOVE_INCREMENT_XY_SCALE = 2.0
DEFAULT_NO_MOTION_CONTROL_HZ = 50.0
DEFAULT_MOTION_CONTROL_HZ = 10.0
MIN_INPUT_TIMEOUT_S = 0.1
MIN_ROBOT_STATE_TIMEOUT_S = 0.05


@dataclass(frozen=True, slots=True)
class ResolvedCollectionRuntime:
    """Validated cadence and Cartesian speed settings for one collection run."""

    motion_enabled: bool
    control_hz: float
    period_s: float
    duration_s: float
    total_steps: int
    move_increment_m: float
    move_increment_xy_m: float
    move_increment_z_m: float
    max_linear_speed_m_s: float
    max_linear_speed_xy_m_s: float
    max_linear_speed_z_m_s: float
    max_speed_m_s_per_axis: tuple[float, float, float] | None
    max_acceleration_m_s2: float
    input_timeout_s: float
    robot_state_timeout_s: float
    speed_source: str

    def effective_config_fields(self) -> dict[str, object]:
        """Return traceable runtime fields in SI units."""
        return {
            "control_hz": self.control_hz,
            "duration_s": self.duration_s,
            "planned_steps": self.total_steps,
            "move_increment_m": self.move_increment_m,
            "move_increment_xy_m": self.move_increment_xy_m,
            "move_increment_z_m": self.move_increment_z_m,
            "max_linear_speed_m_s": self.max_linear_speed_m_s,
            "max_linear_speed_xy_m_s": self.max_linear_speed_xy_m_s,
            "max_linear_speed_z_m_s": self.max_linear_speed_z_m_s,
            "max_speed_m_s_per_axis": (
                None
                if self.max_speed_m_s_per_axis is None
                else list(self.max_speed_m_s_per_axis)
            ),
        }

    def speed_report_fields(self) -> dict[str, float]:
        """Return operator-facing speed fields in millimetres."""
        return {
            "move_increment_mm": round(self.move_increment_m * 1000.0, 3),
            "move_increment_xy_mm": round(self.move_increment_xy_m * 1000.0, 3),
            "move_increment_z_mm": round(self.move_increment_z_m * 1000.0, 3),
            "max_linear_speed_mm_s": round(self.max_linear_speed_m_s * 1000.0, 3),
            "max_linear_speed_xy_mm_s": round(self.max_linear_speed_xy_m_s * 1000.0, 3),
            "max_linear_speed_z_mm_s": round(self.max_linear_speed_z_m_s * 1000.0, 3),
        }

    def startup_report_fields(self) -> dict[str, float | int | str]:
        """Return cadence and speed fields for the startup report."""
        return {
            "duration_s": self.duration_s,
            "planned_steps": self.total_steps,
            "control_hz": self.control_hz,
            **self.speed_report_fields(),
            "max_linear_speed_source": self.speed_source,
        }

    def full_stick_hint(self) -> str:
        """Describe the full-stick Cartesian command for the operator."""
        if self.max_speed_m_s_per_axis is not None:
            return (
                f"Full stick ~= {self.move_increment_xy_m * 1000.0:.1f} mm/tick XY "
                f"({self.max_linear_speed_xy_m_s * 1000.0:.1f} mm/s), "
                f"{self.move_increment_z_m * 1000.0:.1f} mm/tick Z "
                f"({self.max_linear_speed_z_m_s * 1000.0:.1f} mm/s). "
            )
        return (
            f"Full stick ~= {self.move_increment_m * 1000.0:.1f} mm/tick "
            f"({self.max_linear_speed_m_s * 1000.0:.1f} mm/s). "
        )


def resolve_collection_runtime(
    *,
    motion_enabled: bool,
    control_hz: float | None,
    duration_s: float,
    steps: int | None,
    max_linear_speed_m_s: float | None,
    move_increment_m: float | None,
) -> ResolvedCollectionRuntime:
    """Resolve and validate run cadence, Cartesian increments, and freshness limits."""
    if control_hz is None:
        resolved_control_hz = (
            DEFAULT_MOTION_CONTROL_HZ if motion_enabled else DEFAULT_NO_MOTION_CONTROL_HZ
        )
    else:
        resolved_control_hz = float(control_hz)
    if resolved_control_hz <= 0.0:
        raise ValueError("control-hz must be positive")
    period_s = 1.0 / resolved_control_hz

    if steps is not None:
        if steps < 1:
            raise ValueError("steps must be >= 1")
        total_steps = int(steps)
        resolved_duration_s = total_steps * period_s
    else:
        resolved_duration_s = float(duration_s)
        if resolved_duration_s <= 0.0:
            raise ValueError("duration-s must be positive")
        total_steps = max(1, int(round(resolved_duration_s * resolved_control_hz)))

    if max_linear_speed_m_s is not None and move_increment_m is not None:
        raise ValueError("Pass only one of --max-linear-speed-m-s or --move-increment-m")

    max_speed_m_s_per_axis: tuple[float, float, float] | None = None
    if max_linear_speed_m_s is not None:
        resolved_max_speed_m_s = float(max_linear_speed_m_s)
        resolved_move_increment_m = resolved_max_speed_m_s * period_s
        move_increment_xy_m = resolved_move_increment_m
        move_increment_z_m = resolved_move_increment_m
        speed_source = "cli --max-linear-speed-m-s"
    elif motion_enabled:
        if move_increment_m is None:
            move_increment_z_m = DEFAULT_MOTION_MOVE_INCREMENT_M
            speed_source = "default_motion_move_increment_0.01"
        else:
            move_increment_z_m = float(move_increment_m)
            speed_source = "cli --move-increment-m"
        if move_increment_z_m <= 0.0:
            raise ValueError("move-increment-m must be positive")
        move_increment_xy_m = DEFAULT_MOTION_MOVE_INCREMENT_XY_SCALE * move_increment_z_m
        resolved_move_increment_m = move_increment_z_m
        max_speed_xy_m_s = move_increment_xy_m / period_s
        max_speed_z_m_s = move_increment_z_m / period_s
        max_speed_m_s_per_axis = (max_speed_xy_m_s, max_speed_xy_m_s, max_speed_z_m_s)
        resolved_max_speed_m_s = max(max_speed_m_s_per_axis)
        speed_source = f"{speed_source}; xy={DEFAULT_MOTION_MOVE_INCREMENT_XY_SCALE}x z"
    else:
        resolved_max_speed_m_s = DEFAULT_NO_MOTION_SPEED_M_S
        resolved_move_increment_m = resolved_max_speed_m_s * period_s
        move_increment_xy_m = resolved_move_increment_m
        move_increment_z_m = resolved_move_increment_m
        speed_source = "default_no_motion_0.05"

    if resolved_max_speed_m_s <= 0.0:
        raise ValueError("max-linear-speed-m-s must be positive")
    max_speed_xy_m_s = (
        max_speed_m_s_per_axis[0]
        if max_speed_m_s_per_axis is not None
        else resolved_max_speed_m_s
    )
    max_speed_z_m_s = (
        max_speed_m_s_per_axis[2]
        if max_speed_m_s_per_axis is not None
        else resolved_max_speed_m_s
    )
    acceleration_limits = max_speed_m_s_per_axis or (resolved_max_speed_m_s,)
    max_acceleration_m_s2 = max(
        max(limit / period_s, limit / 0.2) for limit in acceleration_limits
    )

    return ResolvedCollectionRuntime(
        motion_enabled=motion_enabled,
        control_hz=resolved_control_hz,
        period_s=period_s,
        duration_s=resolved_duration_s,
        total_steps=total_steps,
        move_increment_m=resolved_move_increment_m,
        move_increment_xy_m=move_increment_xy_m,
        move_increment_z_m=move_increment_z_m,
        max_linear_speed_m_s=resolved_max_speed_m_s,
        max_linear_speed_xy_m_s=max_speed_xy_m_s,
        max_linear_speed_z_m_s=max_speed_z_m_s,
        max_speed_m_s_per_axis=max_speed_m_s_per_axis,
        max_acceleration_m_s2=max_acceleration_m_s2,
        input_timeout_s=max(MIN_INPUT_TIMEOUT_S, 2.0 * period_s),
        robot_state_timeout_s=max(MIN_ROBOT_STATE_TIMEOUT_S, 0.5 * period_s),
        speed_source=speed_source,
    )
