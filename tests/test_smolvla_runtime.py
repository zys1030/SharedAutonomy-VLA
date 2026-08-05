"""Unit tests for SmolVLA checkpoint and action runtime helpers."""

from __future__ import annotations

import numpy as np
import pytest
from sharedautonomy.policies.smolvla.runtime import (
    SmolVLAInferenceRuntime,
    SmolVLARuntimeConfig,
    _action_to_chunk,
    _action_to_vector,
    detect_checkpoint_kind,
    resolve_checkpoint_dir,
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

    runtime.reset()

    assert fake_policy.reset_calls == 1
