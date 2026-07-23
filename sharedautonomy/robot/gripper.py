"""Gripper adapters used by the RM-65B integration."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class GripperDirection(IntEnum):
    """Direction field used by the legacy serial soft-gripper protocol."""

    OPEN = 0
    CLOSE = 1


class RealManControllerGripper:
    """Continuous-position gripper connected through the RealMan controller."""

    def __init__(
        self,
        arm: Any,
        *,
        position_min: int = 1,
        position_max: int = 1000,
        block: bool = False,
        timeout_s: int = 0,
    ) -> None:
        if position_min >= position_max:
            raise ValueError("position_min must be smaller than position_max")
        self._arm = arm
        self.position_min = int(position_min)
        self.position_max = int(position_max)
        self.block = bool(block)
        self.timeout_s = int(timeout_s)

    def prepare_position(self, position: float) -> int:
        """Convert a requested position to the controller range."""
        value = float(position)
        if not math.isfinite(value):
            raise ValueError("Gripper position must be finite")
        return min(max(round(value), self.position_min), self.position_max)

    def set_position(self, position: float) -> int:
        """Command a continuous gripper position and return the value sent."""
        command = self.prepare_position(position)
        status = self._arm.rm_set_gripper_position(command, self.block, self.timeout_s)
        if status != 0:
            raise RuntimeError(f"RealMan gripper position command failed with status {status}")
        return command

    def get_position(self) -> float:
        """Read the gripper's reported actuator position."""
        status, state = self._arm.rm_get_gripper_state()
        if status != 0:
            raise RuntimeError(f"RealMan gripper state read failed with status {status}")
        if "actpos" not in state:
            raise RuntimeError("RealMan gripper state does not contain 'actpos'")
        return float(state["actpos"])


@dataclass(frozen=True)
class SerialSoftGripperConfig:
    """Connection and protocol settings for the legacy serial soft gripper."""

    port: str
    baudrate: int = 115200
    timeout_s: float = 0.5
    response_delay_s: float = 0.1
    device_address: int = 0x01
    subdivision: int = 0x20


class SerialSoftGripper:
    """Legacy write-only serial adapter retained from the previous project."""

    FRAME_HEAD = 0x7B
    FRAME_TAIL = 0x7D
    POSITION_CONTROL_MODE = 0x02

    def __init__(self, config: SerialSoftGripperConfig, *, serial_factory: Any | None = None) -> None:
        self.config = config
        self._serial_factory = serial_factory
        self._serial: Any | None = None

    @property
    def is_connected(self) -> bool:
        return self._serial is not None and bool(getattr(self._serial, "is_open", False))

    def connect(self) -> None:
        if self.is_connected:
            raise RuntimeError("Serial soft gripper is already connected")
        if self._serial_factory is None:
            try:
                import serial
            except ImportError as exc:
                raise ImportError(
                    "Serial soft gripper support requires pyserial. Install the project's hardware extra."
                ) from exc
            self._serial_factory = serial.Serial

        self._serial = self._serial_factory(
            port=self.config.port,
            baudrate=self.config.baudrate,
            bytesize=8,
            parity="N",
            stopbits=1,
            timeout=self.config.timeout_s,
        )
        if not self.is_connected:
            self._serial = None
            raise ConnectionError(f"Failed to open serial soft gripper on {self.config.port}")

    def disconnect(self) -> None:
        if self._serial is not None and bool(getattr(self._serial, "is_open", False)):
            self._serial.close()
        self._serial = None

    def send_motion(
        self,
        direction: GripperDirection,
        *,
        angle_deg: float,
        speed_rad_s: float,
    ) -> bytes:
        """Send one legacy motion frame and return any immediate response bytes."""
        if not self.is_connected:
            raise ConnectionError("Serial soft gripper is not connected")

        command = self.build_command(
            direction,
            angle_deg=angle_deg,
            speed_rad_s=speed_rad_s,
            device_address=self.config.device_address,
            subdivision=self.config.subdivision,
        )
        self._serial.write(command)
        if self.config.response_delay_s > 0:
            time.sleep(self.config.response_delay_s)
        return bytes(self._serial.read_all())

    @classmethod
    def build_command(
        cls,
        direction: GripperDirection,
        *,
        angle_deg: float,
        speed_rad_s: float,
        device_address: int = 0x01,
        subdivision: int = 0x20,
    ) -> bytes:
        """Encode one command using the existing 0x7B ... BCC ... 0x7D protocol."""
        angle_scaled = cls._scaled_uint16(angle_deg, "angle_deg")
        speed_scaled = cls._scaled_uint16(speed_rad_s, "speed_rad_s")
        body = [
            cls.FRAME_HEAD,
            cls._uint8(device_address, "device_address"),
            cls.POSITION_CONTROL_MODE,
            int(GripperDirection(direction)),
            cls._uint8(subdivision, "subdivision"),
            (angle_scaled >> 8) & 0xFF,
            angle_scaled & 0xFF,
            (speed_scaled >> 8) & 0xFF,
            speed_scaled & 0xFF,
        ]
        checksum = 0
        for value in body:
            checksum ^= value
        return bytes([*body, checksum, cls.FRAME_TAIL])

    @staticmethod
    def _scaled_uint16(value: float, name: str) -> int:
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0:
            raise ValueError(f"{name} must be a finite non-negative value")
        scaled = round(numeric * 10)
        if scaled > 0xFFFF:
            raise ValueError(f"{name} is too large for the legacy protocol")
        return scaled

    @staticmethod
    def _uint8(value: int, name: str) -> int:
        numeric = int(value)
        if not 0 <= numeric <= 0xFF:
            raise ValueError(f"{name} must be in [0, 255]")
        return numeric
