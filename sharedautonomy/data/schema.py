"""Typed runtime interfaces for observations, actions, and episode metadata."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

import numpy as np
from numpy.typing import NDArray

SCHEMA_VERSION = "1.0.0"
JOINT_COUNT = 6

Vector3 = tuple[float, float, float]
QuaternionXYZW = tuple[float, float, float, float]
JointVector = tuple[float, ...]
ColorImage = NDArray[np.uint8]
DepthImage = NDArray[np.uint16]


class CoordinateFrame(StrEnum):
    """Coordinate frames accepted by the first-stage control interfaces."""

    BASE = "base"


class CollectionMode(StrEnum):
    """Supported episode collection modes."""

    MANUAL = "manual"
    SHARED_AUTONOMY = "shared_autonomy"
    CORRECTIVE = "corrective"


@dataclass(frozen=True, slots=True)
class SampleTimestamp:
    """Source timestamp plus the host receive time used for cross-device alignment."""

    timestamp_utc: datetime
    received_monotonic_ns: int
    device_timestamp_ms: float | None = None
    device_clock_domain: str | None = None
    sequence_number: int | None = None

    def __post_init__(self) -> None:
        _validate_utc_datetime(self.timestamp_utc, "timestamp_utc")
        if not isinstance(self.received_monotonic_ns, int) or self.received_monotonic_ns < 0:
            raise ValueError("received_monotonic_ns must be a non-negative integer")
        if (self.device_timestamp_ms is None) != (self.device_clock_domain is None):
            raise ValueError(
                "device_timestamp_ms and device_clock_domain must either both be set or both be None"
            )
        if self.device_timestamp_ms is not None:
            _validate_finite(self.device_timestamp_ms, "device_timestamp_ms")
            _validate_non_empty(self.device_clock_domain, "device_clock_domain")
        if self.sequence_number is not None and (
            not isinstance(self.sequence_number, int) or self.sequence_number < 0
        ):
            raise ValueError("sequence_number must be a non-negative integer or None")


@dataclass(frozen=True, slots=True)
class CameraFrame:
    """One RGB frame with optional aligned raw depth and its own source timestamp."""

    timestamp: SampleTimestamp
    color_rgb: ColorImage
    depth_raw: DepthImage | None = None
    depth_scale_m_per_unit: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.color_rgb, np.ndarray):
            raise TypeError("color_rgb must be a numpy.ndarray")
        if self.color_rgb.dtype != np.uint8:
            raise ValueError("color_rgb dtype must be uint8")
        if self.color_rgb.ndim != 3 or self.color_rgb.shape[2] != 3:
            raise ValueError("color_rgb shape must be (height, width, 3)")

        if self.depth_raw is None:
            if self.depth_scale_m_per_unit is not None:
                raise ValueError("depth_scale_m_per_unit must be None when depth_raw is None")
            return

        if not isinstance(self.depth_raw, np.ndarray):
            raise TypeError("depth_raw must be a numpy.ndarray or None")
        if self.depth_raw.dtype != np.uint16:
            raise ValueError("depth_raw dtype must be uint16")
        if self.depth_raw.ndim != 2:
            raise ValueError("depth_raw shape must be (height, width)")
        if self.depth_raw.shape != self.color_rgb.shape[:2]:
            raise ValueError("depth_raw and color_rgb must have matching height and width")
        if self.depth_scale_m_per_unit is None:
            raise ValueError("depth_scale_m_per_unit is required when depth_raw is present")
        _validate_positive(self.depth_scale_m_per_unit, "depth_scale_m_per_unit")


@dataclass(frozen=True, slots=True)
class RobotObservation:
    """Observation assembled for one control step.

    ``timestamp`` is the assembly/control-step time. Each camera frame retains its
    own source and receive timestamp.
    """

    timestamp: SampleTimestamp
    joint_position_deg: JointVector
    joint_velocity_deg_s: JointVector | None
    ee_position_m: Vector3
    ee_quaternion_xyzw: QuaternionXYZW
    gripper_commanded_open_fraction: float | None
    gripper_actual_open_fraction: float | None
    wrist_camera: CameraFrame | None
    external_camera: CameraFrame | None
    robot_state_age_ms: float
    ee_reference_frame: CoordinateFrame = CoordinateFrame.BASE

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "joint_position_deg",
            _finite_tuple(self.joint_position_deg, JOINT_COUNT, "joint_position_deg"),
        )
        if self.joint_velocity_deg_s is not None:
            object.__setattr__(
                self,
                "joint_velocity_deg_s",
                _finite_tuple(
                    self.joint_velocity_deg_s,
                    JOINT_COUNT,
                    "joint_velocity_deg_s",
                ),
            )
        object.__setattr__(
            self,
            "ee_position_m",
            _finite_tuple(self.ee_position_m, 3, "ee_position_m"),
        )
        quaternion = _finite_tuple(
            self.ee_quaternion_xyzw,
            4,
            "ee_quaternion_xyzw",
        )
        quaternion_norm = math.sqrt(sum(value * value for value in quaternion))
        if not math.isclose(quaternion_norm, 1.0, abs_tol=1e-3):
            raise ValueError("ee_quaternion_xyzw must be normalized")
        object.__setattr__(self, "ee_quaternion_xyzw", quaternion)
        _validate_optional_fraction(
            self.gripper_commanded_open_fraction,
            "gripper_commanded_open_fraction",
        )
        _validate_optional_fraction(
            self.gripper_actual_open_fraction,
            "gripper_actual_open_fraction",
        )
        _validate_non_negative(self.robot_state_age_ms, "robot_state_age_ms")
        object.__setattr__(
            self,
            "ee_reference_frame",
            CoordinateFrame(self.ee_reference_frame),
        )


@dataclass(frozen=True, slots=True)
class HumanAction:
    """Physical-space command derived from the operator input."""

    timestamp: SampleTimestamp
    linear_velocity_m_s: Vector3
    angular_velocity_rad_s: Vector3
    gripper_target_open_fraction: float | None
    deadman_active: bool
    input_age_ms: float
    reference_frame: CoordinateFrame = CoordinateFrame.BASE
    gripper_button_edge: bool = False

    def __post_init__(self) -> None:
        _validate_action_vectors(self)
        if not isinstance(self.deadman_active, bool):
            raise TypeError("deadman_active must be bool")
        if not isinstance(self.gripper_button_edge, bool):
            raise TypeError("gripper_button_edge must be bool")
        _validate_non_negative(self.input_age_ms, "input_age_ms")


@dataclass(frozen=True, slots=True)
class AssistAction:
    """Physical-space action proposed by the local assistance policy."""

    timestamp: SampleTimestamp
    linear_velocity_m_s: Vector3
    angular_velocity_rad_s: Vector3
    gripper_target_open_fraction: float | None
    confidence: float
    inferred_target_id: str | None
    reference_frame: CoordinateFrame = CoordinateFrame.BASE

    def __post_init__(self) -> None:
        _validate_action_vectors(self)
        _validate_fraction(self.confidence, "confidence")
        if self.inferred_target_id is not None:
            _validate_non_empty(self.inferred_target_id, "inferred_target_id")


@dataclass(frozen=True, slots=True)
class ExecutedAction:
    """Safety-filtered action and the hardware joint target actually sent."""

    timestamp: SampleTimestamp
    linear_velocity_m_s: Vector3
    angular_velocity_rad_s: Vector3
    gripper_target_open_fraction: float | None
    joint_target_deg: JointVector | None
    actual_dt_s: float
    authority: float
    safety_intervened: bool
    safety_reasons: tuple[str, ...] = ()
    reference_frame: CoordinateFrame = CoordinateFrame.BASE

    def __post_init__(self) -> None:
        _validate_action_vectors(self)
        if self.joint_target_deg is not None:
            object.__setattr__(
                self,
                "joint_target_deg",
                _finite_tuple(self.joint_target_deg, JOINT_COUNT, "joint_target_deg"),
            )
        _validate_positive(self.actual_dt_s, "actual_dt_s")
        _validate_fraction(self.authority, "authority")
        if not isinstance(self.safety_intervened, bool):
            raise TypeError("safety_intervened must be bool")
        reasons = tuple(self.safety_reasons)
        for reason in reasons:
            _validate_non_empty(reason, "safety_reasons item")
        if not self.safety_intervened and reasons:
            raise ValueError("safety_reasons must be empty when safety_intervened is False")
        object.__setattr__(self, "safety_reasons", reasons)


@dataclass(frozen=True, slots=True)
class EpisodeMetadata:
    """Metadata needed to reproduce and interpret one recorded episode."""

    episode_id: str
    run_id: str
    task_id: str
    task_text: str
    source_object: str | None
    destination: str | None
    collection_mode: CollectionMode
    started_at_utc: datetime
    ended_at_utc: datetime | None
    success: bool | None
    failure_reason: str | None
    control_rate_hz: float
    effective_config_path: str
    git_commit: str | None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for value, name in (
            (self.episode_id, "episode_id"),
            (self.run_id, "run_id"),
            (self.task_id, "task_id"),
            (self.task_text, "task_text"),
            (self.effective_config_path, "effective_config_path"),
            (self.schema_version, "schema_version"),
        ):
            _validate_non_empty(value, name)
        for value, name in (
            (self.source_object, "source_object"),
            (self.destination, "destination"),
            (self.failure_reason, "failure_reason"),
            (self.git_commit, "git_commit"),
        ):
            if value is not None:
                _validate_non_empty(value, name)
        object.__setattr__(
            self,
            "collection_mode",
            CollectionMode(self.collection_mode),
        )
        _validate_utc_datetime(self.started_at_utc, "started_at_utc")
        if self.ended_at_utc is not None:
            _validate_utc_datetime(self.ended_at_utc, "ended_at_utc")
            if self.ended_at_utc < self.started_at_utc:
                raise ValueError("ended_at_utc must not be earlier than started_at_utc")
        if self.success is not None and not isinstance(self.success, bool):
            raise TypeError("success must be bool or None")
        if self.success is True and self.failure_reason is not None:
            raise ValueError("failure_reason must be None when success is True")
        if self.failure_reason is not None and self.success is not False:
            raise ValueError("failure_reason requires success=False")
        _validate_positive(self.control_rate_hz, "control_rate_hz")


def _validate_action_vectors(action: Any) -> None:
    object.__setattr__(
        action,
        "linear_velocity_m_s",
        _finite_tuple(action.linear_velocity_m_s, 3, "linear_velocity_m_s"),
    )
    object.__setattr__(
        action,
        "angular_velocity_rad_s",
        _finite_tuple(action.angular_velocity_rad_s, 3, "angular_velocity_rad_s"),
    )
    _validate_optional_fraction(
        action.gripper_target_open_fraction,
        "gripper_target_open_fraction",
    )
    object.__setattr__(
        action,
        "reference_frame",
        CoordinateFrame(action.reference_frame),
    )


def _finite_tuple(values: Any, expected_size: int, name: str) -> tuple[float, ...]:
    try:
        result = tuple(float(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be an iterable of numbers") from exc
    if len(result) != expected_size:
        raise ValueError(f"{name} must contain {expected_size} values, got {len(result)}")
    if not all(math.isfinite(value) for value in result):
        raise ValueError(f"{name} must contain only finite values")
    return result


def _validate_utc_datetime(value: datetime, name: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be timezone-aware UTC")


def _validate_non_empty(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _validate_finite(value: float, name: str) -> None:
    if not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")


def _validate_positive(value: float, name: str) -> None:
    _validate_finite(value, name)
    if float(value) <= 0:
        raise ValueError(f"{name} must be positive")


def _validate_non_negative(value: float, name: str) -> None:
    _validate_finite(value, name)
    if float(value) < 0:
        raise ValueError(f"{name} must be non-negative")


def _validate_fraction(value: float, name: str) -> None:
    _validate_finite(value, name)
    if not 0 <= float(value) <= 1:
        raise ValueError(f"{name} must be in [0, 1]")


def _validate_optional_fraction(value: float | None, name: str) -> None:
    if value is not None:
        _validate_fraction(value, name)
