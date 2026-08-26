"""Tests for native episode → LeRobot export mapping (ADR 0002)."""

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
)
from sharedautonomy.data.lerobot_export import (
    LeRobotExportError,
    ProprioHistoryTracker,
    build_lerobot_features,
    build_lerobot_frame,
    export_lerobot_dataset,
    infer_image_shape,
    iter_native_frames,
    load_native_episode_envelope,
)

pytestmark = pytest.mark.core

IMAGE_SHAPE = (4, 5, 3)


def _stamp(received_monotonic_ns: int = 1_000_000_000) -> SampleTimestamp:
    return SampleTimestamp(
        timestamp_utc=datetime(2026, 7, 27, 8, 0, 0, tzinfo=UTC),
        received_monotonic_ns=received_monotonic_ns,
    )


def _observation(*, color_offset: int = 0) -> RobotObservation:
    color = np.arange(IMAGE_SHAPE[0] * IMAGE_SHAPE[1] * 3, dtype=np.uint8).reshape(IMAGE_SHAPE)
    color = np.ascontiguousarray(color + color_offset, dtype=np.uint8)
    return RobotObservation(
        timestamp=_stamp(),
        joint_position_deg=[10.0, 11.0, 12.0, 13.0, 14.0, 15.0],
        joint_velocity_deg_s=None,
        ee_position_m=[0.3, 0.0, 0.2],
        ee_quaternion_xyzw=[0, 0, 0, 1],
        gripper_commanded_open_fraction=0.6,
        gripper_actual_open_fraction=None,
        wrist_camera=CameraFrame(
            timestamp=_stamp(999_000_000),
            color_rgb=color,
            depth_raw=np.ones(IMAGE_SHAPE[:2], dtype=np.uint16),
            depth_scale_m_per_unit=0.001,
        ),
        external_camera=CameraFrame(
            timestamp=_stamp(998_000_000),
            color_rgb=np.full(IMAGE_SHAPE, 9, dtype=np.uint8),
        ),
        robot_state_age_ms=4.0,
    )


def _human(*, deadman: bool = True) -> HumanAction:
    return HumanAction(
        timestamp=_stamp(),
        linear_velocity_m_s=[0.01, 0.0, 0.0],
        angular_velocity_rad_s=[0.0, 0.0, 0.0],
        gripper_target_open_fraction=0.4,
        deadman_active=deadman,
        input_age_ms=8.0,
    )


def _executed(*, safety: bool = False) -> ExecutedAction:
    return ExecutedAction(
        timestamp=_stamp(),
        linear_velocity_m_s=[0.008, 0.0, 0.0],
        angular_velocity_rad_s=[0.0, 0.0, 0.0],
        gripper_target_open_fraction=0.4,
        joint_target_deg=[20.0, 21.0, 22.0, 23.0, 24.0, 25.0],
        actual_dt_s=0.1,
        authority=0.0,
        safety_intervened=safety,
        safety_reasons=("workspace_clip",) if safety else (),
    )


def _metadata() -> EpisodeMetadata:
    return EpisodeMetadata(
        episode_id="episode-export-test-001",
        run_id="run-export-test-001",
        task_id="shape_pick_place_v1",
        task_text="Pick up the red circle and place it in the UP region.",
        source_object="red",
        destination="up",
        collection_mode=CollectionMode.MANUAL,
        started_at_utc=datetime(2026, 7, 27, 8, 0, 0, tzinfo=UTC),
        ended_at_utc=None,
        success=None,
        failure_reason=None,
        control_rate_hz=10.0,
        effective_config_path="outputs/runs/run-export-test-001/effective_config.yaml",
        git_commit=None,
    )


def _write_exportable_episode(episode_dir) -> None:
    recorder = EpisodeRecorder(episode_dir)
    recorder.start(_metadata())
    recorder.record_step(
        observation=_observation(color_offset=0),
        human_action=_human(deadman=True),
        executed_action=_executed(safety=True),
        sync_warnings=("wrist_camera_stale",),
    )
    recorder.record_step(
        observation=_observation(color_offset=1),
        human_action=_human(deadman=False),
        executed_action=_executed(safety=False),
    )
    recorder.end(success=True, ended_at_utc=datetime(2026, 7, 27, 8, 0, 20, tzinfo=UTC))


