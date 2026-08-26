"""Table-plane homography for the fixed external RGB camera.

``H`` maps undistorted pixel ``[u, v, 1]`` to table meters ``[X, Y, w]``.
The checkerboard X/Y axes should be aligned with the gripper at J6 = 0 so
cube yaw in this frame is already relative to that reference pose.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

DEFAULT_INNER_CORNERS = (8, 11)
DEFAULT_SQUARE_M = 0.015
DEFAULT_GRIPPER_REF = "j6_zero"
DEFAULT_YAML_PATH = Path("configs/local/external_table_homography.local.yaml")


class TableHomographyError(ValueError):
    """Invalid table-homography payload or failed chessboard estimate."""


@dataclass(frozen=True, slots=True)
class TableHomography:
    """Metric mapping from external-camera pixels onto the table plane."""

    H: np.ndarray
    image_width: int
    image_height: int
    inner_corners: tuple[int, int]
    square_m: float
    gripper_ref: str = DEFAULT_GRIPPER_REF
    rms_reproj_m: float = 0.0
    captured_utc: str | None = None
    flip_x: bool = False
    flip_y: bool = False
    swap_axes: bool = False
    valid: bool = True

    def image_to_table_xy_m(self, uv: np.ndarray) -> np.ndarray:
        """Map pixel coordinates ``(..., 2)`` to table ``(X_m, Y_m)``."""
        return apply_homography(self.H, uv)

    def table_xy_m_to_image(self, xy_m: np.ndarray) -> np.ndarray:
        """Map table ``(X_m, Y_m)`` back to pixel coordinates."""
        return apply_homography(np.linalg.inv(self.H), xy_m)


def apply_homography(H: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Apply a 3x3 homography to 2D points. ``points`` is ``(..., 2)``."""
    array = np.asarray(points, dtype=np.float64)
    if array.size == 0:
        return np.zeros((0, 2), dtype=np.float64)
    if array.shape[-1] != 2:
        raise TableHomographyError("points must have shape (..., 2)")
    leading = array.shape[:-1]
    flat = array.reshape(-1, 2)
    homogeneous = np.concatenate([flat, np.ones((flat.shape[0], 1))], axis=1)
    mapped = homogeneous @ np.asarray(H, dtype=np.float64).T
    weight = mapped[:, 2:3]
    if np.any(np.abs(weight) < 1e-12):
        raise TableHomographyError("homography produced a point at infinity")
    return (mapped[:, :2] / weight).reshape(*leading, 2)


def chessboard_object_points_m(
    inner_corners: tuple[int, int],
    square_m: float,
    *,
    flip_x: bool = False,
    flip_y: bool = False,
    swap_axes: bool = False,
) -> np.ndarray:
    """Planar object points in meters, OpenCV row-major inner-corner order."""
    cols, rows = int(inner_corners[0]), int(inner_corners[1])
    if cols < 2 or rows < 2:
        raise TableHomographyError("inner_corners must be at least 2x2")
    if square_m <= 0.0:
        raise TableHomographyError("square_m must be positive")
    grid = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2).astype(np.float64)
    if flip_x:
        grid[:, 0] = float(cols - 1) - grid[:, 0]
    if flip_y:
        grid[:, 1] = float(rows - 1) - grid[:, 1]
    if swap_axes:
        grid = grid[:, ::-1].copy()
    return grid * float(square_m)


def compute_table_homography(
    image_points_uv: np.ndarray,
    object_points_xy_m: np.ndarray,
) -> tuple[np.ndarray, float]:
    """Estimate ``H`` (pixel → table meters) and RMS reprojection in meters."""
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("opencv-python (cv2) is required to estimate a table homography") from exc

    image = np.asarray(image_points_uv, dtype=np.float64).reshape(-1, 2)
    table = np.asarray(object_points_xy_m, dtype=np.float64).reshape(-1, 2)
    if image.shape != table.shape or image.shape[0] < 4:
        raise TableHomographyError("need at least 4 matched image/table points")
    homography, _mask = cv2.findHomography(image, table, method=0)
    if homography is None:
        raise TableHomographyError("findHomography failed")
    mapped = apply_homography(homography, image)
    rms_reproj_m = float(np.sqrt(np.mean(np.sum((mapped - table) ** 2, axis=1))))
    return np.asarray(homography, dtype=np.float64), rms_reproj_m


def detect_chessboard_corners(
    image_bgr: np.ndarray,
    inner_corners: tuple[int, int],
    *,
    exhaustive: bool = False,
) -> np.ndarray | None:
    """Return ``(N, 2)`` inner-corner pixels, or ``None`` if not found."""
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("opencv-python (cv2) is required to detect a chessboard") from exc

    if image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
        raise TableHomographyError("image_bgr must have shape (H, W, 3)")
    cols, rows = int(inner_corners[0]), int(inner_corners[1])
    pattern = (cols, rows)
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    corners = _detect_chessboard_sb(cv2, gray, pattern, exhaustive=exhaustive)
    if corners is None:
        flags = cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
        if not exhaustive:
            flags |= cv2.CALIB_CB_FAST_CHECK
        found, corners = cv2.findChessboardCorners(gray, pattern, flags)
        if not found or corners is None:
            return None
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 1e-3)
        cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
    return np.asarray(corners, dtype=np.float64).reshape(-1, 2)


