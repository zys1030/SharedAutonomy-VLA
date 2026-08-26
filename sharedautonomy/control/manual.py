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

from sharedautonomy.assistance.cube_yaw_assist import CubeYawAssistPolicy
from sharedautonomy.assistance.safety_filter import CartesianSafetyFilter
from sharedautonomy.data.schema import (
    AssistAction,
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
    # Compact twist → J6 / tool-axis yaw only. Mutually exclusive with allow_rotation.
    allow_tool_yaw: bool = False
    # Third-person cube yaw → J6 overlay, concurrent with human XYZ.
    enable_yaw_assist: bool = False
    tool_yaw_sign: float = 1.0
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
        object.__setattr__(self, "allow_rotation", bool(self.allow_rotation))
        object.__setattr__(self, "allow_tool_yaw", bool(self.allow_tool_yaw))
        object.__setattr__(self, "enable_yaw_assist", bool(self.enable_yaw_assist))
        if self.allow_rotation and self.allow_tool_yaw:
            raise ValueError("allow_rotation and allow_tool_yaw are mutually exclusive")
        if self.allow_rotation and self.enable_yaw_assist:
            raise ValueError("allow_rotation and enable_yaw_assist are mutually exclusive")
        sign = float(self.tool_yaw_sign)
        if sign not in (1.0, -1.0):
            raise ValueError("tool_yaw_sign must be 1.0 or -1.0")
        object.__setattr__(self, "tool_yaw_sign", sign)
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
    assist_action: AssistAction | None = None
    assist_reason: str | None = None


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


class GripperActuator(Protocol):
    def apply_human_gripper(self, human_action: HumanAction) -> float | None: ...


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


_FREEZE_TOOL_YAW_REASONS = frozenset({"fixed_orientation", "stale_input", "stale_robot_state"})


def overlay_j6_delta_deg(joint_target_deg: Sequence[float], delta_deg: float) -> list[float]:
    """Add a constant J6 offset on top of the pose-locked IK solution."""
    target = [float(value) for value in joint_target_deg]
    if len(target) < 6:
        raise ValueError("joint_target_deg must contain at least 6 values")
    target[5] = target[5] + float(delta_deg)
    return target


def overlay_tool_yaw_joint(
    joint_target_deg: Sequence[float],
    yaw_rate_rad_s: float,
    *,
    dt_s: float,
    sign: float = 1.0,
) -> list[float]:
    """Add ``sign * deg(wz) * dt`` on top of IK J6.

    IK may already change J6 to hold world-frame yaw during XY motion. Overlaying
    onto the IK solution (not pinning J6 to the present angle) keeps translation
    from spinning the gripper around base Z.
    """
    if float(dt_s) <= 0.0:
        raise ValueError("dt_s must be positive")
    if float(sign) not in (1.0, -1.0):
        raise ValueError("sign must be 1.0 or -1.0")
    target = [float(value) for value in joint_target_deg]
    if len(target) < 6:
        raise ValueError("joint_target_deg must contain at least 6 values")
    target[5] = target[5] + float(sign) * math.degrees(float(yaw_rate_rad_s)) * float(dt_s)
    return target


def _replace_human_angular(human_action: HumanAction, angular_velocity_rad_s: Vector3) -> HumanAction:
    return HumanAction(
        timestamp=human_action.timestamp,
        linear_velocity_m_s=human_action.linear_velocity_m_s,
        angular_velocity_rad_s=angular_velocity_rad_s,
        gripper_target_open_fraction=human_action.gripper_target_open_fraction,
        deadman_active=human_action.deadman_active,
        input_age_ms=human_action.input_age_ms,
        reference_frame=human_action.reference_frame,
        gripper_button_edge=human_action.gripper_button_edge,
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
    gripper_actuator: GripperActuator | None = None
    observation_synchronizer: ObservationSynchronizer | None = None
    yaw_assist_policy: CubeYawAssistPolicy | None = None
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
        if self.config.allow_tool_yaw:
            wz = float(human_action.angular_velocity_rad_s[2])
            if any(abs(float(value)) > 0.0 for value in human_action.angular_velocity_rad_s[:2]):
                human_action = _replace_human_angular(human_action, (0.0, 0.0, wz))
        elif not self.config.allow_rotation and any(
            abs(value) > 0.0 for value in human_action.angular_velocity_rad_s
        ):
            human_action = _replace_human_angular(human_action, (0.0, 0.0, 0.0))

        commanded_gripper_open_fraction: float | None = None
        if self.gripper_actuator is not None:
            commanded_gripper_open_fraction = self.gripper_actuator.apply_human_gripper(human_action)

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
                gripper_commanded_open_fraction=commanded_gripper_open_fraction,
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
        if self.config.allow_rotation:
            requested_rpy = tuple(
                float(current) + float(omega) * actual_dt_s
                for current, omega in zip(
                    robot_state.ee_rpy_rad, human_action.angular_velocity_rad_s, strict=True
                )
            )
        elif self.config.allow_tool_yaw or self.config.enable_yaw_assist:
            # Hold the yaw already achieved (opening + any cube overlay).
            # Snapping yaw back to the opening RPY fights the J6 overlay and
            # rotates about base Z — a reverse twist before grasp.
            requested_rpy = (
                float(self.config.fixed_ee_rpy_rad[0]),
                float(self.config.fixed_ee_rpy_rad[1]),
                float(robot_state.ee_rpy_rad[2]),
            )
        else:
            requested_rpy = self.config.fixed_ee_rpy_rad

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

        freeze_tool_yaw = bool(_FREEZE_TOOL_YAW_REASONS.intersection(safety_reasons))
        if self.config.allow_tool_yaw and not freeze_tool_yaw:
            joint_target = overlay_tool_yaw_joint(
                joint_target,
                human_action.angular_velocity_rad_s[2],
                dt_s=actual_dt_s,
                sign=self.config.tool_yaw_sign,
            )

        assist_decision = None
        if self.config.enable_yaw_assist:
            if self.yaw_assist_policy is None:
                raise ValueError("enable_yaw_assist=True requires yaw_assist_policy")
            color_rgb = None
            if synced_observation is not None:
                external = synced_observation.observation.external_camera
                if external is not None:
                    color_rgb = external.color_rgb
            joints = robot_state.joint_position_deg
            j6_now_deg = float(joints[5]) if len(joints) >= 6 else 0.0
            gripper_open = commanded_gripper_open_fraction
            if gripper_open is None:
                gripper_open = human_action.gripper_target_open_fraction
            assist_decision = self.yaw_assist_policy.propose(
                color_rgb=color_rgb,
                j6_now_deg=j6_now_deg,
                ee_rpy_rad=robot_state.ee_rpy_rad,
                human_wz_rad_s=float(human_action.angular_velocity_rad_s[2]),
                deadman_active=bool(human_action.deadman_active),
                gripper_open_fraction=gripper_open,
                timestamp=SampleTimestamp(timestamp_utc=stamp, received_monotonic_ns=now_ns),
            )

        joint_target = clip_joint_targets(
            robot_state.joint_position_deg,
            joint_target,
            self.config.max_joint_step_deg,
            self.config.joint_limits_deg,
        )
        # Overlay the live cube–gripper wrap90 after IK clip so translation does
        # not steal the J6 step. Stop condition is FK heading vs locked cube,
        # not a commanded-offset counter.
        if (
            assist_decision is not None
            and not freeze_tool_yaw
            and assist_decision.reason in {"assisting", "human_override"}
            and float(assist_decision.authority) > 0.0
            and assist_decision.delta_j6_deg is not None
            and len(joint_target) >= 6
        ):
            remaining_cmd = float(assist_decision.delta_j6_deg) * float(assist_decision.authority)
            max_step = float(self.config.max_joint_step_deg)
            joint_target = overlay_j6_delta_deg(
                joint_target,
                max(-max_step, min(max_step, remaining_cmd)),
            )
            if self.config.joint_limits_deg is not None:
                lower, upper = self.config.joint_limits_deg[5]
                joint_target[5] = min(max(joint_target[5], float(lower)), float(upper))

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
            authority=0.0 if assist_decision is None else float(assist_decision.authority),
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
            assist_action=None if assist_decision is None else assist_decision.assist_action,
            assist_reason=None if assist_decision is None else assist_decision.reason,
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
    gripper_actuator: GripperActuator | None = None,
    observation_synchronizer: ObservationSynchronizer | None = None,
    yaw_assist_policy: CubeYawAssistPolicy | None = None,
) -> ManualCartesianRunner:
    """Construct a runner with the Cartesian safety filter already attached."""
    return ManualCartesianRunner(
        config=config,
        teleop=teleop,
        robot_state_source=robot_state_source,
        safety_pipeline=safety_filter,
        inverse_kinematics=inverse_kinematics or MockInverseKinematics(),
        joint_commander=joint_commander,
        gripper_actuator=gripper_actuator,
        observation_synchronizer=observation_synchronizer,
        yaw_assist_policy=yaw_assist_policy,
    )
