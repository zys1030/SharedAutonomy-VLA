"""Measure guarded RM-65B command-to-observed-motion latency on joint 6."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import threading
import time
from collections.abc import Callable
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "DANGER: this verification tool moves RM-65B joint 6. Use small alternating "
            "targets to measure command-to-motion latency from the existing UDP realtime "
            "state push. Keep the operator at the stop control."
        )
    )
    parser.add_argument("--ip", required=True)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--delta-deg", type=float, default=0.5)
    parser.add_argument("--speed-percent", type=int, default=1)
    parser.add_argument(
        "--command-mode",
        choices=("movej", "canfd-low"),
        default="movej",
        help="Use guarded low-speed MoveJ or low-follow CAN-FD joint passthrough",
    )
    parser.add_argument("--trials", type=int, default=4)
    parser.add_argument("--response-timeout-s", type=float, default=1.0)
    parser.add_argument("--target-timeout-s", type=float, default=3.0)
    parser.add_argument("--position-threshold-deg", type=float, default=0.02)
    parser.add_argument("--speed-threshold-deg-s", type=float, default=0.1)
    parser.add_argument(
        "--confirm-guarded-motion-test",
        action="store_true",
        help="Required confirmation that the workspace and J6-mounted cables are clear",
    )
    return parser.parse_args()


def checked_pair(name: str, call: Callable[[], tuple[int, Any]]) -> Any:
    status, value = call()
    if status != 0:
        raise RuntimeError(f"{name} failed with SDK status {status}")
    return value


def motion_started(
    position_deg: float,
    speed_deg_s: float,
    baseline_deg: float,
    position_threshold_deg: float,
    speed_threshold_deg_s: float,
) -> bool:
    """Return whether an observed joint state indicates motion after a command."""
    return (
        abs(position_deg - baseline_deg) >= position_threshold_deg
        or abs(speed_deg_s) >= speed_threshold_deg_s
    )


def summarize_ms(values_ms: list[float]) -> dict[str, float | int]:
    """Summarize measured latencies."""
    if not values_ms:
        return {"samples": 0}
    ordered = sorted(values_ms)
    return {
        "samples": len(values_ms),
        "mean_ms": round(statistics.mean(values_ms), 3),
        "median_ms": round(statistics.median(values_ms), 3),
        "min_ms": round(min(values_ms), 3),
        "max_ms": round(max(values_ms), 3),
        "values_ms": [round(value, 3) for value in values_ms],
        "ordered_ms": [round(value, 3) for value in ordered],
    }


def main() -> None:
    args = parse_args()
    if not args.confirm_guarded_motion_test:
        raise RuntimeError("Explicit --confirm-guarded-motion-test is required")
    if not 0 < abs(args.delta_deg) <= 1.0:
        raise ValueError("delta-deg magnitude must be in (0, 1.0]")
    if not 0 < args.speed_percent <= 2:
        raise ValueError("speed-percent must be 1 or 2")
    if not 2 <= args.trials <= 6 or args.trials % 2 != 0:
        raise ValueError("trials must be an even number between 2 and 6")
    if not 0 < args.position_threshold_deg < abs(args.delta_deg):
        raise ValueError("position-threshold-deg must be positive and smaller than delta-deg")
    if args.speed_threshold_deg_s <= 0:
        raise ValueError("speed-threshold-deg-s must be positive")

    from Robotic_Arm.rm_ctypes_wrap import rm_realtime_arm_state_callback_ptr
    from Robotic_Arm.rm_robot_interface import RoboticArm, rm_thread_mode_e

    arm = RoboticArm(rm_thread_mode_e.RM_TRIPLE_MODE_E)
    handle = arm.rm_create_robot_arm(args.ip, args.port, level=3)
    if int(getattr(handle, "id", -1)) < 0:
        raise ConnectionError(f"Failed to connect to RM-65B at {args.ip}:{args.port}")

    lock = threading.Lock()
    baseline_event = threading.Event()
    response_event = threading.Event()
    target_event = threading.Event()
    latest_joint_6: dict[str, float | int] = {}
    active_trial: dict[str, float | int] | None = None
    trials: list[dict[str, Any]] = []
    move_sent = False
    slow_stop_status: int | None = None

    @rm_realtime_arm_state_callback_ptr
    def on_state(state: Any) -> None:
        nonlocal active_trial
        received_ns = time.perf_counter_ns()
        if int(state.errCode) != 0:
            return
        position_deg = float(state.joint_status.joint_position[5])
        speed_deg_s = float(state.joint_status.joint_speed[5])
        with lock:
            latest_joint_6.update(
                received_ns=received_ns,
                position_deg=position_deg,
                speed_deg_s=speed_deg_s,
            )
            baseline_event.set()
            if active_trial is None or received_ns <= int(active_trial["command_started_ns"]):
                return
            if "motion_observed_ns" not in active_trial and motion_started(
                position_deg,
                speed_deg_s,
                float(active_trial["baseline_deg"]),
                args.position_threshold_deg,
                args.speed_threshold_deg_s,
            ):
                active_trial["motion_observed_ns"] = received_ns
                active_trial["onset_position_deg"] = position_deg
                active_trial["onset_speed_deg_s"] = speed_deg_s
                response_event.set()
            if abs(position_deg - float(active_trial["target_deg"])) <= 0.03:
                active_trial["target_observed_ns"] = received_ns
                target_event.set()

    try:
        status, realtime_config = arm.rm_get_realtime_push()
        if status != 0 or not bool(realtime_config.get("enable")):
            raise RuntimeError(f"UDP realtime push is unavailable: status={status}")
        arm.rm_realtime_arm_state_call_back(on_state)
        if not baseline_event.wait(timeout=2.0):
            raise RuntimeError("No UDP realtime state arrived within 2 seconds")

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

        start_joint_6 = float(current[5])
        positive_target = start_joint_6 + abs(args.delta_deg)
        if not lower[5] <= positive_target <= upper[5]:
            raise RuntimeError(
                f"J6 test target {positive_target:.3f} is outside "
                f"[{lower[5]:.3f}, {upper[5]:.3f}]"
            )

        print(
            json.dumps(
                {
                    "preflight": {
                        "joint_6_start_deg": start_joint_6,
                        "delta_deg": abs(args.delta_deg),
                        "speed_percent": args.speed_percent,
                        "command_mode": args.command_mode,
                        "trials": args.trials,
                        "udp_cycle_ms": float(realtime_config["cycle"]) * 5.0,
                        "motion_scope": "J6 only; alternating targets return near the start",
                    }
                },
                indent=2,
            ),
            flush=True,
        )
        time.sleep(3.0)

        for trial_index in range(args.trials):
            status, present = arm.rm_get_joint_degree()
            if status != 0:
                raise RuntimeError(f"Trial {trial_index + 1}: failed to read current joints")
            baseline_deg = float(present[5])
            direction = 1.0 if trial_index % 2 == 0 else -1.0
            target_deg = baseline_deg + direction * abs(args.delta_deg)
            if not lower[5] <= target_deg <= upper[5]:
                raise RuntimeError(f"Trial {trial_index + 1}: J6 target is outside limits")

            target = list(present)
            target[5] = target_deg
            if any(
                not math.isclose(target[index], present[index], abs_tol=1e-9)
                for index in range(5)
            ):
                raise AssertionError("Only J6 may change")

            response_event.clear()
            target_event.clear()
            trial: dict[str, Any] = {
                "trial": trial_index + 1,
                "baseline_deg": baseline_deg,
                "target_deg": target_deg,
            }
            with lock:
                active_trial = trial
                trial["command_started_ns"] = time.perf_counter_ns()

            if args.command_mode == "movej":
                move_status = arm.rm_movej(
                    target,
                    v=args.speed_percent,
                    r=0,
                    connect=0,
                    block=0,
                )
            else:
                move_status = arm.rm_movej_canfd(
                    target,
                    follow=False,
                    expand=0,
                    trajectory_mode=0,
                    radio=0,
                )
            command_returned_ns = time.perf_counter_ns()
            move_sent = True
            if move_status != 0:
                raise RuntimeError(
                    f"Trial {trial_index + 1}: MoveJ failed with SDK status {move_status}"
                )
            trial["command_returned_ns"] = command_returned_ns
            trial["sdk_call_ms"] = (
                command_returned_ns - int(trial["command_started_ns"])
            ) / 1_000_000

            if not response_event.wait(timeout=args.response_timeout_s):
                raise TimeoutError(
                    f"Trial {trial_index + 1}: no motion observed within "
                    f"{args.response_timeout_s:.3f} s"
                )
            trial["command_to_motion_ms"] = (
                int(trial["motion_observed_ns"]) - int(trial["command_started_ns"])
            ) / 1_000_000

            if not target_event.wait(timeout=args.target_timeout_s):
                raise TimeoutError(
                    f"Trial {trial_index + 1}: target was not observed within "
                    f"{args.target_timeout_s:.3f} s"
                )
            trial["command_to_target_ms"] = (
                int(trial["target_observed_ns"]) - int(trial["command_started_ns"])
            ) / 1_000_000
            trials.append(trial)
            with lock:
                active_trial = None
            time.sleep(0.5)
    finally:
        with lock:
            active_trial = None
        if move_sent:
            slow_stop_status = arm.rm_set_arm_slow_stop()
        final_status, final_joints = arm.rm_get_joint_degree()
        delete_status = arm.rm_delete_robot_arm()
        if delete_status != 0:
            raise RuntimeError(f"Failed to delete robot arm handle: SDK status {delete_status}")

    command_to_motion_ms = [float(trial["command_to_motion_ms"]) for trial in trials]
    sdk_call_ms = [float(trial["sdk_call_ms"]) for trial in trials]
    command_to_target_ms = [float(trial["command_to_target_ms"]) for trial in trials]
    print(
        json.dumps(
            {
                "measurement": {
                    "sdk_call": summarize_ms(sdk_call_ms),
                    "command_to_udp_motion": summarize_ms(command_to_motion_ms),
                    "command_to_target": summarize_ms(command_to_target_ms),
                    "trials": trials,
                },
                "safety": {
                    "motion_command_sent": move_sent,
                    "fallback_slow_stop_status": slow_stop_status,
                    "final_joint_read_status": final_status,
                    "final_joint_6_deg": final_joints[5] if final_status == 0 else None,
                },
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
