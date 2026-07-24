"""Dual-confirmation gate for enabling real robot motion."""

from __future__ import annotations

from sharedautonomy.robot.safety import MotionDisabledError


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
