"""Compose Cartesian safety pure functions for the manual control runner.

Order before any SDK send:
1. input / robot-state freshness
2. measured-``dt`` speed and acceleration limiting
3. fixed-orientation check
4. workspace segment check
5. (runner) IK
6. (runner) joint step / hard-limit clipping
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from sharedautonomy.data.schema import HumanAction, SampleTimestamp
from sharedautonomy.robot.safety import (
    CartesianSafetyError,
    CartesianWorkspace,
    limit_cartesian_target,
    validate_cartesian_segment,
    validate_fixed_orientation,
    validate_signal_age,
)

Vector3 = tuple[float, float, float]


class CartesianStateView(Protocol):
    """Minimal robot-state view required by the Cartesian safety chain."""

    @property
    def timestamp(self) -> SampleTimestamp: ...

    @property
    def ee_position_m(self) -> Sequence[float]: ...

    @property
    def ee_rpy_rad(self) -> Sequence[float]: ...


def example_cartesian_workspace(*, max_flange_z_m: float | None = 1.0) -> CartesianWorkspace:
    """Generic workspace used only by offline tests and no-motion dry-runs.

    These values do not describe a real robot cell. Motion-capable runners must
    load measured geometry from ``configs/local/rm65_safety.local.yaml`` or an
    explicit config path.
    """
    return CartesianWorkspace(
        polygon_xy_m=[
            [-1.0, -1.0],
            [1.0, -1.0],
            [1.0, 1.0],
            [-1.0, 1.0],
        ],
        table_z_m=0.0,
        min_tool_clearance_m=0.0,
        tool_tip_offset_base_m=[0.0, 0.0, 0.0],
        max_flange_z_m=max_flange_z_m,
    )


@dataclass(frozen=True, slots=True)
class CartesianSafetyLimits:
    """Conservative Cartesian safety defaults for the first-stage runner."""

    max_speed_m_s: float = 0.05
    max_speed_m_s_per_axis: tuple[float, float, float] | None = None
    max_acceleration_m_s2: float = 0.25
    orientation_tolerance_rad: float = 0.05
    input_timeout_s: float = 0.1
    robot_state_timeout_s: float = 0.05
    fixed_ee_rpy_rad: Vector3 = (0.0, 1.5707963267948966, 0.0)
    enforce_fixed_orientation: bool = True
    # RPY indices checked by enforce_fixed_orientation (0=roll, 1=pitch, 2=yaw).
    fixed_orientation_axes: tuple[int, ...] = (0, 1, 2)

    def __post_init__(self) -> None:
        for name in (
            "max_speed_m_s",
            "max_acceleration_m_s2",
            "orientation_tolerance_rad",
            "input_timeout_s",
            "robot_state_timeout_s",
        ):
            if float(getattr(self, name)) <= 0.0:
                raise ValueError(f"{name} must be positive")
        object.__setattr__(
            self,
            "fixed_ee_rpy_rad",
            tuple(float(value) for value in self.fixed_ee_rpy_rad),
        )
        axes = tuple(int(axis) for axis in self.fixed_orientation_axes)
        if not axes:
            raise ValueError("fixed_orientation_axes must not be empty")
        if any(axis not in (0, 1, 2) for axis in axes):
            raise ValueError("fixed_orientation_axes values must be 0, 1, or 2")
        object.__setattr__(self, "fixed_orientation_axes", axes)
        if self.max_speed_m_s_per_axis is not None:
            limits = tuple(float(value) for value in self.max_speed_m_s_per_axis)
            if len(limits) != 3:
                raise ValueError("max_speed_m_s_per_axis must contain 3 values")
            if any(value <= 0.0 for value in limits):
                raise ValueError("max_speed_m_s_per_axis values must be positive")
            object.__setattr__(self, "max_speed_m_s_per_axis", limits)


@dataclass
class CartesianSafetyFilter:
    """Stateful safety chain used once per control step."""

    workspace: CartesianWorkspace
    limits: CartesianSafetyLimits = field(default_factory=CartesianSafetyLimits)
    _previous_velocity_m_s: list[float] | None = field(default=None, init=False, repr=False)

    def reset(self) -> None:
        self._previous_velocity_m_s = None

    def __call__(
        self,
        robot_state: CartesianStateView,
        requested_position_m: Vector3,
        requested_rpy_rad: Vector3,
        dt_s: float,
        human_action: HumanAction,
        *,
        now_monotonic_ns: int,
    ) -> tuple[Vector3, Vector3, bool, tuple[str, ...]]:
        reasons: list[str] = []
        current_position = tuple(float(value) for value in robot_state.ee_position_m)
        current_rpy = tuple(float(value) for value in robot_state.ee_rpy_rad)
        safe_position = tuple(float(value) for value in requested_position_m)
        safe_rpy = tuple(float(value) for value in requested_rpy_rad)

        try:
            validate_signal_age(
                human_action.timestamp.received_monotonic_ns,
                now_monotonic_ns,
                self.limits.input_timeout_s,
            )
        except CartesianSafetyError:
            reasons.append("stale_input")
            self._previous_velocity_m_s = [0.0, 0.0, 0.0]
            return current_position, current_rpy, True, tuple(reasons)

        try:
            validate_signal_age(
                robot_state.timestamp.received_monotonic_ns,
                now_monotonic_ns,
                self.limits.robot_state_timeout_s,
            )
        except CartesianSafetyError:
            reasons.append("stale_robot_state")
            self._previous_velocity_m_s = [0.0, 0.0, 0.0]
            return current_position, current_rpy, True, tuple(reasons)

        limited_position, limited_velocity = limit_cartesian_target(
            current_position,
            safe_position,
            dt_s=dt_s,
            max_speed_m_s=self.limits.max_speed_m_s,
            max_speed_m_s_per_axis=self.limits.max_speed_m_s_per_axis,
            previous_velocity_m_s=self._previous_velocity_m_s,
            max_acceleration_m_s2=(
                None if self._previous_velocity_m_s is None else self.limits.max_acceleration_m_s2
            ),
        )
        if any(
            abs(requested - limited) > 1e-12
            for requested, limited in zip(safe_position, limited_position, strict=True)
        ):
            reasons.append("speed_or_acceleration_limit")
        safe_position = tuple(float(value) for value in limited_position)

        if self.limits.enforce_fixed_orientation:
            try:
                validate_fixed_orientation(
                    self.limits.fixed_ee_rpy_rad,
                    safe_rpy,
                    self.limits.orientation_tolerance_rad,
                    axes=self.limits.fixed_orientation_axes,
                )
            except CartesianSafetyError:
                reasons.append("fixed_orientation")
                recovered = list(safe_rpy)
                checked = set(self.limits.fixed_orientation_axes)
                for axis in (0, 1, 2):
                    if axis in checked:
                        recovered[axis] = float(self.limits.fixed_ee_rpy_rad[axis])
                    elif axis == 2:
                        recovered[axis] = float(current_rpy[2])
                safe_rpy = tuple(recovered)
                # Orientation faults also freeze translation for this cycle.
                safe_position = current_position
                limited_velocity = [0.0, 0.0, 0.0]

        try:
            validate_cartesian_segment(current_position, safe_position, self.workspace)
        except CartesianSafetyError as exc:
            message = str(exc).lower()
            if "current" in message:
                # Measured pose left the workspace (e.g. Z ratchet). Allow a safe
                # recovery target; freeze only when the target is also unsafe.
                try:
                    validate_cartesian_segment(safe_position, safe_position, self.workspace)
                except CartesianSafetyError:
                    reasons.append("workspace_violation")
                    safe_position = current_position
                    limited_velocity = [0.0, 0.0, 0.0]
                else:
                    reasons.append("workspace_recovery")
            else:
                reasons.append("workspace_violation")
                safe_position = current_position
                limited_velocity = [0.0, 0.0, 0.0]

        self._previous_velocity_m_s = [float(value) for value in limited_velocity]
        intervened = bool(reasons)
        return safe_position, safe_rpy, intervened, tuple(reasons)
