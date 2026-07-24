"""UDP realtime joint-state cache and RealMan state-source adapter.

The robot pushes state to the host over UDP. This module never sends motion
commands; it only maintains a thread-safe latest-state cache for the control loop.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class RealtimeStateError(RuntimeError):
    """Raised when UDP realtime state is unavailable or invalid."""


@dataclass(frozen=True, slots=True)
class CachedJointState:
    """Latest joint sample stored by the UDP callback."""

    joint_position_deg: tuple[float, ...]
    received_monotonic_ns: int


@dataclass(frozen=True, slots=True)
class RealtimeArmSnapshot:
    """Cartesian snapshot derived from the latest UDP joints plus local FK."""

    joint_position_deg: tuple[float, ...]
    ee_position_m: tuple[float, float, float]
    ee_rpy_rad: tuple[float, float, float]
    received_monotonic_ns: int
    robot_state_age_ms: float
    callback_error_count: int


@dataclass
class UdpJointStateCache:
    """Thread-safe latest-joint cache updated by a realtime callback."""

    joint_count: int = 6
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _latest: CachedJointState | None = field(default=None, init=False, repr=False)
    _callback_errors: int = field(default=0, init=False, repr=False)
    _first_sample: threading.Event = field(default_factory=threading.Event, init=False, repr=False)

    def update(
        self,
        joint_position_deg: Sequence[float],
        *,
        received_monotonic_ns: int,
    ) -> None:
        joints = tuple(float(value) for value in joint_position_deg)
        if len(joints) != self.joint_count:
            raise ValueError(f"Expected {self.joint_count} joints, got {len(joints)}")
        if int(received_monotonic_ns) < 0:
            raise ValueError("received_monotonic_ns must be non-negative")
        with self._lock:
            self._latest = CachedJointState(
                joint_position_deg=joints,
                received_monotonic_ns=int(received_monotonic_ns),
            )
            self._first_sample.set()

    def record_callback_error(self) -> None:
        with self._lock:
            self._callback_errors += 1

    def snapshot(self) -> CachedJointState | None:
        with self._lock:
            return self._latest

    @property
    def callback_error_count(self) -> int:
        with self._lock:
            return self._callback_errors

    def wait_for_first_sample(self, timeout_s: float) -> bool:
        if float(timeout_s) <= 0.0:
            raise ValueError("timeout_s must be positive")
        return self._first_sample.wait(timeout=float(timeout_s))

    def clear(self) -> None:
        with self._lock:
            self._latest = None
            self._callback_errors = 0
            self._first_sample.clear()


def extract_joint_positions(state: Any, *, joint_count: int = 6) -> list[float]:
    """Extract joint positions from a RealMan realtime state object."""
    joint_status = getattr(state, "joint_status", None)
    if joint_status is None:
        raise RealtimeStateError("Realtime state is missing joint_status")
    positions = getattr(joint_status, "joint_position", None)
    if positions is None:
        raise RealtimeStateError("Realtime state is missing joint_position")
    return [float(positions[index]) for index in range(joint_count)]


class RealManRealtimeStateSource:
    """Read-only RM-65B UDP state source with local forward kinematics."""

    def __init__(
        self,
        *,
        ip: str,
        port: int = 8080,
        sdk_log_level: int = 3,
        first_sample_timeout_s: float = 2.0,
        arm_factory: Callable[[], Any] | None = None,
        wrap_realtime_callback: bool | None = None,
        cache: UdpJointStateCache | None = None,
    ) -> None:
        if not str(ip).strip():
            raise ValueError("ip must not be empty")
        if not 1 <= int(port) <= 65535:
            raise ValueError("port must be in [1, 65535]")
        if float(first_sample_timeout_s) <= 0.0:
            raise ValueError("first_sample_timeout_s must be positive")

        self.ip = str(ip).strip()
        self.port = int(port)
        self.sdk_log_level = int(sdk_log_level)
        self.first_sample_timeout_s = float(first_sample_timeout_s)
        self._arm_factory = arm_factory
        self._wrap_realtime_callback = (
            bool(wrap_realtime_callback) if wrap_realtime_callback is not None else arm_factory is None
        )
        self.cache = cache or UdpJointStateCache()
        self._arm: Any | None = None
        self._connected = False
        self._closing = False
        self._callback_keepalive: Any | None = None
        self._realtime_config: dict[str, Any] | None = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def arm(self) -> Any:
        self._require_connected()
        assert self._arm is not None
        return self._arm

    @property
    def realtime_config(self) -> dict[str, Any] | None:
        return None if self._realtime_config is None else dict(self._realtime_config)

    def connect(self) -> None:
        if self._connected:
            raise RuntimeError("Realtime state source is already connected")

        arm = self._arm_factory() if self._arm_factory is not None else self._make_sdk_arm()
        handle = arm.rm_create_robot_arm(self.ip, self.port, level=self.sdk_log_level)
        handle_id = int(getattr(handle, "id", -1))
        if handle_id < 0:
            raise ConnectionError(f"Failed to connect to RM-65B at {self.ip}:{self.port}")

        self._arm = arm
        try:
            status, config = arm.rm_get_realtime_push()
            if int(status) != 0:
                raise RealtimeStateError(f"Failed to read realtime-push config: SDK status {status}")
            if not bool(config.get("enable")):
                raise RealtimeStateError("UDP realtime push is disabled on the controller")
            self._realtime_config = dict(config)
            self._closing = False
            self.cache.clear()

            callback = self._build_callback()
            self._callback_keepalive = callback
            arm.rm_realtime_arm_state_call_back(callback)
            if not self.cache.wait_for_first_sample(self.first_sample_timeout_s):
                raise RealtimeStateError(
                    f"No UDP realtime state arrived within {self.first_sample_timeout_s:.1f}s"
                )
            self._connected = True
            logger.info(
                "Connected RM-65B realtime state source at %s:%s without enabling motion",
                self.ip,
                self.port,
            )
        except Exception:
            self._disconnect_quietly()
            raise

    def disconnect(self) -> None:
        """Release the SDK handle. Cleanup failures are logged, not raised."""
        self._disconnect_quietly(raise_on_error=False)

    def read_snapshot(self, *, now_monotonic_ns: int | None = None) -> RealtimeArmSnapshot:
        self._require_connected()
        assert self._arm is not None
        now_ns = int(time.perf_counter_ns() if now_monotonic_ns is None else now_monotonic_ns)
        cached = self.cache.snapshot()
        if cached is None:
            raise RealtimeStateError("UDP realtime cache is empty")

        pose = self._arm.rm_algo_forward_kinematics(list(cached.joint_position_deg), flag=1)
        if len(pose) < 6:
            raise RealtimeStateError(f"FK returned invalid pose length {len(pose)}")
        age_ms = max(0.0, (now_ns - cached.received_monotonic_ns) / 1_000_000.0)
        return RealtimeArmSnapshot(
            joint_position_deg=cached.joint_position_deg,
            ee_position_m=(float(pose[0]), float(pose[1]), float(pose[2])),
            ee_rpy_rad=(float(pose[3]), float(pose[4]), float(pose[5])),
            received_monotonic_ns=cached.received_monotonic_ns,
            robot_state_age_ms=age_ms,
            callback_error_count=self.cache.callback_error_count,
        )

    def handle_realtime_state(
        self,
        state: Any,
        *,
        received_monotonic_ns: int | None = None,
    ) -> None:
        """Process one realtime state sample. Used by the SDK callback and tests."""
        if self._closing:
            return
        received_ns = int(time.perf_counter_ns() if received_monotonic_ns is None else received_monotonic_ns)
        err_code = int(getattr(state, "errCode", 0))
        if err_code != 0:
            self.cache.record_callback_error()
            return
        try:
            joints = extract_joint_positions(state, joint_count=self.cache.joint_count)
        except Exception:
            self.cache.record_callback_error()
            logger.exception("Failed to parse realtime joint state")
            return
        self.cache.update(joints, received_monotonic_ns=received_ns)

    def _build_callback(self) -> Any:
        source = self

        def on_state(state: Any) -> None:
            source.handle_realtime_state(state)

        if not self._wrap_realtime_callback:
            return on_state

        from Robotic_Arm.rm_ctypes_wrap import rm_realtime_arm_state_callback_ptr

        return rm_realtime_arm_state_callback_ptr(on_state)

    @staticmethod
    def _make_sdk_arm() -> Any:
        try:
            from Robotic_Arm.rm_robot_interface import RoboticArm, rm_thread_mode_e
        except ImportError as exc:  # pragma: no cover - depends on hardware env
            raise ImportError(
                "RM-65B realtime state requires the RealMan Robotic_Arm package. "
                "Install the project's hardware extra in sharedautonomy-lr060-cf."
            ) from exc
        return RoboticArm(rm_thread_mode_e.RM_TRIPLE_MODE_E)

    def _require_connected(self) -> None:
        if not self._connected or self._arm is None:
            raise ConnectionError("Realtime state source is not connected")

    def _disconnect_quietly(self, *, raise_on_error: bool = False) -> None:
        errors: list[Exception] = []
        self._closing = True
        # Give in-flight UDP callbacks a moment to observe _closing before handle deletion.
        time.sleep(0.05)
        if self._arm is not None:
            try:
                status = self._arm.rm_delete_robot_arm()
                if int(status) != 0:
                    raise RuntimeError(f"Failed to delete robot arm handle: SDK status {status}")
            except Exception as exc:
                errors.append(exc)
        self._arm = None
        self._connected = False
        self._callback_keepalive = None
        self._realtime_config = None
        self._closing = False
        if errors and raise_on_error:
            raise RuntimeError(
                f"Realtime state disconnect completed with {len(errors)} error(s)"
            ) from errors[0]
        if errors:
            logger.warning("Realtime state disconnect completed with cleanup error(s): %s", errors[0])
