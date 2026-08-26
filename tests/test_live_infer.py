"""Unit tests for live infer state-name assembly and /health layout resolution."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from sharedautonomy.policies.act.live_infer import (
    build_infer_observation,
    resolve_infer_state_names,
)
from sharedautonomy.policies.act.protocol import STATE_NAMES

pytestmark = pytest.mark.core


def _synced(*, ee_z: float = 0.179) -> SimpleNamespace:
    wrist = SimpleNamespace(color_rgb=np.zeros((480, 640, 3), dtype=np.uint8))
    external = SimpleNamespace(color_rgb=np.ones((480, 640, 3), dtype=np.uint8) * 3)
    observation = SimpleNamespace(
        joint_position_deg=np.arange(6, dtype=np.float32),
        ee_position_m=np.asarray([0.1, 0.2, ee_z], dtype=np.float32),
        wrist_camera=wrist,
        external_camera=external,
    )
    return SimpleNamespace(observation=observation, warnings=[])


def test_build_infer_observation_follows_requested_names() -> None:
    full = build_infer_observation(
        synced=_synced(ee_z=0.2),
        task="Pick up the blue rectangle and place it in the UP region.",
        gripper_open_fraction=1.0,
        reset=True,
        state_names=list(STATE_NAMES),
    )
    eight = build_infer_observation(
        synced=_synced(ee_z=0.2),
        task="Pick up the blue rectangle and place it in the UP region.",
        gripper_open_fraction=1.0,
        reset=True,
        state_names=list(STATE_NAMES[:8]),
    )
    subset = build_infer_observation(
        synced=_synced(ee_z=0.2),
        task="Pick up the blue rectangle and place it in the UP region.",
        gripper_open_fraction=1.0,
        reset=True,
        state_names=["gripper.pos", "gripper.time_since_close", "ee.z"],
    )
    assert full.state.shape == (10,)
    assert eight.state.shape == (8,)
    np.testing.assert_allclose(eight.state, full.state[:8])
    assert eight.state[7] == pytest.approx(0.2)
    np.testing.assert_allclose(subset.state, [1.0, 0.0, 0.2], atol=1e-6)


def test_build_infer_observation_rejects_unknown_channel() -> None:
    with pytest.raises(ValueError, match="ee.x"):
        build_infer_observation(
            synced=_synced(),
            task="Pick up the blue rectangle and place it in the UP region.",
            gripper_open_fraction=1.0,
            reset=False,
            state_names=["joint_1.pos", "ee.x"],
        )


def test_resolve_infer_state_names_prefers_override_dim() -> None:
    names = resolve_infer_state_names(
        {"state_names": ["ee.z"], "state_dim": 10},
        override_dim=8,
    )
    assert names[-1] == "ee.z"
    assert len(names) == 8


def test_resolve_infer_state_names_uses_health_names() -> None:
    names = resolve_infer_state_names(
        {"ok": True, "state_dim": 8, "state_names": ["gripper.pos", "ee.z"]}
    )
    assert names == ["gripper.pos", "ee.z"]


def test_resolve_infer_state_names_falls_back_to_health_dim() -> None:
    names = resolve_infer_state_names({"ok": True, "state_dim": 8})
    assert len(names) == 8
    assert names[-1] == "ee.z"


def test_resolve_infer_state_names_requires_health_or_override() -> None:
    with pytest.raises(ValueError, match="state_names"):
        resolve_infer_state_names({"ok": True})