def detect_chessboard_corners_with_fallback(
    image_bgr: np.ndarray,
    inner_corners: tuple[int, int],
    *,
    try_swapped: bool = True,
    exhaustive: bool = False,
) -> tuple[np.ndarray, tuple[int, int]] | None:
    """Detect ``inner_corners``, optionally retrying the swapped pattern size."""
    detected = detect_chessboard_corners(image_bgr, inner_corners, exhaustive=exhaustive)
    if detected is not None:
        return detected, (int(inner_corners[0]), int(inner_corners[1]))
    if not try_swapped:
        return None
    swapped = (int(inner_corners[1]), int(inner_corners[0]))
    if swapped == (int(inner_corners[0]), int(inner_corners[1])):
        return None
    detected = detect_chessboard_corners(image_bgr, swapped, exhaustive=exhaustive)
    if detected is None:
        return None
    return detected, swapped


def estimate_table_homography_from_image(
    image_bgr: np.ndarray,
    *,
    inner_corners: tuple[int, int] = DEFAULT_INNER_CORNERS,
    square_m: float = DEFAULT_SQUARE_M,
    gripper_ref: str = DEFAULT_GRIPPER_REF,
    flip_x: bool = False,
    flip_y: bool = False,
    swap_axes: bool = False,
    try_swapped_corners: bool = True,
) -> tuple[TableHomography, np.ndarray]:
    """Detect the board in ``image_bgr`` and return ``(model, image_points)``."""
    result = detect_chessboard_corners_with_fallback(
        image_bgr,
        inner_corners,
        try_swapped=try_swapped_corners,
        exhaustive=True,
    )
    if result is None:
        raise TableHomographyError(
            f"chessboard {inner_corners[0]}x{inner_corners[1]} inner corners not found"
        )
    image_points, used_corners = result
    object_points = chessboard_object_points_m(
        used_corners,
        square_m,
        flip_x=flip_x,
        flip_y=flip_y,
        swap_axes=swap_axes,
    )
    homography, rms_reproj_m = compute_table_homography(image_points, object_points)
    height, width = image_bgr.shape[:2]
    model = TableHomography(
        H=homography,
        image_width=int(width),
        image_height=int(height),
        inner_corners=used_corners,
        square_m=float(square_m),
        gripper_ref=str(gripper_ref),
        rms_reproj_m=rms_reproj_m,
        captured_utc=datetime.now(tz=UTC).replace(microsecond=0).isoformat(),
        flip_x=bool(flip_x),
        flip_y=bool(flip_y),
        swap_axes=bool(swap_axes),
        valid=True,
    )
    return model, image_points


def table_homography_from_mapping(payload: dict[str, Any]) -> TableHomography:
    block = payload.get("table_homography", payload)
    if not isinstance(block, dict):
        raise TableHomographyError("YAML must contain a table_homography mapping")
    if block.get("valid") is False:
        raise TableHomographyError("table_homography.valid is false; run the calibrate script")
    matrix = np.asarray(block.get("H"), dtype=np.float64)
    if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
        raise TableHomographyError("H must be a finite 3x3 matrix")
    corners_raw = block.get("inner_corners", list(DEFAULT_INNER_CORNERS))
    if not isinstance(corners_raw, (list, tuple)) or len(corners_raw) != 2:
        raise TableHomographyError("inner_corners must be [cols, rows]")
    return TableHomography(
        H=matrix,
        image_width=int(block["image_width"]),
        image_height=int(block["image_height"]),
        inner_corners=(int(corners_raw[0]), int(corners_raw[1])),
        square_m=float(block.get("square_m", DEFAULT_SQUARE_M)),
        gripper_ref=str(block.get("gripper_ref", DEFAULT_GRIPPER_REF)),
        rms_reproj_m=float(block.get("rms_reproj_m", 0.0)),
        captured_utc=block.get("captured_utc"),
        flip_x=bool(block.get("flip_x", False)),
        flip_y=bool(block.get("flip_y", False)),
        swap_axes=bool(block.get("swap_axes", False)),
        valid=bool(block.get("valid", True)),
    )


def load_table_homography(path: str | Path) -> TableHomography:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load table homography YAML") from exc
    yaml_path = Path(path)
    with yaml_path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise TableHomographyError(f"{yaml_path} must contain a mapping")
    return table_homography_from_mapping(payload)


