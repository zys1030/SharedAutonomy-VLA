"""SpaceMouse axis mapping and teleoperation adapter.

Axis transforms are adapted from ``try_sc_program``'s verified ``vertical_up``
mount preset. This project maps the left button to a deadman switch (hold to
enable motion), matching the Day-1 J6 smoke test rather than the older
translate/rotate mode latch.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
from numpy.typing import NDArray

from sharedautonomy.data.schema import CoordinateFrame, HumanAction, SampleTimestamp

# Raw pyspacemouse axes are converted into the SC_BR/LeRobot z-up intermediate
# frame, then into the mount preset. Matrices below encode that composition.
RAW_TO_ZUP_SPACEMOUSE_MATRIX = np.array(
    [[0.0, 0.0, -1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
    dtype=np.float64,
)
_ZUP_LEGACY_TRANSLATION_MATRIX = np.array(
    [[0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]],
    dtype=np.float64,
)
_ZUP_LEGACY_ROTATION_MATRIX = np.array(
    [[0.0, 0.0, -1.0], [-1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
    dtype=np.float64,
)
LEGACY_TRANSLATION_MATRIX = _ZUP_LEGACY_TRANSLATION_MATRIX @ RAW_TO_ZUP_SPACEMOUSE_MATRIX
LEGACY_ROTATION_MATRIX = _ZUP_LEGACY_ROTATION_MATRIX @ RAW_TO_ZUP_SPACEMOUSE_MATRIX
VERTICAL_UP_INSTALL_ROTATION = np.array(
    [[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]],
    dtype=np.float64,
)
VERTICAL_UP_SIGN_CORRECTION = np.array(
    [[-1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, -1.0]],
    dtype=np.float64,
)
VERTICAL_UP_TRANSLATION_MATRIX = (
    VERTICAL_UP_SIGN_CORRECTION
    @ VERTICAL_UP_INSTALL_ROTATION
    @ _ZUP_LEGACY_TRANSLATION_MATRIX
    @ RAW_TO_ZUP_SPACEMOUSE_MATRIX
)
VERTICAL_UP_ROTATION_MATRIX = (
    VERTICAL_UP_SIGN_CORRECTION
    @ VERTICAL_UP_INSTALL_ROTATION
    @ _ZUP_LEGACY_ROTATION_MATRIX
    @ RAW_TO_ZUP_SPACEMOUSE_MATRIX
)


@dataclass(frozen=True, slots=True)
class SpaceMouseConfig:
    """Shared SpaceMouse teleop defaults. Local device paths stay in local YAML."""

    deadzone: float = 0.1
    max_linear_speed_m_s: float = 0.05
    max_linear_speed_xy_m_s: float | None = None
    max_linear_speed_z_m_s: float | None = None
    max_angular_speed_rad_s: float = 0.4
    mount_orientation: str = "vertical_up"
    translation_transform: Sequence[Sequence[float]] | None = None
    rotation_transform: Sequence[Sequence[float]] | None = None
    # Extra base-frame yaw after mount transform. +90 = CCW looking down +Z.
    # Used when Z matches but XY needs a quarter-turn relative to try_sc feel.
    base_xy_yaw_deg: float = 0.0
    # If True, zero base-frame Z after mapping (XY planar teleop; rejects Z crosstalk).
    lock_z: bool = False
    input_timeout_s: float = 0.1
    drain_max_reads: int = 128
    allow_rotation: bool = False
    device_path: str | None = None
    hid_timeout_ms: int = 100
    axis_full_scale: float = 350.0

    def __post_init__(self) -> None:
        if not 0.0 <= float(self.deadzone) < 1.0:
            raise ValueError("deadzone must be in [0, 1)")
        if float(self.max_linear_speed_m_s) <= 0.0:
            raise ValueError("max_linear_speed_m_s must be positive")
        for value, name in (
            (self.max_linear_speed_xy_m_s, "max_linear_speed_xy_m_s"),
            (self.max_linear_speed_z_m_s, "max_linear_speed_z_m_s"),
        ):
            if value is not None and float(value) <= 0.0:
                raise ValueError(f"{name} must be positive when set")
        if float(self.max_angular_speed_rad_s) <= 0.0:
            raise ValueError("max_angular_speed_rad_s must be positive")
        if float(self.input_timeout_s) <= 0.0:
            raise ValueError("input_timeout_s must be positive")
        if int(self.drain_max_reads) < 1:
            raise ValueError("drain_max_reads must be >= 1")
        if int(self.hid_timeout_ms) <= 0:
            raise ValueError("hid_timeout_ms must be positive")
        if float(self.axis_full_scale) <= 0.0:
            raise ValueError("axis_full_scale must be positive")
        yaw = float(self.base_xy_yaw_deg)
        if not np.isfinite(yaw):
            raise ValueError("base_xy_yaw_deg must be finite")
        object.__setattr__(self, "base_xy_yaw_deg", yaw)
        object.__setattr__(self, "lock_z", bool(self.lock_z))


KNOWN_SPACEMOUSE_IDS = {
    (0x046D, 0xC625),
    (0x046D, 0xC626),
    (0x046D, 0xC627),
    (0x046D, 0xC629),
    (0x046D, 0xC62B),
    (0x256F, 0xC62E),
    (0x256F, 0xC632),
    (0x256F, 0xC633),
    (0x256F, 0xC635),
    (0x256F, 0xC63A),
    (0x256F, 0xC641),
    (0x256F, 0xC652),
}


@dataclass(frozen=True, slots=True)
class SpaceMouseAxes:
    """Normalized stick values after deadzone and mount transform, in robot base axes."""

    translation: tuple[float, float, float]
    rotation: tuple[float, float, float]
    deadman_active: bool
    gripper_button_edge: bool
    received_monotonic_ns: int
    input_age_ms: float


def get_spacemouse_transform(
    mount_orientation: str,
    custom_matrix: Sequence[Sequence[float]] | None = None,
) -> NDArray[np.float64]:
    if mount_orientation == "legacy_horizontal":
        return LEGACY_TRANSLATION_MATRIX.copy()
    if mount_orientation == "vertical_up":
        return VERTICAL_UP_TRANSLATION_MATRIX.copy()
    if mount_orientation == "custom":
        if custom_matrix is None:
            raise ValueError("custom translation_transform is required for mount_orientation='custom'")
        matrix = np.asarray(custom_matrix, dtype=np.float64)
        if matrix.shape != (3, 3):
            raise ValueError(f"translation_transform must have shape (3, 3), got {matrix.shape}")
        return matrix
    raise ValueError(f"Unknown SpaceMouse mount_orientation: {mount_orientation}")


def get_spacemouse_rotation_transform(
    mount_orientation: str,
    custom_matrix: Sequence[Sequence[float]] | None = None,
) -> NDArray[np.float64]:
    if mount_orientation == "legacy_horizontal":
        return LEGACY_ROTATION_MATRIX.copy()
    if mount_orientation == "vertical_up":
        return VERTICAL_UP_ROTATION_MATRIX.copy()
    if mount_orientation == "custom":
        if custom_matrix is None:
            raise ValueError("custom rotation_transform is required for mount_orientation='custom'")
        matrix = np.asarray(custom_matrix, dtype=np.float64)
        if matrix.shape != (3, 3):
            raise ValueError(f"rotation_transform must have shape (3, 3), got {matrix.shape}")
        return matrix
    raise ValueError(f"Unknown SpaceMouse mount_orientation: {mount_orientation}")


def apply_transform(vector: Sequence[float], matrix: NDArray[np.float64]) -> NDArray[np.float64]:
    vec = np.asarray(vector, dtype=np.float64)
    mat = np.asarray(matrix, dtype=np.float64)
    if vec.shape != (3,):
        raise ValueError(f"vector must have shape (3,), got {vec.shape}")
    if mat.shape != (3, 3):
        raise ValueError(f"matrix must have shape (3, 3), got {mat.shape}")
    return mat @ vec


def apply_deadzone(vector: Sequence[float], deadzone: float) -> NDArray[np.float64]:
    vec = np.asarray(vector, dtype=np.float64).copy()
    threshold = max(0.0, float(deadzone))
    if threshold <= 0.0:
        return vec
    vec[np.abs(vec) < threshold] = 0.0
    return vec


def base_xy_yaw_matrix(yaw_deg: float) -> NDArray[np.float64]:
    """Rotation about base +Z (degrees). Positive = CCW looking down +Z."""
    yaw_rad = np.deg2rad(float(yaw_deg))
    cosine = float(np.cos(yaw_rad))
    sine = float(np.sin(yaw_rad))
    return np.array(
        [[cosine, -sine, 0.0], [sine, cosine, 0.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )


def map_raw_axes_to_base(
    translation_raw: Sequence[float],
    rotation_raw: Sequence[float],
    *,
    config: SpaceMouseConfig,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Apply deadzone and mount transforms to raw stick axes."""
    translation_matrix = get_spacemouse_transform(config.mount_orientation, config.translation_transform)
    rotation_matrix = get_spacemouse_rotation_transform(config.mount_orientation, config.rotation_transform)
    translation = apply_transform(apply_deadzone(translation_raw, config.deadzone), translation_matrix)
    rotation = apply_transform(apply_deadzone(rotation_raw, config.deadzone), rotation_matrix)
    if abs(float(config.base_xy_yaw_deg)) > 1e-12:
        yaw = base_xy_yaw_matrix(config.base_xy_yaw_deg)
        translation = yaw @ translation
        rotation = yaw @ rotation
    if config.lock_z:
        translation = np.asarray([translation[0], translation[1], 0.0], dtype=np.float64)
    return translation, rotation