def test_build_lerobot_features_includes_state_action_images_and_diag() -> None:
    features = build_lerobot_features(image_shape=IMAGE_SHAPE, use_videos=True, include_diag=True)

    assert features["observation.state"]["shape"] == (10,)
    assert features["action"]["shape"] == (7,)
    assert features["observation.state"]["names"] == [
        "joint_1.pos",
        "joint_2.pos",
        "joint_3.pos",
        "joint_4.pos",
        "joint_5.pos",
        "joint_6.pos",
        "gripper.pos",
        "ee.z",
        "ee.dz",
        "gripper.time_since_close",
    ]
    assert features["observation.images.wrist"]["dtype"] == "video"
    assert features["observation.images.external"]["shape"] == IMAGE_SHAPE
    assert features["diag.deadman_active"]["shape"] == (1,)
    assert "diag.wall_time_s" in features


def test_build_lerobot_frame_maps_joint_gripper_and_diag() -> None:
    started = datetime(2026, 7, 27, 8, 0, 0, tzinfo=UTC)
    step_payload = {
        "step_index": 0,
        "observation": {
            "joint_position_deg": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            "gripper_commanded_open_fraction": 0.55,
            "ee_position_m": [0.30, -0.10, 0.179],
            "timestamp": {"timestamp_utc": "2026-07-27T08:00:01.000000Z"},
        },
        "human_action": {"deadman_active": True},
        "executed_action": {
            "joint_target_deg": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
            "gripper_target_open_fraction": 0.45,
            "actual_dt_s": 0.09,
            "safety_intervened": True,
        },
    }
    images = {
        "wrist": np.zeros(IMAGE_SHAPE, dtype=np.uint8),
        "external": np.zeros(IMAGE_SHAPE, dtype=np.uint8),
    }

    frame = build_lerobot_frame(
        step_payload,
        images=images,
        task_text="Pick up the red circle and place it in the UP region.",
        episode_started_at_utc=started,
    )

    assert frame["task"] == "Pick up the red circle and place it in the UP region."
    np.testing.assert_allclose(
        frame["observation.state"],
        np.array([1, 2, 3, 4, 5, 6, 0.55, 0.179, 0.0, 0.0], dtype=np.float32),
    )
    np.testing.assert_allclose(
        frame["action"],
        np.array([7, 8, 9, 10, 11, 12, 0.45], dtype=np.float32),
    )
    assert frame["diag.deadman_active"][0] == pytest.approx(1.0)
    assert frame["diag.safety_intervened"][0] == pytest.approx(1.0)
    assert frame["diag.wall_time_s"][0] == pytest.approx(1.0)


def test_build_lerobot_frame_appends_history_across_steps() -> None:
    started = datetime(2026, 7, 27, 8, 0, 0, tzinfo=UTC)
    images = {
        "wrist": np.zeros(IMAGE_SHAPE, dtype=np.uint8),
        "external": np.zeros(IMAGE_SHAPE, dtype=np.uint8),
    }
    history = ProprioHistoryTracker()

    def _payload(*, step_index: int, gripper: float, ee_z: float) -> dict:
        return {
            "step_index": step_index,
            "observation": {
                "joint_position_deg": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                "gripper_commanded_open_fraction": gripper,
                "ee_position_m": [0.30, -0.10, ee_z],
                "timestamp": {"timestamp_utc": "2026-07-27T08:00:01.000000Z"},
            },
            "human_action": {"deadman_active": True},
            "executed_action": {
                "joint_target_deg": [7.0, 8.0, 9.0, 10.0, 11.0, 12.0],
                "gripper_target_open_fraction": gripper,
                "actual_dt_s": 0.1,
                "safety_intervened": False,
            },
        }

    open_frame = build_lerobot_frame(
        _payload(step_index=0, gripper=1.0, ee_z=0.185),
        images=images,
        task_text="t",
        episode_started_at_utc=started,
        history=history,
    )
    close_frame = build_lerobot_frame(
        _payload(step_index=1, gripper=0.0, ee_z=0.179),
        images=images,
        task_text="t",
        episode_started_at_utc=started,
        history=history,
    )
    hold_frame = build_lerobot_frame(
        _payload(step_index=2, gripper=0.0, ee_z=0.179),
        images=images,
        task_text="t",
        episode_started_at_utc=started,
        history=history,
    )

    np.testing.assert_allclose(open_frame["observation.state"][8:], [0.0, 0.0], atol=1e-6)
    np.testing.assert_allclose(close_frame["observation.state"][8], -0.006, atol=1e-6)
    np.testing.assert_allclose(close_frame["observation.state"][9], 0.0, atol=1e-6)
    np.testing.assert_allclose(hold_frame["observation.state"][8], 0.0, atol=1e-6)
    np.testing.assert_allclose(hold_frame["observation.state"][9], 1.0 / 20.0, atol=1e-6)


