"""SmolVLA live rollout with optional, safety-gated RM-65 motion.

The server owns SmolVLA's action queue and returns one action per request.
Motion is disabled by default and requires both local configuration and
``--allow-motion``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from sharedautonomy.control.motion_gate import resolve_motion_enabled
from sharedautonomy.policies.act.live_infer import (
    JsonHttpClient,
    both_rgb_present,
    build_infer_observation,
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
from sharedautonomy.robot.safety import clip_joint_targets
from sharedautonomy.tasks.shape_pick_place_v1 import resolve_episode_task_text

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "SmolVLA rollout: dual-camera observations, one-action HTTP inference, "
            "optional safety-gated RM-65 motion."
        )
    )
    parser.add_argument("--ip", required=True, help="RM-65B controller IP address")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--infer-url",
        default="http://127.0.0.1:8089",
        help="SmolVLA server base URL",
    )
    parser.add_argument("--control-hz", type=float, default=10.0)
    parser.add_argument("--duration-s", type=float, default=40.0)
    parser.add_argument("--steps", type=int, default=None)
    parser.add_argument("--task-text", default=None)
    parser.add_argument("--source-object", default=None)
    parser.add_argument("--destination", default=None)
    parser.add_argument("--gripper-open-fraction", type=float, default=1.0)
    parser.add_argument(
        "--max-joint-step-deg",
        type=float,
        default=None,
        help="Per-step joint clip; default is 50/control_hz degrees",
    )
    parser.add_argument(
        "--image-encoding",
        choices=(JPEG_IMAGE_ENCODING, RAW_IMAGE_ENCODING),
        default=JPEG_IMAGE_ENCODING,
    )
    parser.add_argument("--jpeg-quality", type=int, default=DEFAULT_JPEG_QUALITY)
    parser.add_argument("--timeout-s", type=float, default=30.0)
    parser.add_argument("--camera-ready-timeout-s", type=float, default=10.0)
    parser.add_argument("--max-consecutive-camera-misses", type=int, default=20)
    parser.add_argument("--print-every", type=int, default=1)
    parser.add_argument(
        "--config-enable-motion",
        action="store_true",
        help="Stand-in for local config enable_motion=true",
    )
    parser.add_argument(
        "--allow-motion",
        action="store_true",
        help="CLI motion confirmation; not sufficient by itself",
    )
    parser.add_argument(
        "--canfd-follow",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    parser.add_argument("--canfd-smoothing", type=int, default=50)
    parser.add_argument("--enable-gripper", action="store_true")
    parser.add_argument("--gripper-config", default=None)
    parser.add_argument(
        "--go-to-ready",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument("--ready-config", default=None)
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser.parse_args()


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


class _NoOpGripper:
    def command_open_fraction(self, open_fraction: float) -> bool:
        del open_fraction
        return False


class _ThresholdGripper:
    def __init__(self, teleop: Any, *, close_threshold: float = 0.5) -> None:
        self.teleop = teleop
        self.close_threshold = float(close_threshold)
        self._commanded_open = True

    def command_open_fraction(self, open_fraction: float) -> bool:
        target_open = float(open_fraction) >= self.close_threshold
        if target_open == self._commanded_open:
            return False
        self.teleop.open_to_fraction(1.0 if target_open else 0.0)
        self._commanded_open = target_open
        return True


def _clip_smolvla_action(
    *,
    present_joints_deg: list[float] | np.ndarray,
    action: np.ndarray,
    max_joint_step_deg: float,
) -> tuple[list[float], float]:
    action_vec = np.asarray(action, dtype=np.float32).reshape(7)
    safe_joints = clip_joint_targets(
        present_joints_deg,
        action_vec[:6].tolist(),
        max_joint_step_deg,
        None,
    )
    return safe_joints, float(np.clip(action_vec[6], 0.0, 1.0))


def _build_infer_payload(
    obs: InferObservation,
    *,
    image_encoding: str,
    jpeg_quality: int,
) -> dict[str, Any]:
    return observation_to_payload(
        obs,
        image_encoding=image_encoding,
        jpeg_quality=jpeg_quality,
    )


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if float(args.control_hz) <= 0.0:
        raise ValueError("control-hz must be positive")
    if not 0.0 <= float(args.gripper_open_fraction) <= 1.0:
        raise ValueError("gripper-open-fraction must be in [0, 1]")
    if int(args.max_consecutive_camera_misses) < 1:
        raise ValueError("max-consecutive-camera-misses must be positive")
    if args.task_text is None and (args.source_object is None or args.destination is None):
        raise ValueError(
            "SmolVLA conditioning requires --task-text, or both --source-object and --destination"
        )

    task_text = resolve_episode_task_text(
        task_text=args.task_text,
        source_object=args.source_object,
        destination=args.destination,
    )
    config_enable_motion, config_source = _load_config_enable_motion(
        cli_config_enable_motion=args.config_enable_motion,
    )
    motion_enabled = resolve_motion_enabled(
        config_enable_motion=config_enable_motion,
        cli_allow_motion=args.allow_motion,
    )
    go_to_ready = bool(motion_enabled) if args.go_to_ready is None else bool(args.go_to_ready)
    if go_to_ready and not motion_enabled:
        raise ValueError("--go-to-ready requires motion enabled")
    if args.enable_gripper and not motion_enabled:
        raise ValueError("--enable-gripper requires motion enabled")

    if args.steps is not None:
        total_steps = int(args.steps)
        if total_steps < 1:
            raise ValueError("steps must be positive")
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

    from sharedautonomy.control.observation import (
        CartesianProprioceptiveSource,
        build_camera_session_from_config,
        build_observation_synchronizer,
        load_camera_runtime_config,
    )
    from sharedautonomy.control.realtime import RealtimeCartesianStateSource
    from sharedautonomy.robot.gripper_config import load_serial_soft_gripper_stack
    from sharedautonomy.robot.ready_pose import load_ready_pose_config, move_arm_to_ready_joints
    from sharedautonomy.robot.realtime_state import RealManRealtimeStateSource

    RealManCanfdJointCommander = None
    if motion_enabled:
        from sharedautonomy.robot.canfd_commander import RealManCanfdJointCommander as _Commander

        RealManCanfdJointCommander = _Commander

    base = str(args.infer_url).rstrip("/")
    http_client = JsonHttpClient(base, timeout_s=float(args.timeout_s))
    health = http_client.get("/health", timeout_s=min(10.0, float(args.timeout_s)))
    if not health.get("ok") or health.get("policy") != "smolvla":
        raise RuntimeError(f"SmolVLA server health check failed: {health}")

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

    commander: Any | None = None
    gripper_device = None
    gripper_actuator = None
    gripper_handler: Any = _NoOpGripper()
    ready_pose = load_ready_pose_config(config_path=args.ready_config) if go_to_ready else None

    backend.connect()
    if args.enable_gripper:
        gripper_device, gripper_stack, _gripper_source = load_serial_soft_gripper_stack(
            config_path=args.gripper_config,
        )
        gripper_actuator = gripper_stack
        gripper_handler = _ThresholdGripper(teleop=gripper_actuator)

    if motion_enabled:
        assert RealManCanfdJointCommander is not None
        commander = RealManCanfdJointCommander(
            backend.arm,
            follow=bool(args.canfd_follow),
            smoothing=int(args.canfd_smoothing),
            armed=True,
        )

    camera_session.start()
    period_s = 1.0 / float(args.control_hz)
    commanded_gripper_fraction = float(args.gripper_open_fraction)
    latencies_ms: list[float] = []
    encode_ms_list: list[float] = []
    skipped_camera_misses = 0
    consecutive_misses = 0
    infer_index = 0

    try:
        first = backend.read_snapshot()
        print(
            json.dumps(
                {
                    "mode": "smolvla_rollout",
                    "enable_motion": motion_enabled,
                    "infer_url": base,
                    "task_text": task_text,
                    "control_hz": float(args.control_hz),
                    "planned_steps": total_steps,
                    "max_joint_step_deg": max_joint_step_deg,
                    "motion_config_source": config_source,
                    "start_joint_position_deg": list(first.joint_position_deg),
                    "server_health": health,
                    "operator_hint": (
                        "MOTION ENABLED: joint targets are sent each control step."
                        if motion_enabled
                        else "Dry rollout: actions are printed, not sent."
                    ),
                },
                indent=2,
            ),
            flush=True,
        )

        if go_to_ready and ready_pose is not None and commander is not None:
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
                gripper_actuator.move_to_working_open(ready_physical if ready_physical > 0.0 else 0.0)
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
                    raise RuntimeError(f"Too many consecutive camera misses ({consecutive_misses})")
                _rate_limit_sleep(loop_start, period_s)
                continue

            consecutive_misses = 0
            obs = build_infer_observation(
                synced=synced,
                task=task_text,
                gripper_open_fraction=commanded_gripper_fraction,
                reset=infer_index == 0,
            )
            present_joints = np.asarray(
                synced.observation.joint_position_deg,
                dtype=np.float32,
            ).reshape(6)

            t_enc = time.perf_counter()
            payload = _build_infer_payload(
                obs,
                image_encoding=str(args.image_encoding),
                jpeg_quality=int(args.jpeg_quality),
            )
            encode_ms = (time.perf_counter() - t_enc) * 1000.0
            t0 = time.perf_counter()
            result = http_client.post("/infer", payload, timeout_s=float(args.timeout_s))
            rtt_ms = (time.perf_counter() - t0) * 1000.0
            if not result.get("ok", True) and "action" not in result:
                raise RuntimeError(f"infer failed: {result}")
            response = payload_to_response(result)
            safe_joints, gripper_fraction = _clip_smolvla_action(
                present_joints_deg=present_joints,
                action=response.action,
                max_joint_step_deg=max_joint_step_deg,
            )
            gripper_commanded = gripper_handler.command_open_fraction(gripper_fraction)
            motion_sent = False
            if motion_enabled:
                if commander is None:
                    raise RuntimeError("joint commander is required when motion is enabled")
                commander.send_joint_target(safe_joints)
                motion_sent = True
            if gripper_commanded:
                commanded_gripper_fraction = gripper_fraction

            latencies_ms.append(rtt_ms)
            encode_ms_list.append(encode_ms)
            if infer_index % max(1, int(args.print_every)) == 0:
                print(
                    json.dumps(
                        {
                            "step": step_index,
                            "infer_index": infer_index,
                            "reset": infer_index == 0,
                            "rtt_ms": round(rtt_ms, 1),
                            "encode_ms": round(encode_ms, 1),
                            "server_timings_ms": result.get("timings_ms"),
                            "sync_warnings": list(synced.warnings),
                            "wrist_age_ms": synced.wrist_age_ms,
                            "external_age_ms": synced.external_age_ms,
                            "state": [round(float(value), 4) for value in obs.state.tolist()],
                            "action": [round(float(value), 4) for value in response.action.tolist()],
                            "safe_joints_deg": [round(float(value), 4) for value in safe_joints],
                            "gripper_open_fraction": round(gripper_fraction, 4),
                            "gripper_commanded": gripper_commanded,
                            "motion_sent": motion_sent,
                        }
                    ),
                    flush=True,
                )
            infer_index += 1
            _rate_limit_sleep(loop_start, period_s)

        if infer_index == 0:
            raise RuntimeError("No successful infer steps")
        summary = {
            "steps_planned": total_steps,
            "infer_steps": infer_index,
            "skipped_camera_misses": skipped_camera_misses,
            "image_encoding": str(args.image_encoding),
            "rtt_ms_mean": round(float(np.mean(latencies_ms)), 1),
            "rtt_ms_p95": round(float(np.percentile(latencies_ms, 95)), 1),
            "encode_ms_mean": round(float(np.mean(encode_ms_list)), 1),
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
        logger.exception("rollout_smolvla_policy failed")
        return 1
    finally:
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


def _rate_limit_sleep(loop_start_s: float, period_s: float) -> None:
    sleep_s = float(period_s) - (time.perf_counter() - float(loop_start_s))
    if sleep_s > 0.0:
        time.sleep(sleep_s)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(json.dumps({"abort": "keyboard_interrupt"}), flush=True)
        sys.exit(130)
