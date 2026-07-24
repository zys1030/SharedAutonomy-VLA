"""Unit tests for the UDP realtime joint-state cache and state source."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sharedautonomy.control.realtime import RealtimeCartesianStateSource
from sharedautonomy.robot.realtime_state import (
    RealManRealtimeStateSource,
    RealtimeStateError,
    UdpJointStateCache,
    extract_joint_positions,
)


def _fake_state(joints: list[float], *, err_code: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        errCode=err_code,
        joint_status=SimpleNamespace(joint_position=joints),
    )


class FakeArm:
    def __init__(self) -> None:
        self.callback = None
        self.deleted = False
        self.fk_calls: list[list[float]] = []

    def rm_create_robot_arm(self, ip: str, port: int, level: int = 3):
        self.connection = (ip, port, level)
        return SimpleNamespace(id=3)

    def rm_get_realtime_push(self):
        return 0, {"enable": True, "cycle": 1, "port": 8089}

    def rm_realtime_arm_state_call_back(self, callback) -> None:
        self.callback = callback

    def rm_algo_forward_kinematics(self, joints, flag: int = 1):
        self.fk_calls.append(list(joints))
        assert flag == 1
        return [-0.30, -0.10, 0.25, 0.0, 1.5708, 0.0]

    def rm_delete_robot_arm(self) -> int:
        self.deleted = True
        return 0


def test_udp_cache_stores_latest_sample_and_errors() -> None:
    cache = UdpJointStateCache()
    assert cache.snapshot() is None

    cache.update([1, 2, 3, 4, 5, 6], received_monotonic_ns=100)
    cache.update([6, 5, 4, 3, 2, 1], received_monotonic_ns=200)
    cache.record_callback_error()

    snap = cache.snapshot()
    assert snap is not None
    assert snap.joint_position_deg == (6.0, 5.0, 4.0, 3.0, 2.0, 1.0)
    assert snap.received_monotonic_ns == 200
    assert cache.callback_error_count == 1
    assert cache.wait_for_first_sample(0.01) is True


def test_extract_joint_positions() -> None:
    assert extract_joint_positions(_fake_state([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])) == [
        0.1,
        0.2,
        0.3,
        0.4,
        0.5,
        0.6,
    ]


def test_realtime_source_connects_and_reads_snapshot() -> None:
    arm = FakeArm()

    def register_and_push(callback) -> None:
        arm.callback = callback
        callback(_fake_state([0.0, 15.0, 15.0, 0.0, 120.0, 0.0]))

    arm.rm_realtime_arm_state_call_back = register_and_push  # type: ignore[method-assign]
    source = RealManRealtimeStateSource(
        ip="192.0.2.10",
        arm_factory=lambda: arm,
        wrap_realtime_callback=False,
        first_sample_timeout_s=0.5,
    )

    source.connect()
    snapshot = source.read_snapshot(now_monotonic_ns=1_000_000)
    adapter = RealtimeCartesianStateSource(source)
    robot_state = adapter.read_cartesian_state(now_monotonic_ns=1_000_000)

    assert source.is_connected is True
    assert snapshot.ee_position_m == pytest.approx((-0.30, -0.10, 0.25))
    assert snapshot.joint_position_deg == pytest.approx((0.0, 15.0, 15.0, 0.0, 120.0, 0.0))
    assert robot_state.robot_state_age_ms == pytest.approx(snapshot.robot_state_age_ms)
    assert arm.fk_calls
    assert source.arm is arm

    source.disconnect()
    assert arm.deleted is True
    assert source.is_connected is False


def test_realtime_source_counts_callback_errors_and_ignores_bad_samples() -> None:
    arm = FakeArm()

    def register_and_push(callback) -> None:
        arm.callback = callback
        callback(_fake_state([0.0] * 6, err_code=7))
        callback(_fake_state([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]))

    arm.rm_realtime_arm_state_call_back = register_and_push  # type: ignore[method-assign]
    source = RealManRealtimeStateSource(
        ip="192.0.2.10",
        arm_factory=lambda: arm,
        wrap_realtime_callback=False,
    )
    source.connect()

    assert source.cache.callback_error_count == 1
    assert source.cache.snapshot() is not None
    assert source.cache.snapshot().joint_position_deg[0] == pytest.approx(1.0)
    source.disconnect()


def test_realtime_source_requires_enabled_udp_push() -> None:
    arm = FakeArm()
    arm.rm_get_realtime_push = lambda: (0, {"enable": False})  # type: ignore[method-assign]
    source = RealManRealtimeStateSource(
        ip="192.0.2.10",
        arm_factory=lambda: arm,
        wrap_realtime_callback=False,
    )
    with pytest.raises(RealtimeStateError, match="disabled"):
        source.connect()
