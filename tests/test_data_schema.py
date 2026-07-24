from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

pytestmark = pytest.mark.extended

from sharedautonomy.data import (
    AssistAction,
    CameraFrame,
    CollectionMode,
    EpisodeMetadata,
    ExecutedAction,
    HumanAction,
    RobotObservation,
    SampleTimestamp,
)


def timestamp() -> SampleTimestamp:
    return SampleTimestamp(
        timestamp_utc=datetime(2026, 7, 23, 12, 0, tzinfo=UTC),
        received_monotonic_ns=123_000_000,
        device_timestamp_ms=456.0,
        device_clock_domain="host_mapped_system_time",
        sequence_number=7,
    )


def test_sample_timestamp_requires_utc_and_paired_device_clock() -> None:
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        SampleTimestamp(
            timestamp_utc=datetime(2026, 7, 23, 12, 0),
            received_monotonic_ns=0,
        )

    with pytest.raises(ValueError, match="must either both be set"):
        SampleTimestamp(
            timestamp_utc=datetime(2026, 7, 23, 12, 0, tzinfo=UTC),
            received_monotonic_ns=0,
            device_timestamp_ms=1.0,
        )


def test_camera_frame_validates_shape_dtype_and_depth_scale() -> None:
    color = np.zeros((4, 5, 3), dtype=np.uint8)
    depth = np.zeros((4, 5), dtype=np.uint16)
    frame = CameraFrame(
        timestamp=timestamp(),
        color_rgb=color,
        depth_raw=depth,
        depth_scale_m_per_unit=0.001,
    )

    assert frame.color_rgb.shape == (4, 5, 3)
    assert frame.depth_raw is not None

    with pytest.raises(ValueError, match="dtype must be uint8"):
        CameraFrame(timestamp=timestamp(), color_rgb=color.astype(np.float32))

    with pytest.raises(ValueError, match="matching height and width"):
        CameraFrame(
            timestamp=timestamp(),
            color_rgb=color,
            depth_raw=np.zeros((3, 5), dtype=np.uint16),
            depth_scale_m_per_unit=0.001,
        )


def test_robot_observation_normalizes_vectors_and_preserves_missing_feedback() -> None:
    observation = RobotObservation(
        timestamp=timestamp(),
        joint_position_deg=[0, 1, 2, 3, 4, 5],
        joint_velocity_deg_s=None,
        ee_position_m=[0.1, 0.2, 0.3],
        ee_quaternion_xyzw=[0, 0, 0, 1],
        gripper_commanded_open_fraction=1.0,
        gripper_actual_open_fraction=None,
        wrist_camera=None,
        external_camera=None,
        robot_state_age_ms=4.5,
    )

    assert observation.joint_position_deg == (0.0, 1.0, 2.0, 3.0, 4.0, 5.0)
    assert observation.gripper_actual_open_fraction is None


def test_robot_observation_rejects_invalid_joint_count_and_quaternion() -> None:
    common = {
        "timestamp": timestamp(),
        "joint_velocity_deg_s": None,
        "ee_position_m": [0, 0, 0],
        "gripper_commanded_open_fraction": None,
        "gripper_actual_open_fraction": None,
        "wrist_camera": None,
        "external_camera": None,
        "robot_state_age_ms": 0.0,
    }
    with pytest.raises(ValueError, match="must contain 6"):
        RobotObservation(
            joint_position_deg=[0] * 5,
            ee_quaternion_xyzw=[0, 0, 0, 1],
            **common,
        )
    with pytest.raises(ValueError, match="must be normalized"):
        RobotObservation(
            joint_position_deg=[0] * 6,
            ee_quaternion_xyzw=[0, 0, 0, 2],
            **common,
        )


def test_action_interfaces_share_physical_units_and_validate_ranges() -> None:
    human = HumanAction(
        timestamp=timestamp(),
        linear_velocity_m_s=[0.01, 0, 0],
        angular_velocity_rad_s=[0, 0, 0],
        gripper_target_open_fraction=None,
        deadman_active=True,
        input_age_ms=8.0,
    )
    assist = AssistAction(
        timestamp=timestamp(),
        linear_velocity_m_s=[0.005, 0, 0],
        angular_velocity_rad_s=[0, 0, 0],
        gripper_target_open_fraction=1.0,
        confidence=0.8,
        inferred_target_id="red-block",
    )
    executed = ExecutedAction(
        timestamp=timestamp(),
        linear_velocity_m_s=[0.008, 0, 0],
        angular_velocity_rad_s=[0, 0, 0],
        gripper_target_open_fraction=1.0,
        joint_target_deg=[0, 1, 2, 3, 4, 5],
        actual_dt_s=0.02,
        authority=0.4,
        safety_intervened=True,
        safety_reasons=("workspace_clip",),
    )

    assert human.linear_velocity_m_s == (0.01, 0.0, 0.0)
    assert assist.confidence == 0.8
    assert executed.joint_target_deg == (0.0, 1.0, 2.0, 3.0, 4.0, 5.0)

    with pytest.raises(ValueError, match=r"in \[0, 1\]"):
        AssistAction(
            timestamp=timestamp(),
            linear_velocity_m_s=[0, 0, 0],
            angular_velocity_rad_s=[0, 0, 0],
            gripper_target_open_fraction=None,
            confidence=1.1,
            inferred_target_id=None,
        )


def test_executed_action_requires_consistent_safety_reasons() -> None:
    with pytest.raises(ValueError, match="must be empty"):
        ExecutedAction(
            timestamp=timestamp(),
            linear_velocity_m_s=[0, 0, 0],
            angular_velocity_rad_s=[0, 0, 0],
            gripper_target_open_fraction=None,
            joint_target_deg=None,
            actual_dt_s=0.02,
            authority=0.0,
            safety_intervened=False,
            safety_reasons=("unexpected",),
        )


def test_episode_metadata_tracks_lifecycle_and_outcome() -> None:
    started = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
    metadata = EpisodeMetadata(
        episode_id="episode-0001",
        run_id="run-0001",
        task_id="red-left",
        task_text="Pick up the red block and place it in the left region.",
        source_object="red",
        destination="left",
        collection_mode=CollectionMode.MANUAL,
        started_at_utc=started,
        ended_at_utc=started + timedelta(seconds=10),
        success=True,
        failure_reason=None,
        control_rate_hz=50.0,
        effective_config_path="outputs/runs/run-0001/effective_config.yaml",
        git_commit=None,
    )

    assert metadata.collection_mode is CollectionMode.MANUAL

    with pytest.raises(ValueError, match="requires success=False"):
        EpisodeMetadata(
            episode_id="episode-0002",
            run_id="run-0001",
            task_id="red-left",
            task_text="task",
            source_object="red",
            destination="left",
            collection_mode="manual",
            started_at_utc=started,
            ended_at_utc=None,
            success=None,
            failure_reason="not finished",
            control_rate_hz=50.0,
            effective_config_path="effective_config.yaml",
            git_commit=None,
        )
