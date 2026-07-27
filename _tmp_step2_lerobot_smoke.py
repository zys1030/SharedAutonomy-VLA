"""One-off Step2 LeRobot v3.0 write/read smoke with synthetic frames."""

from __future__ import annotations

import json
import shutil
import sys
import traceback
from pathlib import Path

import numpy as np


def main() -> int:
    from lerobot.datasets.lerobot_dataset import LeRobotDataset
    import lerobot

    root = Path("outputs/tmp/lerobot_smoke_v001")
    if root.exists():
        shutil.rmtree(root)

    features = {
        "observation.state": {
            "dtype": "float32",
            "shape": (7,),
            "names": [
                "joint_1.pos",
                "joint_2.pos",
                "joint_3.pos",
                "joint_4.pos",
                "joint_5.pos",
                "joint_6.pos",
                "gripper.pos",
            ],
        },
        "action": {
            "dtype": "float32",
            "shape": (7,),
            "names": [
                "joint_1.pos",
                "joint_2.pos",
                "joint_3.pos",
                "joint_4.pos",
                "joint_5.pos",
                "joint_6.pos",
                "gripper.pos",
            ],
        },
        "observation.images.wrist": {
            "dtype": "video",
            "shape": (64, 64, 3),
            "names": ["height", "width", "channels"],
        },
        "observation.images.external": {
            "dtype": "video",
            "shape": (64, 64, 3),
            "names": ["height", "width", "channels"],
        },
    }

    report: dict = {
        "lerobot_version": getattr(lerobot, "__version__", "unknown"),
        "root": root.as_posix(),
        "use_videos": True,
        "ok": False,
        "stages": {},
    }

    try:
        dataset = LeRobotDataset.create(
            repo_id="local/lerobot_smoke",
            fps=10,
            features=features,
            root=root,
            robot_type="rm65",
            use_videos=True,
        )
        report["stages"]["create"] = "ok"

        rng = np.random.default_rng(0)
        for i in range(2):
            frame = {
                "observation.state": rng.standard_normal(7).astype(np.float32),
                "action": rng.standard_normal(7).astype(np.float32),
                "observation.images.wrist": rng.integers(0, 255, (64, 64, 3), dtype=np.uint8),
                "observation.images.external": rng.integers(0, 255, (64, 64, 3), dtype=np.uint8),
                "task": "smoke pick and place",
            }
            # Make channel order obvious: frame0 wrist is pure red.
            if i == 0:
                wrist = np.zeros((64, 64, 3), dtype=np.uint8)
                wrist[..., 0] = 255
                frame["observation.images.wrist"] = wrist
            dataset.add_frame(frame)
        report["stages"]["add_frame"] = "ok"

        # On Windows, ProcessPoolExecutor spawn can be fragile; try default first.
        try:
            dataset.save_episode()
            report["stages"]["save_episode"] = {"ok": True, "parallel_encoding": True}
        except Exception as exc:
            report["stages"]["save_episode_parallel_failed"] = repr(exc)
            traceback.print_exc()
            # Retry with parallel_encoding=False if possible.
            try:
                dataset.save_episode(parallel_encoding=False)
                report["stages"]["save_episode"] = {
                    "ok": True,
                    "parallel_encoding": False,
                    "note": "fallback after parallel failure",
                }
            except Exception as exc2:
                report["stages"]["save_episode"] = {"ok": False, "error": repr(exc2)}
                raise

        dataset.finalize()
        report["stages"]["finalize"] = "ok"

        # Reload read-only
        ds = LeRobotDataset("local/lerobot_smoke", root=root)
        item0 = ds[0]
        shapes = {
            k: (tuple(v.shape) if hasattr(v, "shape") else type(v).__name__)
            for k, v in item0.items()
        }
        report["stages"]["reload"] = {
            "ok": True,
            "num_episodes": ds.num_episodes,
            "num_frames": ds.num_frames,
            "fps": ds.fps,
            "item0_keys": sorted(item0.keys()),
            "item0_shapes": {k: list(v) if isinstance(v, tuple) else v for k, v in shapes.items()},
        }

        # Channel-order smoke: wrist frame0 should still be red-dominant after video roundtrip.
        wrist = item0.get("observation.images.wrist")
        if wrist is not None and hasattr(wrist, "shape"):
            arr = np.asarray(wrist)
            # LeRobot may return CHW float in [0,1] or HWC uint8 depending on return_uint8.
            if arr.ndim == 3 and arr.shape[0] in (1, 3) and arr.shape[-1] not in (1, 3):
                # CHW
                r_mean = float(arr[0].mean())
                g_mean = float(arr[1].mean())
                b_mean = float(arr[2].mean())
                layout = "CHW"
            else:
                r_mean = float(arr[..., 0].mean())
                g_mean = float(arr[..., 1].mean())
                b_mean = float(arr[..., 2].mean())
                layout = "HWC"
            report["stages"]["channel_check"] = {
                "layout": layout,
                "dtype": str(arr.dtype),
                "shape": list(arr.shape),
                "R_mean": r_mean,
                "G_mean": g_mean,
                "B_mean": b_mean,
                "red_dominant": r_mean > g_mean and r_mean > b_mean,
            }

        report["ok"] = True
    except Exception as exc:
        report["ok"] = False
        report["error"] = repr(exc)
        report["traceback"] = traceback.format_exc()
        # Fallback path: try image-backed dataset if video path failed hard.
        if report["stages"].get("create") == "ok" or "create" not in report["stages"]:
            try:
                root_img = Path("outputs/tmp/lerobot_smoke_images_v001")
                if root_img.exists():
                    shutil.rmtree(root_img)
                img_features = {
                    k: ({**v, "dtype": "image"} if v.get("dtype") == "video" else v)
                    for k, v in features.items()
                }
                ds2 = LeRobotDataset.create(
                    repo_id="local/lerobot_smoke_images",
                    fps=10,
                    features=img_features,
                    root=root_img,
                    robot_type="rm65",
                    use_videos=False,
                )
                rng = np.random.default_rng(1)
                for _ in range(2):
                    ds2.add_frame(
                        {
                            "observation.state": rng.standard_normal(7).astype(np.float32),
                            "action": rng.standard_normal(7).astype(np.float32),
                            "observation.images.wrist": rng.integers(
                                0, 255, (64, 64, 3), dtype=np.uint8
                            ),
                            "observation.images.external": rng.integers(
                                0, 255, (64, 64, 3), dtype=np.uint8
                            ),
                            "task": "smoke pick and place images",
                        }
                    )
                ds2.save_episode(parallel_encoding=False)
                ds2.finalize()
                reloaded = LeRobotDataset("local/lerobot_smoke_images", root=root_img)
                report["fallback_images"] = {
                    "ok": True,
                    "root": root_img.as_posix(),
                    "num_frames": reloaded.num_frames,
                    "num_episodes": reloaded.num_episodes,
                }
            except Exception as exc_img:
                report["fallback_images"] = {
                    "ok": False,
                    "error": repr(exc_img),
                    "traceback": traceback.format_exc(),
                }

    print(json.dumps(report, indent=2, ensure_ascii=True), flush=True)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
