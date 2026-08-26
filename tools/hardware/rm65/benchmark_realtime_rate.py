"""Measure an RM-65B's existing UDP realtime-push rate without changing it."""

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
            "Read the existing RM-65B UDP realtime-push configuration and measure callback timing "
            "without changing controller configuration or sending motion commands."
        )
    )
    parser.add_argument("--ip", required=True, help="RM-65B controller IP address")
    parser.add_argument("--port", type=int, default=8080, help="RM-65B controller TCP port")
    parser.add_argument("--duration-s", type=float, default=10.0, help="Measurement duration in seconds")
    return parser.parse_args()


def summarize_intervals(timestamps_ns: list[int]) -> dict[str, float | int]:
    """Summarize consecutive callback timestamps."""
    intervals_ms = [
        (current - previous) / 1_000_000
        for previous, current in zip(timestamps_ns, timestamps_ns[1:], strict=False)
    ]
    if not intervals_ms:
        return {"samples": len(timestamps_ns), "intervals": 0}

    ordered = sorted(intervals_ms)
    p95_index = max(0, int(len(ordered) * 0.95) - 1)
    p99_index = max(0, int(len(ordered) * 0.99) - 1)
    mean_ms = statistics.mean(intervals_ms)
    return {
        "samples": len(timestamps_ns),
        "intervals": len(intervals_ms),
        "mean_ms": round(mean_ms, 3),
        "median_ms": round(statistics.median(intervals_ms), 3),
        "p95_ms": round(ordered[p95_index], 3),
        "p99_ms": round(ordered[p99_index], 3),
        "min_ms": round(min(intervals_ms), 3),
        "max_ms": round(max(intervals_ms), 3),
        "over_10ms": sum(interval > 10 for interval in intervals_ms),
        "over_20ms": sum(interval > 20 for interval in intervals_ms),
        "effective_hz": round(1000 / mean_ms, 2),
    }


def main() -> None:
    args = parse_args()
    if args.duration_s <= 0:
        raise ValueError("duration-s must be positive")

    from Robotic_Arm.rm_ctypes_wrap import rm_realtime_arm_state_callback_ptr
    from Robotic_Arm.rm_robot_interface import RoboticArm, rm_thread_mode_e

    arm = RoboticArm(rm_thread_mode_e.RM_TRIPLE_MODE_E)
    handle = arm.rm_create_robot_arm(args.ip, args.port, level=3)
    if int(getattr(handle, "id", -1)) < 0:
        raise ConnectionError(f"Failed to connect to RM-65B at {args.ip}:{args.port}")

    timestamps_ns: list[int] = []
    callback_errors: list[int] = []
    lock = threading.Lock()

    @rm_realtime_arm_state_callback_ptr
    def on_state(state: Any) -> None:
        with lock:
            timestamps_ns.append(time.perf_counter_ns())
            if state.errCode != 0:
                callback_errors.append(int(state.errCode))

    try:
        status, config = arm.rm_get_realtime_push()
        if status != 0:
            raise RuntimeError(f"Failed to read realtime-push configuration: SDK status {status}")

        safe_config = dict(config)
        safe_config["ip"] = "<redacted>"
        print(json.dumps({"realtime_push": safe_config}, indent=2, sort_keys=True))

        if not bool(config["enable"]):
            raise RuntimeError("UDP realtime push is disabled; no controller setting was changed")

        arm.rm_realtime_arm_state_call_back(on_state)
        time.sleep(args.duration_s)
    finally:
        delete_status = arm.rm_delete_robot_arm()
        if delete_status != 0:
            raise RuntimeError(f"Failed to delete robot arm handle: SDK status {delete_status}")

    with lock:
        result = summarize_intervals(list(timestamps_ns))
        result["callback_errors"] = len(callback_errors)
    print(json.dumps({"measurement": result}, indent=2, sort_keys=True))

    if len(timestamps_ns) < 2:
        raise RuntimeError(
            "Fewer than two realtime callbacks arrived. Check the configured target IP, "
            "UDP port, and firewall."
        )


if __name__ == "__main__":
    main()