def spacemouse_axes_to_human_action(
    axes: SpaceMouseAxes,
    *,
    config: SpaceMouseConfig,
    timestamp_utc: datetime,
    gripper_target_open_fraction: float | None,
) -> HumanAction:
    """Convert mapped SpaceMouse axes into a schema ``HumanAction``."""
    stale = axes.input_age_ms > config.input_timeout_s * 1000.0
    if stale or not axes.deadman_active:
        linear = (0.0, 0.0, 0.0)
        angular = (0.0, 0.0, 0.0)
    else:
        xy_speed = (
            float(config.max_linear_speed_xy_m_s)
            if config.max_linear_speed_xy_m_s is not None
            else float(config.max_linear_speed_m_s)
        )
        z_speed = (
            float(config.max_linear_speed_z_m_s)
            if config.max_linear_speed_z_m_s is not None
            else float(config.max_linear_speed_m_s)
        )
        linear = (
            float(axes.translation[0]) * xy_speed,
            float(axes.translation[1]) * xy_speed,
            float(axes.translation[2]) * z_speed,
        )
        if config.allow_rotation:
            angular = tuple(float(value) * config.max_angular_speed_rad_s for value in axes.rotation)
        else:
            angular = (0.0, 0.0, 0.0)

    return HumanAction(
        timestamp=SampleTimestamp(
            timestamp_utc=timestamp_utc,
            received_monotonic_ns=axes.received_monotonic_ns,
        ),
        linear_velocity_m_s=linear,
        angular_velocity_rad_s=angular,
        gripper_target_open_fraction=gripper_target_open_fraction,
        deadman_active=bool(axes.deadman_active),
        input_age_ms=axes.input_age_ms,
        reference_frame=CoordinateFrame.BASE,
        gripper_button_edge=bool(axes.gripper_button_edge),
    )


