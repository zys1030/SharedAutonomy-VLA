"""Offline coverage for camera sources and runner observation wiring."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from sharedautonomy.assistance.safety_filter import (
    CartesianSafetyFilter,
    CartesianSafetyLimits,
    stamp_cartesian_workspace,
)
from sharedautonomy.control.manual import (
    ManualCartesianConfig,
    MockRobotStateSource,
    build_manual_cartesian_runner,
)
from sharedautonomy.control.observation import (
    CartesianProprioceptiveSource,
    build_observation_synchronizer,
)
from sharedautonomy.data.schema import CameraFrame, SampleTimestamp
from sharedautonomy.devices.cameras import CameraSession, MockRgbCamera, MockRgbdCamera
from sharedautonomy.devices.spacemouse import MockSpaceMouse, SpaceMouseConfig

pytestmark = pytest.mark.core


def _camera_frame(*, received_ns: int, with_depth: bool) -> CameraFrame:
    color = np.zeros((4, 5, 3), dtype=np.uint8)
    if with_depth:
        return CameraFrame(
            timestamp=SampleTimestamp(
                timestamp_utc=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
                received_monotonic_ns=received_ns,
            ),
            color_rgb=color,
            depth_raw=np.zeros((4, 5), dtype=np.uint16),
            depth_scale_m_per_unit=0.001,
        )
    return CameraFrame(
        timestamp=SampleTimestamp(
            timestamp_utc=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
            received_monotonic_ns=received_ns,
        ),
        color_rgb=color,
    )


def _identity_teleop() -> MockSpaceMouse:
    identity = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    return MockSpaceMouse(
        SpaceMouseConfig(
            deadzone=0.0,
            max_linear_speed_m_s=0.05,
            mount_orientation="custom",
            translation_transform=identity,
            rotation_transform=identity,
            input_timeout_s=0.1,
        ),
        deadman_active=True,
    )


def test_manual_runner_attaches_dual_camera_observation_per_step() -> None:
    wrist = MockRgbdCamera(frame=_camera_frame(received_ns=990_000_000, with_depth=True))
    external = MockRgbCamera(frame=_camera_frame(received_ns=970_000_000, with_depth=False))
    camera_session = CameraSession(wrist_camera=wrist, external_camera=external)
    robot_state_source = MockRobotStateSource(robot_state_age_ms=0.0)
    proprio_source = CartesianProprioceptiveSource(robot_state_source)
    synchronizer = build_observation_synchronizer(
        proprioception=proprio_source,
        camera_session=camera_session,
    )
    runner = build_manual_cartesian_runner(
        config=ManualCartesianConfig(control_rate_hz=10.0, enable_motion=False),
        teleop=_identity_teleop(),
        robot_state_source=robot_state_source,
        safety_filter=CartesianSafetyFilter(
            workspace=stamp_cartesian_workspace(),
            limits=CartesianSafetyLimits(),
        ),
        observation_synchronizer=synchronizer,
    )

    step = runner.step(now_monotonic_ns=1_000_000_000, dt_s=0.1)

    assert step.synced_observation is not None
    observation = step.synced_observation.observation
    assert observation.wrist_camera is not None
    assert observation.external_camera is not None
    assert observation.external_camera.depth_raw is None
    assert step.synced_observation.wrist_age_ms == pytest.approx(10.0)
    assert step.synced_observation.external_age_ms == pytest.approx(30.0)
