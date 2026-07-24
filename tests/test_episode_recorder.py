"""Offline coverage for the native episode recorder."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from sharedautonomy.data import (
    AssistAction,
    CameraFrame,
    CollectionMode,
    EpisodeMetadata,
    EpisodeRecorder,
    EpisodeRecorderError,
    ExecutedAction,
    HumanAction,
    RobotObservation,
    SampleTimestamp,
    load_recorded_episode,
)

pytestmark = pytest.mark.extended


def _stamp(received_monotonic_ns: int = 1_000_000_000) -> SampleTimestamp:
    return SampleTimestamp(
        timestamp_utc=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        received_monotonic_ns=received_monotonic_ns,
    )


def _observation(*, with_cameras: bool = True) -> RobotObservation:
    wrist = None
    external = None
    if with_cameras:
        color = np.arange(4 * 5 * 3, dtype=np.uint8).reshape(4, 5, 3)
        wrist = CameraFrame(
            timestamp=_stamp(999_000_000),
            color_rgb=color,
            depth_raw=np.ones((4, 5), dtype=np.uint16),
            depth_scale_m_per_unit=0.001,
        )
        external = CameraFrame(
            timestamp=_stamp(998_000_000),
            color_rgb=np.full((4, 5, 3), 7, dtype=np.uint8),
        )
    return RobotObservation(
        timestamp=_stamp(),
        joint_position_deg=[0, 1, 2, 3, 4, 5],
        joint_velocity_deg_s=None,
        ee_position_m=[0.3, 0.0, 0.2],
        ee_quaternion_xyzw=[0, 0, 0, 1],
        gripper_commanded_open_fraction=1.0,
        gripper_actual_open_fraction=None,
        wrist_camera=wrist,
        external_camera=external,
        robot_state_age_ms=4.0,
    )


def _human() -> HumanAction:
    return HumanAction(
        timestamp=_stamp(),
        linear_velocity_m_s=[0.01, 0.0, 0.0],
        angular_velocity_rad_s=[0.0, 0.0, 0.0],
        gripper_target_open_fraction=None,
        deadman_active=True,
        input_age_ms=8.0,
    )


def _executed(*, safety: bool = False) -> ExecutedAction:
    return ExecutedAction(
        timestamp=_stamp(),
        linear_velocity_m_s=[0.008, 0.0, 0.0],
        angular_velocity_rad_s=[0.0, 0.0, 0.0],
        gripper_target_open_fraction=1.0,
        joint_target_deg=[0, 1, 2, 3, 4, 5],
        actual_dt_s=0.1,
        authority=0.0,
        safety_intervened=safety,
        safety_reasons=("workspace_clip",) if safety else (),
    )


def _assist() -> AssistAction:
    return AssistAction(
        timestamp=_stamp(),
        linear_velocity_m_s=[0.005, 0.0, 0.0],
        angular_velocity_rad_s=[0.0, 0.0, 0.0],
        gripper_target_open_fraction=1.0,
        confidence=0.7,
        inferred_target_id="red-block",
    )


def _open_metadata() -> EpisodeMetadata:
    return EpisodeMetadata(
        episode_id="episode-0001",
        run_id="run-0001",
        task_id="red-left",
        task_text="Pick up the red block and place it in the left region.",
        source_object="red",
        destination="left",
        collection_mode=CollectionMode.MANUAL,
        started_at_utc=datetime(2026, 7, 24, 12, 0, tzinfo=UTC),
        ended_at_utc=None,
        success=None,
        failure_reason=None,
        control_rate_hz=10.0,
        effective_config_path="outputs/runs/run-0001/effective_config.yaml",
        git_commit=None,
    )


def test_recorder_round_trip_keeps_split_cameras_and_actions(tmp_path) -> None:
    recorder = EpisodeRecorder(tmp_path / "episode")
    recorder.start(_open_metadata())
    recorder.record_step(
        observation=_observation(with_cameras=True),
        human_action=_human(),
        executed_action=_executed(safety=True),
        assist_action=_assist(),
        sync_warnings=("external_camera_stale",),
    )
    recorder.record_step(
        observation=_observation(with_cameras=False),
        human_action=_human(),
        executed_action=_executed(),
    )
    metadata = recorder.end(success=True, ended_at_utc=datetime(2026, 7, 24, 12, 1, tzinfo=UTC))

    assert metadata.success is True
    assert metadata.control_rate_hz == 10.0
    assert recorder.step_count == 2

    loaded = load_recorded_episode(tmp_path / "episode")
    assert loaded.status == "completed"
    assert loaded.metadata.episode_id == "episode-0001"
    assert len(loaded.steps) == 2

    first = loaded.steps[0]
    assert first.assist_action is not None
    assert first.assist_action.inferred_target_id == "red-block"
    assert first.executed_action.safety_reasons == ("workspace_clip",)
    assert first.sync_warnings == ("external_camera_stale",)
    assert first.observation.wrist_camera is not None
    assert first.observation.external_camera is not None
    assert first.observation.external_camera.depth_raw is None
    assert first.observation.wrist_camera.color_rgb.shape == (4, 5, 3)
    assert loaded.steps[1].observation.wrist_camera is None


def test_abort_preserves_partial_steps(tmp_path) -> None:
    recorder = EpisodeRecorder(tmp_path / "episode")
    recorder.start(_open_metadata())
    recorder.record_step(
        observation=_observation(with_cameras=False),
        human_action=_human(),
        executed_action=_executed(),
    )
    metadata = recorder.abort(
        failure_reason="operator_estop",
        ended_at_utc=datetime(2026, 7, 24, 12, 0, 30, tzinfo=UTC),
    )

    assert metadata.success is False
    assert metadata.failure_reason == "operator_estop"

    loaded = load_recorded_episode(tmp_path / "episode")
    assert loaded.status == "aborted"
    assert len(loaded.steps) == 1


def test_recorder_refuses_overwrite_and_requires_start(tmp_path) -> None:
    episode_dir = tmp_path / "episode"
    recorder = EpisodeRecorder(episode_dir)
    with pytest.raises(EpisodeRecorderError, match="active start"):
        recorder.record_step(
            observation=_observation(with_cameras=False),
            human_action=_human(),
            executed_action=_executed(),
        )

    recorder.start(_open_metadata())
    recorder.end(success=True, ended_at_utc=datetime(2026, 7, 24, 12, 1, tzinfo=UTC))

    again = EpisodeRecorder(episode_dir)
    with pytest.raises(EpisodeRecorderError, match="refusing to overwrite"):
        again.start(_open_metadata())
