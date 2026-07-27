# 2026-07-27 工作计划（Day 3，续）

## 今日目标

硬件与任务定义就绪：**支架 + FOV + 双相机 + 夹爪 teleop + 拾取区工作区** 已验收；钉死 **shape_pick_place_v1** 任务卡；下一步是 **Phase 0/1 pilot 采集**（非训练集级别的「正式 10 条」）。

> 2026-07-25 ~ 2026-07-26 休息。7/25 的 LeRobot export 仍顺延。

## 完成标准

- [x] 第三视角支架安装固定，FOV 在线工具确认覆盖工作区；
- [x] 换 USB 转接后双相机可同时打开（10s 轮询 100% 命中；`opencv_index_hint: 3` 仍有效）；
- [x] 任务卡 [`tasks/shape_pick_place_v1.md`](../tasks/shape_pick_place_v1.md)：拾取区、6 条指令、`object_id` 用颜色；
- [x] 串口夹爪接入 teleop（SpaceMouse 右键边沿，stamp 参数），真机开合已测；
- [x] `rm65_safety.local.yaml` 工作区多边形扩展至覆盖拾取区（`check_workspace_pick_zone.py` PASS）；
- [x] 夹爪 / 工作区相关代码改动后 `pytest -m core` 通过（54 passed）；
- [ ] **Phase 0/1**：至少 1 条 **pilot** episode（建议 `red` → `up`，reach 或真抓）+ `check` / `replay`；
- [ ] 收工前整理 `log.md`（FOV、任务定义、工作区扩展、夹爪接入摘要）。

## 任务清单

### P0：已完成（硬件 + 任务 + 软件接入）

- [x] 第三视角支架安装；
- [x] FOV 验收（在线预览，不必再单独 motion smoke）；
- [x] 双相机并行验证（换线后已测；**不必复跑**）；
- [x] 编写并迭代任务卡（拾取区 `x∈[-0.42,-0.15]`、`y∈[-0.09,0.17]`；place 区暂不写 base 坐标）；
- [x] `--enable-gripper` + `gripper_serial.local.yaml`，真机验证通过；
- [x] 扩展 `polygon_xy_m` 解决 y≈0.17 拾取区越界。

### P0：剩余（采集 pilot）

- [x] 定 **Ready pose**：`[0, 0, 90, 0, 90, 0]` + 夹爪张开；开运动时 teleop **默认 `rm_movej_canfd(radio=50)` 自动到位**（与 try_sc 相同，`--no-go-to-ready` 可关）；
- [ ] Phase 0 或 Phase 1：`red` → `up`，物体先**固定摆放**亦可；
- [ ] 采集命令：`--enable-cameras --record-dir … --task-id shape_pick_place_v1 --no-lock-z` + 运动/夹爪双确认（默认会先 go-to-ready）；
- [ ] 对该条 pilot 跑 `check_episode` / `replay_episode`（代替单独 motion smoke）。

### P1：有时间再做

- [ ] native → LeRobot export 最小版（自 7/25 顺延）；
- [ ] 拾取区内随机位姿 + 更多 pilot 条数；
- [ ] 第三视角 / 工作区结论摘要写入 `hardware_setup.md`（不含本地机密）。

## 开始前条件

- [x] Conda `sharedautonomy-lr060-cf`；
- [x] 支架、双相机、夹爪、扩展后工作区；
- [x] `check_episode` / `replay_episode`；
- [ ] 场景按任务卡摆好（三色块 + A4 UP/DOWN）。

## 今天不做

- 把今日采集称为「可训练正式集」或凑满 10 条；
- SharedAutonomy 意图推理 / authority；
- 完整 ACT/VLA 训练；
- 重复已做过的双相机基线 / 专用 motion smoke。

## 已决策

- [x] **任务**：`shape_pick_place_v1`（黄/红/蓝 × UP/DOWN）；采集语义见任务卡 Phase 0→3；
- [x] **「正式采集」**：今日只做 **pilot / 协议验证**；训练可用数据需任务协议 + export + 足够条数；
- [x] **FOV**：已通过，不必为验收再录 smoke；
- [x] **place 区**：Phase 1 不写 base 矩形，靠视觉识别 A4 半区；
- [x] **Ready pose**：`[0, 0, 90, 0, 90, 0]`，尖端净空 >150 mm，允许相对拾取区中心偏 3–5 cm；
- [ ] 首条 pilot 是否今日完成（收工前定）。

## 参考链接

- 任务卡：[`docs/tasks/shape_pick_place_v1.md`](../tasks/shape_pick_place_v1.md)
- 工作区检查：`python scripts/check_workspace_pick_zone.py`
- 夹爪模板：`configs/robot/gripper_serial.example.yaml` → `configs/local/gripper_serial.local.yaml`