def test_iter_native_frames_streams_two_steps(tmp_path) -> None:
    episode_dir = tmp_path / "episode"
    _write_exportable_episode(episode_dir)

    frames = list(iter_native_frames(episode_dir, image_shape=IMAGE_SHAPE))

    assert len(frames) == 2
    assert frames[0]["observation.images.wrist"].shape == IMAGE_SHAPE
    assert frames[1]["diag.deadman_active"][0] == pytest.approx(0.0)


def test_infer_image_shape_reads_first_wrist_frame(tmp_path) -> None:
    episode_dir = tmp_path / "episode"
    _write_exportable_episode(episode_dir)

    assert infer_image_shape(episode_dir) == IMAGE_SHAPE


def test_export_lerobot_dataset_round_trip(tmp_path) -> None:
    pytest.importorskip("lerobot")
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    episode_dir = tmp_path / "episode"
    _write_exportable_episode(episode_dir)
    out_root = tmp_path / "lerobot_out"

    export_lerobot_dataset(
        [episode_dir],
        out_root=out_root,
        repo_id="local/export_test",
        use_videos=False,
        parallel_encoding=False,
    )

    manifest = json.loads((out_root / "export_manifest.json").read_text(encoding="utf-8"))
    assert manifest["fps"] == 10
    assert manifest["episodes"][0]["step_count"] == 2

    dataset = LeRobotDataset("local/export_test", root=out_root)
    assert dataset.num_episodes == 1
    assert dataset.num_frames == 2
    item = dataset[0]
    assert item["observation.state"].shape == (10,)
    assert item["action"].shape == (7,)
    assert "diag.safety_intervened" in item
    np.testing.assert_allclose(item["observation.state"][7], 0.2, atol=1e-5)
    np.testing.assert_allclose(item["observation.state"][8], 0.0, atol=1e-5)
    np.testing.assert_allclose(item["observation.state"][9], 0.0, atol=1e-5)


def test_export_refuses_missing_joint_target(tmp_path) -> None:
    episode_dir = tmp_path / "episode"
    recorder = EpisodeRecorder(episode_dir)
    recorder.start(_metadata())
    executed = ExecutedAction(
        timestamp=_stamp(),
        linear_velocity_m_s=[0.008, 0.0, 0.0],
        angular_velocity_rad_s=[0.0, 0.0, 0.0],
        gripper_target_open_fraction=0.4,
        joint_target_deg=None,
        actual_dt_s=0.1,
        authority=0.0,
        safety_intervened=False,
    )
    recorder.record_step(
        observation=_observation(),
        human_action=_human(),
        executed_action=executed,
    )
    recorder.end(success=True, ended_at_utc=datetime(2026, 7, 27, 8, 0, 5, tzinfo=UTC))

    with pytest.raises(LeRobotExportError, match="joint_target_deg"):
        list(iter_native_frames(episode_dir, image_shape=IMAGE_SHAPE))


def test_load_native_episode_envelope_reads_task_text(tmp_path) -> None:
    episode_dir = tmp_path / "episode"
    _write_exportable_episode(episode_dir)

    envelope = load_native_episode_envelope(episode_dir)

    assert envelope.task_text == "Pick up the red circle and place it in the UP region."
    assert envelope.step_count == 2