def table_homography_to_mapping(model: TableHomography) -> dict[str, Any]:
    return {
        "table_homography": {
            "valid": bool(model.valid),
            "image_width": int(model.image_width),
            "image_height": int(model.image_height),
            "inner_corners": [int(model.inner_corners[0]), int(model.inner_corners[1])],
            "square_m": float(model.square_m),
            "gripper_ref": str(model.gripper_ref),
            "flip_x": bool(model.flip_x),
            "flip_y": bool(model.flip_y),
            "swap_axes": bool(model.swap_axes),
            "rms_reproj_m": float(model.rms_reproj_m),
            "captured_utc": model.captured_utc,
            "H": np.asarray(model.H, dtype=np.float64).tolist(),
        }
    }


def save_table_homography(model: TableHomography, path: str | Path) -> None:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to save table homography YAML") from exc
    yaml_path = Path(path)
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    payload = table_homography_to_mapping(model)
    with yaml_path.open("w", encoding="utf-8") as handle:
        handle.write(
            "# Pixel [u, v, 1] -> table meters [X, Y, w]. Board axes aligned with gripper at J6 = 0.\n"
        )
        yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=True)


def annotate_chessboard(
    image_bgr: np.ndarray,
    image_points_uv: np.ndarray,
    model: TableHomography,
) -> np.ndarray:
    """Overlay corners plus table X (red) / Y (green) axes for visual checks."""
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("opencv-python (cv2) is required to annotate a chessboard") from exc

    canvas = np.ascontiguousarray(image_bgr.copy())
    corners = np.asarray(image_points_uv, dtype=np.float32).reshape(-1, 1, 2)
    cv2.drawChessboardCorners(canvas, model.inner_corners, corners, True)
    origin = np.array([0.0, 0.0], dtype=np.float64)
    axis_len_m = 3.0 * float(model.square_m)
    x_end = np.array([axis_len_m, 0.0], dtype=np.float64)
    y_end = np.array([0.0, axis_len_m], dtype=np.float64)
    origin_uv, x_uv, y_uv = model.table_xy_m_to_image(np.stack([origin, x_end, y_end], axis=0))
    origin_pt = _as_pixel(origin_uv)
    cv2.arrowedLine(canvas, origin_pt, _as_pixel(x_uv), (0, 0, 255), 2, tipLength=0.15)
    cv2.arrowedLine(canvas, origin_pt, _as_pixel(y_uv), (0, 255, 0), 2, tipLength=0.15)
    cv2.circle(canvas, origin_pt, 5, (255, 255, 0), -1)
    cv2.putText(canvas, "X", _as_pixel(x_uv), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    cv2.putText(canvas, "Y", _as_pixel(y_uv), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
    first = _as_pixel(image_points_uv[0])
    cv2.putText(canvas, "0", (first[0] + 6, first[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    return canvas


def warp_table_preview(
    image_bgr: np.ndarray,
    model: TableHomography,
    *,
    px_per_m: float = 4000.0,
    margin_m: float = 0.04,
) -> np.ndarray:
    """Warp the camera image into a metric top-down view of the board plane."""
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("opencv-python (cv2) is required to warp a table preview") from exc

    cols, rows = model.inner_corners
    board_x_m = float(cols - 1) * float(model.square_m)
    board_y_m = float(rows - 1) * float(model.square_m)
    x_min = -float(margin_m)
    y_min = -float(margin_m)
    x_max = board_x_m + float(margin_m)
    y_max = board_y_m + float(margin_m)
    preview_w = max(32, int(round((x_max - x_min) * px_per_m)))
    preview_h = max(32, int(round((y_max - y_min) * px_per_m)))
    scale = np.array(
        [
            [px_per_m, 0.0, -x_min * px_per_m],
            [0.0, px_per_m, -y_min * px_per_m],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    warp = scale @ model.H
    warped = cv2.warpPerspective(image_bgr, warp, (preview_w, preview_h))
    origin = (int(round((0.0 - x_min) * px_per_m)), int(round((0.0 - y_min) * px_per_m)))
    x_end = (int(round((3.0 * model.square_m - x_min) * px_per_m)), origin[1])
    y_end = (origin[0], int(round((3.0 * model.square_m - y_min) * px_per_m)))
    cv2.arrowedLine(warped, origin, x_end, (0, 0, 255), 2, tipLength=0.12)
    cv2.arrowedLine(warped, origin, y_end, (0, 255, 0), 2, tipLength=0.12)
    return warped


def _detect_chessboard_sb(
    cv2: Any,
    gray: np.ndarray,
    pattern: tuple[int, int],
    *,
    exhaustive: bool,
) -> np.ndarray | None:
    finder = getattr(cv2, "findChessboardCornersSB", None)
    if finder is None:
        return None
    flags = 0
    if exhaustive:
        flags |= int(getattr(cv2, "CALIB_CB_EXHAUSTIVE", 0))
        flags |= int(getattr(cv2, "CALIB_CB_ACCURACY", 0))
    try:
        found, corners = finder(gray, pattern, flags)
    except cv2.error:
        return None
    if not found or corners is None:
        return None
    return np.asarray(corners, dtype=np.float64).reshape(-1, 2)


def _as_pixel(uv: np.ndarray) -> tuple[int, int]:
    return int(round(float(uv[0]))), int(round(float(uv[1])))
