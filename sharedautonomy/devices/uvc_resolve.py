"""Resolve external UVC cameras to stable OpenCV DirectShow indices."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_MAX_PROBE_INDEX = 15


@dataclass(frozen=True, slots=True)
class UvcCaptureDevice:
    opencv_index: int
    friendly_name: str
    device_path: str = ""


def normalize_hex_id(value: str | int | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower().removeprefix("0x")
    if not text:
        return None
    return text.zfill(4) if len(text) <= 4 else text


def _text_contains(haystack: str, needle: str) -> bool:
    return needle.lower() in haystack.lower()


def _hardware_id_matches(device_path: str, *, vendor_id: str | None, product_id: str | None) -> bool:
    path = device_path.lower()
    normalized_vendor = normalize_hex_id(vendor_id)
    normalized_product = normalize_hex_id(product_id)
    if normalized_vendor is not None and f"vid_{normalized_vendor}" not in path:
        return False
    if normalized_product is not None and f"pid_{normalized_product}" not in path:
        return False
    return True


def device_matches_selector(
    device: UvcCaptureDevice,
    *,
    friendly_name: str | None,
    device_name_contains: str | None,
    vendor_id: str | None,
    product_id: str | None,
) -> bool:
    name_needle = device_name_contains or friendly_name
    if name_needle is not None and not _text_contains(device.friendly_name, name_needle):
        return False
    if vendor_id is not None or product_id is not None:
        if not device.device_path:
            return False
        if not _hardware_id_matches(device.device_path, vendor_id=vendor_id, product_id=product_id):
            return False
    return name_needle is not None or vendor_id is not None or product_id is not None


def list_dshow_capture_devices() -> list[UvcCaptureDevice]:
    if sys.platform == "win32":
        return _list_dshow_capture_devices_win32()
    return []


def _list_dshow_capture_devices_win32() -> list[UvcCaptureDevice]:
    if _running_as_dshow_worker():
        return _list_dshow_capture_devices_inprocess()
    return _list_dshow_capture_devices_subprocess()


def _running_as_dshow_worker() -> bool:
    return Path(sys.argv[0]).name == "_dshow_enum_worker.py" or (
        len(sys.argv) >= 2 and sys.argv[-2:] == ["-m", "sharedautonomy.devices._dshow_enum_worker"]
    )


def _list_dshow_capture_devices_subprocess() -> list[UvcCaptureDevice]:
    completed = subprocess.run(
        [sys.executable, "-m", "sharedautonomy.devices._dshow_enum_worker"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30.0,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise RuntimeError(
            "DirectShow camera enumeration subprocess failed"
            + (f": {stderr}" if stderr else "")
        )
    payload = completed.stdout.strip()
    if not payload:
        return []
    decoded = json.loads(payload)
    if not isinstance(decoded, list):
        raise RuntimeError("DirectShow camera enumeration subprocess returned invalid JSON")
    devices: list[UvcCaptureDevice] = []
    for item in decoded:
        if not isinstance(item, dict):
            continue
        devices.append(
            UvcCaptureDevice(
                opencv_index=int(item["opencv_index"]),
                friendly_name=str(item.get("friendly_name") or ""),
                device_path=str(item.get("device_path") or ""),
            )
        )
    return devices


def _list_dshow_capture_devices_inprocess() -> list[UvcCaptureDevice]:
    try:
        from sharedautonomy.devices._dshow_enum_win32 import enumerate_dshow_video_input_devices
    except ImportError as exc:
        raise RuntimeError(
            "comtypes is required to resolve external UVC cameras by name on Windows. "
            "Install project hardware extras or `pip install comtypes`."
        ) from exc
    return [
        UvcCaptureDevice(
            opencv_index=index,
            friendly_name=friendly_name,
            device_path=device_path,
        )
        for index, friendly_name, device_path in enumerate_dshow_video_input_devices()
    ]


def list_pnp_capture_devices() -> list[UvcCaptureDevice]:
    if sys.platform != "win32":
        return []
    command = [
        "powershell",
        "-NoProfile",
        "-Command",
        (
            "Get-PnpDevice -Class Camera -Status OK | "
            "ForEach-Object { "
            "[PSCustomObject]@{ friendly_name = $_.FriendlyName; device_path = $_.InstanceId } "
            "} | ConvertTo-Json -Compress"
        ),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15.0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("PnP camera enumeration failed: %s", exc)
        return []
    if completed.returncode != 0:
        logger.warning("PnP camera enumeration failed: %s", completed.stderr.strip())
        return []

    payload = completed.stdout.strip()
    if not payload:
        return []
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError:
        logger.warning("PnP camera enumeration returned invalid JSON")
        return []
    if isinstance(decoded, dict):
        decoded = [decoded]

    devices: list[UvcCaptureDevice] = []
    for index, item in enumerate(decoded):
        if not isinstance(item, dict):
            continue
        friendly_name = str(item.get("friendly_name") or "").strip()
        device_path = str(item.get("device_path") or "").strip()
        if not friendly_name:
            continue
        devices.append(
            UvcCaptureDevice(
                opencv_index=index,
                friendly_name=friendly_name,
                device_path=device_path,
            )
        )
    return devices


def _attach_pnp_paths(devices: list[UvcCaptureDevice]) -> list[UvcCaptureDevice]:
    pnp_devices = list_pnp_capture_devices()
    if not pnp_devices:
        return devices

    by_name: dict[str, list[str]] = {}
    for pnp_device in pnp_devices:
        by_name.setdefault(pnp_device.friendly_name.lower(), []).append(pnp_device.device_path)

    enriched: list[UvcCaptureDevice] = []
    for device in devices:
        paths = by_name.get(device.friendly_name.lower(), [])
        device_path = paths.pop(0) if paths else device.device_path
        enriched.append(
            UvcCaptureDevice(
                opencv_index=device.opencv_index,
                friendly_name=device.friendly_name,
                device_path=device_path,
            )
        )
    return enriched


def resolve_uvc_opencv_index(
    *,
    friendly_name: str | None = None,
    device_name_contains: str | None = None,
    vendor_id: str | None = None,
    product_id: str | None = None,
    opencv_index_hint: int | None = None,
    max_probe_index: int = _DEFAULT_MAX_PROBE_INDEX,
) -> int:
    devices = _attach_pnp_paths(list_dshow_capture_devices())
    matches = [
        device
        for device in devices
        if device_matches_selector(
            device,
            friendly_name=friendly_name,
            device_name_contains=device_name_contains,
            vendor_id=vendor_id,
            product_id=product_id,
        )
    ]
    if len(matches) == 1:
        resolved = matches[0]
        logger.info(
            "Resolved external UVC camera '%s' to OpenCV index %d",
            resolved.friendly_name,
            resolved.opencv_index,
        )
        return resolved.opencv_index
    if len(matches) > 1:
        names = ", ".join(f"{device.opencv_index}:{device.friendly_name}" for device in matches)
        raise RuntimeError(
            "Ambiguous UVC camera selector; multiple DirectShow devices matched: "
            f"{names}. Refine friendly_name or VID/PID in configs/local/external_rgb.local.yaml."
        )

    if opencv_index_hint is not None:
        logger.warning(
            "UVC selector did not match any DirectShow device; falling back to opencv_index_hint=%s",
            opencv_index_hint,
        )
        return int(opencv_index_hint)

    available = ", ".join(f"{device.opencv_index}:{device.friendly_name}" for device in devices)
    if not available:
        available = f"no DirectShow devices enumerated; probed up to index {max_probe_index}"
    raise RuntimeError(
        "Failed to resolve external UVC camera. Configure friendly_name and/or vendor_id/product_id "
        f"in configs/local/external_rgb.local.yaml. Available devices: {available}"
    )


def resolve_opencv_backend(backend_name: str | None) -> int | None:
    if backend_name is None:
        return None
    try:
        import cv2
    except ImportError:
        return None
    normalized = backend_name.strip().lower()
    if normalized in {"dshow", "directshow"}:
        return cv2.CAP_DSHOW if hasattr(cv2, "CAP_DSHOW") else cv2.CAP_ANY
    if normalized in {"msmf", "mediafoundation"}:
        return cv2.CAP_MSMF if hasattr(cv2, "CAP_MSMF") else cv2.CAP_ANY
    if normalized in {"any", "auto"}:
        return cv2.CAP_ANY
    raise ValueError(f"Unsupported OpenCV backend '{backend_name}'")


def build_resolved_uvc_opencv_index(
    *,
    friendly_name: str | None = None,
    device_name_contains: str | None = None,
    vendor_id: str | None = None,
    product_id: str | None = None,
    opencv_index_hint: int | None = None,
    opencv_index: int | None = None,
) -> int | None:
    if opencv_index is not None:
        return int(opencv_index)
    if (
        friendly_name is None
        and device_name_contains is None
        and vendor_id is None
        and product_id is None
        and opencv_index_hint is None
    ):
        return None
    return resolve_uvc_opencv_index(
        friendly_name=friendly_name,
        device_name_contains=device_name_contains,
        vendor_id=vendor_id,
        product_id=product_id,
        opencv_index_hint=opencv_index_hint,
    )
