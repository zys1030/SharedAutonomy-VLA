"""Unit tests for ACT/SmolVLA infer wire protocol state layout."""

from __future__ import annotations

import numpy as np
import pytest

from sharedautonomy.policies.act.protocol import (
    InferObservation,
    catalog_state_names,
    observation_state_dim_from_dataset,
    observation_state_layout_from_features,
    observation_to_payload,
    payload_to_observation,
    require_state_matches_dim,
)

pytestmark = pytest.mark.core


def _obs(state_dim: int) -> InferObservation:
    return InferObservation(
        state=np.arange(state_dim, dtype=np.float32),
        wrist_rgb_hwc=np.zeros((8, 8, 3), dtype=np.uint8),
        external_rgb_hwc=np.full((8, 8, 3), 7, dtype=np.uint8),
        task="Pick up the blue rectangle and place it in the UP region.",
        reset=state_dim == 10,
    )


@pytest.mark.parametrize("state_dim", (7, 8, 9, 10))
def test_observation_payload_roundtrip_any_state_length(state_dim: int) -> None:
    original = _obs(state_dim)
    restored = payload_to_observation(observation_to_payload(original))
    np.testing.assert_allclose(restored.state, original.state)
    assert restored.state.shape == (state_dim,)
    assert restored.reset is original.reset
    assert restored.task == original.task


def test_payload_rejects_empty_state() -> None:
    obs = _obs(8)
    payload = observation_to_payload(obs)
    payload["observation"]["state"] = []
    with pytest.raises(ValueError, match="non-empty"):
        payload_to_observation(payload)


def test_catalog_state_names_is_prefix_escape_hatch() -> None:
    assert catalog_state_names(7)[-1] == "gripper.pos"
    assert catalog_state_names(8)[-1] == "ee.z"
    assert catalog_state_names(10)[-2:] == ["ee.dz", "gripper.time_since_close"]
    with pytest.raises(ValueError, match="exceeds the live catalog"):
        catalog_state_names(11)


def test_observation_state_layout_prefers_dataset_names() -> None:
    features = {
        "observation.state": {
            "shape": [3],
            "names": ["gripper.pos", "ee.z", "ee.dz"],
        }
    }
    dim, names = observation_state_layout_from_features(features)
    assert dim == 3
    assert names == ["gripper.pos", "ee.z", "ee.dz"]


def test_observation_state_layout_falls_back_to_catalog_prefix() -> None:
    features = {"observation.state": {"shape": [8], "dtype": "float32"}}
    dim, names = observation_state_layout_from_features(features)
    assert dim == 8
    assert names[-1] == "ee.z"

    class Meta:
        features = {"observation.state": {"shape": (10,)}}

    class Dataset:
        meta = Meta()

    assert observation_state_dim_from_dataset(Dataset()) == 10


def test_require_state_matches_dim_reports_expected_and_root() -> None:
    matched = require_state_matches_dim([0.0] * 8, 8, dataset_root="/tmp/c0_eez")
    assert matched.shape == (8,)
    with pytest.raises(ValueError, match="must be 8, got 10"):
        require_state_matches_dim(np.zeros(10, dtype=np.float32), 8, dataset_root="/tmp/c0_eez")
