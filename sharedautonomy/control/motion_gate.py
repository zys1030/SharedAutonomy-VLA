"""Dual-confirmation gate for enabling real robot motion."""

from __future__ import annotations

from pathlib import Path

from sharedautonomy.robot.safety import MotionDisabledError

DEFAULT_LOCAL_MOTION_CONFIG = Path("configs/local/manual_cartesian.local.yaml")
DEFAULT_DISABLED_MOTION_SOURCE = "built-in safe default (motion disabled)"


def load_motion_enable_config(
    *,
    cli_config_enable_motion: bool,
    config_path: str | Path = DEFAULT_LOCAL_MOTION_CONFIG,
    disabled_source: str = DEFAULT_DISABLED_MOTION_SOURCE,
) -> tuple[bool, str]:
    """Load the local motion flag or fall back to the explicit CLI stand-in.

    This function only resolves the configuration half of the motion gate.
    :func:`resolve_motion_enabled` must still validate the independent
    ``--allow-motion`` confirmation before any command can be sent.
    """
    path = Path(config_path)
    if path.is_file():
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("PyYAML is required to load the local motion config") from exc

        with path.open(encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
        if not isinstance(payload, dict):
            raise ValueError(f"{path} must contain a mapping")
        return bool(payload.get("enable_motion", False)), str(path)
    if cli_config_enable_motion:
        return True, "--config-enable-motion"
    return False, disabled_source


def resolve_motion_enabled(
    *,
    config_enable_motion: bool,
    cli_allow_motion: bool,
) -> bool:
    """Return True only when local config and CLI both explicitly allow motion.

    Engineering rule: motion requires dual confirmation. A lone ``--allow-motion``
    flag is rejected when the config still has ``enable_motion: false``.
    """
    config_ok = bool(config_enable_motion)
    cli_ok = bool(cli_allow_motion)
    if cli_ok and not config_ok:
        raise MotionDisabledError(
            "CLI --allow-motion was set, but config enable_motion is false. "
            "Set enable_motion: true in the local collection config as well."
        )
    return config_ok and cli_ok
