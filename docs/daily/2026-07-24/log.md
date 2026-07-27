# 2026-07-24 工作日志

## 今日计划

参见 [plan.md](plan.md)。

## 完成情况

- [x] P0：Cartesian/SpaceMouse runner、安全链、mock/dry-run、离线测试全部完成；
- [x] P0：真实 IK、UDP 状态、SpaceMouse、CAN-FD、10 Hz XYZ 真机冒烟全部完成；
- [x] P0：episode recorder、同步观测、加载接口完成；
- [x] P0：**check_episode / replay_episode**（库函数 + CLI + 真机样本跑通 + core 单测）；
- [x] P1：EE 3D 轨迹子图、check `--json`、README / `engineering_conventions.md` 命令示例；
- [x] 计划外：第三视角 C920 枚举与延迟基线、双相机并行验证、双相机接入每步 observation；
- [x] 计划外：teleop / motion / 带录制 smoke（`teleop-smoke-001`、`teleop-motion-smoke-001/002`）；
- [ ] 未做：批量正式人工轨迹（10 条）；LeRobot export；第三视角支架安装（预计约两天后到货）。

## 实验与结果

| 项目 | 结果 | 结论 |
| --- | --- | --- |
| Cartesian teleop 10 Hz XYZ | 轴映射 OK；Z 漂移已修；hold-Z 有效 | 可作为采集控制链基础 |
| 外部 C920 单路 30s | ~30 Hz；50 Hz 消费年龄中位 ~16 ms | 软件新鲜度满足 10 Hz 采集 |
| C920 + D435i 双路并行 30s | 两路均 ~30 Hz；无 timeout/掉帧 | 当前 USB 拓扑可继续实验 |
| teleop-motion-smoke-002 | 运动 + 双相机录制 + `--no-lock-z`；XY-only net dZ ≈ 0.24 mm | 采集闭环可验收 |
| check / replay 真机样本 | `teleop-motion-smoke-002`：100 步，wrist 100%、external 99%；replay + EE 3D 正常 | 「一条命令检查数据」第一版达标 |
| `pytest -m core` | 52 passed（含 episode_check） | 离线回归可用 |

## 新结论与决策

- 正式采集命令频率维持 **10 Hz**（`collection_teleop`）；
- 第三视角仅作 RGB 输入时，精确外参/支架不是当前软件前置；**正式 FOV 仍等支架**；
- C920 在当前机器上 OpenCV DirectShow index 应使用 `opencv_index_hint: 3`（勿盲信 PnP 枚举顺序）；
- replay 采用 **matplotlib**（多面板 + 状态文本 + EE 3D）；非 OpenCV 视频窗；
- check：`issues` → exit 1；仅 `warnings`（如缺 `images/`）→ exit 0；支持 `--json`；
- native episode 约 230 MB/10 s（未压缩 `.npy`）；smoke 后可删 `images/`；
- native episode 是语义真相源；LeRobot 可视化需等 export adapter，二者不互相替代。

## 今日理解重点（15–30 分钟）

自测问题见各条目；**完整参考答案在文末「自测参考答案」**。

### 1. Native episode vs LeRobot dataset

- **一句话**：采集落盘用强类型 native 格式；训练侧再经 adapter 转成 LeRobot 扁平数据集。
- **为什么重要**：SharedAutonomy 字段（human/executed、deadman、sync_warnings）在 native 层可完整保留。
- **本项目怎么做**：`EpisodeRecorder` 写盘；`check`/`replay` 只读 native；export 尚未做。
- **代码入口**：`sharedautonomy/data/recorder.py`、`sharedautonomy/data/episode_check.py`。
- **自测问题**：为什么现在不能直接用 LeRobot 自带可视化检查刚采的 episode？

### 2. check = 结构完整性 + 健康度摘要

- **一句话**：硬失败看 `issues`；软提示与统计进 `warnings` / 摘要字段，不自动判定「适合训练」。
- **为什么重要**：采完能快速发现坏盘/缺文件，批量时可用 `--json` 过滤。
- **本项目怎么做**：`check_episode_dir` 不加载 `.npy`；缺 `images/` 仍可文本检查。
- **代码入口**：`scripts/check_episode.py`、`episode_check_report_to_dict`。
- **自测问题**：缺 `images/` 时 exit code 是多少？为什么？

### 3. 采集安全链与双确认运动门闩

