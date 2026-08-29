# Datasets and Data Lineage

Language: English | [简体中文](datasets.zh-CN.md)

> Release status: the two exported datasets used by the locked comparison are public on the Hugging Face Hub under CC BY 4.0. Raw native recordings and standalone private videos remain outside the public release.

## 1. Locked comparison datasets

The main comparison uses two datasets collected for the same task contract and exported with the same data mapping:

| Dataset | Collection mode | Episodes | Frames | FPS | Task | Public repository |
| --- | --- | ---: | ---: | ---: | --- | --- |
| Manual 70 | Manual Cartesian teleoperation and gripper control | 70 | 15,829 | 10 | `shape_pick_place_v1 / block_rotation_rq2` | [`zys1030/sharedautonomy-vla-block-rot-manual-70ep`](https://huggingface.co/datasets/zys1030/sharedautonomy-vla-block-rot-manual-70ep) |
| Shared Autonomy 70 | Human Cartesian/gripper control with bounded J6 yaw assistance | 70 | 14,212 | 10 | `shape_pick_place_v1 / block_rotation_rq2` | [`zys1030/sharedautonomy-vla-block-rot-sa-70ep`](https://huggingface.co/datasets/zys1030/sharedautonomy-vla-block-rot-sa-70ep) |

The episode budget is matched. Frame counts differ because the demonstrations have different durations. The stable task text and publication boundary are defined in [`tasks/shape_pick_place_v1.md`](tasks/shape_pick_place_v1.md).

## 2. Public data contract

Native episodes are the semantic source of truth. They preserve synchronized observations, robot state, the separate `human`, `assist`, and safety-filtered `executed` actions, timestamps, collection mode, effective configuration, and safety metadata.

The final LeRobot export contains synchronized image, state, action, task, and
optional diagnostic features. The stable field layout is defined below.

### 2.1 LeRobot feature layout

The v1 export uses the following stable feature order. Joint positions and joint
targets use degrees; `gripper.pos` is an open fraction in `[0, 1]`; Cartesian
height and its step difference use metres.

| Feature | Shape / dtype | Ordered fields or meaning | Null and validation behavior |
| --- | --- | --- | --- |
| `observation.state` | `(10,)` `float32` | `joint_1.pos` … `joint_6.pos`, `gripper.pos`, `ee.z`, `ee.dz`, `gripper.time_since_close` | Joint state, commanded gripper fraction, and `ee.z` must be finite; export fails on missing or invalid values |
| `action` | `(7,)` `float32` | `joint_1.pos` … `joint_6.pos`, `gripper.pos` | Executed joint targets and gripper target must be present and finite; export fails fast if they are missing |
| `observation.images.wrist` | `(480, 640, 3)` RGB video | Wrist camera color frames | Every frame and color path must be present, `uint8`, and the expected shape |
| `observation.images.external` | `(480, 640, 3)` RGB video | Fixed external camera color frames | Every frame and color path must be present, `uint8`, and the expected shape |
| `task` | Per-frame string | `metadata.task_text` | Required task text copied to each exported frame |

The state vector has these derived channels:

- `ee.z`: the flange height `observation.ee_position_m[2]` in metres;
- `ee.dz`: the current `ee.z` minus the previous value, with `0` at the first frame;
- `gripper.time_since_close`: normalized steps since the latest open-to-close edge, saturated at 20 steps; it is `0` while open.

The final training export uses RGB video and does not export wrist depth,
`gripper_actual_open_fraction`, `assist_action`, `authority`, or the full
end-effector pose. Optional `diag.*` columns record deadman state, safety
intervention, timing, and wall-clock information, but are not default policy
inputs. The implementation in [`schema.py`](../sharedautonomy/data/schema.py)
and [`lerobot_export.py`](../sharedautonomy/data/lerobot_export.py) remains
authoritative for serialization and validation details.

## 3. Data lineage

```text
native episode
    → structural and media validation
    → LeRobot-compatible export
    → dataset metadata verification
    → SmolVLA training
    → paired real-robot evaluation
```

Exports are treated as reproducible derivatives: a new export uses a new dataset root instead of overwriting an existing one, and the native episode remains the source record. Machine-local export manifests, absolute source paths, raw videos, and operator records are not part of the public documentation release.

## 4. Included and excluded scope

Only the two 70-episode datasets above support the locked Manual-versus-Shared-Autonomy result. Earlier ACT, aligned-cube, handle-based, color-binding, partial, and superseded snapshots are development history and must not be mixed into this comparison. Their high-level role is summarized only where it helps interpret the final experiment.

The current code release does not include raw episodes, standalone private
videos, training logs, model weights, machine-local configuration, or private
field notes. The exported LeRobot datasets are hosted in the public repositories
listed above.

## 5. Release boundary

The current code/document release publishes the data contract and lineage, not
the raw episodes or standalone private videos. The two public exported
datasets are released under CC BY 4.0; their public metadata must match the
locked task, counts, features, and limitations in this page and
[`results.json`](results.json). The corresponding public checkpoints are linked
from [`training.md`](training.md). See [`results.md`](results.md) for the
evaluation and [`limitations.md`](limitations.md) for interpretation boundaries.
