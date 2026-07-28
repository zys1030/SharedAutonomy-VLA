"""Read real RM-65 + dual-camera observations and POST to cloud ACT /infer.

Motion is never enabled: this script only prints predicted joint+gripper targets.
Requires the cloud ``serve_act_policy.py`` process to be running.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from typing import Any

import numpy as np
from sharedautonomy.control.observation import (
    CartesianProprioceptiveSource,
    build_camera_session_from_config,
    build_observation_synchronizer,
    load_camera_runtime_config,
)
from sharedautonomy.control.realtime import RealtimeCartesianStateSource
from sharedautonomy.policies.act.protocol import (
    DEFAULT_IMAGE_HWC,
    DEFAULT_JPEG_QUALITY,
    JPEG_IMAGE_ENCODING,
    RAW_IMAGE_ENCODING,
    InferObservation,
    observation_to_payload,
    payload_to_response,
)
from sharedautonomy.robot.realtime_state import RealManRealtimeStateSource
from sharedautonomy.tasks.shape_pick_place_v1 import resolve_episode_task_text

logger = logging.getLogger(__name__)

_IMAGE_H, _IMAGE_W, _IMAGE_C = DEFAULT_IMAGE_HWC


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Capture live robot+camera observations and call cloud ACT /infer. "
            "Does not enable motion or send joint commands."
        )
    )
    parser.add_argument("--ip", required=True, help="RM-65B controller IP address")
    parser.add_argument("--port", type=int, default=8080, help="RM-65B SDK port (default 8080)")
    parser.add_argument(
        "--infer-url",
        default="http://127.0.0.1:8088",
        help="ACT server base URL (default http://127.0.0.1:8088)",
    )
    parser.add_argument(
        "--control-hz",
        type=float,
        default=10.0,
        help="Observation / infer loop rate (default 10, matches collection)",
    )
    parser.add_argument(
        "--duration-s",
        type=float,
        default=20.0,
        help="How long to run the loop (default 20)",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=None,
        help="Optional hard step cap (overrides duration if set)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Capture and infer a single frame then exit",
    )
    parser.add_argument(
        "--task-text",
        default=None,
        help="Explicit task string for ACT conditioning",
    )
    parser.add_argument(
        "--source-object",
        default=None,
        help="Task card object_id (e.g. blue); used with --destination to fill task_text",
    )
    parser.add_argument(
        "--destination",
        default=None,
        help="Task card destination_id (e.g. down); used with --source-object",
    )
    parser.add_argument(
        "--gripper-open-fraction",
        type=float,
        default=1.0,
        help=(
            "Value written into observation.state[6] (commanded open fraction). "
            "No gripper hardware is driven; default 1.0 (open)."
        ),
    )
    parser.add_argument(
        "--reset-every",
        type=int,
        default=0,
        help=(
            "Reset ACT chunk cache every N steps (0 = only on the first step). "
            "Useful if the server cache drifts during long dry-runs."
        ),
    )
    parser.add_argument(
        "--camera-ready-timeout-s",
        type=float,
        default=10.0,
        help="After start(), wait up to this many seconds for wrist+external frames (default 10)",
    )
    parser.add_argument(
        "--max-consecutive-camera-misses",
        type=int,
        default=20,
        help="Abort if this many loop steps in a row lack wrist or external RGB (default 20)",
    )
    parser.add_argument(
        "--image-encoding",
        choices=(JPEG_IMAGE_ENCODING, RAW_IMAGE_ENCODING),
        default=JPEG_IMAGE_ENCODING,
        help=(
            "Wire encoding for camera images (default jpeg_b64). "
            "jpeg_b64 cuts payload ~10-20x vs raw base64 for RTT reduction."
        ),
    )
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        default=DEFAULT_JPEG_QUALITY,
        help=f"JPEG quality when --image-encoding={JPEG_IMAGE_ENCODING} (default {DEFAULT_JPEG_QUALITY})",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=30.0,
        help="HTTP timeout per infer request (default 30)",
    )
    parser.add_argument(
        "--print-every",
        type=int,
        default=1,
        help="Print a JSON line every N successful steps (default 1)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return parser.parse_args()


def _http_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout_s: float,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = response.read().decode("utf-8")
            result = json.loads(body)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to reach {url}: {exc}") from exc
    if not isinstance(result, dict):
        raise RuntimeError(f"Expected JSON object from {url}")
    return result


def _ensure_hwc_rgb_uint8(image: np.ndarray, *, height: int, width: int) -> np.ndarray:
    array = np.asarray(image)
    if array.dtype != np.uint8 or array.ndim != 3 or array.shape[2] != 3:
        raise ValueError(f"expected HWC uint8 RGB, got dtype={array.dtype} shape={array.shape}")
    if array.shape[0] == height and array.shape[1] == width:
        return np.ascontiguousarray(array)
    import cv2

    resized = cv2.resize(array, (width, height), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(resized)


def _both_rgb_present(synced: Any) -> bool:
    observation = synced.observation
    return observation.wrist_camera is not None and observation.external_camera is not None


def _wait_for_both_cameras(
    *,
    proprio_source: CartesianProprioceptiveSource,
    synchronizer: Any,
    gripper_open_fraction: float,
    timeout_s: float,
    poll_s: float = 0.05,
) -> Any:
    """Block until wrist+external frames are buffered (RealSense thread publish race)."""
    if timeout_s < 0.0:
        raise ValueError("camera-ready-timeout-s must be >= 0")
    deadline = time.perf_counter() + float(timeout_s)
    last_warnings: list[str] = []
    attempts = 0
    while True:
        now_ns = time.perf_counter_ns()
        proprio_source.prime(now_monotonic_ns=now_ns)
        synced = synchronizer.capture(
            now_monotonic_ns=now_ns,
            gripper_commanded_open_fraction=float(gripper_open_fraction),
        )
        attempts += 1
        last_warnings = list(synced.warnings)
        if _both_rgb_present(synced):
            print(
                json.dumps(
                    {
                        "cameras_ready": True,
                        "attempts": attempts,
                        "wrist_age_ms": synced.wrist_age_ms,
                        "external_age_ms": synced.external_age_ms,
                    }
                ),
                flush=True,
            )
            return synced
        if time.perf_counter() >= deadline:
            wrist = synced.observation.wrist_camera is not None
            external = synced.observation.external_camera is not None
            raise RuntimeError(
                "Timed out waiting for wrist+external RGB after camera start. "
                f"wrist={'ok' if wrist else 'missing'} external={'ok' if external else 'missing'} "
                f"attempts={attempts} warnings={last_warnings}"
            )
        time.sleep(poll_s)


def _build_infer_observation(
    *,
    synced: Any,
    task: str,
    gripper_open_fraction: float,
    reset: bool,
) -> InferObservation:
    observation = synced.observation
    if observation.wrist_camera is None or observation.external_camera is None:
        raise RuntimeError(
            "Both wrist and external RGB frames are required for ACT infer; "
            f"wrist={'ok' if observation.wrist_camera else 'missing'} "
            f"external={'ok' if observation.external_camera else 'missing'} "
            f"warnings={list(synced.warnings)}"
        )
    joints = np.asarray(observation.joint_position_deg, dtype=np.float32).reshape(6)
    state = np.concatenate([joints, np.asarray([float(gripper_open_fraction)], dtype=np.float32)])
    return InferObservation(
        state=state,
        wrist_rgb_hwc=_ensure_hwc_rgb_uint8(
            observation.wrist_camera.color_rgb,
            height=_IMAGE_H,
            width=_IMAGE_W,
        ),
        external_rgb_hwc=_ensure_hwc_rgb_uint8(
            observation.external_camera.color_rgb,
            height=_IMAGE_H,
            width=_IMAGE_W,
        ),
        task=task,
        reset=reset,
    )


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if float(args.control_hz) <= 0.0:
        raise ValueError("control-hz must be positive")
    if float(args.gripper_open_fraction) < 0.0 or float(args.gripper_open_fraction) > 1.0:
        raise ValueError("gripper-open-fraction must be in [0, 1]")

    if args.task_text is None and (args.source_object is None or args.destination is None):
        raise ValueError(
            "ACT conditioning requires --task-text, or both --source-object and --destination "
            "(do not use the teleop default task string)."
        )
    task_text = resolve_episode_task_text(
        task_text=args.task_text,
        source_object=args.source_object,
        destination=args.destination,
    )

    period_s = 1.0 / float(args.control_hz)
    if args.once:
        total_steps = 1
    elif args.steps is not None:
        total_steps = int(args.steps)
        if total_steps <= 0:
            raise ValueError("steps must be positive")
    else:
        total_steps = max(1, int(round(float(args.duration_s) * float(args.control_hz))))

    base = str(args.infer_url).rstrip("/")
    health = _http_json("GET", f"{base}/health", timeout_s=min(10.0, float(args.timeout_s)))
    if not health.get("ok"):
        raise RuntimeError(f"ACT server health check failed: {health}")

    backend = RealManRealtimeStateSource(ip=args.ip, port=args.port)
    camera_runtime = load_camera_runtime_config()
    camera_session, sync_config = build_camera_session_from_config(camera_runtime)
    if camera_session.wrist_camera is None or camera_session.external_camera is None:
        raise RuntimeError(
            "Wrist RealSense and external UVC configs are required "
            "(configs/local/wrist_realsense.local.yaml and external_rgb.local.yaml)"
        )

    proprio_source = CartesianProprioceptiveSource(RealtimeCartesianStateSource(backend))
    synchronizer = build_observation_synchronizer(
        proprioception=proprio_source,
        camera_session=camera_session,
        sync_config=sync_config,
    )

    backend.connect()
    camera_session.start()
    try:
        first = backend.read_snapshot()
        print(
            json.dumps(
                {
                    "mode": "act_observe_infer_dry_run",
                    "enable_motion": False,
                    "operator_hint": (
                        "Cameras + UDP state only. Predicted actions are printed, never sent. "
                        "Keep teach-pendant estop ready anyway."
                    ),
                    "infer_url": base,
                    "task_text": task_text,
                    "control_hz": float(args.control_hz),
                    "planned_steps": total_steps,
                    "gripper_open_fraction": float(args.gripper_open_fraction),
                    "camera_ready_timeout_s": float(args.camera_ready_timeout_s),
                    "start_joint_position_deg": list(first.joint_position_deg),
                    "start_ee_position_m": list(first.ee_position_m),
                    "server_health": health,
                },
                indent=2,
            ),
            flush=True,
        )

        _wait_for_both_cameras(
            proprio_source=proprio_source,
            synchronizer=synchronizer,
            gripper_open_fraction=float(args.gripper_open_fraction),
            timeout_s=float(args.camera_ready_timeout_s),
        )

        latencies_ms: list[float] = []
        encode_ms_list: list[float] = []
        skipped_camera_misses = 0
        consecutive_misses = 0
        infer_index = 0
        for index in range(total_steps):
            loop_start = time.perf_counter()
            now_ns = time.perf_counter_ns()
            proprio_source.prime(now_monotonic_ns=now_ns)
            synced = synchronizer.capture(
                now_monotonic_ns=now_ns,
                gripper_commanded_open_fraction=float(args.gripper_open_fraction),
            )
            if not _both_rgb_present(synced):
                consecutive_misses += 1
                skipped_camera_misses += 1
                print(
                    json.dumps(
                        {
                            "step": index,
                            "skip": "camera_missing",
                            "consecutive_misses": consecutive_misses,
                            "sync_warnings": list(synced.warnings),
                            "wrist_ok": synced.observation.wrist_camera is not None,
                            "external_ok": synced.observation.external_camera is not None,
                        }
                    ),
                    flush=True,
                )
                if consecutive_misses >= int(args.max_consecutive_camera_misses):
                    raise RuntimeError(
                        "Too many consecutive steps without wrist+external RGB "
                        f"({consecutive_misses}). Last warnings={list(synced.warnings)}"
                    )
                elapsed = time.perf_counter() - loop_start
                sleep_s = period_s - elapsed
                if sleep_s > 0.0 and index + 1 < total_steps:
                    time.sleep(sleep_s)
                continue

            consecutive_misses = 0
            reset = infer_index == 0 or (args.reset_every > 0 and infer_index % int(args.reset_every) == 0)
            obs = _build_infer_observation(
                synced=synced,
                task=task_text,
                gripper_open_fraction=float(args.gripper_open_fraction),
                reset=reset,
            )

            t_enc = time.perf_counter()
            payload = observation_to_payload(
                obs,
                image_encoding=str(args.image_encoding),
                jpeg_quality=int(args.jpeg_quality),
            )
            encode_ms = (time.perf_counter() - t_enc) * 1000.0
            encode_ms_list.append(encode_ms)

            t0 = time.perf_counter()
            result = _http_json(
                "POST",
                f"{base}/infer",
                payload=payload,
                timeout_s=float(args.timeout_s),
            )
            rtt_ms = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(rtt_ms)
            if not result.get("ok", True) and "action" not in result:
                raise RuntimeError(f"infer failed: {result}")

            response = payload_to_response(result)
            if infer_index % max(1, int(args.print_every)) == 0:
                print(
                    json.dumps(
                        {
                            "step": index,
                            "infer_index": infer_index,
                            "rtt_ms": round(rtt_ms, 1),
                            "encode_ms": round(encode_ms, 1),
                            "server_timings_ms": result.get("timings_ms"),
                            "reset": reset,
                            "sync_warnings": list(synced.warnings),
                            "wrist_age_ms": synced.wrist_age_ms,
                            "external_age_ms": synced.external_age_ms,
                            "state": [round(float(x), 4) for x in obs.state.tolist()],
                            "action": [round(float(x), 4) for x in response.action.tolist()],
                        }
                    ),
                    flush=True,
                )
            infer_index += 1

            elapsed = time.perf_counter() - loop_start
            sleep_s = period_s - elapsed
            if sleep_s > 0.0 and index + 1 < total_steps:
                time.sleep(sleep_s)

        if infer_index == 0:
            raise RuntimeError(
                "No successful infer steps; cameras never produced a complete wrist+external pair "
                f"(skipped_camera_misses={skipped_camera_misses})"
            )

        summary = {
            "steps_planned": total_steps,
            "infer_steps": infer_index,
            "skipped_camera_misses": skipped_camera_misses,
            "image_encoding": str(args.image_encoding),
            "rtt_ms_mean": round(float(np.mean(latencies_ms)), 1) if latencies_ms else None,
            "rtt_ms_p95": (round(float(np.percentile(latencies_ms, 95)), 1) if latencies_ms else None),
            "encode_ms_mean": (round(float(np.mean(encode_ms_list)), 1) if encode_ms_list else None),
            "enable_motion": False,
        }
        print(json.dumps({"done": summary}, indent=2), flush=True)
    finally:
        camera_session.stop()
        backend.disconnect()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(json.dumps({"abort": "keyboard_interrupt"}), flush=True)
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001 — CLI boundary
        print(json.dumps({"error": str(exc)}), flush=True)
        logger.exception("dry_run_act_observe_infer failed")
        sys.exit(1)
