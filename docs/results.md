# Manual vs. Shared-Autonomy Data Collection: Closed-Loop Results

Language: English | [简体中文](results.zh-CN.md)

> Release status: the numerical record and evaluation labels are frozen. External publication of the datasets, videos, and checkpoints is currently deferred; this page intentionally makes no external revision, checksum, or license claim.

## Summary

Under the same 70-episode demonstration budget and training recipe, the policy trained on shared-autonomy-assisted demonstrations succeeded in **34/36 conditions (94.4%)**, compared with **25/36 (69.4%)** for the policy trained on manual demonstrations. This is a descriptive difference of **9 conditions** or **25.0 percentage points** on the fixed evaluation grid.

The paired comparison is more informative than the marginal rates alone: both policies succeeded in 24 conditions, shared autonomy alone succeeded in 10, manual alone succeeded in 1, and both failed in 1. The largest difference appeared in the far-distance band, where shared autonomy succeeded in 12/12 conditions and manual succeeded in 5/12.

![Manual and shared-autonomy success rates, paired outcomes, and position heatmaps](../assets/results_paired.svg)

*Figure 1. Overall hard-success rates, paired condition outcomes, and per-position success rates aggregated over the four evaluated yaw values.*

No error bars or confidence intervals are shown. Each of the 36 conditions was evaluated once per policy, so the percentages describe this fixed `9 × 4` grid rather than repeated-trial variability or performance over a sampled deployment distribution.

## 1. Evaluation protocol

### Research question

The comparison asks whether shared-autonomy-assisted data collection improves the closed-loop success of a learned policy when the demonstration budget and training recipe are held fixed.

The shared-autonomy collector kept the human in control of Cartesian translation and the gripper while a yaw assistant controlled J6 alignment. The manual collector did not use that yaw assistance.

### Compared policies and data

| Property | Manual | Shared autonomy |
| --- | ---: | ---: |
| Collection mode | Manual teleoperation | Human XYZ/gripper + assisted J6 yaw alignment |
| Demonstrations | 70 episodes | 70 episodes |
| Dataset frames | 15,829 | 14,212 |
| Policy family | SmolVLA | SmolVLA |
| Adaptation | Expert-only | Expert-only |
| Training steps | 50,000 | 50,000 |
| Precision | bf16 | bf16 |
| Batch configuration | `8 × 2` | `8 × 2` |

The episode budget is matched, while the frame counts differ because episode lengths are not identical.

### Task and condition grid

The stable task contract is [`shape_pick_place_v1`](tasks/shape_pick_place_v1.md).

The task is `Pick up the red cube and place it in the UP region.` The paired real-robot evaluation contains 9 categorical XY positions (`left/center/right × near/middle/far`) and 4 wrap-90 initial yaw values (`0°`, `22.5°`, `−22.5°`, `45°`). There are 36 attempts per policy, with one rollout per policy per condition, and both policies use the same physical arrangement for each pair.

The public record uses categorical position IDs; exact site coordinates are intentionally not included.

### Execution protocol

- **Execution:** closed-loop real-robot rollout.
- **Attempts:** 36 per policy, one rollout per policy per condition.
- **Pairing:** the Manual and Shared-Autonomy policies use the same condition arrangement.
- **Safety:** the normal motion gate and local safety supervision remain enabled.
- **Adjudication:** human observation supported by rollout video and replay notes.

This is a fixed paired grid, not a random sample from a deployment distribution. It does not establish performance over arbitrary continuous positions or yaw angles.

### Hard-success criterion

Hard success requires a grasp across two opposing faces, a lift clear of the support surface, a release that remains in the `UP` region, and no safety abort. An edge grasp is a hard failure even if the cube is later lifted or placed.

Exact yaw, a perfectly centered grasp, and a perfectly smooth path are not required. Non-fatal yaw and XY deviations remain quality annotations rather than changing the hard-success label.

### Recorded outcomes and analysis

The public evaluation record contains one normalized row per condition with categorical position and distance fields, initial wrap-90 yaw, hard-success labels, one primary failure type for each failed rollout, quality flags, and a short public note. The complete record is in [`evaluation_records.csv`](evaluation_records.csv).

The report includes overall counts, paired outcomes, descriptive slices by distance, position, and yaw, and primary failure-type counts. No confidence intervals or error bars are reported because each policy has one rollout per condition.

## 2. Results

### Overall results

| Policy | Successes | Attempts | Success rate |
| --- | ---: | ---: | ---: |
| Manual | 25 | 36 | 69.4% |
| Shared autonomy | 34 | 36 | 94.4% |
| Descriptive difference | +9 | — | +25.0 percentage points |

