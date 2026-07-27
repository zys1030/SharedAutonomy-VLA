# 2026-07-25 工作计划（Day 3）

## 今日目标

启动 **native episode → LeRobot dataset** 的最小 export 路径，使 smoke episode 可被 LeRobot 工具链加载（为「一条命令启动最小训练」铺路）。支架预计约两天后到货，**今日不做正式第三视角安装与正式 10 条采集**。

## 完成标准

- [ ] 存在可运行的 export 入口（脚本或库函数）：输入 native `episode/`，输出可被 LeRobot 0.6.0 识别的最小 dataset 目录或等价产物；
- [ ] 用 `teleop-motion-smoke-002`（或 tests fixture）至少跑通一次 export + load 冒烟；
- [ ] 明确记录：导出了哪些特征、丢弃/暂缓了哪些 SharedAutonomy 字段（human/assist、sync_warnings 等）；
- [ ] 相关离线测试或手动验收通过；改动后 `pytest -m core` 通过；
- [ ] 更新 `roadmap.md` / 当日 log 中与 export 相关的进度说明。

## 任务清单

### P0：今天必须完成

- [ ] 调研并钉死最小 LeRobot 0.6.0 dataset 布局与必需 feature（对照本机已装版本，避免凭记忆）；
- [ ] `sharedautonomy/data/`（或等价模块）实现 **export 纯函数/适配器**（native `RecordedEpisode` → LeRobot frames/features）；
- [ ] `scripts/` 薄 CLI：`episode_dir` → 输出目录；只读源 episode，不连机械臂；
- [ ] 用 fixture 或 `teleop-motion-smoke-002` 跑通 export + load；
- [ ] 文档：在 README 或 `engineering_conventions.md` 记一条 export 命令示例（可很短）。

### P1：有时间再做

- [ ] 导出后用 LeRobot 自带可视化看一眼图像/动作（可选，不阻塞）；
- [ ] 设计 ACT/VLA smoke 的数据量与命令草稿（先不跑完整训练）；
- [ ] 临时摆放 C920 练习 1 条 teleop（**不**计入正式 10 条）。

## 开始前条件

- [ ] Conda `sharedautonomy-lr060-cf`（含 LeRobot 0.6.0）；
- [ ] 可读样本：`outputs/runs/teleop-motion-smoke-002/episode` 或 tests fixture；
- [ ] export **只读源盘**；不启用真机运动。

## 今天不做

- 第三视角支架安装与正式 FOV（货未到）；
- 批量正式 10 条人工轨迹；
- SharedAutonomy 意图推理 / authority；
- 完整 ACT/VLA 训练（除非 export 极顺利且时间充裕，再另开小范围 smoke）。

## 待决策

- [ ] 第一版 export 动作特征用 **executed** 还是 **human**（建议先 executed，与「实际发生」对齐）；
- [ ] 图像：wrist + external 双路如何映射到 LeRobot camera keys；
- [ ] 输出放 `outputs/datasets/` 还是 `outputs/runs/<run_id>/lerobot/`（建议 run 旁或统一 datasets 根，当天定一条并写进文档）。
