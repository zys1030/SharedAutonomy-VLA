"""Offline coverage for native episode validation helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np
import pytest
from sharedautonomy.data import (
    CameraFrame,
    CollectionMode,
    EpisodeMetadata,
    EpisodeRecorder,
    ExecutedAction,
    HumanAction,
    RobotObservation,
    SampleTimestamp,
    check_episode_dir,
    check_recorded_episode,
    episode_check_report_to_dict,
    format_episode_check_report,
    load_recorded_episode,
)

pytestmark = pytest.mark.core


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


def _human(*, deadman: bool = True) -> HumanAction:
    return HumanAction(
        timestamp=_stamp(),
        linear_velocity_m_s=[0.01, 0.0, 0.0],
        angular_velocity_rad_s=[0.0, 0.0, 0.0],
        gripper_target_open_fraction=None,
        deadman_active=deadman,
        input_age_ms=8.0,
    )


def _executed() -> ExecutedAction:
    return ExecutedAction(
        timestamp=_stamp(),
        linear_velocity_m_s=[0.008, 0.0, 0.0],
        angular_velocity_rad_s=[0.0, 0.0, 0.0],
        gripper_target_open_fraction=1.0,
        joint_target_deg=[0, 1, 2, 3, 4, 5],
        actual_dt_s=0.1,
        authority=0.0,
        safety_intervened=False,
        safety_reasons=(),
    )


def _open_metadata() -> EpisodeMetadata:
    return EpisodeMetadata(
        episode_id="episode-check-0001",
        run_id="run-check-0001",
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
        effective_config_path="outputs/runs/run-check-0001/effective_config.yaml",
        git_commit=None,
    )


def _write_sample_episode(episode_dir) -> None:
    recorder = EpisodeRecorder(episode_dir)
    recorder.start(_open_metadata())
    recorder.record_step(
        observation=_observation(with_cameras=True),
        human_action=_human(deadman=True),
        executed_action=_executed(),
        sync_warnings=("external_camera_stale",),
    )
    recorder.record_step(
        observation=_observation(with_cameras=False),
        human_action=_human(deadman=False),
        executed_action=_executed(),
    )
    recorder.end(success=True, ended_at_utc=datetime(2026, 7, 24, 12, 1, tzinfo=UTC))


def test_check_episode_dir_reports_coverage_and_sync_warnings(tmp_path) -> None:
    episode_dir = tmp_path / "episode"
    _write_sample_episode(episode_dir)

    report = check_episode_dir(episode_dir)

    assert report.ok
    assert report.step_count == 2
    assert report.metadata_step_count == 2
    assert report.step_count_consistent
    assert report.step_index_consistent
    assert report.camera_coverage.wrist_steps == 1
    assert report.camera_coverage.external_steps == 1
    assert report.camera_coverage.images_dir_present
    assert report.camera_coverage.wrist_image_files_found == 1
    assert report.camera_coverage.external_image_files_found == 1
    assert report.sync_warning_counts == {"external_camera_stale": 1}
    assert report.sync_warning_step_count == 1
    assert report.human_action_stats.deadman_active_steps == 1
    assert report.executed_action_stats.linear_norm_max_m_s == pytest.approx(0.008)
    assert "episode_id: episode-check-0001" in format_episode_check_report(report)


def test_check_recorded_episode_matches_dir_based_check(tmp_path) -> None:
    episode_dir = tmp_path / "episode"
    _write_sample_episode(episode_dir)

    loaded = load_recorded_episode(episode_dir)
    from_dir = check_episode_dir(episode_dir)
    from_loaded = check_recorded_episode(loaded)

    assert from_loaded.step_count == from_dir.step_count
    assert from_loaded.camera_coverage == from_dir.camera_coverage
    assert from_loaded.sync_warning_counts == from_dir.sync_warning_counts


def test_check_episode_dir_warns_when_images_dir_missing(tmp_path) -> None:
    episode_dir = tmp_path / "episode"
    _write_sample_episode(episode_dir)
    for image_path in (episode_dir / "images").glob("*.npy"):
        image_path.unlink()
    (episode_dir / "images").rmdir()

    report = check_episode_dir(episode_dir)

    assert report.ok
    assert report.camera_coverage.wrist_steps == 1
    assert report.camera_coverage.images_dir_present is False
    assert report.camera_coverage.wrist_image_files_found is None
    assert any("missing images/" in item for item in report.warnings)


def test_check_episode_dir_flags_missing_metadata(tmp_path) -> None:
    report = check_episode_dir(tmp_path / "missing")

    assert not report.ok
    assert report.issues == ("missing metadata.json",)


def test_episode_check_report_to_dict_is_json_serializable(tmp_path) -> None:
    episode_dir = tmp_path / "episode"
    _write_sample_episode(episode_dir)

    payload = episode_check_report_to_dict(check_episode_dir(episode_dir))

    assert payload["ok"] is True
    assert payload["episode_id"] == "episode-check-0001"
    assert payload["camera_coverage"]["wrist_steps"] == 1
    assert payload["sync_warning_counts"] == {"external_camera_stale": 1}
    assert isinstance(json.dumps(payload), str)
