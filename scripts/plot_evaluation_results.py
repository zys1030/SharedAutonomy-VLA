"""Generate the public evaluation figure from JSON and CSV result files.

This offline helper does not connect to hardware. Position heatmaps aggregate
each categorical XY position over the four evaluated yaw conditions.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

LATERAL_BANDS = ("left", "center", "right")
DISTANCE_BANDS = ("near", "middle", "far")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the public Manual vs shared-autonomy evaluation figure."
    )
    parser.add_argument("--results-json", type=Path, default=Path("docs/results.json"))
    parser.add_argument("--records-csv", type=Path, default=Path("docs/evaluation_records.csv"))
    parser.add_argument("--output", type=Path, default=Path("assets/results_paired.svg"))
    return parser.parse_args()


def parse_bool(value: str, *, field: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError(f"{field} must be true or false, got {value!r}")


def load_records(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    records = [
        {
            "position_id": row["position_id"],
            "distance_band": row["distance_band"],
            "lateral_band": row["lateral_band"],
            "manual_success": parse_bool(row["manual_success"], field="manual_success"),
            "shared_autonomy_success": parse_bool(
                row["shared_autonomy_success"], field="shared_autonomy_success"
            ),
        }
        for row in rows
    ]
    if len(records) != 36:
        raise ValueError(f"Expected 36 evaluation records, found {len(records)}")
    positions = Counter(record["position_id"] for record in records)
    if len(positions) != 9 or set(positions.values()) != {4}:
        raise ValueError(f"Expected nine positions with four yaw records each, found {dict(positions)}")
    return records


def load_results(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def position_rates(records: list[dict[str, Any]], *, field: str) -> list[list[float]]:
    rates: list[list[float]] = []
    for distance in DISTANCE_BANDS:
        row: list[float] = []
        for lateral in LATERAL_BANDS:
            values = [
                record[field]
                for record in records
                if record["distance_band"] == distance and record["lateral_band"] == lateral
            ]
            if len(values) != 4:
                raise ValueError(f"Expected four records for {lateral}_{distance}, found {len(values)}")
            row.append(sum(values) / 4)
        rates.append(row)
    return rates


def validate_summary(records: list[dict[str, Any]], results: dict[str, Any]) -> None:
    summary = results["results"]
    manual = sum(record["manual_success"] for record in records)
    shared = sum(record["shared_autonomy_success"] for record in records)
    if manual != summary["manual"]["successes"] or shared != summary["shared_autonomy"]["successes"]:
        raise ValueError("CSV success totals do not match results.json")

    paired = Counter()
    for record in records:
        manual_success = record["manual_success"]
        shared_success = record["shared_autonomy_success"]
        if manual_success and shared_success:
            paired["both_success"] += 1
        elif manual_success:
            paired["manual_only_success"] += 1
        elif shared_success:
            paired["shared_autonomy_only_success"] += 1
        else:
            paired["both_failure"] += 1
    if dict(paired) != summary["paired_outcomes"]:
        raise ValueError(f"CSV paired outcomes do not match results.json: {dict(paired)}")


def annotate_heatmap(axis: Any, values: list[list[float]], *, difference: bool) -> None:
    for row_index, row in enumerate(values):
        for column_index, value in enumerate(row):
            if difference:
                label = f"{value * 100:+.0f} pp"
                color = "white" if abs(value) >= 0.5 else "#172033"
            else:
                label = f"{round(value * 4):.0f}/4\n{value * 100:.0f}%"
                color = "white" if value >= 0.65 else "#172033"
            axis.text(
                column_index,
                row_index,
                label,
                ha="center",
                va="center",
                color=color,
                fontsize=10,
                fontweight="bold",
            )


def plot_results(output: Path, records: list[dict[str, Any]], results: dict[str, Any]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.patches import FancyBboxPatch

    output_format = output.suffix.lower().lstrip(".")
    if output_format not in {"svg", "png"}:
        raise ValueError("--output must end in .svg or .png")
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "axes.titleweight": "bold",
            "axes.titlesize": 12,
            "text.color": "#172033",
            "xtick.color": "#64748B",
            "ytick.color": "#64748B",
            "svg.hashsalt": "sharedautonomy-vla-evaluation-v1",
        }
    )

    summary = results["results"]
    attempts = int(summary["manual"]["attempts"])
    manual_successes = int(summary["manual"]["successes"])
    shared_successes = int(summary["shared_autonomy"]["successes"])
    manual_rate = manual_successes / attempts
    shared_rate = shared_successes / attempts
    paired = summary["paired_outcomes"]
    manual_positions = position_rates(records, field="manual_success")
    shared_positions = position_rates(records, field="shared_autonomy_success")
    difference = [
        [shared_positions[row][column] - manual_positions[row][column] for column in range(3)]
        for row in range(3)
    ]

    figure = plt.figure(figsize=(14.4, 9.2), facecolor="#F8FAFC")
    grid = figure.add_gridspec(2, 6, height_ratios=(1.0, 1.08), hspace=0.48, wspace=0.55)
    rate_axis = figure.add_subplot(grid[0, :3])
    paired_axis = figure.add_subplot(grid[0, 3:])
    manual_axis = figure.add_subplot(grid[1, 0:2])
    shared_axis = figure.add_subplot(grid[1, 2:4])
    difference_axis = figure.add_subplot(grid[1, 4:6])
    figure.suptitle(
        "Closed-loop evaluation over 36 paired conditions",
        x=0.06,
        y=0.987,
        ha="left",
        fontsize=22,
        fontweight="bold",
    )
    figure.text(
        0.06,
        0.918,
        "Same 9 × 4 grid, one real-robot rollout per policy per condition",
        ha="left",
        fontsize=11.5,
        color="#475569",
    )

    rates = (manual_rate, shared_rate)
    bars = rate_axis.barh(("Manual", "Shared autonomy"), rates, color=("#64748B", "#1565C0"), height=0.5)
    rate_axis.invert_yaxis()
    rate_axis.set_xlim(0.0, 1.0)
    rate_axis.set_xticks(np.linspace(0.0, 1.0, 5), ("0%", "25%", "50%", "75%", "100%"))
    rate_axis.set_title("Hard-success rate", loc="left", pad=12)
    rate_axis.grid(axis="x", color="#E2E8F0", linewidth=0.8)
    rate_axis.set_axisbelow(True)
    for bar, successes, rate in zip(bars, (manual_successes, shared_successes), rates, strict=True):
        rate_axis.text(
            rate / 2,
            bar.get_y() + bar.get_height() / 2,
            f"{successes}/{attempts} · {rate:.1%}",
            ha="center",
            va="center",
            color="white",
            fontsize=11,
            fontweight="bold",
        )
    rate_axis.text(
        0.5,
        -0.22,
        f"+{shared_successes - manual_successes} conditions · +{(shared_rate - manual_rate) * 100:.1f} pp",
        transform=rate_axis.transAxes,
        ha="center",
        color="#1D4ED8",
        fontsize=10.5,
        fontweight="bold",
        bbox={"boxstyle": "round,pad=0.45", "facecolor": "#EFF6FF", "edgecolor": "none"},
    )
    for spine in ("top", "right", "left"):
        rate_axis.spines[spine].set_visible(False)
    rate_axis.spines["bottom"].set_color("#94A3B8")
    rate_axis.tick_params(axis="y", length=0, labelsize=10.5)

    paired_axis.set_xlim(0, 2)
    paired_axis.set_ylim(0, 2)
    paired_axis.set_aspect("equal")
    paired_axis.set_title("Paired outcomes", loc="left", pad=12)
    values = (
        (paired["both_success"], paired["shared_autonomy_only_success"]),
        (paired["manual_only_success"], paired["both_failure"]),
    )
    labels = (("both succeed", "SA only"), ("Manual only", "both fail"))
    colors = (("#2E7D32", "#1565C0"), ("#F9A825", "#6B7280"))
    for row in range(2):
        for column in range(2):
            y_position = 1 - row
            paired_axis.add_patch(
                FancyBboxPatch(
                    (column + 0.06, y_position + 0.06),
                    0.88,
                    0.88,
                    boxstyle="round,pad=0.02,rounding_size=0.08",
                    linewidth=0,
                    facecolor=colors[row][column],
                )
            )
            color = "#172033" if (row, column) == (1, 0) else "white"
            paired_axis.text(
                column + 0.5,
                y_position + 0.61,
                str(values[row][column]),
                ha="center",
                va="center",
                fontsize=23,
                fontweight="bold",
                color=color,
            )
            paired_axis.text(
                column + 0.5,
                y_position + 0.33,
                labels[row][column],
                ha="center",
                va="center",
                fontsize=9.5,
                color=color,
            )
    paired_axis.set_xticks((0.5, 1.5), ("Manual success", "Manual failure"))
    paired_axis.set_yticks((1.5, 0.5), ("SA success", "SA failure"))
    paired_axis.tick_params(length=0, labelsize=9.5)
    for spine in paired_axis.spines.values():
        spine.set_visible(False)

    success_cmap = LinearSegmentedColormap.from_list("success", ("#F1F5F9", "#93C5FD", "#1565C0"))
    difference_cmap = LinearSegmentedColormap.from_list("difference", ("#F59E0B", "#FFFFFF", "#1565C0"))
    heatmaps = (
        (manual_axis, manual_positions, "Manual by initial position", success_cmap, 0.0, 1.0, False),
        (shared_axis, shared_positions, "Shared autonomy by initial position", success_cmap, 0.0, 1.0, False),
        (difference_axis, difference, "SA − Manual by initial position", difference_cmap, -1.0, 1.0, True),
    )
    for axis, matrix, title, cmap, minimum, maximum, is_difference in heatmaps:
        axis.imshow(matrix, cmap=cmap, vmin=minimum, vmax=maximum, aspect="equal")
        axis.set_title(title, pad=12)
        axis.set_xticks(range(3), ("Left", "Center", "Right"))
        axis.set_yticks(range(3), ("Near", "Middle", "Far"))
        axis.set_xlabel("Lateral position")
        if axis is manual_axis:
            axis.set_ylabel("Distance band")
        annotate_heatmap(axis, matrix, difference=is_difference)
        axis.set_xticks(np.arange(-0.5, 3, 1), minor=True)
        axis.set_yticks(np.arange(-0.5, 3, 1), minor=True)
        axis.grid(which="minor", color="white", linewidth=3)
        axis.tick_params(which="minor", bottom=False, left=False)
        axis.tick_params(which="major", length=0)
        for spine in axis.spines.values():
            spine.set_visible(False)

    figure.text(
        0.5,
        0.025,
        "Position heatmaps aggregate over four yaw values. Fixed-grid descriptive results; no error bars.",
        ha="center",
        fontsize=9.5,
        color="#64748B",
    )
    figure.subplots_adjust(left=0.085, right=0.965, top=0.845, bottom=0.10)
    output.parent.mkdir(parents=True, exist_ok=True)
    options: dict[str, Any] = {
        "format": output_format,
        "bbox_inches": "tight",
        "facecolor": figure.get_facecolor(),
    }
    if output_format == "png":
        options["dpi"] = 180
    else:
        options["metadata"] = {"Date": None}
    figure.savefig(output, **options)
    plt.close(figure)


def main() -> int:
    args = parse_args()
    records = load_records(args.records_csv)
    results = load_results(args.results_json)
    validate_summary(records, results)
    plot_results(args.output, records, results)
    print(f"Wrote {args.output} from {args.results_json} and {args.records_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
