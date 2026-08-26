"""Connect to an RM-65B, read one state sample, and disconnect without motion."""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any

from sharedautonomy.robot.rm65 import RM65, RM65Config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read one RM-65B state sample without sending any motion or gripper command."
    )
    parser.add_argument("--ip", required=True, help="RM-65B controller IP address")
    parser.add_argument("--port", type=int, default=8080, help="RM-65B controller port")
    parser.add_argument("--id", default="rm65-readonly-check", help="Local LeRobot device identifier")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = RM65Config(
        id=args.id,
        ip=args.ip,
        port=args.port,
        enable_motion=False,
        set_run_mode_on_connect=False,
    )
    robot = RM65(config)

    try:
        robot.connect()
        observation = robot.get_observation()
        printable = {
            key: _json_value(value)
            for key, value in observation.items()
            if key.startswith("joint_") or key.startswith("ee.")
        }
        print(json.dumps(printable, indent=2, sort_keys=True))
    finally:
        if robot.is_connected:
            robot.disconnect()


def _json_value(value: Any) -> Any:
    return value.item() if hasattr(value, "item") else value


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
