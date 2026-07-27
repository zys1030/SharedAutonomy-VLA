"""Check a native SharedAutonomy episode directory and print a structured summary."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sharedautonomy.data import (
    check_episode_dir,
    episode_check_report_to_dict,
    format_episode_check_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a native episode directory and print step, camera, sync, "
            "action, and end-effector statistics. Read-only; does not load robot hardware."
        )
    )
    parser.add_argument(
        "episode_dir",
        type=Path,
        help="Path to an episode directory containing metadata.json and steps.jsonl",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit EpisodeCheckReport as JSON on stdout instead of the text summary",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = check_episode_dir(args.episode_dir)
    if args.json:
        print(
            json.dumps(episode_check_report_to_dict(report), indent=2, ensure_ascii=True),
            flush=True,
        )
    else:
        print(format_episode_check_report(report), flush=True)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
