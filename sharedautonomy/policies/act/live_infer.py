"""Shared helpers for live robot observations and cloud ACT HTTP infer."""

from __future__ import annotations

import http.client
import json
import socket
import time
from typing import Any
from urllib.parse import urlparse

import numpy as np

from sharedautonomy.control.observation import CartesianProprioceptiveSource
from sharedautonomy.policies.act.protocol import DEFAULT_IMAGE_HWC, InferObservation

_IMAGE_H, _IMAGE_W, _IMAGE_C = DEFAULT_IMAGE_HWC


class NoDelayHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection with TCP_NODELAY (disable Nagle for small RPC payloads)."""

    def connect(self) -> None:
        super().connect()
        if self.sock is not None:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


class JsonHttpClient:
    """Persistent-connection JSON client (HTTP/1.1 keep-alive)."""

    def __init__(self, base_url: str, *, timeout_s: float) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or not parsed.hostname:
            raise ValueError(f"only http:// URLs are supported, got {base_url!r}")
        self._host = parsed.hostname
        self._port = parsed.port or 80
        self._base_url = base_url.rstrip("/")
        self._timeout_s = float(timeout_s)
        self._conn: http.client.HTTPConnection | None = None

    def _connection(self, timeout_s: float) -> http.client.HTTPConnection:
        if self._conn is None:
            self._conn = NoDelayHTTPConnection(self._host, self._port, timeout=timeout_s)
        elif self._conn.sock is not None:
            self._conn.sock.settimeout(timeout_s)
        else:
            self._conn.timeout = timeout_s
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None,
        timeout_s: float,
    ) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"} if body is not None else {}
        raw = b""
        status = 0
        for attempt in (0, 1):
            conn = self._connection(timeout_s)
            try:
                conn.request(method, path, body=body, headers=headers)
                response = conn.getresponse()
                status = response.status
                raw = response.read()
                break
            except (http.client.HTTPException, OSError) as exc:
                self.close()
                if attempt == 1:
                    raise RuntimeError(
                        f"Failed to reach {self._base_url}{path}: {exc}"
                    ) from exc
        if status != 200:
            detail = raw.decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {status} from {self._base_url}{path}: {detail}")
        result = json.loads(raw.decode("utf-8"))
        if not isinstance(result, dict):
            raise RuntimeError(f"Expected JSON object from {self._base_url}{path}")
        return result

    def get(self, path: str, *, timeout_s: float) -> dict[str, Any]:
        return self._request("GET", path, payload=None, timeout_s=timeout_s)

    def post(self, path: str, payload: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        return self._request("POST", path, payload=payload, timeout_s=timeout_s)


def ensure_hwc_rgb_uint8(image: np.ndarray, *, height: int, width: int) -> np.ndarray:
    array = np.asarray(image)
    if array.dtype != np.uint8 or array.ndim != 3 or array.shape[2] != 3:
        raise ValueError(f"expected HWC uint8 RGB, got dtype={array.dtype} shape={array.shape}")
    if array.shape[0] == height and array.shape[1] == width:
        return np.ascontiguousarray(array)
    import cv2

    resized = cv2.resize(array, (width, height), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(resized)


def both_rgb_present(synced: Any) -> bool:
    observation = synced.observation
    return observation.wrist_camera is not None and observation.external_camera is not None


def wait_for_both_cameras(
    *,
    proprio_source: CartesianProprioceptiveSource,
    synchronizer: Any,
    gripper_open_fraction: float,
    timeout_s: float,
    poll_s: float = 0.05,
) -> Any:
    """Block until wrist+external frames are buffered (RealSense thread publish race)."""
    if timeout_s < 0.0:
        raise ValueError("camera-ready-timeout-s must be >= 0")
    deadline = time.perf_counter() + float(timeout_s)
    last_warnings: list[str] = []
    attempts = 0
    while True:
        now_ns = time.perf_counter_ns()
        proprio_source.prime(now_monotonic_ns=now_ns)
        synced = synchronizer.capture(
            now_monotonic_ns=now_ns,
            gripper_commanded_open_fraction=float(gripper_open_fraction),
        )
        attempts += 1
        last_warnings = list(synced.warnings)
        if both_rgb_present(synced):
            print(
                json.dumps(
                    {
                        "cameras_ready": True,
                        "attempts": attempts,
                        "wrist_age_ms": synced.wrist_age_ms,
                        "external_age_ms": synced.external_age_ms,
                    }
                ),
                flush=True,
            )
            return synced
        if time.perf_counter() >= deadline:
            wrist = synced.observation.wrist_camera is not None
            external = synced.observation.external_camera is not None
            raise RuntimeError(
                "Timed out waiting for wrist+external RGB after camera start. "
                f"wrist={'ok' if wrist else 'missing'} external={'ok' if external else 'missing'} "
                f"attempts={attempts} warnings={last_warnings}"
            )
        time.sleep(poll_s)


def build_infer_observation(
    *,
    synced: Any,
    task: str,
    gripper_open_fraction: float,
    reset: bool,
) -> InferObservation:
    observation = synced.observation
    if observation.wrist_camera is None or observation.external_camera is None:
        raise RuntimeError(
            "Both wrist and external RGB frames are required for ACT infer; "
            f"wrist={'ok' if observation.wrist_camera else 'missing'} "
            f"external={'ok' if observation.external_camera else 'missing'} "
            f"warnings={list(synced.warnings)}"
        )
    joints = np.asarray(observation.joint_position_deg, dtype=np.float32).reshape(6)
    state = np.concatenate([joints, np.asarray([float(gripper_open_fraction)], dtype=np.float32)])
    return InferObservation(
        state=state,
        wrist_rgb_hwc=ensure_hwc_rgb_uint8(
            observation.wrist_camera.color_rgb,
            height=_IMAGE_H,
            width=_IMAGE_W,
        ),
        external_rgb_hwc=ensure_hwc_rgb_uint8(
            observation.external_camera.color_rgb,
            height=_IMAGE_H,
            width=_IMAGE_W,
        ),
        task=task,
        reset=reset,
    )
