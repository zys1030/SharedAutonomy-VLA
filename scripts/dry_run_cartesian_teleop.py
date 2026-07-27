"""Cartesian teleop entry: real SpaceMouse + UDP + stamp workspace.

Motion stays disabled unless BOTH local config enable_motion and CLI
``--allow-motion`` are set. Default runs remain read-only dry-runs.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from sharedautonomy.assistance.safety_filter import CartesianSafetyFilter, CartesianSafetyLimits
from sharedautonomy.assistance.workspace_config import load_cartesian_workspace
from sharedautonomy.control.manual import (
    ManualCartesianConfig,
    MockJointCommander,
    build_manual_cartesian_runner,
)
from sharedautonomy.control.motion_gate import resolve_motion_enabled
from sharedautonomy.control.observation import (
    CartesianProprioceptiveSource,
    build_camera_session_from_config,
    build_observation_synchronizer,
    load_camera_runtime_config,
)
from sharedautonomy.control.realtime import RealtimeCartesianStateSource
from sharedautonomy.control.recording import (
    build_manual_episode_metadata,
    record_cartesian_control_step,
    write_effective_config_yaml,
)
from sharedautonomy.data import EpisodeRecorder
from sharedautonomy.data.schema import CoordinateFrame, HumanAction, SampleTimestamp
from sharedautonomy.devices.spacemouse import HidSpaceMouse, SpaceMouseConfig
from sharedautonomy.robot.canfd_commander import RealManCanfdJointCommander
from sharedautonomy.robot.gripper_config import load_serial_soft_gripper_stack
from sharedautonomy.robot.kinematics import RealManInverseKinematics
from sharedautonomy.robot.ready_pose import load_ready_pose_config, move_arm_to_ready_joints
from sharedautonomy.robot.realtime_state import RealManRealtimeStateSource
from sharedautonomy.robot.safety import CartesianSafetyError, validate_cartesian_segment


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Cartesian SpaceMouse teleop with UDP state and stamp workspace. "
            "Motion requires dual confirmation: config enable_motion=true AND --allow-motion."
        )
    )
    parser.add_argument("--ip", required=True, help="RM-65B controller IP address")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--duration-s", type=float, default=40.0)
    parser.add_argument("--steps", type=int, default=None)
    parser.add_argument(
        "--control-hz",
        type=float,
        default=None,
        help="Control loop rate. Default: 10 Hz with motion enabled (try_sc), else 50 Hz dry-run.",
    )
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--deadzone", type=float, default=0.1)
    parser.add_argument(
        "--max-linear-speed-m-s",
        type=float,
        default=None,
        help=(
            "Teleop speed cap (m/s). Stick is soft-clamped to this. "
            "If omitted with motion enabled, derived from --move-increment-m / dt "
            "(try_sc-style). Dry-run default: 0.05."
        ),
    )
    parser.add_argument(
        "--move-increment-m",
        type=float,
        default=None,
        help=(
            "Full-stick Cartesian step per control tick (meters), try_sc action_limit_m style. "
            "Motion default: 0.01 (10 mm/tick @ 10 Hz ≈ 100 mm/s). Ignored if "
            "--max-linear-speed-m-s is set."
        ),
    )
    parser.add_argument(
        "--xy-yaw-deg",
        type=float,
        default=90.0,
        help=(
            "Extra base-frame XY yaw after vertical_up (degrees). "
            "Default 90 (CCW about +Z) after user reported Z OK but XY needs a quarter-turn; "
            "try -90 if the trial spins the wrong way, or 0 to match try_sc matrices exactly."
        ),
    )
    parser.add_argument(
        "--lock-z",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Zero base-frame Z command after mapping (XY planar only; isolates Z crosstalk)",
    )
    parser.add_argument(
        "--workspace-yaml",
        default=None,
        help="Optional path to rm65_safety YAML (default: local then example then stamp fixture)",
    )
    parser.add_argument(
        "--config-enable-motion",
        action="store_true",
        help="Stand-in for local config enable_motion=true (required with --allow-motion)",
    )
    parser.add_argument(
        "--allow-motion",
        action="store_true",
        help="CLI motion confirmation; alone is not enough without --config-enable-motion",
    )
    parser.add_argument(
        "--canfd-follow",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="CAN-FD follow mode when motion is enabled (default: low-follow / False, matching try_sc)",
    )
    parser.add_argument(
        "--canfd-smoothing",
        type=int,
        default=50,
        help="rm_movej_canfd radio/smoothing when motion is enabled (try_sc uses 50)",
    )
    parser.add_argument(
        "--enable-cameras",
        action="store_true",
        help="Start wrist RealSense + external UVC cameras and attach synced observations",
    )
    parser.add_argument(
        "--record-dir",
        default=None,
        help=(
            "Write a native episode under this directory (requires --enable-cameras). "
            "Parent directory receives effective_config.yaml."
        ),
    )
    parser.add_argument("--run-id", default=None, help="Run identifier for episode metadata")
    parser.add_argument("--episode-id", default=None, help="Episode identifier (default: timestamp-based)")
    parser.add_argument("--task-id", default="teleop-smoke", help="Task id stored in episode metadata")
    parser.add_argument(
        "--task-text",
        default="Manual Cartesian teleop smoke recording.",
        help="Task description stored in episode metadata",
    )
    parser.add_argument(
        "--source-object",
        default=None,
        help="Object to pick, stored as episode metadata source_object (task card object_id, e.g. red)",
    )
    parser.add_argument(
        "--destination",
        default=None,
        help="Placement target, stored as episode metadata destination (task card destination_id, e.g. up)",
    )
    parser.add_argument(
        "--enable-gripper",
        action="store_true",
        help=(
            "Drive the legacy serial soft gripper on SpaceMouse right-button edges "
            "(requires motion enabled and configs/local/gripper_serial.local.yaml)"
        ),
    )
    parser.add_argument(
        "--gripper-config",
        default=None,
        help="Optional path to gripper_serial YAML (default: configs/local/gripper_serial.local.yaml)",
    )
    parser.add_argument(
        "--go-to-ready",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Before teleop, move to ready_pose via try_sc-style rm_movej_canfd. "
            "Default: on when motion is enabled, off for dry-run. "
            "Matches try_sc move_to_init_on_connect."
        ),
    )
    parser.add_argument(
        "--ready-config",
        default=None,
        help="YAML containing ready_pose (default: configs/collection/manual_cartesian.yaml)",
    )
    parser.add_argument(
        "--end-on-gripper-release",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "End the episode after a grasp (gripper close) followed by release (open). "
            "Default: on when --enable-gripper is set. --duration-s remains the max cap."
        ),
    )
    parser.add_argument(
        "--post-release-s",
        type=float,
        default=1.0,
        help="Keep recording this many seconds after gripper release before finalizing",
    )
    return parser.parse_args()


_DEFAULT_DRY_RUN_SPEED_M_S = 0.05
_DEFAULT_MOTION_MOVE_INCREMENT_M = 0.01  # try_sc robot_arm.yaml action_limit_m
_DEFAULT_MOTION_MOVE_INCREMENT_XY_SCALE = 2.0
_DEFAULT_DRY_RUN_CONTROL_HZ = 50.0
_DEFAULT_MOTION_CONTROL_HZ = 10.0
_MIN_INPUT_TIMEOUT_S = 0.1
_MIN_ROBOT_STATE_TIMEOUT_S = 0.05


def _load_config_enable_motion(*, cli_config_enable_motion: bool) -> tuple[bool, str]:
    local_path = Path("configs/local/manual_cartesian.local.yaml")
    if local_path.is_file():
        import yaml

        with local_path.open(encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
        if not isinstance(payload, dict):
            raise ValueError(f"{local_path} must contain a mapping")
        return bool(payload.get("enable_motion", False)), str(local_path)
    if cli_config_enable_motion:
        return True, "--config-enable-motion"
    return False, "configs/collection/manual_cartesian.yaml (default false)"


def main() -> int:
    args = parse_args()
    config_enable_motion, config_source = _load_config_enable_motion(
        cli_config_enable_motion=args.config_enable_motion
    )
    motion_enabled = resolve_motion_enabled(
        config_enable_motion=config_enable_motion,
        cli_allow_motion=args.allow_motion,
    )
    if args.control_hz is None:
        control_hz = _DEFAULT_MOTION_CONTROL_HZ if motion_enabled else _DEFAULT_DRY_RUN_CONTROL_HZ
    else:
        control_hz = float(args.control_hz)
    if control_hz <= 0.0:
        raise ValueError("control-hz must be positive")
    period_s = 1.0 / control_hz
    if args.steps is not None:
        if args.steps < 1:
            raise ValueError("steps must be >= 1")
        total_steps = int(args.steps)
        duration_s = total_steps * period_s
    else:
        if float(args.duration_s) <= 0.0:
            raise ValueError("duration-s must be positive")
        duration_s = float(args.duration_s)
        total_steps = max(1, int(round(duration_s * control_hz)))

    if args.max_linear_speed_m_s is not None and args.move_increment_m is not None:
        raise ValueError("Pass only one of --max-linear-speed-m-s or --move-increment-m")
    move_increment_m: float
    move_increment_xy_m: float
    move_increment_z_m: float
    max_speed_m_s_per_axis: tuple[float, float, float] | None = None
    speed_source: str
    if args.max_linear_speed_m_s is not None:
        max_speed_m_s = float(args.max_linear_speed_m_s)
        move_increment_m = max_speed_m_s * period_s
        move_increment_xy_m = move_increment_m
        move_increment_z_m = move_increment_m
        speed_source = "cli --max-linear-speed-m-s"
    elif motion_enabled:
        if args.move_increment_m is None:
            move_increment_z_m = _DEFAULT_MOTION_MOVE_INCREMENT_M
            speed_source = "default_motion_move_increment_0.01"
        else:
            move_increment_z_m = float(args.move_increment_m)
            speed_source = "cli --move-increment-m"
        if move_increment_z_m <= 0.0:
            raise ValueError("move-increment-m must be positive")
        move_increment_xy_m = _DEFAULT_MOTION_MOVE_INCREMENT_XY_SCALE * move_increment_z_m
        move_increment_m = move_increment_z_m
        max_speed_xy_m_s = move_increment_xy_m / period_s
        max_speed_z_m_s = move_increment_z_m / period_s
        max_speed_m_s_per_axis = (max_speed_xy_m_s, max_speed_xy_m_s, max_speed_z_m_s)
        max_speed_m_s = max(max_speed_m_s_per_axis)
        speed_source = f"{speed_source}; xy={_DEFAULT_MOTION_MOVE_INCREMENT_XY_SCALE}x z"
    else:
        max_speed_m_s = _DEFAULT_DRY_RUN_SPEED_M_S
        move_increment_m = max_speed_m_s * period_s
        move_increment_xy_m = move_increment_m
        move_increment_z_m = move_increment_m
        speed_source = "default_dry_run_0.05"
    if max_speed_m_s <= 0.0:
        raise ValueError("max-linear-speed-m-s must be positive")
    # Reach the speed cap in about one control period (try_sc has no separate accel ramp).
    if max_speed_m_s_per_axis is not None:
        max_acceleration_m_s2 = max(
            max(limit / period_s, limit / 0.2) for limit in max_speed_m_s_per_axis
        )
    else:
        max_acceleration_m_s2 = max(max_speed_m_s / period_s, max_speed_m_s / 0.2)
    # At 10 Hz, 100 ms input timeout sits on the period boundary and can false-trigger stale holds.
    input_timeout_s = max(_MIN_INPUT_TIMEOUT_S, 2.0 * period_s)
    robot_state_timeout_s = max(_MIN_ROBOT_STATE_TIMEOUT_S, 0.5 * period_s)

    if motion_enabled and bool(args.canfd_follow) and period_s > 0.010 + 1e-12:
        raise ValueError(
            "High-follow CAN-FD requires control period <= 10 ms (SDK). "
            f"Got control-hz={control_hz} ({period_s * 1000.0:.1f} ms). "
            "Use --no-canfd-follow (default) or raise --control-hz to >= 100."
        )
    canfd_smoothing = int(args.canfd_smoothing)
    if canfd_smoothing < 0:
        raise ValueError("canfd-smoothing must be >= 0")

    if args.record_dir is not None and not args.enable_cameras:
        raise ValueError("--record-dir requires --enable-cameras so each step has synced observations")
    if args.enable_gripper and not motion_enabled:
        raise ValueError("--enable-gripper requires motion enabled (--config-enable-motion and --allow-motion)")
    end_on_gripper_release = (
        bool(args.enable_gripper) if args.end_on_gripper_release is None else bool(args.end_on_gripper_release)
    )
    if end_on_gripper_release and not args.enable_gripper:
        raise ValueError("--end-on-gripper-release requires --enable-gripper")
    post_release_s = float(args.post_release_s)
    if post_release_s < 0.0:
        raise ValueError("post-release-s must be >= 0")
    go_to_ready = bool(motion_enabled) if args.go_to_ready is None else bool(args.go_to_ready)
    if go_to_ready and not motion_enabled:
        raise ValueError("--go-to-ready requires motion enabled (--config-enable-motion and --allow-motion)")
    ready_pose = load_ready_pose_config(config_path=args.ready_config) if go_to_ready else None

    workspace, workspace_source = load_cartesian_workspace(args.workspace_yaml)
    max_speed_xy_m_s = (
        max_speed_m_s_per_axis[0] if max_speed_m_s_per_axis is not None else max_speed_m_s
    )
    max_speed_z_m_s = (
        max_speed_m_s_per_axis[2] if max_speed_m_s_per_axis is not None else max_speed_m_s
    )
    teleop = HidSpaceMouse(
        SpaceMouseConfig(
            deadzone=args.deadzone,
            max_linear_speed_m_s=max_speed_m_s,
            max_linear_speed_xy_m_s=max_speed_xy_m_s if max_speed_m_s_per_axis is not None else None,
            max_linear_speed_z_m_s=max_speed_z_m_s if max_speed_m_s_per_axis is not None else None,
            mount_orientation="vertical_up",
            base_xy_yaw_deg=float(args.xy_yaw_deg),
            lock_z=bool(args.lock_z),
            allow_rotation=False,
            input_timeout_s=input_timeout_s,
        ),
        device_index=args.device_index,
    )
    backend = RealManRealtimeStateSource(ip=args.ip, port=args.port)
    teleop.connect()
    backend.connect()
    camera_session = None
    observation_synchronizer = None
    if args.enable_cameras:
        camera_runtime = load_camera_runtime_config()
        camera_session, sync_config = build_camera_session_from_config(camera_runtime)
        camera_session.start()
        proprio_source = CartesianProprioceptiveSource(RealtimeCartesianStateSource(backend))
        observation_synchronizer = build_observation_synchronizer(
            proprioception=proprio_source,
            camera_session=camera_session,
            sync_config=sync_config,
        )
    commander: MockJointCommander | RealManCanfdJointCommander
    if motion_enabled:
        commander = RealManCanfdJointCommander(
            backend.arm,
            follow=bool(args.canfd_follow),
            smoothing=canfd_smoothing,
            armed=True,
        )
    else:
        commander = MockJointCommander()

    recorder: EpisodeRecorder | None = None
    episode_dir: Path | None = None
    effective_config_path: Path | None = None
    gripper_device = None
    gripper_actuator = None
    gripper_config_source: str | None = None
    if args.enable_gripper:
        gripper_device, gripper_actuator, gripper_config_source = load_serial_soft_gripper_stack(
            config_path=args.gripper_config,
        )
    try:
        if ready_pose is not None:
            print(
                json.dumps(
                    {
                        "go_to_ready": True,
                        "ready_joints_deg": list(ready_pose.joint_position_deg),
                        "canfd_follow": ready_pose.canfd_follow,
                        "canfd_smoothing": ready_pose.canfd_smoothing,
                        "settle_s": ready_pose.settle_s,
                        "ready_config_source": ready_pose.source,
                    },
                    indent=2,
                ),
                flush=True,
            )
            move_arm_to_ready_joints(
                backend.arm,
                ready_pose.joint_position_deg,
                follow=ready_pose.canfd_follow,
                smoothing=ready_pose.canfd_smoothing,
                settle_s=ready_pose.settle_s,
            )
            if gripper_actuator is not None:
                ready_physical = float(ready_pose.gripper_open_fraction)
                if ready_physical >= 1.0:
                    ready_physical = gripper_actuator.working_open_fraction
                close_angle_deg, open_angle_deg = gripper_actuator.move_to_working_open(
                    ready_physical if ready_physical > 0.0 else 0.0
                )
                print(
                    json.dumps(
                        {
                            "gripper_ready": {
                                "physical_open_fraction": ready_physical,
                                "close_pulse_angle_deg": close_angle_deg,
                                "open_pulse_angle_deg": open_angle_deg,
                                "working_open_fraction": gripper_actuator.working_open_fraction,
                                "gripper_config_source": gripper_config_source,
                            }
                        },
                        indent=2,
                    ),
                    flush=True,
                )

        first = backend.read_snapshot()
        try:
            validate_cartesian_segment(first.ee_position_m, first.ee_position_m, workspace)
        except CartesianSafetyError as exc:
            raise RuntimeError(
                "Current flange pose is outside the stamp workspace; "
                "move the arm into the safe region before teleop. "
                f"pose={list(first.ee_position_m)} source={workspace_source}. detail={exc}"
            ) from exc

        fixed_rpy = tuple(float(value) for value in first.ee_rpy_rad)
        first_ee = [float(value) for value in first.ee_position_m]
        hold_flange_z_m = float(first_ee[2]) if bool(args.lock_z) else None
        # Keep the Day-1 ~50 deg/s joint-rate budget when control_hz changes.
        # At 10 Hz, max_joint_step_deg=1 is only 10 deg/s and clips IK solutions so
        # heavily that commanded hold-Z Cartesian targets are not reachable → Z drift.
        max_joint_step_deg = max(1.0, 50.0 / control_hz)
        safety = CartesianSafetyFilter(
            workspace=workspace,
            limits=CartesianSafetyLimits(
                max_speed_m_s=max_speed_m_s,
                max_speed_m_s_per_axis=max_speed_m_s_per_axis,
                max_acceleration_m_s2=max_acceleration_m_s2,
                fixed_ee_rpy_rad=fixed_rpy,
                input_timeout_s=input_timeout_s,
                robot_state_timeout_s=robot_state_timeout_s,
            ),
        )
        # try_sc solves IK on the connected arm; offline Algo can disagree with real FK.
        inverse_kinematics = (
            RealManInverseKinematics.from_arm(backend.arm)
            if motion_enabled
            else RealManInverseKinematics.offline_rm65()
        )
        runner = build_manual_cartesian_runner(
            config=ManualCartesianConfig(
                control_rate_hz=control_hz,
                enable_motion=motion_enabled,
                fixed_ee_rpy_rad=fixed_rpy,
                hold_flange_z_m=hold_flange_z_m,
                max_joint_step_deg=max_joint_step_deg,
            ),
            teleop=teleop,
            robot_state_source=RealtimeCartesianStateSource(backend),
            safety_filter=safety,
            inverse_kinematics=inverse_kinematics,
            joint_commander=commander,
            gripper_actuator=gripper_actuator,
            observation_synchronizer=observation_synchronizer,
        )

        if args.record_dir is not None:
            episode_dir = Path(args.record_dir)
            run_dir = episode_dir.parent
            run_dir.mkdir(parents=True, exist_ok=True)
            run_id = args.run_id or run_dir.name
            episode_id = args.episode_id or datetime.now(tz=UTC).strftime("episode-%Y%m%d-%H%M%S")
            effective_config_path = run_dir / "effective_config.yaml"
            write_effective_config_yaml(
                effective_config_path,
                {
                    "teleop": {
                        "ip": args.ip,
                        "port": args.port,
                        "control_hz": control_hz,
                        "enable_motion": motion_enabled,
                        "enable_cameras": True,
                        "duration_s": duration_s,
                        "planned_steps": total_steps,
                        "move_increment_m": move_increment_m,
                        "move_increment_xy_m": move_increment_xy_m,
                        "move_increment_z_m": move_increment_z_m,
                        "max_linear_speed_m_s": max_speed_m_s,
                        "max_linear_speed_xy_m_s": max_speed_xy_m_s,
                        "max_linear_speed_z_m_s": max_speed_z_m_s,
                        "max_speed_m_s_per_axis": (
                            None if max_speed_m_s_per_axis is None else list(max_speed_m_s_per_axis)
                        ),
                        "xy_yaw_deg": float(args.xy_yaw_deg),
                        "lock_z": bool(args.lock_z),
                        "hold_flange_z_m": hold_flange_z_m,
                        "max_joint_step_deg": max_joint_step_deg,
                        "canfd_follow": bool(args.canfd_follow) if motion_enabled else None,
                        "canfd_smoothing": canfd_smoothing if motion_enabled else None,
                        "workspace_source": workspace_source,
                        "motion_config_source": config_source,
                        "enable_gripper": bool(args.enable_gripper),
                        "gripper_config_source": gripper_config_source,
                        "end_on_gripper_release": end_on_gripper_release,
                        "post_release_s": post_release_s,
                        "go_to_ready": go_to_ready,
                        "ready_config_source": None if ready_pose is None else ready_pose.source,
                        "ready_joints_deg": (
                            None if ready_pose is None else list(ready_pose.joint_position_deg)
                        ),
                    },
                    "task": {
                        "task_id": args.task_id,
                        "task_text": args.task_text,
                        "source_object": args.source_object,
                        "destination": args.destination,
                    },
                },
            )
            recorder = EpisodeRecorder(episode_dir)
            recorder.start(
                build_manual_episode_metadata(
                    episode_id=episode_id,
                    run_id=run_id,
                    task_id=args.task_id,
                    task_text=args.task_text,
                    source_object=args.source_object,
                    destination=args.destination,
                    control_rate_hz=control_hz,
                    effective_config_path=effective_config_path.as_posix(),
                )
            )

        startup_payload = {
            "operator_hint": (
                "Hold left SpaceMouse button (deadman) and move the cap. "
                + (
                    "Right button toggles gripper open/close (one pulse per press). "
                    + (
                        "Episode ends shortly after you release the object (open after a grasp); "
                        f"{duration_s:.0f}s is the max duration. "
                        if end_on_gripper_release
                        else ""
                    )
                    if gripper_actuator is not None
                    else ""
                )
                + (
                    f"Full stick ≈ {move_increment_xy_m * 1000.0:.1f} mm/tick XY "
                    f"({max_speed_xy_m_s * 1000.0:.1f} mm/s), "
                    f"{move_increment_z_m * 1000.0:.1f} mm/tick Z "
                    f"({max_speed_z_m_s * 1000.0:.1f} mm/s). "
                    if max_speed_m_s_per_axis is not None
                    else f"Full stick ≈ {move_increment_m * 1000.0:.1f} mm/tick "
                    f"({max_speed_m_s * 1000.0:.1f} mm/s). "
                )
                + (
                    "MOTION ENABLED: keep teach-pendant estop ready; release deadman to stop command stream."
                    if motion_enabled
                    else "Motion is disabled; this is a dry-run."
                )
            ),
            "duration_s": duration_s,
            "planned_steps": total_steps,
            "control_hz": control_hz,
            "enable_motion": motion_enabled,
            "enable_cameras": bool(args.enable_cameras),
            "move_increment_mm": round(float(move_increment_m) * 1000.0, 3),
            "move_increment_xy_mm": round(float(move_increment_xy_m) * 1000.0, 3),
            "move_increment_z_mm": round(float(move_increment_z_m) * 1000.0, 3),
            "max_linear_speed_mm_s": round(max_speed_m_s * 1000.0, 3),
            "max_linear_speed_xy_mm_s": round(max_speed_xy_m_s * 1000.0, 3),
            "max_linear_speed_z_mm_s": round(max_speed_z_m_s * 1000.0, 3),
            "max_linear_speed_source": speed_source,
            "xy_yaw_deg": float(args.xy_yaw_deg),
            "lock_z": bool(args.lock_z),
            "hold_flange_z_mm": (None if hold_flange_z_m is None else round(hold_flange_z_m * 1000.0, 3)),
            "max_joint_step_deg": max_joint_step_deg,
            "ik_source": ("connected_arm" if motion_enabled else "offline_algo"),
            "input_timeout_s": input_timeout_s,
            "max_acceleration_m_s2": max_acceleration_m_s2,
            "canfd_follow": (bool(args.canfd_follow) if motion_enabled else None),
            "canfd_smoothing": (canfd_smoothing if motion_enabled else None),
            "motion_config_source": config_source,
            "workspace_source": workspace_source,
            "start_ee_position_m": first_ee,
            "go_to_ready": go_to_ready,
            "ready_joints_deg": None if ready_pose is None else list(ready_pose.joint_position_deg),
            "end_on_gripper_release": end_on_gripper_release,
            "post_release_s": post_release_s,
        }
        if episode_dir is not None:
            startup_payload["record_dir"] = str(episode_dir)
            startup_payload["episode_id"] = recorder.metadata.episode_id if recorder else None
        print(json.dumps(startup_payload, indent=2), flush=True)

        steps = []
        started_ns = time.perf_counter_ns()
        next_progress_s = 5.0
        abort_reason: str | None = None
        end_trigger: str | None = None
        grasp_seen = False
        post_release_deadline_ns: int | None = None
        for index in range(total_steps):
            now_ns = time.perf_counter_ns()
            try:
                step = runner.step(now_monotonic_ns=now_ns, dt_s=period_s)
            except CartesianSafetyError as exc:
                abort_reason = repr(exc)
                print(json.dumps({"abort": "cartesian_safety", "detail": abort_reason}), flush=True)
                break
            steps.append(step)
            if recorder is not None:
                record_cartesian_control_step(recorder, step)

            if end_on_gripper_release and step.human_action.gripper_button_edge:
                target = step.human_action.gripper_target_open_fraction
                if target is not None:
                    if float(target) < 0.5:
                        grasp_seen = True
                    elif grasp_seen and post_release_deadline_ns is None:
                        end_trigger = "gripper_release"
                        post_release_deadline_ns = now_ns + int(post_release_s * 1_000_000_000)
                        print(
                            json.dumps(
                                {
                                    "end_trigger": end_trigger,
                                    "post_release_s": post_release_s,
                                    "steps_done": index + 1,
                                }
                            ),
                            flush=True,
                        )

            elapsed_s = (time.perf_counter_ns() - started_ns) / 1_000_000_000
            if elapsed_s >= next_progress_s:
                progress_payload = {
                    "progress_s": round(elapsed_s, 1),
                    "steps_done": index + 1,
                    "deadman_active": step.human_action.deadman_active,
                    "motion_sent": step.motion_sent,
                    "command_mm_s": [
                        round(value * 1000.0, 2) for value in step.human_action.linear_velocity_m_s
                    ],
                    "requested_z_mm": round(float(step.requested_ee_position_m[2]) * 1000.0, 2),
                    "ee_z_mm": round(float(step.robot_state.ee_position_m[2]) * 1000.0, 2),
                }
                if step.synced_observation is not None:
                    progress_payload["wrist_camera_age_ms"] = step.synced_observation.wrist_age_ms
                    progress_payload["external_camera_age_ms"] = step.synced_observation.external_age_ms
                    progress_payload["sync_warnings"] = list(step.synced_observation.warnings)
                print(json.dumps(progress_payload), flush=True)
                next_progress_s += 5.0

            if post_release_deadline_ns is not None and now_ns >= post_release_deadline_ns:
                break

            if index + 1 < total_steps:
                time.sleep(period_s)

        if end_trigger is None and not abort_reason and steps:
            end_trigger = "max_duration"

        if not steps:
            if recorder is not None and recorder.is_recording:
                recorder.abort(
                    failure_reason=abort_reason or "no_steps",
                    ended_at_utc=datetime.now(tz=UTC),
                )
            print(json.dumps({"status": "aborted", "reason": abort_reason or "no_steps"}), flush=True)
            return 1

        if recorder is not None and recorder.is_recording:
            if abort_reason:
                recorder.abort(failure_reason=abort_reason, ended_at_utc=datetime.now(tz=UTC))
            else:
                recorder.end(success=True, ended_at_utc=datetime.now(tz=UTC))

        active_steps = [step for step in steps if step.human_action.deadman_active]
        nonzero_steps = [
            step
            for step in active_steps
            if any(abs(value) > 1e-9 for value in step.human_action.linear_velocity_m_s)
        ]
        first_step = steps[0]
        last = steps[-1]
        last_ee = [float(value) for value in last.robot_state.ee_position_m]
        delta_ee_mm = [round((end - start) * 1000.0, 3) for start, end in zip(first_ee, last_ee, strict=True)]
        summary = {
            "status": "aborted" if abort_reason else "ok",
            "abort_reason": abort_reason,
            "end_trigger": end_trigger,
            "enable_motion": motion_enabled,
            "enable_cameras": bool(args.enable_cameras),
            "teleop": "hid_spacemouse",
            "workspace_source": workspace_source,
            "motion_config_source": config_source,
            "move_increment_mm": round(float(move_increment_m) * 1000.0, 3),
            "move_increment_xy_mm": round(float(move_increment_xy_m) * 1000.0, 3),
            "move_increment_z_mm": round(float(move_increment_z_m) * 1000.0, 3),
            "max_linear_speed_mm_s": round(max_speed_m_s * 1000.0, 3),
            "max_linear_speed_xy_mm_s": round(max_speed_xy_m_s * 1000.0, 3),
            "max_linear_speed_z_mm_s": round(max_speed_z_m_s * 1000.0, 3),
            "duration_s": round((time.perf_counter_ns() - started_ns) / 1_000_000_000, 3),
            "steps": len(steps),
            "deadman_active_steps": len(active_steps),
            "nonzero_command_steps": len(nonzero_steps),
            "commands_sent": getattr(commander, "commands_sent", 0),
            "gripper_commands_sent": (
                None if gripper_actuator is None else getattr(gripper_actuator, "commands_sent", 0)
            ),
            "callback_error_count": backend.cache.callback_error_count,
            "first_input_age_ms": first_step.human_action.input_age_ms,
            "last_input_age_ms": last.human_action.input_age_ms,
            "first_robot_state_age_ms": first_step.robot_state.robot_state_age_ms,
            "last_robot_state_age_ms": last.robot_state.robot_state_age_ms,
            "last_linear_velocity_m_s": list(last.human_action.linear_velocity_m_s),
            "start_ee_position_m": first_ee,
            "last_ee_position_m": last_ee,
            "delta_ee_mm": delta_ee_mm,
            "last_requested_ee_position_m": list(last.requested_ee_position_m),
            "last_joint_target_deg": list(last.executed_action.joint_target_deg or ()),
            "motion_sent_any": any(step.motion_sent for step in steps),
            "safety_intervened_any": any(step.executed_action.safety_intervened for step in steps),
            "safety_reasons": sorted(
                {reason for step in steps for reason in step.executed_action.safety_reasons}
            ),
        }
        if last.synced_observation is not None:
            summary["wrist_camera_present_steps"] = sum(
                1
                for step in steps
                if step.synced_observation is not None
                and step.synced_observation.observation.wrist_camera is not None
            )
            summary["external_camera_present_steps"] = sum(
                1
                for step in steps
                if step.synced_observation is not None
                and step.synced_observation.observation.external_camera is not None
            )
            summary["sync_warning_counts"] = {
                warning: sum(
                    1
                    for step in steps
                    if step.synced_observation is not None and warning in step.synced_observation.warnings
                )
                for warning in sorted(
                    {
                        warning
                        for step in steps
                        if step.synced_observation is not None
                        for warning in step.synced_observation.warnings
                    }
                )
            }
            summary["last_wrist_camera_age_ms"] = last.synced_observation.wrist_age_ms
            summary["last_external_camera_age_ms"] = last.synced_observation.external_age_ms
        if recorder is not None:
            summary["record_dir"] = str(episode_dir)
            summary["recorded_steps"] = recorder.step_count
            summary["episode_status"] = recorder.status
            if effective_config_path is not None:
                summary["effective_config_path"] = str(effective_config_path)
        print(json.dumps(summary, indent=2), flush=True)
    finally:
        if recorder is not None and recorder.is_recording:
            recorder.abort(failure_reason="interrupted", ended_at_utc=datetime.now(tz=UTC))
        if camera_session is not None:
            camera_session.stop()
        if gripper_device is not None:
            gripper_device.disconnect()
        if isinstance(commander, RealManCanfdJointCommander) and commander.commands_sent > 0:
            try:
                slow_stop_status = commander.slow_stop()
                print(json.dumps({"slow_stop_status": slow_stop_status}), flush=True)
            except Exception as exc:
                print(json.dumps({"slow_stop_warning": repr(exc)}), flush=True)
        backend.disconnect()
        teleop.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
