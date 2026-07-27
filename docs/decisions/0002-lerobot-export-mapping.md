# ADR 0002：Native Episode → LeRobot 导出映射

- 状态：Accepted
- 日期：2026-07-27
- 依赖：ADR 0001（`sharedautonomy.episode.v1`）、LeRobot `0.6.0`（on-disk `codebase_version` v3.0）

## 背景

Pilot 阶段已在 `outputs/runs/shape-pick-place-pilot-*/episode/` 落盘 native episode（`metadata.json` + `steps.jsonl` + `images/*.npy`）。训练与 LeRobot 生态对接需要把 native 格式导出为 LeRobot dataset，且导出结果应能 `LeRobotDataset` 加载、与 RM65 关节动作语义一致。

2026-07-27 对三条 pilot（001/002/003）完成 `check_episode` 与字段统计；并在 `sharedautonomy-lr060-cf` 上用合成帧验证 `LeRobotDataset.create → add_frame → save_episode → finalize → reload` 全链路（含双路 RGB 视频编码）。

## 决策

### 1. 导出粒度与命名

- **输入**：一个或多个 native `episode/` 目录（`status == "completed"` 且 `success == true`；默认不导 `aborted`，可用 CLI 显式放开）。
- **输出**：本地 LeRobot dataset 目录，例如 `outputs/datasets/shape_pick_place_v1_v001/`。
- **`repo_id`**：`local/shape_pick_place_v1`（本地标识，不要求立刻 push Hub）。
- **`robot_type`**：`rm65`。
- **`fps`**：`10`（来自 episode `metadata.control_rate_hz`，取整；与采集 `collection_teleop.control_rate_hz` 一致）。
- **版本目录**：重导出使用新目录名（如 `_v002`），不覆盖已有 dataset root（LeRobot `create()` 要求 root 不存在；追加用 `resume()`）。

### 2. Feature 声明（v1 最小集）

| LeRobot key | dtype | shape | 来源（native） | 说明 |
| --- | --- | --- | --- | --- |
| `observation.state` | float32 | (7,) | `observation.joint_position_deg` + 夹爪 | 见 §3 |
| `action` | float32 | (7,) | `executed_action.joint_target_deg` + 夹爪 | 见 §3 |
| `observation.images.wrist` | video | (480, 640, 3) | `images/step_*_wrist_color.npy` | RGB，HWC uint8 写入 |
| `observation.images.external` | video | (480, 640, 3) | `images/step_*_external_color.npy` | 同上 |
| `task`（每帧） | — | — | `metadata.task_text` | LeRobot 必填字符串；写入 `meta/tasks` |

**v1 不导出**：腕部深度、`assist_action`、`authority`、EE 位姿/四元数、多路时间戳细项。

**图像存储**：`use_videos=True`（MP4）；本机 smoke 已验证 SVT-AV1 编码与 `parallel_encoding=True` 可用。通道顺序保持 native RGB，**不做 BGR 翻转**（ADR 0001 §5；smoke 纯红帧读回 R 主导）。

### 3. 向量语义（7 维 joint + gripper）

`names` 与 `sharedautonomy/robot/rm65.py` 中 `JOINT_KEYS` + `gripper.pos` 对齐：

```text
joint_1.pos … joint_6.pos, gripper.pos   # 单位：deg, deg, …, 无单位 [0,1]
```

| 维度 | `observation.state` | `action` |
| --- | --- | --- |
| 0–5 | `observation.joint_position_deg`（当前实测关节角，deg） | `executed_action.joint_target_deg`（当步下发目标，deg） |
| 6 | `observation.gripper_commanded_open_fraction` | `executed_action.gripper_target_open_fraction` |

**夹爪不用 `gripper_actual_open_fraction`**：当前串口软夹爪无位置回读，pilot 三条该字段 **100% 为 null**（ADR 0001 §7）。`gripper_commanded_open_fraction` 与 human/executed gripper target 在 pilot 中均为 0% null。

**`joint_target_deg` 兜底**：pilot 三条 **0% null**，v1 不做前向填充或拒导；若未来出现 null，export 应 fail-fast 并报告步号。

### 4. 诊断列（`diag.*`）：记录、不进默认训练输入

下列字段 **写入 LeRobot parquet**，但 **不使用 `observation.` 前缀**，避免 `dataset_to_policy_features()` 将其归类为策略 state 输入：

| LeRobot key | dtype | shape | 来源 | 用途 |
| --- | --- | --- | --- | --- |
| `diag.deadman_active` | float32 | (1,) | `human_action.deadman_active` | 操作者是否按住 deadman |
| `diag.safety_intervened` | float32 | (1,) | `executed_action.safety_intervened` | 安全层是否改写本步运动 |
| `diag.actual_dt_s` | float32 | (1,) | `executed_action.actual_dt_s` | 真实控制周期 |
| `diag.wall_time_s` | float64 | (1,) | `observation.timestamp.timestamp_utc` | 相对 episode 起点的墙钟秒数 |

