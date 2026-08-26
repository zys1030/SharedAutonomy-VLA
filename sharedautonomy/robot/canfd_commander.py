"""RealMan low-follow CAN-FD joint command adapter."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sharedautonomy.robot.safety import MotionDisabledError


class RealManCanfdJointCommander:
    """Send joint targets with ``rm_movej_canfd``. Motion must be explicitly armed."""

    def __init__(
        self,
        arm: Any,
        *,
        follow: bool = False,
        trajectory_mode: int = 0,
        smoothing: int = 0,
        armed: bool = False,
    ) -> None:
        self._arm = arm
        self.follow = bool(follow)
        self.trajectory_mode = int(trajectory_mode)
        self.smoothing = int(smoothing)
        self._armed = bool(armed)
        self.commands_sent = 0

    @property
    def armed(self) -> bool:
        return self._armed

    def arm_motion(self) -> None:
        """Allow subsequent ``send_joint_target`` calls."""
        self._armed = True

    def disarm_motion(self) -> None:
        self._armed = False

    def send_joint_target(self, joint_target_deg: Sequence[float]) -> None:
        if not self._armed:
            raise MotionDisabledError(
                "RealManCanfdJointCommander is not armed; refusing to send CAN-FD motion"
            )
        joints = [float(value) for value in joint_target_deg]
        if len(joints) != 6:
            raise ValueError(f"joint_target_deg must contain 6 values, got {len(joints)}")
        status = self._arm.rm_movej_canfd(
            joints,
            follow=self.follow,
            expand=0,
            trajectory_mode=self.trajectory_mode,
            radio=self.smoothing,
        )
        if int(status) != 0:
            raise RuntimeError(f"rm_movej_canfd failed with SDK status {status}")
        self.commands_sent += 1

    def slow_stop(self) -> int:
        """Request a controller slow stop. Returns SDK status."""
        if not hasattr(self._arm, "rm_set_arm_slow_stop"):
            raise RuntimeError("Connected arm does not expose rm_set_arm_slow_stop")
        return int(self._arm.rm_set_arm_slow_stop())
