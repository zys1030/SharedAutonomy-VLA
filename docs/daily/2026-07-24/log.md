# 2026-07-24 工作日志

## 今日计划

参见 [plan.md](plan.md)。

## 完成情况

- [x] P0：Cartesian/SpaceMouse runner、安全链、mock/dry-run、离线测试全部完成；
- [x] P1：真实 IK、UDP 状态、SpaceMouse、CAN-FD、10 Hz XYZ 真机冒烟全部完成；
- [x] P1：episode recorder、同步观测、加载回放接口完成；
- [x] 计划外：第三视角 C920 枚举与延迟基线、双相机并行验证、双相机接入每步 observation；
- [ ] 未做：正式人工轨迹采集、EpisodeRecorder 接入 teleop 落盘、可视化回放。

## 实验与结果

| 项目 | 结果 | 结论 |
| --- | --- | --- |
| Cartesian teleop 10 Hz XYZ | 轴映射 OK；Z 漂移已修；hold-Z 有效 | 可作为采集控制链基础 |
| 外部 C920 单路 30s | ~30 Hz；50 Hz 消费年龄中位 ~16 ms | 软件新鲜度满足 10 Hz 采集 |
| C920 + D435i 双路并行 30s | 两路均 ~30 Hz；无 timeout/掉帧 | 当前 USB 拓扑可继续实验 |
| 双相机 observation 接线 | `pytest -m core` 46 passed | 每步 `synced_observation` 含 wrist + external |

## 新结论与决策

- 正式采集命令频率维持 **10 Hz**（`collection_teleop`）；
- 第三视角仅作 RGB 输入时，精确外参/支架不是当前前置；
- C920 在当前机器上 OpenCV DirectShow index 应使用 `opencv_index_hint: 3`（勿盲信 PnP 枚举顺序）；
- 双相机并行未见明显带宽退化，可在此 USB 条件下推进。

## 代码与文档变更

- `sharedautonomy/control/manual.py`：每步附加 `synced_observation`；
- `sharedautonomy/devices/cameras.py`：RealSense + UVC 后台采集；
- `sharedautonomy/control/observation.py`：本地配置加载与 synchronizer 构建；
- `sharedautonomy/data/recorder.py`、`sharedautonomy/data/sync.py`：episode 与同步观测；
- `scripts/dry_run_cartesian_teleop.py`：`--enable-cameras`；
- 诊断脚本：`check_external_rgb_latency.py`、`check_dual_camera_parallel.py`（`scripts/`，手动执行）。

## 验证

- `pytest -m core`：46 passed；
- 真机：10 Hz XYZ 冒烟、双相机并行检查（无运动采集闭环）；
- 未验证：`--enable-cameras` 与 UDP teleop 长时间联跑、正式 episode 落盘。

## 未完成与阻塞

- EpisodeRecorder 尚未挂到 teleop 采集命令（缺“一条命令开始采集”）；
- 数据校验/可视化工具未建；
- 支架未到，第三视角固定安装位姿与 FOV 未定；
- LeRobot export adapter 未做。

## 已沉淀到长期文档

- 硬件与延迟结论仍见 [`../../hardware_setup.md`](../../hardware_setup.md)（第三视角条目待补一轮正式记录）；
- 数据接口见 [`../../decisions/0001-runtime-data-interfaces.md`](../../decisions/0001-runtime-data-interfaces.md)。

## 下一工作日建议

1. **P0**：`dry_run_cartesian_teleop.py --enable-cameras` 短跑真机，确认每步两路图像与 sync warnings；
2. **P0**：把 `EpisodeRecorder` 接到 teleop runner（`--record-dir` / 开始结束键），跑 1 条短 episode 并 `load_recorded_episode` 验证；
3. **P1**：最小数据检查脚本（步数、相机覆盖率、动作统计）；
4. 支架到后再做固定安装与 FOV 验收。