class MockSpaceMouse:
    """Deterministic SpaceMouse source for offline runner tests."""

    def __init__(
        self,
        config: SpaceMouseConfig | None = None,
        *,
        translation_raw: Sequence[float] = (0.0, 0.0, 0.0),
        rotation_raw: Sequence[float] = (0.0, 0.0, 0.0),
        deadman_active: bool = True,
        gripper_target_open_fraction: float = 1.0,
        received_monotonic_ns: int = 0,
        input_age_ms: float = 0.0,
    ) -> None:
        self.config = config or SpaceMouseConfig()
        self.translation_raw = tuple(float(value) for value in translation_raw)
        self.rotation_raw = tuple(float(value) for value in rotation_raw)
        self.deadman_active = bool(deadman_active)
        self.gripper_target_open_fraction = float(gripper_target_open_fraction)
        self.received_monotonic_ns = int(received_monotonic_ns)
        self.input_age_ms = float(input_age_ms)
        self._gripper_button_edge = False

    def connect(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def set_raw_axes(
        self,
        *,
        translation_raw: Sequence[float] | None = None,
        rotation_raw: Sequence[float] | None = None,
        deadman_active: bool | None = None,
        gripper_target_open_fraction: float | None = None,
        received_monotonic_ns: int | None = None,
        input_age_ms: float | None = None,
        gripper_button_edge: bool = False,
    ) -> None:
        if translation_raw is not None:
            self.translation_raw = tuple(float(value) for value in translation_raw)
        if rotation_raw is not None:
            self.rotation_raw = tuple(float(value) for value in rotation_raw)
        if deadman_active is not None:
            self.deadman_active = bool(deadman_active)
        if gripper_target_open_fraction is not None:
            self.gripper_target_open_fraction = float(gripper_target_open_fraction)
        if received_monotonic_ns is not None:
            self.received_monotonic_ns = int(received_monotonic_ns)
        if input_age_ms is not None:
            self.input_age_ms = float(input_age_ms)
        self._gripper_button_edge = bool(gripper_button_edge)

    def read_axes(self, *, now_monotonic_ns: int) -> SpaceMouseAxes:
        age_ns = int(round(self.input_age_ms * 1_000_000.0))
        received_ns = max(0, int(now_monotonic_ns) - age_ns)
        translation, rotation = map_raw_axes_to_base(
            self.translation_raw,
            self.rotation_raw,
            config=self.config,
        )
        edge = self._gripper_button_edge
        self._gripper_button_edge = False
        return SpaceMouseAxes(
            translation=(float(translation[0]), float(translation[1]), float(translation[2])),
            rotation=(float(rotation[0]), float(rotation[1]), float(rotation[2])),
            deadman_active=self.deadman_active,
            gripper_button_edge=edge,
            received_monotonic_ns=received_ns,
            input_age_ms=self.input_age_ms,
        )

    def read_human_action(
        self,
        *,
        now_monotonic_ns: int,
        timestamp_utc: datetime | None = None,
    ) -> HumanAction:
        axes = self.read_axes(now_monotonic_ns=now_monotonic_ns)
        return spacemouse_axes_to_human_action(
            axes,
            config=self.config,
            timestamp_utc=timestamp_utc or datetime.now(tz=UTC),
            gripper_target_open_fraction=self.gripper_target_open_fraction,
        )


class SpaceMouseDevice:
    """Lazy pyspacemouse adapter. Offline imports must not require the package."""

    def __init__(self, config: SpaceMouseConfig | None = None) -> None:
        self.config = config or SpaceMouseConfig()
        self._pyspacemouse: Any = None
        self._device: Any = None
        self._gripper_open_fraction = 1.0
        self._last_button_1 = False

    def connect(self) -> None:
        try:
            import pyspacemouse  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on local hardware env
            raise RuntimeError(
                "Real SpaceMouse teleoperation requires pyspacemouse. "
                "Install it in the robot environment or use MockSpaceMouse offline."
            ) from exc
        self._pyspacemouse = pyspacemouse
        try:
            if self.config.device_path:
                self._device = pyspacemouse.open(path=self.config.device_path)
            else:
                self._device = pyspacemouse.open()
        except Exception as exc:  # pragma: no cover - hardware path
            raise RuntimeError("Failed to open SpaceMouse device.") from exc

    def disconnect(self) -> None:
        close = getattr(self._pyspacemouse, "close", None)
        if callable(close):
            close()
        self._device = None
        self._pyspacemouse = None

    def read_axes(self, *, now_monotonic_ns: int) -> SpaceMouseAxes:
        if self._pyspacemouse is None:
            raise RuntimeError("SpaceMouseDevice.connect() must be called before read_axes().")
        state = self._read_latest_state()
        received_ns = int(now_monotonic_ns)
        translation_raw = (
            float(getattr(state, "x", 0.0)),
            float(getattr(state, "y", 0.0)),
            float(getattr(state, "z", 0.0)),
        )
        rotation_raw = (
            float(getattr(state, "roll", 0.0)),
            float(getattr(state, "pitch", 0.0)),
            float(getattr(state, "yaw", 0.0)),
        )
        translation, rotation = map_raw_axes_to_base(
            translation_raw,
            rotation_raw,
            config=self.config,
        )
        button_0 = _button_pressed(state, 0)
        button_1 = _button_pressed(state, 1)
        gripper_edge = bool(button_1 and not self._last_button_1)
        if gripper_edge:
            self._gripper_open_fraction = 1.0 - self._gripper_open_fraction
        self._last_button_1 = bool(button_1)
        return SpaceMouseAxes(
            translation=(float(translation[0]), float(translation[1]), float(translation[2])),
            rotation=(float(rotation[0]), float(rotation[1]), float(rotation[2])),
            deadman_active=bool(button_0),
            gripper_button_edge=gripper_edge,
            received_monotonic_ns=received_ns,
            input_age_ms=0.0,
        )

    def read_human_action(
        self,
        *,
        now_monotonic_ns: int,
        timestamp_utc: datetime | None = None,
        input_age_ms: float | None = None,
    ) -> HumanAction:
        axes = self.read_axes(now_monotonic_ns=now_monotonic_ns)
        if input_age_ms is not None:
            axes = SpaceMouseAxes(
                translation=axes.translation,
                rotation=axes.rotation,
                deadman_active=axes.deadman_active,
                gripper_button_edge=axes.gripper_button_edge,
                received_monotonic_ns=axes.received_monotonic_ns,
                input_age_ms=float(input_age_ms),
            )
        return spacemouse_axes_to_human_action(
            axes,
            config=self.config,
            timestamp_utc=timestamp_utc or datetime.now(tz=UTC),
            gripper_target_open_fraction=self._gripper_open_fraction,
        )

    def _read_latest_state(self) -> Any:
        reader = (
            self._device if self._device is not None and hasattr(self._device, "read") else self._pyspacemouse
        )
        state = reader.read()
        latest = state
        last_t = getattr(state, "t", None)
        for _ in range(self.config.drain_max_reads - 1):
            candidate = reader.read()
            if candidate is None:
                break
            candidate_t = getattr(candidate, "t", None)
            if candidate_t == last_t:
                break
            latest = candidate
            last_t = candidate_t
        return latest


def _button_pressed(state: Any, index: int) -> bool:
    buttons = getattr(state, "buttons", None)
    if buttons is not None and len(buttons) > index:
        return bool(buttons[index])
    return bool(getattr(state, f"button_{index}", False))


def decode_int16_le(report: Sequence[int], offset: int) -> int:
    if offset < 0 or offset + 2 > len(report):
        raise ValueError("report does not contain the requested int16 value")
    return int.from_bytes(bytes(report[offset : offset + 2]), byteorder="little", signed=True)


def compact_hid_translation_to_legacy(hx: float, hy: float, hz: float) -> tuple[float, float, float]:
    """Match Compact pyspacemouse LEGACY signs before mount transforms.

    pyspacemouse Compact mappings invert HID Y/Z. ``vertical_up`` matrices were
    verified against that signed input, not raw HID.
    """
    return (float(hx), -float(hy), -float(hz))


def compact_hid_rotation_to_legacy(rx: float, ry: float, rz: float) -> tuple[float, float, float]:
    """Match Compact pyspacemouse (roll, pitch, yaw) from HID Rx/Ry/Rz."""
    # Compact: pitch=-Rx, roll=-Ry, yaw=+Rz; callers store (roll, pitch, yaw).
    return (-float(ry), -float(rx), float(rz))


def normalize_axis(raw_value: int, deadzone: float, full_scale: float = 350.0) -> float:
    if not 0 <= deadzone < 1:
        raise ValueError("deadzone must be in [0, 1)")
    if full_scale <= 0:
        raise ValueError("full_scale must be positive")
    normalized = max(-1.0, min(1.0, raw_value / full_scale))
    if abs(normalized) <= deadzone:
        return 0.0
    magnitude = (abs(normalized) - deadzone) / (1.0 - deadzone)
    return magnitude if normalized > 0 else -magnitude


def enumerate_spacemice(hid_module: Any) -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    seen_paths: set[bytes | str] = set()
    for device in hid_module.enumerate():
        identifier = (int(device["vendor_id"]), int(device["product_id"]))
        path = device.get("path")
        if identifier not in KNOWN_SPACEMOUSE_IDS or path in seen_paths:
            continue
        seen_paths.add(path)
        devices.append(device)
    return devices


@dataclass
class _HidMotionCache:
    translation_raw: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation_raw: tuple[float, float, float] = (0.0, 0.0, 0.0)
    translation_received_ns: int | None = None
    rotation_received_ns: int | None = None
    deadman_active: bool = False
    button_1: bool = False
    reader_error: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "translation_raw": self.translation_raw,
                "rotation_raw": self.rotation_raw,
                "translation_received_ns": self.translation_received_ns,
                "rotation_received_ns": self.rotation_received_ns,
                "deadman_active": self.deadman_active,
                "button_1": self.button_1,
                "reader_error": self.reader_error,
            }


