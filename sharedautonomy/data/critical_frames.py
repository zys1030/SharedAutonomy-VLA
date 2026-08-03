"""Critical-frame detection and index serialization for LeRobot datasets.

The index is built from the exported LeRobot ``action`` feature only.  A
grasp event is the first open-to-close transition in an episode, and the
resulting window describes valid *chunk start* candidates rather than
cropping or concatenating trajectory data.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

CRITICAL_FRAME_INDEX_SCHEMA_VERSION = 1
GRIPPER_ACTION_INDEX = 6


class CriticalFrameIndexError(ValueError):
    """Raised when a critical-frame index cannot be built or loaded."""


@dataclass(frozen=True, slots=True)
class CriticalFrameConfig:
    """Parameters used to detect and weight grasp windows."""

    pre_frames: int = 20
    post_frames: int = 10
    close_threshold: float = 0.5
    weight: float = 5.0

    def __post_init__(self) -> None:
        if int(self.pre_frames) < 0:
            raise ValueError("pre_frames must be >= 0")
        if int(self.post_frames) < 0:
            raise ValueError("post_frames must be >= 0")
        if not 0.0 < float(self.close_threshold) < 1.0:
            raise ValueError("close_threshold must be in (0, 1)")
        if not math.isfinite(float(self.weight)) or float(self.weight) < 1.0:
            raise ValueError("weight must be finite and >= 1")


@dataclass(frozen=True, slots=True)
class EpisodeFrameRange:
    """Absolute, half-open frame range for one LeRobot episode."""

    episode_index: int
    from_index: int
    to_index: int

    def __post_init__(self) -> None:
        if int(self.episode_index) < 0:
            raise ValueError("episode_index must be >= 0")
        if int(self.from_index) < 0:
            raise ValueError("from_index must be >= 0")
        if int(self.to_index) <= int(self.from_index):
            raise ValueError("to_index must be greater than from_index")


@dataclass(frozen=True, slots=True)
class CriticalFrameEpisode:
    """Detected grasp event and inclusive window for one episode."""

    episode_index: int
    from_index: int
    to_index: int
    close_frame_index: int | None
    window_start_index: int | None
    window_end_index: int | None

    @property
    def window_frame_count(self) -> int:
        if self.window_start_index is None or self.window_end_index is None:
            return 0
        return self.window_end_index - self.window_start_index + 1


@dataclass(frozen=True, slots=True)
class CriticalFrameIndex:
    """Serializable critical-frame metadata for a complete LeRobot dataset."""

    dataset_repo_id: str
    dataset_root: str
    num_frames: int
    episodes: tuple[CriticalFrameEpisode, ...]
    config: CriticalFrameConfig
    schema_version: int = CRITICAL_FRAME_INDEX_SCHEMA_VERSION

    @property
    def num_episodes(self) -> int:
        return len(self.episodes)

    @property
    def episodes_with_close(self) -> int:
        return sum(episode.close_frame_index is not None for episode in self.episodes)

    @property
    def num_window_frames(self) -> int:
        return sum(episode.window_frame_count for episode in self.episodes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "dataset": {
                "repo_id": self.dataset_repo_id,
                "root": self.dataset_root,
                "num_frames": self.num_frames,
                "num_episodes": self.num_episodes,
            },
            "config": asdict(self.config),
            "episodes": [asdict(episode) for episode in self.episodes],
            "summary": {
                "episodes_with_close": self.episodes_with_close,
                "episodes_without_close": self.num_episodes - self.episodes_with_close,
                "window_frames": self.num_window_frames,
            },
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> CriticalFrameIndex:
        schema_version = int(payload.get("schema_version", -1))
        if schema_version != CRITICAL_FRAME_INDEX_SCHEMA_VERSION:
            raise CriticalFrameIndexError(f"unsupported critical-frame index schema_version={schema_version}")

        dataset = _require_mapping(payload, "dataset")
        config_payload = _require_mapping(payload, "config")
        episodes_payload = payload.get("episodes")
        if not isinstance(episodes_payload, list):
            raise CriticalFrameIndexError("critical-frame index field 'episodes' must be a list")

        episodes = tuple(
            CriticalFrameEpisode(
                episode_index=int(item["episode_index"]),
                from_index=int(item["from_index"]),
                to_index=int(item["to_index"]),
                close_frame_index=_optional_int(item.get("close_frame_index")),
                window_start_index=_optional_int(item.get("window_start_index")),
                window_end_index=_optional_int(item.get("window_end_index")),
            )
            for item in episodes_payload
        )
        index = cls(
            dataset_repo_id=str(dataset["repo_id"]),
            dataset_root=str(dataset["root"]),
            num_frames=int(dataset["num_frames"]),
            episodes=episodes,
            config=CriticalFrameConfig(
                pre_frames=int(config_payload["pre_frames"]),
                post_frames=int(config_payload["post_frames"]),
                close_threshold=float(config_payload["close_threshold"]),
                weight=float(config_payload["weight"]),
            ),
            schema_version=schema_version,
        )
        _validate_index_episodes(index)
        return index


def find_close_transitions(
    gripper_open_fractions: Sequence[float],
    *,
    close_threshold: float = 0.5,
) -> tuple[int, ...]:
    """Return local indices where the gripper crosses from open to closed."""

    if not 0.0 < float(close_threshold) < 1.0:
        raise ValueError("close_threshold must be in (0, 1)")

    transitions: list[int] = []
    previous: float | None = None
    for local_index, raw_value in enumerate(gripper_open_fractions):
        value = float(raw_value)
        if not math.isfinite(value):
            raise CriticalFrameIndexError(
                f"gripper action at local frame {local_index} is not finite: {value!r}"
            )
        if previous is not None and previous >= close_threshold and value < close_threshold:
            transitions.append(local_index)
        previous = value
    return tuple(transitions)


def extract_gripper_open_fraction(action: Any) -> float:
    """Read the anchor-frame gripper value from a raw LeRobot action.

    A raw LeRobot row normally contains a seven-dimensional action vector.
    The helper also accepts an already-expanded action chunk and uses its
    first timestep, which is the chunk's dataset-frame anchor.
    """

    if isinstance(action, Mapping):
        if "action" not in action:
            raise CriticalFrameIndexError("raw action mapping does not contain an 'action' field")
        action = action["action"]

    if hasattr(action, "detach"):
        action = action.detach().cpu().numpy()
    values = np.asarray(action)
    if values.ndim == 0:
        raise CriticalFrameIndexError("raw action must have at least one dimension")
    if values.ndim == 1:
        if values.shape[0] <= GRIPPER_ACTION_INDEX:
            raise CriticalFrameIndexError(
                f"raw action has {values.shape[0]} values; expected index {GRIPPER_ACTION_INDEX}"
            )
        return float(values[GRIPPER_ACTION_INDEX])
    if values.shape[-1] <= GRIPPER_ACTION_INDEX:
        raise CriticalFrameIndexError(
            f"raw action last dimension has {values.shape[-1]} values; expected index {GRIPPER_ACTION_INDEX}"
        )
    return float(values.reshape(-1, values.shape[-1])[0, GRIPPER_ACTION_INDEX])


def build_critical_frame_index(
    *,
    dataset_repo_id: str,
    dataset_root: str | Path,
    num_frames: int,
    episode_ranges: Sequence[EpisodeFrameRange],
    action_reader: Callable[[int], Any],
    config: CriticalFrameConfig | None = None,
) -> CriticalFrameIndex:
    """Scan raw LeRobot actions and build one grasp-window index."""

    config = config or CriticalFrameConfig()
    ranges = tuple(episode_ranges)
    _validate_episode_ranges(ranges, num_frames)

    episodes: list[CriticalFrameEpisode] = []
    for episode in ranges:
        gripper_values = [
            extract_gripper_open_fraction(action_reader(frame_index))
            for frame_index in range(episode.from_index, episode.to_index)
        ]
        transitions = find_close_transitions(
            gripper_values,
            close_threshold=config.close_threshold,
        )
        if len(transitions) > 1:
            raise CriticalFrameIndexError(
                f"episode {episode.episode_index} has multiple open-to-close transitions: {transitions}"
            )

        if transitions:
            close_frame_index = episode.from_index + transitions[0]
            window_start_index = max(episode.from_index, close_frame_index - config.pre_frames)
            window_end_index = min(episode.to_index - 1, close_frame_index + config.post_frames)
        else:
            close_frame_index = None
            window_start_index = None
            window_end_index = None

        episodes.append(
            CriticalFrameEpisode(
                episode_index=episode.episode_index,
                from_index=episode.from_index,
                to_index=episode.to_index,
                close_frame_index=close_frame_index,
                window_start_index=window_start_index,
                window_end_index=window_end_index,
            )
        )

    return CriticalFrameIndex(
        dataset_repo_id=str(dataset_repo_id),
        dataset_root=str(dataset_root),
        num_frames=int(num_frames),
        episodes=tuple(episodes),
        config=config,
    )


def save_critical_frame_index(index: CriticalFrameIndex, path: str | Path) -> None:
    """Write a UTF-8 JSON critical-frame index."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(index.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_critical_frame_index(path: str | Path) -> CriticalFrameIndex:
    """Load and validate a UTF-8 JSON critical-frame index."""

    index_path = Path(path)
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CriticalFrameIndexError(f"failed to read critical-frame index {index_path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise CriticalFrameIndexError("critical-frame index root must be a JSON object")
    return CriticalFrameIndex.from_dict(payload)


def _require_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise CriticalFrameIndexError(f"critical-frame index field '{key}' must be an object")
    return value


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _validate_episode_ranges(ranges: Sequence[EpisodeFrameRange], num_frames: int) -> None:
    if not ranges:
        raise CriticalFrameIndexError("at least one episode is required")
    if int(num_frames) <= 0:
        raise CriticalFrameIndexError("num_frames must be positive")
    expected_start = 0
    for episode in ranges:
        if episode.from_index != expected_start:
            raise CriticalFrameIndexError(
                "episode ranges must cover the complete dataset contiguously; "
                f"expected from_index={expected_start}, got {episode.from_index}"
            )
        expected_start = episode.to_index
    if expected_start != int(num_frames):
        raise CriticalFrameIndexError(
            f"episode ranges end at {expected_start}, but dataset has {num_frames} frames"
        )


def _validate_index_episodes(index: CriticalFrameIndex) -> None:
    ranges = tuple(
        EpisodeFrameRange(
            episode_index=episode.episode_index,
            from_index=episode.from_index,
            to_index=episode.to_index,
        )
        for episode in index.episodes
    )
    _validate_episode_ranges(ranges, index.num_frames)
    for episode in index.episodes:
        has_close = episode.close_frame_index is not None
        has_window = episode.window_start_index is not None or episode.window_end_index is not None
        if has_close != has_window:
            raise CriticalFrameIndexError(
                f"episode {episode.episode_index} has an incomplete close/window record"
            )
        if has_close:
            assert episode.close_frame_index is not None
            assert episode.window_start_index is not None
            assert episode.window_end_index is not None
            if not episode.from_index <= episode.close_frame_index < episode.to_index:
                raise CriticalFrameIndexError(
                    f"episode {episode.episode_index} close frame is outside its range"
                )
            if not (
                episode.from_index
                <= episode.window_start_index
                <= episode.window_end_index
                < episode.to_index
            ):
                raise CriticalFrameIndexError(
                    f"episode {episode.episode_index} critical window is outside its range"
                )