- **一句话**：输入新鲜度 → 真实 dt 限幅 → 固定姿态 → 工作空间 → IK → 关节过滤；运动需配置 + CLI 双确认。
- **为什么重要**：默认 dry-run，避免误动真机。
- **本项目怎么做**：`CartesianSafetyFilter` + `resolve_motion_enabled` + teleop runner。
- **代码入口**：`sharedautonomy/control/manual.py`、`scripts/dry_run_cartesian_teleop.py`。
- **自测问题**：只开 `--allow-motion` 但不改 local config，会不会真正动臂？

### 面试式自测

先只读问题，自己作答；答案见文末「自测参考答案」。

1. check 和 replay 分别解决什么问题？为何不合并成一个脚本？
2. 支架未到时，还能推进哪条软件主线？

## 代码与文档变更

- `sharedautonomy/data/episode_check.py`：摘要/校验 + JSON 序列化；
- `scripts/check_episode.py`、`scripts/replay_episode.py`（含 EE 3D）；
- `tests/test_episode_check.py`；
- `README.md`、`docs/engineering_conventions.md`：检查与回放命令；
- 上午已落地：`recorder` / `sync` / cameras / teleop runner / observation 接线（见同日上午记录）。

## 验证

- 真机：`teleop-smoke-001`、`teleop-motion-smoke-001/002`；
- 离线：`check` / `replay` 对 `teleop-motion-smoke-002`；
- `pytest -m core`：52 passed。

## 未完成与阻塞

- 批量正式人工轨迹（10 条）：等 check/replay 稳定后；**正式第三视角 FOV 待支架（约两天后）**；
- LeRobot export adapter 与 ACT/VLA smoke：**未做**（明日主线候选）；
- 支架未到：第三视角固定安装位姿未定。

## 已沉淀到长期文档

- 检查/回放约定 → [`../../engineering_conventions.md`](../../engineering_conventions.md)；
- 命令入口 → [`../../../README.md`](../../../README.md)；
- 硬件与延迟结论仍见 [`../../hardware_setup.md`](../../hardware_setup.md)（第三视角支架安装后补 FOV）；
- 数据接口见 [`../../decisions/0001-runtime-data-interfaces.md`](../../decisions/0001-runtime-data-interfaces.md)。

## 下一工作日建议

1. **主线（不等支架）**：设计并实现 native episode → LeRobot dataset 的 **export adapter** 最小版（至少能从 smoke episode 导出并 `load`）；
2. 用 export 结果做 **LeRobot 侧可视化或 ACT/VLA smoke 前置检查**（能跑多深视环境与时间而定）；
3. **可选**：临时摆放 C920 再练 1–2 条 teleop（不计入正式 10 条）；
4. **支架到货后**：固定第三视角安装 + FOV 验收，再开正式 10 条人工轨迹。

## 自测参考答案

### 理解重点

1. **Native episode vs LeRobot dataset — 为什么现在不能直接用 LeRobot 自带可视化检查刚采的 episode？**
   - 参考答案：刚采的是 native 目录（`metadata.json` + `steps.jsonl` + `images/`），不是 LeRobot dataset schema。LeRobot 工具吃扁平 feature；SharedAutonomy 字段要等 export adapter。现在用 `check_episode` / `replay_episode` 查 native。

2. **check = 结构完整性 + 健康度摘要 — 缺 `images/` 时 exit code 是多少？为什么？**
   - 参考答案：exit **0**（仅 warning）。硬失败是缺 metadata/steps、格式错误、步数不一致等 `issues`。缺图像时动作与 EE 统计仍可用；训练侧视觉不可用，但不把结构检查判死。

3. **采集安全链与双确认运动门闩 — 只开 `--allow-motion` 但不改 local config，会不会真正动臂？**
   - 参考答案：**不会**。运动需 local config `enable_motion=true` **且** CLI `--allow-motion`；缺一即 dry-run / 不发运动命令。

### 面试式自测

1. **check 和 replay 分别解决什么问题？为何不合并成一个脚本？**
   - 参考答案：check 做快速结构校验与统计摘要（可批量、`--json`、exit code），不加载大图也可跑。replay 逐步看 RGB + 状态 + EE 轨迹，给人肉眼验收。合并会让批量/CI 也被迫开 GUI、加载全部图像，职责混在一起。

2. **支架未到时，还能推进哪条软件主线？**
   - 参考答案：native → LeRobot export、训练 smoke、SharedAutonomy 算法/接口复用；临时摆放 C920 可练习采集。支架主要挡正式 FOV 与正式 10 条数据质量，不是全部软件工作的门闩。
