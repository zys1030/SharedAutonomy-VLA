"""Paired A/B benchmark: fresh-connection vs keep-alive /infer latency.

Posts the SAME dataset-frame payload to the cloud ACT server, alternating
connection modes step by step, so the two modes share the same time window
and network conditions. Settles whether keep-alive itself adds latency or
the difference comes from run-to-run network variance.

No robot hardware, no motion. Server must be running serve_act_policy.py.
"""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import socket
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import numpy as np
from sharedautonomy.policies.act.protocol import (
    DEFAULT_JPEG_QUALITY,
    JPEG_IMAGE_ENCODING,
    RAW_IMAGE_ENCODING,
    InferObservation,
    chw_float_to_hwc_uint8,
    observation_to_payload,
)

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Paired fresh-vs-keep-alive /infer benchmark on one fixed payload. "
            "No robot motion."
        )
    )
    parser.add_argument("--url", default="http://127.0.0.1:8088", help="Server base URL")
    parser.add_argument("--dataset-repo-id", default="local/shape_pick_place_v1")
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=Path("outputs/datasets/shape_pick_place_v1_v003"),
        help="Local dataset root used to build one fixed payload",
    )
    parser.add_argument("--episode-index", type=int, default=0)
    parser.add_argument("--frame-index", type=int, default=10)
    parser.add_argument(
        "--pairs",
        type=int,
        default=30,
        help="Number of (fresh, keep-alive) pairs; total requests = 2*pairs (default 30)",
    )
    parser.add_argument(
        "--gap-ms",
        type=float,
        default=100.0,
        help="Sleep between requests to mimic the control loop (default 100)",
    )
    parser.add_argument(
        "--image-encoding",
        choices=(JPEG_IMAGE_ENCODING, RAW_IMAGE_ENCODING),
        default=JPEG_IMAGE_ENCODING,
    )
    parser.add_argument("--jpeg-quality", type=int, default=DEFAULT_JPEG_QUALITY)
    parser.add_argument(
        "--sndbuf-kb",
        type=int,
        default=0,
        help=(
            "If > 0, set client SO_SNDBUF to this many KB on both modes. "
            "Test whether sendall() blocking on a small kernel send buffer "
            "(ACK-gated by the peer's delayed-ACK) causes the keep-alive penalty."
        ),
    )
    parser.add_argument("--timeout-s", type=float, default=30.0)
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser.parse_args()


def _fresh_post(
    parsed: Any, body: bytes, *, timeout_s: float, sndbuf_bytes: int
) -> tuple[dict[str, Any], dict[str, float]]:
    """One-shot POST on a brand-new connection, with phase timings."""
    phases: dict[str, float] = {}
    conn = _NoDelayHTTPConnection(
        parsed.hostname, parsed.port or 80, timeout=timeout_s, sndbuf_bytes=sndbuf_bytes
    )
    t0 = time.perf_counter()
    try:
        conn.request("POST", "/infer", body=body, headers={"Content-Type": "application/json"})
        phases["request_ms"] = (time.perf_counter() - t0) * 1000.0
        t1 = time.perf_counter()
        response = conn.getresponse()
        phases["ttfb_ms"] = (time.perf_counter() - t1) * 1000.0
        t2 = time.perf_counter()
        first = response.read(1)
        phases["read_first_byte_ms"] = (time.perf_counter() - t2) * 1000.0
        t3 = time.perf_counter()
        raw = first + response.read()
        phases["read_rest_ms"] = (time.perf_counter() - t3) * 1000.0
        phases["response_bytes"] = float(len(raw))
    except (http.client.HTTPException, OSError) as exc:
        conn.close()
        raise RuntimeError(f"fresh POST failed: {exc}") from exc
    finally:
        conn.close()
    if response.status != 200:
        raise RuntimeError(f"HTTP {response.status}: {raw.decode('utf-8', errors='replace')}")
    return json.loads(raw.decode("utf-8")), phases


class _NoDelayHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args: Any, sndbuf_bytes: int = 0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._sndbuf_bytes = int(sndbuf_bytes)

    def connect(self) -> None:
        super().connect()
        if self.sock is not None:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if self._sndbuf_bytes > 0:
                self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, self._sndbuf_bytes)


