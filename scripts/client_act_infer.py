"""Dumb ACT inference client: send observation to cloud server, print action.

Does not connect to cameras or enable robot motion. For smoke testing B2.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
from sharedautonomy.policies.act.protocol import (
    DEFAULT_JPEG_QUALITY,
    JPEG_IMAGE_ENCODING,
    RAW_IMAGE_ENCODING,
    InferObservation,
    chw_float_to_hwc_uint8,
    observation_to_payload,
    payload_to_response,
)

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Call cloud ACT infer HTTP endpoints. No robot motion. "
            "Use --mode health|dataset-remote|dataset-local."
        )
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8088",
        help="Server base URL (e.g. http://202.38.78.65:8088)",
    )
    parser.add_argument(
        "--mode",
        choices=("health", "dataset-remote", "dataset-local"),
        default="dataset-remote",
        help=(
            "health: GET /health; "
            "dataset-remote: POST /infer_dataset (server reads its dataset); "
            "dataset-local: load a frame locally and POST /infer"
        ),
    )
    parser.add_argument("--episode-index", type=int, default=0)
    parser.add_argument("--frame-index", type=int, default=0)
    parser.add_argument(
        "--reset",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Ask the server to reset ACT chunk cache before this step (default: true)",
    )
    parser.add_argument(
        "--task",
        default=None,
        help="Optional task string override (default: use dataset sample task)",
    )
    parser.add_argument(
        "--dataset-repo-id",
        default="local/shape_pick_place_v1",
        help="Only for --mode dataset-local",
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=Path("outputs/datasets/shape_pick_place_v1_v002"),
        help="Only for --mode dataset-local",
    )
    parser.add_argument("--timeout-s", type=float, default=120.0)
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Only for --mode health: repeat the request N times and report per-call rtt_ms",
    )
    parser.add_argument(
        "--image-encoding",
        choices=(JPEG_IMAGE_ENCODING, RAW_IMAGE_ENCODING),
        default=JPEG_IMAGE_ENCODING,
        help="Only for --mode dataset-local (default jpeg_b64; use base64 for raw A/B)",
    )
    parser.add_argument(
        "--jpeg-quality",
        type=int,
        default=DEFAULT_JPEG_QUALITY,
        help=f"JPEG quality when --image-encoding={JPEG_IMAGE_ENCODING} (default {DEFAULT_JPEG_QUALITY})",
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


def _load_local_observation(
    *,
    repo_id: str,
    root: Path,
    episode_index: int,
    frame_index: int,
    reset: bool,
    task_override: str | None,
) -> InferObservation:
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    from sharedautonomy.policies.act.runtime import resolve_dataset_frame_index

    dataset = LeRobotDataset(repo_id, root=str(root))
    global_index = resolve_dataset_frame_index(
        dataset,
        episode_index=episode_index,
        frame_index=frame_index,
    )
    sample = dataset[global_index]
    task = task_override if task_override is not None else str(sample["task"])
    return InferObservation(
        state=np.asarray(sample["observation.state"], dtype=np.float32),
        wrist_rgb_hwc=chw_float_to_hwc_uint8(np.asarray(sample["observation.images.wrist"])),
        external_rgb_hwc=chw_float_to_hwc_uint8(np.asarray(sample["observation.images.external"])),
        task=task,
        reset=reset,
    )


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    base = args.url.rstrip("/")

    if args.mode == "health":
        rtts_ms: list[float] = []
        result: dict[str, Any] = {}
        for _ in range(max(1, int(args.repeat))):
            t0 = time.perf_counter()
            result = _http_json("GET", f"{base}/health", timeout_s=args.timeout_s)
            rtts_ms.append((time.perf_counter() - t0) * 1000.0)
        print(json.dumps(result, indent=2))
        if rtts_ms:
            print(
                "health rtt_ms:",
                [round(x, 1) for x in rtts_ms],
                "mean:",
                round(float(np.mean(rtts_ms)), 1),
            )
        return 0 if result.get("ok") else 1

    if args.mode == "dataset-remote":
        payload: dict[str, Any] = {
            "episode_index": args.episode_index,
            "frame_index": args.frame_index,
            "reset": args.reset,
        }
        if args.task is not None:
            payload["task"] = args.task
        result = _http_json(
            "POST",
            f"{base}/infer_dataset",
            payload=payload,
            timeout_s=args.timeout_s,
        )
        print(json.dumps(result, indent=2))
        if not result.get("ok"):
            return 1
        response = payload_to_response(result)
        print("action:", np.round(response.action, 4).tolist())
        return 0

    # dataset-local -> POST /infer
    obs = _load_local_observation(
        repo_id=args.dataset_repo_id,
        root=args.dataset_root,
        episode_index=args.episode_index,
        frame_index=args.frame_index,
        reset=args.reset,
        task_override=args.task,
    )
    result = _http_json(
        "POST",
        f"{base}/infer",
        payload=observation_to_payload(
            obs,
            image_encoding=str(args.image_encoding),
            jpeg_quality=int(args.jpeg_quality),
        ),
        timeout_s=args.timeout_s,
    )
    print(json.dumps({k: v for k, v in result.items() if k != "traceback"}, indent=2))
    if not result.get("ok"):
        return 1
    response = payload_to_response(result)
    print("action:", np.round(response.action, 4).tolist())
    return 0


if __name__ == "__main__":
    sys.exit(main())
