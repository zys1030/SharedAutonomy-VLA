"""Background camera sources for synchronized observations.

Hardware SDKs are lazy-imported so offline imports and unit tests stay usable
without RealSense or OpenCV installed.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np

from sharedautonomy.data.schema import CameraFrame, SampleTimestamp

logger = logging.getLogger(__name__)


def _bgr_to_rgb(color_bgr: np.ndarray) -> np.ndarray:
    if color_bgr.ndim != 3 or color_bgr.shape[2] != 3:
        raise ValueError("color_bgr must have shape (height, width, 3)")
    return np.ascontiguousarray(color_bgr[:, :, ::-1])


@dataclass
class _LatestFrameBuffer:
    frame: CameraFrame | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def publish(self, frame: CameraFrame) -> None:
        with self._lock:
            self.frame = frame

    def read(self) -> CameraFrame | None:
        with self._lock:
            return self.frame


@dataclass
class MockRgbdCamera:
    """Deterministic RGB-D source for offline tests."""

    frame: CameraFrame | None = None

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def read_camera(self, *, now_monotonic_ns: int) -> CameraFrame | None:
        del now_monotonic_ns
        return self.frame


@dataclass
class MockRgbCamera:
    """Deterministic RGB-only source for offline tests."""

    frame: CameraFrame | None = None

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def read_camera(self, *, now_monotonic_ns: int) -> CameraFrame | None:
        del now_monotonic_ns
        return self.frame


@dataclass
class RealSenseRgbdCamera:
    """Wrist RGB-D camera backed by a background RealSense capture thread."""

    width: int = 640
    height: int = 480
    fps: int = 30
    serial_number: str | None = None
    warmup_frames: int = 60
    depth_scale_m_per_unit: float = 0.001

    _buffer: _LatestFrameBuffer = field(default_factory=_LatestFrameBuffer, init=False, repr=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _pipeline: Any = field(default=None, init=False, repr=False)

    def start(self) -> None:
        if self._thread is not None:
            return
        try:
            import pyrealsense2 as rs
        except ImportError as exc:
            raise RuntimeError("pyrealsense2 is required for RealSenseRgbdCamera") from exc

        pipeline = rs.pipeline()
        config = rs.config()
        if self.serial_number:
            config.enable_device(self.serial_number)
        config.enable_stream(rs.stream.depth, self.width, self.height, rs.format.z16, self.fps)
        config.enable_stream(rs.stream.color, self.width, self.height, rs.format.bgr8, self.fps)
        self._pipeline = pipeline
        pipeline.start(config)

        for _ in range(max(0, int(self.warmup_frames))):
            pipeline.wait_for_frames(timeout_ms=2000)

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name="realsense-rgbd-capture",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        if self._pipeline is not None:
            self._pipeline.stop()
            self._pipeline = None

    def read_camera(self, *, now_monotonic_ns: int) -> CameraFrame | None:
        del now_monotonic_ns
        return self._buffer.read()

    def _capture_loop(self) -> None:
        assert self._pipeline is not None
        while not self._stop_event.is_set():
            try:
                frames = self._pipeline.wait_for_frames(timeout_ms=2000)
            except RuntimeError:
                continue
            depth = frames.get_depth_frame()
            color = frames.get_color_frame()
            if not depth or not color:
                continue
            arrival_ns = time.perf_counter_ns()
            color_bgr = np.asanyarray(color.get_data())
            depth_raw = np.asanyarray(depth.get_data())
            if depth_raw.dtype != np.uint16:
                depth_raw = depth_raw.astype(np.uint16, copy=False)
            frame = CameraFrame(
                timestamp=SampleTimestamp(
                    timestamp_utc=datetime.now(tz=UTC),
                    received_monotonic_ns=arrival_ns,
                    device_timestamp_ms=float(color.get_timestamp()),
                    device_clock_domain=str(color.get_frame_timestamp_domain()),
                    sequence_number=int(color.get_frame_number()),
                ),
                color_rgb=_bgr_to_rgb(color_bgr),
                depth_raw=depth_raw,
                depth_scale_m_per_unit=float(self.depth_scale_m_per_unit),
            )
            self._buffer.publish(frame)


@dataclass
class UvcRgbCamera:
    """External RGB-only camera backed by a background OpenCV capture thread."""

    width: int = 640
    height: int = 480
    fps: int = 30
    opencv_index: int = 0
    warmup_frames: int = 60

    _buffer: _LatestFrameBuffer = field(default_factory=_LatestFrameBuffer, init=False, repr=False)
    _stop_event: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _capture: Any = field(default=None, init=False, repr=False)

    def start(self) -> None:
        if self._thread is not None:
            return
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("opencv-python (cv2) is required for UvcRgbCamera") from exc

        backend = cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else cv2.CAP_ANY
        capture = cv2.VideoCapture(int(self.opencv_index), backend)
        if not capture.isOpened():
            capture.release()
            raise RuntimeError(f"Failed to open UVC camera at OpenCV index {self.opencv_index}")
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, float(self.width))
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self.height))
        capture.set(cv2.CAP_PROP_FPS, float(self.fps))
        self._capture = capture

        for _ in range(max(0, int(self.warmup_frames))):
            capture.read()

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._capture_loop,
            name="uvc-rgb-capture",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def read_camera(self, *, now_monotonic_ns: int) -> CameraFrame | None:
        del now_monotonic_ns
        return self._buffer.read()

    def _capture_loop(self) -> None:
        assert self._capture is not None
        sequence_number = 0
        while not self._stop_event.is_set():
            ok, color_bgr = self._capture.read()
            if not ok or color_bgr is None:
                continue
            arrival_ns = time.perf_counter_ns()
            frame = CameraFrame(
                timestamp=SampleTimestamp(
                    timestamp_utc=datetime.now(tz=UTC),
                    received_monotonic_ns=arrival_ns,
                    sequence_number=sequence_number,
                ),
                color_rgb=_bgr_to_rgb(color_bgr),
                depth_raw=None,
            )
            sequence_number += 1
            self._buffer.publish(frame)


@dataclass
class CameraSession:
    """Start and stop a wrist/external camera pair as one unit."""

    wrist_camera: RealSenseRgbdCamera | MockRgbdCamera | None = None
    external_camera: UvcRgbCamera | MockRgbCamera | None = None

    def start(self) -> None:
        if self.wrist_camera is not None:
            logger.info("Starting wrist RGB-D camera")
            self.wrist_camera.start()
        if self.external_camera is not None:
            logger.info("Starting external RGB camera")
            self.external_camera.start()

    def stop(self) -> None:
        if self.external_camera is not None:
            self.external_camera.stop()
        if self.wrist_camera is not None:
            self.wrist_camera.stop()
