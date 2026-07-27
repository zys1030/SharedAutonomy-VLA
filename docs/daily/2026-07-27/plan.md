# 2026-07-27 工作计划（Day 3，续）

## 今日目标

硬件与任务定义就绪：**支架 + FOV + 双相机 + 夹爪 teleop + 拾取区工作区** 已验收；**shape_pick_place_v1** 采集协议（metadata + 录制 + check/replay）已用真机 pilot 验证。**下午主线：native → LeRobot export 最小版**（自 7/25 顺延）。

> 2026-07-25 ~ 2026-07-26 休息。

## 完成标准

- [x] 第三视角支架安装固定，FOV 在线工具确认覆盖工作区；
- [x] 换 USB 转接后双相机可同时打开（`opencv_index_hint` 以本机实测为准）；
- [x] 任务卡 [`tasks/shape_pick_place_v1.md`](../tasks/shape_pick_place_v1.md)：拾取区、6 条指令、`object_id` 用颜色；
- [x] 串口夹爪接入 teleop（SpaceMouse 右键边沿，stamp 参数），真机开合已测；
- [x] `rm65_safety.local.yaml` 工作区多边形扩展至覆盖拾取区（`check_workspace_pick_zone.py` PASS）；
- [x] 夹爪 / 工作区 / 采集相关改动后 `pytest -m core` 通过；
- [x] **Pilot 协议验收**：录制 → `check_episode` → `replay_episode`；CLI `--source-object` / `--destination` / `--task-text` 写入 metadata 正常；
- [x] **Export 最小版**：至少 1 条现有 native episode 导出为 LeRobot dataset 并可加载/浏览；
- [x] 收工前整理 `log.md`（含 pilot 结论与 export 摘要）。

## 任务清单

### P0：已完成（硬件 + 任务 + 软件接入）

- [x] 第三视角支架安装；
- [x] FOV 验收（在线预览，不必再单独 motion smoke）；
- [x] 双相机并行验证（换线后已测；**不必复跑**）；
- [x] 编写并迭代任务卡（拾取区 `x∈[-0.42,-0.15]`、`y∈[-0.09,0.17]`；place 区暂不写 base 坐标）；
- [x] `--enable-gripper` + `gripper_serial.local.yaml`，真机验证通过；
- [x] 扩展 `polygon_xy_m` 解决 y≈0.17 拾取区越界。

### P0：已完成（pilot / 采集协议）

- [x] **Ready pose**：`[0, 0, 90, 0, 90, 0]` + go-to-ready（`rm_movej_canfd(radio=50)`）；
- [x] 采集命令链路：`--enable-cameras --record-dir … --task-id shape_pick_place_v1` + 运动/夹爪双确认；
- [x] `red` → `up`：≥2 条，拾取区初始位姿不同（视为 **Phase 1** 协议已覆盖）；
- [x] `red` → `down`：1 条，`check` / `replay` 正常；
- [x] 现场调参：`min_flange_z_m`（158 mm）、`working_open_fraction`（半开夹爪）、松爪结束 episode、XY 2× / Z 逐轴限速；
- [x] teleop：`--source-object` / `--destination` → `metadata.json` 的 `source_object` / `destination`。

### P0：剩余（今日下午 — export）

- [x] 梳理 native episode 字段与 LeRobot 0.6 目标 feature 的映射（动作、双相机 RGB、metadata）；→ [`docs/decisions/0002-lerobot-export-mapping.md`](../decisions/0002-lerobot-export-mapping.md)
- [x] 实现 **export adapter 最小版**（`sharedautonomy/data/lerobot_export.py` + `scripts/export_lerobot_dataset.py`）；
- [x] 用已有 pilot（001/002/003）跑通 export；
- [x] 验证：LeRobot `load` 或等价方式能打开导出结果；记录命令与已知限制（见 ADR 0002 + `export_manifest.json`）；
- [x] （可选）export 后做一次 LeRobot 侧可视化 smoke（`lerobot-dataset-viz` + 三帧 Python 抽查 PASS）。

### P1：顺延 / 非今日

- [ ] 第三视角 / 工作区结论摘要写入 `hardware_setup.md`（不含本地机密）；
- [ ] 正式训练集扩量（多色 × 多区 × 随机位姿 × N 条/条件）：等 export + 训练 smoke 后再开；
- [ ] 完整 ACT/VLA 训练 smoke。

## 开始前条件

- [x] Conda `sharedautonomy-lr060-cf`；
- [x] 支架、双相机、夹爪、扩展后工作区；
- [x] `check_episode` / `replay_episode`；
- [x] 场景按任务卡摆好（三色块 + A4 UP/DOWN）。

## 今天不做

- **不为 pilot 凑条数**（不要求 6 组合全覆盖、不要求 10 条；现有样本仅验证协议，不用于训练）；
- 为覆盖黄/蓝 × up/down 而额外采轨迹（metadata 解析已验证即可）；
- SharedAutonomy 意图推理 / authority；
- 完整 ACT/VLA 训练（export 最小版之后另排）；
- 重复已做过的双相机基线 / 专用 motion smoke。

## 已决策

- [x] **任务**：`shape_pick_place_v1`（黄/红/蓝 × UP/DOWN）；采集语义见任务卡 Phase 0→3；
- [x] **Pilot 验收标准**：录制链路 + metadata 字段正确 + `check`/`replay` 通过；**不**以条数或 6 组合全覆盖为 gate；
- [x] **已采样本**：`red→up`（≥2，Phase 1 位姿变化）、`red→down`（1）；足够关闭 pilot 阶段；
- [x] **FOV**：已通过，不必为验收再录 smoke；
- [x] **place 区**：Phase 1 不写 base 矩形，靠视觉识别 A4 半区；
- [x] **Ready pose**：`[0, 0, 90, 0, 90, 0]`；夹爪半开由 `gripper_serial.local.yaml` `working_open_fraction` 控制；
- [x] **下午主线**：native → LeRobot export（7/25 顺延项，今日推进）。

## 参考链接

- 任务卡：[`docs/tasks/shape_pick_place_v1.md`](../tasks/shape_pick_place_v1.md)
- Export 映射 ADR：[`docs/decisions/0002-lerobot-export-mapping.md`](../decisions/0002-lerobot-export-mapping.md)
- 导出命令：`python scripts/export_lerobot_dataset.py <episode_dirs...> --out-root outputs/datasets/shape_pick_place_v1_v001`
- 工作区检查：`python scripts/check_workspace_pick_zone.py`
- 夹爪模板：`configs/robot/gripper_serial.example.yaml` → `configs/local/gripper_serial.local.yaml`
- 7/24 export 候选说明：[`2026-07-24/log.md`](2026-07-24/log.md)「下一工作日建议」
