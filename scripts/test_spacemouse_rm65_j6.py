"""Guarded SpaceMouse-to-RM65 J6 integration smoke test."""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
from typing import Any

from sharedautonomy.robot.safety import clip_joint_targets

try:
    from scripts.check_spacemouse_rate import enumerate_spacemice
except ModuleNotFoundError:
    from check_spacemouse_rate import enumerate_spacemice


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Map SpaceMouse Compact yaw to guarded RM-65B J6 low-follow CAN-FD commands. "
            "Hold the left SpaceMouse button to enable; releasing it ends the test."
        )
    )
    parser.add_argument("--ip", required=True)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--duration-s", type=float, default=15.0)
    parser.add_argument("--control-hz", type=float, default=50.0)
    parser.add_argument("--max-velocity-deg-s", type=float, default=0.3)
    parser.add_argument("--max-travel-deg", type=float, default=0.3)
    parser.add_argument("--deadzone", type=float, default=0.15)
    parser.add_argument("--input-timeout-ms", type=float, default=100.0)
    parser.add_argument(
        "--confirm-spacemouse-j6-motion-test",
        action="store_true",
        help="Required confirmation that the workspace is clear and the operator is at the stop control",
    )
    return parser.parse_args()


def decode_int16_le(report: list[int], offset: int) -> int:
    if offset < 0 or offset + 2 > len(report):
        raise ValueError("report does not contain the requested int16 value")
    return int.from_bytes(bytes(report[offset : offset + 2]), byteorder="little", signed=True)


def normalize_axis(raw_value: int, deadzone: float, full_scale: float = 350.0) -> float:
    if not 0 <= deadzone < 1:
        raise ValueError("deadzone must be in [0, 1)")
    normalized = max(-1.0, min(1.0, raw_value / full_scale))
    if abs(normalized) <= deadzone:
        return 0.0
    magnitude = (abs(normalized) - deadzone) / (1.0 - deadzone)
    return magnitude if normalized > 0 else -magnitude


def summarize_ms(values_ms: list[float]) -> dict[str, float | int]:
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


