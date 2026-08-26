"""Load Cartesian workspace geometry from machine-local YAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sharedautonomy.assistance.safety_filter import example_cartesian_workspace
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
    if "tool_tip_offset_base_m" not in workspace_data:
        raise ValueError("workspace mapping must include tool_tip_offset_base_m")
    tool_tip_offset_base_m = workspace_data["tool_tip_offset_base_m"]
    min_tool_clearance_m = float(workspace_data.get("min_tool_clearance_m", 0.0))
    table_z_m = float(workspace_data.get("table_z_m", 0.0))
    if "min_flange_z_m" in workspace_data:
        # Keep the physical tool offset for XY tip projection while honoring an
        # explicit flange-Z floor from machine-local safety YAML.
        offset_z = float(tool_tip_offset_base_m[2])
        table_z_m = float(workspace_data["min_flange_z_m"]) + offset_z - min_tool_clearance_m
    return CartesianWorkspace(
        polygon_xy_m=workspace_data["polygon_xy_m"],
        table_z_m=table_z_m,
        min_tool_clearance_m=min_tool_clearance_m,
        tool_tip_offset_base_m=tool_tip_offset_base_m,
        max_flange_z_m=(
            None
            if workspace_data.get("max_flange_z_m", None) is None
            else float(workspace_data["max_flange_z_m"])
        ),
    )


def load_cartesian_workspace(
    path: str | Path | None = None,
    *,
    allow_example_fallback: bool = False,
) -> tuple[CartesianWorkspace, str]:
    """Load measured workspace geometry from explicit or machine-local YAML.

    Search order when ``path`` is None:
    1. ``configs/local/rm65_safety.local.yaml``

    The generic fallback exists only for no-motion dry-runs. Callers that can
    enable motion must leave ``allow_example_fallback`` false.
    """
    candidates: list[Path] = []
    if path is not None:
        candidates.append(Path(path))
    else:
        candidates.extend(
            [
                Path("configs/local/rm65_safety.local.yaml"),
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

    if allow_example_fallback:
        return example_cartesian_workspace(), "example_cartesian_workspace() [offline only]"

    requested = Path(path) if path is not None else Path("configs/local/rm65_safety.local.yaml")
    raise FileNotFoundError(
        f"Measured Cartesian workspace config not found: {requested}. "
        "Copy configs/local/rm65_safety.example.yaml to "
        "configs/local/rm65_safety.local.yaml and enter cell-specific geometry."
    )
