"""Collect Cartesian SpaceMouse demonstrations with synchronized observations.

Motion stays disabled unless BOTH local config enable_motion and CLI
``--allow-motion`` are set. Without both gates, the collector runs a no-motion
preview that exercises observation, mapping, safety, and recording setup.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from sharedautonomy.assistance.cube_yaw_assist import CubeYawAssistConfig, ExternalCubeYawAssistPolicy
from sharedautonomy.assistance.safety_filter import CartesianSafetyFilter, CartesianSafetyLimits
from sharedautonomy.assistance.workspace_config import load_cartesian_workspace
from sharedautonomy.control.collection_runtime import resolve_collection_runtime
from sharedautonomy.control.manual import (
    ManualCartesianConfig,
    MockJointCommander,
    build_manual_cartesian_runner,
)
from sharedautonomy.control.motion_gate import load_motion_enable_config, resolve_motion_enabled
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
from sharedautonomy.data import AttemptLedgerError, AttemptLedgerSession, EpisodeRecorder
from sharedautonomy.data.schema import CollectionMode
from sharedautonomy.devices.spacemouse import HidSpaceMouse, SpaceMouseConfig
from sharedautonomy.perception.cube_yaw import (
    StartYawError,
    measure_start_yaw_from_rgb,
    resolve_start_yaw_bin,
    should_measure_start_yaw,
)
from sharedautonomy.perception.table_homography import DEFAULT_YAML_PATH, load_table_homography
from sharedautonomy.robot.canfd_commander import RealManCanfdJointCommander
from sharedautonomy.robot.gripper_config import load_serial_soft_gripper_stack
from sharedautonomy.robot.kinematics import RealManInverseKinematics
from sharedautonomy.robot.ready_pose import load_ready_pose_config, move_arm_to_ready_joints
from sharedautonomy.robot.realtime_state import RealManRealtimeStateSource
from sharedautonomy.robot.safety import CartesianSafetyError, validate_cartesian_segment
from sharedautonomy.tasks.shape_pick_place_v1 import resolve_episode_task_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect synchronized Cartesian SpaceMouse demonstrations. "
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
        help="Control loop rate. Default: 10 Hz with motion enabled, else 50 Hz no-motion preview.",
    )
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--deadzone", type=float, default=0.1)
    parser.add_argument(
        "--max-linear-speed-m-s",
        type=float,
        default=None,
        help=(
            "Teleop speed cap (m/s). Stick is soft-clamped to this. "
            "If omitted with motion enabled, derived from --move-increment-m / dt. "
            "No-motion preview default: 0.05."
        ),
    )
    parser.add_argument(
        "--move-increment-m",
        type=float,
        default=None,
        help=(
            "Full-stick Cartesian step per control tick (meters). "
            "Motion default: 0.01 (10 mm/tick @ 10 Hz ~= 100 mm/s). Ignored if "
            "--max-linear-speed-m-s is set."
        ),
    )
    parser.add_argument(
        "--xy-yaw-deg",
        type=float,
        default=90.0,
        help=(
            "Extra base-frame XY yaw after vertical_up (degrees). "
            "Default 90 (CCW about +Z). Use -90 to reverse the correction or 0 to disable it."
        ),
    )
    parser.add_argument(
        "--lock-z",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Zero base-frame Z command after mapping (XY planar only; isolates Z crosstalk)",
    )
    parser.add_argument(
        "--allow-tool-yaw",
        action="store_true",
        help=(
            "Allow Compact twist to rotate tool-axis yaw / J6 only (rx/ry stay frozen). "
            "Do not use allow_rotation; that would open pitch/roll. Default: off."
        ),
    )
    parser.add_argument(
        "--tool-yaw-sign",
        type=int,
        default=1,
        metavar="{1,-1}",
        help="Sign of Compact twist -> J6 overlay (default +1; pass --tool-yaw-sign=-1 if reversed)",
    )
    parser.add_argument(
        "--enable-yaw-assist",
        action="store_true",
        help=(
            "Shared-autonomy J6 edge alignment. Cube wrap90 is locked at start; "
            "each step overlays wrap90(cube - gripper table yaw from FK). IK "
            "holds current gripper yaw. Human twist override requires "
            "--allow-tool-yaw. Default: off. Recording defaults to "
            "--collection-mode shared_autonomy unless you pass that flag."
        ),
    )
    parser.add_argument(
        "--yaw-assist-max-rate-rad-s",
        type=float,
        default=0.4,
        help="Saturated tool-yaw assist rate (rad/s). Default matches SpaceMouse max angular speed.",
    )
    parser.add_argument(
        "--yaw-assist-deadband-deg",
        type=float,
        default=2.0,
        help="Stop J6 assist when abs(cube-gripper table wrap90 from FK) is within this many degrees.",
    )
    parser.add_argument(
        "--workspace-yaml",
        default=None,
        help=(
            "Optional path to measured rm65_safety YAML. Motion requires this path "
            "or configs/local/rm65_safety.local.yaml; no-motion previews use a generic fixture."
        ),
    )
    parser.add_argument(
        "--config-enable-motion",
        action="store_true",
        help=(
            "Configuration-side motion confirmation when no local config file is present; "
            "required with --allow-motion"
        ),
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
        help="CAN-FD follow mode when motion is enabled (default: low-follow / False)",
    )
    parser.add_argument(
        "--canfd-smoothing",
        type=int,
        default=50,
        help="rm_movej_canfd radio/smoothing when motion is enabled (default: 50)",
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
            "Parent directory receives effective_config.yaml. Every recording also "
            "appends start/finish rows to the attempt ledger (default: sibling of the "
            "run directory, e.g. outputs/runs/attempts.jsonl) so deleting a failed "
            "episode still leaves a retry count. Recommended layout: "
            "outputs/runs/<run_id>/episode. Use --collection-mode shared_autonomy "
            "for assisted demonstrations."
        ),
    )
    parser.add_argument(
        "--attempt-ledger",
        default=None,
        help=(
            "JSONL attempt ledger path. Default: outputs/runs/attempts.jsonl when "
            "--record-dir is outputs/runs/<run_id>/episode. Must not live inside the "
            "run directory. There is no opt-out: recording always writes the ledger."
        ),
    )
    parser.add_argument(
        "--layout-id",
        default=None,
        help=(
            "Paired-layout key for retry counting (e.g. 1 or L03). Default: run_id. "
            "Reuse the same id when re-recording a deleted failure so attempt_index increments."
        ),
    )
    parser.add_argument(
        "--yaw-bin",
        type=float,
        default=None,
        help=(
            "Override the opening cube yaw (deg, wrap90) written to the attempt ledger. "
            "Default for --allow-tool-yaw or --enable-yaw-assist recording: measure "
            "from the external camera at start. Not inferred from J6 at close."
        ),
    )
    measure_yaw = parser.add_mutually_exclusive_group()
    measure_yaw.add_argument(
        "--measure-start-yaw",
        dest="measure_start_yaw",
        action="store_true",
        help="Force a third-person wrap90 measurement at recording start.",
    )
    measure_yaw.add_argument(
        "--no-measure-start-yaw",
        dest="measure_start_yaw",
        action="store_false",
        help="Do not auto-measure opening cube yaw (use --yaw-bin or leave null).",
    )
    parser.set_defaults(measure_start_yaw=None)
    parser.add_argument(
        "--table-homography",
        default=None,
        help=(f"Table homography YAML for start-yaw (default: {DEFAULT_YAML_PATH.as_posix()})"),
    )
    parser.add_argument(
        "--collection-mode",
        choices=[mode.value for mode in CollectionMode],
        default=CollectionMode.MANUAL.value,
        help=(
            "Collection mode stored in episode metadata and the attempt ledger. "
            "Default: manual, or shared_autonomy when --enable-yaw-assist is set "
            "and this flag is omitted."
        ),
    )
    parser.add_argument("--run-id", default=None, help="Run identifier for episode metadata")
    parser.add_argument("--episode-id", default=None, help="Episode identifier (default: timestamp-based)")
    parser.add_argument("--task-id", default="teleop-smoke", help="Task id stored in episode metadata")
    parser.add_argument(
        "--task-text",
        default=None,
        help=(
            "Task description in episode metadata. If omitted and both "
            "--source-object and --destination are set, uses the shape_pick_place_v1 "
            "standard sentence from the task card."
        ),
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
            "Drive the serial soft gripper on SpaceMouse right-button edges "
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
            "Before teleop, move to the machine-local ready_pose via rm_movej_canfd. "
            "Default: on when motion is enabled, off for no-motion preview. "
        ),
    )
    parser.add_argument(
        "--ready-config",
        default=None,
        help="YAML containing ready_pose (default: configs/local/manual_cartesian.local.yaml)",
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


_START_YAW_FRAME_TIMEOUT_S = 2.0


def _wait_external_color_rgb(camera_session: object, *, timeout_s: float = _START_YAW_FRAME_TIMEOUT_S):
    external = getattr(camera_session, "external_camera", None) if camera_session is not None else None
    if external is None:
        raise StartYawError("start-yaw measurement needs the external RGB camera")
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        frame = external.read_camera(now_monotonic_ns=time.perf_counter_ns())
        if frame is not None:
            return frame.color_rgb
        time.sleep(0.02)
    raise StartYawError("timed out waiting for an external RGB frame to measure start yaw")


def _measure_start_cube_yaw(
    *,
    camera_session: object,
    homography_path: Path,
    j6_now_deg: float,
):
    if not homography_path.is_file():
        raise StartYawError(
            f"table homography not found: {homography_path}. "
            "Run scripts/calibrate_external_table_homography.py or pass --yaw-bin."
        )
    homography = load_table_homography(homography_path)
    color_rgb = _wait_external_color_rgb(camera_session)
    return measure_start_yaw_from_rgb(color_rgb, homography, j6_now_deg=j6_now_deg)


def _finish_attempt_ledger(
    session: AttemptLedgerSession | None,
    *,
    recorder: EpisodeRecorder | None,
    recording_status: str,
    end_trigger: str | None = None,
) -> None:
    if session is None or session.finished:
        return
    episode_success = None
    failure_reason = None
    step_count = None
    if recorder is not None:
        step_count = recorder.step_count
        metadata = recorder.metadata
        if metadata is not None:
            episode_success = metadata.success
            failure_reason = metadata.failure_reason
    session.finish(
        recording_status=recording_status,
        episode_success=episode_success,
        failure_reason=failure_reason,
        step_count=step_count,
        end_trigger=end_trigger,
    )


def _argv_has_flag(argv: list[str], flag: str) -> bool:
    prefix = flag + "="
    return any(item == flag or item.startswith(prefix) for item in argv)


def main() -> int:
    args = parse_args()
    if bool(args.enable_yaw_assist) and not _argv_has_flag(sys.argv, "--collection-mode"):
        args.collection_mode = CollectionMode.SHARED_AUTONOMY.value
    args.task_text = resolve_episode_task_text(
        task_text=args.task_text,
        source_object=args.source_object,
        destination=args.destination,
    )
    config_enable_motion, config_source = load_motion_enable_config(
        cli_config_enable_motion=args.config_enable_motion
    )
    motion_enabled = resolve_motion_enabled(
        config_enable_motion=config_enable_motion,
        cli_allow_motion=args.allow_motion,
    )
    runtime = resolve_collection_runtime(
        motion_enabled=motion_enabled,
        control_hz=args.control_hz,
        duration_s=args.duration_s,
        steps=args.steps,
        max_linear_speed_m_s=args.max_linear_speed_m_s,
        move_increment_m=args.move_increment_m,
    )
    control_hz = runtime.control_hz
    period_s = runtime.period_s
    duration_s = runtime.duration_s
    total_steps = runtime.total_steps
    max_speed_m_s = runtime.max_linear_speed_m_s
    max_speed_xy_m_s = runtime.max_linear_speed_xy_m_s
    max_speed_z_m_s = runtime.max_linear_speed_z_m_s
    max_speed_m_s_per_axis = runtime.max_speed_m_s_per_axis
    max_acceleration_m_s2 = runtime.max_acceleration_m_s2
    input_timeout_s = runtime.input_timeout_s
    robot_state_timeout_s = runtime.robot_state_timeout_s

    if motion_enabled and bool(args.canfd_follow) and period_s > 0.010 + 1e-12:
        raise ValueError(
            "High-follow CAN-FD requires control period <= 10 ms (SDK). "
            f"Got control-hz={control_hz} ({period_s * 1000.0:.1f} ms). "
            "Use --no-canfd-follow (default) or raise --control-hz to >= 100."
        )
    canfd_smoothing = int(args.canfd_smoothing)
    if canfd_smoothing < 0:
        raise ValueError("canfd-smoothing must be >= 0")
    if int(args.tool_yaw_sign) not in (1, -1):
        raise ValueError("tool-yaw-sign must be 1 or -1")

    if args.enable_yaw_assist and not args.enable_cameras:
        raise ValueError("--enable-yaw-assist requires --enable-cameras (third-person RGB)")
    if float(args.yaw_assist_max_rate_rad_s) <= 0.0:
        raise ValueError("yaw-assist-max-rate-rad-s must be positive")
    if float(args.yaw_assist_deadband_deg) < 0.0:
        raise ValueError("yaw-assist-deadband-deg must be >= 0")
    if args.record_dir is not None and not args.enable_cameras:
        raise ValueError("--record-dir requires --enable-cameras so each step has synced observations")
    if args.enable_gripper and not motion_enabled:
        raise ValueError(
            "--enable-gripper requires motion enabled (--config-enable-motion and --allow-motion)"
        )
    end_on_gripper_release = (
        bool(args.enable_gripper)
        if args.end_on_gripper_release is None
        else bool(args.end_on_gripper_release)
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

    workspace, workspace_source = load_cartesian_workspace(
        args.workspace_yaml,
        allow_example_fallback=not motion_enabled,
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
            allow_tool_yaw=bool(args.allow_tool_yaw),
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
    ledger_session: AttemptLedgerSession | None = None
    episode_dir: Path | None = None
    effective_config_path: Path | None = None
    abort_reason: str | None = None
    end_trigger: str | None = None
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
                "Current flange pose is outside the configured workspace; "
                "move the arm into the safe region before teleop. "
                f"pose={list(first.ee_position_m)} source={workspace_source}. detail={exc}"
            ) from exc

        j6_now_deg = float(first.joint_position_deg[5]) if len(first.joint_position_deg) >= 6 else 0.0
        measure_start_yaw = should_measure_start_yaw(
            cli_measure_start_yaw=args.measure_start_yaw,
            recording=args.record_dir is not None,
            allow_tool_yaw=bool(args.allow_tool_yaw),
            enable_yaw_assist=bool(args.enable_yaw_assist),
        )
        start_yaw_estimate = None
        if measure_start_yaw:
            try:
                start_yaw_estimate = _measure_start_cube_yaw(
                    camera_session=camera_session,
                    homography_path=Path(args.table_homography or DEFAULT_YAML_PATH),
                    j6_now_deg=j6_now_deg,
                )
            except StartYawError:
                if args.yaw_bin is None and args.record_dir is not None:
                    raise
        start_yaw_bin, start_yaw_extra = resolve_start_yaw_bin(
            cli_yaw_bin_deg=args.yaw_bin,
            estimate=start_yaw_estimate,
            required=bool(measure_start_yaw) and args.yaw_bin is None and args.record_dir is not None,
        )
        if measure_start_yaw or start_yaw_bin is not None:
            print(
                json.dumps(
                    {
                        "start_yaw": {
                            "yaw_bin_deg": start_yaw_bin,
                            "j6_now_deg": j6_now_deg,
                            **start_yaw_extra,
                        }
                    },
                    indent=2,
                ),
                flush=True,
            )

        fixed_rpy = tuple(float(value) for value in first.ee_rpy_rad)
        first_ee = [float(value) for value in first.ee_position_m]
        hold_flange_z_m = float(first_ee[2]) if bool(args.lock_z) else None
        # Preserve the validated ~50 deg/s joint-rate budget when control_hz changes.
        # At 10 Hz, max_joint_step_deg=1 is only 10 deg/s and clips IK solutions so
        # heavily that commanded hold-Z Cartesian targets are not reachable, causing Z drift.
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
                fixed_orientation_axes=(0, 1)
                if bool(args.allow_tool_yaw) or bool(args.enable_yaw_assist)
                else (0, 1, 2),
            ),
        )
        # Real collection solves IK on the connected arm; offline Algo can disagree with real FK.
        inverse_kinematics = (
            RealManInverseKinematics.from_arm(backend.arm)
            if motion_enabled
            else RealManInverseKinematics.offline_rm65()
        )
        yaw_assist_policy = None
        yaw_assist_homography_path: str | None = None
        if args.enable_yaw_assist:
            homography_path = Path(args.table_homography or DEFAULT_YAML_PATH)
            if not homography_path.is_file():
                raise StartYawError(
                    f"table homography not found: {homography_path}. "
                    "Run scripts/calibrate_external_table_homography.py before --enable-yaw-assist."
                )
            yaw_assist_homography_path = homography_path.as_posix()
            yaw_assist_policy = ExternalCubeYawAssistPolicy(
                load_table_homography(homography_path),
                CubeYawAssistConfig(
                    max_yaw_rate_rad_s=float(args.yaw_assist_max_rate_rad_s),
                    deadband_deg=float(args.yaw_assist_deadband_deg),
                ),
                locked_wrap90_deg=start_yaw_bin,
                lock_on_first_detection=start_yaw_bin is None,
            )
        runner = build_manual_cartesian_runner(
            config=ManualCartesianConfig(
                control_rate_hz=control_hz,
                enable_motion=motion_enabled,
                allow_tool_yaw=bool(args.allow_tool_yaw),
                enable_yaw_assist=bool(args.enable_yaw_assist),
                tool_yaw_sign=float(args.tool_yaw_sign),
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
            yaw_assist_policy=yaw_assist_policy,
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
                        "enable_motion": motion_enabled,
                        "enable_cameras": True,
                        **runtime.effective_config_fields(),
                        "xy_yaw_deg": float(args.xy_yaw_deg),
                        "lock_z": bool(args.lock_z),
                        "allow_tool_yaw": bool(args.allow_tool_yaw),
                        "enable_yaw_assist": bool(args.enable_yaw_assist),
                        "yaw_assist_locked_wrap90_deg": start_yaw_bin,
                        "yaw_assist_max_rate_rad_s": float(args.yaw_assist_max_rate_rad_s),
                        "yaw_assist_deadband_deg": float(args.yaw_assist_deadband_deg),
                        "yaw_assist_homography": yaw_assist_homography_path,
                        "tool_yaw_sign": int(args.tool_yaw_sign),
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
                        "collection_mode": args.collection_mode,
                        "layout_id": args.layout_id or run_id,
                        "yaw_bin_deg": start_yaw_bin,
                        "start_yaw": start_yaw_extra or None,
                    },
                },
            )
            collection_mode = CollectionMode(args.collection_mode)
            ledger_session = AttemptLedgerSession.begin(
                record_dir=episode_dir,
                run_id=run_id,
                episode_id=episode_id,
                layout_id=args.layout_id,
                collection_mode=collection_mode,
                ledger_path=args.attempt_ledger,
                task_id=args.task_id,
                task_text=args.task_text,
                source_object=args.source_object,
                destination=args.destination,
                yaw_bin_deg=start_yaw_bin,
                argv=sys.argv,
                extra=start_yaw_extra or None,
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
                    collection_mode=collection_mode,
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
                + runtime.full_stick_hint()
                + (
                    "Twist the cap to rotate tool-axis yaw / J6; pitch and roll stay frozen. "
                    if bool(args.allow_tool_yaw)
                    else ""
                )
                + (
                    "Yaw assist ON: J6 overlay is wrap90(locked cube - FK gripper heading); "
                    "hold that pose when aligned or after close. "
                    if bool(args.enable_yaw_assist)
                    else ""
                )
                + (
                    "MOTION ENABLED: keep teach-pendant estop ready; release deadman to stop command stream."
                    if motion_enabled
                    else "Motion is disabled; this is a no-motion preview."
                )
            ),
            **runtime.startup_report_fields(),
            "enable_motion": motion_enabled,
            "enable_cameras": bool(args.enable_cameras),
            "xy_yaw_deg": float(args.xy_yaw_deg),
            "lock_z": bool(args.lock_z),
            "allow_tool_yaw": bool(args.allow_tool_yaw),
            "enable_yaw_assist": bool(args.enable_yaw_assist),
            "yaw_assist_locked_wrap90_deg": start_yaw_bin,
            "tool_yaw_sign": int(args.tool_yaw_sign),
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
        if ledger_session is not None:
            startup_payload["attempt_ledger"] = str(ledger_session.ledger_path)
            startup_payload["attempt_id"] = ledger_session.attempt_id
            startup_payload["attempt_index"] = ledger_session.attempt_index
            startup_payload["layout_id"] = ledger_session.layout_id
            startup_payload["collection_mode"] = ledger_session.collection_mode
            startup_payload["yaw_bin_deg"] = start_yaw_bin
        if args.enable_yaw_assist and args.collection_mode == CollectionMode.MANUAL.value:
            startup_payload["collection_mode_hint"] = (
                "pass --collection-mode shared_autonomy for SA paired collection"
            )
        print(json.dumps(startup_payload, indent=2), flush=True)

        steps = []
        started_ns = time.perf_counter_ns()
        next_progress_s = 5.0
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
                if step.assist_action is not None:
                    progress_payload["assist_wz_rad_s"] = round(
                        float(step.assist_action.angular_velocity_rad_s[2]), 4
                    )
                    progress_payload["assist_authority"] = round(float(step.executed_action.authority), 3)
                    progress_payload["assist_confidence"] = round(float(step.assist_action.confidence), 3)
                    progress_payload["assist_reason"] = step.assist_reason
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
            **runtime.speed_report_fields(),
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
            "yaw_assist_steps": sum(1 for step in steps if step.assist_action is not None),
            "yaw_assist_mean_authority": (
                None
                if not any(step.assist_action is not None for step in steps)
                else round(
                    sum(float(step.executed_action.authority) for step in steps) / len(steps),
                    3,
                )
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
        if ledger_session is not None:
            summary["attempt_ledger"] = str(ledger_session.ledger_path)
            summary["attempt_id"] = ledger_session.attempt_id
            summary["attempt_index"] = ledger_session.attempt_index
            summary["layout_id"] = ledger_session.layout_id
        print(json.dumps(summary, indent=2), flush=True)
    finally:
        if recorder is not None and recorder.is_recording:
            recorder.abort(failure_reason="interrupted", ended_at_utc=datetime.now(tz=UTC))
        if ledger_session is not None and not ledger_session.finished:
            recording_status = "error"
            if recorder is not None:
                if recorder.status == "completed":
                    recording_status = "completed"
                elif recorder.status == "aborted":
                    reason = None if recorder.metadata is None else recorder.metadata.failure_reason
                    recording_status = "interrupted" if reason == "interrupted" else "aborted"
            try:
                _finish_attempt_ledger(
                    ledger_session,
                    recorder=recorder,
                    recording_status=recording_status,
                    end_trigger=end_trigger,
                )
            except AttemptLedgerError as exc:
                print(json.dumps({"attempt_ledger_warning": repr(exc)}), flush=True)
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
