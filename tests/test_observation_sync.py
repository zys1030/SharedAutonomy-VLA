"""Offline coverage for the synchronized observation skeleton."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from sharedautonomy.data import (
    CameraFrame,
    ObservationSyncConfig,
    ObservationSyncError,
    ObservationSynchronizer,
    SampleTimestamp,
    StaticCameraSource,
    StaticProprioceptiveSource,
    proprioceptive_sample_from_cartesian,
    rpy_rad_to_quaternion_xyzw,
)

pytestmark = pytest.mark.extended


def _stamp(received_monotonic_ns: int) -> SampleTimestamp:
    return SampleTimestamp(
        timestamp_utc=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        received_monotonic_ns=received_monotonic_ns,
    )


def _proprio(*, received_ns: int = 1_000_000_000, age_ms: float = 5.0):
    return proprioceptive_sample_from_cartesian(
        timestamp=_stamp(received_ns),
        joint_position_deg=[0, 10, 20, 30, 40, 50],
        ee_position_m=[0.3, 0.0, 0.2],
        ee_rpy_rad=[0.0, 1.5707963267948966, 0.0],
        robot_state_age_ms=age_ms,
        gripper_commanded_open_fraction=1.0,
        gripper_actual_open_fraction=None,
    )


def _rgb_frame(*, received_ns: int, with_depth: bool = False) -> CameraFrame:
    color = np.zeros((4, 5, 3), dtype=np.uint8)
    if with_depth:
        return CameraFrame(
            timestamp=_stamp(received_ns),
            color_rgb=color,
            depth_raw=np.zeros((4, 5), dtype=np.uint16),
            depth_scale_m_per_unit=0.001,
        )
    return CameraFrame(timestamp=_stamp(received_ns), color_rgb=color)


def test_rpy_identity_pitch_maps_to_unit_quaternion() -> None:
    quaternion = rpy_rad_to_quaternion_xyzw((0.0, 0.0, 0.0))
    assert quaternion == pytest.approx((0.0, 0.0, 0.0, 1.0))


def test_capture_allows_missing_external_camera_slot() -> None:
    sync = ObservationSynchronizer(
        StaticProprioceptiveSource(_proprio(received_ns=1_000_000_000)),
        wrist_camera=StaticCameraSource(_rgb_frame(received_ns=999_980_000, with_depth=True)),
        external_camera=None,
        config=ObservationSyncConfig(require_external_camera=False),
    )

    result = sync.capture(now_monotonic_ns=1_000_000_000)

    assert result.observation.wrist_camera is not None
    assert result.observation.external_camera is None
    assert result.external_age_ms is None
    assert result.observation.gripper_actual_open_fraction is None
    assert result.warnings == ()


def test_external_rgb_only_camera_is_first_class_optional_slot() -> None:
    sync = ObservationSynchronizer(
        StaticProprioceptiveSource(_proprio(received_ns=1_000_000_000)),
        wrist_camera=StaticCameraSource(_rgb_frame(received_ns=999_990_000, with_depth=True)),
        external_camera=StaticCameraSource(_rgb_frame(received_ns=970_000_000, with_depth=False)),
    )

    result = sync.capture(now_monotonic_ns=1_000_000_000)

    assert result.observation.external_camera is not None
    assert result.observation.external_camera.depth_raw is None
    assert result.external_age_ms == pytest.approx(30.0)


def test_unconnected_external_source_emits_missing_warning() -> None:
    sync = ObservationSynchronizer(
        StaticProprioceptiveSource(_proprio(received_ns=1_000_000_000)),
        external_camera=StaticCameraSource(None),
    )

    result = sync.capture(now_monotonic_ns=1_000_000_000)

    assert result.observation.external_camera is None
    assert "external_camera_missing" in result.warnings


def test_required_external_camera_rejects_missing_frame() -> None:
    sync = ObservationSynchronizer(
        StaticProprioceptiveSource(_proprio(received_ns=1_000_000_000)),
        external_camera=StaticCameraSource(None),
        config=ObservationSyncConfig(require_external_camera=True),
    )

    with pytest.raises(ObservationSyncError, match="external_camera is required"):
        sync.capture(now_monotonic_ns=1_000_000_000)


def test_stale_robot_state_is_rejected_by_default() -> None:
    sync = ObservationSynchronizer(
        StaticProprioceptiveSource(_proprio(received_ns=900_000_000)),
        config=ObservationSyncConfig(max_robot_state_age_ms=50.0),
    )

    with pytest.raises(ObservationSyncError, match="robot_state_stale"):
        sync.capture(now_monotonic_ns=1_000_000_000)


def test_stale_camera_can_be_kept_with_warning_or_dropped() -> None:
    keep_sync = ObservationSynchronizer(
        StaticProprioceptiveSource(_proprio(received_ns=1_000_000_000)),
        wrist_camera=StaticCameraSource(_rgb_frame(received_ns=800_000_000, with_depth=True)),
        config=ObservationSyncConfig(max_wrist_camera_age_ms=100.0, drop_stale_cameras=False),
    )
    keep = keep_sync.capture(now_monotonic_ns=1_000_000_000)
    assert keep.observation.wrist_camera is not None
    assert "wrist_camera_stale" in keep.warnings

    drop_sync = ObservationSynchronizer(
        StaticProprioceptiveSource(_proprio(received_ns=1_000_000_000)),
        wrist_camera=StaticCameraSource(_rgb_frame(received_ns=800_000_000, with_depth=True)),
        config=ObservationSyncConfig(max_wrist_camera_age_ms=100.0, drop_stale_cameras=True),
    )
    dropped = drop_sync.capture(now_monotonic_ns=1_000_000_000)
    assert dropped.observation.wrist_camera is None
    assert "wrist_camera_stale" in dropped.warnings
