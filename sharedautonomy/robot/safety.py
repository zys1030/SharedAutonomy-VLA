"""Safety checks shared by real-robot command adapters."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


class MotionDisabledError(RuntimeError):
    """Raised when a robot command is attempted while motion is disabled."""


class CartesianSafetyError(ValueError):
    """Raised when a Cartesian command violates the configured safe workspace."""


@dataclass(frozen=True)
class CartesianWorkspace:
    """Convex task workspace expressed in the robot base frame, in meters.

    ``tool_tip_offset_base_m`` is valid while the configured fixed tool
    orientation is maintained. For a vertically downward 178 mm gripper it is
    ``(0, 0, -0.178)``.
    """

    polygon_xy_m: Sequence[Sequence[float]]
    table_z_m: float
    min_tool_clearance_m: float
    tool_tip_offset_base_m: Sequence[float]
    max_flange_z_m: float | None = None

    def __post_init__(self) -> None:
        polygon = _normalize_convex_polygon(self.polygon_xy_m)
        offset = _finite_vector(self.tool_tip_offset_base_m, "tool_tip_offset_base_m")
        if len(offset) != 3:
            raise ValueError("tool_tip_offset_base_m must contain 3 values")

        table_z = float(self.table_z_m)
        clearance = float(self.min_tool_clearance_m)
        if not math.isfinite(table_z):
            raise ValueError("table_z_m must be finite")
        if not math.isfinite(clearance) or clearance < 0:
            raise ValueError("min_tool_clearance_m must be finite and non-negative")
        if self.max_flange_z_m is not None:
            max_z = float(self.max_flange_z_m)
            if not math.isfinite(max_z):
                raise ValueError("max_flange_z_m must be finite")

        object.__setattr__(self, "polygon_xy_m", polygon)
        object.__setattr__(self, "tool_tip_offset_base_m", tuple(offset))

    @property
    def min_flange_z_m(self) -> float:
        """Minimum flange Z implied by the table, clearance and tool offset."""
        return self.table_z_m + self.min_tool_clearance_m - self.tool_tip_offset_base_m[2]


def validate_cartesian_segment(
    current_flange_position_m: Sequence[float],
    target_flange_position_m: Sequence[float],
    workspace: CartesianWorkspace,
) -> list[float]:
    """Fail closed unless the complete straight segment stays in the workspace.

    The configured XY polygon is required to be convex. Therefore, if both
    endpoints are inside it, the entire straight segment is inside it as well.
    The same property applies to the Z interval.
    """
    current = _cartesian_position(current_flange_position_m, "current_flange_position_m")
    target = _cartesian_position(target_flange_position_m, "target_flange_position_m")
    _validate_workspace_point(current, workspace, "current")
    _validate_workspace_point(target, workspace, "target")
    return target


def _clip_per_axis(vector: Sequence[float], max_per_axis: Sequence[float]) -> list[float]:
    limits = _finite_vector(max_per_axis, "max_per_axis")
    values = _finite_vector(vector, "vector")
    if len(limits) != len(values):
        raise ValueError(f"max_per_axis must contain {len(values)} values, got {len(limits)}")
    return [max(-limit, min(limit, float(value))) for value, limit in zip(values, limits, strict=True)]


def limit_cartesian_target(
    current_position_m: Sequence[float],
    requested_position_m: Sequence[float],
    *,
    dt_s: float,
    max_speed_m_s: float,
    max_speed_m_s_per_axis: Sequence[float] | None = None,
    previous_velocity_m_s: Sequence[float] | None = None,
    max_acceleration_m_s2: float | None = None,
) -> tuple[list[float], list[float]]:
    """Limit a Cartesian target using the measured control interval.

    When ``max_speed_m_s_per_axis`` is set, each velocity component is clipped
    independently (typical for slower Z / faster XY teleop). Otherwise the
    legacy vector-norm speed cap is used.

    Returns the limited position and the velocity that should be retained for
    the next acceleration-limiting step.
    """
    current = _cartesian_position(current_position_m, "current_position_m")
    requested = _cartesian_position(requested_position_m, "requested_position_m")
    dt = _positive_finite(dt_s, "dt_s")
    max_speed = _positive_finite(max_speed_m_s, "max_speed_m_s")

    velocity = [(target - present) / dt for present, target in zip(current, requested, strict=True)]
    if max_speed_m_s_per_axis is not None:
        speed_limits = _cartesian_position(max_speed_m_s_per_axis, "max_speed_m_s_per_axis")
        for limit in speed_limits:
            _positive_finite(limit, "max_speed_m_s_per_axis item")
        velocity = _clip_per_axis(velocity, speed_limits)
    else:
        velocity = _limit_vector_norm(velocity, max_speed)

    if (previous_velocity_m_s is None) != (max_acceleration_m_s2 is None):
        raise ValueError(
            "previous_velocity_m_s and max_acceleration_m_s2 must either both be set or both be None"
        )
    if previous_velocity_m_s is not None and max_acceleration_m_s2 is not None:
        previous = _cartesian_position(previous_velocity_m_s, "previous_velocity_m_s")
        max_acceleration = _positive_finite(max_acceleration_m_s2, "max_acceleration_m_s2")
        delta_velocity = [
            requested_velocity - previous_velocity
            for previous_velocity, requested_velocity in zip(previous, velocity, strict=True)
        ]
        if max_speed_m_s_per_axis is not None:
            accel_limits = [
                max(limit / dt, limit / 0.2) for limit in speed_limits
            ]
            limited_delta = _clip_per_axis(delta_velocity, [value * dt for value in accel_limits])
        else:
            limited_delta = _limit_vector_norm(delta_velocity, max_acceleration * dt)
        velocity = [
            previous_velocity + velocity_delta
            for previous_velocity, velocity_delta in zip(previous, limited_delta, strict=True)
        ]
        if max_speed_m_s_per_axis is not None:
            velocity = _clip_per_axis(velocity, speed_limits)
        else:
            velocity = _limit_vector_norm(velocity, max_speed)

    target = [
        present + limited_velocity * dt for present, limited_velocity in zip(current, velocity, strict=True)
    ]
    return target, velocity


def validate_fixed_orientation(
    reference_rpy_rad: Sequence[float],
    target_rpy_rad: Sequence[float],
    tolerance_rad: float,
) -> None:
    """Reject a target that would rotate a fixed-orientation tool."""
    reference = _cartesian_position(reference_rpy_rad, "reference_rpy_rad")
    target = _cartesian_position(target_rpy_rad, "target_rpy_rad")
    tolerance = _positive_finite(tolerance_rad, "tolerance_rad")
    errors = [
        abs(math.remainder(value - origin, 2 * math.pi))
        for origin, value in zip(reference, target, strict=True)
    ]
    if any(error > tolerance for error in errors):
        raise CartesianSafetyError(
            f"Target orientation differs from the fixed reference by {errors} rad; "
            f"tolerance is {tolerance} rad"
        )


def validate_signal_age(
    received_monotonic_ns: int,
    now_monotonic_ns: int,
    max_age_s: float,
) -> None:
    """Reject stale or future-dated device/control state."""
    max_age = _positive_finite(max_age_s, "max_age_s")
    age_ns = int(now_monotonic_ns) - int(received_monotonic_ns)
    if age_ns < 0:
        raise CartesianSafetyError("Signal receive time is in the future")
    if age_ns > max_age * 1_000_000_000:
        raise CartesianSafetyError(
            f"Signal is stale: age={age_ns / 1_000_000_000:.6f}s exceeds max_age_s={max_age}"
        )


def clip_joint_targets(
    present_deg: Sequence[float],
    target_deg: Sequence[float],
    max_step_deg: float | Sequence[float],
    joint_limits_deg: Sequence[Sequence[float]] | None = None,
) -> list[float]:
    """Validate and clip joint targets to configured position and step limits."""
    present = _finite_vector(present_deg, "present_deg")
    target = _finite_vector(target_deg, "target_deg")
    if len(present) != len(target):
        raise ValueError(
            f"present_deg and target_deg must have the same length, got {len(present)} and {len(target)}"
        )

    max_steps = _expand_positive_limits(max_step_deg, len(present), "max_step_deg")
    hard_limits = _normalize_joint_limits(joint_limits_deg, len(present))

    safe_targets: list[float] = []
    for index, (current, requested, max_step) in enumerate(zip(present, target, max_steps, strict=True)):
        if hard_limits is not None:
            lower, upper = hard_limits[index]
            if not lower <= current <= upper:
                raise ValueError(
                    f"Present joint {index + 1} position {current} is outside configured limits "
                    f"[{lower}, {upper}]"
                )
            requested = min(max(requested, lower), upper)

        safe_targets.append(min(max(requested, current - max_step), current + max_step))

    return safe_targets


def validate_joint_limits(
    joint_limits_deg: Sequence[Sequence[float]] | None,
    expected_size: int,
) -> None:
    """Validate joint limits without applying them."""
    _normalize_joint_limits(joint_limits_deg, expected_size)


def _finite_vector(values: Sequence[float], name: str) -> list[float]:
    result = [float(value) for value in values]
    if not result:
        raise ValueError(f"{name} must not be empty")
    if not all(math.isfinite(value) for value in result):
        raise ValueError(f"{name} must contain only finite values")
    return result


def _cartesian_position(values: Sequence[float], name: str) -> list[float]:
    result = _finite_vector(values, name)
    if len(result) != 3:
        raise ValueError(f"{name} must contain 3 values, got {len(result)}")
    return result


def _positive_finite(value: float, name: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return result


def _limit_vector_norm(vector: Sequence[float], max_norm: float) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= max_norm:
        return list(vector)
    scale = max_norm / norm
    return [value * scale for value in vector]


def _normalize_convex_polygon(points: Sequence[Sequence[float]]) -> tuple[tuple[float, float], ...]:
    polygon: list[tuple[float, float]] = []
    for index, point in enumerate(points):
        values = _finite_vector(point, f"polygon_xy_m[{index}]")
        if len(values) != 2:
            raise ValueError(f"polygon_xy_m[{index}] must contain 2 values")
        polygon.append((values[0], values[1]))
    if len(polygon) < 3:
        raise ValueError("polygon_xy_m must contain at least 3 points")

    signs: set[int] = set()
    for index in range(len(polygon)):
        a = polygon[index]
        b = polygon[(index + 1) % len(polygon)]
        c = polygon[(index + 2) % len(polygon)]
        cross = _cross_2d(a, b, c)
        if abs(cross) > 1e-12:
            signs.add(1 if cross > 0 else -1)
    if len(signs) != 1:
        raise ValueError("polygon_xy_m must be a non-degenerate convex polygon in boundary order")
    return tuple(polygon)


def _cross_2d(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> float:
    return (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])


def _point_in_convex_polygon(
    point: tuple[float, float],
    polygon: Sequence[tuple[float, float]],
) -> bool:
    signs: set[int] = set()
    for index, a in enumerate(polygon):
        b = polygon[(index + 1) % len(polygon)]
        cross = (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])
        if abs(cross) > 1e-12:
            signs.add(1 if cross > 0 else -1)
            if len(signs) > 1:
                return False
    return True


def _validate_workspace_point(
    flange_position_m: Sequence[float],
    workspace: CartesianWorkspace,
    label: str,
) -> None:
    tool_tip = [
        position + offset
        for position, offset in zip(
            flange_position_m,
            workspace.tool_tip_offset_base_m,
            strict=True,
        )
    ]
    if not _point_in_convex_polygon((tool_tip[0], tool_tip[1]), workspace.polygon_xy_m):
        raise CartesianSafetyError(
            f"{label} tool-tip XY ({tool_tip[0]}, {tool_tip[1]}) is outside the safe polygon"
        )

    minimum_tip_z = workspace.table_z_m + workspace.min_tool_clearance_m
    if tool_tip[2] < minimum_tip_z:
        raise CartesianSafetyError(
            f"{label} tool-tip Z {tool_tip[2]} is below the minimum safe Z {minimum_tip_z}"
        )
    if workspace.max_flange_z_m is not None and flange_position_m[2] > workspace.max_flange_z_m:
        raise CartesianSafetyError(
            f"{label} flange Z {flange_position_m[2]} exceeds max_flange_z_m={workspace.max_flange_z_m}"
        )


def _expand_positive_limits(
    values: float | Sequence[float],
    expected_size: int,
    name: str,
) -> list[float]:
    if isinstance(values, int | float):
        result = [float(values)] * expected_size
    else:
        result = _finite_vector(values, name)

    if len(result) != expected_size:
        raise ValueError(f"{name} must contain {expected_size} values, got {len(result)}")
    if any(value <= 0 for value in result):
        raise ValueError(f"{name} values must all be positive")
    return result


def _normalize_joint_limits(
    joint_limits_deg: Sequence[Sequence[float]] | None,
    expected_size: int,
) -> list[tuple[float, float]] | None:
    if joint_limits_deg is None:
        return None
    if len(joint_limits_deg) != expected_size:
        raise ValueError(
            f"joint_limits_deg must contain {expected_size} [min, max] pairs, got {len(joint_limits_deg)}"
        )

    result: list[tuple[float, float]] = []
    for index, pair in enumerate(joint_limits_deg):
        if len(pair) != 2:
            raise ValueError(f"Joint {index + 1} limit must be a [min, max] pair")
        lower, upper = (float(pair[0]), float(pair[1]))
        if not math.isfinite(lower) or not math.isfinite(upper):
            raise ValueError(f"Joint {index + 1} limits must be finite")
        if lower >= upper:
            raise ValueError(f"Joint {index + 1} minimum must be smaller than its maximum")
        result.append((lower, upper))
    return result
