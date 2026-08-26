"""Unit tests for SmolVLA rollout replan cadence."""

from __future__ import annotations

import pytest
from sharedautonomy.policies.smolvla.rollout import should_reset_action_queue

pytestmark = pytest.mark.core


def test_reset_every_zero_only_clears_on_first_infer() -> None:
    assert should_reset_action_queue(infer_index=0, reset_every=0) is True
    assert should_reset_action_queue(infer_index=1, reset_every=0) is False
    assert should_reset_action_queue(infer_index=49, reset_every=0) is False
    assert should_reset_action_queue(infer_index=50, reset_every=0) is False


def test_reset_every_25_matches_act_blocking_cadence() -> None:
    assert should_reset_action_queue(infer_index=0, reset_every=25) is True
    assert should_reset_action_queue(infer_index=1, reset_every=25) is False
    assert should_reset_action_queue(infer_index=24, reset_every=25) is False
    assert should_reset_action_queue(infer_index=25, reset_every=25) is True
    assert should_reset_action_queue(infer_index=50, reset_every=25) is True


def test_reset_every_rejects_negative() -> None:
    with pytest.raises(ValueError, match="reset_every"):
        should_reset_action_queue(infer_index=0, reset_every=-1)
    with pytest.raises(ValueError, match="infer_index"):
        should_reset_action_queue(infer_index=-1, reset_every=25)
