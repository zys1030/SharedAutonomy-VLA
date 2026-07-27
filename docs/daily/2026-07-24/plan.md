# 2026-07-24 工作计划（Day 2）

## 今日目标

完成 Cartesian 遥操作采集闭环真机验收，并实现 native episode 的**校验 + 可视化回放**（roadmap「一条命令检查数据」第一版）。

## 完成标准

### 上午 / 已达成（采集与控制）

- [x] 存在可运行的 Cartesian/SpaceMouse runner（至少 mock 路径）；
- [x] 安全链按固定顺序接入：输入新鲜度 → 真实 `dt` 限幅 → 固定姿态 → 工作空间 → IK → 关节过滤；
- [x] mock / dry-run 与真机 motion smoke 通过（含双相机录制、`--no-lock-z`）；
- [x] `EpisodeRecorder` + `load_recorded_episode` 落盘/加载；
- [x] 相关离线测试通过（`pytest -m core`）。

### 下一 session（replay）— 已达成

- [x] `scripts/check_episode.py` 可对 episode 输出结构化摘要（步数、相机覆盖率、`sync_warnings`、动作/EE 统计）；
- [x] `scripts/replay_episode.py` 可逐步回放 wrist + external RGB，并显示 deadman、笛卡尔命令、EE 位置；
- [x] 用 `teleop-motion-smoke-002/episode`（或 tests fixture）跑通检查与回放；
- [x] 为检查逻辑补离线测试；改动后 `pytest -m core` 通过。

## 任务清单

### P0：上午已完成

- [x] Cartesian/SpaceMouse runner + 安全链 + mock/dry-run；
- [x] 真实 IK、UDP 状态、SpaceMouse、CAN-FD、双确认运动门闩；
- [x] 10 Hz XYZ 真机 motion smoke（`--lock-z` / `--no-lock-z`）；
- [x] `EpisodeRecorder` + `--record-dir` + `--enable-cameras` 接入 teleop；
- [x] `ObservationSynchronizer` + 双相机 `CameraSource`。

### P0：下一 session（replay）— 已完成

- [x] `sharedautonomy/data/` 可复用 episode 摘要/校验函数（供脚本与测试共用）；
- [x] `scripts/check_episode.py`：
  - 步数一致性、`wrist`/`external` 覆盖率、`sync_warnings` 计数；
  - `human`/`executed` 动作统计、EE Δxyz 范围；
  - 缺失 `images/` 时明确降级提示；
  - `--json` 输出（P1，同日完成）。
- [x] `scripts/replay_episode.py`：
  - CLI：`episode_dir`，`--step` / 步进、`--hz` 自动播放；
  - 并排显示 wrist / external RGB + 文本状态（deadman、速度、EE、`sync_warnings`）；
  - EE 3D 轨迹子图（P1，同日完成）。
- [x] `tests/` 最小 fixture + check 核心逻辑单测；
- [x] 真机样本：`teleop-motion-smoke-002`（保留 `images/` 时）跑通 check + replay。

### P1：有时间再做

- [x] replay 增加 EE 3D 轨迹子图；
- [x] check 输出 JSON 模式；
- [x] README / `engineering_conventions.md` 补检查与回放命令示例。

## 开始前条件（replay session）

- [x] Conda `sharedautonomy-lr060-cf`；
- [x] 样本 episode：`outputs/runs/teleop-motion-smoke-002/episode`（建议保留 `images/`）或 tests fixture；
- [x] replay **只读磁盘**，不连机械臂。

## 今天不做

- LeRobot export 与 ACT/VLA 训练；
- 批量采集 10 条正式人工轨迹（等 check/replay 稳定后；正式 FOV 待支架）；
- SharedAutonomy 意图推理；第三视角支架安装。

## 计划外已完成（当日追加）

- [x] 第三视角 C920 枚举 + 延迟/双相机并行检查；
- [x] `teleop-smoke-001`（dry-run + 双相机录制）；
- [x] `teleop-motion-smoke-001`（motion + `--lock-z`）、`teleop-motion-smoke-002`（motion + 录制 + `--no-lock-z`）；
- [x] Z 分析：XY-only 步 net dZ ≈ 0.24 mm。

## 待决策

- [x] 采集命令频率默认 **10 Hz**；
- [x] replay 用 matplotlib（已实现；非 OpenCV）；
- [x] check：warnings 仍 exit 0，格式/缺文件 exit 1。
