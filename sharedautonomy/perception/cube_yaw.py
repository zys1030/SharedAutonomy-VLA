"""Red-cube yaw from the top-face square, relative to the J6=0 gripper frame.

An oblique view of a cube is usually a hexagon (three faces). Fitting a
rectangle to the whole red blob measures the silhouette, not yaw. This
module reconstructs the visible faces from the hull, then keeps the face
that is most square after the table homography — that is the top.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from sharedautonomy.perception.table_homography import TableHomography

DEFAULT_MIN_AREA_PX = 250
DEFAULT_HSV_S_MIN = 80
DEFAULT_HSV_V_MIN = 50
DEFAULT_HSV_H_LOW = 10
DEFAULT_HSV_H_HIGH = 170


@dataclass(frozen=True, slots=True)
class CubeYawEstimate:
    """Top-face orientation after mapping through the table homography."""

    yaw_table_deg: float
    yaw_wrap90_deg: float
    delta_j6_deg: float
    center_uv: tuple[float, float]
    box_uv: np.ndarray
    area_px: float
    other_faces_uv: tuple[np.ndarray, ...] = ()


class StartYawError(RuntimeError):
    """Raised when a required start-of-episode cube yaw cannot be resolved."""


def wrap_square_yaw_deg(angle_deg: float) -> float:
    """Map a planar yaw onto ``(-45, 45]`` using 90-degree cube symmetry.

    45 stays +45, not -45. A cube on the diagonal is reported as 45, not 0.
    """
    folded = ((float(angle_deg) + 45.0) % 90.0) - 45.0
    if folded <= -45.0:
        return 45.0
    return float(folded)


def wrap90_j6_error_deg(target_wrap90_deg: float, j6_now_deg: float) -> float:
    """Wrap90 difference ``target - current`` in the table / J6=0 frame."""
    return wrap_square_yaw_deg(float(target_wrap90_deg) - float(j6_now_deg))


def _rpy_xyz_rotation_matrix(rpy_rad: Sequence[float]) -> np.ndarray:
    """XYZ-intrinsic RPY, same as ``rpy_rad_to_quaternion_xyzw``: ``R = Rz Ry Rx``."""
    roll, pitch, yaw = (float(value) for value in rpy_rad)
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    rotation_z = np.array([[cy, -sy, 0.0], [sy, cy, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)
    rotation_y = np.array([[cp, 0.0, sp], [0.0, 1.0, 0.0], [-sp, 0.0, cp]], dtype=np.float64)
    rotation_x = np.array([[1.0, 0.0, 0.0], [0.0, cr, -sr], [0.0, sr, cr]], dtype=np.float64)
    return rotation_z @ rotation_y @ rotation_x


def gripper_jaw_table_heading_deg(ee_rpy_rad: Sequence[float]) -> float:
    """Table-plane heading of a gripper jaw axis, from FK RPY (degrees).

    Ready-pose FK is about ``(pi, 0, 0)``: tool Z down, tool X in the table plane.
    Positive J6 then decreases this heading. Assist treats
    ``heading_lock - heading_now`` as progress toward a positive cube wrap90.
    """
    rotation = _rpy_xyz_rotation_matrix(ee_rpy_rad)
    approach = int(np.argmax(np.abs(rotation[2, :])))
    best_index = None
    best_xy = -1.0
    for index in range(3):
        if index == approach:
            continue
        xy_norm = float(math.hypot(rotation[0, index], rotation[1, index]))
        if xy_norm > best_xy:
            best_xy = xy_norm
            best_index = index
    if best_index is None or best_xy < 1e-9:
        return 0.0
    return float(math.degrees(math.atan2(rotation[1, best_index], rotation[0, best_index])))


def cube_gripper_wrap90_error_deg(
    cube_wrap90_deg: float,
    gripper_heading_now_deg: float,
    gripper_heading_lock_deg: float,
) -> float:
    """Remaining wrap90 angle between the locked cube and the live gripper heading.

    ``gripper_heading_*`` come from FK, not from the J6 encoder and not from a
    commanded-offset integrator. IK may move J6 to hold world yaw; that does
    not count as cube alignment unless the table-plane jaw heading actually
    changes.
    """
    progress_deg = wrap_square_yaw_deg(float(gripper_heading_lock_deg) - float(gripper_heading_now_deg))
    return wrap_square_yaw_deg(float(cube_wrap90_deg) - progress_deg)


def should_measure_start_yaw(
    *,
    cli_measure_start_yaw: bool | None,
    recording: bool,
    allow_tool_yaw: bool,
    enable_yaw_assist: bool,
) -> bool:
    """Whether to sample third-person wrap90 at episode start.

    Rotated Manual recording and yaw-assist both need an opening cube yaw.
    Assist overlays the locked cube wrap90 onto pose-hold IK so descent cannot
    retarget from a gripper-occluded blob. Stop when FK jaw heading matches.
    """
    if cli_measure_start_yaw is not None:
        return bool(cli_measure_start_yaw)
    if bool(enable_yaw_assist):
        return True
    return bool(recording) and bool(allow_tool_yaw)


def measure_red_cube_yaw(
    image_bgr: np.ndarray,
    homography: TableHomography,
    *,
    j6_now_deg: float = 0.0,
    min_area_px: float = DEFAULT_MIN_AREA_PX,
    hsv_s_min: int = DEFAULT_HSV_S_MIN,
    hsv_v_min: int = DEFAULT_HSV_V_MIN,
    hsv_h_low: int = DEFAULT_HSV_H_LOW,
    hsv_h_high: int = DEFAULT_HSV_H_HIGH,
) -> CubeYawEstimate | None:
    """Return cube-vs-J6 yaw from the top face, or ``None`` if not found."""
    contour = _largest_red_contour(
        image_bgr,
        min_area_px=min_area_px,
        hsv_s_min=hsv_s_min,
        hsv_v_min=hsv_v_min,
        hsv_h_low=hsv_h_low,
        hsv_h_high=hsv_h_high,
    )
    if contour is None:
        return None
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("opencv-python (cv2) is required to measure cube yaw") from exc

    hull = _simplify_cube_hull(contour)
    if hull is None or len(hull) < 4:
        return None
    faces = _visible_cube_faces(hull)
    if not faces:
        return None
    top_face, other_faces = _select_top_face(faces, homography)
    table_quad = homography.image_to_table_xy_m(top_face)
    yaw_table_deg = _quad_yaw_deg(table_quad)
    if yaw_table_deg is None:
        return None
    yaw_wrap90_deg = wrap_square_yaw_deg(yaw_table_deg)
    delta_j6_deg = wrap_square_yaw_deg(yaw_wrap90_deg - float(j6_now_deg))
    center = top_face.mean(axis=0)
    return CubeYawEstimate(
        yaw_table_deg=yaw_table_deg,
        yaw_wrap90_deg=yaw_wrap90_deg,
        delta_j6_deg=delta_j6_deg,
        center_uv=(float(center[0]), float(center[1])),
        box_uv=top_face,
        area_px=float(cv2.contourArea(contour)),
        other_faces_uv=tuple(other_faces),
    )


def measure_start_yaw_from_rgb(
    color_rgb: np.ndarray,
    homography: TableHomography,
    *,
    j6_now_deg: float = 0.0,
) -> CubeYawEstimate | None:
    """Measure cube yaw from an RGB camera frame (HWC uint8)."""
    image_bgr = np.ascontiguousarray(np.asarray(color_rgb)[:, :, ::-1])
    return measure_red_cube_yaw(image_bgr, homography, j6_now_deg=j6_now_deg)


def resolve_start_yaw_bin(
    *,
    cli_yaw_bin_deg: float | None,
    estimate: CubeYawEstimate | None,
    required: bool,
) -> tuple[float | None, dict[str, Any]]:
    """Choose the ledger yaw bin: CLI override, else measured wrap90.

    ``yaw_bin_deg`` is the cube's opening wrap90 in the J6=0 table frame,
    not the gripper J6 at close.
    """
    extras: dict[str, Any] = {}
    measured = None if estimate is None else float(estimate.yaw_wrap90_deg)
    if estimate is not None:
        extras["start_yaw_wrap90_deg"] = float(estimate.yaw_wrap90_deg)
        extras["start_delta_j6_deg"] = float(estimate.delta_j6_deg)
        extras["start_yaw_table_deg"] = float(estimate.yaw_table_deg)
        extras["start_yaw_source"] = "measured"
    if cli_yaw_bin_deg is not None:
        extras["cli_yaw_bin_deg"] = float(cli_yaw_bin_deg)
        extras["start_yaw_source"] = "cli" if measured is None else "cli_override"
        return float(cli_yaw_bin_deg), extras
    if measured is not None:
        return measured, extras
    if required:
        raise StartYawError(
            "start cube yaw is required for rotated-block recording, but the "
            "external camera did not find a top face. Check the cube is visible "
            "in the third-person view, or pass --yaw-bin from "
            "measure_cube_gripper_yaw.py."
        )
    return None, extras


def annotate_cube_yaw(
    image_bgr: np.ndarray,
    estimate: CubeYawEstimate | None,
    *,
    j6_now_deg: float = 0.0,
) -> np.ndarray:
    """Draw side faces, the top-face square, and wrap90 delta."""
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("opencv-python (cv2) is required to annotate cube yaw") from exc

    canvas = np.ascontiguousarray(image_bgr.copy())
    if estimate is None:
        cv2.putText(
            canvas,
            "no red cube",
            (12, 32),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 0, 255),
            2,
        )
        return canvas
    for face in estimate.other_faces_uv:
        cv2.polylines(canvas, [np.round(face).astype(np.int32)], True, (180, 180, 180), 1)
    top = np.round(estimate.box_uv).astype(np.int32)
    cv2.polylines(canvas, [top], True, (0, 255, 0), 2)
    start = tuple(int(v) for v in np.round(estimate.box_uv[0]))
    end = tuple(int(v) for v in np.round(estimate.box_uv[1]))
    cv2.arrowedLine(canvas, start, end, (0, 255, 255), 2, tipLength=0.2)
    center = (int(round(estimate.center_uv[0])), int(round(estimate.center_uv[1])))
    cv2.circle(canvas, center, 4, (255, 0, 0), -1)
    cv2.putText(
        canvas,
        f"top dJ6 {estimate.delta_j6_deg:+.1f} deg  (wrap90, J6={j6_now_deg:.1f})",
        (12, 32),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (0, 255, 0),
        2,
    )
    return canvas


def _largest_red_contour(
    image_bgr: np.ndarray,
    *,
    min_area_px: float,
    hsv_s_min: int,
    hsv_v_min: int,
    hsv_h_low: int,
    hsv_h_high: int,
) -> np.ndarray | None:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("opencv-python (cv2) is required to segment a red cube") from exc

    if image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
        raise ValueError("image_bgr must have shape (H, W, 3)")
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    lower = cv2.inRange(
        hsv,
        (0, int(hsv_s_min), int(hsv_v_min)),
        (int(hsv_h_low), 255, 255),
    )
    upper = cv2.inRange(
        hsv,
        (int(hsv_h_high), int(hsv_s_min), int(hsv_v_min)),
        (180, 255, 255),
    )
    mask = cv2.bitwise_or(lower, upper)
    kernel = np.ones((5, 5), dtype=np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _hierarchy = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    if float(cv2.contourArea(contour)) < float(min_area_px):
        return None
    return contour


def _simplify_cube_hull(contour: np.ndarray) -> np.ndarray | None:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("opencv-python (cv2) is required to parse a cube hull") from exc

    hull = cv2.convexHull(contour)
    peri = float(cv2.arcLength(hull, True))
    if peri <= 1.0:
        return None
    best: np.ndarray | None = None
    best_n = 99
    for eps_frac in (0.01, 0.015, 0.02, 0.03, 0.04, 0.06, 0.08):
        approx = cv2.approxPolyDP(hull, eps_frac * peri, True).reshape(-1, 2).astype(np.float64)
        n = int(approx.shape[0])
        if n in {4, 6}:
            return approx
        if 4 <= n <= 8 and n < best_n:
            best = approx
            best_n = n
    return best


def _visible_cube_faces(hull_uv: np.ndarray) -> list[np.ndarray]:
    """Return candidate face quads in image pixels, cyclic vertex order."""
    points = np.asarray(hull_uv, dtype=np.float64)
    n = int(points.shape[0])
    if n == 4:
        return [points]
    if n == 6:
        pairing = _best_hexagon_pairing(points)
        return pairing if pairing else [points]
    if n == 5:
        dropped = _drop_shortest_edge(points)
        if dropped is not None and len(dropped) == 4:
            return [dropped]
    return [points]


def _best_hexagon_pairing(hull_uv: np.ndarray) -> list[np.ndarray] | None:
    best_faces: list[np.ndarray] | None = None
    best_score = float("inf")
    for offset in (0, 1):
        t_estimates = []
        groups = []
        for k in range(3):
            i = (offset + 2 * k) % 6
            a = hull_uv[i]
            b = hull_uv[(i + 1) % 6]
            c = hull_uv[(i + 2) % 6]
            t_estimates.append(a + c - b)
            groups.append((a, b, c))
        t_stack = np.stack(t_estimates, axis=0)
        t_mean = t_stack.mean(axis=0)
        t_spread = float(np.mean(np.linalg.norm(t_stack - t_mean, axis=1)))
        peri = float(np.sum(np.linalg.norm(np.roll(hull_uv, -1, axis=0) - hull_uv, axis=1)))
        if peri < 1.0 or t_spread > 0.15 * peri:
            continue
        faces = [np.stack([a, b, c, t_mean], axis=0) for a, b, c in groups]
        para = 0.0
        for face in faces:
            para += _parallelogram_error(face)
        score = t_spread / peri + para
        if score < best_score:
            best_score = score
            best_faces = faces
    return best_faces


def _select_top_face(
    faces: list[np.ndarray],
    homography: TableHomography,
) -> tuple[np.ndarray, list[np.ndarray]]:
    if len(faces) == 1:
        return faces[0], []
    scores = [_square_error(homography.image_to_table_xy_m(face)) for face in faces]
    top_index = int(np.argmin(scores))
    top = faces[top_index]
    others = [face for i, face in enumerate(faces) if i != top_index]
    return top, others


def _quad_yaw_deg(table_xy: np.ndarray) -> float | None:
    """Mean side direction of a table-plane quad, period 90° (not a wrap90 median).

    Adjacent sides differ by 90°. Wrapping each to ±45 then taking the median
    splits +45 / -45 on a diamond and collapses to 0.
    """
    points = np.asarray(table_xy, dtype=np.float64)
    if points.shape[0] < 2:
        return None
    raw: list[float] = []
    for i in range(points.shape[0]):
        edge = points[(i + 1) % points.shape[0]] - points[i]
        if float(np.linalg.norm(edge)) < 1e-6:
            continue
        raw.append(float(np.degrees(np.arctan2(edge[1], edge[0]))))
    if not raw:
        return None
    radians = np.deg2rad(np.asarray(raw, dtype=np.float64))
    z = np.mean(np.exp(1j * 4.0 * radians))
    if float(np.abs(z)) < 1e-12:
        return 0.0
    return wrap_square_yaw_deg(float(np.rad2deg(np.angle(z)) / 4.0))


def _square_error(table_xy: np.ndarray) -> float:
    points = np.asarray(table_xy, dtype=np.float64)
    n = int(points.shape[0])
    if n < 4:
        return float("inf")
    lengths = []
    dots = []
    for i in range(n):
        edge = points[(i + 1) % n] - points[i]
        length = float(np.linalg.norm(edge))
        if length < 1e-9:
            return float("inf")
        lengths.append(length)
        nxt = points[(i + 2) % n] - points[(i + 1) % n]
        nxt_n = float(np.linalg.norm(nxt))
        if nxt_n < 1e-9:
            return float("inf")
        dots.append(abs(float(np.dot(edge, nxt) / (length * nxt_n))))
    mean_len = float(np.mean(lengths))
    length_cv = float(np.std(lengths) / mean_len) if mean_len > 0 else float("inf")
    return length_cv + float(np.mean(dots))


def _parallelogram_error(face_uv: np.ndarray) -> float:
    a, b, c, t = np.asarray(face_uv, dtype=np.float64)
    residual = a + c - b - t
    scale = float(np.linalg.norm(c - a)) + 1e-6
    return float(np.linalg.norm(residual) / scale)


def _drop_shortest_edge(hull_uv: np.ndarray) -> np.ndarray | None:
    points = np.asarray(hull_uv, dtype=np.float64)
    n = int(points.shape[0])
    if n < 5:
        return None
    lengths = [float(np.linalg.norm(points[(i + 1) % n] - points[i])) for i in range(n)]
    drop = int(np.argmin(lengths))
    keep = [i for i in range(n) if i != (drop + 1) % n]
    return points[keep]
