"""Measure D435i frame delivery and 50 Hz consumer freshness without a robot."""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
from collections import Counter
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Measure synchronized RealSense RGB-D frame arrival timing and the age of the latest "
            "frameset when sampled by a nominal control loop."
        )
    )
    parser.add_argument("--duration-s", type=float, default=30.0)
    parser.add_argument("--warmup-s", type=float, default=2.0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--control-hz", type=float, default=50.0)
    return parser.parse_args()


def summarize_values_ms(values_ms: list[float]) -> dict[str, float | int]:
    """Summarize millisecond values using nearest-rank-style percentiles."""
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


def is_wall_clock_comparable(frame_timestamp_ms: float, arrival_wall_ms: float) -> bool:
    """Return whether a frame timestamp appears to share the host wall-clock epoch."""
    return abs(arrival_wall_ms - frame_timestamp_ms) < 60_000


def main() -> None:
    args = parse_args()
    if args.duration_s <= 0:
        raise ValueError("duration-s must be positive")
    if args.warmup_s < 0:
        raise ValueError("warmup-s must not be negative")
    if args.control_hz <= 0:
        raise ValueError("control-hz must be positive")

    try:
        import pyrealsense2 as rs
    except ImportError as exc:
        raise RuntimeError("pyrealsense2 is required for this hardware check") from exc

    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_stream(rs.stream.depth, args.width, args.height, rs.format.z16, args.fps)
    config.enable_stream(rs.stream.color, args.width, args.height, rs.format.bgr8, args.fps)

    startup_started_ns = time.perf_counter_ns()
    profile = pipeline.start(config)
    startup_ms = (time.perf_counter_ns() - startup_started_ns) / 1_000_000

    frame_arrival_ns: list[int] = []
    depth_frame_numbers: list[int] = []
    color_frame_numbers: list[int] = []
    depth_timestamps_ms: list[float] = []
    color_timestamps_ms: list[float] = []
    depth_domains: Counter[str] = Counter()
    color_domains: Counter[str] = Counter()
    depth_delivery_ages_ms: list[float] = []
    color_delivery_ages_ms: list[float] = []
    depth_color_skews_ms: list[float] = []
    cache_ages_ms: list[float] = []
    depth_ages_at_control_ms: list[float] = []
    color_ages_at_control_ms: list[float] = []
    tick_lateness_ms: list[float] = []
    latest_arrival_ns: int | None = None
    latest_depth_timestamp_ms: float | None = None
    latest_color_timestamp_ms: float | None = None
    wait_timeouts = 0
    lock = threading.Lock()
    stop_event = threading.Event()

    try:
        warmup_started = time.perf_counter()
        while time.perf_counter() - warmup_started < args.warmup_s:
            pipeline.wait_for_frames(timeout_ms=2000)

        def capture_frames() -> None:
            nonlocal latest_arrival_ns, latest_color_timestamp_ms
            nonlocal latest_depth_timestamp_ms, wait_timeouts
            while not stop_event.is_set():
                try:
                    frames = pipeline.wait_for_frames(timeout_ms=2000)
                except RuntimeError:
                    with lock:
                        wait_timeouts += 1
                    continue

                arrival_ns = time.perf_counter_ns()
                arrival_wall_ms = time.time_ns() / 1_000_000
                depth = frames.get_depth_frame()
                color = frames.get_color_frame()
                if not depth or not color:
                    continue

                depth_timestamp_ms = float(depth.get_timestamp())
                color_timestamp_ms = float(color.get_timestamp())
                depth_domain = str(depth.get_frame_timestamp_domain())
                color_domain = str(color.get_frame_timestamp_domain())

                with lock:
                    frame_arrival_ns.append(arrival_ns)
                    latest_arrival_ns = arrival_ns
                    latest_depth_timestamp_ms = depth_timestamp_ms
                    latest_color_timestamp_ms = color_timestamp_ms
                    depth_frame_numbers.append(int(depth.get_frame_number()))
                    color_frame_numbers.append(int(color.get_frame_number()))
                    depth_timestamps_ms.append(depth_timestamp_ms)
                    color_timestamps_ms.append(color_timestamp_ms)
                    depth_domains[depth_domain] += 1
                    color_domains[color_domain] += 1
                    if is_wall_clock_comparable(depth_timestamp_ms, arrival_wall_ms):
                        depth_delivery_ages_ms.append(arrival_wall_ms - depth_timestamp_ms)
                    if is_wall_clock_comparable(color_timestamp_ms, arrival_wall_ms):
                        color_delivery_ages_ms.append(arrival_wall_ms - color_timestamp_ms)
                    if depth_domain == color_domain:
                        depth_color_skews_ms.append(
                            abs(depth_timestamp_ms - color_timestamp_ms)
                        )

        capture_thread = threading.Thread(
            target=capture_frames,
            name="realsense-capture",
            daemon=True,
        )
        started_ns = time.perf_counter_ns()
        period_ns = round(1_000_000_000 / args.control_hz)
        next_tick_ns = started_ns + period_ns
        capture_thread.start()
        while True:
            remaining_ns = next_tick_ns - time.perf_counter_ns()
            if remaining_ns > 0:
                time.sleep(remaining_ns / 1_000_000_000)
            sampled_ns = time.perf_counter_ns()
            sampled_wall_ms = time.time_ns() / 1_000_000
            if sampled_ns - started_ns > args.duration_s * 1_000_000_000:
                break

            with lock:
                current_arrival_ns = latest_arrival_ns
                current_depth_timestamp_ms = latest_depth_timestamp_ms
                current_color_timestamp_ms = latest_color_timestamp_ms
            if current_arrival_ns is not None:
                cache_ages_ms.append((sampled_ns - current_arrival_ns) / 1_000_000)
            if current_depth_timestamp_ms is not None and is_wall_clock_comparable(
                current_depth_timestamp_ms,
                sampled_wall_ms,
            ):
                depth_ages_at_control_ms.append(
                    sampled_wall_ms - current_depth_timestamp_ms
                )
            if current_color_timestamp_ms is not None and is_wall_clock_comparable(
                current_color_timestamp_ms,
                sampled_wall_ms,
            ):
                color_ages_at_control_ms.append(
                    sampled_wall_ms - current_color_timestamp_ms
                )
            tick_lateness_ms.append((sampled_ns - next_tick_ns) / 1_000_000)
            next_tick_ns += period_ns
    finally:
        stop_event.set()
        if "capture_thread" in locals():
            capture_thread.join(timeout=3.0)
        pipeline.stop()

    with lock:
        arrivals = list(frame_arrival_ns)
        depth_numbers = list(depth_frame_numbers)
        color_numbers = list(color_frame_numbers)
        depth_times = list(depth_timestamps_ms)
        color_times = list(color_timestamps_ms)
        depth_domain_counts = dict(depth_domains)
        color_domain_counts = dict(color_domains)
        timeout_count = wait_timeouts

    arrival_intervals_ms = [
        (current - previous) / 1_000_000
        for previous, current in zip(arrivals, arrivals[1:], strict=False)
    ]
    depth_timestamp_intervals_ms = [
        current - previous
        for previous, current in zip(depth_times, depth_times[1:], strict=False)
        if current > previous
    ]
    color_timestamp_intervals_ms = [
        current - previous
        for previous, current in zip(color_times, color_times[1:], strict=False)
        if current > previous
    ]

    result: dict[str, Any] = {
        "requested": {
            "width": args.width,
            "height": args.height,
            "fps": args.fps,
            "control_hz": args.control_hz,
        },
        "startup_ms": round(startup_ms, 3),
        "warmup_s": args.warmup_s,
        "framesets": len(arrivals),
        "wait_timeouts": timeout_count,
        "estimated_missing_depth_frames": sum(
            max(0, current - previous - 1)
            for previous, current in zip(depth_numbers, depth_numbers[1:], strict=False)
        ),
        "estimated_missing_color_frames": sum(
            max(0, current - previous - 1)
            for previous, current in zip(color_numbers, color_numbers[1:], strict=False)
        ),
        "host_arrival_interval": summarize_values_ms(arrival_intervals_ms),
        "depth_device_interval": summarize_values_ms(depth_timestamp_intervals_ms),
        "color_device_interval": summarize_values_ms(color_timestamp_intervals_ms),
        "depth_timestamp_domains": depth_domain_counts,
        "color_timestamp_domains": color_domain_counts,
        "depth_timestamp_to_host_arrival": summarize_values_ms(depth_delivery_ages_ms),
        "color_timestamp_to_host_arrival": summarize_values_ms(color_delivery_ages_ms),
        "depth_color_timestamp_skew": summarize_values_ms(depth_color_skews_ms),
        "control_loop_latest_frameset_age": summarize_values_ms(cache_ages_ms),
        "depth_timestamp_age_at_control": summarize_values_ms(depth_ages_at_control_ms),
        "color_timestamp_age_at_control": summarize_values_ms(color_ages_at_control_ms),
        "control_tick_lateness": summarize_values_ms(tick_lateness_ms),
        "notes": {
            "timestamp_to_arrival_scope": (
                "Reported only when the frame timestamp appears comparable with the host wall clock."
            ),
            "latest_frameset_age_scope": (
                "Time since Python received the latest complete RGB-D frameset when sampled by "
                "the control loop; it excludes exposure-to-USB delivery time."
            ),
            "physical_latency_scope": (
                "True photon-to-application latency requires an external optical timing reference."
            ),
        },
        "device": {
            "name": profile.get_device().get_info(rs.camera_info.name),
            "usb_type": profile.get_device().get_info(rs.camera_info.usb_type_descriptor),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
