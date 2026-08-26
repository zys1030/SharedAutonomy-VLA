"""Run a guarded joint-6 motion for a teach-pendant emergency-stop test."""

from __future__ import annotations

import argparse
import json
import math
import time
from collections.abc import Callable
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "DANGER: this verification tool moves RM-65B joint 6 at low speed while an "
            "operator triggers the teach-pendant emergency stop. This is not a substitute "
            "for testing an independent physical E-stop."
        )
    )
    parser.add_argument("--ip", required=True, help="RM-65B controller IP address")
    parser.add_argument("--port", type=int, default=8080, help="RM-65B controller TCP port")
    parser.add_argument("--delta-deg", type=float, default=20.0, help="Joint-6 target delta")
    parser.add_argument("--speed-percent", type=int, default=1, help="MoveJ speed percentage")
    parser.add_argument("--monitor-s", type=float, default=10.0, help="Post-command monitoring time")
    parser.add_argument(
        "--confirm-teach-pendant-estop-test",
        action="store_true",
        help="Required explicit acknowledgement that the workspace is clear and the operator is ready",
    )
    return parser.parse_args()


def checked_pair(name: str, call: Callable[[], tuple[int, Any]]) -> Any:
    status, value = call()
    if status != 0:
        raise RuntimeError(f"{name} failed with SDK status {status}")
    return value


def main() -> None:
    args = parse_args()
    if not args.confirm_teach_pendant_estop_test:
        raise RuntimeError("Explicit --confirm-teach-pendant-estop-test is required")
    if not 0 < args.speed_percent <= 5:
        raise ValueError("speed-percent must be between 1 and 5 for this guarded test")
    if not 0 < abs(args.delta_deg) <= 20:
        raise ValueError("delta-deg magnitude must be between 0 and 20")
    if not 5 <= args.monitor_s <= 20:
        raise ValueError("monitor-s must be between 5 and 20")

    from Robotic_Arm.rm_robot_interface import RoboticArm, rm_thread_mode_e

    arm = RoboticArm(rm_thread_mode_e.RM_TRIPLE_MODE_E)
    handle = arm.rm_create_robot_arm(args.ip, args.port, level=3)
    if int(getattr(handle, "id", -1)) < 0:
        raise ConnectionError(f"Failed to connect to RM-65B at {args.ip}:{args.port}")

    move_sent = False
    slow_stop_status: int | None = None
    samples: list[dict[str, Any]] = []
    try:
        current = checked_pair("read joint position", arm.rm_get_joint_degree)
        lower = checked_pair("read controller joint minimum", arm.rm_get_joint_min_pos)
        upper = checked_pair("read controller joint maximum", arm.rm_get_joint_max_pos)
        joint_errors = arm.rm_get_joint_err_flag()
        controller_state = arm.rm_get_controller_state()
        run_mode = checked_pair("read arm run mode", arm.rm_get_arm_run_mode)
        program_state = checked_pair("read program run state", arm.rm_get_program_run_state)

        if run_mode != 1:
            raise RuntimeError(f"Expected real run mode 1, got {run_mode}")
        if program_state.get("run_state") != 0:
            raise RuntimeError(f"A controller program is running: {program_state}")
        if joint_errors.get("return_code") != 0 or any(joint_errors.get("err_flag", [])):
            raise RuntimeError(f"Joint error is present: {joint_errors}")
        if controller_state.get("return_code") != 0 or controller_state.get("system_error") != 0:
            raise RuntimeError(f"Controller error is present: {controller_state}")

        target = list(current)
        target[5] += args.delta_deg
        if not lower[5] <= target[5] <= upper[5]:
            raise RuntimeError(
                f"Joint-6 target {target[5]:.3f} is outside controller limits "
                f"[{lower[5]:.3f}, {upper[5]:.3f}]"
            )
        if any(
            not math.isclose(target[index], current[index], abs_tol=1e-9)
            for index in range(5)
        ):
            raise AssertionError("Only joint 6 may change in this test")

        print(
            json.dumps(
                {
                    "preflight": {
                        "joint_6_start_deg": current[5],
                        "joint_6_target_deg": target[5],
                        "speed_percent": args.speed_percent,
                        "monitor_s": args.monitor_s,
                    }
                },
                indent=2,
            ),
            flush=True,
        )
        time.sleep(2.0)

        command_started_ns = time.perf_counter_ns()
        move_status = arm.rm_movej(
            target,
            v=args.speed_percent,
            r=0,
            connect=0,
            block=0,
        )
        if move_status != 0:
            raise RuntimeError(f"Low-speed joint-6 MoveJ failed with SDK status {move_status}")
        move_sent = True

        while (time.perf_counter_ns() - command_started_ns) / 1_000_000_000 < args.monitor_s:
            status, joints = arm.rm_get_joint_degree()
            if status != 0:
                samples.append({"t_s": None, "read_status": status})
                time.sleep(0.05)
                continue
            samples.append(
                {
                    "t_s": round(
                        (time.perf_counter_ns() - command_started_ns) / 1_000_000_000,
                        4,
                    ),
                    "joint_6_deg": joints[5],
                }
            )
            time.sleep(0.05)
    finally:
        if move_sent:
            slow_stop_status = arm.rm_set_arm_slow_stop()

        final_joint_status, final_joints = arm.rm_get_joint_degree()
        final_result = {
            "motion_command_sent": move_sent,
            "fallback_slow_stop_status": slow_stop_status,
            "sample_count": len(samples),
            "samples": samples,
            "final_joint_read_status": final_joint_status,
            "final_joint_6_deg": final_joints[5] if final_joint_status == 0 else None,
            "final_joint_error": arm.rm_get_joint_err_flag(),
            "final_controller_state": arm.rm_get_controller_state(),
            "final_joint_enabled": checked_pair(
                "read final joint enable state", arm.rm_get_joint_en_state
            ),
            "final_arm_power_state": checked_pair(
                "read final arm power state", arm.rm_get_arm_power_state
            ),
            "final_program_run_state": checked_pair(
                "read final program run state", arm.rm_get_program_run_state
            ),
        }
        print(json.dumps({"result": final_result}, indent=2), flush=True)

        delete_status = arm.rm_delete_robot_arm()
        if delete_status != 0:
            raise RuntimeError(f"Failed to delete robot arm handle: SDK status {delete_status}")


if __name__ == "__main__":
    main()
