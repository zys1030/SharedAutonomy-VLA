"""Episode summary and validation for native SharedAutonomy recordings.

These helpers are shared by ``scripts/check_episode.py``, replay tooling, and
offline tests. They read metadata and ``steps.jsonl`` without loading ``.npy``
image payloads so checks work even when ``images/`` is missing or incomplete.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sharedautonomy.data.recorder import (
    EPISODE_FORMAT,
    METADATA_FILENAME,
    STEPS_FILENAME,
    IMAGES_DIRNAME,
    EpisodeStep,
    RecordedEpisode,
)

Vector3 = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class AxisRange:
    """Inclusive min/max for one spatial axis."""

    min_value: float
    max_value: float


@dataclass(frozen=True, slots=True)
class VectorRange:
    """Inclusive min/max for a 3-vector field sampled across steps."""

    x: AxisRange
    y: AxisRange
    z: AxisRange


@dataclass(frozen=True, slots=True)
class VelocityActionStats:
    """Summary statistics for Cartesian velocity commands."""

    step_count: int
    linear_norm_min_m_s: float
    linear_norm_max_m_s: float
    linear_norm_mean_m_s: float
    angular_norm_min_rad_s: float
    angular_norm_max_rad_s: float
    angular_norm_mean_rad_s: float
    deadman_active_steps: int | None = None
    deadman_active_fraction: float | None = None


@dataclass(frozen=True, slots=True)
class CameraCoverage:
    """Camera presence in step records and optional on-disk image files."""

    total_steps: int
    wrist_steps: int
    external_steps: int
    wrist_fraction: float
    external_fraction: float
    images_dir_present: bool
    wrist_image_files_found: int | None
    external_image_files_found: int | None


@dataclass(frozen=True, slots=True)
class EeMotionStats:
    """End-effector position range and per-step displacement norms."""

    position_range_m: VectorRange
    step_delta_norm_min_m: float
    step_delta_norm_max_m: float
    step_delta_norm_mean_m: float


@dataclass(frozen=True, slots=True)
class EpisodeCheckReport:
    """Structured episode validation result."""

    episode_dir: Path
    episode_id: str
    run_id: str
    status: str
    step_count: int
    metadata_step_count: int | None
    step_count_consistent: bool
    step_index_consistent: bool
    camera_coverage: CameraCoverage
    sync_warning_counts: dict[str, int]
    sync_warning_step_count: int
    human_action_stats: VelocityActionStats
    executed_action_stats: VelocityActionStats
    ee_motion_stats: EeMotionStats
    issues: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """True when there are no hard issues (warnings may still be present)."""
        return not self.issues


def check_episode_dir(episode_dir: str | Path) -> EpisodeCheckReport:
    """Validate an on-disk native episode without loading image arrays."""
    root = Path(episode_dir)
    issues: list[str] = []
    warnings: list[str] = []

    meta_path = root / METADATA_FILENAME
    steps_path = root / STEPS_FILENAME
    if not meta_path.is_file():
        return _error_report(
            root,
            issues=(f"missing {METADATA_FILENAME}",),
            warnings=warnings,
        )
    if not steps_path.is_file():
        return _error_report(
            root,
            issues=(f"missing {STEPS_FILENAME}",),
            warnings=warnings,
        )

    try:
        envelope = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _error_report(root, issues=(f"invalid {METADATA_FILENAME}: {exc}",), warnings=warnings)

    if envelope.get("format") != EPISODE_FORMAT:
        issues.append(
            f"unsupported episode format {envelope.get('format')!r}; expected {EPISODE_FORMAT!r}"
        )

    metadata = envelope.get("metadata") or {}
    episode_id = str(metadata.get("episode_id", "unknown"))
    run_id = str(metadata.get("run_id", "unknown"))
    status = str(envelope.get("status", "unknown"))
    metadata_step_count = envelope.get("step_count")
    if metadata_step_count is not None:
        metadata_step_count = int(metadata_step_count)

    step_payloads = _load_step_payloads(steps_path, issues)
    images_dir = root / IMAGES_DIRNAME
    images_dir_present = images_dir.is_dir()
    if not images_dir_present and any(_step_references_images(item) for item in step_payloads):
        warnings.append(
            f"missing {IMAGES_DIRNAME}/; camera statistics use step metadata only "
            "(image file coverage unavailable)"
        )

    return _build_report_from_payloads(
        episode_dir=root,
        episode_id=episode_id,
        run_id=run_id,
        status=status,
        metadata_step_count=metadata_step_count,
        step_payloads=step_payloads,
        images_dir_present=images_dir_present,
        issues=issues,
        warnings=warnings,
    )


def check_recorded_episode(episode: RecordedEpisode) -> EpisodeCheckReport:
    """Validate an in-memory episode loaded by ``load_recorded_episode``."""
    issues: list[str] = []
    warnings: list[str] = []
    step_payloads = [_step_to_payload(step) for step in episode.steps]
    images_dir_present = (episode.episode_dir / IMAGES_DIRNAME).is_dir()
    if not images_dir_present and any(_step_references_images(item) for item in step_payloads):
        warnings.append(
            f"missing {IMAGES_DIRNAME}/; camera statistics use step metadata only "
            "(image file coverage unavailable)"
        )
    return _build_report_from_payloads(
        episode_dir=episode.episode_dir,
        episode_id=episode.metadata.episode_id,
        run_id=episode.metadata.run_id,
        status=episode.status,
        metadata_step_count=len(episode.steps),
        step_payloads=step_payloads,
        images_dir_present=images_dir_present,
        issues=issues,
        warnings=warnings,
    )


def format_episode_check_report(report: EpisodeCheckReport) -> str:
    """Render a human-readable multi-line summary."""
    lines = [
        f"episode_id: {report.episode_id}",
        f"run_id: {report.run_id}",
        f"status: {report.status}",
        f"episode_dir: {report.episode_dir}",
        "",
        "steps:",
        f"  count: {report.step_count}",
        f"  metadata_step_count: {report.metadata_step_count}",
        f"  step_count_consistent: {report.step_count_consistent}",
        f"  step_index_consistent: {report.step_index_consistent}",
        "",
        "camera_coverage:",
        f"  wrist_steps: {report.camera_coverage.wrist_steps}/{report.camera_coverage.total_steps} "
        f"({report.camera_coverage.wrist_fraction:.1%})",
        f"  external_steps: {report.camera_coverage.external_steps}/{report.camera_coverage.total_steps} "
        f"({report.camera_coverage.external_fraction:.1%})",
        f"  images_dir_present: {report.camera_coverage.images_dir_present}",
    ]
    if report.camera_coverage.wrist_image_files_found is not None:
        lines.append(
            f"  wrist_image_files_found: {report.camera_coverage.wrist_image_files_found}"
        )
    if report.camera_coverage.external_image_files_found is not None:
        lines.append(
            f"  external_image_files_found: {report.camera_coverage.external_image_files_found}"
        )

    lines.extend(
        [
            "",
            "sync_warnings:",
            f"  steps_with_warnings: {report.sync_warning_step_count}",
            f"  total_warning_events: {sum(report.sync_warning_counts.values())}",
        ]
    )
    if report.sync_warning_counts:
        for name, count in sorted(report.sync_warning_counts.items()):
            lines.append(f"  {name}: {count}")
    else:
        lines.append("  (none)")

    lines.extend(
        [
            "",
            "human_action:",
            *_format_velocity_stats(report.human_action_stats, include_deadman=True),
            "",
            "executed_action:",
            *_format_velocity_stats(report.executed_action_stats, include_deadman=False),
            "",
            "ee_motion:",
            f"  position_x_m: [{report.ee_motion_stats.position_range_m.x.min_value:.4f}, "
            f"{report.ee_motion_stats.position_range_m.x.max_value:.4f}]",
            f"  position_y_m: [{report.ee_motion_stats.position_range_m.y.min_value:.4f}, "
            f"{report.ee_motion_stats.position_range_m.y.max_value:.4f}]",
            f"  position_z_m: [{report.ee_motion_stats.position_range_m.z.min_value:.4f}, "
            f"{report.ee_motion_stats.position_range_m.z.max_value:.4f}]",
            f"  step_delta_norm_min_m: {report.ee_motion_stats.step_delta_norm_min_m:.6f}",
            f"  step_delta_norm_max_m: {report.ee_motion_stats.step_delta_norm_max_m:.6f}",
            f"  step_delta_norm_mean_m: {report.ee_motion_stats.step_delta_norm_mean_m:.6f}",
        ]
    )

    if report.warnings:
        lines.extend(["", "warnings:"])
        lines.extend(f"  - {item}" for item in report.warnings)
    if report.issues:
        lines.extend(["", "issues:"])
        lines.extend(f"  - {item}" for item in report.issues)
    return "\n".join(lines)


def episode_check_report_to_dict(report: EpisodeCheckReport) -> dict[str, Any]:
    """Convert a check report to a JSON-serializable dictionary."""
    return {
        "ok": report.ok,
        "episode_dir": str(report.episode_dir),
        "episode_id": report.episode_id,
        "run_id": report.run_id,
        "status": report.status,
        "step_count": report.step_count,
        "metadata_step_count": report.metadata_step_count,
        "step_count_consistent": report.step_count_consistent,
        "step_index_consistent": report.step_index_consistent,
        "camera_coverage": {
            "total_steps": report.camera_coverage.total_steps,
            "wrist_steps": report.camera_coverage.wrist_steps,
            "external_steps": report.camera_coverage.external_steps,
            "wrist_fraction": report.camera_coverage.wrist_fraction,
            "external_fraction": report.camera_coverage.external_fraction,
            "images_dir_present": report.camera_coverage.images_dir_present,
            "wrist_image_files_found": report.camera_coverage.wrist_image_files_found,
            "external_image_files_found": report.camera_coverage.external_image_files_found,
        },
        "sync_warning_counts": dict(report.sync_warning_counts),
        "sync_warning_step_count": report.sync_warning_step_count,
        "human_action_stats": _velocity_stats_to_dict(report.human_action_stats),
        "executed_action_stats": _velocity_stats_to_dict(report.executed_action_stats),
        "ee_motion_stats": {
            "position_range_m": {
                "x": _axis_range_to_dict(report.ee_motion_stats.position_range_m.x),
                "y": _axis_range_to_dict(report.ee_motion_stats.position_range_m.y),
                "z": _axis_range_to_dict(report.ee_motion_stats.position_range_m.z),
            },
            "step_delta_norm_min_m": report.ee_motion_stats.step_delta_norm_min_m,
            "step_delta_norm_max_m": report.ee_motion_stats.step_delta_norm_max_m,
            "step_delta_norm_mean_m": report.ee_motion_stats.step_delta_norm_mean_m,
        },
        "issues": list(report.issues),
        "warnings": list(report.warnings),
    }


def _axis_range_to_dict(axis: AxisRange) -> dict[str, float]:
    return {"min_value": axis.min_value, "max_value": axis.max_value}


def _velocity_stats_to_dict(stats: VelocityActionStats) -> dict[str, Any]:
    return {
        "step_count": stats.step_count,
        "linear_norm_min_m_s": stats.linear_norm_min_m_s,
        "linear_norm_max_m_s": stats.linear_norm_max_m_s,
        "linear_norm_mean_m_s": stats.linear_norm_mean_m_s,
        "angular_norm_min_rad_s": stats.angular_norm_min_rad_s,
        "angular_norm_max_rad_s": stats.angular_norm_max_rad_s,
        "angular_norm_mean_rad_s": stats.angular_norm_mean_rad_s,
        "deadman_active_steps": stats.deadman_active_steps,
        "deadman_active_fraction": stats.deadman_active_fraction,
    }


def _error_report(
    episode_dir: Path,
    *,
    issues: tuple[str, ...],
    warnings: tuple[str, ...] = (),
) -> EpisodeCheckReport:
    empty_velocity = _empty_velocity_stats()
    empty_range = VectorRange(
        x=AxisRange(0.0, 0.0),
        y=AxisRange(0.0, 0.0),
        z=AxisRange(0.0, 0.0),
    )
    return EpisodeCheckReport(
        episode_dir=episode_dir,
        episode_id="unknown",
        run_id="unknown",
        status="unknown",
        step_count=0,
        metadata_step_count=None,
        step_count_consistent=False,
        step_index_consistent=False,
        camera_coverage=CameraCoverage(
            total_steps=0,
            wrist_steps=0,
            external_steps=0,
            wrist_fraction=0.0,
            external_fraction=0.0,
            images_dir_present=False,
            wrist_image_files_found=None,
            external_image_files_found=None,
        ),
        sync_warning_counts={},
        sync_warning_step_count=0,
        human_action_stats=empty_velocity,
        executed_action_stats=empty_velocity,
        ee_motion_stats=EeMotionStats(
            position_range_m=empty_range,
            step_delta_norm_min_m=0.0,
            step_delta_norm_max_m=0.0,
            step_delta_norm_mean_m=0.0,
        ),
        issues=issues,
        warnings=warnings,
    )


def _load_step_payloads(steps_path: Path, issues: list[str]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    with steps_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                issues.append(f"invalid {STEPS_FILENAME} line {line_number}: {exc}")
                continue
            if not isinstance(payload, dict):
                issues.append(f"invalid {STEPS_FILENAME} line {line_number}: expected JSON object")
                continue
            payloads.append(payload)
    return payloads


def _build_report_from_payloads(
    *,
    episode_dir: Path,
    episode_id: str,
    run_id: str,
    status: str,
    metadata_step_count: int | None,
    step_payloads: list[dict[str, Any]],
    images_dir_present: bool,
    issues: list[str],
    warnings: list[str],
) -> EpisodeCheckReport:
    step_count = len(step_payloads)
    step_count_consistent = metadata_step_count is None or metadata_step_count == step_count
    if metadata_step_count is not None and not step_count_consistent:
        issues.append(
            f"step_count mismatch: metadata={metadata_step_count} {STEPS_FILENAME}={step_count}"
        )

    step_index_consistent = True
    expected_index = 0
    for payload in step_payloads:
        if int(payload.get("step_index", -1)) != expected_index:
            step_index_consistent = False
            issues.append(
                f"step_index sequence broken at expected {expected_index}, "
                f"got {payload.get('step_index')!r}"
            )
            break
        expected_index += 1

    wrist_steps = 0
    external_steps = 0
    wrist_image_files_found = 0 if images_dir_present else None
    external_image_files_found = 0 if images_dir_present else None
    sync_warning_counter: Counter[str] = Counter()
    sync_warning_step_count = 0

    human_linear: list[float] = []
    human_angular: list[float] = []
    human_deadman = 0
    executed_linear: list[float] = []
    executed_angular: list[float] = []
    ee_positions: list[Vector3] = []

    for payload in step_payloads:
        observation = payload.get("observation") or {}
        wrist_payload = observation.get("wrist_camera")
        external_payload = observation.get("external_camera")
        if wrist_payload is not None:
            wrist_steps += 1
            if wrist_image_files_found is not None:
                color_path = wrist_payload.get("color_rgb_path")
                if color_path and (episode_dir / str(color_path)).is_file():
                    wrist_image_files_found += 1
        if external_payload is not None:
            external_steps += 1
            if external_image_files_found is not None:
                color_path = external_payload.get("color_rgb_path")
                if color_path and (episode_dir / str(color_path)).is_file():
                    external_image_files_found += 1

        warnings_for_step = payload.get("sync_warnings") or []
        if warnings_for_step:
            sync_warning_step_count += 1
            for item in warnings_for_step:
                sync_warning_counter[str(item)] += 1

        human = payload.get("human_action") or {}
        executed = payload.get("executed_action") or {}
        human_linear.append(_vector_norm(human.get("linear_velocity_m_s")))
        human_angular.append(_vector_norm(human.get("angular_velocity_rad_s")))
        if bool(human.get("deadman_active")):
            human_deadman += 1
        executed_linear.append(_vector_norm(executed.get("linear_velocity_m_s")))
        executed_angular.append(_vector_norm(executed.get("angular_velocity_rad_s")))
        ee_positions.append(_as_vector3(observation.get("ee_position_m")))

    total_steps = step_count
    wrist_fraction = 0.0 if total_steps == 0 else wrist_steps / total_steps
    external_fraction = 0.0 if total_steps == 0 else external_steps / total_steps

    ee_motion_stats = _compute_ee_motion_stats(ee_positions)
    human_stats = _velocity_stats(
        human_linear,
        human_angular,
        deadman_active_steps=human_deadman,
    )
    executed_stats = _velocity_stats(executed_linear, executed_angular)

    return EpisodeCheckReport(
        episode_dir=episode_dir,
        episode_id=episode_id,
        run_id=run_id,
        status=status,
        step_count=step_count,
        metadata_step_count=metadata_step_count,
        step_count_consistent=step_count_consistent,
        step_index_consistent=step_index_consistent,
        camera_coverage=CameraCoverage(
            total_steps=total_steps,
            wrist_steps=wrist_steps,
            external_steps=external_steps,
            wrist_fraction=wrist_fraction,
            external_fraction=external_fraction,
            images_dir_present=images_dir_present,
            wrist_image_files_found=wrist_image_files_found,
            external_image_files_found=external_image_files_found,
        ),
        sync_warning_counts=dict(sync_warning_counter),
        sync_warning_step_count=sync_warning_step_count,
        human_action_stats=human_stats,
        executed_action_stats=executed_stats,
        ee_motion_stats=ee_motion_stats,
        issues=tuple(issues),
        warnings=tuple(warnings),
    )


def _step_references_images(payload: dict[str, Any]) -> bool:
    observation = payload.get("observation") or {}
    return observation.get("wrist_camera") is not None or observation.get("external_camera") is not None


def _step_to_payload(step: EpisodeStep) -> dict[str, Any]:
    observation = step.observation
    wrist_payload = None
    if observation.wrist_camera is not None:
        wrist_payload = {"color_rgb_path": f"{IMAGES_DIRNAME}/step_{step.step_index:06d}_wrist_color.npy"}
    external_payload = None
    if observation.external_camera is not None:
        external_payload = {
            "color_rgb_path": f"{IMAGES_DIRNAME}/step_{step.step_index:06d}_external_color.npy"
        }
    return {
        "step_index": step.step_index,
        "sync_warnings": list(step.sync_warnings),
        "observation": {
            "ee_position_m": list(observation.ee_position_m),
            "wrist_camera": wrist_payload,
            "external_camera": external_payload,
        },
        "human_action": {
            "linear_velocity_m_s": list(step.human_action.linear_velocity_m_s),
            "angular_velocity_rad_s": list(step.human_action.angular_velocity_rad_s),
            "deadman_active": step.human_action.deadman_active,
        },
        "executed_action": {
            "linear_velocity_m_s": list(step.executed_action.linear_velocity_m_s),
            "angular_velocity_rad_s": list(step.executed_action.angular_velocity_rad_s),
        },
    }


def _vector_norm(values: Any) -> float:
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        return 0.0
    x, y, z = (float(values[0]), float(values[1]), float(values[2]))
    return math.sqrt(x * x + y * y + z * z)


def _as_vector3(values: Any) -> Vector3:
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        return (0.0, 0.0, 0.0)
    return (float(values[0]), float(values[1]), float(values[2]))


def _compute_ee_motion_stats(positions: list[Vector3]) -> EeMotionStats:
    if not positions:
        empty = AxisRange(0.0, 0.0)
        return EeMotionStats(
            position_range_m=VectorRange(x=empty, y=empty, z=empty),
            step_delta_norm_min_m=0.0,
            step_delta_norm_max_m=0.0,
            step_delta_norm_mean_m=0.0,
        )

    xs = [item[0] for item in positions]
    ys = [item[1] for item in positions]
    zs = [item[2] for item in positions]
    deltas: list[float] = []
    for previous, current in zip(positions, positions[1:], strict=False):
        dx = current[0] - previous[0]
        dy = current[1] - previous[1]
        dz = current[2] - previous[2]
        deltas.append(math.sqrt(dx * dx + dy * dy + dz * dz))

    if deltas:
        delta_min = min(deltas)
        delta_max = max(deltas)
        delta_mean = sum(deltas) / len(deltas)
    else:
        delta_min = 0.0
        delta_max = 0.0
        delta_mean = 0.0

    return EeMotionStats(
        position_range_m=VectorRange(
            x=AxisRange(min(xs), max(xs)),
            y=AxisRange(min(ys), max(ys)),
            z=AxisRange(min(zs), max(zs)),
        ),
        step_delta_norm_min_m=delta_min,
        step_delta_norm_max_m=delta_max,
        step_delta_norm_mean_m=delta_mean,
    )


def _velocity_stats(
    linear_norms: list[float],
    angular_norms: list[float],
    *,
    deadman_active_steps: int | None = None,
) -> VelocityActionStats:
    if not linear_norms:
        return _empty_velocity_stats(deadman_active_steps=deadman_active_steps)

    deadman_fraction = None
    if deadman_active_steps is not None:
        deadman_fraction = deadman_active_steps / len(linear_norms)

    return VelocityActionStats(
        step_count=len(linear_norms),
        linear_norm_min_m_s=min(linear_norms),
        linear_norm_max_m_s=max(linear_norms),
        linear_norm_mean_m_s=sum(linear_norms) / len(linear_norms),
        angular_norm_min_rad_s=min(angular_norms),
        angular_norm_max_rad_s=max(angular_norms),
        angular_norm_mean_rad_s=sum(angular_norms) / len(angular_norms),
        deadman_active_steps=deadman_active_steps,
        deadman_active_fraction=deadman_fraction,
    )


def _empty_velocity_stats(*, deadman_active_steps: int | None = None) -> VelocityActionStats:
    return VelocityActionStats(
        step_count=0,
        linear_norm_min_m_s=0.0,
        linear_norm_max_m_s=0.0,
        linear_norm_mean_m_s=0.0,
        angular_norm_min_rad_s=0.0,
        angular_norm_max_rad_s=0.0,
        angular_norm_mean_rad_s=0.0,
        deadman_active_steps=deadman_active_steps,
        deadman_active_fraction=None,
    )


def _format_velocity_stats(stats: VelocityActionStats, *, include_deadman: bool) -> list[str]:
    lines = [
        f"  linear_norm_m_s: min={stats.linear_norm_min_m_s:.6f} "
        f"max={stats.linear_norm_max_m_s:.6f} mean={stats.linear_norm_mean_m_s:.6f}",
        f"  angular_norm_rad_s: min={stats.angular_norm_min_rad_s:.6f} "
        f"max={stats.angular_norm_max_rad_s:.6f} mean={stats.angular_norm_mean_rad_s:.6f}",
    ]
    if include_deadman and stats.deadman_active_steps is not None:
        fraction = 0.0 if stats.deadman_active_fraction is None else stats.deadman_active_fraction
        lines.append(
            f"  deadman_active: {stats.deadman_active_steps}/{stats.step_count} ({fraction:.1%})"
        )
    return lines


__all__ = [
    "AxisRange",
    "CameraCoverage",
    "EpisodeCheckReport",
    "EeMotionStats",
    "VelocityActionStats",
    "VectorRange",
    "check_episode_dir",
    "check_recorded_episode",
    "episode_check_report_to_dict",
    "format_episode_check_report",
]
