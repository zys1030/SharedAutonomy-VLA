"""Assemble a control-step ``RobotObservation`` from asynchronous device samples.

Cross-device alignment uses the host ``received_monotonic_ns`` clock (ADR 0001).
Optional camera slots stay ``None`` until hardware is wired; the external RGB
camera is a first-class optional source from day one.
"""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sharedautonomy.data.schema import (
    CameraFrame,
    CoordinateFrame,
    JointVector,
    QuaternionXYZW,
    RobotObservation,
    SampleTimestamp,
    Vector3,
)

Vector3Like = Sequence[float]
JointVectorLike = Sequence[float]


class ObservationSyncError(ValueError):
    """Raised when a required observation source is missing or too stale."""


@dataclass(frozen=True, slots=True)
class ProprioceptiveSample:
    """Latest robot proprioception sample used by the synchronizer."""

    timestamp: SampleTimestamp
    joint_position_deg: JointVector
    ee_position_m: Vector3
    ee_quaternion_xyzw: QuaternionXYZW
    robot_state_age_ms: float
    joint_velocity_deg_s: JointVector | None = None
    gripper_commanded_open_fraction: float | None = None
    gripper_actual_open_fraction: float | None = None
    ee_reference_frame: CoordinateFrame = CoordinateFrame.BASE


class ProprioceptiveSource(Protocol):
    """Read the latest robot state for one control step."""

    def read_proprioception(self, *, now_monotonic_ns: int) -> ProprioceptiveSample: ...


class CameraSource(Protocol):
    """Read the latest camera frame, or ``None`` if no sample is available yet."""

    def read_camera(self, *, now_monotonic_ns: int) -> CameraFrame | None: ...


@dataclass(frozen=True, slots=True)
class ObservationSyncConfig:
    """Age budgets and required-slot policy for observation assembly.

    Cameras may run slower than the control loop (for example 30 FPS cameras with
    a 10 Hz ``collection_teleop`` loop). Age budgets are soft by default: stale
    frames produce warnings and are still attached unless ``drop_stale_cameras``
    is enabled.
    """

    max_robot_state_age_ms: float | None = 50.0
    max_wrist_camera_age_ms: float | None = 100.0
    max_external_camera_age_ms: float | None = 100.0
    require_wrist_camera: bool = False
    require_external_camera: bool = False
    drop_stale_cameras: bool = False
    reject_stale_robot_state: bool = True

    def __post_init__(self) -> None:
        for name in (
            "max_robot_state_age_ms",
            "max_wrist_camera_age_ms",
            "max_external_camera_age_ms",
        ):
            value = getattr(self, name)
            if value is not None and float(value) < 0.0:
                raise ValueError(f"{name} must be non-negative when provided")


@dataclass(frozen=True, slots=True)
class SyncedObservation:
    """Assembled observation plus sync diagnostics for one control step."""

    observation: RobotObservation
    warnings: tuple[str, ...]
    wrist_age_ms: float | None
    external_age_ms: float | None


@dataclass(frozen=True, slots=True)
class StaticProprioceptiveSource:
    """Deterministic proprioception source for offline / mock paths."""

    sample: ProprioceptiveSample

    def read_proprioception(self, *, now_monotonic_ns: int) -> ProprioceptiveSample:
        del now_monotonic_ns
        return self.sample


@dataclass(frozen=True, slots=True)
class StaticCameraSource:
    """Deterministic camera source for offline / mock paths.

    Use RGB-D for the wrist camera and RGB-only (``depth_raw=None``) for a future
    fixed external camera. Returning ``None`` models "camera not connected yet".
    """

    frame: CameraFrame | None = None

    def read_camera(self, *, now_monotonic_ns: int) -> CameraFrame | None:
        del now_monotonic_ns
        return self.frame


