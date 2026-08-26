"""Unit tests for SmolVLA checkpoint and action runtime helpers."""

from __future__ import annotations

import numpy as np
import pytest
from sharedautonomy.policies.smolvla.runtime import (
    SmolVLAInferenceRuntime,
    SmolVLARuntimeConfig,
    DEFAULT_INFERENCE_NOISE_SEED,
    _action_to_chunk,
    _action_to_vector,
    detect_checkpoint_kind,
    inference_noise_seed_for_chunk,
    make_smolvla_inference_noise,
    resolve_checkpoint_dir,
    smolvla_action_queue_is_empty,
)

pytestmark = pytest.mark.core


def test_detect_full_checkpoint(tmp_path) -> None:
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "model.safetensors").write_bytes(b"")

    assert detect_checkpoint_kind(tmp_path) == "full"


def test_detect_lora_checkpoint(tmp_path) -> None:
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "adapter_config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "adapter_model.safetensors").write_bytes(b"")

    assert detect_checkpoint_kind(tmp_path) == "lora"


def test_resolve_nested_pretrained_model(tmp_path) -> None:
    nested = tmp_path / "pretrained_model"
    nested.mkdir()
    (nested / "config.json").write_text("{}", encoding="utf-8")
    (nested / "model.safetensors").write_bytes(b"")

    assert resolve_checkpoint_dir(tmp_path) == nested


def test_invalid_checkpoint_reports_expected_artifacts(tmp_path) -> None:
    with pytest.raises(FileNotFoundError, match="config.json"):
        detect_checkpoint_kind(tmp_path)


def test_action_shapes_are_normalized() -> None:
    action = np.arange(7, dtype=np.float32).reshape(1, 7)
    assert _action_to_vector(action).shape == (7,)

    chunk = np.zeros((1, 5, 7), dtype=np.float32)
    assert _action_to_chunk(chunk).shape == (5, 7)


def test_runtime_reset_delegates_to_loaded_policy(tmp_path) -> None:
    class FakePolicy:
        def __init__(self) -> None:
            self.reset_calls = 0

        def reset(self) -> None:
            self.reset_calls += 1

    runtime = SmolVLAInferenceRuntime(
        SmolVLARuntimeConfig(
            checkpoint_dir=tmp_path,
            dataset_repo_id="local/test",
            dataset_root=tmp_path,
        )
    )
    fake_policy = FakePolicy()
    runtime._policy = fake_policy
    runtime._inference_chunk_index = 3

    runtime.reset()

    assert fake_policy.reset_calls == 1
    assert runtime._inference_chunk_index == 0


def test_runtime_describe_includes_dataset_state_dim(tmp_path) -> None:
    runtime = SmolVLAInferenceRuntime(
        SmolVLARuntimeConfig(
            checkpoint_dir=tmp_path,
            dataset_repo_id="local/test",
            dataset_root=tmp_path,
        )
    )
    runtime._state_dim = 8
    runtime._state_names = [
        "joint_1.pos",
        "joint_2.pos",
        "joint_3.pos",
        "joint_4.pos",
        "joint_5.pos",
        "joint_6.pos",
        "gripper.pos",
        "ee.z",
    ]
    summary = runtime.describe()
    assert summary["state_dim"] == 8
    assert summary["state_names"][-1] == "ee.z"


def test_inference_noise_seed_for_chunk_increments_per_replan() -> None:
    base = DEFAULT_INFERENCE_NOISE_SEED
    assert inference_noise_seed_for_chunk(base, 0) == base
    assert inference_noise_seed_for_chunk(base, 4) == base + 4


def test_make_smolvla_inference_noise_is_reproducible() -> None:
    torch = pytest.importorskip("torch")

    kwargs = {
        "torch": torch,
        "device": torch.device("cpu"),
        "chunk_size": 25,
        "max_action_dim": 32,
        "seed": 123,
    }
    first = make_smolvla_inference_noise(**kwargs)
    second = make_smolvla_inference_noise(**kwargs)

    assert first.shape == (1, 25, 32)
    assert torch.allclose(first, second)


def test_smolvla_action_queue_is_empty_without_queues() -> None:
    class Policy:
        pass

    assert smolvla_action_queue_is_empty(Policy()) is True


def test_smolvla_action_queue_is_empty_tracks_action_deque() -> None:
    from collections import deque

    from lerobot.utils.constants import ACTION

    class Policy:
        def __init__(self) -> None:
            self._queues = {ACTION: deque()}

    policy = Policy()
    assert smolvla_action_queue_is_empty(policy) is True
    policy._queues[ACTION].append(object())
    assert smolvla_action_queue_is_empty(policy) is False
