"""Load Cartesian workspace geometry from local YAML or the stamp fixture."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sharedautonomy.assistance.safety_filter import stamp_cartesian_workspace
from sharedautonomy.robot.safety import CartesianWorkspace


def _extract_workspace_mapping(data: dict[str, Any]) -> dict[str, Any]:
    """Accept flat YAML or nested ``cartesian_safety`` (as in rm65_safety.local.yaml)."""
    nested = data.get("cartesian_safety")
    if isinstance(nested, dict) and "polygon_xy_m" in nested:
        return nested
    if "polygon_xy_m" in data:
        return data
    raise ValueError(
        "workspace mapping must include polygon_xy_m at the top level "
        "or under cartesian_safety"
    )


def workspace_from_mapping(data: dict[str, Any]) -> CartesianWorkspace:
    """Build a ``CartesianWorkspace`` from a plain mapping (e.g. YAML contents)."""
    workspace_data = _extract_workspace_mapping(data)
    return CartesianWorkspace(
        polygon_xy_m=workspace_data["polygon_xy_m"],
        table_z_m=float(workspace_data.get("table_z_m", 0.0)),
        min_tool_clearance_m=float(workspace_data.get("min_tool_clearance_m", 0.0)),
        tool_tip_offset_base_m=workspace_data.get("tool_tip_offset_base_m", [0.0, 0.0, -0.178]),
        max_flange_z_m=(
            None
            if workspace_data.get("max_flange_z_m", None) is None
            else float(workspace_data["max_flange_z_m"])
        ),
    )


def load_cartesian_workspace(path: str | Path | None = None) -> tuple[CartesianWorkspace, str]:
    """Load stamp/local workspace YAML, falling back to the in-code stamp fixture.

    Search order when ``path`` is None:
    1. ``configs/local/rm65_safety.local.yaml``
    2. ``configs/local/rm65_safety.example.yaml``
    3. in-code ``stamp_cartesian_workspace()``
    """
    candidates: list[Path] = []
    if path is not None:
        candidates.append(Path(path))
    else:
        candidates.extend(
            [
                Path("configs/local/rm65_safety.local.yaml"),
                Path("configs/local/rm65_safety.example.yaml"),
            ]
        )

    for candidate in candidates:
        if not candidate.is_file():
            continue
        import yaml

        with candidate.open(encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
        if not isinstance(payload, dict):
            raise ValueError(f"Workspace file must contain a mapping: {candidate}")
        return workspace_from_mapping(payload), str(candidate)

    return stamp_cartesian_workspace(), "stamp_cartesian_workspace()"
