"""Measure raw SpaceMouse HID report timing without controlling a robot."""

from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
from collections import Counter
from typing import Any

KNOWN_SPACEMOUSE_IDS = {
    (0x046D, 0xC625),
    (0x046D, 0xC626),
    (0x046D, 0xC627),
    (0x046D, 0xC629),
    (0x046D, 0xC62B),
    (0x256F, 0xC62E),
    (0x256F, 0xC632),
    (0x256F, 0xC633),
    (0x256F, 0xC635),
    (0x256F, 0xC63A),
    (0x256F, 0xC641),
    (0x256F, 0xC652),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "List supported SpaceMouse HID devices or measure raw report timing. "
            "Keep moving or twisting the cap during measurement so the device emits motion reports."
        )
    )
    parser.add_argument("--duration-s", type=float, default=0.0, help="Measurement duration")
    parser.add_argument("--device-index", type=int, default=0, help="Device index from the listing")
    parser.add_argument("--timeout-ms", type=int, default=500, help="HID read timeout")
    parser.add_argument(
        "--control-hz",
        type=float,
        default=50.0,
        help="Also sample the latest complete motion state at this control-loop frequency",
    )
    return parser.parse_args()


def safe_device_record(device: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "index": index,
        "manufacturer": device.get("manufacturer_string"),
        "product": device.get("product_string"),
        "vendor_id": f"0x{int(device['vendor_id']):04x}",
        "product_id": f"0x{int(device['product_id']):04x}",
        "usage_page": device.get("usage_page"),
        "usage": device.get("usage"),
    }


def summarize_timestamps(timestamps_ns: list[int]) -> dict[str, Any]:
    intervals_ms = [
        (current - previous) / 1_000_000
        for previous, current in zip(timestamps_ns, timestamps_ns[1:], strict=False)
    ]
    result: dict[str, Any] = {"reports": len(timestamps_ns)}
    if not intervals_ms:
        return result

    ordered = sorted(intervals_ms)
    p95_index = max(0, int(len(ordered) * 0.95) - 1)
    p99_index = max(0, int(len(ordered) * 0.99) - 1)
    mean_ms = statistics.mean(intervals_ms)
    result.update(
        mean_interval_ms=round(mean_ms, 3),
        median_interval_ms=round(statistics.median(intervals_ms), 3),
        p95_interval_ms=round(ordered[p95_index], 3),
        p99_interval_ms=round(ordered[p99_index], 3),
        max_interval_ms=round(max(intervals_ms), 3),
        effective_report_hz=round(1000 / mean_ms, 2),
    )
    return result


def summarize_ages(ages_ns: list[int]) -> dict[str, Any]:
    """Summarize non-negative sample ages measured on the host monotonic clock."""
    if not ages_ns:
        return {"samples": 0}
    if any(age_ns < 0 for age_ns in ages_ns):
        raise ValueError("ages must not be negative")

    ages_ms = [age_ns / 1_000_000 for age_ns in ages_ns]
    ordered = sorted(ages_ms)

    def percentile(fraction: float) -> float:
        index = max(0, int(len(ordered) * fraction) - 1)
        return ordered[index]

    return {
        "samples": len(ages_ms),
        "mean_ms": round(statistics.mean(ages_ms), 3),
        "median_ms": round(statistics.median(ages_ms), 3),
        "p95_ms": round(percentile(0.95), 3),
        "p99_ms": round(percentile(0.99), 3),
        "min_ms": round(min(ages_ms), 3),
        "max_ms": round(max(ages_ms), 3),
    }


def enumerate_spacemice(hid: Any) -> list[dict[str, Any]]:
    devices = []
    seen_paths: set[bytes | str] = set()
    for device in hid.enumerate():
        identifier = (int(device["vendor_id"]), int(device["product_id"]))
        path = device.get("path")
        if identifier not in KNOWN_SPACEMOUSE_IDS or path in seen_paths:
            continue
        seen_paths.add(path)
        devices.append(device)
    return devices


