"""SmolVLA inference runtime for local and HTTP deployment."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np

from sharedautonomy.policies.act.protocol import (
    ACTION_DIM,
    EXTERNAL_KEY,
    WRIST_KEY,
    InferObservation,
    InferResponse,
    chw_float_to_hwc_uint8,
    observation_state_layout_from_dataset,
    require_state_matches_dim,
)

logger = logging.getLogger(__name__)

# Default seed for flow-matching noise at each action-chunk replan (base + chunk index).
DEFAULT_INFERENCE_NOISE_SEED = 42

CheckpointKind = Literal["full", "lora"]


def inference_noise_seed_for_chunk(base_seed: int, chunk_index: int) -> int:
    """Derive a deterministic seed for one action-chunk generation."""
    return int(base_seed) + int(chunk_index)


def make_smolvla_inference_noise(
    *,
    torch: Any,
    device: Any,
    chunk_size: int,
    max_action_dim: int,
    seed: int,
) -> Any:
    """Sample the initial flow-matching noise tensor passed to ``select_action``."""
    generator = torch.Generator(device=device)
    generator.manual_seed(int(seed))
    return torch.randn(
        1,
        int(chunk_size),
        int(max_action_dim),
        generator=generator,
        device=device,
        dtype=torch.float32,
    )


def smolvla_action_queue_is_empty(policy: Any) -> bool:
    """Return True when the policy will sample a new action chunk on the next step."""
    try:
        from lerobot.utils.constants import ACTION
    except ImportError:
        return True

    queues = getattr(policy, "_queues", None)
    if not isinstance(queues, dict):
        return True
    queue = queues.get(ACTION)
    if queue is None:
        return True
    return len(queue) == 0


@dataclass(frozen=True)
class SmolVLARuntimeConfig:
    """Configuration for one SmolVLA policy instance."""

    checkpoint_dir: Path
    dataset_repo_id: str
    dataset_root: Path
    device: str = "cuda"
    base_model: str | Path | None = None
    inference_noise_seed: int | None = DEFAULT_INFERENCE_NOISE_SEED


def resolve_checkpoint_dir(path: str | Path) -> Path:
    """Resolve a LeRobot checkpoint or its enclosing ``pretrained_model`` directory."""
    checkpoint = Path(path).expanduser()
    if not checkpoint.is_dir():
        raise FileNotFoundError(f"SmolVLA checkpoint directory does not exist: {checkpoint}")

    nested = checkpoint / "pretrained_model"
    if nested.is_dir() and not _has_policy_artifacts(checkpoint):
        return nested
    return checkpoint


def detect_checkpoint_kind(path: str | Path) -> CheckpointKind:
    """Detect a full policy checkpoint versus a PEFT adapter checkpoint."""
    checkpoint = resolve_checkpoint_dir(path)
    has_full_config = (checkpoint / "config.json").is_file()
    has_full_weights = (
        any(checkpoint.glob("model*.safetensors")) or (checkpoint / "pytorch_model.bin").is_file()
    )
    has_adapter_config = (checkpoint / "adapter_config.json").is_file()
    has_adapter_weights = (checkpoint / "adapter_model.safetensors").is_file() or (
        checkpoint / "adapter_model.bin"
    ).is_file()

    if has_full_config and has_full_weights:
        return "full"
    if has_adapter_config and has_adapter_weights:
        return "lora"
    raise FileNotFoundError(
        "SmolVLA checkpoint must contain either config.json + model*.safetensors "
        "or adapter_config.json + adapter_model.safetensors: "
        f"{checkpoint}"
    )


def _has_policy_artifacts(path: Path) -> bool:
    return any(
        (
            (path / "config.json").is_file(),
            (path / "adapter_config.json").is_file(),
            any(path.glob("model*.safetensors")),
            (path / "adapter_model.safetensors").is_file(),
        )
    )


class SmolVLAInferenceRuntime:
    """Load SmolVLA once and expose one-action and action-chunk inference."""

    def __init__(self, config: SmolVLARuntimeConfig) -> None:
        self.config = config
        self._checkpoint_dir: Path | None = None
        self._checkpoint_kind: CheckpointKind | None = None
        self._policy: Any | None = None
        self._base_policy: Any | None = None
        self._policy_config: Any | None = None
        self._preprocessor: Any | None = None
        self._postprocessor: Any | None = None
        self._device: Any | None = None
        self._dataset: Any | None = None
        self._base_model: str | None = None
        self._chunk_size: int | None = None
        self._n_action_steps: int | None = None
        self._inference_chunk_index: int = 0
        self._state_dim: int | None = None
        self._state_names: list[str] | None = None

    def load(self) -> None:
        """Load model, PEFT adapter when needed, dataset stats, and processors."""
        try:
            import torch
            from lerobot.configs.policies import PreTrainedConfig
            from lerobot.datasets.lerobot_dataset import LeRobotDataset
            from lerobot.policies.factory import make_pre_post_processors
            from lerobot.policies.smolvla.modeling_smolvla import SmolVLAPolicy
        except ImportError as exc:
            raise RuntimeError(
                "SmolVLA inference requires LeRobot's SmolVLA dependencies "
                "(including transformers and PEFT for adapter checkpoints)."
            ) from exc

        checkpoint = resolve_checkpoint_dir(self.config.checkpoint_dir)
        kind = detect_checkpoint_kind(checkpoint)
        device = self._resolve_device(torch)

        logger.info("Loading SmolVLA %s checkpoint from %s", kind, checkpoint)
        base_policy: Any
        policy: Any
        base_model: str | None = None
        if kind == "full":
            base_policy = SmolVLAPolicy.from_pretrained(str(checkpoint))
            policy = base_policy
        else:
            try:
                from peft import PeftConfig, PeftModel
            except ImportError as exc:
                raise RuntimeError("Loading a SmolVLA LoRA checkpoint requires the PEFT package") from exc

            peft_config = PeftConfig.from_pretrained(str(checkpoint))
            base_model = self._resolve_base_model(
                checkpoint,
                configured_base=self.config.base_model,
                adapter_base=peft_config.base_model_name_or_path,
            )
            if base_model is None:
                raise ValueError(
                    "LoRA checkpoint does not declare a base model; pass --base-model explicitly"
                )
            logger.info("Loading SmolVLA LoRA base model from %s", base_model)
            adapter_policy_config = (
                PreTrainedConfig.from_pretrained(str(checkpoint))
                if (checkpoint / "config.json").is_file()
                else None
            )
            base_policy = SmolVLAPolicy.from_pretrained(
                base_model,
                config=adapter_policy_config,
            )
            policy = PeftModel.from_pretrained(
                base_policy,
                str(checkpoint),
                is_trainable=False,
            )

        policy_config = base_policy.config
        policy_config.device = str(device)
        policy.to(device)
        policy.eval()

        logger.info(
            "Loading dataset stats from repo_id=%s root=%s",
            self.config.dataset_repo_id,
            self.config.dataset_root,
        )
        dataset = LeRobotDataset(
            self.config.dataset_repo_id,
            root=str(self.config.dataset_root),
        )
        preprocessor, postprocessor = make_pre_post_processors(
            policy_config,
            dataset_stats=dataset.meta.stats,
        )

        action_feature = getattr(policy_config, "action_feature", None)
        action_shape = getattr(action_feature, "shape", None)
        if action_shape is None or int(np.prod(action_shape)) != ACTION_DIM:
            raise ValueError(
                "SmolVLA action feature must have 7 values for the RM-65 wire protocol; "
                f"got shape={action_shape}"
            )

        self._checkpoint_dir = checkpoint
        self._checkpoint_kind = kind
        self._policy = policy
        self._base_policy = base_policy
        self._policy_config = policy_config
        self._preprocessor = preprocessor
        self._postprocessor = postprocessor
        self._device = device
        self._dataset = dataset
        self._base_model = base_model
        self._chunk_size = _optional_int(getattr(policy_config, "chunk_size", None))
        self._n_action_steps = _optional_int(getattr(policy_config, "n_action_steps", None))
        self._state_dim, self._state_names = observation_state_layout_from_dataset(dataset)
        self._inference_chunk_index = 0
        self.reset()
        logger.info(
            "SmolVLA ready on %s (kind=%s chunk_size=%s n_action_steps=%s "
            "state_dim=%s inference_noise_seed=%s)",
            device,
            kind,
            self._chunk_size,
            self._n_action_steps,
            self._state_dim,
            self.config.inference_noise_seed,
        )

    @property
    def is_loaded(self) -> bool:
        return self._policy is not None

    @property
    def checkpoint_dir(self) -> Path | None:
        return self._checkpoint_dir

    def describe(self) -> dict[str, Any]:
        """Return a JSON-friendly health summary."""
        return {
            "loaded": self.is_loaded,
            "checkpoint": str(self._checkpoint_dir) if self._checkpoint_dir else None,
            "checkpoint_kind": self._checkpoint_kind,
            "base_model": self._base_model,
            "dataset_repo_id": self.config.dataset_repo_id,
            "dataset_root": str(self.config.dataset_root),
            "device": str(self._device) if self._device is not None else None,
            "chunk_size": self._chunk_size,
            "n_action_steps": self._n_action_steps,
            "state_dim": self._state_dim,
            "state_names": list(self._state_names) if self._state_names is not None else None,
            "inference_noise_seed": self.config.inference_noise_seed,
        }

    def reset(self) -> None:
        """Reset the policy's internal action queue."""
        if self._policy is None:
            raise RuntimeError("SmolVLAInferenceRuntime.load() must be called first")
        reset = getattr(self._policy, "reset", None)
        if not callable(reset) and self._base_policy is not None:
            reset = getattr(self._base_policy, "reset", None)
        if not callable(reset):
            raise RuntimeError("Loaded SmolVLA policy does not expose reset()")
        reset()
        self._inference_chunk_index = 0

    def close(self) -> None:
        """Release policy and dataset references after a checkpoint smoke."""
        self._policy = None
        self._base_policy = None
        self._policy_config = None
        self._preprocessor = None
        self._postprocessor = None
        self._dataset = None
        try:
            import torch

            if self._device is not None and str(self._device).startswith("cuda"):
                torch.cuda.empty_cache()
        except ImportError:
            pass
        self._device = None

    def infer(self, observation: InferObservation) -> InferResponse:
        """Run ``select_action`` and return one unnormalized RM-65 action."""
        import torch

        if (
            self._policy is None
            or self._preprocessor is None
            or self._postprocessor is None
            or self._device is None
        ):
            raise RuntimeError("SmolVLAInferenceRuntime.load() must be called first")

        if observation.reset:
            self.reset()

        batch = self._observation_to_batch(observation, torch)
        with torch.inference_mode():
            processed = self._preprocessor(batch)
            action = self._select_action(processed, torch)
            action = self._postprocessor(action)

        return InferResponse(
            action=_action_to_vector(action),
            chunk_size=self._chunk_size,
            n_action_steps=self._n_action_steps,
        )

    def infer_chunk(self, observation: InferObservation) -> np.ndarray:
        """Run ``predict_action_chunk`` and return an unnormalized (N, 7) array."""
        import torch

        if (
            self._policy is None
            or self._preprocessor is None
            or self._postprocessor is None
            or self._device is None
        ):
            raise RuntimeError("SmolVLAInferenceRuntime.load() must be called first")

        if observation.reset:
            self.reset()

        batch = self._observation_to_batch(observation, torch)
        with torch.inference_mode():
            processed = self._preprocessor(batch)
            action_chunk = self._predict_action_chunk(processed, torch)
            action_chunk = self._postprocessor(action_chunk)

        return _action_to_chunk(action_chunk)

    def infer_dataset_frame(
        self,
        *,
        episode_index: int,
        frame_index: int,
        reset: bool = False,
        task_override: str | None = None,
    ) -> tuple[InferObservation, InferResponse]:
        """Load one server-side dataset frame and run one-action inference."""
        if self._dataset is None:
            raise RuntimeError("SmolVLAInferenceRuntime.load() must be called first")

        from sharedautonomy.policies.act.runtime import resolve_dataset_frame_index

        global_index = resolve_dataset_frame_index(
            self._dataset,
            episode_index=episode_index,
            frame_index=frame_index,
        )
        sample = self._dataset[global_index]
        task = task_override if task_override is not None else str(sample["task"])
        if self._state_dim is None:
            raise RuntimeError("SmolVLAInferenceRuntime.load() must be called first")
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

    def _policy_with_queues(self) -> Any:
        if self._base_policy is not None:
            return self._base_policy
        return self._policy

    def _select_action(self, processed: dict[str, Any], torch: Any) -> Any:
        if self._policy is None or self._policy_config is None or self._device is None:
            raise RuntimeError("SmolVLAInferenceRuntime.load() must be called first")

        queue_policy = self._policy_with_queues()
        replan = smolvla_action_queue_is_empty(queue_policy)
        noise = self._noise_for_replan(torch) if replan else None
        action = self._policy.select_action(processed, noise=noise)
        if replan and self.config.inference_noise_seed is not None:
            self._inference_chunk_index += 1
        return action

    def _predict_action_chunk(self, processed: dict[str, Any], torch: Any) -> Any:
        if self._policy is None or self._policy_config is None or self._device is None:
            raise RuntimeError("SmolVLAInferenceRuntime.load() must be called first")

        noise = self._noise_for_replan(torch)
        action_chunk = self._policy.predict_action_chunk(processed, noise=noise)
        if self.config.inference_noise_seed is not None:
            self._inference_chunk_index += 1
        return action_chunk

    def _noise_for_replan(self, torch: Any) -> Any | None:
        seed_base = self.config.inference_noise_seed
        if seed_base is None or self._policy_config is None or self._device is None:
            return None
        return make_smolvla_inference_noise(
            torch=torch,
            device=self._device,
            chunk_size=int(self._policy_config.chunk_size),
            max_action_dim=int(self._policy_config.max_action_dim),
            seed=inference_noise_seed_for_chunk(seed_base, self._inference_chunk_index),
        )

    def _resolve_device(self, torch: Any) -> Any:
        device = torch.device(self.config.device)
        if str(self.config.device).startswith("cuda") and not torch.cuda.is_available():
            logger.warning("CUDA unavailable; falling back to CPU")
            device = torch.device("cpu")
        return device

    @staticmethod
    def _resolve_base_model(
        checkpoint: Path,
        *,
        configured_base: str | Path | None,
        adapter_base: str | None,
    ) -> str | None:
        candidate = configured_base if configured_base is not None else adapter_base
        if candidate is None:
            return None
        candidate_text = str(candidate)
        candidate_path = Path(candidate_text).expanduser()
        if not candidate_path.is_absolute():
            relative_path = checkpoint / candidate_path
            if relative_path.is_dir():
                return str(relative_path)
        return candidate_text

    def _observation_to_batch(self, observation: InferObservation, torch: Any) -> dict[str, Any]:
        if self._state_dim is None:
            raise RuntimeError("SmolVLAInferenceRuntime.load() must be called first")
        state = require_state_matches_dim(
            observation.state,
            self._state_dim,
            dataset_root=str(self.config.dataset_root),
        )
        wrist = _hwc_uint8_to_chw_float(observation.wrist_rgb_hwc)
        external = _hwc_uint8_to_chw_float(observation.external_rgb_hwc)
        return {
            "observation.state": torch.from_numpy(state),
            f"observation.images.{WRIST_KEY}": torch.from_numpy(wrist),
            f"observation.images.{EXTERNAL_KEY}": torch.from_numpy(external),
            "task": str(observation.task),
        }


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _hwc_uint8_to_chw_float(image_hwc: np.ndarray) -> np.ndarray:
    image = np.asarray(image_hwc, dtype=np.uint8)
    if image.ndim != 3 or image.shape[-1] != 3:
        raise ValueError(f"expected HWC RGB uint8, got {image.shape}")
    chw = np.transpose(image, (2, 0, 1)).astype(np.float32) / 255.0
    return np.ascontiguousarray(chw)


def _action_to_vector(action: Any) -> np.ndarray:
    array = _to_numpy(action)
    if array.ndim == 3 and array.shape[0] == 1:
        array = array[0]
    if array.ndim == 2 and array.shape[0] == 1:
        array = array[0]
    return np.asarray(array, dtype=np.float32).reshape(ACTION_DIM)


def _action_to_chunk(action: Any) -> np.ndarray:
    array = _to_numpy(action)
    if array.ndim == 3 and array.shape[0] == 1:
        array = array[0]
    if array.ndim != 2 or array.shape[-1] != ACTION_DIM:
        raise ValueError(f"expected action chunk with shape (N, {ACTION_DIM}), got {array.shape}")
    return np.asarray(array, dtype=np.float32)


def _to_numpy(value: Any) -> np.ndarray:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        value = value.numpy()
    return np.asarray(value, dtype=np.float32)
