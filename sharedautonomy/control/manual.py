"""Manual Cartesian SpaceMouse control loop.

Wires teleop integration, Cartesian safety filtering, mock/real IK, and optional
joint command send behind ``enable_motion``.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from sharedautonomy.assistance.safety_filter import CartesianSafetyFilter
from sharedautonomy.data.schema import (
    CoordinateFrame,
    ExecutedAction,
    HumanAction,
    SampleTimestamp,
)
from sharedautonomy.data.sync import ObservationSynchronizer, SyncedObservation
from sharedautonomy.robot.kinematics import InverseKinematicsError
from sharedautonomy.robot.safety import MotionDisabledError, clip_joint_targets

logger = logging.getLogger(__name__)

Vector3 = tuple[float, float, float]
JointVector = tuple[float, ...]


@dataclass(frozen=True, slots=True)
class CartesianRobotState:
    """Minimal robot state required by the manual Cartesian runner."""

    timestamp: SampleTimestamp
    joint_position_deg: JointVector
    ee_position_m: Vector3
    ee_rpy_rad: Vector3
    robot_state_age_ms: float


@dataclass(frozen=True, slots=True)
class ManualCartesianConfig:
    """Runner defaults. Motion remains disabled unless explicitly enabled."""

    control_rate_hz: float = 50.0
    enable_motion: bool = False
    allow_rotation: bool = False
    fixed_ee_rpy_rad: Vector3 = (0.0, 1.5707963267948966, 0.0)
    # If set, every step retargets flange Z to this value (planar teleop; stops Z ratchet).
    hold_flange_z_m: float | None = None
    max_joint_step_deg: float = 1.0
    joint_limits_deg: Sequence[Sequence[float]] | None = None
    max_steps: int | None = None

    def __post_init__(self) -> None:
        if float(self.control_rate_hz) <= 0.0:
            raise ValueError("control_rate_hz must be positive")
        if float(self.max_joint_step_deg) <= 0.0:
            raise ValueError("max_joint_step_deg must be positive")
        if self.max_steps is not None and int(self.max_steps) < 1:
            raise ValueError("max_steps must be >= 1 when provided")
        if self.hold_flange_z_m is not None and not math.isfinite(float(self.hold_flange_z_m)):
            raise ValueError("hold_flange_z_m must be finite when provided")
        object.__setattr__(
            self,
            "fixed_ee_rpy_rad",
            tuple(float(value) for value in self.fixed_ee_rpy_rad),
        )
        if self.hold_flange_z_m is not None:
            object.__setattr__(self, "hold_flange_z_m", float(self.hold_flange_z_m))

    @property
    def nominal_dt_s(self) -> float:
        return 1.0 / float(self.control_rate_hz)


@dataclass(frozen=True, slots=True)
class CartesianControlStep:
    """One control-cycle result produced by ``ManualCartesianRunner.step``."""

    step_index: int
    human_action: HumanAction
    robot_state: CartesianRobotState
    requested_ee_position_m: Vector3
    requested_ee_rpy_rad: Vector3
    actual_dt_s: float
    executed_action: ExecutedAction
    motion_sent: bool
    synced_observation: SyncedObservation | None = None


class TeleopSource(Protocol):
    def read_human_action(
        self,
        *,
        now_monotonic_ns: int,
        timestamp_utc: datetime | None = None,
    ) -> HumanAction: ...


class RobotStateSource(Protocol):
    def read_cartesian_state(self, *, now_monotonic_ns: int) -> CartesianRobotState: ...


class InverseKinematics(Protocol):
    def solve(
        self,
        *,
        joint_seed_deg: Sequence[float],
        target_position_m: Sequence[float],
        target_rpy_rad: Sequence[float],
    ) -> list[float]: ...


class JointCommander(Protocol):
    def send_joint_target(self, joint_target_deg: Sequence[float]) -> None: ...


class SafetyPipeline(Protocol):
    def __call__(
        self,
        robot_state: CartesianRobotState,
        requested_position_m: Vector3,
        requested_rpy_rad: Vector3,
        dt_s: float,
        human_action: HumanAction,
        *,
        now_monotonic_ns: int,
    ) -> tuple[Vector3, Vector3, bool, tuple[str, ...]]: ...


def integrate_cartesian_velocity(
    current_position_m: Sequence[float],
    linear_velocity_m_s: Sequence[float],
    *,
    dt_s: float,
) -> Vector3:
    """Integrate a base-frame linear velocity over the measured control interval."""
    if float(dt_s) <= 0.0:
        raise ValueError("dt_s must be positive")
    current = tuple(float(value) for value in current_position_m)
    velocity = tuple(float(value) for value in linear_velocity_m_s)
    if len(current) != 3 or len(velocity) != 3:
        raise ValueError("position and velocity must each contain 3 values")
    return (
        current[0] + velocity[0] * float(dt_s),
        current[1] + velocity[1] * float(dt_s),
        current[2] + velocity[2] * float(dt_s),
    )


def passthrough_safety_pipeline(
    robot_state: CartesianRobotState,
    requested_position_m: Vector3,
    requested_rpy_rad: Vector3,
    dt_s: float,
    human_action: HumanAction,
    *,
    now_monotonic_ns: int,
) -> tuple[Vector3, Vector3, bool, tuple[str, ...]]:
    """No-op safety path retained for isolated unit tests."""
    del robot_state, dt_s, human_action, now_monotonic_ns
    return requested_position_m, requested_rpy_rad, False, ()


class MockInverseKinematics:
    """Return the seed joints unchanged. Useful until RealMan IK is wired."""

    def solve(
        self,
        *,
        joint_seed_deg: Sequence[float],
        target_position_m: Sequence[float],
        target_rpy_rad: Sequence[float],
    ) -> list[float]:
        del target_position_m, target_rpy_rad
        return [float(value) for value in joint_seed_deg]


class MockJointCommander:
    """Record commanded joint targets without talking to hardware."""

    def __init__(self) -> None:
        self.commands: list[list[float]] = []

    def send_joint_target(self, joint_target_deg: Sequence[float]) -> None:
        self.commands.append([float(value) for value in joint_target_deg])


class MockRobotStateSource:
    """In-memory Cartesian state for dry-run and unit tests."""

    def __init__(
        self,
        *,
        ee_position_m: Sequence[float] = (-0.3, -0.1, 0.25),
        ee_rpy_rad: Sequence[float] = (0.0, 1.5707963267948966, 0.0),
        joint_position_deg: Sequence[float] = (0.0, 15.0, 15.0, 0.0, 120.0, 0.0),
        robot_state_age_ms: float = 0.0,
    ) -> None:
        self.ee_position_m = tuple(float(value) for value in ee_position_m)
        self.ee_rpy_rad = tuple(float(value) for value in ee_rpy_rad)
        self.joint_position_deg = tuple(float(value) for value in joint_position_deg)
        self.robot_state_age_ms = float(robot_state_age_ms)

    def read_cartesian_state(self, *, now_monotonic_ns: int) -> CartesianRobotState:
        age_ns = int(round(self.robot_state_age_ms * 1_000_000.0))
        return CartesianRobotState(
            timestamp=SampleTimestamp(
                timestamp_utc=datetime.now(tz=UTC),
                received_monotonic_ns=max(0, int(now_monotonic_ns) - age_ns),
            ),
            joint_position_deg=self.joint_position_deg,
            ee_position_m=self.ee_position_m,
            ee_rpy_rad=self.ee_rpy_rad,
            robot_state_age_ms=self.robot_state_age_ms,
        )

    def set_ee_position_m(self, position_m: Sequence[float]) -> None:
        self.ee_position_m = tuple(float(value) for value in position_m)


@dataclass
class ManualCartesianRunner:
    """50 Hz-oriented manual Cartesian control loop.

    Control step contract:
    1. Read SpaceMouse / teleop ``HumanAction``
    2. Read latest Cartesian robot state
    3. Integrate velocity with measured ``dt``
    4. Run Cartesian safety chain
    5. Solve IK (mock by default)
    6. Clip joint targets
    7. Send joints only when ``enable_motion`` is true
    """

    config: ManualCartesianConfig
    teleop: TeleopSource
    robot_state_source: RobotStateSource
    safety_pipeline: SafetyPipeline
    inverse_kinematics: InverseKinematics = field(default_factory=MockInverseKinematics)
    joint_commander: JointCommander | None = None
    observation_synchronizer: ObservationSynchronizer | None = None
    _step_index: int = field(default=0, init=False, repr=False)
    _last_tick_monotonic_ns: int | None = field(default=None, init=False, repr=False)

    def reset(self) -> None:
        self._step_index = 0
        self._last_tick_monotonic_ns = None
        reset = getattr(self.safety_pipeline, "reset", None)
        if callable(reset):
            reset()

    def step(
        self,
        *,
        now_monotonic_ns: int | None = None,
        timestamp_utc: datetime | None = None,
        dt_s: float | None = None,
    ) -> CartesianControlStep:
        now_ns = int(time.perf_counter_ns() if now_monotonic_ns is None else now_monotonic_ns)
        stamp = timestamp_utc or datetime.now(tz=UTC)
        if dt_s is None:
            if self._last_tick_monotonic_ns is None:
                actual_dt_s = self.config.nominal_dt_s
            else:
                actual_dt_s = max((now_ns - self._last_tick_monotonic_ns) * 1e-9, 1e-6)
        else:
            actual_dt_s = float(dt_s)
            if actual_dt_s <= 0.0:
                raise ValueError("dt_s must be positive")

        human_action = self.teleop.read_human_action(
            now_monotonic_ns=now_ns,
            timestamp_utc=stamp,
        )
        if not self.config.allow_rotation and any(
            abs(value) > 0.0 for value in human_action.angular_velocity_rad_s
        ):
            human_action = HumanAction(
                timestamp=human_action.timestamp,
                linear_velocity_m_s=human_action.linear_velocity_m_s,
                angular_velocity_rad_s=(0.0, 0.0, 0.0),
                gripper_target_open_fraction=human_action.gripper_target_open_fraction,
                deadman_active=human_action.deadman_active,
                input_age_ms=human_action.input_age_ms,
                reference_frame=human_action.reference_frame,
            )

        robot_state = self.robot_state_source.read_cartesian_state(now_monotonic_ns=now_ns)
        synced_observation = None
        if self.observation_synchronizer is not None:
            proprio_source = self.observation_synchronizer.proprioception
            set_from_cartesian = getattr(proprio_source, "set_from_cartesian_state", None)
            if callable(set_from_cartesian):
                set_from_cartesian(robot_state)
            else:
                proprio_source.read_proprioception(now_monotonic_ns=now_ns)
            synced_observation = self.observation_synchronizer.capture(
                now_monotonic_ns=now_ns,
                timestamp_utc=stamp,
            )
        requested_position = integrate_cartesian_velocity(
            robot_state.ee_position_m,
            human_action.linear_velocity_m_s,
            dt_s=actual_dt_s,
        )
        if self.config.hold_flange_z_m is not None:
            # Pin flange Z to the hold setpoint. Zeroing vz alone is not enough: each
            # cycle rebases on measured pose, so IK/tracking Z error ratchets downward.
            requested_position = (
                float(requested_position[0]),
                float(requested_position[1]),
                float(self.config.hold_flange_z_m),
            )
        requested_rpy = (
            self.config.fixed_ee_rpy_rad
            if not self.config.allow_rotation
            else tuple(
                float(current) + float(omega) * actual_dt_s
                for current, omega in zip(
                    robot_state.ee_rpy_rad, human_action.angular_velocity_rad_s, strict=True
                )
            )
        )

        safe_position, safe_rpy, safety_intervened, safety_reasons = self.safety_pipeline(
            robot_state,
            requested_position,
            requested_rpy,
            actual_dt_s,
            human_action,
            now_monotonic_ns=now_ns,
        )
        if self.config.hold_flange_z_m is not None:
            if abs(float(safe_position[2]) - float(self.config.hold_flange_z_m)) > 1e-12:
                safety_intervened = True
                if "hold_flange_z" not in safety_reasons:
                    safety_reasons = tuple([*safety_reasons, "hold_flange_z"])
            safe_position = (
                float(safe_position[0]),
                float(safe_position[1]),
                float(self.config.hold_flange_z_m),
            )

        executed_velocity = tuple(
            (target - present) / actual_dt_s
            for present, target in zip(robot_state.ee_position_m, safe_position, strict=True)
        )
        executed_angular = tuple(
            (target - present) / actual_dt_s
            for present, target in zip(robot_state.ee_rpy_rad, safe_rpy, strict=True)
        )

        try:
            joint_target = self.inverse_kinematics.solve(
                joint_seed_deg=robot_state.joint_position_deg,
                target_position_m=safe_position,
                target_rpy_rad=safe_rpy,
            )
        except InverseKinematicsError:
            logger.warning("Inverse kinematics failed on step %s; holding current joints", self._step_index)
            joint_target = list(robot_state.joint_position_deg)
            safe_position = tuple(float(value) for value in robot_state.ee_position_m)
            safe_rpy = tuple(float(value) for value in robot_state.ee_rpy_rad)
            executed_velocity = (0.0, 0.0, 0.0)
            executed_angular = (0.0, 0.0, 0.0)
            safety_intervened = True
            safety_reasons = tuple([*safety_reasons, "ik_failure"])

        joint_target = clip_joint_targets(
            robot_state.joint_position_deg,
            joint_target,
            self.config.max_joint_step_deg,
            self.config.joint_limits_deg,
        )

        motion_sent = False
        if self.config.enable_motion:
            if self.joint_commander is None:
                raise MotionDisabledError(
                    "enable_motion=True requires a joint_commander; refusing to invent a hardware send path"
                )
            self.joint_commander.send_joint_target(joint_target)
            motion_sent = True
        elif self.joint_commander is not None:
            logger.debug("Dry-run step %s computed joint target without sending", self._step_index)

        executed = ExecutedAction(
            timestamp=SampleTimestamp(timestamp_utc=stamp, received_monotonic_ns=now_ns),
            linear_velocity_m_s=executed_velocity,
            angular_velocity_rad_s=executed_angular,
            gripper_target_open_fraction=human_action.gripper_target_open_fraction,
            joint_target_deg=tuple(joint_target),
            actual_dt_s=actual_dt_s,
            authority=0.0,
            safety_intervened=safety_intervened,
            safety_reasons=safety_reasons,
            reference_frame=CoordinateFrame.BASE,
        )

        step = CartesianControlStep(
            step_index=self._step_index,
            human_action=human_action,
            robot_state=robot_state,
            requested_ee_position_m=requested_position,
            requested_ee_rpy_rad=requested_rpy,
            actual_dt_s=actual_dt_s,
            executed_action=executed,
            motion_sent=motion_sent,
            synced_observation=synced_observation,
        )
        self._step_index += 1
        self._last_tick_monotonic_ns = now_ns
        return step

    def run_dry_run(self, *, steps: int, dt_s: float | None = None) -> list[CartesianControlStep]:
        """Execute a fixed number of offline steps without enabling motion."""
        if self.config.enable_motion:
            raise MotionDisabledError("run_dry_run() refuses enable_motion=True")
        if steps < 1:
            raise ValueError("steps must be >= 1")
        results: list[CartesianControlStep] = []
        base_ns = time.perf_counter_ns()
        period_ns = int(round((dt_s or self.config.nominal_dt_s) * 1e9))
        for index in range(steps):
            results.append(
                self.step(
                    now_monotonic_ns=base_ns + index * period_ns,
                    dt_s=dt_s or self.config.nominal_dt_s,
                )
            )
        return results


def build_manual_cartesian_runner(
    *,
    config: ManualCartesianConfig,
    teleop: TeleopSource,
    robot_state_source: RobotStateSource,
    safety_filter: CartesianSafetyFilter,
    inverse_kinematics: InverseKinematics | None = None,
    joint_commander: JointCommander | None = None,
    observation_synchronizer: ObservationSynchronizer | None = None,
) -> ManualCartesianRunner:
    """Construct a runner with the Cartesian safety filter already attached."""
    return ManualCartesianRunner(
        config=config,
        teleop=teleop,
        robot_state_source=robot_state_source,
        safety_pipeline=safety_filter,
        inverse_kinematics=inverse_kinematics or MockInverseKinematics(),
        joint_commander=joint_commander,
        observation_synchronizer=observation_synchronizer,
    )
