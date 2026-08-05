"""HTTP server for SmolVLA inference (no robot motion).

Runs on the GPU training machine. The server owns the SmolVLA action queue;
clients send one observation and receive one 7-dimensional RM-65 action.
"""

from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sharedautonomy.policies.act.protocol import payload_to_observation, response_to_payload
from sharedautonomy.policies.smolvla.runtime import (
    SmolVLAInferenceRuntime,
    SmolVLARuntimeConfig,
)

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Serve a SmolVLA policy over HTTP. Does not connect to robot hardware or enable motion.")
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        required=True,
        help="Completed SmolVLA full or LoRA checkpoint directory",
    )
    parser.add_argument(
        "--base-model",
        default=None,
        help="Optional base model path or Hub id for an adapter-only checkpoint",
    )
    parser.add_argument(
        "--dataset-repo-id",
        default="local/shape_pick_place_v1",
        help="LeRobot repo id used for dataset stats / infer_dataset",
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=Path("outputs/datasets/shape_pick_place_v1_c1"),
        help="C1 dataset root used for stats and optional dataset smoke",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=8089, help="Bind port")
    parser.add_argument("--device", default="cuda", help="Torch device (cuda|cpu)")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return parser.parse_args()


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.log_request(status)
    handler.send_response_only(status)
    handler.send_header("Server", handler.version_string())
    handler.send_header("Date", handler.date_time_string())
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler._headers_buffer.append(b"\r\n")
    handler.wfile.write(b"".join(handler._headers_buffer) + body)
    handler._headers_buffer = []


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length) if length > 0 else b"{}"
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object")
    return payload


def make_handler(runtime: SmolVLAInferenceRuntime) -> type[BaseHTTPRequestHandler]:
    """Build a request handler with serialized access to the stateful policy queue."""
    infer_lock = threading.Lock()

    class SmolVLAInferHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            super().setup()
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            logger.info("%s - %s", self.address_string(), format % args)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/health":
                _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "policy": "smolvla",
                        **runtime.describe(),
                    },
                )
                return
            _json_response(self, 404, {"ok": False, "error": f"unknown path {path}"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                if path == "/infer":
                    t0 = time.perf_counter()
                    payload = _read_json(self)
                    t1 = time.perf_counter()
                    observation = payload_to_observation(payload)
                    t2 = time.perf_counter()
                    with infer_lock:
                        result = runtime.infer(observation)
                    t3 = time.perf_counter()
                    body = {"ok": True, **response_to_payload(result)}
                    t4 = time.perf_counter()
                    timings = {
                        "read_json_ms": (t1 - t0) * 1000.0,
                        "decode_images_ms": (t2 - t1) * 1000.0,
                        "forward_ms": (t3 - t2) * 1000.0,
                        "serialize_ms": (t4 - t3) * 1000.0,
                    }
                    logger.info(
                        "infer timings: read=%.1fms decode=%.1fms forward=%.1fms serialize=%.1fms",
                        timings["read_json_ms"],
                        timings["decode_images_ms"],
                        timings["forward_ms"],
                        timings["serialize_ms"],
                    )
                    _json_response(
                        self,
                        200,
                        {
                            **body,
                            "timings_ms": {key: round(value, 1) for key, value in timings.items()},
                        },
                    )
                    return

                if path == "/infer_dataset":
                    payload = _read_json(self)
                    episode_index = int(payload["episode_index"])
                    frame_index = int(payload.get("frame_index", 0))
                    reset = bool(payload.get("reset", False))
                    task_override = payload.get("task")
                    with infer_lock:
                        observation, result = runtime.infer_dataset_frame(
                            episode_index=episode_index,
                            frame_index=frame_index,
                            reset=reset,
                            task_override=(str(task_override) if task_override is not None else None),
                        )
                    _json_response(
                        self,
                        200,
                        {
                            "ok": True,
                            **response_to_payload(result),
                            "echo_task": observation.task,
                            "echo_state": [float(value) for value in observation.state.tolist()],
                        },
                    )
                    return

                _json_response(self, 404, {"ok": False, "error": f"unknown path {path}"})
            except Exception as exc:  # noqa: BLE001 — return error to client
                logger.exception("SmolVLA infer request failed")
                _json_response(
                    self,
                    400,
                    {
                        "ok": False,
                        "error": str(exc),
                        "traceback": traceback.format_exc(limit=5),
                    },
                )

    return SmolVLAInferHandler


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    runtime = SmolVLAInferenceRuntime(
        SmolVLARuntimeConfig(
            checkpoint_dir=args.checkpoint.resolve(),
            dataset_repo_id=args.dataset_repo_id,
            dataset_root=args.dataset_root.resolve(),
            device=args.device,
            base_model=args.base_model,
        )
    )
    runtime.load()

    server = ThreadingHTTPServer((args.host, args.port), make_handler(runtime))
    logger.info("SmolVLA infer server listening on http://%s:%s", args.host, args.port)
    logger.info("Endpoints: GET /health  POST /infer  POST /infer_dataset")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
