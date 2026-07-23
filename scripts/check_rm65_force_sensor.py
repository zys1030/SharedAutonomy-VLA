"""Read RM-65B force-sensor capability and samples without changing calibration."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from typing import Any


CHANNELS = ("Fx_N", "Fy_N", "Fz_N", "Mx_Nm", "My_Nm", "Mz_Nm")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read robot model metadata and six-axis force samples. "
            "This script never clears, zeros, calibrates, or configures the force sensor."
        )
    )
    parser.add_argument("--ip", required=True, help="RM-65B controller IP address")
    parser.add_argument("--port", type=int, default=8080, help="RM-65B controller TCP port")
    parser.add_argument("--samples", type=int, default=20, help="Number of read-only samples")
    parser.add_argument("--interval-s", type=float, default=0.1, help="Delay between samples")
    return parser.parse_args()


def vector(value: Any) -> list[float]:
    return [float(item) for item in value]


def summarize(samples: list[list[float]]) -> dict[str, dict[str, float]]:
    columns = list(zip(*samples, strict=True))
    return {
        channel: {
            "mean": round(statistics.mean(values), 6),
            "min": round(min(values), 6),
            "max": round(max(values), 6),
            "pstdev": round(statistics.pstdev(values), 6),
        }
        for channel, values in zip(CHANNELS, columns, strict=True)
    }


def main() -> None:
    args = parse_args()
    if args.samples <= 0:
        raise ValueError("samples must be positive")
    if args.interval_s < 0:
        raise ValueError("interval-s must not be negative")

    from Robotic_Arm.rm_robot_interface import RoboticArm, rm_thread_mode_e

    arm = RoboticArm(rm_thread_mode_e.RM_TRIPLE_MODE_E)
    handle = arm.rm_create_robot_arm(args.ip, args.port, level=3)
    if int(getattr(handle, "id", -1)) < 0:
        raise ConnectionError(f"Failed to connect to RM-65B at {args.ip}:{args.port}")

    try:
        info_status, robot_info = arm.rm_get_robot_info()
        if info_status != 0:
            raise RuntimeError(f"read robot info failed with SDK status {info_status}")
        if robot_info.get("force_type") not in {"6F", "6FB", "6FB-V"}:
            raise RuntimeError(f"Robot does not report a six-axis force version: {robot_info}")

        raw_samples: list[list[float]] = []
        external_samples: list[list[float]] = []
        for _ in range(args.samples):
            status, data = arm.rm_get_force_data()
            if status != 0:
                raise RuntimeError(f"read six-axis force failed with SDK status {status}")
            raw_samples.append(vector(data["force_data"]))
            external_samples.append(vector(data["zero_force_data"]))
            if args.interval_s:
                time.sleep(args.interval_s)

        print(
            json.dumps(
                {
                    "robot_info": robot_info,
                    "sample_count": len(raw_samples),
                    "channels": list(CHANNELS),
                    "raw_force_summary": summarize(raw_samples),
                    "external_force_summary": summarize(external_samples),
                    "sensor_configuration_changed": False,
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        delete_status = arm.rm_delete_robot_arm()
        if delete_status != 0:
            raise RuntimeError(f"Failed to delete robot arm handle: SDK status {delete_status}")


if __name__ == "__main__":
    main()
