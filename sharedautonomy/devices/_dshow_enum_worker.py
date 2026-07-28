"""Isolated DirectShow enumeration worker for subprocess invocation."""

from __future__ import annotations

import json
import sys

from sharedautonomy.devices._dshow_enum_win32 import enumerate_dshow_video_input_devices


def main() -> int:
    payload = [
        {
            "opencv_index": index,
            "friendly_name": friendly_name,
            "device_path": device_path,
        }
        for index, friendly_name, device_path in enumerate_dshow_video_input_devices()
    ]
    json.dump(payload, sys.stdout, ensure_ascii=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
