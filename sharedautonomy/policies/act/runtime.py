"""ACT policy runtime for cloud inference (LeRobot 0.6, lazy imports)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from sharedautonomy.policies.act.protocol import (
    ACTION_DIM,
    EXTERNAL_KEY,
    WRIST_KEY,
    InferObservation,
    InferResponse,
    observation_state_layout_from_dataset,
    require_state_matches_dim,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActRuntimeConfig:
    checkpoint_dir: Path
    dataset_repo_id: str
    dataset_root: Path
    device: str = "cuda"


class ActInferenceRuntime:
    """Load ACT once; run select_action with the same preprocessor path as offline checks."""

    def __init__(self, config: ActRuntimeConfig) -> None:
        self.config = config
        self._policy: Any | None = None
        self._preprocessor: Any | None = None
        self._postprocessor: Any | None = None
        self._device: Any | None = None
        self._chunk_size: int | None = None
        self._n_action_steps: int | None = None
        self._dataset: Any | None = None
        self._state_dim: int | None = None
        self._state_names: list[str] | None = None

    def load(self) -> None:
        import torch
        from lerobot.datasets.lerobot_dataset import LeRobotDataset
        from lerobot.policies.act.modeling_act import ACTPolicy
        from lerobot.policies.factory import make_pre_post_processors

        checkpoint = Path(self.config.checkpoint_dir)
        if not (checkpoint / "model.safetensors").is_file() and not (checkpoint / "config.json").is_file():
            raise FileNotFoundError(f"ACT checkpoint not found under {checkpoint}")

        device = torch.device(self.config.device)
        if self.config.device.startswith("cuda") and not torch.cuda.is_available():
            logger.warning("CUDA unavailable; falling back to CPU")
            device = torch.device("cpu")

        logger.info("Loading ACT checkpoint from %s", checkpoint)
        policy = ACTPolicy.from_pretrained(str(checkpoint))
        policy.to(device)
        policy.eval()

        logger.info(
            "Loading dataset stats from repo_id=%s root=%s",
            self.config.dataset_repo_id,
            self.config.dataset_root,
        )
        dataset = LeRobotDataset(self.config.dataset_repo_id, root=str(self.config.dataset_root))
        preprocessor, postprocessor = make_pre_post_processors(
            policy.config,
            dataset_stats=dataset.meta.stats,
        )

        self._policy = policy
        self._preprocessor = preprocessor
        self._postprocessor = postprocessor
        self._device = device
        self._dataset = dataset
        self._state_dim, self._state_names = observation_state_layout_from_dataset(dataset)
        self._chunk_size = getattr(policy.config, "chunk_size", None)
        self._n_action_steps = getattr(policy.config, "n_action_steps", None)
        policy.reset()
        logger.info(
            "ACT ready on %s (chunk_size=%s n_action_steps=%s state_dim=%s)",
            device,
            self._chunk_size,
            self._n_action_steps,
            self._state_dim,
        )

    @property
    def is_loaded(self) -> bool:
        return self._policy is not None

    @property
    def state_dim(self) -> int | None:
        return self._state_dim

    def describe(self) -> dict[str, Any]:
        dim = self._state_dim
        return {
            "loaded": self.is_loaded,
            "checkpoint": str(self.config.checkpoint_dir),
            "dataset_repo_id": self.config.dataset_repo_id,
            "dataset_root": str(self.config.dataset_root),
            "device": self.config.device,
            "chunk_size": self._chunk_size,
            "n_action_steps": self._n_action_steps,
            "state_dim": dim,
            "state_names": list(self._state_names) if self._state_names is not None else None,
        }

    def reset(self) -> None:
        if self._policy is None:
            raise RuntimeError("ActInferenceRuntime.load() must be called first")
        self._policy.reset()

    def infer(self, observation: InferObservation) -> InferResponse:
        import torch

        if self._policy is None or self._preprocessor is None or self._postprocessor is None:
            raise RuntimeError("ActInferenceRuntime.load() must be called first")

        if observation.reset:
            self._policy.reset()

        if self._state_dim is None:
            raise RuntimeError("ActInferenceRuntime.load() must be called first")
        state = require_state_matches_dim(
            observation.state,
            self._state_dim,
            dataset_root=str(self.config.dataset_root),
        )
        wrist = _hwc_uint8_to_chw_float(observation.wrist_rgb_hwc)
        external = _hwc_uint8_to_chw_float(observation.external_rgb_hwc)

        batch: dict[str, Any] = {
            "observation.state": torch.from_numpy(state).unsqueeze(0).to(self._device),
            f"observation.images.{WRIST_KEY}": torch.from_numpy(wrist).unsqueeze(0).to(self._device),
            f"observation.images.{EXTERNAL_KEY}": torch.from_numpy(external).unsqueeze(0).to(self._device),
            "task": [str(observation.task)],
        }

        with torch.no_grad():
            processed = self._preprocessor(batch)
            action = self._policy.select_action(processed)
            action = self._postprocessor(action)

        action_np = action.detach().cpu().numpy()
        if action_np.ndim == 2:
            action_np = action_np[0]
        action_np = np.asarray(action_np, dtype=np.float32).reshape(ACTION_DIM)
        return InferResponse(
            action=action_np,
            chunk_size=self._chunk_size,
            n_action_steps=self._n_action_steps,
        )

    def infer_dataset_frame(
        self,
        *,
        episode_index: int,
        frame_index: int,
        reset: bool = False,
        task_override: str | None = None,
    ) -> tuple[InferObservation, InferResponse]:
        """Smoke path: pull a frame from the server-side dataset, then infer."""
        if self._dataset is None:
            raise RuntimeError("ActInferenceRuntime.load() must be called first")

        global_index = resolve_dataset_frame_index(
            self._dataset,
            episode_index=episode_index,
            frame_index=frame_index,
        )
        sample = self._dataset[global_index]
        from sharedautonomy.policies.act.protocol import chw_float_to_hwc_uint8

        if self._state_dim is None:
            raise RuntimeError("ActInferenceRuntime.load() must be called first")
        task = task_override if task_override is not None else str(sample["task"])
        obs = InferObservation(
            state=require_state_matches_dim(
                sample["observation.state"],
                self._state_dim,
                dataset_root=str(self.config.dataset_root),
            ),
            wrist_rgb_hwc=chw_float_to_hwc_uint8(np.asarray(sample[f"observation.images.{WRIST_KEY}"])),
            external_rgb_hwc=chw_float_to_hwc_uint8(np.asarray(sample[f"observation.images.{EXTERNAL_KEY}"])),
            task=task,
            reset=reset,
        )
        return obs, self.infer(obs)


def _hwc_uint8_to_chw_float(image_hwc: np.ndarray) -> np.ndarray:
    image = np.asarray(image_hwc, dtype=np.uint8)
    if image.ndim != 3 or image.shape[-1] != 3:
        raise ValueError(f"expected HWC RGB uint8, got {image.shape}")
    chw = np.transpose(image, (2, 0, 1)).astype(np.float32) / 255.0
    return np.ascontiguousarray(chw)


def resolve_dataset_frame_index(dataset: Any, *, episode_index: int, frame_index: int) -> int:
    """Map (episode_index, frame_index within episode) -> LeRobot global index."""
    bounds = _episode_bounds_from_data_index(dataset, episode_index)
    if bounds is None:
        bounds = _episode_bounds_from_meta_episodes(dataset, episode_index)
    if bounds is None:
        raise ValueError(
            "Cannot resolve dataset frame index: dataset must expose "
            "episode_data_index['from'/'to'] or meta.episodes "
            "dataset_from_index/dataset_to_index"
        )
    start, end = bounds
    length = end - start
    if frame_index < 0 or frame_index >= length:
        raise ValueError(
            f"frame_index={frame_index} out of range for episode {episode_index} (len={length})"
        )
    return start + int(frame_index)


def _episode_bounds_from_data_index(dataset: Any, episode_index: int) -> tuple[int, int] | None:
    episode_data_index = getattr(dataset, "episode_data_index", None)
    if episode_data_index is None or "from" not in episode_data_index or "to" not in episode_data_index:
        return None
    starts = episode_data_index["from"]
    ends = episode_data_index["to"]
    if episode_index < 0 or episode_index >= len(starts):
        raise ValueError(f"episode_index={episode_index} out of range (n_episodes={len(starts)})")
    return int(starts[episode_index]), int(ends[episode_index])


def _episode_bounds_from_meta_episodes(dataset: Any, episode_index: int) -> tuple[int, int] | None:
    """LeRobot 0.6+ episode offsets from ``dataset.meta.episodes`` (O(1), no frame decode)."""
    meta = getattr(getattr(dataset, "meta", None), "episodes", None)
    if meta is None:
        return None
    try:
        from_indices = meta["dataset_from_index"]
        to_indices = meta["dataset_to_index"]
    except (KeyError, TypeError):
        return None

    if hasattr(meta, "columns") and "episode_index" in meta.columns:
        rows = meta.index[meta["episode_index"] == int(episode_index)].tolist()
        if not rows:
            raise ValueError(f"episode_index={episode_index} not found in dataset meta.episodes")
        row = rows[0]
        return int(from_indices[row]), int(to_indices[row])

    if episode_index < 0 or episode_index >= len(from_indices):
        raise ValueError(
            f"episode_index={episode_index} out of range (n_episodes={len(from_indices)})"
        )
    return int(from_indices[episode_index]), int(to_indices[episode_index])
