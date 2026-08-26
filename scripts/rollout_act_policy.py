"""ACT policy rollout: live observations, chunk playback, optional motion dispatch.

Motion requires dual confirmation (local config enable_motion + CLI --allow-motion).
Default is observe + print only; add motion flags only after go-to-ready and safety checks.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from typing import Any

import numpy as np
from sharedautonomy.control.motion_gate import load_motion_enable_config, resolve_motion_enabled
from sharedautonomy.control.observation import (
    CartesianProprioceptiveSource,
    build_camera_session_from_config,
    build_observation_synchronizer,
    load_camera_runtime_config,
)
from sharedautonomy.control.realtime import RealtimeCartesianStateSource
from sharedautonomy.data.lerobot_export import ProprioHistoryTracker
from sharedautonomy.policies.act.live_infer import (
    JsonHttpClient,
    both_rgb_present,
    build_infer_observation,
    resolve_infer_state_names,
    wait_for_both_cameras,
)
from sharedautonomy.policies.act.protocol import (
    DEFAULT_JPEG_QUALITY,
    JPEG_IMAGE_ENCODING,
    RAW_IMAGE_ENCODING,
    InferObservation,
    observation_to_payload,
    payload_to_response,
)
from sharedautonomy.policies.act.rollout import (
    ActChunkPlayer,
    ActRolloutConfig,
    ActRolloutLoop,
    GripperActuator,
    InferMode,
    NoOpGripperActuator,
    ThresholdGripperActuator,
    rate_limit_sleep,
)
from sharedautonomy.robot.canfd_commander import RealManCanfdJointCommander
from sharedautonomy.robot.gripper_config import load_serial_soft_gripper_stack
from sharedautonomy.robot.ready_pose import load_ready_pose_config, move_arm_to_ready_joints
from sharedautonomy.robot.realtime_state import RealManRealtimeStateSource
from sharedautonomy.tasks.shape_pick_place_v1 import resolve_episode_task_text

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "ACT rollout: dual-camera observations, chunk playback, optional joint dispatch. "
            "Motion requires config enable_motion=true AND --allow-motion."
        )
    )
    parser.add_argument("--ip", required=True, help="RM-65B controller IP address")
    parser.add_argument("--port", type=int, default=8080, help="RM-65B SDK port (default 8080)")
    parser.add_argument(
        "--infer-url",
        default="http://127.0.0.1:8088",
        help="ACT server base URL (default http://127.0.0.1:8088)",
    )
    parser.add_argument("--control-hz", type=float, default=10.0, help="Control loop rate (default 10)")
    parser.add_argument(
        "--duration-s",
        type=float,
        default=40.0,
        help="Rollout duration in seconds (default 40)",
    )
    parser.add_argument("--steps", type=int, default=None, help="Optional hard step cap")
    parser.add_argument(
        "--infer-mode",
        choices=(InferMode.BLOCKING.value, InferMode.ASYNC.value),
        default=InferMode.BLOCKING.value,
        help="blocking: refill chunk synchronously; async: background dequeue refill",
    )
    parser.add_argument(
        "--reset-every",
        type=int,
        default=25,
        help="Replan with fresh observation every N executed steps (default 25 = 2.5s @ 10Hz)",
    )
    parser.add_argument(
        "--n-action-steps",
        type=int,
        default=None,
        help="Optional cap on blind-play window (<= server n_action_steps); defaults to reset-every",
    )
    parser.add_argument("--task-text", default=None, help="Explicit ACT task string")
    parser.add_argument("--source-object", default=None, help="Task card object_id (e.g. blue)")
    parser.add_argument("--destination", default=None, help="Task card destination_id (e.g. down)")
    parser.add_argument(
        "--max-joint-step-deg",
        type=float,
        default=None,
        help="Per-step joint clip (default: 50/control_hz deg, ~50 deg/s at 10Hz)",
    )
    parser.add_argument(
        "--image-encoding",
        choices=(JPEG_IMAGE_ENCODING, RAW_IMAGE_ENCODING),
        default=JPEG_IMAGE_ENCODING,
        help="Wire image encoding (default jpeg_b64)",
    )
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        default=DEFAULT_JPEG_QUALITY,
        help=f"JPEG quality when --image-encoding={JPEG_IMAGE_ENCODING}",
    )
    parser.add_argument("--timeout-s", type=float, default=30.0, help="HTTP timeout per infer request")
    parser.add_argument(
        "--camera-ready-timeout-s",
        type=float,
        default=10.0,
        help="Seconds to wait for wrist+external RGB after camera start",
    )
    parser.add_argument(
        "--max-consecutive-camera-misses",
        type=int,
        default=20,
        help="Abort after this many consecutive camera-miss steps",
    )
    parser.add_argument("--print-every", type=int, default=1, help="Print JSON every N successful steps")
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
        help="CAN-FD follow mode when motion is enabled (default low-follow / False)",
    )
    parser.add_argument(
        "--canfd-smoothing",
        type=int,
        default=50,
        help="rm_movej_canfd radio/smoothing when motion is enabled (default 50)",
    )
    parser.add_argument(
        "--enable-gripper",
        action="store_true",
        help="Drive the serial soft gripper from predicted gripper open_fraction",
    )
    parser.add_argument("--gripper-config", default=None, help="Path to gripper_serial YAML")
    parser.add_argument(
        "--go-to-ready",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Move to ready pose before rollout (default: true when motion enabled)",
    )
    parser.add_argument(
        "--ready-config",
        default=None,
        help="Optional ready_pose YAML override (default: configs/local/manual_cartesian.local.yaml)",
    )
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    parser.add_argument(
        "--state-dim",
        type=int,
        default=None,
        help="Fallback state dimension when /health does not provide state_names.",
    )
    return parser.parse_args()


def _make_infer_fn(
    *,
    http_client: JsonHttpClient,
    task_text: str,
    image_encoding: str,
    jpeg_quality: int,
    timeout_s: float,
) -> Any:
    def infer_fn(obs: InferObservation) -> tuple[Any, dict[str, Any], float]:
        t_enc = time.perf_counter()
        payload = observation_to_payload(
            obs,
            image_encoding=image_encoding,
            jpeg_quality=jpeg_quality,
        )
        encode_ms = (time.perf_counter() - t_enc) * 1000.0
        t0 = time.perf_counter()
        result = http_client.post("/infer", payload, timeout_s=timeout_s)
        rtt_ms = (time.perf_counter() - t0) * 1000.0
        if not result.get("ok", True) and "action" not in result:
            raise RuntimeError(f"infer failed: {result}")
        response = payload_to_response(result)
        meta = {
            "encode_ms": round(encode_ms, 1),
            "server_timings_ms": result.get("timings_ms"),
        }
        return response, meta, rtt_ms

    infer_fn.task_text = task_text  # type: ignore[attr-defined]
    return infer_fn


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if float(args.control_hz) <= 0.0:
        raise ValueError("control-hz must be positive")
    if int(args.reset_every) < 0:
        raise ValueError("reset-every must be >= 0")

    config_enable_motion, config_source = load_motion_enable_config(
        cli_config_enable_motion=args.config_enable_motion
    )
    motion_enabled = resolve_motion_enabled(
        config_enable_motion=config_enable_motion,
        cli_allow_motion=args.allow_motion,
    )
    go_to_ready = bool(motion_enabled) if args.go_to_ready is None else bool(args.go_to_ready)
    if go_to_ready and not motion_enabled:
        raise ValueError("--go-to-ready requires motion enabled (--config-enable-motion and --allow-motion)")
    if args.enable_gripper and not motion_enabled:
        raise ValueError("--enable-gripper requires motion enabled")

    period_s = 1.0 / float(args.control_hz)
    if args.steps is not None:
        if int(args.steps) < 1:
            raise ValueError("steps must be >= 1")
        total_steps = int(args.steps)
    else:
        if float(args.duration_s) <= 0.0:
            raise ValueError("duration-s must be positive")
        total_steps = max(1, int(round(float(args.duration_s) * float(args.control_hz))))

    max_joint_step_deg = (
        max(1.0, 50.0 / float(args.control_hz))
        if args.max_joint_step_deg is None
        else float(args.max_joint_step_deg)
    )
    if max_joint_step_deg <= 0.0:
        raise ValueError("max-joint-step-deg must be positive")

    if args.task_text is None and (args.source_object is None or args.destination is None):
        raise ValueError(
            "ACT conditioning requires --task-text, or both --source-object and --destination"
        )
    task_text = resolve_episode_task_text(
        task_text=args.task_text,
        source_object=args.source_object,
        destination=args.destination,
    )

    rollout_config = ActRolloutConfig(
        control_hz=float(args.control_hz),
        reset_every=int(args.reset_every),
        n_action_steps=args.n_action_steps,
        infer_mode=InferMode(str(args.infer_mode)),
        max_joint_step_deg=max_joint_step_deg,
    )

    base = str(args.infer_url).rstrip("/")
    http_client = JsonHttpClient(base, timeout_s=float(args.timeout_s))
    health = http_client.get("/health", timeout_s=min(10.0, float(args.timeout_s)))
    if not health.get("ok"):
        raise RuntimeError(f"ACT server health check failed: {health}")
    state_names = resolve_infer_state_names(health, override_dim=args.state_dim)

    infer_fn = _make_infer_fn(
        http_client=http_client,
        task_text=task_text,
        image_encoding=str(args.image_encoding),
        jpeg_quality=int(args.jpeg_quality),
        timeout_s=float(args.timeout_s),
    )

    backend = RealManRealtimeStateSource(ip=args.ip, port=args.port)
    camera_runtime = load_camera_runtime_config()
    camera_session, sync_config = build_camera_session_from_config(camera_runtime)
    if camera_session.wrist_camera is None or camera_session.external_camera is None:
        raise RuntimeError("Wrist RealSense and external UVC configs are required")

    proprio_source = CartesianProprioceptiveSource(RealtimeCartesianStateSource(backend))
    synchronizer = build_observation_synchronizer(
        proprioception=proprio_source,
        camera_session=camera_session,
        sync_config=sync_config,
    )

    commander: RealManCanfdJointCommander | None = None
    gripper_device = None
    gripper_actuator = None
    gripper_handler: GripperActuator = NoOpGripperActuator()
    ready_pose = load_ready_pose_config(config_path=args.ready_config) if go_to_ready else None

    backend.connect()
    if args.enable_gripper:
        gripper_device, gripper_stack, _gripper_source = load_serial_soft_gripper_stack(
            config_path=args.gripper_config,
        )
        gripper_actuator = gripper_stack
        gripper_handler = ThresholdGripperActuator(
            teleop=gripper_actuator,
            close_threshold=rollout_config.gripper_close_threshold,
            hysteresis=rollout_config.gripper_hysteresis,
        )

    if motion_enabled:
        commander = RealManCanfdJointCommander(
            backend.arm,
            follow=bool(args.canfd_follow),
            smoothing=int(args.canfd_smoothing),
            armed=True,
        )

    camera_session.start()
    player = ActChunkPlayer(config=rollout_config)
    loop = ActRolloutLoop(
        config=rollout_config,
        infer_fn=infer_fn,
        player=player,
        gripper=gripper_handler,
        motion_enabled=motion_enabled,
        joint_commander=commander,
    )

    latencies_ms: list[float] = []
    replan_count = 0
    skipped_camera_misses = 0
    consecutive_misses = 0
    commanded_gripper_fraction = 1.0
    proprio_history = ProprioHistoryTracker()

    try:
        first = backend.read_snapshot()
        print(
            json.dumps(
                {
                    "mode": "act_rollout",
                    "enable_motion": motion_enabled,
                    "infer_mode": rollout_config.infer_mode.value,
                    "reset_every": rollout_config.reset_every,
                    "blind_window_steps": rollout_config.blind_window_steps,
                    "operator_hint": (
                        "Keep teach-pendant estop ready. "
                        + (
                            "MOTION ENABLED: joint targets are sent each control step."
                            if motion_enabled
                            else "Dry rollout: actions are printed, not sent."
                        )
                    ),
                    "infer_url": base,
                    "task_text": task_text,
                    "control_hz": float(args.control_hz),
                    "planned_steps": total_steps,
                    "max_joint_step_deg": max_joint_step_deg,
                    "motion_config_source": config_source,
                    "start_joint_position_deg": list(first.joint_position_deg),
                    "server_health": health,
                    "server_state_dim": health.get("state_dim"),
                    "server_state_names": health.get("state_names"),
                    "client_state_dim": len(state_names),
                    "client_state_names": state_names,
                },
                indent=2,
            ),
            flush=True,
        )

        if go_to_ready and ready_pose is not None and commander is not None:
            print(
                json.dumps(
                    {
                        "go_to_ready": True,
                        "ready_joints_deg": list(ready_pose.joint_position_deg),
                        "canfd_follow": ready_pose.canfd_follow,
                        "canfd_smoothing": ready_pose.canfd_smoothing,
                        "settle_s": ready_pose.settle_s,
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
                gripper_actuator.move_to_working_open(
                    ready_physical if ready_physical > 0.0 else 0.0
                )
                commanded_gripper_fraction = float(gripper_actuator.commanded_open_fraction)

        wait_for_both_cameras(
            proprio_source=proprio_source,
            synchronizer=synchronizer,
            gripper_open_fraction=commanded_gripper_fraction,
            timeout_s=float(args.camera_ready_timeout_s),
        )

        for step_index in range(total_steps):
            loop_start = time.perf_counter()
            now_ns = time.perf_counter_ns()
            proprio_source.prime(now_monotonic_ns=now_ns)
            synced = synchronizer.capture(
                now_monotonic_ns=now_ns,
                gripper_commanded_open_fraction=commanded_gripper_fraction,
            )
            if not both_rgb_present(synced):
                consecutive_misses += 1
                skipped_camera_misses += 1
                print(
                    json.dumps(
                        {
                            "step": step_index,
                            "skip": "camera_missing",
                            "consecutive_misses": consecutive_misses,
                        }
                    ),
                    flush=True,
                )
                if consecutive_misses >= int(args.max_consecutive_camera_misses):
                    raise RuntimeError(
                        f"Too many consecutive camera misses ({consecutive_misses})"
                    )
                rate_limit_sleep(loop_start_s=loop_start, period_s=period_s)
                continue

            consecutive_misses = 0
            infer_obs = build_infer_observation(
                synced=synced,
                task=task_text,
                gripper_open_fraction=commanded_gripper_fraction,
                reset=False,
                history=proprio_history,
                state_names=state_names,
            )
            present_joints = np.asarray(
                synced.observation.joint_position_deg,
                dtype=np.float32,
            ).reshape(6)

            step_result = loop.run_step(
                step_index=step_index,
                obs=infer_obs,
                present_joints_deg=present_joints,
                sync_warnings=list(synced.warnings),
                wrist_age_ms=synced.wrist_age_ms,
                external_age_ms=synced.external_age_ms,
            )
            if step_result.replan:
                replan_count += 1
                if step_result.rtt_ms is not None:
                    latencies_ms.append(float(step_result.rtt_ms))
            if step_result.gripper_commanded:
                if gripper_actuator is not None:
                    commanded_gripper_fraction = float(gripper_actuator.commanded_open_fraction)
                else:
                    commanded_gripper_fraction = (
                        1.0 if float(step_result.gripper_open_fraction) >= 0.5 else 0.0
                    )

            if step_index % max(1, int(args.print_every)) == 0:
                print(
                    json.dumps(
                        {
                            "step": step_result.step_index,
                            "replan": step_result.replan,
                            "queue_depth_before": step_result.queue_depth_before,
                            "queue_depth_after": step_result.queue_depth_after,
                            "rtt_ms": step_result.rtt_ms,
                            "encode_ms": step_result.encode_ms,
                            "server_timings_ms": step_result.server_timings_ms,
                            "sync_warnings": step_result.sync_warnings,
                            "wrist_age_ms": step_result.wrist_age_ms,
                            "external_age_ms": step_result.external_age_ms,
                            "state": step_result.state,
                            "action": step_result.raw_action,
                            "safe_joints_deg": step_result.safe_joints_deg,
                            "gripper_open_fraction": step_result.gripper_open_fraction,
                            "gripper_commanded": step_result.gripper_commanded,
                            "motion_sent": step_result.motion_sent,
                        }
                    ),
                    flush=True,
                )

            if step_index + 1 < total_steps:
                rate_limit_sleep(loop_start_s=loop_start, period_s=period_s)

        summary = {
            "steps_planned": total_steps,
            "executed_steps": total_steps - skipped_camera_misses,
            "skipped_camera_misses": skipped_camera_misses,
            "replan_count": replan_count,
            "infer_mode": rollout_config.infer_mode.value,
            "blind_window_steps": rollout_config.blind_window_steps,
            "image_encoding": str(args.image_encoding),
            "rtt_ms_mean": round(float(np.mean(latencies_ms)), 1) if latencies_ms else None,
            "rtt_ms_p95": (
                round(float(np.percentile(latencies_ms, 95)), 1) if latencies_ms else None
            ),
            "enable_motion": motion_enabled,
            "joint_commands_sent": 0 if commander is None else commander.commands_sent,
        }
        print(json.dumps({"done": summary}, indent=2), flush=True)
        return 0
    except KeyboardInterrupt:
        print(json.dumps({"abort": "keyboard_interrupt"}), flush=True)
        return 130
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        print(json.dumps({"error": str(exc)}), flush=True)
        logger.exception("rollout_act_policy failed")
        return 1
    finally:
        loop.close()
        http_client.close()
        camera_session.stop()
        if commander is not None and commander.armed:
            try:
                commander.slow_stop()
            except Exception:
                logger.exception("slow_stop failed during cleanup")
            commander.disarm_motion()
        backend.disconnect()
        if gripper_device is not None:
            gripper_device.disconnect()


if __name__ == "__main__":
    sys.exit(main())
