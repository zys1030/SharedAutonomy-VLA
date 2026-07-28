"""List external UVC capture devices and resolved OpenCV indices."""

from __future__ import annotations

import argparse

from sharedautonomy.devices.uvc_resolve import list_dshow_capture_devices, resolve_uvc_opencv_index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe external UVC cameras and OpenCV DirectShow indices.")
    parser.add_argument("--friendly-name", default=None, help="Substring match against DirectShow friendly name")
    parser.add_argument("--vendor-id", default=None, help="USB vendor ID, e.g. 046D")
    parser.add_argument("--product-id", default=None, help="USB product ID, e.g. 08E5")
    parser.add_argument("--opencv-index-hint", type=int, default=None, help="Fallback OpenCV index")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    devices = list_dshow_capture_devices()
    if not devices:
        print("No DirectShow video input devices found.")
        return 1

    print("DirectShow video input devices:")
    for device in devices:
        suffix = f" | {device.device_path}" if device.device_path else ""
        print(f"  [{device.opencv_index}] {device.friendly_name}{suffix}")

    if args.friendly_name or args.vendor_id or args.product_id or args.opencv_index_hint is not None:
        resolved = resolve_uvc_opencv_index(
            friendly_name=args.friendly_name,
            vendor_id=args.vendor_id,
            product_id=args.product_id,
            opencv_index_hint=args.opencv_index_hint,
        )
        print(f"Resolved OpenCV index: {resolved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
