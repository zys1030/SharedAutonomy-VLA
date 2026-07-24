"""Adapters that expose robot realtime state to the manual Cartesian runner."""

from __future__ import annotations

from datetime import UTC, datetime

from sharedautonomy.control.manual import CartesianRobotState
from sharedautonomy.data.schema import SampleTimestamp
from sharedautonomy.robot.realtime_state import RealManRealtimeStateSource


class RealtimeCartesianStateSource:
    """``RobotStateSource`` wrapper over ``RealManRealtimeStateSource``."""

    def __init__(self, backend: RealManRealtimeStateSource) -> None:
        self._backend = backend

    @property
    def backend(self) -> RealManRealtimeStateSource:
        return self._backend

    def read_cartesian_state(self, *, now_monotonic_ns: int) -> CartesianRobotState:
        snapshot = self._backend.read_snapshot(now_monotonic_ns=now_monotonic_ns)
        return CartesianRobotState(
            timestamp=SampleTimestamp(
                timestamp_utc=datetime.now(tz=UTC),
                received_monotonic_ns=snapshot.received_monotonic_ns,
            ),
            joint_position_deg=snapshot.joint_position_deg,
            ee_position_m=snapshot.ee_position_m,
            ee_rpy_rad=snapshot.ee_rpy_rad,
            robot_state_age_ms=snapshot.robot_state_age_ms,
        )
