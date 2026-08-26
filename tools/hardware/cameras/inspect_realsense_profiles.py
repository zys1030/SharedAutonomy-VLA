"""Inspect a connected RealSense camera without exposing its serial number."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import defaultdict
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "List RealSense device metadata, sensor stream profiles, depth scale, and video "
            "intrinsics. The camera serial number is intentionally omitted."
        )
    )
    parser.add_argument(
        "--duration-s",
        type=float,
        default=0.0,
        help="Also test synchronized depth and color streaming for this many seconds",
    )
    parser.add_argument(
        "--warmup-s",
        type=float,
        default=2.0,
        help="Discard synchronized frames for this many seconds before measurement",
    )
    parser.add_argument("--width", type=int, default=640, help="Test stream width")
    parser.add_argument("--height", type=int, default=480, help="Test stream height")
    parser.add_argument("--fps", type=int, default=30, help="Test stream frame rate")
    return parser.parse_args()


def safe_camera_info(device: Any, rs: Any) -> dict[str, str]:
    fields = {
        "name": rs.camera_info.name,
        "product_line": rs.camera_info.product_line,
        "product_id": rs.camera_info.product_id,
        "firmware_version": rs.camera_info.firmware_version,
        "recommended_firmware_version": rs.camera_info.recommended_firmware_version,
        "usb_type_descriptor": rs.camera_info.usb_type_descriptor,
    }
    result: dict[str, str] = {}
    for name, field in fields.items():
        if device.supports(field):
            result[name] = device.get_info(field)
    return result


def profile_record(profile: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "stream": str(profile.stream_type()),
        "format": str(profile.format()),
        "fps": profile.fps(),
        "index": profile.stream_index(),
    }
    if profile.is_video_stream_profile():
        video = profile.as_video_stream_profile()
        record.update(width=video.width(), height=video.height())
    elif profile.is_motion_stream_profile():
        motion = profile.as_motion_stream_profile()
        intrinsics = motion.get_motion_intrinsics()
        record["motion_intrinsics"] = {
            "data": intrinsics.data,
            "noise_variances": intrinsics.noise_variances,
            "bias_variances": intrinsics.bias_variances,
        }
    return record


def video_intrinsics(profile: Any) -> dict[str, Any] | None:
    if not profile.is_video_stream_profile():
        return None
    video = profile.as_video_stream_profile()
    try:
        intrinsics = video.get_intrinsics()
    except RuntimeError:
        return None
    return {
        "stream": str(profile.stream_type()),
        "format": str(profile.format()),
        "fps": profile.fps(),
        "index": profile.stream_index(),
        "width": intrinsics.width,
        "height": intrinsics.height,
        "fx": intrinsics.fx,
        "fy": intrinsics.fy,
        "ppx": intrinsics.ppx,
        "ppy": intrinsics.ppy,
        "model": str(intrinsics.model),
        "coeffs": intrinsics.coeffs,
    }


def inspect_device(
    device: Any,
    rs: Any,
    preferred_width: int,
    preferred_height: int,
    preferred_fps: int,
) -> dict[str, Any]:
    sensor_records: list[dict[str, Any]] = []
    default_intrinsics: list[dict[str, Any]] = []
    depth_scale: float | None = None

    for sensor in device.query_sensors():
        sensor_name = (
            sensor.get_info(rs.camera_info.name)
            if sensor.supports(rs.camera_info.name)
            else "<unknown>"
        )
        grouped_profiles: dict[tuple[Any, ...], set[int]] = defaultdict(set)
        for profile in sensor.get_stream_profiles():
            record = profile_record(profile)
            key = (
                record["stream"],
                record["format"],
                record.get("width"),
                record.get("height"),
                record["index"],
            )
            grouped_profiles[key].add(record["fps"])

        profiles = [
            {
                "stream": key[0],
                "format": key[1],
                "width": key[2],
                "height": key[3],
                "index": key[4],
                "fps": sorted(fps_values),
            }
            for key, fps_values in sorted(grouped_profiles.items(), key=lambda item: str(item[0]))
        ]

        candidates = sorted(
            sensor.get_stream_profiles(),
            key=lambda profile: (
                not (
                    profile.is_video_stream_profile()
                    and profile.as_video_stream_profile().width() == preferred_width
                    and profile.as_video_stream_profile().height() == preferred_height
                    and profile.fps() == preferred_fps
                ),
                str(profile.stream_type()),
                str(profile.format()),
            ),
        )
        for candidate in candidates:
            intrinsics = video_intrinsics(candidate)
            if intrinsics is not None:
                intrinsics["sensor"] = sensor_name
                default_intrinsics.append(intrinsics)
                break

        if sensor.is_depth_sensor():
            depth_scale = sensor.as_depth_sensor().get_depth_scale()

        sensor_records.append({"name": sensor_name, "profiles": profiles})

    return {
        "device": safe_camera_info(device, rs),
        "depth_scale_m_per_unit": depth_scale,
        "default_profile_intrinsics": default_intrinsics,
        "sensors": sensor_records,
    }


def summarize_frames(frame_numbers: list[int], timestamps_ms: list[float]) -> dict[str, Any]:
    intervals_ms = [
        current - previous
        for previous, current in zip(timestamps_ms, timestamps_ms[1:], strict=False)
        if current > previous
    ]
    result: dict[str, Any] = {
        "frames": len(frame_numbers),
        "estimated_missing_frames": sum(
            max(0, current - previous - 1)
            for previous, current in zip(frame_numbers, frame_numbers[1:], strict=False)
        ),
        "non_monotonic_timestamps": sum(
            current <= previous
            for previous, current in zip(timestamps_ms, timestamps_ms[1:], strict=False)
        ),
    }
    if intervals_ms:
        ordered = sorted(intervals_ms)
        p95_index = max(0, int(len(ordered) * 0.95) - 1)
        mean_ms = statistics.mean(intervals_ms)
        result.update(
            mean_interval_ms=round(mean_ms, 3),
            median_interval_ms=round(statistics.median(intervals_ms), 3),
            p95_interval_ms=round(ordered[p95_index], 3),
            max_interval_ms=round(max(intervals_ms), 3),
            device_timestamp_hz=round(1000 / mean_ms, 2),
        )
    return result


def sensor_option(sensor: Any, option: Any) -> float | None:
    return sensor.get_option(option) if sensor.supports(option) else None


def test_stream(
    rs: Any,
    duration_s: float,
    warmup_s: float,
    width: int,
    height: int,
    fps: int,
) -> dict[str, Any]:
    if duration_s <= 0:
        raise ValueError("duration-s must be positive when streaming")
    if warmup_s < 0:
        raise ValueError("warmup-s must not be negative")

    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_stream(rs.stream.depth, width, height, rs.format.z16, fps)
    config.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)

    depth_numbers: list[int] = []
    depth_timestamps_ms: list[float] = []
    color_numbers: list[int] = []
    color_timestamps_ms: list[float] = []
    host_arrival_timestamps_ms: list[float] = []
    timeout_count = 0
    startup_started_at = time.perf_counter()
    profile = pipeline.start(config)
    startup_s = time.perf_counter() - startup_started_at
    capture_elapsed_s = 0.0
    try:
        warmup_started_at = time.perf_counter()
        while time.perf_counter() - warmup_started_at < warmup_s:
            pipeline.wait_for_frames(timeout_ms=2000)

        depth_sensor = profile.get_device().first_depth_sensor()
        color_sensor = profile.get_device().first_color_sensor()
        options = {
            "depth_auto_exposure": sensor_option(depth_sensor, rs.option.enable_auto_exposure),
            "depth_exposure": sensor_option(depth_sensor, rs.option.exposure),
            "color_auto_exposure": sensor_option(color_sensor, rs.option.enable_auto_exposure),
            "color_exposure": sensor_option(color_sensor, rs.option.exposure),
            "color_auto_white_balance": sensor_option(
                color_sensor, rs.option.enable_auto_white_balance
            ),
            "color_white_balance": sensor_option(color_sensor, rs.option.white_balance),
        }
        capture_started_at = time.perf_counter()
        while time.perf_counter() - capture_started_at < duration_s:
            try:
                frames = pipeline.wait_for_frames(timeout_ms=2000)
            except RuntimeError:
                timeout_count += 1
                continue
            host_arrival_timestamps_ms.append(time.perf_counter_ns() / 1_000_000)
            depth = frames.get_depth_frame()
            color = frames.get_color_frame()
            if depth:
                depth_numbers.append(depth.get_frame_number())
                depth_timestamps_ms.append(depth.get_timestamp())
            if color:
                color_numbers.append(color.get_frame_number())
                color_timestamps_ms.append(color.get_timestamp())
        capture_elapsed_s = time.perf_counter() - capture_started_at
    finally:
        pipeline.stop()

    return {
        "requested": {"width": width, "height": height, "fps": fps},
        "startup_s": round(startup_s, 3),
        "warmup_s": warmup_s,
        "capture_elapsed_s": round(capture_elapsed_s, 3),
        "frameset_wall_hz": round(len(depth_numbers) / capture_elapsed_s, 2),
        "wait_timeouts": timeout_count,
        "options_after_warmup": options,
        "host_arrival": summarize_frames(depth_numbers, host_arrival_timestamps_ms),
        "depth": summarize_frames(depth_numbers, depth_timestamps_ms),
        "color": summarize_frames(color_numbers, color_timestamps_ms),
    }


def main() -> None:
    args = parse_args()

    try:
        import pyrealsense2 as rs
    except ImportError as exc:
        raise RuntimeError(
            "pyrealsense2 is required only when running this hardware check. "
            "Install it in the active hardware environment."
        ) from exc

    devices = list(rs.context().query_devices())
    if not devices:
        raise RuntimeError("No RealSense device was detected")

    result = {
        "device_count": len(devices),
        "devices": [
            inspect_device(
                device,
                rs,
                preferred_width=args.width,
                preferred_height=args.height,
                preferred_fps=args.fps,
            )
            for device in devices
        ],
    }
    if args.duration_s > 0:
        result["stream_test"] = test_stream(
            rs,
            duration_s=args.duration_s,
            warmup_s=args.warmup_s,
            width=args.width,
            height=args.height,
            fps=args.fps,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
