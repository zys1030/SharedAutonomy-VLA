"""Offline coverage for UVC device resolution helpers."""

from __future__ import annotations

import pytest

from sharedautonomy.devices.uvc_resolve import (
    UvcCaptureDevice,
    device_matches_selector,
    normalize_hex_id,
    resolve_uvc_opencv_index,
)

pytestmark = pytest.mark.core


def test_normalize_hex_id_zero_pads_short_values() -> None:
    assert normalize_hex_id("46D") == "046d"
    assert normalize_hex_id("0x08E5") == "08e5"


def test_device_matches_selector_by_friendly_name() -> None:
    device = UvcCaptureDevice(
        opencv_index=1,
        friendly_name="HD Pro Webcam C920",
        device_path="USB\\VID_046D&PID_08E5&MI_00\\7&abc&0&0000",
    )
    assert device_matches_selector(
        device,
        friendly_name="C920",
        device_name_contains=None,
        vendor_id=None,
        product_id=None,
    )


def test_device_matches_selector_requires_vid_pid_path_when_configured() -> None:
    device = UvcCaptureDevice(
        opencv_index=3,
        friendly_name="HP Wide Vision HD Camera",
        device_path="USB\\VID_04F2&PID_B6BB&MI_00\\6&abc&0&0000",
    )
    assert not device_matches_selector(
        device,
        friendly_name="HP Wide Vision HD Camera",
        device_name_contains=None,
        vendor_id="046D",
        product_id="08E5",
    )


def test_resolve_uvc_opencv_index_prefers_name_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "sharedautonomy.devices.uvc_resolve._list_dshow_capture_devices_subprocess",
        lambda: [
            UvcCaptureDevice(0, "Intel(R) RealSense(TM) Depth Camera 435i Depth"),
            UvcCaptureDevice(1, "HD Pro Webcam C920", "USB\\VID_046D&PID_08E5&MI_00\\7&abc&0&0000"),
            UvcCaptureDevice(3, "HP Wide Vision HD Camera", "USB\\VID_04F2&PID_B6BB&MI_00\\6&abc&0&0000"),
        ],
    )
    monkeypatch.setattr("sharedautonomy.devices.uvc_resolve.list_pnp_capture_devices", lambda: [])

    assert (
        resolve_uvc_opencv_index(
            friendly_name="HD Pro Webcam C920",
            vendor_id="046D",
            product_id="08E5",
            opencv_index_hint=3,
        )
        == 1
    )


def test_resolve_uvc_opencv_index_falls_back_to_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sharedautonomy.devices.uvc_resolve._list_dshow_capture_devices_subprocess", lambda: [])
    monkeypatch.setattr("sharedautonomy.devices.uvc_resolve.list_pnp_capture_devices", lambda: [])

    assert resolve_uvc_opencv_index(friendly_name="Missing Camera", opencv_index_hint=2) == 2
