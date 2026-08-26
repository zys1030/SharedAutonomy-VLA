"""Weak J6 edge-alignment assist from the third-person cube yaw estimate.

Cube wrap90 is locked at opening (vision). Every step the remaining error is
the wrap90 angle between that cube heading and the live gripper jaw heading
from FK (current joints → RPY). IK holds the current gripper yaw so
translation does not unwind the overlay. Live RGB is not used after the lock.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from sharedautonomy.data.schema import AssistAction, CoordinateFrame, SampleTimestamp
from sharedautonomy.perception.cube_yaw import (
    cube_gripper_wrap90_error_deg,
    gripper_jaw_table_heading_deg,
    measure_start_yaw_from_rgb,
    wrap_square_yaw_deg,
)
from sharedautonomy.perception.table_homography import TableHomography

INFERRED_RED_CUBE_ID = "red_cube"

_DEFAULT_EE_RPY_RAD = (0.0, math.pi / 2.0, 0.0)


@dataclass(frozen=True, slots=True)
class CubeYawAssistConfig:
    """Rate limits and freeze conditions for tool-axis yaw assist."""

    max_yaw_rate_rad_s: float = 0.4
    deadband_deg: float = 2.0
    align_time_s: float = 0.5
    override_full_wz_rad_s: float = 0.12
    min_open_fraction_to_assist: float = 0.9

    def __post_init__(self) -> None:
        if float(self.max_yaw_rate_rad_s) <= 0.0:
            raise ValueError("max_yaw_rate_rad_s must be positive")
        if float(self.deadband_deg) < 0.0:
            raise ValueError("deadband_deg must be >= 0")
        if float(self.align_time_s) <= 0.0:
            raise ValueError("align_time_s must be positive")
        if float(self.override_full_wz_rad_s) <= 0.0:
            raise ValueError("override_full_wz_rad_s must be positive")
        open_frac = float(self.min_open_fraction_to_assist)
        if not 0.0 <= open_frac <= 1.0:
            raise ValueError("min_open_fraction_to_assist must be in [0, 1]")
        object.__setattr__(self, "max_yaw_rate_rad_s", float(self.max_yaw_rate_rad_s))
        object.__setattr__(self, "deadband_deg", float(self.deadband_deg))
        object.__setattr__(self, "align_time_s", float(self.align_time_s))
        object.__setattr__(self, "override_full_wz_rad_s", float(self.override_full_wz_rad_s))
        object.__setattr__(self, "min_open_fraction_to_assist", open_frac)


@dataclass(frozen=True, slots=True)
class CubeYawAssistDecision:
    """One-step yaw-assist command plus the ``AssistAction`` to record."""

    delta_j6_deg: float | None
    yaw_wrap90_deg: float | None
    desired_yaw_rate_rad_s: float
    authority: float
    confidence: float
    reason: str
    assist_action: AssistAction

    @property
    def applied_yaw_rate_rad_s(self) -> float:
        """Joint-positive yaw rate to overlay on the IK J6 (sign = +1)."""
        return float(self.desired_yaw_rate_rad_s) * float(self.authority)


class CubeYawAssistPolicy(Protocol):
    """Propose a J6 overlay equal to the live cube–gripper wrap90 error."""

    def propose(
        self,
        *,
        color_rgb: np.ndarray | None,
        j6_now_deg: float,
        ee_rpy_rad: Sequence[float],
        human_wz_rad_s: float,
        deadman_active: bool,
        gripper_open_fraction: float | None,
        timestamp: SampleTimestamp,
    ) -> CubeYawAssistDecision: ...


def compute_cube_yaw_assist(
    *,
    delta_j6_deg: float | None,
    timestamp: SampleTimestamp,
    human_wz_rad_s: float = 0.0,
    deadman_active: bool = True,
    gripper_open_fraction: float | None = 1.0,
    yaw_wrap90_deg: float | None = None,
    config: CubeYawAssistConfig | None = None,
) -> CubeYawAssistDecision:
    """Map wrap90 ``delta_j6_deg`` to a saturated rate, authority, and freeze.

    ``desired_yaw_rate_rad_s`` is the visual P-command before human override.
    Applied overlay is ``desired * authority``. Linear velocity and gripper stay
    with the human; this function never proposes either.
    """
    cfg = config or CubeYawAssistConfig()
    human_wz = float(human_wz_rad_s)
    override_frac = min(1.0, abs(human_wz) / float(cfg.override_full_wz_rad_s))

    if not deadman_active:
        return _decision(
            timestamp=timestamp,
            delta_j6_deg=delta_j6_deg,
            yaw_wrap90_deg=yaw_wrap90_deg,
            desired_yaw_rate_rad_s=0.0,
            authority=0.0,
            confidence=0.0 if delta_j6_deg is None else 1.0,
            reason="deadman_released",
            inferred_target_id=None if delta_j6_deg is None else INFERRED_RED_CUBE_ID,
        )
    if gripper_open_fraction is not None and float(gripper_open_fraction) < cfg.min_open_fraction_to_assist:
        # Stop adding overlay; the runner still holds current EE yaw so J6 does not unwind.
        return _decision(
            timestamp=timestamp,
            delta_j6_deg=delta_j6_deg,
            yaw_wrap90_deg=yaw_wrap90_deg,
            desired_yaw_rate_rad_s=0.0,
            authority=0.0,
            confidence=0.0 if delta_j6_deg is None else 1.0,
            reason="gripper_closing",
            inferred_target_id=None if delta_j6_deg is None else INFERRED_RED_CUBE_ID,
        )
    if delta_j6_deg is None:
        return _decision(
            timestamp=timestamp,
            delta_j6_deg=None,
            yaw_wrap90_deg=yaw_wrap90_deg,
            desired_yaw_rate_rad_s=0.0,
            authority=0.0,
            confidence=0.0,
            reason="no_detection",
            inferred_target_id=None,
        )

    error_deg = float(delta_j6_deg)
    if abs(error_deg) <= cfg.deadband_deg:
        return _decision(
            timestamp=timestamp,
            delta_j6_deg=error_deg,
            yaw_wrap90_deg=yaw_wrap90_deg,
            desired_yaw_rate_rad_s=0.0,
            authority=1.0 * (1.0 - override_frac),
            confidence=1.0,
            reason="human_override" if override_frac >= 1.0 else "aligned",
            inferred_target_id=INFERRED_RED_CUBE_ID,
        )

    error_rad = math.radians(error_deg)
    uncapped = error_rad / float(cfg.align_time_s)
    desired = max(-cfg.max_yaw_rate_rad_s, min(cfg.max_yaw_rate_rad_s, uncapped))
    authority = 1.0 - override_frac
    reason = "human_override" if override_frac > 0.0 else "assisting"
    if override_frac >= 1.0:
        reason = "human_override"
    return _decision(
        timestamp=timestamp,
        delta_j6_deg=error_deg,
        yaw_wrap90_deg=yaw_wrap90_deg,
        desired_yaw_rate_rad_s=desired,
        authority=authority,
        confidence=1.0,
        reason=reason,
        inferred_target_id=INFERRED_RED_CUBE_ID,
    )


def _decision(
    *,
    timestamp: SampleTimestamp,
    delta_j6_deg: float | None,
    yaw_wrap90_deg: float | None,
    desired_yaw_rate_rad_s: float,
    authority: float,
    confidence: float,
    reason: str,
    inferred_target_id: str | None,
) -> CubeYawAssistDecision:
    assist = AssistAction(
        timestamp=timestamp,
        linear_velocity_m_s=(0.0, 0.0, 0.0),
        angular_velocity_rad_s=(0.0, 0.0, float(desired_yaw_rate_rad_s)),
        gripper_target_open_fraction=None,
        confidence=float(confidence),
        inferred_target_id=inferred_target_id,
        reference_frame=CoordinateFrame.BASE,
    )
    return CubeYawAssistDecision(
        delta_j6_deg=None if delta_j6_deg is None else float(delta_j6_deg),
        yaw_wrap90_deg=None if yaw_wrap90_deg is None else float(yaw_wrap90_deg),
        desired_yaw_rate_rad_s=float(desired_yaw_rate_rad_s),
        authority=float(authority),
        confidence=float(confidence),
        reason=reason,
        assist_action=assist,
    )


class ExternalCubeYawAssistPolicy:
    """Overlay J6 until FK gripper heading matches the locked cube wrap90.

    ``locked_wrap90_deg`` is ledger ``yaw_bin_deg``. Stop when the live
    cube–gripper table angle is inside the deadband, or when the gripper
    starts closing. Do not integrate commanded J6.
    """

    def __init__(
        self,
        homography: TableHomography,
        config: CubeYawAssistConfig | None = None,
        *,
        locked_wrap90_deg: float | None = None,
        lock_on_first_detection: bool = True,
    ) -> None:
        self.homography = homography
        self.config = config or CubeYawAssistConfig()
        self._lock_on_first_detection = bool(lock_on_first_detection)
        self._locked_wrap90_deg: float | None = None
        self._gripper_heading_lock_deg: float | None = None
        if locked_wrap90_deg is not None:
            self._locked_wrap90_deg = wrap_square_yaw_deg(float(locked_wrap90_deg))

    @property
    def locked_wrap90_deg(self) -> float | None:
        return self._locked_wrap90_deg

    def propose(
        self,
        *,
        color_rgb: np.ndarray | None,
        j6_now_deg: float,
        ee_rpy_rad: Sequence[float],
        human_wz_rad_s: float,
        deadman_active: bool,
        gripper_open_fraction: float | None,
        timestamp: SampleTimestamp,
    ) -> CubeYawAssistDecision:
        rpy = tuple(float(value) for value in ee_rpy_rad) if ee_rpy_rad else _DEFAULT_EE_RPY_RAD
        heading_now_deg = gripper_jaw_table_heading_deg(rpy)
        if self._locked_wrap90_deg is None and self._lock_on_first_detection and color_rgb is not None:
            estimate = measure_start_yaw_from_rgb(
                color_rgb,
                self.homography,
                j6_now_deg=float(j6_now_deg),
            )
            if estimate is not None:
                self._locked_wrap90_deg = wrap_square_yaw_deg(float(estimate.yaw_wrap90_deg))
        if self._locked_wrap90_deg is None:
            return compute_cube_yaw_assist(
                delta_j6_deg=None,
                timestamp=timestamp,
                human_wz_rad_s=human_wz_rad_s,
                deadman_active=deadman_active,
                gripper_open_fraction=gripper_open_fraction,
                config=self.config,
            )
        if self._gripper_heading_lock_deg is None:
            self._gripper_heading_lock_deg = heading_now_deg
        remaining_deg = cube_gripper_wrap90_error_deg(
            self._locked_wrap90_deg,
            heading_now_deg,
            self._gripper_heading_lock_deg,
        )
        return compute_cube_yaw_assist(
            delta_j6_deg=remaining_deg,
            yaw_wrap90_deg=self._locked_wrap90_deg,
            timestamp=timestamp,
            human_wz_rad_s=human_wz_rad_s,
            deadman_active=deadman_active,
            gripper_open_fraction=gripper_open_fraction,
            config=self.config,
        )
