"""Unit tests for the post-close arm hold gate."""

from __future__ import annotations

import pytest
from sharedautonomy.control.post_close_hold import PostCloseMotionHold, validate_post_close_hold_s

pytestmark = pytest.mark.core


def test_validate_post_close_hold_s_rejects_negative() -> None:
    with pytest.raises(ValueError, match="post_close_hold_s"):
        validate_post_close_hold_s(-0.1)


def test_default_hold_is_disabled() -> None:
    hold = PostCloseMotionHold()
    assert hold.hold_s == pytest.approx(0.0)


def test_zero_hold_is_noop() -> None:
    hold = PostCloseMotionHold(hold_s=0.0)
    commanded = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    present = [0.0] * 6
    decision = hold.apply(
        commanded_joints_deg=commanded,
        freeze_joints_deg=present,
        close_triggered=True,
        now_s=0.0,
    )
    assert decision.active is False
    assert decision.joints_deg == commanded
    assert decision.remaining_s == pytest.approx(0.0)


def test_close_edge_freezes_present_joints_for_hold_duration() -> None:
    hold = PostCloseMotionHold(hold_s=1.5)
    present = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
    lifting = [11.0, 20.0, 30.0, 40.0, 50.0, 60.0]

    start = hold.apply(
        commanded_joints_deg=lifting,
        freeze_joints_deg=present,
        close_triggered=True,
        now_s=0.0,
    )
    assert start.active is True
    assert start.joints_deg == present
    assert start.remaining_s == pytest.approx(1.5)

    mid = hold.apply(
        commanded_joints_deg=lifting,
        freeze_joints_deg=present,
        close_triggered=False,
        now_s=1.0,
    )
    assert mid.active is True
    assert mid.joints_deg == present
    assert mid.remaining_s == pytest.approx(0.5)

    done = hold.apply(
        commanded_joints_deg=lifting,
        freeze_joints_deg=present,
        close_triggered=False,
        now_s=1.5,
    )
    assert done.active is False
    assert done.joints_deg == lifting
    assert done.remaining_s == pytest.approx(0.0)


def test_close_during_hold_does_not_extend() -> None:
    hold = PostCloseMotionHold(hold_s=1.5)
    present = [0.0] * 6
    later = [5.0] * 6
    hold.apply(
        commanded_joints_deg=later,
        freeze_joints_deg=present,
        close_triggered=True,
        now_s=0.0,
    )
    nested = hold.apply(
        commanded_joints_deg=later,
        freeze_joints_deg=later,
        close_triggered=True,
        now_s=0.5,
    )
    assert nested.active is True
    assert nested.joints_deg == present
    assert nested.remaining_s == pytest.approx(1.0)


def test_next_close_after_hold_retriggers() -> None:
    hold = PostCloseMotionHold(hold_s=1.5)
    first = [1.0] * 6
    second = [2.0] * 6
    command = [9.0] * 6
    hold.apply(
        commanded_joints_deg=command,
        freeze_joints_deg=first,
        close_triggered=True,
        now_s=0.0,
    )
    hold.apply(
        commanded_joints_deg=command,
        freeze_joints_deg=first,
        close_triggered=False,
        now_s=1.5,
    )
    again = hold.apply(
        commanded_joints_deg=command,
        freeze_joints_deg=second,
        close_triggered=True,
        now_s=2.0,
    )
    assert again.active is True
    assert again.joints_deg == second
    assert again.remaining_s == pytest.approx(1.5)
