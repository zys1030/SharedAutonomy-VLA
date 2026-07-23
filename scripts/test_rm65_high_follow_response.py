"""Measure guarded high-follow CAN-FD response with a tiny J6 trajectory."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import threading
import time
from typing import Any, Callable


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stream one tiny J6 triangle trajectory through high-follow CAN-FD and measure "
            "host timing plus UDP-observed response."
        )
    )
    parser.add_argument("--ip", required=True)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--amplitude-deg", type=float, default=0.1)
    parser.add_argument("--send-hz", type=float, default=200.0)
    parser.add_argument("--ramp-s", type=float, default=0.5)
    parser.add_argument("--warmup-s", type=float, default=0.1)
    parser.add_argument("--response-timeout-s", type=float, default=0.5)
    parser.add_argument("--position-threshold-deg", type=float, default=0.005)
    parser.add_argument(
        "--confirm-high-follow-motion-test",
        action="store_true",
        help="Required confirmation that the workspace is clear and an operator is at the stop control",
    )
    return parser.parse_args()


def checked_pair(name: str, call: Callable[[], tuple[int, Any]]) -> Any:
    status, value = call()
    if status != 0:
        raise RuntimeError(f"{name} failed with SDK status {status}")
    return value


def triangle_offset_deg(index: int, ramp_ticks: int, amplitude_deg: float) -> float:
    """Return a zero-to-amplitude-to-zero triangle target."""
    if not 0 <= index <= 2 * ramp_ticks:
        raise ValueError("index must be within the triangle trajectory")
    if ramp_ticks <= 0:
        raise ValueError("ramp_ticks must be positive")
    if index <= ramp_ticks:
        return amplitude_deg * index / ramp_ticks
    return amplitude_deg * (2 * ramp_ticks - index) / ramp_ticks


def summarize_ms(values_ms: list[float]) -> dict[str, float | int]:
    if not values_ms:
        return {"samples": 0}
    ordered = sorted(values_ms)

    def percentile(fraction: float) -> float:
        position = max(0, int(len(ordered) * fraction) - 1)
        return ordered[position]

    return {
        "samples": len(values_ms),
        "mean_ms": round(statistics.mean(values_ms), 3),
        "median_ms": round(statistics.median(values_ms), 3),
        "p95_ms": round(percentile(0.95), 3),
        "p99_ms": round(percentile(0.99), 3),
        "min_ms": round(min(values_ms), 3),
        "max_ms": round(max(values_ms), 3),
    }


def main() -> None:
    args = parse_args()
    if not args.confirm_high_follow_motion_test:
        raise RuntimeError("Explicit --confirm-high-follow-motion-test is required")
    if not 0 < args.amplitude_deg <= 0.1:
        raise ValueError("amplitude-deg must be in (0, 0.1]")
    if not 100 <= args.send_hz <= 200:
        raise ValueError("send-hz must be between 100 and 200")
    if 1000 / args.send_hz > 10:
        raise ValueError("high-follow send period must not exceed 10 ms")
    if not 0.2 <= args.ramp_s <= 1.0:
        raise ValueError("ramp-s must be between 0.2 and 1.0")
    if not 0.05 <= args.warmup_s <= 0.5:
        raise ValueError("warmup-s must be between 0.05 and 0.5")
    if not 0 < args.position_threshold_deg < args.amplitude_deg:
        raise ValueError("position-threshold-deg must be smaller than amplitude-deg")

    from Robotic_Arm.rm_ctypes_wrap import rm_realtime_arm_state_callback_ptr
    from Robotic_Arm.rm_robot_interface import RoboticArm, rm_thread_mode_e

    arm = RoboticArm(rm_thread_mode_e.RM_TRIPLE_MODE_E)
    handle = arm.rm_create_robot_arm(args.ip, args.port, level=3)
    if int(getattr(handle, "id", -1)) < 0:
        raise ConnectionError(f"Failed to connect to RM-65B at {args.ip}:{args.port}")

    lock = threading.Lock()
    baseline_event = threading.Event()
    motion_event = threading.Event()
    command_started_ns: int | None = None
    baseline_joint_6_deg: float | None = None
    motion_observed_ns: int | None = None
    onset_position_deg: float | None = None
    callback_errors = 0
    move_sent = False
    slow_stop_status: int | None = None
    send_intervals_ms: list[float] = []
    sdk_call_ms: list[float] = []
    tick_lateness_ms: list[float] = []
    deadline_violation_ms: float | None = None

    @rm_realtime_arm_state_callback_ptr
    def on_state(state: Any) -> None:
        nonlocal callback_errors, motion_observed_ns, onset_position_deg
        received_ns = time.perf_counter_ns()
        if int(state.errCode) != 0:
            with lock:
                callback_errors += 1
            return
        position_deg = float(state.joint_status.joint_position[5])
        with lock:
            baseline_event.set()
            if (
                command_started_ns is not None
                and baseline_joint_6_deg is not None
                and received_ns > command_started_ns
                and motion_observed_ns is None
                and abs(position_deg - baseline_joint_6_deg) >= args.position_threshold_deg
            ):
                motion_observed_ns = received_ns
                onset_position_deg = position_deg
                motion_event.set()

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

        baseline = [float(value) for value in current]
        baseline_joint_6_deg = baseline[5]
        target_peak_deg = baseline_joint_6_deg + args.amplitude_deg
        if not lower[5] <= target_peak_deg <= upper[5]:
            raise RuntimeError("J6 peak target is outside controller limits")

        period_ns = round(1_000_000_000 / args.send_hz)
        max_allowed_interval_ms = 10.0
        warmup_ticks = max(1, round(args.warmup_s * args.send_hz))
        ramp_ticks = max(1, round(args.ramp_s * args.send_hz))
        offsets_deg = [0.0] * warmup_ticks + [
            triangle_offset_deg(index, ramp_ticks, args.amplitude_deg)
            for index in range(2 * ramp_ticks + 1)
        ]

        print(
            json.dumps(
                {
                    "preflight": {
                        "start_joints_deg": baseline,
                        "joint_6_peak_deg": target_peak_deg,
                        "amplitude_deg": args.amplitude_deg,
                        "send_hz": args.send_hz,
                        "period_ms": period_ns / 1_000_000,
                        "ramp_s_each_way": args.ramp_s,
                        "stream_samples": len(offsets_deg),
                        "udp_cycle_ms": float(realtime_config["cycle"]) * 5.0,
                    }
                },
                indent=2,
            ),
            flush=True,
        )
        time.sleep(3.0)

        next_tick_ns = time.perf_counter_ns()
        previous_call_started_ns: int | None = None
        for index, offset_deg in enumerate(offsets_deg):
            remaining_ns = next_tick_ns - time.perf_counter_ns()
            if remaining_ns > 0:
                time.sleep(remaining_ns / 1_000_000_000)
            call_started_ns = time.perf_counter_ns()
            lateness_ms = (call_started_ns - next_tick_ns) / 1_000_000
            tick_lateness_ms.append(lateness_ms)

            if previous_call_started_ns is not None:
                interval_ms = (call_started_ns - previous_call_started_ns) / 1_000_000
                send_intervals_ms.append(interval_ms)
                if interval_ms > max_allowed_interval_ms:
                    deadline_violation_ms = interval_ms
                    raise TimeoutError(
                        f"High-follow send interval {interval_ms:.3f} ms exceeded 10 ms"
                    )

            target = list(baseline)
            target[5] = baseline_joint_6_deg + offset_deg
            if any(
                not math.isclose(target[joint_index], baseline[joint_index], abs_tol=1e-9)
                for joint_index in range(5)
            ):
                raise AssertionError("Only J6 may change")

            if index == warmup_ticks:
                with lock:
                    command_started_ns = call_started_ns

            status = arm.rm_movej_canfd(
                target,
                follow=True,
                expand=0,
                trajectory_mode=0,
                radio=0,
            )
            call_returned_ns = time.perf_counter_ns()
            move_sent = True
            sdk_call_ms.append((call_returned_ns - call_started_ns) / 1_000_000)
            if status != 0:
                raise RuntimeError(f"High-follow CAN-FD failed at sample {index}: {status}")
            if (
                command_started_ns is not None
                and (call_returned_ns - command_started_ns) / 1_000_000_000
                > args.response_timeout_s
                and not motion_event.is_set()
            ):
                raise TimeoutError("No UDP-observed J6 motion within the response timeout")

            previous_call_started_ns = call_started_ns
            next_tick_ns += period_ns

        if not motion_event.wait(timeout=args.response_timeout_s):
            raise TimeoutError("No UDP-observed J6 motion within the response timeout")
    finally:
        if move_sent:
            slow_stop_status = arm.rm_set_arm_slow_stop()
        final_status, final_joints = arm.rm_get_joint_degree()
        final_joint_errors = arm.rm_get_joint_err_flag()
        final_controller_state = arm.rm_get_controller_state()
        delete_status = arm.rm_delete_robot_arm()
        if delete_status != 0:
            raise RuntimeError(f"Failed to delete robot arm handle: SDK status {delete_status}")

    response_ms = (
        (motion_observed_ns - command_started_ns) / 1_000_000
        if motion_observed_ns is not None and command_started_ns is not None
        else None
    )
    print(
        json.dumps(
            {
                "measurement": {
                    "command_to_udp_motion_ms": round(response_ms, 3)
                    if response_ms is not None
                    else None,
                    "onset_position_deg": onset_position_deg,
                    "send_intervals": summarize_ms(send_intervals_ms),
                    "sdk_call": summarize_ms(sdk_call_ms),
                    "tick_lateness": summarize_ms(tick_lateness_ms),
                    "deadline_violation_ms": deadline_violation_ms,
                    "callback_errors": callback_errors,
                },
                "safety": {
                    "motion_command_sent": move_sent,
                    "fallback_slow_stop_status": slow_stop_status,
                    "final_joint_read_status": final_status,
                    "final_joints_deg": final_joints if final_status == 0 else None,
                    "final_joint_error": final_joint_errors,
                    "final_controller_state": final_controller_state,
                },
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