def main() -> None:
    args = parse_args()
    if not args.confirm_spacemouse_j6_motion_test:
        raise RuntimeError("Explicit --confirm-spacemouse-j6-motion-test is required")
    if not 5 <= args.duration_s <= 30:
        raise ValueError("duration-s must be between 5 and 30")
    if not 20 <= args.control_hz <= 50:
        raise ValueError("control-hz must be between 20 and 50")
    if not 0 < args.max_velocity_deg_s <= 0.5:
        raise ValueError("max-velocity-deg-s must be in (0, 0.5]")
    if not 0 < args.max_travel_deg <= 0.5:
        raise ValueError("max-travel-deg must be in (0, 0.5]")
    if not 20 <= args.input_timeout_ms <= 200:
        raise ValueError("input-timeout-ms must be between 20 and 200")

    import hid
    from Robotic_Arm.rm_ctypes_wrap import rm_realtime_arm_state_callback_ptr
    from Robotic_Arm.rm_robot_interface import RoboticArm, rm_thread_mode_e

    devices = enumerate_spacemice(hid)
    if len(devices) != 1:
        raise RuntimeError(f"Expected exactly one supported SpaceMouse, found {len(devices)}")

    hid_device = hid.device()
    hid_lock = threading.Lock()
    hid_stop = threading.Event()
    yaw_value = 0.0
    yaw_received_ns: int | None = None
    left_button_pressed = False
    hid_error: str | None = None

    def read_hid() -> None:
        nonlocal hid_error, left_button_pressed, yaw_received_ns, yaw_value
        try:
            hid_device.open_path(devices[0]["path"])
            hid_device.set_nonblocking(0)
            while not hid_stop.is_set():
                report = hid_device.read(64, 100)
                if not report:
                    continue
                received_ns = time.perf_counter_ns()
                report_id = int(report[0])
                with hid_lock:
                    if report_id == 2 and len(report) >= 7:
                        yaw_value = normalize_axis(
                            decode_int16_le(report, 5),
                            args.deadzone,
                        )
                        yaw_received_ns = received_ns
                    elif report_id == 3 and len(report) >= 2:
                        left_button_pressed = bool(int(report[1]) & 0x01)
        except Exception as exc:
            with hid_lock:
                hid_error = repr(exc)

    arm = RoboticArm(rm_thread_mode_e.RM_TRIPLE_MODE_E)
    handle = arm.rm_create_robot_arm(args.ip, args.port, level=3)
    if int(getattr(handle, "id", -1)) < 0:
        raise ConnectionError(f"Failed to connect to RM-65B at {args.ip}:{args.port}")

    arm_lock = threading.Lock()
    arm_state_event = threading.Event()
    latest_joints: list[float] | None = None
    latest_arm_state_ns: int | None = None
    callback_errors = 0
    motion_command_started_ns: int | None = None
    motion_observed_ns: int | None = None
    start_joint_6_deg: float | None = None
    move_sent = False
    slow_stop_status: int | None = None

    @rm_realtime_arm_state_callback_ptr
    def on_state(state: Any) -> None:
        nonlocal callback_errors, latest_arm_state_ns, latest_joints, motion_observed_ns
        received_ns = time.perf_counter_ns()
        if int(state.errCode) != 0:
            with arm_lock:
                callback_errors += 1
            return
        joints = [float(state.joint_status.joint_position[index]) for index in range(6)]
        with arm_lock:
            latest_joints = joints
            latest_arm_state_ns = received_ns
            arm_state_event.set()
            if (
                motion_command_started_ns is not None
                and start_joint_6_deg is not None
                and motion_observed_ns is None
                and received_ns > motion_command_started_ns
                and abs(joints[5] - start_joint_6_deg) >= 0.005
            ):
                motion_observed_ns = received_ns

    hid_thread = threading.Thread(target=read_hid, name="spacemouse-reader", daemon=True)
    control_intervals_ms: list[float] = []
    input_ages_ms: list[float] = []
    state_ages_ms: list[float] = []
    sdk_call_ms: list[float] = []
    commanded_joint_6_deg: list[float] = []
    enabled_ticks = 0
    stale_input_ticks = 0
    deadman_seen = False
    release_ended_test = False

    try:
        status, realtime_config = arm.rm_get_realtime_push()
        if status != 0 or not bool(realtime_config.get("enable")):
            raise RuntimeError(f"UDP realtime push is unavailable: status={status}")
        arm.rm_realtime_arm_state_call_back(on_state)
        if not arm_state_event.wait(timeout=2.0):
            raise RuntimeError("No UDP realtime state arrived within 2 seconds")

        current_status, current = arm.rm_get_joint_degree()
        if current_status != 0:
            raise RuntimeError(f"Failed to read current joints: SDK status {current_status}")
        lower_status, lower = arm.rm_get_joint_min_pos()
        upper_status, upper = arm.rm_get_joint_max_pos()
        if lower_status != 0 or upper_status != 0:
            raise RuntimeError("Failed to read controller joint limits")
        joint_errors = arm.rm_get_joint_err_flag()
        controller_state = arm.rm_get_controller_state()
        run_status, run_mode = arm.rm_get_arm_run_mode()
        program_status, program_state = arm.rm_get_program_run_state()
        if run_status != 0 or run_mode != 1:
            raise RuntimeError(f"Expected real run mode 1, got status={run_status}, mode={run_mode}")
        if program_status != 0 or program_state.get("run_state") != 0:
            raise RuntimeError(f"A controller program is running: {program_state}")
        if joint_errors.get("return_code") != 0 or any(joint_errors.get("err_flag", [])):
            raise RuntimeError(f"Joint error is present: {joint_errors}")
        if controller_state.get("return_code") != 0 or controller_state.get("system_error") != 0:
            raise RuntimeError(f"Controller error is present: {controller_state}")

        joint_limits = [[float(lower[index]), float(upper[index])] for index in range(6)]
        start_joint_6_deg = float(current[5])
        command_joint_6_deg = start_joint_6_deg
        period_ns = round(1_000_000_000 / args.control_hz)
        maximum_step_deg = args.max_velocity_deg_s / args.control_hz

        print(
            json.dumps(
                {
                    "preflight": {
                        "start_joints_deg": current,
                        "control_hz": args.control_hz,
                        "max_velocity_deg_s": args.max_velocity_deg_s,
                        "max_travel_deg": args.max_travel_deg,
                        "deadzone": args.deadzone,
                        "input_timeout_ms": args.input_timeout_ms,
                        "udp_cycle_ms": float(realtime_config["cycle"]) * 5.0,
                        "operator_action": (
                            "Hold the left SpaceMouse button, gently twist left/right, "
                            "then release the button to stop"
                        ),
                    }
                },
                indent=2,
            ),
            flush=True,
        )
        hid_thread.start()
        time.sleep(2.0)

        started_ns = time.perf_counter_ns()
        next_tick_ns = started_ns + period_ns
        previous_tick_ns: int | None = None
        while time.perf_counter_ns() - started_ns < args.duration_s * 1_000_000_000:
            remaining_ns = next_tick_ns - time.perf_counter_ns()
            if remaining_ns > 0:
                time.sleep(remaining_ns / 1_000_000_000)
            tick_ns = time.perf_counter_ns()
            if previous_tick_ns is not None:
                control_intervals_ms.append((tick_ns - previous_tick_ns) / 1_000_000)
            previous_tick_ns = tick_ns
            next_tick_ns += period_ns

            with hid_lock:
                current_yaw = yaw_value
                current_yaw_ns = yaw_received_ns
                current_deadman = left_button_pressed
                current_hid_error = hid_error
            if current_hid_error is not None:
                raise RuntimeError(f"SpaceMouse reader failed: {current_hid_error}")

            if current_deadman:
                deadman_seen = True
            elif deadman_seen:
                release_ended_test = True
                break
            else:
                continue

            enabled_ticks += 1
            if current_yaw_ns is None:
                current_yaw = 0.0
                stale_input_ticks += 1
            else:
                input_age_ms = (tick_ns - current_yaw_ns) / 1_000_000
                input_ages_ms.append(input_age_ms)
                if input_age_ms > args.input_timeout_ms:
                    current_yaw = 0.0
                    stale_input_ticks += 1

            with arm_lock:
                observed_joints = list(latest_joints) if latest_joints is not None else None
                observed_ns = latest_arm_state_ns
            if observed_joints is None or observed_ns is None:
                raise RuntimeError("Latest UDP arm state is unavailable")
            state_age_ms = (tick_ns - observed_ns) / 1_000_000
            state_ages_ms.append(state_age_ms)
            if state_age_ms > 50:
                raise TimeoutError(f"Robot state age {state_age_ms:.3f} ms exceeded 50 ms")

            dt_s = (
                control_intervals_ms[-1] / 1000
                if control_intervals_ms
                else 1.0 / args.control_hz
            )
            requested_joint_6_deg = command_joint_6_deg + (
                current_yaw * args.max_velocity_deg_s * dt_s
            )
            requested_joint_6_deg = min(
                max(
                    requested_joint_6_deg,
                    start_joint_6_deg - args.max_travel_deg,
                ),
                start_joint_6_deg + args.max_travel_deg,
            )
            requested = list(observed_joints)
            requested[5] = requested_joint_6_deg
            safe_target = clip_joint_targets(
                observed_joints,
                requested,
                maximum_step_deg,
                joint_limits,
            )
            command_joint_6_deg = safe_target[5]
            commanded_joint_6_deg.append(command_joint_6_deg)

            if motion_command_started_ns is None and abs(current_yaw) > 0:
                motion_command_started_ns = tick_ns
            call_started_ns = time.perf_counter_ns()
            status = arm.rm_movej_canfd(
                safe_target,
                follow=False,
                expand=0,
                trajectory_mode=0,
                radio=0,
            )
            call_returned_ns = time.perf_counter_ns()
            move_sent = True
            sdk_call_ms.append((call_returned_ns - call_started_ns) / 1_000_000)
            if status != 0:
                raise RuntimeError(f"Low-follow CAN-FD failed with SDK status {status}")
    finally:
        hid_stop.set()
        if hid_thread.is_alive():
            hid_thread.join(timeout=1.0)
        try:
            hid_device.close()
        except Exception:
            pass
        if move_sent:
            slow_stop_status = arm.rm_set_arm_slow_stop()
        final_status, final_joints = arm.rm_get_joint_degree()
        final_joint_errors = arm.rm_get_joint_err_flag()
        final_controller_state = arm.rm_get_controller_state()
        delete_status = arm.rm_delete_robot_arm()
        if delete_status != 0:
            raise RuntimeError(f"Failed to delete robot arm handle: SDK status {delete_status}")

    command_to_motion_ms = (
        (motion_observed_ns - motion_command_started_ns) / 1_000_000
        if motion_observed_ns is not None and motion_command_started_ns is not None
        else None
    )
    print(
        json.dumps(
            {
                "measurement": {
                    "deadman_seen": deadman_seen,
                    "release_ended_test": release_ended_test,
                    "enabled_ticks": enabled_ticks,
                    "stale_input_ticks": stale_input_ticks,
                    "command_to_udp_motion_ms": round(command_to_motion_ms, 3)
                    if command_to_motion_ms is not None
                    else None,
                    "control_intervals": summarize_ms(control_intervals_ms),
                    "input_age": summarize_ms(input_ages_ms),
                    "robot_state_age": summarize_ms(state_ages_ms),
                    "sdk_call": summarize_ms(sdk_call_ms),
                    "commanded_joint_6_min_deg": min(commanded_joint_6_deg)
                    if commanded_joint_6_deg
                    else None,
                    "commanded_joint_6_max_deg": max(commanded_joint_6_deg)
                    if commanded_joint_6_deg
                    else None,
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
