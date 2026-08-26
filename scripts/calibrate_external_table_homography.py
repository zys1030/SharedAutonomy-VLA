"""Estimate a table-plane homography H from the fixed external RGB camera.

Does not connect to the arm or enable motion. Lay the 9x12 square board
(8x11 inner corners, 15 mm) flat in the pick zone, aligned with the gripper
at J6 = 0. Space captures; the overlay X (red) / Y (green) axes should match
the finger frame.

Example::

    python scripts/calibrate_external_table_homography.py
    python scripts/calibrate_external_table_homography.py --image capture.png
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from sharedautonomy.control.observation import load_camera_runtime_config
from sharedautonomy.devices.cameras import CameraSession, UvcRgbCamera
from sharedautonomy.devices.uvc_resolve import build_resolved_uvc_opencv_index
from sharedautonomy.perception.table_homography import (
    DEFAULT_GRIPPER_REF,
    DEFAULT_INNER_CORNERS,
    DEFAULT_SQUARE_M,
    DEFAULT_YAML_PATH,
    TableHomography,
    TableHomographyError,
    annotate_chessboard,
    detect_chessboard_corners_with_fallback,
    estimate_table_homography_from_image,
    save_table_homography,
    warp_table_preview,
)

_WINDOW = "external table homography"
_DEFAULT_CAMERA_CONFIG = Path("configs/local/external_rgb.local.yaml")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Calibrate external-camera table homography H from a chessboard "
            "aligned with the gripper at J6 = 0. Camera only; no arm motion."
        )
    )
    parser.add_argument(
        "--image",
        default=None,
        help="Use a saved BGR/RGB still instead of opening the C920",
    )
    parser.add_argument(
        "--camera-config",
        default=None,
        help=f"External camera YAML (default: {_DEFAULT_CAMERA_CONFIG.as_posix()})",
    )
    parser.add_argument("--opencv-index", type=int, default=None, help="Override OpenCV index")
    parser.add_argument(
        "--inner-corners",
        nargs=2,
        type=int,
        default=list(DEFAULT_INNER_CORNERS),
        metavar=("COLS", "ROWS"),
        help="Inner corners (default: 8 11 for a 9x12 square board)",
    )
    parser.add_argument(
        "--square-mm",
        type=float,
        default=DEFAULT_SQUARE_M * 1000.0,
        help="Checkerboard square edge in millimetres (default: 15)",
    )
    parser.add_argument(
        "--no-try-swapped-corners",
        action="store_true",
        help="Do not retry inner_corners with cols/rows swapped on miss",
    )
    parser.add_argument("--flip-x", action="store_true", help="Mirror object X after detection")
    parser.add_argument("--flip-y", action="store_true", help="Mirror object Y after detection")
    parser.add_argument("--swap-axes", action="store_true", help="Swap object X/Y after detection")
    parser.add_argument(
        "--output",
        default=None,
        help=f"YAML path (default: {DEFAULT_YAML_PATH.as_posix()})",
    )
    parser.add_argument(
        "--preview-dir",
        default=None,
        help="Directory for corners/warp PNGs (default: outputs/table_homography/<utc>)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Grab one live frame after warmup, then exit (no GUI loop)",
    )
    parser.add_argument("--no-gui", action="store_true", help="Do not open a preview window")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    _disable_opencv_opencl()
    args = parse_args()
    inner_corners = (int(args.inner_corners[0]), int(args.inner_corners[1]))
    square_m = float(args.square_mm) / 1000.0
    output_path = Path(args.output or DEFAULT_YAML_PATH)
    preview_dir = Path(args.preview_dir) if args.preview_dir else _default_preview_dir()

    if args.image:
        image_bgr = _read_image_bgr(Path(args.image))
        return _calibrate_and_save(
            image_bgr,
            inner_corners=inner_corners,
            square_m=square_m,
            flip_x=bool(args.flip_x),
            flip_y=bool(args.flip_y),
            swap_axes=bool(args.swap_axes),
            try_swapped=not bool(args.no_try_swapped_corners),
            output_path=output_path,
            preview_dir=preview_dir,
            show_gui=not bool(args.no_gui),
        )

    camera = _open_external_camera(
        config_path=Path(args.camera_config) if args.camera_config else _DEFAULT_CAMERA_CONFIG,
        opencv_index=args.opencv_index,
    )
    session = CameraSession(external_camera=camera)
    session.start()
    try:
        if args.once or args.no_gui:
            image_bgr = _read_live_bgr(camera, timeout_s=5.0)
            return _calibrate_and_save(
                image_bgr,
                inner_corners=inner_corners,
                square_m=square_m,
                flip_x=bool(args.flip_x),
                flip_y=bool(args.flip_y),
                swap_axes=bool(args.swap_axes),
                try_swapped=not bool(args.no_try_swapped_corners),
                output_path=output_path,
                preview_dir=preview_dir,
                show_gui=not bool(args.no_gui or args.once),
            )
        return _interactive_loop(
            camera,
            inner_corners=inner_corners,
            square_m=square_m,
            flip_x=bool(args.flip_x),
            flip_y=bool(args.flip_y),
            swap_axes=bool(args.swap_axes),
            try_swapped=not bool(args.no_try_swapped_corners),
            output_path=output_path,
            preview_dir=preview_dir,
        )
    finally:
        session.stop()


def _calibrate_and_save(
    image_bgr: np.ndarray,
    *,
    inner_corners: tuple[int, int],
    square_m: float,
    flip_x: bool,
    flip_y: bool,
    swap_axes: bool,
    try_swapped: bool,
    output_path: Path,
    preview_dir: Path,
    show_gui: bool,
) -> int:
    try:
        model, image_points = estimate_table_homography_from_image(
            image_bgr,
            inner_corners=inner_corners,
            square_m=square_m,
            gripper_ref=DEFAULT_GRIPPER_REF,
            flip_x=flip_x,
            flip_y=flip_y,
            swap_axes=swap_axes,
            try_swapped_corners=try_swapped,
        )
    except TableHomographyError as exc:
        print(f"Calibration failed: {exc}", file=sys.stderr)
        return 1
    _write_outputs(
        image_bgr,
        model,
        image_points,
        output_path=output_path,
        preview_dir=preview_dir,
        requested_inner_corners=inner_corners,
    )
    if show_gui:
        _show_result_windows(image_bgr, model, image_points, wait=True)
    return 0


def _interactive_loop(
    camera: UvcRgbCamera,
    *,
    inner_corners: tuple[int, int],
    square_m: float,
    flip_x: bool,
    flip_y: bool,
    swap_axes: bool,
    try_swapped: bool,
    output_path: Path,
    preview_dir: Path,
) -> int:
    print("Live preview: Space = capture and save H, q = quit.")
    print("Red = table X, green = table Y; they should match the gripper at J6 = 0.")
    if _highgui_available():
        return _interactive_loop_highgui(
            camera,
            inner_corners=inner_corners,
            square_m=square_m,
            flip_x=flip_x,
            flip_y=flip_y,
            swap_axes=swap_axes,
            try_swapped=try_swapped,
            output_path=output_path,
            preview_dir=preview_dir,
        )
    if _tkinter_available():
        print("OpenCV highgui is unavailable (headless wheel); using Tk preview.")
        return _interactive_loop_tk(
            camera,
            inner_corners=inner_corners,
            square_m=square_m,
            flip_x=flip_x,
            flip_y=flip_y,
            swap_axes=swap_axes,
            try_swapped=try_swapped,
            output_path=output_path,
            preview_dir=preview_dir,
        )
    print(
        "No GUI backend (headless OpenCV, no Tk). Enter = capture current frame, q = quit.",
        file=sys.stderr,
    )
    return _interactive_loop_terminal(
        camera,
        inner_corners=inner_corners,
        square_m=square_m,
        flip_x=flip_x,
        flip_y=flip_y,
        swap_axes=swap_axes,
        try_swapped=try_swapped,
        output_path=output_path,
        preview_dir=preview_dir,
    )


def _interactive_loop_highgui(
    camera: UvcRgbCamera,
    *,
    inner_corners: tuple[int, int],
    square_m: float,
    flip_x: bool,
    flip_y: bool,
    swap_axes: bool,
    try_swapped: bool,
    output_path: Path,
    preview_dir: Path,
) -> int:
    import cv2

    saved = False
    while True:
        image_bgr, overlay, _detected = _live_overlay(
            camera, inner_corners=inner_corners, try_swapped=try_swapped
        )
        cv2.imshow(_WINDOW, overlay)
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            break
        if key not in (ord(" "), ord("s"), 13):
            continue
        if _save_capture(
            image_bgr,
            inner_corners=inner_corners,
            square_m=square_m,
            flip_x=flip_x,
            flip_y=flip_y,
            swap_axes=swap_axes,
            try_swapped=try_swapped,
            output_path=output_path,
            preview_dir=preview_dir,
            show_result=True,
            wait_result=False,
        ):
            saved = True
            print("Saved. Press q in the preview window to exit, or Space to recapture.")
    cv2.destroyAllWindows()
    return 0 if saved else 1


def _interactive_loop_tk(
    camera: UvcRgbCamera,
    *,
    inner_corners: tuple[int, int],
    square_m: float,
    flip_x: bool,
    flip_y: bool,
    swap_axes: bool,
    try_swapped: bool,
    output_path: Path,
    preview_dir: Path,
) -> int:
    import tkinter as tk

    root = tk.Tk()
    root.title(_WINDOW)
    image_label = tk.Label(root)
    image_label.pack()
    hint = tk.Label(root, text="Space / s = save H    q = quit    click the window first")
    hint.pack()
    state: dict[str, object] = {"saved": False, "photo": None, "closing": False}

    def on_key(event: object) -> None:
        key = str(getattr(event, "keysym", "")).lower()
        if key in {"q", "escape"}:
            state["closing"] = True
            root.destroy()
            return
        if key not in {"space", "s", "return"}:
            return
        image_bgr, _overlay, _detected = _live_overlay(
            camera, inner_corners=inner_corners, try_swapped=try_swapped
        )
        if _save_capture(
            image_bgr,
            inner_corners=inner_corners,
            square_m=square_m,
            flip_x=flip_x,
            flip_y=flip_y,
            swap_axes=swap_axes,
            try_swapped=try_swapped,
            output_path=output_path,
            preview_dir=preview_dir,
            show_result=True,
            wait_result=False,
        ):
            state["saved"] = True
            hint.configure(text="Saved. Space = recapture, q = quit.")

    def tick() -> None:
        if state["closing"]:
            return
        _image_bgr, overlay, _detected = _live_overlay(
            camera, inner_corners=inner_corners, try_swapped=try_swapped
        )
        photo = _bgr_to_tk_photo(overlay)
        state["photo"] = photo
        image_label.configure(image=photo)
        root.after(50, tick)

    root.bind("<Key>", on_key)
    root.after(50, tick)
    root.mainloop()
    return 0 if bool(state["saved"]) else 1


def _interactive_loop_terminal(
    camera: UvcRgbCamera,
    *,
    inner_corners: tuple[int, int],
    square_m: float,
    flip_x: bool,
    flip_y: bool,
    swap_axes: bool,
    try_swapped: bool,
    output_path: Path,
    preview_dir: Path,
) -> int:
    saved = False
    while True:
        image_bgr, _overlay, detected = _live_overlay(
            camera, inner_corners=inner_corners, try_swapped=try_swapped
        )
        status = "miss" if detected is None else f"{detected[1][0]}x{detected[1][1]} ok"
        command = input(f"board={status}. Enter=save, q=quit: ").strip().lower()
        if command in {"q", "quit"}:
            break
        if _save_capture(
            image_bgr,
            inner_corners=inner_corners,
            square_m=square_m,
            flip_x=flip_x,
            flip_y=flip_y,
            swap_axes=swap_axes,
            try_swapped=try_swapped,
            output_path=output_path,
            preview_dir=preview_dir,
            show_result=False,
            wait_result=False,
        ):
            saved = True
    return 0 if saved else 1


def _live_overlay(
    camera: UvcRgbCamera,
    *,
    inner_corners: tuple[int, int],
    try_swapped: bool,
) -> tuple[np.ndarray, np.ndarray, tuple[np.ndarray, tuple[int, int]] | None]:
    import cv2

    image_bgr = _read_live_bgr(camera, timeout_s=2.0)
    overlay = image_bgr.copy()
    detected = detect_chessboard_corners_with_fallback(
        image_bgr,
        inner_corners,
        try_swapped=try_swapped,
        exhaustive=False,
    )
    status = "board: miss"
    color = (0, 0, 255)
    if detected is not None:
        points, used = detected
        status = f"board: {used[0]}x{used[1]} ok"
        color = (0, 255, 0)
        cv2.drawChessboardCorners(
            overlay,
            used,
            np.asarray(points, dtype=np.float32).reshape(-1, 1, 2),
            True,
        )
    cv2.putText(
        overlay,
        f"{status} | Space=save  q=quit",
        (12, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        color,
        2,
    )
    return image_bgr, overlay, detected


def _save_capture(
    image_bgr: np.ndarray,
    *,
    inner_corners: tuple[int, int],
    square_m: float,
    flip_x: bool,
    flip_y: bool,
    swap_axes: bool,
    try_swapped: bool,
    output_path: Path,
    preview_dir: Path,
    show_result: bool,
    wait_result: bool,
) -> bool:
    try:
        model, image_points = estimate_table_homography_from_image(
            image_bgr,
            inner_corners=inner_corners,
            square_m=square_m,
            gripper_ref=DEFAULT_GRIPPER_REF,
            flip_x=flip_x,
            flip_y=flip_y,
            swap_axes=swap_axes,
            try_swapped_corners=try_swapped,
        )
    except TableHomographyError as exc:
        print(f"Capture failed: {exc}", file=sys.stderr)
        return False
    _write_outputs(
        image_bgr,
        model,
        image_points,
        output_path=output_path,
        preview_dir=preview_dir,
        requested_inner_corners=inner_corners,
    )
    if show_result:
        _show_result_windows(image_bgr, model, image_points, wait=wait_result)
    return True


def _write_outputs(
    image_bgr: np.ndarray,
    model: TableHomography,
    image_points: np.ndarray,
    *,
    output_path: Path,
    preview_dir: Path,
    requested_inner_corners: tuple[int, int],
) -> None:
    save_table_homography(model, output_path)
    preview_dir.mkdir(parents=True, exist_ok=True)
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("opencv-python (cv2) is required to write preview images") from exc

    source_path = preview_dir / "source_bgr.png"
    corners_path = preview_dir / "corners_bgr.png"
    warp_path = preview_dir / "warp_bgr.png"
    cv2.imwrite(str(source_path), image_bgr)
    cv2.imwrite(str(corners_path), annotate_chessboard(image_bgr, image_points, model))
    cv2.imwrite(str(warp_path), warp_table_preview(image_bgr, model))
    rms_mm = model.rms_reproj_m * 1000.0
    print(
        f"Wrote {output_path.as_posix()} | inner_corners={model.inner_corners} "
        f"| rms={rms_mm:.3f} mm | {model.image_width}x{model.image_height}"
    )
    print(f"Preview: {corners_path.as_posix()}  {warp_path.as_posix()}")
    if model.inner_corners != requested_inner_corners:
        print(
            f"Detected swapped pattern {model.inner_corners} "
            f"(requested {requested_inner_corners}). Check X/Y vs the gripper."
        )
    if rms_mm > 1.0:
        print(
            f"WARNING: RMS {rms_mm:.2f} mm is high; board may be warped or not flat.",
            file=sys.stderr,
        )
    print("Check warp_bgr.png: squares should look square, red=X green=Y along J6=0 fingers.")


def _show_result_windows(
    image_bgr: np.ndarray,
    model: TableHomography,
    image_points: np.ndarray,
    *,
    wait: bool,
) -> None:
    corners = annotate_chessboard(image_bgr, image_points, model)
    warped = warp_table_preview(image_bgr, model)
    if _highgui_available():
        import cv2

        cv2.imshow(f"{_WINDOW} corners", corners)
        cv2.imshow(f"{_WINDOW} warp", warped)
        if wait:
            print("Press any key in the preview window to close.")
            cv2.waitKey(0)
            cv2.destroyAllWindows()
        else:
            cv2.waitKey(1)
        return
    if _tk_root_exists() or _tkinter_available():
        _show_result_tk(corners, warped, wait=wait)
        return
    print("GUI unavailable; open the PNG previews written next to the YAML.")


def _show_result_tk(corners_bgr: np.ndarray, warped_bgr: np.ndarray, *, wait: bool) -> None:
    import tkinter as tk

    root = tk.Toplevel() if _tk_root_exists() else tk.Tk()
    root.title(f"{_WINDOW} result")
    photos: list[object] = []
    for title, image_bgr in (("corners", corners_bgr), ("warp", warped_bgr)):
        frame = tk.LabelFrame(root, text=title)
        frame.pack(side=tk.LEFT, padx=6, pady=6)
        photo = _bgr_to_tk_photo(image_bgr)
        photos.append(photo)
        tk.Label(frame, image=photo).pack()
    root._photos = photos  # noqa: SLF001  keep Tk images alive
    tk.Button(root, text="Close", command=root.destroy).pack(pady=6)
    print("Check the result window: warp squares should look square.")
    if wait:
        print("Close the result window to continue.")
        if isinstance(root, tk.Tk):
            root.mainloop()
        else:
            root.wait_window()


_HIGHGUI: bool | None = None
_TKINTER: bool | None = None


def _disable_opencv_opencl() -> None:
    try:
        import cv2
    except ImportError:
        return
    try:
        cv2.ocl.setUseOpenCL(False)
    except Exception:
        return


def _highgui_available() -> bool:
    global _HIGHGUI
    if _HIGHGUI is not None:
        return _HIGHGUI
    try:
        import cv2
    except ImportError:
        _HIGHGUI = False
        return False
    gui_yes = False
    for line in cv2.getBuildInformation().splitlines():
        stripped = line.strip()
        if stripped.endswith("YES") and stripped.startswith(("Win32 UI:", "QT:", "GTK+", "Cocoa:")):
            gui_yes = True
            break
    _HIGHGUI = gui_yes
    return _HIGHGUI


def _tkinter_available() -> bool:
    global _TKINTER
    if _TKINTER is not None:
        return _TKINTER
    try:
        import tkinter as tk
    except ImportError:
        _TKINTER = False
        return False
    try:
        probe = tk.Tk()
        probe.withdraw()
        probe.destroy()
    except tk.TclError:
        _TKINTER = False
        return False
    _TKINTER = True
    return True


def _tk_root_exists() -> bool:
    try:
        import tkinter as tk
    except ImportError:
        return False
    return tk._default_root is not None  # noqa: SLF001


def _bgr_to_tk_photo(image_bgr: np.ndarray) -> object:
    import tkinter as tk

    rgb = np.ascontiguousarray(image_bgr[:, :, ::-1])
    try:
        from PIL import Image, ImageTk

        return ImageTk.PhotoImage(Image.fromarray(rgb))
    except ImportError:
        height, width = rgb.shape[:2]
        header = f"P6\n{width} {height}\n255\n".encode("ascii")
        return tk.PhotoImage(data=header + rgb.tobytes(), format="PPM")


def _open_external_camera(*, config_path: Path, opencv_index: int | None) -> UvcRgbCamera:
    config = load_camera_runtime_config(external_config_path=config_path)
    payload = config.external or {}
    if not payload and not config_path.is_file() and opencv_index is None:
        raise SystemExit(
            f"External camera config not found: {config_path}. "
            "Copy configs/local/external_rgb.example.yaml if present, or pass --opencv-index."
        )
    resolved = opencv_index
    if resolved is None:
        resolved = build_resolved_uvc_opencv_index(
            friendly_name=payload.get("friendly_name"),
            device_name_contains=payload.get("device_name_contains"),
            vendor_id=payload.get("vendor_id"),
            product_id=payload.get("product_id"),
            opencv_index_hint=payload.get("opencv_index_hint"),
            opencv_index=payload.get("opencv_index"),
        )
    return UvcRgbCamera(
        width=int(payload.get("width", 640)),
        height=int(payload.get("height", 480)),
        fps=int(payload.get("fps", 30)),
        opencv_index=resolved,
        friendly_name=payload.get("friendly_name"),
        device_name_contains=payload.get("device_name_contains"),
        vendor_id=payload.get("vendor_id"),
        product_id=payload.get("product_id"),
        opencv_index_hint=payload.get("opencv_index_hint"),
        opencv_backend=payload.get("opencv_backend", "dshow"),
        warmup_frames=int(payload.get("warmup_frames", 60)),
    )


def _read_live_bgr(camera: UvcRgbCamera, *, timeout_s: float) -> np.ndarray:
    deadline = time.perf_counter() + timeout_s
    last_error = "no frame"
    while time.perf_counter() < deadline:
        frame = camera.read_camera(now_monotonic_ns=time.perf_counter_ns())
        if frame is None or frame.color_rgb is None:
            time.sleep(0.02)
            last_error = "camera returned no RGB frame"
            continue
        rgb = np.asarray(frame.color_rgb)
        if rgb.ndim != 3 or rgb.shape[2] != 3:
            last_error = f"unexpected RGB shape {rgb.shape}"
            continue
        return np.ascontiguousarray(rgb[:, :, ::-1])
    raise RuntimeError(f"Timed out reading external RGB ({last_error})")


def _read_image_bgr(path: Path) -> np.ndarray:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("opencv-python (cv2) is required to load --image") from exc
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"Failed to read image: {path}")
    return image


def _default_preview_dir() -> Path:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path("outputs") / "table_homography" / stamp


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit("Interrupted.") from None
