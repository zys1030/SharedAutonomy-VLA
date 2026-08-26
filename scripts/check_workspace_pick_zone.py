"""Check that the configured workspace covers the shape_pick_place_v1 pick rectangle."""

from __future__ import annotations

import argparse
import sys

from sharedautonomy.assistance.workspace_config import load_cartesian_workspace
from sharedautonomy.robot.safety import CartesianSafetyError, validate_cartesian_segment

PICK_X_MIN = -0.42
PICK_X_MAX = -0.15
PICK_Y_MIN = -0.09
PICK_Y_MAX = 0.17
MARGIN_M = 0.02
PROBE_FLANGE_Z_M = 0.228


def _pick_corners(*, margin_m: float) -> list[tuple[str, float, float]]:
    return [
        ("pick_ll", PICK_X_MIN - margin_m, PICK_Y_MIN - margin_m),
        ("pick_lr", PICK_X_MAX + margin_m, PICK_Y_MIN - margin_m),
        ("pick_ul", PICK_X_MIN - margin_m, PICK_Y_MAX + margin_m),
        ("pick_ur", PICK_X_MAX + margin_m, PICK_Y_MAX + margin_m),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-yaml", default=None)
    parser.add_argument("--margin-m", type=float, default=MARGIN_M)
    args = parser.parse_args()

    workspace, source = load_cartesian_workspace(args.workspace_yaml)
    flange = [0.0, 0.0, PROBE_FLANGE_Z_M]
    failures: list[str] = []
    for label, x, y in _pick_corners(margin_m=float(args.margin_m)):
        flange[0] = x
        flange[1] = y
        try:
            validate_cartesian_segment(flange, flange, workspace)
        except CartesianSafetyError as exc:
            failures.append(f"{label} ({x:.3f}, {y:.3f}): {exc}")

    print(f"workspace_source: {source}")
    print(f"vertices: {len(workspace.polygon_xy_m)}")
    if failures:
        print("FAIL")
        for line in failures:
            print(f"  - {line}")
        return 1

    print("PASS: pick rectangle corners (with margin) are inside the safe polygon")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
