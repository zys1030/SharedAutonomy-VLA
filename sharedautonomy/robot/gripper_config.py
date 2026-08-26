"""Load machine-local serial soft-gripper settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sharedautonomy.robot.gripper import (
    SerialSoftGripper,
    SerialSoftGripperConfig,
    SerialSoftGripperTeleop,
    SerialSoftGripperTeleopConfig,
)


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load gripper local configs") from exc
    with path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a mapping")
    return payload


def load_serial_soft_gripper_stack(
    *,
    config_path: str | Path | None = None,
) -> tuple[SerialSoftGripper, SerialSoftGripperTeleop, str]:
    """Load, connect, and return the machine-local serial gripper teleop stack.

    Returns ``(gripper, teleop_actuator, config_source)``. Caller must call
    ``gripper.disconnect()`` when finished.
    """
    path = Path(config_path or "configs/local/gripper_serial.local.yaml")
    if not path.is_file():
        example = Path("configs/robot/gripper_serial.example.yaml")
        raise FileNotFoundError(
            f"Serial gripper config not found: {path}. "
            f"Copy {example} to {path} and set the COM port."
        )
    payload = _load_yaml_mapping(path)
    serial_payload = payload.get("serial")
    teleop_payload = payload.get("teleop")
    if not isinstance(serial_payload, dict):
        raise ValueError(f"{path} must contain a 'serial' mapping")
    port = serial_payload.get("port")
    if not port or not str(port).strip():
        raise ValueError(f"{path} serial.port must be set")

    gripper = SerialSoftGripper(
        SerialSoftGripperConfig(
            port=str(port),
            baudrate=int(serial_payload.get("baudrate", 115200)),
            timeout_s=float(serial_payload.get("timeout_s", 0.5)),
            response_delay_s=float(serial_payload.get("response_delay_s", 0.1)),
            device_address=int(serial_payload.get("device_address", 0x01)),
            subdivision=int(serial_payload.get("subdivision", 0x20)),
        )
    )
    teleop_config = SerialSoftGripperTeleopConfig(
        open_angle_deg=float((teleop_payload or {}).get("open_angle_deg", 1800.0)),
        close_angle_deg=float((teleop_payload or {}).get("close_angle_deg", 1872.0)),
        speed_rad_s=float((teleop_payload or {}).get("speed_rad_s", 20.0)),
        initial_open_fraction=float((teleop_payload or {}).get("initial_open_fraction", 1.0)),
        working_open_fraction=float((teleop_payload or {}).get("working_open_fraction", 1.0)),
    )
    gripper.connect()
    return gripper, SerialSoftGripperTeleop(gripper, teleop_config), str(path)