class _PersistentClient:
    """Keep-alive client identical in spirit to dry_run's _JsonHttpClient,
    but every retry is counted and surfaced (retries resend the whole body
    and are otherwise silent)."""

    def __init__(self, base_url: str, *, timeout_s: float, sndbuf_bytes: int = 0) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or not parsed.hostname:
            raise ValueError(f"only http:// URLs are supported, got {base_url!r}")
        self._host = parsed.hostname
        self._port = parsed.port or 80
        self._timeout_s = timeout_s
        self._sndbuf_bytes = int(sndbuf_bytes)
        self._conn: http.client.HTTPConnection | None = None
        self.retry_count = 0
        self.retried_flags: list[bool] = []

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def post(self, path: str, body: bytes) -> tuple[dict[str, Any], dict[str, float]]:
        retried = False
        raw = b""
        status = 0
        phases: dict[str, float] = {}
        for attempt in (0, 1):
            if self._conn is None:
                self._conn = _NoDelayHTTPConnection(
                    self._host,
                    self._port,
                    timeout=self._timeout_s,
                    sndbuf_bytes=self._sndbuf_bytes,
                )
            try:
                t0 = time.perf_counter()
                self._conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
                phases["request_ms"] = (time.perf_counter() - t0) * 1000.0
                t1 = time.perf_counter()
                response = self._conn.getresponse()
                phases["ttfb_ms"] = (time.perf_counter() - t1) * 1000.0
                status = response.status
                t2 = time.perf_counter()
                first = response.read(1)
                phases["read_first_byte_ms"] = (time.perf_counter() - t2) * 1000.0
                t3 = time.perf_counter()
                raw = first + response.read()
                phases["read_rest_ms"] = (time.perf_counter() - t3) * 1000.0
                phases["response_bytes"] = float(len(raw))
                break
            except (http.client.HTTPException, OSError):
                self.close()
                retried = True
                self.retry_count += 1
                if attempt == 1:
                    raise
        self.retried_flags.append(retried)
        if status != 200:
            raise RuntimeError(f"HTTP {status}: {raw.decode('utf-8', errors='replace')}")
        return json.loads(raw.decode("utf-8")), phases


def _load_fixed_observation(args: argparse.Namespace) -> InferObservation:
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    from sharedautonomy.policies.act.runtime import resolve_dataset_frame_index

    dataset = LeRobotDataset(args.dataset_repo_id, root=str(args.dataset_root))
    global_index = resolve_dataset_frame_index(
        dataset, episode_index=args.episode_index, frame_index=args.frame_index
    )
    sample = dataset[global_index]
    return InferObservation(
        state=np.asarray(sample["observation.state"], dtype=np.float32),
        wrist_rgb_hwc=chw_float_to_hwc_uint8(np.asarray(sample["observation.images.wrist"])),
        external_rgb_hwc=chw_float_to_hwc_uint8(np.asarray(sample["observation.images.external"])),
        task=str(sample["task"]),
        reset=False,
    )