class HidSpaceMouse:
    """Threaded hidapi SpaceMouse adapter used by the control loop.

    Compact report map (Day-1 verified), then Compact LEGACY signs like pyspacemouse:
    - ID 1: translation int16 LE at offsets 1/3/5 → (x, -y, -z)
    - ID 2: rotation int16 LE at offsets 1/3/5 → (roll, pitch, yaw)=(-Ry, -Rx, Rz)
    - ID 3: buttons; bit0 = deadman (left), bit1 = gripper toggle
    """

    def __init__(self, config: SpaceMouseConfig | None = None, *, device_index: int = 0) -> None:
        self.config = config or SpaceMouseConfig()
        self.device_index = int(device_index)
        self._hid_module: Any = None
        self._device: Any = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cache = _HidMotionCache()
        self._gripper_open_fraction = 1.0
        self._last_button_1 = False

    def connect(self) -> None:
        if self._thread is not None:
            raise RuntimeError("HidSpaceMouse is already connected")
        try:
            import hid
        except ImportError as exc:  # pragma: no cover - depends on hardware env
            raise RuntimeError(
                "Real SpaceMouse teleoperation requires hidapi. "
                "Install it in sharedautonomy-lr060-cf or use MockSpaceMouse offline."
            ) from exc
        self._hid_module = hid
        devices = enumerate_spacemice(hid)
        if not devices:
            raise RuntimeError("No supported SpaceMouse HID device was found")
        if not 0 <= self.device_index < len(devices):
            raise IndexError(f"device_index must be between 0 and {len(devices) - 1}")
        selected = devices[self.device_index]
        path = self.config.device_path or selected["path"]
        device = hid.device()
        device.open_path(path)
        device.set_nonblocking(0)
        self._device = device
        self._stop.clear()
        self._thread = threading.Thread(target=self._read_loop, name="spacemouse-hid-reader", daemon=True)
        self._thread.start()

    def disconnect(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=(self.config.hid_timeout_ms / 1000.0) + 1.0)
            self._thread = None
        if self._device is not None:
            try:
                self._device.close()
            finally:
                self._device = None
        self._hid_module = None

    def read_axes(self, *, now_monotonic_ns: int) -> SpaceMouseAxes:
        if self._thread is None:
            raise RuntimeError("HidSpaceMouse.connect() must be called before read_axes().")
        snap = self._cache.snapshot()
        if snap["reader_error"] is not None:
            raise RuntimeError(f"SpaceMouse HID reader failed: {snap['reader_error']}")

        translation_raw = snap["translation_raw"]
        rotation_raw = snap["rotation_raw"]
        translation, rotation = map_raw_axes_to_base(
            translation_raw,
            rotation_raw,
            config=self.config,
        )

        ages_ns = [
            int(now_monotonic_ns) - int(stamp)
            for stamp in (snap["translation_received_ns"], snap["rotation_received_ns"])
            if stamp is not None
        ]
        if ages_ns:
            # Complete-state age uses the older of the latest translation/rotation reports.
            age_ns = max(ages_ns)
            received_ns = int(now_monotonic_ns) - age_ns
        else:
            age_ns = int(round(self.config.input_timeout_s * 1_000_000_000)) + 1
            received_ns = max(0, int(now_monotonic_ns) - age_ns)

        button_1 = bool(snap["button_1"])
        gripper_edge = bool(button_1 and not self._last_button_1)
        if gripper_edge:
            self._gripper_open_fraction = 1.0 - self._gripper_open_fraction
        self._last_button_1 = button_1

        return SpaceMouseAxes(
            translation=(float(translation[0]), float(translation[1]), float(translation[2])),
            rotation=(float(rotation[0]), float(rotation[1]), float(rotation[2])),
            deadman_active=bool(snap["deadman_active"]),
            gripper_button_edge=gripper_edge,
            received_monotonic_ns=received_ns,
            input_age_ms=age_ns / 1_000_000.0,
        )

    def read_human_action(
        self,
        *,
        now_monotonic_ns: int,
        timestamp_utc: datetime | None = None,
    ) -> HumanAction:
        axes = self.read_axes(now_monotonic_ns=now_monotonic_ns)
        return spacemouse_axes_to_human_action(
            axes,
            config=self.config,
            timestamp_utc=timestamp_utc or datetime.now(tz=UTC),
            gripper_target_open_fraction=self._gripper_open_fraction,
        )

    def _read_loop(self) -> None:
        assert self._device is not None
        try:
            while not self._stop.is_set():
                report = self._device.read(64, self.config.hid_timeout_ms)
                if not report:
                    continue
                received_ns = time.perf_counter_ns()
                self._ingest_report(report, received_monotonic_ns=received_ns)
        except Exception as exc:  # pragma: no cover - hardware path
            with self._cache._lock:
                self._cache.reader_error = repr(exc)

    def _ingest_report(self, report: Sequence[int], *, received_monotonic_ns: int) -> None:
        if not report:
            return
        report_id = int(report[0])
        full_scale = self.config.axis_full_scale
        with self._cache._lock:
            if report_id == 1 and len(report) >= 7:
                hx = normalize_axis(decode_int16_le(report, 1), 0.0, full_scale)
                hy = normalize_axis(decode_int16_le(report, 3), 0.0, full_scale)
                hz = normalize_axis(decode_int16_le(report, 5), 0.0, full_scale)
                self._cache.translation_raw = compact_hid_translation_to_legacy(hx, hy, hz)
                self._cache.translation_received_ns = int(received_monotonic_ns)
            elif report_id == 2 and len(report) >= 7:
                rx = normalize_axis(decode_int16_le(report, 1), 0.0, full_scale)
                ry = normalize_axis(decode_int16_le(report, 3), 0.0, full_scale)
                rz = normalize_axis(decode_int16_le(report, 5), 0.0, full_scale)
                self._cache.rotation_raw = compact_hid_rotation_to_legacy(rx, ry, rz)
                self._cache.rotation_received_ns = int(received_monotonic_ns)
            elif report_id == 3 and len(report) >= 2:
                buttons = int(report[1])
                self._cache.deadman_active = bool(buttons & 0x01)
                self._cache.button_1 = bool(buttons & 0x02)
