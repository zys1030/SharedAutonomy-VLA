"""Hold arm joints after a grasp-close edge so the gripper can settle."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field


def validate_post_close_hold_s(hold_s: float) -> float:
    """Return ``hold_s`` after checking it is a finite non-negative duration."""
    value = float(hold_s)
    if not math.isfinite(value) or value < 0.0:
        raise ValueError("post_close_hold_s must be a finite value >= 0")
    return value


@dataclass(frozen=True, slots=True)
class PostCloseHoldDecision:
    """Joints to send this step after applying the post-close hold."""

    joints_deg: list[float]
    active: bool
    remaining_s: float


@dataclass
class PostCloseMotionHold:
    """Freeze joint commands for ``hold_s`` after the first open→close edge.

    Gripper actuation is not gated: the close pulse should already have been
    sent. A close edge while the hold is active does not extend it. After the
    hold ends, the next close edge starts a new hold.
    ``hold_s=0`` (default) is a no-op; callers must pass a positive duration
    to enable the gate.
    """

    hold_s: float = 0.0
    _hold_until_s: float | None = field(default=None, init=False, repr=False)
    _frozen_joints_deg: list[float] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.hold_s = validate_post_close_hold_s(self.hold_s)

    @property
    def active(self) -> bool:
        return self._hold_until_s is not None

    def apply(
        self,
        *,
        commanded_joints_deg: Sequence[float],
        freeze_joints_deg: Sequence[float],
        close_triggered: bool,
        now_s: float,
    ) -> PostCloseHoldDecision:
        """Return frozen joints while the hold is active, else the command."""
        if self.hold_s <= 0.0:
            return PostCloseHoldDecision(
                joints_deg=[float(value) for value in commanded_joints_deg],
                active=False,
                remaining_s=0.0,
            )

        if close_triggered and self._hold_until_s is None:
            self._frozen_joints_deg = [float(value) for value in freeze_joints_deg]
            self._hold_until_s = float(now_s) + self.hold_s

        if self._hold_until_s is None or self._frozen_joints_deg is None:
            return PostCloseHoldDecision(
                joints_deg=[float(value) for value in commanded_joints_deg],
                active=False,
                remaining_s=0.0,
            )

        remaining_s = self._hold_until_s - float(now_s)
        if remaining_s <= 0.0:
            self._hold_until_s = None
            self._frozen_joints_deg = None
            return PostCloseHoldDecision(
                joints_deg=[float(value) for value in commanded_joints_deg],
                active=False,
                remaining_s=0.0,
            )

        return PostCloseHoldDecision(
            joints_deg=list(self._frozen_joints_deg),
            active=True,
            remaining_s=remaining_s,
        )