class ObservationSynchronizer:
    """Merge proprioception and optional camera streams into ``RobotObservation``.

    ``wrist_camera`` and ``external_camera`` are independent optional slots. Leave
    either source as ``None`` (or return ``None`` from ``read_camera``) until the
    corresponding hardware is available.
    """

    def __init__(
        self,
        proprioception: ProprioceptiveSource,
        *,
        wrist_camera: CameraSource | None = None,
        external_camera: CameraSource | None = None,
        config: ObservationSyncConfig | None = None,
    ) -> None:
        self.proprioception = proprioception
        self.wrist_camera = wrist_camera
        self.external_camera = external_camera
        self.config = config or ObservationSyncConfig()

    def capture(
        self,
        *,
        now_monotonic_ns: int | None = None,
        timestamp_utc: datetime | None = None,
        gripper_commanded_open_fraction: float | None = None,
        gripper_actual_open_fraction: float | None = None,
    ) -> SyncedObservation:
        """Assemble one synced observation at the host control-step time."""
        now_ns = int(time.perf_counter_ns() if now_monotonic_ns is None else now_monotonic_ns)
        if now_ns < 0:
            raise ValueError("now_monotonic_ns must be non-negative")
        stamp = timestamp_utc or datetime.now(tz=UTC)
        warnings: list[str] = []

        proprio = self.proprioception.read_proprioception(now_monotonic_ns=now_ns)
        robot_age_ms = _sample_age_ms(proprio.timestamp.received_monotonic_ns, now_ns)
        _enforce_robot_state_freshness(
            robot_age_ms,
            config=self.config,
            warnings=warnings,
        )

        wrist_frame, wrist_age_ms = self._read_optional_camera(
            source=self.wrist_camera,
            slot_name="wrist_camera",
            now_monotonic_ns=now_ns,
            max_age_ms=self.config.max_wrist_camera_age_ms,
            required=self.config.require_wrist_camera,
            warnings=warnings,
        )
        external_frame, external_age_ms = self._read_optional_camera(
            source=self.external_camera,
            slot_name="external_camera",
            now_monotonic_ns=now_ns,
            max_age_ms=self.config.max_external_camera_age_ms,
            required=self.config.require_external_camera,
            warnings=warnings,
        )

        commanded = (
            gripper_commanded_open_fraction
            if gripper_commanded_open_fraction is not None
            else proprio.gripper_commanded_open_fraction
        )
        actual = (
            gripper_actual_open_fraction
            if gripper_actual_open_fraction is not None
            else proprio.gripper_actual_open_fraction
        )

        observation = RobotObservation(
            timestamp=SampleTimestamp(
                timestamp_utc=stamp,
                received_monotonic_ns=now_ns,
            ),
            joint_position_deg=proprio.joint_position_deg,
            joint_velocity_deg_s=proprio.joint_velocity_deg_s,
            ee_position_m=proprio.ee_position_m,
            ee_quaternion_xyzw=proprio.ee_quaternion_xyzw,
            gripper_commanded_open_fraction=commanded,
            gripper_actual_open_fraction=actual,
            wrist_camera=wrist_frame,
            external_camera=external_frame,
            robot_state_age_ms=robot_age_ms,
            ee_reference_frame=proprio.ee_reference_frame,
        )
        return SyncedObservation(
            observation=observation,
            warnings=tuple(warnings),
            wrist_age_ms=wrist_age_ms,
            external_age_ms=external_age_ms,
        )

    def _read_optional_camera(
        self,
        *,
        source: CameraSource | None,
        slot_name: str,
        now_monotonic_ns: int,
        max_age_ms: float | None,
        required: bool,
        warnings: list[str],
    ) -> tuple[CameraFrame | None, float | None]:
        if source is None:
            if required:
                raise ObservationSyncError(f"{slot_name} is required but no source is configured")
            return None, None

        frame = source.read_camera(now_monotonic_ns=now_monotonic_ns)
        if frame is None:
            if required:
                raise ObservationSyncError(f"{slot_name} is required but no frame is available")
            warnings.append(f"{slot_name}_missing")
            return None, None

        age_ms = _sample_age_ms(frame.timestamp.received_monotonic_ns, now_monotonic_ns)
        if max_age_ms is not None and age_ms > float(max_age_ms):
            warning = f"{slot_name}_stale"
            warnings.append(warning)
            if self.config.drop_stale_cameras:
                if required:
                    raise ObservationSyncError(
                        f"{slot_name} age {age_ms:.3f} ms exceeds budget {float(max_age_ms):.3f} ms"
                    )
                return None, age_ms
        return frame, age_ms


def rpy_rad_to_quaternion_xyzw(rpy_rad: Vector3Like) -> QuaternionXYZW:
    """Convert roll-pitch-yaw (radians, XYZ intrinsic) to a normalized ``xyzw`` quaternion."""
    roll, pitch, yaw = (float(value) for value in rpy_rad)
    if not all(math.isfinite(value) for value in (roll, pitch, yaw)):
        raise ValueError("rpy_rad values must be finite")

    cr = math.cos(roll * 0.5)
    sr = math.sin(roll * 0.5)
    cp = math.cos(pitch * 0.5)
    sp = math.sin(pitch * 0.5)
    cy = math.cos(yaw * 0.5)
    sy = math.sin(yaw * 0.5)

    x = sr * cp * cy - cr * sp * sy
    y = cr * sp * cy + sr * cp * sy
    z = cr * cp * sy - sr * sp * cy
    w = cr * cp * cy + sr * sp * sy
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm <= 0.0:
        raise ValueError("quaternion norm must be positive")
    return (x / norm, y / norm, z / norm, w / norm)


def proprioceptive_sample_from_cartesian(
    *,
    timestamp: SampleTimestamp,
    joint_position_deg: JointVectorLike,
    ee_position_m: Vector3Like,
    ee_rpy_rad: Vector3Like,
    robot_state_age_ms: float,
    joint_velocity_deg_s: JointVectorLike | None = None,
    gripper_commanded_open_fraction: float | None = None,
    gripper_actual_open_fraction: float | None = None,
) -> ProprioceptiveSample:
    """Bridge Cartesian/RPY control-loop state into schema proprioception."""
    return ProprioceptiveSample(
        timestamp=timestamp,
        joint_position_deg=tuple(float(value) for value in joint_position_deg),
        joint_velocity_deg_s=(
            None if joint_velocity_deg_s is None else tuple(float(value) for value in joint_velocity_deg_s)
        ),
        ee_position_m=(
            float(ee_position_m[0]),
            float(ee_position_m[1]),
            float(ee_position_m[2]),
        ),
        ee_quaternion_xyzw=rpy_rad_to_quaternion_xyzw(ee_rpy_rad),
        robot_state_age_ms=float(robot_state_age_ms),
        gripper_commanded_open_fraction=gripper_commanded_open_fraction,
        gripper_actual_open_fraction=gripper_actual_open_fraction,
    )


def _sample_age_ms(received_monotonic_ns: int, now_monotonic_ns: int) -> float:
    age_ns = int(now_monotonic_ns) - int(received_monotonic_ns)
    if age_ns < 0:
        # Future-dated device samples are treated as age 0; callers may still inspect warnings.
        return 0.0
    return age_ns / 1_000_000.0


def _enforce_robot_state_freshness(
    robot_age_ms: float,
    *,
    config: ObservationSyncConfig,
    warnings: list[str],
) -> None:
    budget = config.max_robot_state_age_ms
    if budget is None or robot_age_ms <= float(budget):
        return
    message = f"robot_state_stale age_ms={robot_age_ms:.3f} budget_ms={float(budget):.3f}"
    if config.reject_stale_robot_state:
        raise ObservationSyncError(message)
    warnings.append("robot_state_stale")
