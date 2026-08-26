"""Measure wrist RealSense + external UVC RGB streaming in parallel without a robot."""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stream wrist RealSense RGB-D and an external UVC RGB camera in parallel, "
            "then report host-side arrival timing and simulated consumer freshness."
        )
    )
    parser.add_argument("--duration-s", type=float, default=30.0)
    parser.add_argument("--warmup-s", type=float, default=2.0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--consumer-hz", type=str, default="10,50")
    parser.add_argument(
        "--external-opencv-index",
        type=int,
        required=True,
        help="Machine-local OpenCV index for the external UVC camera",
    )
    parser.add_argument("--realsense-serial", type=str, default=None)
    return parser.parse_args()


def summarize_values_ms(values_ms: list[float]) -> dict[str, float | int]:
    if not values_ms:
        return {"samples": 0}
    ordered = sorted(values_ms)

    def percentile(fraction: float) -> float:
        index = max(0, int(len(ordered) * fraction) - 1)
        return ordered[index]

    return {
        "samples": len(values_ms),
        "mean_ms": round(statistics.mean(values_ms), 3),
        "median_ms": round(statistics.median(values_ms), 3),
        "p95_ms": round(percentile(0.95), 3),
        "p99_ms": round(percentile(0.99), 3),
        "min_ms": round(min(values_ms), 3),
        "max_ms": round(max(values_ms), 3),
    }


def simulate_consumer_ages_ms(
    arrivals_ns: list[int],
    *,
    started_ns: int,
    duration_ns: int,
    consumer_hz: float,
) -> list[float]:
    if not arrivals_ns:
        return []
    period_ns = round(1_000_000_000 / consumer_hz)
    end_ns = started_ns + duration_ns
    next_tick_ns = started_ns + period_ns
    ages_ms: list[float] = []
    while next_tick_ns <= end_ns:
        latest_arrival = None
        for arrival_ns in arrivals_ns:
            if arrival_ns <= next_tick_ns:
                latest_arrival = arrival_ns
            else:
                break
        if latest_arrival is not None:
            ages_ms.append((next_tick_ns - latest_arrival) / 1_000_000)
        next_tick_ns += period_ns
    return ages_ms


def missing_frame_count(frame_numbers: list[int]) -> int:
    return sum(
        max(0, current - previous - 1)
        for previous, current in zip(frame_numbers, frame_numbers[1:], strict=False)
    )


def stream_report(
    *,
    label: str,
    arrivals_ns: list[int],
    started_ns: int,
    duration_s: float,
    consumer_rates: list[float],
    frame_numbers: list[int] | None = None,
    wait_timeouts: int = 0,
    read_failures: int = 0,
) -> dict[str, Any]:
    intervals_ms = [(c - p) / 1_000_000 for p, c in zip(arrivals_ns, arrivals_ns[1:], strict=False)]
    report: dict[str, Any] = {
        "frames": len(arrivals_ns),
        "estimated_receive_hz": round(len(arrivals_ns) / duration_s, 3) if duration_s else 0.0,
        "wait_timeouts": wait_timeouts,
        "read_failures": read_failures,
        "host_arrival_interval": summarize_values_ms(intervals_ms),
    }
    if frame_numbers is not None:
        report["estimated_missing_frames"] = missing_frame_count(frame_numbers)
    for consumer_hz in consumer_rates:
        hz_label = f"{consumer_hz:g}hz".replace(".", "p")
        report[f"control_loop_latest_frame_age_{hz_label}"] = summarize_values_ms(
            simulate_consumer_ages_ms(
                arrivals_ns,
                started_ns=started_ns,
                duration_ns=int(duration_s * 1_000_000_000),
                consumer_hz=consumer_hz,
            )
        )
    return report


def main() -> None:
    args = parse_args()
    consumer_rates = [float(part.strip()) for part in args.consumer_hz.split(",") if part.strip()]

    try:
        import cv2
        import pyrealsense2 as rs
    except ImportError as exc:
        raise RuntimeError("opencv-python and pyrealsense2 are required") from exc

    pipeline = rs.pipeline()
    config = rs.config()
    if args.realsense_serial:
        config.enable_device(args.realsense_serial)
    config.enable_stream(rs.stream.depth, args.width, args.height, rs.format.z16, args.fps)
    config.enable_stream(rs.stream.color, args.width, args.height, rs.format.bgr8, args.fps)

    startup_started_ns = time.perf_counter_ns()
    profile = pipeline.start(config)
    realsense_startup_ms = (time.perf_counter_ns() - startup_started_ns) / 1_000_000

    backend = cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else cv2.CAP_ANY
    external_cap = cv2.VideoCapture(args.external_opencv_index, backend)
    if not external_cap.isOpened():
        pipeline.stop()
        raise RuntimeError(f"Failed to open external camera at OpenCV index {args.external_opencv_index}")
    external_cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    external_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    external_cap.set(cv2.CAP_PROP_FPS, args.fps)
    ok, frame = external_cap.read()
    if not ok or frame is None:
        external_cap.release()
        pipeline.stop()
        raise RuntimeError("External camera opened but failed to read an initial frame")

    wrist_arrivals_ns: list[int] = []
    wrist_depth_numbers: list[int] = []
    wrist_color_numbers: list[int] = []
    external_arrivals_ns: list[int] = []
    wrist_wait_timeouts = 0
    external_read_failures = 0
    wrist_lock = threading.Lock()
    external_lock = threading.Lock()
    stop_event = threading.Event()

    try:
        warmup_started = time.perf_counter()
        while time.perf_counter() - warmup_started < args.warmup_s:
            pipeline.wait_for_frames(timeout_ms=2000)
            external_cap.read()

        def capture_realsense() -> None:
            nonlocal wrist_wait_timeouts
            while not stop_event.is_set():
                try:
                    frames = pipeline.wait_for_frames(timeout_ms=2000)
                except RuntimeError:
                    with wrist_lock:
                        wrist_wait_timeouts += 1
                    continue
                depth = frames.get_depth_frame()
                color = frames.get_color_frame()
                if not depth or not color:
                    continue
                arrival_ns = time.perf_counter_ns()
                with wrist_lock:
                    wrist_arrivals_ns.append(arrival_ns)
                    wrist_depth_numbers.append(int(depth.get_frame_number()))
                    wrist_color_numbers.append(int(color.get_frame_number()))

        def capture_external() -> None:
            nonlocal external_read_failures
            while not stop_event.is_set():
                ok_frame, _ = external_cap.read()
                if not ok_frame:
                    with external_lock:
                        external_read_failures += 1
                    continue
                arrival_ns = time.perf_counter_ns()
                with external_lock:
                    external_arrivals_ns.append(arrival_ns)

        started_ns = time.perf_counter_ns()
        wrist_thread = threading.Thread(target=capture_realsense, name="wrist-realsense", daemon=True)
        external_thread = threading.Thread(target=capture_external, name="external-uvc", daemon=True)
        wrist_thread.start()
        external_thread.start()
        time.sleep(args.duration_s)
    finally:
        stop_event.set()
        wrist_thread.join(timeout=3.0)
        external_thread.join(timeout=3.0)
        external_cap.release()
        pipeline.stop()

    with wrist_lock:
        wrist_arrivals = list(wrist_arrivals_ns)
        depth_numbers = list(wrist_depth_numbers)
        color_numbers = list(wrist_color_numbers)
        wrist_timeouts = wrist_wait_timeouts
    with external_lock:
        external_arrivals = list(external_arrivals_ns)
        external_failures = external_read_failures

    result: dict[str, Any] = {
        "requested": {
            "width": args.width,
            "height": args.height,
            "fps": args.fps,
            "consumer_hz": consumer_rates,
            "external_opencv_index": args.external_opencv_index,
            "realsense_serial_configured": bool(args.realsense_serial),
        },
        "startup_ms": {
            "realsense": round(realsense_startup_ms, 3),
        },
        "wrist_realsense": stream_report(
            label="wrist_realsense",
            arrivals_ns=wrist_arrivals,
            started_ns=started_ns,
            duration_s=args.duration_s,
            consumer_rates=consumer_rates,
            frame_numbers=depth_numbers,
            wait_timeouts=wrist_timeouts,
        ),
        "external_rgb": stream_report(
            label="external_rgb",
            arrivals_ns=external_arrivals,
            started_ns=started_ns,
            duration_s=args.duration_s,
            consumer_rates=consumer_rates,
            read_failures=external_failures,
        ),
        "wrist_color_frame_gaps": missing_frame_count(color_numbers),
        "devices": {
            "realsense_name": profile.get_device().get_info(rs.camera_info.name),
            "realsense_usb_type": profile.get_device().get_info(rs.camera_info.usb_type_descriptor),
            "external_opencv_index": args.external_opencv_index,
        },
        "notes": {
            "scope": (
                "Parallel host-side streaming check only. Consumer ages are simulated from "
                "received_monotonic_ns arrival timestamps."
            ),
            "pass_heuristic": (
                "Both streams near nominal fps with low timeout/read-failure counts and no large "
                "sustained arrival-interval tails are treated as acceptable for 10 Hz collection."
            ),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

