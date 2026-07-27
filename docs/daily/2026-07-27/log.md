# 2026-07-27 工作日志

## 今日计划

参见 [plan.md](plan.md)。

## 完成情况

- [x] P0：第三视角支架 + FOV + 双相机 + 夹爪 teleop + 拾取区工作区（上午，见 plan 已勾项）；
- [x] P0：**shape_pick_place_v1** pilot 协议验收（录制 → check → replay；metadata 字段正确）；
- [x] P0：**native → LeRobot export 最小版**（ADR 0002、映射模块、CLI、三条 pilot 导出、加载抽查）；
- [x] P0：LeRobot 可视化 smoke（`lerobot-dataset-viz` + 帧 0/520/1039 Python 抽查）；
- [x] 计划外：修复 `test_serial_gripper_teleop` mock 缺 `config`；export CLI Windows 并行编码提示；
- [ ] P1 顺延：`hardware_setup.md` 第三视角/工作区摘要；正式 10 条扩量；ACT/VLA 训练 smoke。

## 实验与结果

| 项目 | 结果 | 结论 |
| --- | --- | --- |
| Pilot 样本 | 001/002/003：`check_episode` 均 PASS；双相机 100% 覆盖 | 采集协议可关闭 pilot 阶段 |
| Pilot 字段统计 | `joint_target_deg` 0% null；`gripper_actual` 100% null（串口无回读） | export 用 commanded / executed target |
| LeRobot 导出 | `outputs/datasets/shape_pick_place_v1_v001`：3 episodes / 1040 frames / 10 Hz | export 最小版达标 |
| 加载抽查 | 帧 0/520/1039：双路 RGB CHW float32 [0,1]；state/action 7 维正常 | 视频解码与映射正确 |
| Rerun 可视化 | 时间轴 ~40s（episode 0）；Selection Duration ~7.6s 为写 `.rrd` 墙钟，非 episode 时长 | 数据完整，UI 元数据易误解 |
| `pytest -m core` | export 单测 7 passed；全量 core 曾 1 失败（gripper mock），已修 | 收工前 gripper teleop 测试 5 passed |

## 新结论与决策

- **Export 映射**见 [ADR 0002](../decisions/0002-lerobot-export-mapping.md)：`action` = `joint_target_deg` + gripper；`observation.state` 夹爪用 `gripper_commanded_open_fraction`；双 RGB MP4；v1 不导深度；
- **`diag.*`** 写入 parquet，默认不进训练输入；`safety_intervened` 等保留分析用，export 不裁帧；
- LeRobot `timestamp` = `frame_index / fps`（合成），真实墙钟在 `diag.wall_time_s`；Rerun 显示 1970 年起算属正常；
- Windows 并行视频编码日志会交错，数据不受影响；可用 `--no-parallel-encoding`；
- Pilot 不以 6 组合全覆盖为 gate；现有 3 条仅验证协议，**不用于训练**；训练扩量等 ACT smoke 之后。

## 今日理解重点（15–30 分钟）

自测问题见各条目；**完整参考答案在文末「自测参考答案」**。

### 1. Export adapter：native 仍是真相源

- **一句话**：LeRobot dataset 是训练侧派生格式；native episode 保留完整 SharedAutonomy 语义，export 只做字段映射与视频编码。
- **为什么重要**：export 丢字段或改语义后无法从 LeRobot 反推 human/executed、安全介入等；调试与复现仍以 native 为准。
- **本项目怎么做**：`lerobot_export.py` 逐步读 `steps.jsonl` + `images/*.npy`；`check`/`replay` 仍只读 native；重导出用新 `_v00N` 目录。
- **代码入口**：`sharedautonomy/data/lerobot_export.py`、`scripts/export_lerobot_dataset.py`。
- **自测问题**：为什么 v1 export 默认不裁帧，即使 `safety_intervened` 比例很高？

### 2. 7 维 state / action 的夹爪语义

- **一句话**：state 用实测关节角 + **commanded** 夹爪开度；action 用当步 **executed** 关节目标 + gripper target。
- **为什么重要**：串口夹爪无位置回读，`gripper_actual_open_fraction` 在 pilot 中 100% null；误用会导致训练标签缺失或编造。
- **本项目怎么做**：ADR 0002 §3；`names` 对齐 `JOINT_KEYS` + `gripper.pos`；`joint_target_deg` null 时 fail-fast。
- **代码入口**：`docs/decisions/0002-lerobot-export-mapping.md`、`sharedautonomy/data/lerobot_export.py`。
- **自测问题**：`observation.state` 第 6 维和 `action` 第 6 维分别来自哪个 native 字段？

### 3. `diag.*`：记录但不进默认训练输入

- **一句话**：deadman、safety、真实 dt、墙钟时间写入 parquet 的 `diag.*` 列，不用 `observation.` 前缀，避免被策略当成 state 输入。
- **为什么重要**：BC/ACT smoke 只需图像 + state + task；诊断列留作事后分析，训练 filter 另做。
- **本项目怎么做**：export 全量逐步对应；`timestamp` 用 `frame_index/fps` 合成，真实墙钟在 `diag.wall_time_s`。
- **代码入口**：ADR 0002 §4、`build_lerobot_features`。
- **自测问题**：Rerun 时间轴显示 1970 年或 Selection Duration 很短，是否说明 episode 被截断？

### 4. Pilot 验收 vs 训练数据 gate

