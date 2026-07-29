"""Unit tests for ACT rollout chunk playback."""

from __future__ import annotations

import numpy as np
import pytest

pytestmark = pytest.mark.core

from sharedautonomy.policies.act.protocol import ACTION_DIM, InferObservation
from sharedautonomy.policies.act.rollout import (
    ActChunkPlayer,
    ActRolloutConfig,
    ActRolloutLoop,
    InferMode,
    NoOpGripperActuator,
    clip_act_action,
)


def _zeros_obs(*, reset: bool = False) -> InferObservation:
    return InferObservation(
        state=np.zeros(7, dtype=np.float32),
        wrist_rgb_hwc=np.zeros((480, 640, 3), dtype=np.uint8),
        external_rgb_hwc=np.zeros((480, 640, 3), dtype=np.uint8),
        task="Pick up the blue rectangle and place it in the DOWN region.",
        reset=reset,
    )


def test_blind_window_steps_caps_reset_every_with_n_action_steps() -> None:
    config = ActRolloutConfig(reset_every=50, n_action_steps=10)
    assert config.blind_window_steps == 10


def test_blocking_replan_fills_local_queue() -> None:
    calls: list[bool] = []

    def infer_fn(obs: InferObservation):
        calls.append(bool(obs.reset))
        action = np.arange(ACTION_DIM, dtype=np.float32) + len(calls)
        return type("Resp", (), {"action": action})(), {}, float(len(calls))

    player = ActChunkPlayer(config=ActRolloutConfig(reset_every=3, infer_mode=InferMode.BLOCKING))
    try:
        first, _, _, _ = player.replan(obs=_zeros_obs(reset=True), infer_fn=infer_fn)
        assert first[0] == pytest.approx(1.0)
        assert player.queue_depth == 2
        assert calls == [True, False, False]

        second = player.pop_action()
        third = player.pop_action()
        assert second[0] == pytest.approx(2.0)
        assert third[0] == pytest.approx(3.0)
        assert player.needs_replan(step_index=3)
    finally:
        player.close()


def test_clip_act_action_limits_joint_step() -> None:
    present = [0.0, 0.0, 90.0, 0.0, 90.0, 0.0]
    action = [10.0, 0.0, 90.0, 0.0, 90.0, 0.0, 1.0]
    safe, gripper = clip_act_action(
        present_joints_deg=present,
        action=action,
        max_joint_step_deg=1.0,
        joint_limits_deg=None,
    )
    assert safe[0] == pytest.approx(1.0)
    assert gripper == pytest.approx(1.0)


def test_rollout_loop_replans_then_plays_from_queue() -> None:
    infer_calls = 0

    def infer_fn(obs: InferObservation):
        nonlocal infer_calls
        infer_calls += 1
        value = float(infer_calls)
        action = np.full(ACTION_DIM, value, dtype=np.float32)
        return type("Resp", (), {"action": action})(), {}, 1.0

    config = ActRolloutConfig(reset_every=2, infer_mode=InferMode.BLOCKING)
    player = ActChunkPlayer(config=config)
    loop = ActRolloutLoop(
        config=config,
        infer_fn=infer_fn,
        player=player,
        gripper=NoOpGripperActuator(),
        motion_enabled=False,
    )
    try:
        step0 = loop.run_step(
            step_index=0,
            obs=_zeros_obs(),
            present_joints_deg=[0.0] * 6,
        )
        assert step0.replan is True
        assert step0.raw_action[0] == pytest.approx(1.0)

        step1 = loop.run_step(
            step_index=1,
            obs=_zeros_obs(),
            present_joints_deg=[0.0] * 6,
        )
        assert step1.replan is False
        assert step1.raw_action[0] == pytest.approx(2.0)

        step2 = loop.run_step(
            step_index=2,
            obs=_zeros_obs(),
            present_joints_deg=[0.0] * 6,
        )
        assert step2.replan is True
        assert step2.raw_action[0] == pytest.approx(3.0)
        assert infer_calls == 4
    finally:
        loop.close()
