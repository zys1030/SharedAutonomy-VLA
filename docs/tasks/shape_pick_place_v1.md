# Task Definition: `shape_pick_place_v1`

Language: English | [简体中文](shape_pick_place_v1.zh-CN.md)

This document defines the stable public task contract for `shape_pick_place_v1`. The final public experiment uses the `block_rotation_rq2` variant; its experiment-specific grid and results are documented in [`results.md`](../results.md). Exact site geometry, ready poses, calibration, and operator checklists remain private.

## 1. Task identity

| Field | Public value |
| --- | --- |
| `task_id` | `shape_pick_place_v1` |
| `evaluation_variant` | `block_rotation_rq2` |
| Instruction | `Pick up the red cube and place it in the UP region.` |
| Object | Red cube |
| Target region | `UP` |

## 2. Target-region semantics

`UP` is the name of a visually identifiable target region on the fixed task surface. It is a task-space label, not the positive Z direction of the robot base frame.

The runtime interface can represent other destination values, but `DOWN` is not part of the public `block_rotation_rq2` evaluation. Exact sheet dimensions, placement coordinates, camera calibration, and site-specific layout values are intentionally omitted.

## 3. Task contract

Each episode contains one instructed pick-and-place attempt. The task record preserves the instruction and collection mode:

- `collection_mode`: `manual` or `shared_autonomy`;
- task text: the standard English instruction above;
- observation and action details are defined by the runtime data interfaces.

At task level, success means grasping and lifting the target object, releasing it in the named target region, and completing without a safety abort. The exact hard-success adjudication and failure-type rules belong to [`results.md`](../results.md).

## 4. Public episode metadata

```yaml
task_id: shape_pick_place_v1
evaluation_variant: block_rotation_rq2
task_text: "Pick up the red cube and place it in the UP region."
collection_mode: manual  # manual | shared_autonomy
```

The machine-readable evaluation fields are maintained in [`results.json`](../results.json) and [`evaluation_records.csv`](../evaluation_records.csv).

## 5. Publication boundary

The public task contract does not claim continuous-pose, object, language, camera, lighting, or robot generalization. Exact coordinates and machine-specific motion parameters belong in local configuration and private hardware notes. Physical execution remains subject to the project motion gates and safety supervisor.
