"""Pulse the serial soft gripper to a chosen physical open fraction.

This gripper has no position feedback. Each command is a relative pulse, so the
script always closes fully first, then opens to the requested fraction
(``move_to_working_open``). Inspect the jaw gap by eye; nothing is read back.

Does not move the arm. Refuses to send pulses unless ``--allow-gripper`` is set.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sharedautonomy.robot.gripper import SerialSoftGripperTeleop

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = Path("configs/local/gripper_serial.local.yaml")
SUGGESTED_FRACTIONS = (0.65, 0.75, 0.85, 1.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "CAUTION: this calibration tool closes and opens the serial gripper. "
            "It never moves the arm and requires --allow-gripper before sending pulses."
        )
    )
    parser.add_argument(
        "--gripper-config",
        default=None,
        help=f"YAML path (default: {DEFAULT_CONFIG.as_posix()})",
    )
    parser.add_argument(
        "--allow-gripper",
        action="store_true",
        help="Send close/open pulses on the serial gripper (default: print-only)",
    )
    parser.add_argument(
        "--fraction",
        type=float,
        default=None,
        help="One-shot open fraction in [0, 1]. Omit for an interactive prompt.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the workspace-clear confirmation before the first pulse",
    )
    return parser.parse_args()


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load gripper local configs") from exc
    with path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a mapping")
    return payload


def _config_summary(path: Path) -> dict[str, Any]:
    payload = _load_yaml_mapping(path)
    teleop = payload.get("teleop") if isinstance(payload.get("teleop"), dict) else {}
    serial = payload.get("serial") if isinstance(payload.get("serial"), dict) else {}
    open_angle_deg = float(teleop.get("open_angle_deg", 1800.0))
    close_angle_deg = float(teleop.get("close_angle_deg", 1872.0))
    working = float(teleop.get("working_open_fraction", 1.0))
    return {
        "path": str(path),
        "port": serial.get("port"),
        "open_angle_deg": open_angle_deg,
        "close_angle_deg": close_angle_deg,
        "working_open_fraction": working,
        "working_open_pulse_deg": working * open_angle_deg,
    }


def _print_summary(summary: dict[str, Any]) -> None:
    print("Gripper config:")
    print(f"  file: {summary['path']}")
    print(f"  port: {summary['port']}")
    print(f"  close pulse: {summary['close_angle_deg']:.1f} deg")
    print(f"  full-open pulse: {summary['open_angle_deg']:.1f} deg")
    print(
        f"  yaml working_open_fraction: {summary['working_open_fraction']:.3f} "
        f"-> open pulse {summary['working_open_pulse_deg']:.1f} deg"
    )
    print("  (no encoder; judge opening by eye / 4 cm cube fit)")


def _confirm_clear(*, skip: bool) -> None:
    if skip:
        return
    answer = input("Gripper clear of fingers and objects? Type yes to pulse: ").strip().lower()
    if answer != "yes":
        raise SystemExit("Aborted: confirmation was not 'yes'.")


def _pulse(teleop: SerialSoftGripperTeleop, fraction: float) -> None:
    if not 0.0 <= fraction <= 1.0:
        raise ValueError(f"fraction must be in [0, 1], got {fraction}")
    close_deg, open_deg = teleop.move_to_working_open(fraction)
    if fraction <= 0.0:
        print(f"Closed (close pulse {close_deg:.1f} deg).")
        return
    print(
        f"Closed then opened to {fraction:.3f} "
        f"(close {close_deg:.1f} deg, open {open_deg:.1f} deg)."
    )


def _parse_repl_line(line: str) -> str | float | None:
    text = line.strip().lower()
    if not text:
        return None
    if text in {"q", "quit", "exit"}:
        return "quit"
    if text in {"c", "close"}:
        return 0.0
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError("Enter a fraction in [0, 1], 'close', or 'quit'") from exc
    if not 0.0 <= value <= 1.0:
        raise ValueError("fraction must be in [0, 1]")
    return value


def _repl(teleop: SerialSoftGripperTeleop) -> None:
    suggested = ", ".join(f"{item:g}" for item in SUGGESTED_FRACTIONS)
    print(
        "Interactive open calibration. Commands: <fraction> | close | quit. "
        f"Suggested: {suggested}"
    )
    while True:
        try:
            raw = input("open_fraction> ")
        except EOFError:
            print()
            return
        try:
            command = _parse_repl_line(raw)
        except ValueError as exc:
            print(f"  {exc}")
            continue
        if command is None:
            continue
        if command == "quit":
            return
        _pulse(teleop, float(command))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args()
    config_path = Path(args.gripper_config or DEFAULT_CONFIG)
    if not config_path.is_file():
        print(
            f"Gripper config not found: {config_path}. "
            "Copy configs/robot/gripper_serial.example.yaml to "
            "configs/local/gripper_serial.local.yaml and set the COM port.",
            file=sys.stderr,
        )
        return 1
    if args.fraction is not None and not 0.0 <= float(args.fraction) <= 1.0:
        print("--fraction must be in [0, 1]", file=sys.stderr)
        return 1

    summary = _config_summary(config_path)
    _print_summary(summary)

    if not args.allow_gripper:
        print(
            "Print-only. Re-run with --allow-gripper after confirming the gripper "
            "workspace is clear. Arm is not commanded by this script."
        )
        return 0

    from sharedautonomy.robot.gripper_config import load_serial_soft_gripper_stack

    gripper = None
    try:
        gripper, teleop, source = load_serial_soft_gripper_stack(config_path=config_path)
        logger.info("Connected serial gripper from %s", source)
        _confirm_clear(skip=bool(args.yes))
        if args.fraction is not None:
            _pulse(teleop, float(args.fraction))
        else:
            _repl(teleop)
    finally:
        if gripper is not None:
            gripper.disconnect()
            logger.info("Disconnected serial gripper")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
