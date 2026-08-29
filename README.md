# SharedAutonomy-VLA

Language: English | [简体中文](README.zh-CN.md)

SharedAutonomy-VLA is a real-robot research system for studying whether
narrow, local shared-autonomy assistance can produce better demonstrations
and better learned robot policies than manual teleoperation alone.

The project connects the full experimental path:

```text
human demonstration
        → structured episode recording
        → LeRobot-compatible dataset export
        → task-conditioned policy training
        → safety-gated closed-loop evaluation
```

The project is an engineering and methodology study. It does not claim that
this simple pick-and-place task requires a VLA, or that shared autonomy is
universally better than manual control.

## Current status

This repository contains the source code and documentation release. The two
exported datasets and the final expert-only SmolVLA checkpoints are published
on the Hugging Face Hub. Raw native recordings, standalone private videos,
training logs, and machine-local artifacts remain outside this repository.

The repository therefore does not include the raw datasets, model weights,
training logs, machine-local configuration, or private commissioning notes.

## Public artifacts

| Artifact | Public repository | License |
| --- | --- | --- |
| Manual 70 dataset | [`zys1030/sharedautonomy-vla-block-rot-manual-70ep`](https://huggingface.co/datasets/zys1030/sharedautonomy-vla-block-rot-manual-70ep) | CC BY 4.0 |
| Shared Autonomy 70 dataset | [`zys1030/sharedautonomy-vla-block-rot-sa-70ep`](https://huggingface.co/datasets/zys1030/sharedautonomy-vla-block-rot-sa-70ep) | CC BY 4.0 |
| Manual 70 SmolVLA checkpoint | [`zys1030/smolvla-block-rot-manual-70ep-50k`](https://huggingface.co/zys1030/smolvla-block-rot-manual-70ep-50k) | Apache-2.0 |
| Shared Autonomy 70 SmolVLA checkpoint | [`zys1030/smolvla-block-rot-sa-70ep-50k`](https://huggingface.co/zys1030/smolvla-block-rot-sa-70ep-50k) | Apache-2.0 |

## Main result

The locked comparison uses the `shape_pick_place_v1` task in the
`block_rotation_rq2` variant:

> Pick up the red cube and place it in the UP region.

Both policies use 70 demonstrations, the same SmolVLA expert-only training
recipe, 50,000 training steps, bf16 precision, and an `8 × 2` batch
configuration. The paired real-robot evaluation contains 36 conditions:
9 categorical XY positions across 4 initial wrap-90 yaw values, with one
rollout per policy per condition.

| Policy trained from | Episodes | Frames | Hard successes |
| --- | ---: | ---: | ---: |
| Manual demonstrations | 70 | 15,829 | 25 / 36 (69.4%) |
| Shared-autonomy demonstrations | 70 | 14,212 | 34 / 36 (94.4%) |

![Manual and shared-autonomy success rates, paired outcomes, and position heatmaps](assets/results_paired.svg)

This is a descriptive result on a fixed paired grid, not a claim of repeated-
trial variance or broad deployment generalization. See the complete protocol
and failure analysis in [`docs/results.md`](docs/results.md).

## Method

During manual collection, the operator controls Cartesian motion and the
gripper. During shared-autonomy collection, the operator retains those roles
while a bounded yaw assistant proposes local J6 alignment from the external
camera. The assistant does not independently execute the complete task.

The learned policy receives synchronized visual observations, robot state, and
task text. Its suggested actions remain behind the same local arbitration and
safety supervisor used by the rest of the system.

```text
SpaceMouse / task text / cameras / robot state
              ↓
     human or policy suggestion
              ↓
    arbitration and safety filter
              ↓
       RM65 robot + gripper
              ↓
        structured episode data
```

## Documentation

| Document | Description |
| --- | --- |
| [`docs/overview.md`](docs/overview.md) | Research question, architecture, data interfaces, policy route, and scope |
| [`docs/tasks/shape_pick_place_v1.md`](docs/tasks/shape_pick_place_v1.md) | Stable task contract and publication boundary |
| [`docs/datasets.md`](docs/datasets.md) | Locked dataset facts, data contract, lineage, and release status |
| [`docs/results.md`](docs/results.md) | Locked 36-condition evaluation protocol and results |
| [`docs/training.md`](docs/training.md) | Training recipe, sanitized telemetry curves, and checkpoint selection |
| [`docs/limitations.md`](docs/limitations.md) | Interpretation limits and negative-result boundary |
| [`docs/hardware.md`](docs/hardware.md) | Public hardware roles, host split, timing, and safety boundary |
| [`docs/engineering_conventions.md`](docs/engineering_conventions.md) | Project conventions for code, checks, configuration, documentation, and safety |
| [`tools/README.md`](tools/README.md) | Support tools for diagnostics, benchmarking, and experiment workflows |

The Chinese documentation mirrors the main public documents with the
`.zh-CN.md` suffix.

## Installation

The project targets Python 3.12 and pins the core LeRobot dependency to
version 0.6.0. Install only the optional groups required for the task:

```bash
# Core development and offline tests
pip install -e ".[dev]"

# Dataset export and LeRobot dataset utilities
pip install -e ".[dataset]"

# SmolVLA training or inference dependencies
pip install -e ".[smolvla]"

# Replay and plotting tools
pip install -e ".[dev,visualization]"

# Hardware integrations; use only on the robot host
pip install -e ".[dev,hardware,visualization]"
```

Hardware-specific identities, calibration, workspace geometry, ready poses,
gripper settings, and connection values belong in ignored local configuration
files based on the `configs/local/*.example.yaml` templates. They must not be
committed.

## Capability / Quickstart

The commands below use placeholders such as `<episode_dir>` and
`<dataset_root>`. Run any script with `--help` for the complete argument list.

### Offline episode inspection

These checks do not connect to robot hardware:

```bash
pytest -m core
python scripts/dry_run_manual_cartesian.py
python scripts/check_episode.py <episode_dir>
python scripts/check_episode.py <episode_dir> --json
python scripts/replay_episode.py <episode_dir> --step 0 --hz 5
python scripts/plot_evaluation_results.py
```

`check_episode.py` validates a native episode and prints summary statistics.
`replay_episode.py` requires `metadata.json`, `steps.jsonl`, and the referenced
`images/` files; omit `--hz` for manual stepping with the arrow keys.

### Collect demonstrations

`collect_demonstrations.py` uses a SpaceMouse and the RM-65 interface. Add
`--enable-cameras` when recording visual episodes:

```bash
python scripts/collect_demonstrations.py --ip <RM65_IP> --record-dir <run_dir>/episode --enable-cameras --collection-mode manual --task-id shape_pick_place_v1 --source-object red --destination up
```

The main controls are `--duration-s`, `--steps`, and `--control-hz`;
`--collection-mode` accepts `manual` or `shared_autonomy`; and
`--task-text`, `--source-object`, and `--destination` populate task metadata.
Motion is enabled only when the configuration-side gate is true (local
`enable_motion: true` or `--config-enable-motion`) and `--allow-motion` is
present. Omit the motion flags for a no-motion preview. `--enable-gripper` and
`--go-to-ready` also require motion to be enabled.

### Export a local LeRobot dataset

Export is read-only with respect to hardware and accepts one or more native
episode directories:

```bash
python scripts/export_lerobot_dataset.py <episode_dir> --out-root <dataset_root> --repo-id local/shape_pick_place_v1
```

Video export is the default. Use `--no-videos` for image-in-parquet output,
`--no-diag` to omit diagnostic columns, `--allow-aborted` to include aborted
episodes, and `--resume` to append to an existing dataset.
The feature order and units are defined in [`docs/datasets.md`](docs/datasets.md).

### Train, serve, and roll out a policy

Training requires a local exported dataset or one of the public dataset
repositories above, the upstream model assets, and the corresponding training
dependencies. The locked recipe and checkpoint links are in
[`docs/training.md`](docs/training.md); the public GitHub repository does not
vendor the datasets or final checkpoints.

The inference servers do not connect to robot hardware or enable motion:

```bash
python scripts/serve_smolvla_policy.py --checkpoint <checkpoint_dir> --dataset-root <dataset_root> --dataset-repo-id <repo_id> --device cuda --port 8089
python scripts/serve_act_policy.py --checkpoint <checkpoint_dir> --dataset-root <dataset_root> --dataset-repo-id <repo_id> --device cuda --port 8088
```

The corresponding rollout clients read the cameras and robot state and send
inference requests to those servers. They print actions without dispatching
motion by default:

```bash
python scripts/rollout_smolvla_policy.py --ip <RM65_IP> --infer-url http://<policy_host>:8089 --steps 10 --task-text "Pick up the red cube and place it in the UP region."
python scripts/rollout_act_policy.py --ip <RM65_IP> --infer-url http://<policy_host>:8088 --steps 10 --task-text "Pick up the red cube and place it in the UP region."
```

Useful rollout parameters include `--infer-url`, `--steps`, `--duration-s`,
`--control-hz`, `--reset-every`, and the task metadata flags. Sending joint
commands additionally requires both motion gates, the local hardware
configuration, an operator-controlled ready-pose and safety procedure, and
an available emergency stop. See [`docs/hardware.md`](docs/hardware.md).

## Safety boundary

Physical motion is disabled by default. A motion-capable run requires both a
valid local configuration gate and an explicit command-line motion gate. The
safety supervisor remains between human or policy suggestions and the robot,
and enforces workspace, velocity, step-size, freshness, gripper, deadman, and
emergency-stop checks.

A successful import, dry-run, or SDK connection check does not authorize
motion. Hardware commissioning requires an operator-controlled procedure and
a usable emergency-stop path. See [`docs/hardware.md`](docs/hardware.md).

## Release boundary

The source code is licensed under the [MIT License](LICENSE). The published
datasets use CC BY 4.0 and the published fine-tuned checkpoints use
Apache-2.0, with upstream attribution recorded in their model cards. Raw
recordings, standalone private videos, training logs, and machine-local media
remain outside the repository.

## Citation

For the project method and locked comparison, cite the repository and refer
to [`docs/results.md`](docs/results.md) for the evaluation protocol. Public
dataset and checkpoint links are also recorded in
[`docs/datasets.md`](docs/datasets.md) and [`docs/training.md`](docs/training.md).
