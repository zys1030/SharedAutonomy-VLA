# 2026-07-24 工作计划

## 今日目标

把 Day 1 已实现的 Cartesian 安全纯函数接入 SpaceMouse 控制 runner，完成 mock / dry-run，形成可重复验证的安全控制链。

## 完成标准

- [x] 存在可运行的 Cartesian/SpaceMouse runner（至少 mock 路径）；
- [x] 安全链按固定顺序接入：输入新鲜度 → 真实 `dt` 限幅 → 固定姿态 → 工作空间 → IK → 关节过滤；
- [x] 最大法兰 Z、线速度、线加速度、姿态容差、输入超时有初始保守默认值，并写入配置或文档；
- [x] mock / dry-run 通过；真机 XYZ 运动默认关闭，未经明确授权不执行；
- [x] 相关离线测试通过，并更新 `hardware_setup.md` / 路线图进度。

## 任务清单

### P0：今天必须完成

- [x] 定义并实现 Cartesian/SpaceMouse runner 骨架（复用 Day 1 的 SpaceMouse → deadman → RM-65B 联调经验）；
- [x] 将 `sharedautonomy.robot.safety` 的 Cartesian 纯函数接入控制循环；
- [x] 定稿第一组保守安全参数初值（最大法兰 Z、线速度、线加速度、姿态容差、输入超时）；
- [x] 完成 mock 路径与 dry-run（不发真机运动命令，或明确 `enable_motion=false`）；
- [x] 为安全链和控制步写/补离线单元测试。

### P1：有时间再做

- [x] 接入真实 IK（RealMan `rm_algo_inverse_kinematics` / 离线 `Algo`）；
- [x] 为 Cartesian runner 接入真实 UDP 状态源与只读真机 dry-run 脚本（仍默认 `enable_motion=false`）；
- [x] 接入真实 SpaceMouse（hidapi 线程读取），并在 `enable_motion=false` 下提供 teleop dry-run；
- [x] 接入真实 CAN-FD JointCommander、双确认运动门闩，teleop 默认改用 stamp 工作区（运动仍默认关闭）；
- [x] 极小范围 XYZ 真机复验（10 Hz 跟手；50 Hz 冒烟通过但体感钝；轴映射 OK；Z 漂移已修；采集默认 10 Hz）；
- [x] 设计 episode recorder / 同步观测 / 回放骨架（接口即可，不必完整落地；须遵守 `collection_teleop` 硬约束）。
  - [x] `EpisodeRecorder` + `load_recorded_episode` 落盘/加载；
  - [x] `ObservationSynchronizer` 同步规则与离线测试；
  - [x] 双相机 `CameraSource` 适配器并接入 `ManualCartesianRunner.step()`；
  - [ ] 可视化回放工具（非 P1 硬需求，留后续）。

## 开始前条件

- [x] 使用 Conda 环境 `sharedautonomy-lr060-cf`；
- [x] 真机运动默认关闭；示教器软件急停可用（授权 XYZ 冒烟时临时双确认开启）；
- [x] 先跑离线测试，再决定是否连接硬件。

## 今天不做

- 采集正式人工轨迹或训练 ACT/VLA；
- ~~固定第三视角相机采购或接入~~（相机已到；完成枚举、单路/双路延迟基线、软件接入 observation；支架安装与正式 FOV 仍待后续）；
- 完整 SharedAutonomy 意图推理与动态 authority。

## 计划外已完成（当日追加）

- [x] 第三视角 C920 只读枚举并写入 `configs/local/external_rgb.local.yaml`；
- [x] 外部 RGB / 双相机并行软件新鲜度检查（`scripts/check_external_rgb_latency.py`、`scripts/check_dual_camera_parallel.py`）；
- [x] teleop dry-run 增加 `--enable-cameras`（双相机进每步 `synced_observation`）。

## 待决策 / 已决策

- [x] 第一组保守安全参数：见 `configs/collection/manual_cartesian.yaml` 与 teleop 实测；
- [x] 当天做极小范围 XYZ 真机验收：已授权并完成（10 Hz 主验收 + 50 Hz 冒烟）；
- [x] 采集命令频率：默认 **10 Hz**（非必须 50 Hz）；50 Hz 仅作可选对照。