### Paired outcomes

| Shared autonomy | Manual success | Manual failure | Row total |
| --- | ---: | ---: | ---: |
| Success | 24 | 10 | 34 |
| Failure | 1 | 1 | 2 |
| Column total | 25 | 11 | 36 |

Among the 11 discordant conditions, shared autonomy alone succeeded in 10 and manual alone succeeded in 1. This comparison remains descriptive because the grid contains one rollout per policy per condition.

### Results by distance

| Distance band | Manual | Shared autonomy | Difference |
| --- | --- | --- | ---: |
| Near | 10/12 (83.3%) | 12/12 (100.0%) | +16.7 pp |
| Middle | 10/12 (83.3%) | 10/12 (83.3%) | 0.0 pp |
| Far | 5/12 (41.7%) | 12/12 (100.0%) | +58.3 pp |

The overall difference was concentrated in the far band. This slice is useful diagnostically, but it should not be interpreted as an independently powered statistical comparison.

### Results by initial position

The figure's three heatmaps aggregate each categorical XY position over the four evaluated yaw values:

- At `left_far`, manual failed all four yaw conditions while shared autonomy succeeded in all four (`0/4 → 4/4`).
- At `right_far`, success increased from `2/4` to `4/4`; at `center_far`, it increased from `3/4` to `4/4`.
- Near positions were already strong for manual collection: the center and right cells remained `4/4`, while `left_near` increased from `2/4` to `4/4`.
- `right_middle` was the only position where the shared-autonomy policy was lower (`4/4 → 3/4`); its single additional failure was an edge grasp at `45°`.

These are four-trial descriptive cell rates, not estimates with enough within-position replication for meaningful error bars.

### Results by initial yaw

| Initial yaw | Manual | Shared autonomy | Difference |
| --- | --- | --- | ---: |
| `0°` | 7/9 (77.8%) | 9/9 (100.0%) | +22.2 pp |
| `22.5°` | 7/9 (77.8%) | 9/9 (100.0%) | +22.2 pp |
| `−22.5°` | 7/9 (77.8%) | 9/9 (100.0%) | +22.2 pp |
| `45°` | 4/9 (44.4%) | 7/9 (77.8%) | +33.3 pp |

The `45°` condition was the most difficult yaw slice for both policies. Both shared-autonomy failures were edge grasps at `45°` in the middle-distance band.

### Observed primary failure types

| Primary failure type | Manual | Shared autonomy |
| --- | ---: | ---: |
| Edge grasp | 5 | 2 |
| No grasp | 3 | 0 |
| No lift | 1 | 0 |
| Grasp slip or drop | 2 | 0 |
| **Total failures** | **11** | **2** |

Primary failure type records where the rollout failed. Quality flags separately retain observations such as under-rotation, over-rotation, wrong rotation sign, reach error, XY offset, and repeated grasp attempts. An edge grasp is always a failure and is never represented only as a quality flag.

## 3. Interpretation

On this fixed grid, shared-autonomy-assisted demonstrations produced a policy with higher closed-loop success under a matched episode budget and training recipe. The paired results show that the difference is not explained only by a few conditions where both policies were unstable: shared autonomy recovered 10 conditions that manual failed, while the reverse occurred once.

The pattern is consistent with the intended role of yaw assistance during data collection. The strongest difference appeared at far positions, and the remaining shared-autonomy failures were both `45°` edge grasps. These observations are diagnostic associations within this experiment; they do not by themselves isolate a causal mechanism or establish general performance outside the tested grid.

For broader scope and interpretation limits, see [`limitations.md`](limitations.md).

## 4. Reproducibility artifacts

- [`results.json`](results.json): authoritative machine-readable protocol, condition grid, aggregate results, and release metadata.
- [`evaluation_records.csv`](evaluation_records.csv): all 36 paired conditions with normalized success labels, primary failure types, quality flags, and public notes.
- [`datasets.md`](datasets.md): locked dataset counts, public data contract, lineage, and deferred release metadata.
- [`training.md`](training.md): locked training recipe, sanitized loss/gradient/learning-rate curves, checkpoint selection, and training interpretation limits.
- [`../scripts/plot_evaluation_results.py`](../scripts/plot_evaluation_results.py): regenerates the public SVG directly from the JSON and CSV sources.

```bash
python scripts/plot_evaluation_results.py
```

The numerical tables and figure in this document are derived from the JSON and CSV sources. If external dataset or checkpoint metadata is added later, it must not change the locked evaluation labels or aggregate counts.
