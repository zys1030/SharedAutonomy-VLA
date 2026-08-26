"""Glue between the manual Cartesian runner and synchronized observations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sharedautonomy.control.manual import CartesianRobotState, RobotStateSource
from sharedautonomy.data.sync import (
    ObservationSyncConfig,
    ObservationSynchronizer,
    ProprioceptiveSample,
    proprioceptive_sample_from_cartesian,
)
from sharedautonomy.devices.cameras import (
    CameraSession,
    MockRgbCamera,
    MockRgbdCamera,
    RealSenseRgbdCamera,
    UvcRgbCamera,
)
from sharedautonomy.devices.uvc_resolve import build_resolved_uvc_opencv_index


class CartesianProprioceptiveSource:
    """Expose the latest Cartesian robot state to ``ObservationSynchronizer``."""

    def __init__(self, robot_state_source: RobotStateSource) -> None:
        self._robot_state_source = robot_state_source
        self._latest: ProprioceptiveSample | None = None

    def prime(self, *, now_monotonic_ns: int) -> ProprioceptiveSample:
        robot_state = self._robot_state_source.read_cartesian_state(now_monotonic_ns=now_monotonic_ns)
        self._latest = proprioceptive_sample_from_cartesian(
            timestamp=robot_state.timestamp,
            joint_position_deg=robot_state.joint_position_deg,
            ee_position_m=robot_state.ee_position_m,
            ee_rpy_rad=robot_state.ee_rpy_rad,
            robot_state_age_ms=robot_state.robot_state_age_ms,
        )
        return self._latest

    def set_from_cartesian_state(self, robot_state: CartesianRobotState) -> ProprioceptiveSample:
        self._latest = proprioceptive_sample_from_cartesian(
            timestamp=robot_state.timestamp,
            joint_position_deg=robot_state.joint_position_deg,
            ee_position_m=robot_state.ee_position_m,
            ee_rpy_rad=robot_state.ee_rpy_rad,
            robot_state_age_ms=robot_state.robot_state_age_ms,
        )
        return self._latest

    def read_proprioception(self, *, now_monotonic_ns: int) -> ProprioceptiveSample:
        del now_monotonic_ns
        if self._latest is None:
            raise RuntimeError("Cartesian proprioception has not been primed for this control step")
        return self._latest


@dataclass(frozen=True, slots=True)
class CameraRuntimeConfig:
    wrist: dict[str, Any] | None = None
    external: dict[str, Any] | None = None
    sync: dict[str, Any] | None = None


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load camera local configs") from exc
    with path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a mapping")
    return payload


def load_camera_runtime_config(
    *,
    wrist_config_path: str | Path | None = None,
    external_config_path: str | Path | None = None,
) -> CameraRuntimeConfig:
    wrist_path = Path(wrist_config_path or "configs/local/wrist_realsense.local.yaml")
    external_path = Path(external_config_path or "configs/local/external_rgb.local.yaml")
    wrist_payload = _load_yaml_mapping(wrist_path) if wrist_path.is_file() else None
    external_payload = _load_yaml_mapping(external_path) if external_path.is_file() else None
    return CameraRuntimeConfig(
        wrist=None if wrist_payload is None else wrist_payload.get("camera"),
        external=None if external_payload is None else external_payload.get("camera"),
        sync=None if external_payload is None else external_payload.get("sync"),
    )


def build_camera_session_from_config(
    config: CameraRuntimeConfig,
) -> tuple[CameraSession, ObservationSyncConfig]:
    wrist_camera = None
    external_camera = None
    if config.wrist is not None:
        wrist_camera = RealSenseRgbdCamera(
            width=int(config.wrist.get("width", 640)),
            height=int(config.wrist.get("height", 480)),
            fps=int(config.wrist.get("fps", 30)),
            serial_number=config.wrist.get("serial_number"),
            warmup_frames=int(config.wrist.get("warmup_frames", 60)),
        )
    if config.external is not None:
        resolved_index = build_resolved_uvc_opencv_index(
            friendly_name=config.external.get("friendly_name"),
            device_name_contains=config.external.get("device_name_contains"),
            vendor_id=config.external.get("vendor_id"),
            product_id=config.external.get("product_id"),
            opencv_index_hint=config.external.get("opencv_index_hint"),
            opencv_index=config.external.get("opencv_index"),
        )
        external_camera = UvcRgbCamera(
            width=int(config.external.get("width", 640)),
            height=int(config.external.get("height", 480)),
            fps=int(config.external.get("fps", 30)),
            opencv_index=resolved_index,
            friendly_name=config.external.get("friendly_name"),
            device_name_contains=config.external.get("device_name_contains"),
            vendor_id=config.external.get("vendor_id"),
            product_id=config.external.get("product_id"),
            opencv_index_hint=config.external.get("opencv_index_hint"),
            opencv_backend=config.external.get("opencv_backend", "dshow"),
            warmup_frames=int(config.external.get("warmup_frames", 60)),
        )
    sync_payload = config.sync or {}
    sync_config = ObservationSyncConfig(
        max_wrist_camera_age_ms=sync_payload.get("max_wrist_camera_age_ms", 100.0),
        max_external_camera_age_ms=sync_payload.get("max_external_camera_age_ms", 100.0),
        require_wrist_camera=bool(sync_payload.get("require_wrist_camera", False)),
        require_external_camera=bool(sync_payload.get("require_external_camera", False)),
        drop_stale_cameras=bool(sync_payload.get("drop_stale_cameras", False)),
    )
    return CameraSession(wrist_camera=wrist_camera, external_camera=external_camera), sync_config


def build_mock_camera_session() -> CameraSession:
    return CameraSession(wrist_camera=MockRgbdCamera(), external_camera=MockRgbCamera())


def build_observation_synchronizer(
    *,
    proprioception: CartesianProprioceptiveSource,
    camera_session: CameraSession,
    sync_config: ObservationSyncConfig | None = None,
) -> ObservationSynchronizer:
    return ObservationSynchronizer(
        proprioception,
        wrist_camera=camera_session.wrist_camera,
        external_camera=camera_session.external_camera,
        config=sync_config or ObservationSyncConfig(),
    )
