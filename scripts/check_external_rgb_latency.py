"""Measure external UVC RGB frame delivery and consumer freshness without a robot."""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import threading
import time
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Enumerate an external UVC RGB camera, stream for a fixed duration, and "
            "report host-side arrival timing plus simulated 10/50 Hz consumer freshness."
        )
    )
    parser.add_argument("--duration-s", type=float, default=30.0)
    parser.add_argument("--warmup-s", type=float, default=2.0)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--consumer-hz", type=str, default="10,50")
    parser.add_argument("--friendly-name", type=str, default="HD Pro Webcam C920")
    parser.add_argument("--vendor-id", type=str, default="046D")
    parser.add_argument("--product-id", type=str, default="08E5")
    parser.add_argument("--opencv-index", type=int, default=None)
    return parser.parse_args()


def summarize_values_ms(values_ms: list[float]) -> dict[str, float | int]:
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


def enumerate_windows_video_devices() -> list[dict[str, Any]]:
    ps = (
        "$index = 0; "
        "Get-PnpDevice -PresentOnly | Where-Object { $_.Class -eq 'Camera' } | ForEach-Object { "
        "[PSCustomObject]@{ index = $index; name = $_.FriendlyName; id = $_.InstanceId; "
        "enabled = ($_.Status -eq 'OK') } | ConvertTo-Json -Compress; $index++ }"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    devices: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if line:
            devices.append(json.loads(line))
    return devices


def device_matches(device: dict[str, Any], *, friendly_name: str, vendor_id: str, product_id: str) -> bool:
    device_id = str(device.get("id", "")).upper()
    vid = vendor_id.upper().removeprefix("0X")
    pid = product_id.upper().removeprefix("0X")
    if f"VID_{vid}&PID_{pid}" in device_id:
        return True
    return friendly_name.lower() in str(device.get("name", "")).lower()


def resolve_opencv_backend():
    import cv2

    return cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else cv2.CAP_ANY


def try_open_capture(index: int, backend: int, width: int, height: int, fps: int):
    import cv2

    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        cap.release()
        return None
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    ok, frame = cap.read()
    if not ok or frame is None:
        cap.release()
        return None
    return cap


def select_capture(*, friendly_name: str, vendor_id: str, product_id: str, width: int, height: int, fps: int, opencv_index: int | None):
    import cv2

    devices = enumerate_windows_video_devices()
    matches = [d for d in devices if device_matches(d, friendly_name=friendly_name, vendor_id=vendor_id, product_id=product_id)]
    if not matches:
        raise RuntimeError("No matching external camera found: " + json.dumps(devices, ensure_ascii=False))
    selected_meta = next((d for d in matches if d.get("enabled", True)), matches[0])
    backend = resolve_opencv_backend()
    candidate_indices: list[int] = []
    if opencv_index is not None:
        candidate_indices.append(opencv_index)
    else:
        winrt_index = int(selected_meta["index"])
        candidate_indices.extend([winrt_index, winrt_index - 1, winrt_index + 1, winrt_index + 2])
    seen: set[int] = set()
    for index in candidate_indices:
        if index < 0 or index in seen:
            continue
        seen.add(index)
        cap = try_open_capture(index, backend, width, height, fps)
        if cap is not None:
            return cap, index, devices, selected_meta
    raise RuntimeError(f"Matched camera but failed to open OpenCV indices: {candidate_indices}")


def simulate_consumer_ages_ms(arrivals_ns: list[int], *, started_ns: int, duration_ns: int, consumer_hz: float) -> list[float]:
    if not arrivals_ns:
        return []
    period_ns = round(1_000_000_000 / consumer_hz)
    end_ns = started_ns + duration_ns
    next_tick_ns = started_ns + period_ns
    ages_ms: list[float] = []
    while next_tick_ns <= end_ns:
        latest_arrival = None
        for arrival_ns in arrivals_ns:
            if arrival_ns <= next_tick_ns:
                latest_arrival = arrival_ns
            else:
                break
        if latest_arrival is not None:
            ages_ms.append((next_tick_ns - latest_arrival) / 1_000_000)
        next_tick_ns += period_ns
    return ages_ms


def main() -> None:
    args = parse_args()
    consumer_rates = [float(part.strip()) for part in args.consumer_hz.split(",") if part.strip()]
    import cv2

    cap, opened_index, enumerated, selected_meta = select_capture(
        friendly_name=args.friendly_name,
        vendor_id=args.vendor_id,
        product_id=args.product_id,
        width=args.width,
        height=args.height,
        fps=args.fps,
        opencv_index=args.opencv_index,
    )
    startup_ms = 0.0
    frame_arrival_ns: list[int] = []
    opencv_pos_msec_delivery_ages_ms: list[float] = []
    read_failures = 0
    lock = threading.Lock()
    stop_event = threading.Event()
    try:
        warmup_started = time.perf_counter()
        while time.perf_counter() - warmup_started < args.warmup_s:
            ok, _ = cap.read()
            if not ok:
                read_failures += 1

        def capture_frames() -> None:
            nonlocal read_failures
            while not stop_event.is_set():
                ok, _ = cap.read()
                if not ok:
                    with lock:
                        read_failures += 1
                    continue
                arrival_ns = time.perf_counter_ns()
                arrival_wall_ms = time.time_ns() / 1_000_000
                pos_msec = float(cap.get(cv2.CAP_PROP_POS_MSEC))
                with lock:
                    frame_arrival_ns.append(arrival_ns)
                    if pos_msec > 0:
                        opencv_pos_msec_delivery_ages_ms.append(arrival_wall_ms - pos_msec)

        started_ns = time.perf_counter_ns()
        capture_thread = threading.Thread(target=capture_frames, name="external-rgb-capture", daemon=True)
        capture_thread.start()
        time.sleep(args.duration_s)
    finally:
        stop_event.set()
        capture_thread.join(timeout=3.0)
        cap.release()

    with lock:
        arrivals = list(frame_arrival_ns)
        pos_delivery_ages = list(opencv_pos_msec_delivery_ages_ms)
        failures = read_failures

    arrival_intervals_ms = [(c - p) / 1_000_000 for p, c in zip(arrivals, arrivals[1:], strict=False)]
    consumer_reports: dict[str, Any] = {}
    for consumer_hz in consumer_rates:
        label = f"{consumer_hz:g}hz".replace(".", "p")
        consumer_reports[f"control_loop_latest_frame_age_{label}"] = summarize_values_ms(
            simulate_consumer_ages_ms(arrivals, started_ns=started_ns, duration_ns=int(args.duration_s * 1_000_000_000), consumer_hz=consumer_hz)
        )

    result = {
        "requested": {
            "width": args.width,
            "height": args.height,
            "fps": args.fps,
            "consumer_hz": consumer_rates,
            "friendly_name": args.friendly_name,
            "vendor_id": args.vendor_id,
            "product_id": args.product_id,
        },
        "enumeration": enumerated,
        "selected_device": selected_meta,
        "opencv": {"index": opened_index, "backend": "DSHOW", "cv2_version": cv2.__version__},
        "startup_ms": round(startup_ms, 3),
        "warmup_s": args.warmup_s,
        "frames": len(arrivals),
        "read_failures": failures,
        "estimated_receive_hz": round(len(arrivals) / args.duration_s, 3),
        "host_arrival_interval": summarize_values_ms(arrival_intervals_ms),
        "opencv_pos_msec_to_host_arrival": summarize_values_ms(pos_delivery_ages),
        **consumer_reports,
        "notes": {
            "latest_frame_age_scope": "Simulated consumer freshness from host monotonic arrival timestamps.",
            "physical_latency_scope": "True photon-to-application latency requires an external optical timing reference.",
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

