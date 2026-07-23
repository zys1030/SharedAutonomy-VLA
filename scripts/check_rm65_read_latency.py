"""Measure read-only RM-65B SDK latency without sending motion commands."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections.abc import Callable
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Measure RM-65B connection, joint-state request, local forward-kinematics, "
            "and complete observation latency without changing controller state."
        )
    )
    parser.add_argument("--ip", required=True, help="RM-65B controller IP address")
    parser.add_argument("--port", type=int, default=8080, help="RM-65B controller TCP port")
    parser.add_argument("--samples", type=int, default=100, help="Number of measured observations")
    parser.add_argument("--warmup", type=int, default=5, help="Number of discarded warmup observations")
    return parser.parse_args()


def summarize_durations(durations_ns: list[int]) -> dict[str, float | int]:
    """Summarize positive monotonic-clock durations."""
    if not durations_ns:
        return {"samples": 0}
    if any(duration < 0 for duration in durations_ns):
        raise ValueError("durations must not be negative")

    durations_ms = [duration / 1_000_000 for duration in durations_ns]
    ordered = sorted(durations_ms)

    def percentile(fraction: float) -> float:
        index = max(0, int(len(ordered) * fraction) - 1)
        return ordered[index]

    return {
        "samples": len(durations_ms),
        "mean_ms": round(statistics.mean(durations_ms), 3),
        "median_ms": round(statistics.median(durations_ms), 3),
        "p95_ms": round(percentile(0.95), 3),
        "p99_ms": round(percentile(0.99), 3),
        "min_ms": round(min(durations_ms), 3),
        "max_ms": round(max(durations_ms), 3),
    }


def measure_ns(operation: Callable[[], Any]) -> tuple[Any, int]:
    """Run an operation and return its result and host-side elapsed time."""
    started_ns = time.perf_counter_ns()
    result = operation()
    return result, time.perf_counter_ns() - started_ns


def main() -> None:
    args = parse_args()
    if args.samples <= 0:
        raise ValueError("samples must be positive")
    if args.warmup < 0:
        raise ValueError("warmup must not be negative")

    from Robotic_Arm.rm_robot_interface import RoboticArm, rm_thread_mode_e

    arm = RoboticArm(rm_thread_mode_e.RM_TRIPLE_MODE_E)
    handle, connect_ns = measure_ns(
        lambda: arm.rm_create_robot_arm(args.ip, args.port, level=3)
    )
    if int(getattr(handle, "id", -1)) < 0:
        raise ConnectionError(f"Failed to connect to RM-65B at {args.ip}:{args.port}")

    joint_read_ns: list[int] = []
    forward_kinematics_ns: list[int] = []
    observation_ns: list[int] = []
    disconnect_ns: int | None = None
    try:
        for sample_index in range(args.warmup + args.samples):
            observation_started_ns = time.perf_counter_ns()
            joint_result, current_joint_read_ns = measure_ns(arm.rm_get_joint_degree)
            status, joints = joint_result
            if status != 0:
                raise RuntimeError(f"Failed to read joint positions: SDK status {status}")
            if len(joints) < 6:
                raise RuntimeError(f"SDK returned {len(joints)} joints, expected at least 6")

            pose, current_fk_ns = measure_ns(
                lambda: arm.rm_algo_forward_kinematics(joints[:6], flag=1)
            )
            if len(pose) != 6:
                raise RuntimeError(f"SDK returned an invalid end-effector pose of length {len(pose)}")
            current_observation_ns = time.perf_counter_ns() - observation_started_ns

            if sample_index >= args.warmup:
                joint_read_ns.append(current_joint_read_ns)
                forward_kinematics_ns.append(current_fk_ns)
                observation_ns.append(current_observation_ns)
    finally:
        delete_status, disconnect_ns = measure_ns(arm.rm_delete_robot_arm)
        if delete_status != 0:
            raise RuntimeError(f"Failed to delete robot arm handle: SDK status {delete_status}")

    print(
        json.dumps(
            {
                "measurement": {
                    "connect_ms": round(connect_ns / 1_000_000, 3),
                    "disconnect_ms": round(disconnect_ns / 1_000_000, 3),
                    "joint_state_request_rtt": summarize_durations(joint_read_ns),
                    "local_forward_kinematics": summarize_durations(forward_kinematics_ns),
                    "complete_observation_path": summarize_durations(observation_ns),
                },
                "notes": {
                    "clock": "host perf_counter_ns",
                    "motion_commands_sent": False,
                    "controller_configuration_changed": False,
                    "scope": (
                        "Host-side SDK call latency. This does not measure controller sampling age "
                        "or motion command-to-physical-response latency."
                    ),
                },
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
