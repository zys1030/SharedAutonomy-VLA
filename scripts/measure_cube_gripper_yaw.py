"""Live-measure red-cube yaw relative to the gripper at J6 = 0.

Uses the table homography from ``calibrate_external_table_homography.py``.
Does not move the arm. If J6 has left zero, pass ``--j6-now-deg``.

Example::

    python scripts/measure_cube_gripper_yaw.py
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import numpy as np
from sharedautonomy.control.observation import load_camera_runtime_config
from sharedautonomy.devices.cameras import CameraSession, UvcRgbCamera
from sharedautonomy.devices.uvc_resolve import build_resolved_uvc_opencv_index
from sharedautonomy.perception.cube_yaw import annotate_cube_yaw, measure_red_cube_yaw
from sharedautonomy.perception.table_homography import DEFAULT_YAML_PATH, load_table_homography

_WINDOW = "cube vs gripper yaw"
_DEFAULT_CAMERA_CONFIG = Path("configs/local/external_rgb.local.yaml")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Measure red-cube yaw vs the J6=0 gripper using the external "
            "table homography. Camera only; no arm motion."
        )
    )
    parser.add_argument(
        "--homography",
        default=None,
        help=f"Table homography YAML (default: {DEFAULT_YAML_PATH.as_posix()})",
    )
    parser.add_argument("--image", default=None, help="Measure a saved still instead of live C920")
    parser.add_argument(
        "--camera-config",
        default=None,
        help=f"External camera YAML (default: {_DEFAULT_CAMERA_CONFIG.as_posix()})",
    )
    parser.add_argument("--opencv-index", type=int, default=None, help="Override OpenCV index")
    parser.add_argument(
        "--j6-now-deg",
        type=float,
        default=0.0,
        help="Current J6 in degrees if it is not still 0 (default: 0)",
    )
    parser.add_argument("--once", action="store_true", help="Measure one frame and exit")
    parser.add_argument("--no-gui", action="store_true", help="Do not open a preview window")
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    _disable_opencv_opencl()
    args = parse_args()
    homography = load_table_homography(Path(args.homography or DEFAULT_YAML_PATH))
    j6_now_deg = float(args.j6_now_deg)

    if args.image:
        image_bgr = _read_image_bgr(Path(args.image))
        estimate = measure_red_cube_yaw(image_bgr, homography, j6_now_deg=j6_now_deg)
        _print_estimate(estimate, j6_now_deg=j6_now_deg)
        if not args.no_gui:
            _show_overlay(annotate_cube_yaw(image_bgr, estimate, j6_now_deg=j6_now_deg))
        return 0 if estimate is not None else 1

    camera = _open_external_camera(
        config_path=Path(args.camera_config) if args.camera_config else _DEFAULT_CAMERA_CONFIG,
        opencv_index=args.opencv_index,
    )
    session = CameraSession(external_camera=camera)
    session.start()
    try:
        if args.once or args.no_gui:
            image_bgr = _read_live_bgr(camera, timeout_s=5.0)
            estimate = measure_red_cube_yaw(image_bgr, homography, j6_now_deg=j6_now_deg)
            _print_estimate(estimate, j6_now_deg=j6_now_deg)
            if not args.no_gui and not args.once:
                _show_overlay(annotate_cube_yaw(image_bgr, estimate, j6_now_deg=j6_now_deg))
            return 0 if estimate is not None else 1
        return _live_loop(camera, homography, j6_now_deg=j6_now_deg)
    finally:
        session.stop()


def _live_loop(camera: UvcRgbCamera, homography, *, j6_now_deg: float) -> int:
    import tkinter as tk

    if not _tkinter_available():
        print("No Tk preview; printing one measurement per Enter, q to quit.")
        while True:
            command = input("Enter=measure, q=quit: ").strip().lower()
            if command in {"q", "quit"}:
                return 0
            image_bgr = _read_live_bgr(camera, timeout_s=2.0)
            estimate = measure_red_cube_yaw(image_bgr, homography, j6_now_deg=j6_now_deg)
            _print_estimate(estimate, j6_now_deg=j6_now_deg)

    root = tk.Tk()
    root.title(_WINDOW)
    image_label = tk.Label(root)
    image_label.pack()
    angle_var = tk.StringVar(value="looking for red cube…")
    tk.Label(root, textvariable=angle_var, font=("Segoe UI", 18)).pack(pady=6)
    tk.Label(root, text="q = quit   (click this window first)").pack()
    state: dict[str, object] = {"photo": None, "closing": False, "last_print_s": 0.0}

    def on_key(event: object) -> None:
        if str(getattr(event, "keysym", "")).lower() in {"q", "escape"}:
            state["closing"] = True
            root.destroy()

    def tick() -> None:
        if state["closing"]:
            return
        image_bgr = _read_live_bgr(camera, timeout_s=2.0)
        estimate = measure_red_cube_yaw(image_bgr, homography, j6_now_deg=j6_now_deg)
        overlay = annotate_cube_yaw(image_bgr, estimate, j6_now_deg=j6_now_deg)
        photo = _bgr_to_tk_photo(overlay)
        state["photo"] = photo
        image_label.configure(image=photo)
        if estimate is None:
            angle_var.set("no red cube")
        else:
            angle_var.set(f"top dJ6 {estimate.delta_j6_deg:+.1f}°   (wrap90, J6={j6_now_deg:.1f})")
            now = time.perf_counter()
            if now - float(state["last_print_s"]) >= 1.0:
                _print_estimate(estimate, j6_now_deg=j6_now_deg)
                state["last_print_s"] = now
        root.after(50, tick)

    root.bind_all("<Key>", on_key)
    root.after(50, tick)
    print("Live cube yaw vs J6=0 gripper. Click the window, q to quit.")
    root.mainloop()
    return 0


def _print_estimate(estimate, *, j6_now_deg: float) -> None:
    if estimate is None:
        print("no red cube")
        return
    print(
        f"top dJ6 {estimate.delta_j6_deg:+.1f} deg | wrap90 {estimate.yaw_wrap90_deg:+.1f} deg "
        f"| raw {estimate.yaw_table_deg:+.1f} deg | J6={j6_now_deg:.1f}"
    )


def _show_overlay(overlay_bgr: np.ndarray) -> None:
    import tkinter as tk

    if not _tkinter_available():
        return
    root = tk.Tk()
    root.title(_WINDOW)
    photo = _bgr_to_tk_photo(overlay_bgr)
    tk.Label(root, image=photo).pack()
    root._photo = photo  # noqa: SLF001
    tk.Button(root, text="Close", command=root.destroy).pack(pady=6)
    root.mainloop()


def _disable_opencv_opencl() -> None:
    try:
        import cv2

        cv2.ocl.setUseOpenCL(False)
    except Exception:
        return


def _tkinter_available() -> bool:
    try:
        import tkinter as tk

        probe = tk.Tk()
        probe.withdraw()
        probe.destroy()
    except Exception:
        return False
    return True


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
            "Pass --opencv-index or add configs/local/external_rgb.local.yaml."
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


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit("Interrupted.") from None