- **一句话**：pilot 验的是「录制链路 + metadata + check/replay」；不要求 6 色×方向全覆盖，也不以条数作为关闭条件。
- **为什么重要**：避免为凑数采低质量轨迹；协议通了再扩量，且扩量前还要 ACT smoke。
- **本项目怎么做**：3 条（`red→up`×2、`red→down`×1）已关 pilot；`source_object`/`destination`/`task_text` 经 CLI 写入 metadata。
- **代码入口**：`docs/tasks/shape_pick_place_v1.md`、`docs/daily/2026-07-27/plan.md`「已决策」。
- **自测问题**：现有 3 条 pilot 能直接用于正式训练吗？还差什么 gate？

### 面试式自测

先只读问题，自己作答；答案见文末「自测参考答案」。

1. `create()` 与 `resume()` 分别适用于什么导出场景？
2. 若要在训练里只保留 deadman 按住的有效段，应在哪一层做 filter？

## 代码与文档变更

- `sharedautonomy/data/lerobot_export.py`：`build_lerobot_features`、`iter_native_frames`、`export_lerobot_dataset`；
- `scripts/export_lerobot_dataset.py`：多 episode 导出 CLI（`--resume`、`--no-parallel-encoding` 等）；
- `tests/test_lerobot_export.py`（core）；
- `docs/decisions/0002-lerobot-export-mapping.md`；
- `tests/test_serial_gripper_teleop.py`：`_RecordingGripper.config`；
- `docs/daily/2026-07-27/plan.md`、`docs/roadmap.md`（本 log）。

## 验证

- 真机：pilot 001/002/003 录制与 check/replay（上午）；
- 导出：`python scripts/export_lerobot_dataset.py` 三条 pilot → `shape_pick_place_v1_v001`；
- 离线：`pytest tests/test_lerobot_export.py -m core`；`lerobot-dataset-viz` + Python 三帧抽查；
- 未做：完整 ACT/VLA 训练 smoke；Hub push。

## 未完成与阻塞

- 正式训练集扩量（多色 × 多区 × N 条/条件）：刻意等 export + 训练 smoke；
- `hardware_setup.md` 硬件结论摘要：P1 顺延；
- `test_serial_gripper_teleop` 修复后未再跑全量 `pytest -m core`（建议下次收工前补一次）。

## 已沉淀到长期文档

- Native → LeRobot 字段表与训练策略 → [ADR 0002](../decisions/0002-lerobot-export-mapping.md)

## 常用命令（备忘）

```powershell
# 导出（三条 pilot 一次写入）
python scripts/export_lerobot_dataset.py `
  outputs/runs/shape-pick-place-pilot-001/episode `
  outputs/runs/shape-pick-place-pilot-002/episode `
  outputs/runs/shape-pick-place-pilot-003/episode `
  --out-root outputs/datasets/shape_pick_place_v1_v001

# 加载抽查
python -c "from lerobot.datasets.lerobot_dataset import LeRobotDataset; ds=LeRobotDataset('local/shape_pick_place_v1', root='outputs/datasets/shape_pick_place_v1_v001'); print(ds.num_episodes, ds.num_frames)"

# LeRobot 可视化（会弹 Rerun；写 .rrd 用 --save 1）
lerobot-dataset-viz --repo-id local/shape_pick_place_v1 --root outputs/datasets/shape_pick_place_v1_v001 --episode-index 0 --mode local
```

## 下一工作日建议

1. **ACT/VLA 训练 smoke**：用 `shape_pick_place_v1_v001`（3 episodes）在 LeRobot 0.6 上跑通最小训练配置（不求效果，只求 pipeline）；
2. 训练 smoke 通过后，再开 **正式 10 条** 或按任务卡 Phase 扩量采集；
3. （可选）`hardware_setup.md` 补充第三视角 FOV / 工作区结论；
4. 收工前跑 `pytest -m core` 确认全绿。

## 自测参考答案

### 理解重点

1. **Export adapter — 为什么 v1 export 默认不裁帧？**
   - 参考答案：native 与 LeRobot 保持逐步一一对应，避免 export 阶段隐式改变数据分布；`safety_intervened` 高等现象留 `diag.*` 供分析。若只训有效操作段，在训练脚本或单独 filter 阶段基于 `diag.deadman_active` 等处理，不在 v1 export 默认启用。

2. **7 维 state / action — 第 6 维分别来自哪？**
   - 参考答案：state[6] = `observation.gripper_commanded_open_fraction`；action[6] = `executed_action.gripper_target_open_fraction`。不用 `gripper_actual_open_fraction`（串口无回读，pilot 全 null）。

3. **`diag.*` — Rerun 时间轴异常是否说明截断？**
   - 参考答案：**不一定**。LeRobot `timestamp` 是 `frame_index/fps` 的合成值，Rerun 可能按 Unix 纪元显示；Selection Duration 是写 `.rrd` 的墙钟，不是 episode 时长。应以 `num_frames`、`fps` 和 native `check` 步数为准。

4. **Pilot 验收 — 3 条能直接正式训练吗？**
   - 参考答案：**不能**。它们只验证采集与 export 协议；还缺 ACT/VLA 训练 smoke、正式扩量（多色×方向×位姿×N 条/条件）及数据质量 gate。现有 v001 仅作 pipeline 打通用。

### 面试式自测

1. **`create()` 与 `resume()` 分别适用于什么导出场景？**
   - 参考答案：`create()` 要求输出 root 不存在，用于新建 dataset；同一 root 追加 episode 或断点续写用 `resume()`（CLI `--resume`）。重导出全新版本应换新 `_v00N` 目录再 `create()`，避免覆盖。

2. **训练里只保留 deadman 有效段，在哪层 filter？**
   - 参考答案：**训练脚本或独立 filter 工具**，读取已导出 LeRobot parquet 的 `diag.deadman_active`（及可选 `diag.safety_intervened`），不在 v1 export adapter 里默认裁帧。
