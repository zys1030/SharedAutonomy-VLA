"""Visual step-through replay for native SharedAutonomy episodes.

Read-only: loads episode artifacts from disk and does not connect to hardware.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.gridspec import GridSpec
from sharedautonomy.data import EpisodeStep, RecordedEpisode, load_recorded_episode
from sharedautonomy.data.recorder import EpisodeRecorderError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replay a native episode step by step with wrist/external RGB, "
            "an EE 3D trajectory subplot, and control-state overlays. "
            "Read-only; does not connect to robot hardware."
        )
    )
    parser.add_argument(
        "episode_dir",
        type=Path,
        help="Path to an episode directory containing metadata.json, steps.jsonl, and images/",
    )
    parser.add_argument(
        "--step",
        type=int,
        default=0,
        help="Initial step index (default: 0)",
    )
    parser.add_argument(
        "--hz",
        type=float,
        default=None,
        help="Auto-play rate in Hz. Omit for manual stepping with arrow keys.",
    )
    return parser.parse_args()


def _vector_text(values: tuple[float, float, float] | list[float]) -> str:
    return f"[{values[0]:+.4f}, {values[1]:+.4f}, {values[2]:+.4f}]"


def format_step_status(episode: RecordedEpisode, step: EpisodeStep) -> str:
    human = step.human_action
    executed = step.executed_action
    ee = step.observation.ee_position_m
    warnings = ", ".join(step.sync_warnings) if step.sync_warnings else "(none)"
    lines = [
        f"episode: {episode.metadata.episode_id} | run: {episode.metadata.run_id} | status: {episode.status}",
        (
            f"step: {step.step_index + 1}/{len(episode.steps)} "
            f"(index {step.step_index}) | deadman: {human.deadman_active}"
        ),
        f"human linear vel (m/s): {_vector_text(human.linear_velocity_m_s)}",
        f"human angular vel (rad/s): {_vector_text(human.angular_velocity_rad_s)}",
        f"executed linear vel (m/s): {_vector_text(executed.linear_velocity_m_s)}",
        f"executed angular vel (rad/s): {_vector_text(executed.angular_velocity_rad_s)}",
        f"ee position (m): {_vector_text(ee)}",
        f"sync_warnings: {warnings}",
    ]
    if executed.safety_intervened:
        reasons = ", ".join(executed.safety_reasons) or "(none)"
        lines.append(f"safety_intervened: True ({reasons})")
    return "\n".join(lines)


def _blank_panel(shape: tuple[int, int, int] = (480, 640, 3)) -> np.ndarray:
    panel = np.zeros(shape, dtype=np.uint8)
    panel[:] = (32, 32, 32)
    return panel


def _ee_positions_m(episode: RecordedEpisode) -> np.ndarray:
    """Stack EE positions as an (N, 3) array in metres."""
    return np.asarray(
        [step.observation.ee_position_m for step in episode.steps],
        dtype=np.float64,
    )


def _axis_limits(values: np.ndarray, *, pad_m: float = 0.02) -> tuple[float, float]:
    lo = float(np.min(values))
    hi = float(np.max(values))
    if abs(hi - lo) < 1e-6:
        return lo - pad_m, hi + pad_m
    span = hi - lo
    margin = max(pad_m, 0.05 * span)
    return lo - margin, hi + margin


class EpisodeReplayViewer:
    """Matplotlib viewer with optional auto-play and EE 3D trajectory."""

    def __init__(self, episode: RecordedEpisode, *, start_step: int, hz: float | None) -> None:
        if not episode.steps:
            raise ValueError("episode has no steps to replay")
        self.episode = episode
        self.step_index = max(0, min(int(start_step), len(episode.steps) - 1))
        self.hz = hz
        self._timer = None
        self._ee_xyz = _ee_positions_m(episode)

        self.wrist_image = None
        self.external_image = None
        self._ee_current = None

        self.fig = plt.figure(figsize=(14, 6.5))
        grid = GridSpec(
            2,
            3,
            figure=self.fig,
            height_ratios=[3.2, 1.2],
            width_ratios=[1.0, 1.0, 1.1],
            hspace=0.25,
            wspace=0.25,
        )
        self.ax_wrist = self.fig.add_subplot(grid[0, 0])
        self.ax_external = self.fig.add_subplot(grid[0, 1])
        self.ax_ee = self.fig.add_subplot(grid[0, 2], projection="3d")
        self.ax_status = self.fig.add_subplot(grid[1, :])
        self.ax_status.axis("off")
        self.status_text = self.ax_status.text(
            0.0,
            1.0,
            "",
            ha="left",
            va="top",
            fontsize=10,
            family="monospace",
            transform=self.ax_status.transAxes,
        )

        self._init_ee_axes()
        self.fig.canvas.mpl_connect("key_press_event", self._on_key_press)
        self._render_current_step()
        self._configure_title()

        if self.hz is not None:
            if self.hz <= 0.0:
                raise ValueError("--hz must be positive")
            interval_ms = max(1, int(round(1000.0 / self.hz)))
            self._timer = self.fig.canvas.new_timer(interval=interval_ms)
            self._timer.add_callback(self._advance_step)
            self._timer.start()

    def _init_ee_axes(self) -> None:
        xs = self._ee_xyz[:, 0]
        ys = self._ee_xyz[:, 1]
        zs = self._ee_xyz[:, 2]
        self.ax_ee.plot(xs, ys, zs, color="0.55", linewidth=1.5, label="trajectory")
        (self._ee_current,) = self.ax_ee.plot(
            [xs[self.step_index]],
            [ys[self.step_index]],
            [zs[self.step_index]],
            "o",
            color="C3",
            markersize=8,
            label="current",
        )
        self.ax_ee.set_xlabel("X (m)")
        self.ax_ee.set_ylabel("Y (m)")
        self.ax_ee.set_zlabel("Z (m)")
        self.ax_ee.set_title("EE trajectory (base)")
        self.ax_ee.set_xlim(*_axis_limits(xs))
        self.ax_ee.set_ylim(*_axis_limits(ys))
        self.ax_ee.set_zlim(*_axis_limits(zs))
        # Base frame view: +X into screen, +Y left, +Z up.
        self.ax_ee.view_init(elev=30, azim=180)
        self.ax_ee.legend(loc="upper left", fontsize=8)

    def _configure_title(self) -> None:
        controls = "auto-play" if self.hz is not None else "keys: <-/-> step, q quit"
        self.fig.suptitle(f"Episode replay ({controls})", fontsize=12)

    def _render_current_step(self) -> None:
        step = self.episode.steps[self.step_index]
        wrist = step.observation.wrist_camera
        external = step.observation.external_camera

        wrist_rgb = wrist.color_rgb if wrist is not None else _blank_panel()
        external_rgb = external.color_rgb if external is not None else _blank_panel()

        if self.wrist_image is None:
            self.wrist_image = self.ax_wrist.imshow(wrist_rgb)
        else:
            self.wrist_image.set_data(wrist_rgb)

        if self.external_image is None:
            self.external_image = self.ax_external.imshow(external_rgb)
        else:
            self.external_image.set_data(external_rgb)

        self.ax_wrist.set_title("wrist" if wrist is not None else "wrist (missing)")
        self.ax_external.set_title("external" if external is not None else "external (missing)")
        self.ax_wrist.axis("off")
        self.ax_external.axis("off")

        current = self._ee_xyz[self.step_index]
        if self._ee_current is not None:
            self._ee_current.set_data_3d([current[0]], [current[1]], [current[2]])

        self.status_text.set_text(format_step_status(self.episode, step))
        self.fig.canvas.draw_idle()

    def _advance_step(self) -> None:
        if self.step_index + 1 >= len(self.episode.steps):
            if self._timer is not None:
                self._timer.stop()
            return
        self.step_index += 1
        self._render_current_step()

    def _retreat_step(self) -> None:
        if self.step_index <= 0:
            return
        self.step_index -= 1
        self._render_current_step()

    def _on_key_press(self, event) -> None:
        if event.key in {"right", "n", " "}:
            self._advance_step()
        elif event.key in {"left", "p"}:
            self._retreat_step()
        elif event.key in {"q", "escape"}:
            plt.close(self.fig)

    def show(self) -> None:
        plt.show()


def main() -> int:
    args = parse_args()
    try:
        episode = load_recorded_episode(args.episode_dir)
    except (EpisodeRecorderError, OSError, ValueError) as exc:
        print(f"Failed to load episode: {exc}", file=sys.stderr)
        print(
            "Replay requires metadata.json, steps.jsonl, and referenced images/ files.",
            file=sys.stderr,
        )
        return 1

    try:
        viewer = EpisodeReplayViewer(episode, start_step=args.step, hz=args.hz)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    viewer.show()
    return 0


if __name__ == "__main__":
    sys.exit(main())