**训练策略（v1）**：

- **默认 BC / ACT smoke 不使用 `diag.*`**；策略输入仅为 `observation.state`、图像与 `task`。
- **export 默认不裁帧**：不因 `deadman_active`、`safety_intervened` 或 `sync_warnings` 丢弃步；native 与 LeRobot 逐步一一对应。
- 后续若需「只训有效操作段」，在训练脚本或单独 filter 阶段基于 `diag.*` 处理，**不在 v1 export 里默认启用**。

**Pilot 参考**（2026-07-27，`shape_pick_place_v1`）：

| run | 步数 | deadman 有效比例 | `safety_intervened` 步数 |
| --- | ---: | ---: | ---: |
| pilot-001 `red→up` | 400 | 87.5% | 214 |
| pilot-002 `red→down` | 400 | 69.3% | 356 |
| pilot-003 `red→up` | 240 | 90.4% | 47 |

`safety_intervened` 比例高（尤其 002）反映工作区/限速/IK 等安全链介入频繁，保留 `diag.*` 便于事后分析，**不因此改变 v1 全量导出**。

### 5. 语言条件与 episode 元数据

- 每帧 `task` = `metadata.task_text`（须与任务卡标准句逐字一致，以便 `task_index` 稳定）。
- `metadata.source_object` / `destination` **不单独建 feature 列**；语义已编码在 `task_text` 中。若后续需要结构化条件，另开 ADR 增列（如 `observation.task_condition`）。
- Export manifest（实现层）应记录：`episode_dir` → LeRobot `episode_index`、`git_commit`、lerobot 版本、native `episode_id` / `run_id`。

### 6. 时间与同步

- LeRobot 侧 `timestamp` 由 `frame_index / fps` 均匀生成，**不可写入 native 的真实抖动**；真实时序保留在 `diag.actual_dt_s` 与 `diag.wall_time_s`。
- `sync_warnings`（如 `wrist_camera_stale`）在 pilot 中每条仅 1–2 步，**v1 不单独导出**；相机帧仍随步导出（`drop_stale_cameras` 默认 false）。

### 7. 深度与其它模态

- **v1 不导出腕部深度**。理由：BC/ACT 主流基线为 RGB；深度留在 native `.npy`，可随时 `--include-depth` 重导；且 LeRobot 深度单位推断与 native `depth_scale_m_per_unit` 解耦，需单独验证。
- 若启用深度导出，须另行列 `observation.images.wrist_depth`、`info.is_depth_map`、单位与编码器，并更新本 ADR。

### 8. 实现边界

- **映射逻辑**放在 `sharedautonomy/data/`（可单测的纯函数 + driver）；`scripts/export_lerobot_dataset.py` 为薄 CLI。
- **读取 native 时优先流式**解析 `steps.jsonl` 并按步 `np.load` 图像；避免对大批量 episode 使用 `load_recorded_episode()` 一次性载入全部图像。
- **必须**在全部 episode 写入后调用 `dataset.finalize()`。
- **lazy import** `lerobot`：无 lerobot 环境时，纯 mapping 单测仍可运行。

## Pilot 验收事实（2026-07-27）

- 三条 pilot：`check_episode` 均 PASS；双相机覆盖率 100%；图像 shape `480×640×3` uint8；深度 `480×640` uint16、`depth_scale_m_per_unit=0.001`。
- `task_text` 与 [`docs/tasks/shape_pick_place_v1.md`](../tasks/shape_pick_place_v1.md) 标准句一致。
- LeRobot smoke：`outputs/tmp/lerobot_smoke_v001`，2 帧 64×64 双路 video，reload 成功；读回图像为 CHW float32 `[0,1]`。

## 后果

- Export adapter 有明确、可测的字段表；与 RM65 `send_action` 关节语义一致，便于导出后 replay 抽检。
- `diag.*` 保留安全与操作有效性信息，但不污染默认 policy 输入；训练管线须显式选择是否使用诊断列或裁帧。
- 不导深度与 EE 状态，v1 数据集小于 native 全模态，但满足 shape_pick_place_v1 的 RGB + 语言 + 关节 BC 最小闭环。
- Native 仍为 semantic source of truth；LeRobot 目录为可重建派生产物。

## 未决（非 v1 blocker）

- 是否在 manifest 中写入 `effective_config.yaml` 哈希或相机内参（当前 native metadata 未结构化保存）。
- 多 task 扩量后 `meta/tasks.parquet` 与 6 条标准 `task_text` 的维护流程。
- `resume()` 追加 episode 时的 feature 兼容性校验与 CI 回归（合成 + 一条真实 pilot）。
