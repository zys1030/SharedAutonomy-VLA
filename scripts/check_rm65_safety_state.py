"""Read RM-65B safety-related state without changing controller settings."""

from __future__ import annotations

import argparse
import json
import time
from typing import Any, Callable

from sharedautonomy.robot.safety import clip_joint_targets


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read joint limits, joint state, error flags, power state, and controller state. "
            "This script does not enable joints, clear errors, change limits, or send motion commands."
        )
    )
    parser.add_argument("--ip", required=True, help="RM-65B controller IP address")
    parser.add_argument("--port", type=int, default=8080, help="RM-65B controller TCP port")
    return parser.parse_args()


def checked_pair(name: str, call: Callable[[], tuple[int, Any]]) -> Any:
    status, value = call()
    if status != 0:
        raise RuntimeError(f"{name} failed with SDK status {status}")
    return value


def main() -> None:
    args = parse_args()

    from Robotic_Arm.rm_robot_interface import RoboticArm, rm_thread_mode_e

    arm = RoboticArm(rm_thread_mode_e.RM_TRIPLE_MODE_E)
    handle = arm.rm_create_robot_arm(args.ip, args.port, level=3)
    if int(getattr(handle, "id", -1)) < 0:
        raise ConnectionError(f"Failed to connect to RM-65B at {args.ip}:{args.port}")

    try:
        current_joints = checked_pair("read joint position", arm.rm_get_joint_degree)
        controller_min = checked_pair("read controller joint minimum", arm.rm_get_joint_min_pos)
        controller_max = checked_pair("read controller joint maximum", arm.rm_get_joint_max_pos)
        dry_run_above = clip_joint_targets(
            current_joints,
            [upper + 10.0 for upper in controller_max],
            max_step_deg=1.0,
            joint_limits_deg=list(zip(controller_min, controller_max, strict=True)),
        )
        dry_run_below = clip_joint_targets(
            current_joints,
            [lower - 10.0 for lower in controller_min],
            max_step_deg=1.0,
            joint_limits_deg=list(zip(controller_min, controller_max, strict=True)),
        )
        time.sleep(2.0)
        second_joint_sample = checked_pair("read second joint position", arm.rm_get_joint_degree)
        result = {
            "joint_position_deg": current_joints,
            "joint_position_after_2s_deg": second_joint_sample,
            "max_joint_change_over_2s_deg": max(
                abs(after - before)
                for before, after in zip(current_joints, second_joint_sample, strict=True)
            ),
            "controller_joint_min_deg": controller_min,
            "controller_joint_max_deg": controller_max,
            "drive_joint_min_deg": checked_pair(
                "read drive joint minimum", arm.rm_get_joint_drive_min_pos
            ),
            "drive_joint_max_deg": checked_pair(
                "read drive joint maximum", arm.rm_get_joint_drive_max_pos
            ),
            "joint_enabled": checked_pair("read joint enable state", arm.rm_get_joint_en_state),
            "joint_error": arm.rm_get_joint_err_flag(),
            "arm_power_state": checked_pair("read arm power state", arm.rm_get_arm_power_state),
            "arm_run_mode": checked_pair("read arm run mode", arm.rm_get_arm_run_mode),
            "program_run_state": checked_pair(
                "read program run state", arm.rm_get_program_run_state
            ),
            "controller_state": arm.rm_get_controller_state(),
            "software_limit_dry_run": {
                "max_relative_target_deg": 1.0,
                "requested_above_max_deg": [upper + 10.0 for upper in controller_max],
                "filtered_above_max_deg": dry_run_above,
                "requested_below_min_deg": [lower - 10.0 for lower in controller_min],
                "filtered_below_min_deg": dry_run_below,
                "sdk_motion_call_made": False,
            },
        }
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        delete_status = arm.rm_delete_robot_arm()
        if delete_status != 0:
            raise RuntimeError(f"Failed to delete robot arm handle: SDK status {delete_status}")


if __name__ == "__main__":
    main()
