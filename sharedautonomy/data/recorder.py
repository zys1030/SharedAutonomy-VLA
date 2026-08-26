"""Native episode recorder for SharedAutonomy control steps.

Records strongly typed schema objects to a run-local episode directory. This is
the semantic source of truth; a future LeRobot export adapter can flatten frames
separately.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from sharedautonomy.data.schema import (
    SCHEMA_VERSION,
    AssistAction,
    CameraFrame,
    CollectionMode,
    CoordinateFrame,
    EpisodeMetadata,
    ExecutedAction,
    HumanAction,
    RobotObservation,
    SampleTimestamp,
)

logger = logging.getLogger(__name__)

EPISODE_FORMAT = "sharedautonomy.episode.v1"
METADATA_FILENAME = "metadata.json"
STEPS_FILENAME = "steps.jsonl"
IMAGES_DIRNAME = "images"


class EpisodeRecorderError(RuntimeError):
    """Raised when the recorder lifecycle or on-disk format is invalid."""


@dataclass(frozen=True, slots=True)
class EpisodeStep:
    """One control-cycle record stored by the native episode recorder."""

    step_index: int
    observation: RobotObservation
    human_action: HumanAction
    executed_action: ExecutedAction
    assist_action: AssistAction | None = None
    sync_warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if int(self.step_index) < 0:
            raise ValueError("step_index must be non-negative")
        object.__setattr__(self, "sync_warnings", tuple(str(item) for item in self.sync_warnings))


@dataclass(frozen=True, slots=True)
class RecordedEpisode:
    """Episode metadata plus ordered steps loaded from disk."""

    metadata: EpisodeMetadata
    steps: tuple[EpisodeStep, ...]
    episode_dir: Path
    status: str


class EpisodeRecorder:
    """Append schema-native steps under ``episode_dir``.

    Layout::

        episode_dir/
        ├── metadata.json
        ├── steps.jsonl
        └── images/
            ├── step_000000_wrist_color.npy
            └── ...

    Steps are written incrementally so an abort or crash still leaves recoverable
    partial data. Images stay on separate camera keys; they are never stitched.
    """

    def __init__(self, episode_dir: str | Path) -> None:
        self.episode_dir = Path(episode_dir)
        self._metadata: EpisodeMetadata | None = None
        self._status: str = "idle"
        self._step_count = 0
        self._steps_file = None

    @property
    def metadata(self) -> EpisodeMetadata | None:
        return self._metadata

    @property
    def status(self) -> str:
        return self._status

    @property
    def step_count(self) -> int:
        return self._step_count

    @property
    def is_recording(self) -> bool:
        return self._status == "recording"

    def start(self, metadata: EpisodeMetadata) -> EpisodeMetadata:
        """Begin a new episode. ``metadata`` should usually have open-ended outcome fields."""
        if self._status == "recording":
            raise EpisodeRecorderError("episode already recording; end or abort first")
        if metadata.ended_at_utc is not None:
            raise ValueError("start() expects ended_at_utc=None")
        if metadata.success is not None:
            raise ValueError("start() expects success=None")
        if metadata.failure_reason is not None:
            raise ValueError("start() expects failure_reason=None")
        if float(metadata.control_rate_hz) <= 0.0:
            raise ValueError("control_rate_hz must be positive")

        self.episode_dir.mkdir(parents=True, exist_ok=True)
        images_dir = self.episode_dir / IMAGES_DIRNAME
        images_dir.mkdir(parents=True, exist_ok=True)
        steps_path = self.episode_dir / STEPS_FILENAME
        if steps_path.exists() or (self.episode_dir / METADATA_FILENAME).exists():
            raise EpisodeRecorderError(
                f"refusing to overwrite existing episode artifacts in {self.episode_dir}"
            )

        self._metadata = metadata
        self._status = "recording"
        self._step_count = 0
        self._steps_file = steps_path.open("w", encoding="utf-8")
        self._write_metadata_file()
        logger.info(
            "Started episode %s at %s (control_rate_hz=%.3f)",
            metadata.episode_id,
            self.episode_dir,
            float(metadata.control_rate_hz),
        )
        return self._metadata

    def record_step(
        self,
        *,
        observation: RobotObservation,
        human_action: HumanAction,
        executed_action: ExecutedAction,
        assist_action: AssistAction | None = None,
        sync_warnings: tuple[str, ...] | list[str] = (),
        step_index: int | None = None,
    ) -> EpisodeStep:
        """Append one control step. Image arrays are flushed immediately to disk."""
        if not self.is_recording or self._metadata is None or self._steps_file is None:
            raise EpisodeRecorderError("record_step() requires an active start()")

        index = self._step_count if step_index is None else int(step_index)
        if index != self._step_count:
            raise ValueError(f"expected step_index={self._step_count}, got {index}")

        step = EpisodeStep(
            step_index=index,
            observation=observation,
            human_action=human_action,
            executed_action=executed_action,
            assist_action=assist_action,
            sync_warnings=tuple(sync_warnings),
        )
        payload = _serialize_step(step, episode_dir=self.episode_dir)
        self._steps_file.write(json.dumps(payload, ensure_ascii=True) + "\n")
        self._steps_file.flush()
        self._step_count += 1
        self._write_metadata_file()
        return step

    def end(self, *, success: bool, ended_at_utc: datetime | None = None) -> EpisodeMetadata:
        """Finalize a completed episode."""
        if success:
            return self._finalize(
                status="completed",
                success=True,
                failure_reason=None,
                ended_at_utc=ended_at_utc,
            )
        raise ValueError("end(success=False) is invalid; use abort(failure_reason=...)")

    def abort(
        self,
        *,
        failure_reason: str,
        ended_at_utc: datetime | None = None,
    ) -> EpisodeMetadata:
        """Finalize an interrupted or failed episode while keeping recorded steps."""
        return self._finalize(
            status="aborted",
            success=False,
            failure_reason=failure_reason,
            ended_at_utc=ended_at_utc,
        )

    def _finalize(
        self,
        *,
        status: str,
        success: bool,
        failure_reason: str | None,
        ended_at_utc: datetime | None,
    ) -> EpisodeMetadata:
        if not self.is_recording or self._metadata is None:
            raise EpisodeRecorderError("no active episode to finalize")
        ended = ended_at_utc or datetime.now(tz=UTC)
        self._metadata = replace(
            self._metadata,
            ended_at_utc=ended,
            success=success,
            failure_reason=failure_reason,
        )
        self._status = status
        self._close_steps_file()
        self._write_metadata_file()
        logger.info(
            "Finalized episode %s status=%s steps=%s success=%s",
            self._metadata.episode_id,
            status,
            self._step_count,
            success,
        )
        return self._metadata

    def _close_steps_file(self) -> None:
        if self._steps_file is not None:
            self._steps_file.close()
            self._steps_file = None

    def _write_metadata_file(self) -> None:
        if self._metadata is None:
            raise EpisodeRecorderError("metadata is not initialized")
        payload = {
            "format": EPISODE_FORMAT,
            "schema_version": SCHEMA_VERSION,
            "status": self._status,
            "step_count": self._step_count,
            "metadata": _serialize_episode_metadata(self._metadata),
        }
        path = self.episode_dir / METADATA_FILENAME
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def load_recorded_episode(episode_dir: str | Path) -> RecordedEpisode:
    """Load a native episode directory written by ``EpisodeRecorder``."""
    root = Path(episode_dir)
    meta_path = root / METADATA_FILENAME
    steps_path = root / STEPS_FILENAME
    if not meta_path.is_file():
        raise EpisodeRecorderError(f"missing {METADATA_FILENAME} in {root}")
    if not steps_path.is_file():
        raise EpisodeRecorderError(f"missing {STEPS_FILENAME} in {root}")

    envelope = json.loads(meta_path.read_text(encoding="utf-8"))
    if envelope.get("format") != EPISODE_FORMAT:
        raise EpisodeRecorderError(
            f"unsupported episode format {envelope.get('format')!r}; expected {EPISODE_FORMAT!r}"
        )
    metadata = _deserialize_episode_metadata(envelope["metadata"])
    steps: list[EpisodeStep] = []
    with steps_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
                steps.append(_deserialize_step(payload, episode_dir=root))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError) as exc:
                raise EpisodeRecorderError(
                    f"failed to parse {STEPS_FILENAME} line {line_number}: {exc}"
                ) from exc

    expected = int(envelope.get("step_count", len(steps)))
    if expected != len(steps):
        raise EpisodeRecorderError(f"step_count mismatch: metadata={expected} steps.jsonl={len(steps)}")
    return RecordedEpisode(
        metadata=metadata,
        steps=tuple(steps),
        episode_dir=root,
        status=str(envelope.get("status", "unknown")),
    )


def _serialize_episode_metadata(metadata: EpisodeMetadata) -> dict[str, Any]:
    return {
        "episode_id": metadata.episode_id,
        "run_id": metadata.run_id,
        "task_id": metadata.task_id,
        "task_text": metadata.task_text,
        "source_object": metadata.source_object,
        "destination": metadata.destination,
        "collection_mode": str(metadata.collection_mode),
        "started_at_utc": _datetime_to_iso(metadata.started_at_utc),
        "ended_at_utc": None if metadata.ended_at_utc is None else _datetime_to_iso(metadata.ended_at_utc),
        "success": metadata.success,
        "failure_reason": metadata.failure_reason,
        "control_rate_hz": float(metadata.control_rate_hz),
        "effective_config_path": metadata.effective_config_path,
        "git_commit": metadata.git_commit,
        "schema_version": metadata.schema_version,
    }


def _deserialize_episode_metadata(payload: dict[str, Any]) -> EpisodeMetadata:
    return EpisodeMetadata(
        episode_id=str(payload["episode_id"]),
        run_id=str(payload["run_id"]),
        task_id=str(payload["task_id"]),
        task_text=str(payload["task_text"]),
        source_object=payload.get("source_object"),
        destination=payload.get("destination"),
        collection_mode=CollectionMode(payload["collection_mode"]),
        started_at_utc=_datetime_from_iso(payload["started_at_utc"]),
        ended_at_utc=(
            None if payload.get("ended_at_utc") is None else _datetime_from_iso(payload["ended_at_utc"])
        ),
        success=payload.get("success"),
        failure_reason=payload.get("failure_reason"),
        control_rate_hz=float(payload["control_rate_hz"]),
        effective_config_path=str(payload["effective_config_path"]),
        git_commit=payload.get("git_commit"),
        schema_version=str(payload.get("schema_version", SCHEMA_VERSION)),
    )


def _serialize_step(step: EpisodeStep, *, episode_dir: Path) -> dict[str, Any]:
    return {
        "step_index": step.step_index,
        "sync_warnings": list(step.sync_warnings),
        "observation": _serialize_observation(
            step.observation, episode_dir=episode_dir, step_index=step.step_index
        ),
        "human_action": _serialize_human_action(step.human_action),
        "assist_action": None if step.assist_action is None else _serialize_assist_action(step.assist_action),
        "executed_action": _serialize_executed_action(step.executed_action),
    }


def _deserialize_step(payload: dict[str, Any], *, episode_dir: Path) -> EpisodeStep:
    assist_payload = payload.get("assist_action")
    return EpisodeStep(
        step_index=int(payload["step_index"]),
        observation=_deserialize_observation(payload["observation"], episode_dir=episode_dir),
        human_action=_deserialize_human_action(payload["human_action"]),
        executed_action=_deserialize_executed_action(payload["executed_action"]),
        assist_action=None if assist_payload is None else _deserialize_assist_action(assist_payload),
        sync_warnings=tuple(payload.get("sync_warnings") or ()),
    )


def _serialize_observation(
    observation: RobotObservation,
    *,
    episode_dir: Path,
    step_index: int,
) -> dict[str, Any]:
    return {
        "timestamp": _serialize_timestamp(observation.timestamp),
        "joint_position_deg": list(observation.joint_position_deg),
        "joint_velocity_deg_s": None
        if observation.joint_velocity_deg_s is None
        else list(observation.joint_velocity_deg_s),
        "ee_position_m": list(observation.ee_position_m),
        "ee_quaternion_xyzw": list(observation.ee_quaternion_xyzw),
        "gripper_commanded_open_fraction": observation.gripper_commanded_open_fraction,
        "gripper_actual_open_fraction": observation.gripper_actual_open_fraction,
        "wrist_camera": None
        if observation.wrist_camera is None
        else _serialize_camera_frame(
            observation.wrist_camera,
            episode_dir=episode_dir,
            step_index=step_index,
            slot="wrist",
        ),
        "external_camera": None
        if observation.external_camera is None
        else _serialize_camera_frame(
            observation.external_camera,
            episode_dir=episode_dir,
            step_index=step_index,
            slot="external",
        ),
        "robot_state_age_ms": float(observation.robot_state_age_ms),
        "ee_reference_frame": str(observation.ee_reference_frame),
    }


def _deserialize_observation(payload: dict[str, Any], *, episode_dir: Path) -> RobotObservation:
    return RobotObservation(
        timestamp=_deserialize_timestamp(payload["timestamp"]),
        joint_position_deg=payload["joint_position_deg"],
        joint_velocity_deg_s=payload.get("joint_velocity_deg_s"),
        ee_position_m=payload["ee_position_m"],
        ee_quaternion_xyzw=payload["ee_quaternion_xyzw"],
        gripper_commanded_open_fraction=payload.get("gripper_commanded_open_fraction"),
        gripper_actual_open_fraction=payload.get("gripper_actual_open_fraction"),
        wrist_camera=(
            None
            if payload.get("wrist_camera") is None
            else _deserialize_camera_frame(payload["wrist_camera"], episode_dir=episode_dir)
        ),
        external_camera=(
            None
            if payload.get("external_camera") is None
            else _deserialize_camera_frame(payload["external_camera"], episode_dir=episode_dir)
        ),
        robot_state_age_ms=float(payload["robot_state_age_ms"]),
        ee_reference_frame=CoordinateFrame(payload.get("ee_reference_frame", CoordinateFrame.BASE)),
    )


def _serialize_camera_frame(
    frame: CameraFrame,
    *,
    episode_dir: Path,
    step_index: int,
    slot: str,
) -> dict[str, Any]:
    images_dir = episode_dir / IMAGES_DIRNAME
    images_dir.mkdir(parents=True, exist_ok=True)
    color_rel = Path(IMAGES_DIRNAME) / f"step_{step_index:06d}_{slot}_color.npy"
    np.save(episode_dir / color_rel, np.asarray(frame.color_rgb))
    depth_rel: Path | None = None
    if frame.depth_raw is not None:
        depth_rel = Path(IMAGES_DIRNAME) / f"step_{step_index:06d}_{slot}_depth.npy"
        np.save(episode_dir / depth_rel, np.asarray(frame.depth_raw))
    return {
        "timestamp": _serialize_timestamp(frame.timestamp),
        "color_rgb_path": color_rel.as_posix(),
        "depth_raw_path": None if depth_rel is None else depth_rel.as_posix(),
        "depth_scale_m_per_unit": frame.depth_scale_m_per_unit,
    }


def _deserialize_camera_frame(payload: dict[str, Any], *, episode_dir: Path) -> CameraFrame:
    color = np.load(episode_dir / payload["color_rgb_path"])
    depth = None
    if payload.get("depth_raw_path") is not None:
        depth = np.load(episode_dir / payload["depth_raw_path"])
    return CameraFrame(
        timestamp=_deserialize_timestamp(payload["timestamp"]),
        color_rgb=color,
        depth_raw=depth,
        depth_scale_m_per_unit=payload.get("depth_scale_m_per_unit"),
    )


def _serialize_human_action(action: HumanAction) -> dict[str, Any]:
    return {
        "timestamp": _serialize_timestamp(action.timestamp),
        "linear_velocity_m_s": list(action.linear_velocity_m_s),
        "angular_velocity_rad_s": list(action.angular_velocity_rad_s),
        "gripper_target_open_fraction": action.gripper_target_open_fraction,
        "deadman_active": bool(action.deadman_active),
        "input_age_ms": float(action.input_age_ms),
        "reference_frame": str(action.reference_frame),
    }


def _deserialize_human_action(payload: dict[str, Any]) -> HumanAction:
    return HumanAction(
        timestamp=_deserialize_timestamp(payload["timestamp"]),
        linear_velocity_m_s=payload["linear_velocity_m_s"],
        angular_velocity_rad_s=payload["angular_velocity_rad_s"],
        gripper_target_open_fraction=payload.get("gripper_target_open_fraction"),
        deadman_active=bool(payload["deadman_active"]),
        input_age_ms=float(payload["input_age_ms"]),
        reference_frame=CoordinateFrame(payload.get("reference_frame", CoordinateFrame.BASE)),
    )


def _serialize_assist_action(action: AssistAction) -> dict[str, Any]:
    return {
        "timestamp": _serialize_timestamp(action.timestamp),
        "linear_velocity_m_s": list(action.linear_velocity_m_s),
        "angular_velocity_rad_s": list(action.angular_velocity_rad_s),
        "gripper_target_open_fraction": action.gripper_target_open_fraction,
        "confidence": float(action.confidence),
        "inferred_target_id": action.inferred_target_id,
        "reference_frame": str(action.reference_frame),
    }


def _deserialize_assist_action(payload: dict[str, Any]) -> AssistAction:
    return AssistAction(
        timestamp=_deserialize_timestamp(payload["timestamp"]),
        linear_velocity_m_s=payload["linear_velocity_m_s"],
        angular_velocity_rad_s=payload["angular_velocity_rad_s"],
        gripper_target_open_fraction=payload.get("gripper_target_open_fraction"),
        confidence=float(payload["confidence"]),
        inferred_target_id=payload.get("inferred_target_id"),
        reference_frame=CoordinateFrame(payload.get("reference_frame", CoordinateFrame.BASE)),
    )


def _serialize_executed_action(action: ExecutedAction) -> dict[str, Any]:
    return {
        "timestamp": _serialize_timestamp(action.timestamp),
        "linear_velocity_m_s": list(action.linear_velocity_m_s),
        "angular_velocity_rad_s": list(action.angular_velocity_rad_s),
        "gripper_target_open_fraction": action.gripper_target_open_fraction,
        "joint_target_deg": None if action.joint_target_deg is None else list(action.joint_target_deg),
        "actual_dt_s": float(action.actual_dt_s),
        "authority": float(action.authority),
        "safety_intervened": bool(action.safety_intervened),
        "safety_reasons": list(action.safety_reasons),
        "reference_frame": str(action.reference_frame),
    }


def _deserialize_executed_action(payload: dict[str, Any]) -> ExecutedAction:
    return ExecutedAction(
        timestamp=_deserialize_timestamp(payload["timestamp"]),
        linear_velocity_m_s=payload["linear_velocity_m_s"],
        angular_velocity_rad_s=payload["angular_velocity_rad_s"],
        gripper_target_open_fraction=payload.get("gripper_target_open_fraction"),
        joint_target_deg=payload.get("joint_target_deg"),
        actual_dt_s=float(payload["actual_dt_s"]),
        authority=float(payload["authority"]),
        safety_intervened=bool(payload["safety_intervened"]),
        safety_reasons=tuple(payload.get("safety_reasons") or ()),
        reference_frame=CoordinateFrame(payload.get("reference_frame", CoordinateFrame.BASE)),
    )


def _serialize_timestamp(timestamp: SampleTimestamp) -> dict[str, Any]:
    return {
        "timestamp_utc": _datetime_to_iso(timestamp.timestamp_utc),
        "received_monotonic_ns": int(timestamp.received_monotonic_ns),
        "device_timestamp_ms": timestamp.device_timestamp_ms,
        "device_clock_domain": timestamp.device_clock_domain,
        "sequence_number": timestamp.sequence_number,
    }


def _deserialize_timestamp(payload: dict[str, Any]) -> SampleTimestamp:
    return SampleTimestamp(
        timestamp_utc=_datetime_from_iso(payload["timestamp_utc"]),
        received_monotonic_ns=int(payload["received_monotonic_ns"]),
        device_timestamp_ms=payload.get("device_timestamp_ms"),
        device_clock_domain=payload.get("device_clock_domain"),
        sequence_number=payload.get("sequence_number"),
    )


def _datetime_to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware UTC")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _datetime_from_iso(value: str) -> datetime:
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("datetime must be timezone-aware UTC")
    return parsed.astimezone(UTC)