def measure_device(
    hid: Any,
    device_info: dict[str, Any],
    duration_s: float,
    timeout_ms: int,
    control_hz: float,
) -> dict[str, Any]:
    if duration_s <= 0:
        raise ValueError("duration-s must be positive when measuring")
    if timeout_ms <= 0:
        raise ValueError("timeout-ms must be positive")
    if control_hz <= 0:
        raise ValueError("control-hz must be positive")

    device = hid.device()
    timestamps_ns: list[int] = []
    report_ids: Counter[int] = Counter()
    latest_report_ns: dict[int, int] = {}
    freshest_report_ages_ns: list[int] = []
    complete_motion_ages_ns: list[int] = []
    control_tick_lateness_ns: list[int] = []
    empty_reads = 0
    lock = threading.Lock()
    stop_event = threading.Event()

    device.open_path(device_info["path"])
    device.set_nonblocking(0)

    def read_reports() -> None:
        nonlocal empty_reads
        while not stop_event.is_set():
            report = device.read(64, timeout_ms)
            if not report:
                with lock:
                    empty_reads += 1
                continue
            received_ns = time.perf_counter_ns()
            report_id = int(report[0])
            with lock:
                timestamps_ns.append(received_ns)
                report_ids[report_id] += 1
                latest_report_ns[report_id] = received_ns

    reader = threading.Thread(target=read_reports, name="spacemouse-hid-reader", daemon=True)
    started_ns = time.perf_counter_ns()
    period_ns = round(1_000_000_000 / control_hz)
    next_tick_ns = started_ns + period_ns
    reader.start()
    try:
        while True:
            remaining_ns = next_tick_ns - time.perf_counter_ns()
            if remaining_ns > 0:
                time.sleep(remaining_ns / 1_000_000_000)
            sampled_ns = time.perf_counter_ns()
            if sampled_ns - started_ns > duration_s * 1_000_000_000:
                break

            with lock:
                latest_translation_ns = latest_report_ns.get(1)
                latest_rotation_ns = latest_report_ns.get(2)
            available = [
                timestamp_ns
                for timestamp_ns in (latest_translation_ns, latest_rotation_ns)
                if timestamp_ns is not None
            ]
            if available:
                freshest_report_ages_ns.append(sampled_ns - max(available))
            if latest_translation_ns is not None and latest_rotation_ns is not None:
                complete_motion_ages_ns.append(
                    sampled_ns - min(latest_translation_ns, latest_rotation_ns)
                )
            control_tick_lateness_ns.append(sampled_ns - next_tick_ns)
            next_tick_ns += period_ns
    finally:
        stop_event.set()
        reader.join(timeout=(timeout_ms / 1000) + 1.0)
        device.close()

    elapsed_s = (time.perf_counter_ns() - started_ns) / 1_000_000_000
    with lock:
        captured_timestamps_ns = list(timestamps_ns)
        captured_report_ids = dict(report_ids)
        captured_empty_reads = empty_reads
    return {
        "elapsed_s": round(elapsed_s, 3),
        "empty_reads": captured_empty_reads,
        "all_reports": summarize_timestamps(captured_timestamps_ns),
        "report_id_counts": {
            str(key): value for key, value in sorted(captured_report_ids.items())
        },
        "control_loop": {
            "nominal_hz": control_hz,
            "tick_lateness": summarize_ages(control_tick_lateness_ns),
            "freshest_report_age": summarize_ages(freshest_report_ages_ns),
            "complete_6dof_age": summarize_ages(complete_motion_ages_ns),
            "complete_state_definition": (
                "Age of the older timestamp from the latest translation report (ID 1) "
                "and rotation report (ID 2)"
            ),
        },
    }


def main() -> None:
    args = parse_args()

    try:
        import hid
    except ImportError as exc:
        raise RuntimeError(
            "The optional 'hidapi' package is required only for this hardware check."
        ) from exc

    devices = enumerate_spacemice(hid)
    result: dict[str, Any] = {
        "device_count": len(devices),
        "devices": [safe_device_record(device, index) for index, device in enumerate(devices)],
    }
    if args.duration_s > 0:
        if not devices:
            raise RuntimeError(
                "No supported SpaceMouse HID device was found. Connect or power on the device and retry."
            )
        if not 0 <= args.device_index < len(devices):
            raise IndexError(f"device-index must be between 0 and {len(devices) - 1}")
        result["measurement"] = measure_device(
            hid,
            devices[args.device_index],
            duration_s=args.duration_s,
            timeout_ms=args.timeout_ms,
            control_hz=args.control_hz,
        )

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
