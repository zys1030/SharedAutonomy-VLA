"""Bridge manual Cartesian control steps to ``EpisodeRecorder``."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sharedautonomy.data.recorder import EpisodeRecorder, EpisodeRecorderError, EpisodeStep
from sharedautonomy.data.schema import CollectionMode, EpisodeMetadata

if TYPE_CHECKING:
    from sharedautonomy.control.manual import CartesianControlStep


def build_manual_episode_metadata(
    *,
    episode_id: str,
    run_id: str,
    task_id: str,
    task_text: str,
    control_rate_hz: float,
    effective_config_path: str,
    started_at_utc: datetime | None = None,
    source_object: str | None = None,
    destination: str | None = None,
    git_commit: str | None = None,
    collection_mode: CollectionMode = CollectionMode.MANUAL,
) -> EpisodeMetadata:
    """Create open-ended episode metadata for a teleop recording session."""
    return EpisodeMetadata(
        episode_id=episode_id,
        run_id=run_id,
        task_id=task_id,
        task_text=task_text,
        source_object=source_object,
        destination=destination,
        collection_mode=collection_mode,
        started_at_utc=started_at_utc or datetime.now(tz=UTC),
        ended_at_utc=None,
        success=None,
        failure_reason=None,
        control_rate_hz=float(control_rate_hz),
        effective_config_path=effective_config_path,
        git_commit=git_commit,
    )


def record_cartesian_control_step(
    recorder: EpisodeRecorder,
    step: CartesianControlStep,
) -> EpisodeStep:
    """Append one runner step using its synced observation bundle."""
    if step.synced_observation is None:
        raise EpisodeRecorderError(
            "Cannot record CartesianControlStep without synced_observation; "
            "enable cameras and an observation synchronizer"
        )
    synced = step.synced_observation
    return recorder.record_step(
        observation=synced.observation,
        human_action=step.human_action,
        executed_action=step.executed_action,
        assist_action=step.assist_action,
        sync_warnings=synced.warnings,
        step_index=step.step_index,
    )


def write_effective_config_yaml(path: Path, payload: dict[str, Any]) -> Path:
    """Persist the merged teleop effective config next to the episode directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to write effective_config.yaml") from exc
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return path