def _summarize(rows: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    subset = [r for r in rows if r["mode"] == mode]

    def _mean(key: str) -> float | None:
        values = [r[key] for r in subset if r.get(key) is not None]
        return round(float(np.mean(values)), 1) if values else None

    rtts = [r["rtt_ms"] for r in subset]
    return {
        "mode": mode,
        "n": len(rtts),
        "rtt_ms_mean": round(float(np.mean(rtts)), 1) if rtts else None,
        "rtt_ms_median": round(float(np.median(rtts)), 1) if rtts else None,
        "rtt_ms_p95": round(float(np.percentile(rtts, 95)), 1) if rtts else None,
        "client_request_ms_mean": _mean("client_request_ms"),
        "client_ttfb_ms_mean": _mean("client_ttfb_ms"),
        "client_read_first_byte_ms_mean": _mean("client_read_first_byte_ms"),
        "client_read_rest_ms_mean": _mean("client_read_rest_ms"),
        "server_read_json_ms_mean": _mean("read_json_ms"),
        "retries": sum(1 for r in subset if r["retried"]),
    }


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    base = args.url.rstrip("/")
    infer_url = f"{base}/infer"

    print(json.dumps({"stage": "loading_dataset", "dataset_root": str(args.dataset_root)}), flush=True)
    t_load = time.perf_counter()
    obs = _load_fixed_observation(args)
    print(
        json.dumps({"stage": "dataset_loaded", "load_s": round(time.perf_counter() - t_load, 1)}),
        flush=True,
    )
    print(json.dumps({"stage": "encoding_payload", "image_encoding": str(args.image_encoding)}), flush=True)
    t_enc = time.perf_counter()
    payload = observation_to_payload(
        obs, image_encoding=str(args.image_encoding), jpeg_quality=int(args.jpeg_quality)
    )
    body = json.dumps(payload).encode("utf-8")
    print(
        json.dumps({"stage": "payload_encoded", "encode_s": round(time.perf_counter() - t_enc, 1)}),
        flush=True,
    )
    print(
        json.dumps(
            {
                "mode": "bench_infer_connection",
                "url": infer_url,
                "payload_bytes": len(body),
                "pairs": int(args.pairs),
                "gap_ms": float(args.gap_ms),
                "image_encoding": str(args.image_encoding),
                "dataset_root": str(args.dataset_root),
                "episode_index": int(args.episode_index),
                "frame_index": int(args.frame_index),
            }
        ),
        flush=True,
    )

    # Warmup: one fresh + one persistent request, excluded from stats.
    sndbuf_bytes = int(args.sndbuf_kb) * 1024
    parsed = urlparse(infer_url)
    print(json.dumps({"stage": "warmup_fresh", "url": infer_url, "sndbuf_kb": int(args.sndbuf_kb)}), flush=True)
    t_w = time.perf_counter()
    _fresh_post(parsed, body, timeout_s=float(args.timeout_s), sndbuf_bytes=sndbuf_bytes)
    print(
        json.dumps({"stage": "warmup_fresh_done", "rtt_ms": round((time.perf_counter() - t_w) * 1000.0, 1)}),
        flush=True,
    )
    persistent = _PersistentClient(base, timeout_s=float(args.timeout_s), sndbuf_bytes=sndbuf_bytes)
    print(json.dumps({"stage": "warmup_keep_alive"}), flush=True)
    t_w = time.perf_counter()
    persistent.post("/infer", body)
    print(
        json.dumps({"stage": "warmup_keep_alive_done", "rtt_ms": round((time.perf_counter() - t_w) * 1000.0, 1)}),
        flush=True,
    )

    rows: list[dict[str, Any]] = []
    gap_s = float(args.gap_ms) / 1000.0
    for pair in range(int(args.pairs)):
        # Alternate which mode goes first to cancel slow drift within the run.
        modes = ("fresh", "keep_alive") if pair % 2 == 0 else ("keep_alive", "fresh")
        for order, mode in enumerate(modes):
            t0 = time.perf_counter()
            if mode == "fresh":
                result, phases = _fresh_post(
                    parsed, body, timeout_s=float(args.timeout_s), sndbuf_bytes=sndbuf_bytes
                )
                retried = False
            else:
                result, phases = persistent.post("/infer", body)
                retried = persistent.retried_flags[-1]
            rtt_ms = (time.perf_counter() - t0) * 1000.0
            timings = result.get("timings_ms") or {}
            rows.append(
                {
                    "pair": pair,
                    "order": order,
                    "mode": mode,
                    "rtt_ms": round(rtt_ms, 1),
                    "client_request_ms": round(phases.get("request_ms", 0.0), 1),
                    "client_ttfb_ms": round(phases.get("ttfb_ms", 0.0), 1),
                    "client_read_first_byte_ms": round(phases.get("read_first_byte_ms", 0.0), 1),
                    "client_read_rest_ms": round(phases.get("read_rest_ms", 0.0), 1),
                    "response_bytes": int(phases.get("response_bytes", 0)),
                    "read_json_ms": timings.get("read_json_ms"),
                    "decode_images_ms": timings.get("decode_images_ms"),
                    "forward_ms": timings.get("forward_ms"),
                    "serialize_ms": timings.get("serialize_ms"),
                    "retried": retried,
                }
            )
            print(json.dumps(rows[-1]), flush=True)
            time.sleep(gap_s)
    persistent.close()

    fresh_rows = [r for r in rows if r["mode"] == "fresh"]
    keep_rows = [r for r in rows if r["mode"] == "keep_alive"]
    # Pairwise diff on rtt (same pair index = same time window).
    fresh_by_pair = {r["pair"]: r["rtt_ms"] for r in fresh_rows}
    keep_by_pair = {r["pair"]: r["rtt_ms"] for r in keep_rows}
    diffs = [keep_by_pair[p] - fresh_by_pair[p] for p in sorted(fresh_by_pair) if p in keep_by_pair]
    summary = {
        "fresh": _summarize(rows, "fresh"),
        "keep_alive": _summarize(rows, "keep_alive"),
        "paired_diff_keep_minus_fresh_ms": {
            "mean": round(float(np.mean(diffs)), 1) if diffs else None,
            "median": round(float(np.median(diffs)), 1) if diffs else None,
            "p95": round(float(np.percentile(diffs, 95)), 1) if diffs else None,
        },
        "note": (
            "paired_diff ~ 0 => keep-alive is innocent, earlier gap was run-to-run "
            "variance; keep_alive retries > 0 => silent retry path is the suspect "
            "(each retry resends the full payload)."
        ),
    }
    print(json.dumps({"done": summary}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
