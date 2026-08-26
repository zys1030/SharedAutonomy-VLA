"""Native episode → LeRobot dataset export (ADR 0002).

Maps ``sharedautonomy.episode.v1`` artifacts to LeRobot v3.0 on-disk layout via
``LeRobotDataset.create`` / ``add_frame`` / ``save_episode`` / ``finalize``.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from lerobot.utils.constants import ACTION, OBS_STR
from lerobot.utils.feature_utils import combine_feature_dicts, hw_to_dataset_features

from sharedautonomy.data.recorder import (
    EPISODE_FORMAT,
    METADATA_FILENAME,
    STEPS_FILENAME,
)
from sharedautonomy.robot.rm65 import EE_KEYS, GRIPPER_KEY, JOINT_KEYS

logger = logging.getLogger(__name__)

WRIST_CAMERA_SLOT = "wrist"
EXTERNAL_CAMERA_SLOT = "external"
DEFAULT_IMAGE_SHAPE = (480, 640, 3)
MANIFEST_FILENAME = "export_manifest.json"
# Proprio state: joints + gripper + flange height + motion/phase history.
# Action stays joints + gripper.
EE_Z_KEY = EE_KEYS[2]  # "ee.z"
D_EE_Z_KEY = "ee.dz"
TIME_SINCE_CLOSE_KEY = "gripper.time_since_close"
STATE_KEYS = (*JOINT_KEYS, GRIPPER_KEY, EE_Z_KEY, D_EE_Z_KEY, TIME_SINCE_CLOSE_KEY)
ACTION_KEYS = (*JOINT_KEYS, GRIPPER_KEY)
GRIPPER_CLOSE_THRESHOLD = 0.5
TIME_SINCE_CLOSE_SATURATION_STEPS = 20.0

DIAG_FEATURE_SPECS: dict[str, dict[str, Any]] = {
    "diag.deadman_active": {"dtype": "float32", "shape": (1,)},
    "diag.safety_intervened": {"dtype": "float32", "shape": (1,)},
    "diag.actual_dt_s": {"dtype": "float32", "shape": (1,)},
    "diag.wall_time_s": {"dtype": "float64", "shape": (1,)},
}


class LeRobotExportError(RuntimeError):
    """Raised when native episode data cannot be exported to LeRobot."""


@dataclass(frozen=True, slots=True)
class NativeEpisodeEnvelope:
    """Parsed ``metadata.json`` envelope for one native episode directory."""

    episode_dir: Path
    format: str
    schema_version: str
    status: str
    step_count: int
    metadata: dict[str, Any]

    @property
    def task_text(self) -> str:
        return str(self.metadata["task_text"])

    @property
    def control_rate_hz(self) -> float:
        return float(self.metadata["control_rate_hz"])

    @property
    def episode_id(self) -> str:
        return str(self.metadata["episode_id"])

    @property
    def run_id(self) -> str:
        return str(self.metadata["run_id"])


def build_lerobot_features(
    *,
    image_shape: tuple[int, int, int] = DEFAULT_IMAGE_SHAPE,
    use_videos: bool = True,
    include_diag: bool = True,
) -> dict[str, dict[str, Any]]:
    """Build the LeRobot ``features`` dict for ``LeRobotDataset.create``."""
    if len(image_shape) != 3 or image_shape[2] != 3:
        raise ValueError(f"image_shape must be (height, width, 3), got {image_shape}")

    state_hw = {key: float for key in STATE_KEYS}
    action_hw = {key: float for key in ACTION_KEYS}
    camera_hw = {
        WRIST_CAMERA_SLOT: image_shape,
        EXTERNAL_CAMERA_SLOT: image_shape,
    }

    features = combine_feature_dicts(
        hw_to_dataset_features({**state_hw, **camera_hw}, OBS_STR, use_video=use_videos),
        hw_to_dataset_features(action_hw, ACTION, use_video=False),
    )
    if include_diag:
        features = {**features, **DIAG_FEATURE_SPECS}
    return features


def load_native_episode_envelope(episode_dir: str | Path) -> NativeEpisodeEnvelope:
    """Load and validate the native episode metadata envelope."""
    root = Path(episode_dir)
    meta_path = root / METADATA_FILENAME
    if not meta_path.is_file():
        raise LeRobotExportError(f"missing {METADATA_FILENAME} in {root}")
    try:
        envelope = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LeRobotExportError(f"invalid {METADATA_FILENAME}: {exc}") from exc

    if envelope.get("format") != EPISODE_FORMAT:
        raise LeRobotExportError(
            f"unsupported episode format {envelope.get('format')!r}; expected {EPISODE_FORMAT!r}"
        )
    metadata = envelope.get("metadata")
    if not isinstance(metadata, dict):
        raise LeRobotExportError(f"missing metadata object in {meta_path}")

    return NativeEpisodeEnvelope(
        episode_dir=root,
        format=str(envelope["format"]),
        schema_version=str(envelope.get("schema_version", "unknown")),
        status=str(envelope.get("status", "unknown")),
        step_count=int(envelope.get("step_count", 0)),
        metadata=metadata,
    )


def validate_episode_for_export(
    envelope: NativeEpisodeEnvelope,
    *,
    allow_aborted: bool = False,
) -> None:
    """Ensure episode metadata satisfies export preconditions."""
    if envelope.status != "completed":
        if not (allow_aborted and envelope.status == "aborted"):
            raise LeRobotExportError(
                f"episode {envelope.episode_id} status={envelope.status!r}; "
                "expected 'completed' (pass allow_aborted=True to override)"
            )
    success = envelope.metadata.get("success")
    if success is not True and not allow_aborted:
        raise LeRobotExportError(f"episode {envelope.episode_id} success={success!r}; expected True")
    steps_path = envelope.episode_dir / STEPS_FILENAME
    if not steps_path.is_file():
        raise LeRobotExportError(f"missing {STEPS_FILENAME} in {envelope.episode_dir}")
    if float(envelope.control_rate_hz) <= 0.0:
        raise LeRobotExportError("control_rate_hz must be positive")


def infer_image_shape(episode_dir: str | Path) -> tuple[int, int, int]:
    """Read the first wrist color frame to determine RGB image shape."""
    root = Path(episode_dir)
    steps_path = root / STEPS_FILENAME
    with steps_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            wrist = (payload.get("observation") or {}).get("wrist_camera")
            if wrist is None:
                continue
            color_path = root / wrist["color_rgb_path"]
            color = np.load(color_path)
            if color.ndim != 3 or color.shape[2] != 3:
                raise LeRobotExportError(
                    f"expected wrist color shape (H, W, 3), got {color.shape} in {color_path}"
                )
            return (int(color.shape[0]), int(color.shape[1]), int(color.shape[2]))
    raise LeRobotExportError(f"no wrist camera frame found in {steps_path}")


def _parse_utc_timestamp(value: str) -> datetime:
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise LeRobotExportError(f"timestamp must be timezone-aware UTC, got {value!r}")
    return parsed.astimezone(UTC)


def _scalar_diag(value: bool | float, *, dtype: np.dtype) -> np.ndarray:
    return np.array([value], dtype=dtype)


def _require_joint_target_deg(executed: dict[str, Any], *, step_index: int) -> list[float]:
    joints = executed.get("joint_target_deg")
    if joints is None:
        raise LeRobotExportError(
            f"step {step_index}: executed_action.joint_target_deg is null; refusing to export"
        )
    if len(joints) != len(JOINT_KEYS):
        raise LeRobotExportError(
            f"step {step_index}: joint_target_deg length {len(joints)} != {len(JOINT_KEYS)}"
        )
    return [float(value) for value in joints]


def _require_gripper_commanded(observation: dict[str, Any], *, step_index: int) -> float:
    value = observation.get("gripper_commanded_open_fraction")
    if value is None:
        raise LeRobotExportError(f"step {step_index}: observation.gripper_commanded_open_fraction is null")
    return float(value)


def _require_gripper_action(executed: dict[str, Any], *, step_index: int) -> float:
    value = executed.get("gripper_target_open_fraction")
    if value is None:
        raise LeRobotExportError(f"step {step_index}: executed_action.gripper_target_open_fraction is null")
    return float(value)


def _require_ee_z_m(observation: dict[str, Any], *, step_index: int) -> float:
    ee = observation.get("ee_position_m")
    if ee is None or len(ee) != 3:
        raise LeRobotExportError(f"step {step_index}: invalid ee_position_m")
    value = float(ee[2])
    if not np.isfinite(value):
        raise LeRobotExportError(f"step {step_index}: ee_position_m[2] is not finite")
    return value


def saturate_time_since_close(steps: int | float) -> float:
    """Map closed-streak steps to [0, 1] with a 2.0 s (20-step) saturation."""
    return float(min(max(float(steps), 0.0), TIME_SINCE_CLOSE_SATURATION_STEPS) / TIME_SINCE_CLOSE_SATURATION_STEPS)


class ProprioHistoryTracker:
    """Per-episode tracker for Δee_z and saturated time-since-close."""

    def __init__(self) -> None:
        self._prev_ee_z_m: float | None = None
        self._prev_gripper: float | None = None
        self._steps_since_close: int = 0

    def reset(self) -> None:
        self._prev_ee_z_m = None
        self._prev_gripper = None
        self._steps_since_close = 0

    def update(self, *, ee_z_m: float, gripper_open_fraction: float) -> tuple[float, float]:
        d_ee_z = 0.0 if self._prev_ee_z_m is None else float(ee_z_m) - self._prev_ee_z_m
        gripper = float(gripper_open_fraction)
        closed = gripper < GRIPPER_CLOSE_THRESHOLD
        if self._prev_gripper is None:
            self._steps_since_close = 0
        elif closed:
            crossed_close = self._prev_gripper >= GRIPPER_CLOSE_THRESHOLD
            self._steps_since_close = 0 if crossed_close else self._steps_since_close + 1
        else:
            self._steps_since_close = 0
        self._prev_ee_z_m = float(ee_z_m)
        self._prev_gripper = gripper
        return d_ee_z, saturate_time_since_close(self._steps_since_close)


def _stack_state_action_vectors(
    observation: dict[str, Any],
    executed: dict[str, Any],
    *,
    step_index: int,
    history: ProprioHistoryTracker,
) -> tuple[np.ndarray, np.ndarray]:
    joints_obs = observation.get("joint_position_deg")
    if joints_obs is None or len(joints_obs) != len(JOINT_KEYS):
        raise LeRobotExportError(f"step {step_index}: invalid joint_position_deg")
    joint_target = _require_joint_target_deg(executed, step_index=step_index)
    gripper_obs = _require_gripper_commanded(observation, step_index=step_index)
    gripper_act = _require_gripper_action(executed, step_index=step_index)
    ee_z_m = _require_ee_z_m(observation, step_index=step_index)
    d_ee_z, time_since_close = history.update(ee_z_m=ee_z_m, gripper_open_fraction=gripper_obs)
    state = np.array(
        [*joints_obs, gripper_obs, ee_z_m, d_ee_z, time_since_close],
        dtype=np.float32,
    )
    action = np.array([*joint_target, gripper_act], dtype=np.float32)
    return state, action


def _load_camera_rgb(
    episode_dir: Path,
    camera_payload: dict[str, Any] | None,
    *,
    slot: str,
    step_index: int,
    expected_shape: tuple[int, int, int],
) -> np.ndarray:
    if camera_payload is None:
        raise LeRobotExportError(f"step {step_index}: missing {slot}_camera")
    rel_path = camera_payload.get("color_rgb_path")
    if not rel_path:
        raise LeRobotExportError(f"step {step_index}: {slot}_camera missing color_rgb_path")
    color = np.load(episode_dir / rel_path)
    if color.dtype != np.uint8:
        raise LeRobotExportError(f"step {step_index}: {slot} color dtype must be uint8, got {color.dtype}")
    if tuple(color.shape) != expected_shape:
        raise LeRobotExportError(
            f"step {step_index}: {slot} color shape {color.shape} != expected {expected_shape}"
        )
    return np.ascontiguousarray(color)


def build_lerobot_frame(
    step_payload: dict[str, Any],
    *,
    images: dict[str, np.ndarray],
    task_text: str,
    episode_started_at_utc: datetime,
    include_diag: bool = True,
    history: ProprioHistoryTracker | None = None,
) -> dict[str, Any]:
    """Build one LeRobot ``add_frame`` dictionary from a native step payload."""
    step_index = int(step_payload["step_index"])
    observation = step_payload.get("observation") or {}
    human = step_payload.get("human_action") or {}
    executed = step_payload.get("executed_action") or {}

    state, action = _stack_state_action_vectors(
        observation,
        executed,
        step_index=step_index,
        history=history if history is not None else ProprioHistoryTracker(),
    )

    frame: dict[str, Any] = {
        "observation.state": state,
        "action": action,
        "observation.images.wrist": images[WRIST_CAMERA_SLOT],
        "observation.images.external": images[EXTERNAL_CAMERA_SLOT],
        "task": task_text,
    }

    if include_diag:
        timestamp_payload = observation.get("timestamp") or {}
        wall_time_s = 0.0
        if timestamp_payload.get("timestamp_utc") is not None:
            step_time = _parse_utc_timestamp(str(timestamp_payload["timestamp_utc"]))
            wall_time_s = (step_time - episode_started_at_utc).total_seconds()
        frame["diag.deadman_active"] = _scalar_diag(
            bool(human.get("deadman_active")), dtype=np.dtype(np.float32)
        )
        frame["diag.safety_intervened"] = _scalar_diag(
            bool(executed.get("safety_intervened")), dtype=np.dtype(np.float32)
        )
        frame["diag.actual_dt_s"] = _scalar_diag(
            float(executed.get("actual_dt_s", 0.0)), dtype=np.dtype(np.float32)
        )
        frame["diag.wall_time_s"] = _scalar_diag(wall_time_s, dtype=np.dtype(np.float64))

    return frame


def iter_native_frames(
    episode_dir: str | Path,
    *,
    image_shape: tuple[int, int, int] | None = None,
    include_diag: bool = True,
    allow_aborted: bool = False,
) -> Iterator[dict[str, Any]]:
    """Stream native episode steps as LeRobot frame dicts without loading all images at once."""
    envelope = load_native_episode_envelope(episode_dir)
    validate_episode_for_export(envelope, allow_aborted=allow_aborted)
    shape = image_shape or infer_image_shape(envelope.episode_dir)
    started_at = _parse_utc_timestamp(str(envelope.metadata["started_at_utc"]))
    task_text = envelope.task_text
    history = ProprioHistoryTracker()

    steps_path = envelope.episode_dir / STEPS_FILENAME
    with steps_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise LeRobotExportError(
                    f"failed to parse {STEPS_FILENAME} line {line_number}: {exc}"
                ) from exc

            step_index = int(payload.get("step_index", -1))
            observation = payload.get("observation") or {}
            images = {
                WRIST_CAMERA_SLOT: _load_camera_rgb(
                    envelope.episode_dir,
                    observation.get("wrist_camera"),
                    slot=WRIST_CAMERA_SLOT,
                    step_index=step_index,
                    expected_shape=shape,
                ),
                EXTERNAL_CAMERA_SLOT: _load_camera_rgb(
                    envelope.episode_dir,
                    observation.get("external_camera"),
                    slot=EXTERNAL_CAMERA_SLOT,
                    step_index=step_index,
                    expected_shape=shape,
                ),
            }
            yield build_lerobot_frame(
                payload,
                images=images,
                task_text=task_text,
                episode_started_at_utc=started_at,
                include_diag=include_diag,
                history=history,
            )


def _resolve_git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    commit = result.stdout.strip()
    return commit or None


def export_lerobot_dataset(
    episode_dirs: list[str | Path],
    *,
    out_root: str | Path,
    repo_id: str = "local/shape_pick_place_v1",
    robot_type: str = "rm65",
    use_videos: bool = True,
    include_diag: bool = True,
    allow_aborted: bool = False,
    parallel_encoding: bool = True,
    resume: bool = False,
) -> Path:
    """Export one or more native episode directories to a LeRobot dataset on disk."""
    if not episode_dirs:
        raise LeRobotExportError("episode_dirs must not be empty")

    import lerobot
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    roots = [Path(path) for path in episode_dirs]
    out_path = Path(out_root)

    envelopes = [load_native_episode_envelope(path) for path in roots]
    for envelope in envelopes:
        validate_episode_for_export(envelope, allow_aborted=allow_aborted)

    image_shape = infer_image_shape(envelopes[0].episode_dir)
    for envelope in envelopes[1:]:
        other_shape = infer_image_shape(envelope.episode_dir)
        if other_shape != image_shape:
            raise LeRobotExportError(
                f"image shape mismatch: {envelopes[0].episode_dir} {image_shape} vs "
                f"{envelope.episode_dir} {other_shape}"
            )

    fps = int(round(envelopes[0].control_rate_hz))
    for envelope in envelopes[1:]:
        other_fps = int(round(envelope.control_rate_hz))
        if other_fps != fps:
            raise LeRobotExportError(
                f"control_rate_hz mismatch: {envelopes[0].run_id} {fps} vs {envelope.run_id} {other_fps}"
            )

    features = build_lerobot_features(
        image_shape=image_shape,
        use_videos=use_videos,
        include_diag=include_diag,
    )

    if resume:
        if not out_path.is_dir():
            raise LeRobotExportError(f"resume requires existing dataset root {out_path}")
        dataset = LeRobotDataset.resume(
            repo_id=repo_id,
            root=out_path,
            batch_encoding_size=1,
        )
        start_episode_index = dataset.meta.total_episodes
    else:
        if out_path.exists():
            raise LeRobotExportError(
                f"refusing to overwrite existing output root {out_path}; "
                "choose a new directory or pass resume=True"
            )
        dataset = LeRobotDataset.create(
            repo_id=repo_id,
            fps=fps,
            features=features,
            root=out_path,
            robot_type=robot_type,
            use_videos=use_videos,
        )
        start_episode_index = 0

    manifest_episodes: list[dict[str, Any]] = []
    try:
        for offset, (episode_dir, envelope) in enumerate(zip(roots, envelopes, strict=True)):
            step_count = 0
            for frame in iter_native_frames(
                episode_dir,
                image_shape=image_shape,
                include_diag=include_diag,
                allow_aborted=allow_aborted,
            ):
                dataset.add_frame(frame)
                step_count += 1
            if step_count == 0:
                raise LeRobotExportError(f"episode {envelope.episode_id} contains no frames")
            if step_count != envelope.step_count:
                logger.warning(
                    "Episode %s metadata step_count=%s but exported %s frames",
                    envelope.episode_id,
                    envelope.step_count,
                    step_count,
                )
            dataset.save_episode(parallel_encoding=parallel_encoding)
            manifest_episodes.append(
                {
                    "native_episode_dir": episode_dir.as_posix(),
                    "episode_index": start_episode_index + offset,
                    "episode_id": envelope.episode_id,
                    "run_id": envelope.run_id,
                    "task_text": envelope.task_text,
                    "step_count": step_count,
                    "metadata_step_count": envelope.step_count,
                }
            )
            logger.info(
                "Exported episode %s (%s) as LeRobot episode_index=%s steps=%s",
                envelope.episode_id,
                envelope.run_id,
                start_episode_index + offset,
                step_count,
            )
    except Exception:
        logger.exception("Export failed; calling finalize() to flush partial writer state")
        dataset.finalize()
        raise

    dataset.finalize()

    manifest = {
        "repo_id": repo_id,
        "root": out_path.as_posix(),
        "lerobot_version": getattr(lerobot, "__version__", "unknown"),
        "git_commit": _resolve_git_commit(),
        "fps": fps,
        "image_shape": list(image_shape),
        "use_videos": use_videos,
        "include_diag": include_diag,
        "episodes": manifest_episodes,
    }
    manifest_path = out_path / MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    logger.info("Wrote export manifest to %s (%s episodes)", manifest_path, len(manifest_episodes))
    return out_path


def export_single_episode(
    episode_dir: str | Path,
    *,
    out_root: str | Path,
    **kwargs: Any,
) -> Path:
    """Convenience wrapper around :func:`export_lerobot_dataset` for one episode."""
    return export_lerobot_dataset([episode_dir], out_root=out_root, **kwargs)
