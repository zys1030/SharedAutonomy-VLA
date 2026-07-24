# 硬件接入与实测记录

本文记录已经通过真机验证、会影响接口或运行配置的硬件事实，以及可复现的检查方法。机器本地 IP、串口、相机序列号和标定结果不写入本文。

## RM-65B

### 只读连通性检查

检查环境：

- Conda 环境：`sharedautonomy-lr060-cf`
- Python：3.12.13
- LeRobot：0.6.0
- RealMan `Robotic-Arm` SDK：1.1.5
- 仓库入口：`scripts/check_rm65_connection.py`

运行时必须通过命令行或本机配置提供控制器地址，不在本文复制真实地址。该入口使用：

- `enable_motion=False`
- `set_run_mode_on_connect=False`
- `rm_get_joint_degree()` 读取关节角
- `rm_algo_forward_kinematics()` 计算末端位姿

它不发送运动、夹爪、使能或运行模式切换命令。2026-07-23 已在 RM-65B 真机上成功读取六个关节角和末端位姿。

### 状态轮询频率

2026-07-23 使用同一只读连接预热 5 次后，连续调用 `get_observation()` 100 次。当前路径每次包含同步关节角查询和本地正向运动学计算。

| 指标 | 实测值 |
| --- | ---: |
| 样本数 | 100 |
| 平均周期 | 10.611 ms |
| 中位周期 | 10.174 ms |
| P95 周期 | 17.184 ms |
| 最小周期 | 3.055 ms |
| 最大周期 | 25.722 ms |
| 平均有效频率 | 94.25 Hz |

SDK 1.1.5 对 `rm_movej_canfd()` 的说明指出，高跟随模式要求通信周期不超过 10 ms；I 系列有线网口最快可达 2 ms。这是接口能力说明，不代表当前 Python 同步链路能够稳定达到该频率。由于本次 P95 已超过 10 ms，当前链路不能作为稳定 100 Hz 高跟随控制的依据。

### UDP realtime push 频率

2026-07-23 使用 `scripts/check_rm65_realtime_rate.py` 查询控制器已有配置并监听 30 秒，全程未修改 realtime push 配置，也未发送运动命令。控制器已有配置为：

- realtime push 已启用；
- `cycle=1`，按 SDK 定义对应 5 ms；
- 自定义状态项保持关闭。

实测结果：

| 指标 | 实测值 |
| --- | ---: |
| 样本数 | 5989 |
| 回调错误数 | 0 |
| 平均周期 | 5.009 ms |
| 中位周期 | 4.997 ms |
| P95 周期 | 5.656 ms |
| P99 周期 | 6.373 ms |
| 最小周期 | 1.240 ms |
| 最大周期 | 22.466 ms |
| 超过 10 ms | 10 次 |
| 超过 20 ms | 4 次 |
| 平均有效频率 | 199.63 Hz |

第一阶段项目采用 **50 Hz nominal control rate**，为 Python 调度、SpaceMouse、相机同步、安全过滤和数据记录保留余量；状态反馈使用现有约 200 Hz realtime push，并由同步层按控制周期选择最新状态。

当前链路具有足够的平均带宽，但不是硬实时链路：30 秒内仍出现少量超过 20 ms 的回调间隔。因此 runner 必须记录单调时间戳和 state age，并在状态过期时保持或拒绝动作，不能仅依赖平均频率判断数据新鲜度。过期阈值应在安全策略与完整控制循环联调时确定。

该结论确认的是项目控制循环设定与状态反馈能力。CAN-FD 动作连续下发的真机频率和抖动仍需在关节限位、工作空间、急停和低速空载检查通过后验证，不得为频率测试提前启用运动。

### 只读状态请求延迟

2026-07-23 使用 `scripts/check_rm65_read_latency.py` 预热 5 次后采样 100 次。每次采样分别测量同步
`rm_get_joint_degree()` 请求、本地主机正向运动学和两者组成的完整 observation 路径。测试未发送运动命令，也未修改控制器配置。

| 指标 | 中位数 | P95 | P99 | 最大值 |
| --- | ---: | ---: | ---: | ---: |
| 关节状态请求 RTT | 11.136 ms | 20.948 ms | 47.102 ms | 49.650 ms |
| 本地正向运动学 | 0.057 ms | 0.197 ms | 0.295 ms | 0.303 ms |
| 完整 observation 路径 | 11.213 ms | 20.998 ms | 47.175 ms | 49.771 ms |

SDK 连接耗时为 149.708 ms，断开耗时为 0.660 ms。完整 observation 的主要耗时来自同步状态请求，本地正向运动学开销可以忽略。该结果是主机侧 SDK 调用 RTT，不包含控制器内部状态采样年龄，也不代表运动命令到物理响应的延迟。

同步读取 P95 已超过 20 ms，且最大值接近 50 ms，因此 50 Hz runner 不应在每个控制周期阻塞调用同步状态查询。控制路径应使用 realtime push callback 更新线程安全的最新状态缓存；控制周期读取缓存并计算 `state_age_ms`，同步查询只用于低频检查或降级诊断。

2026-07-24 已将上述模式落地为库模块 `sharedautonomy.robot.realtime_state.RealManRealtimeStateSource`，并由 `RealtimeCartesianStateSource` 接入 manual Cartesian runner。只读验证入口：`scripts/dry_run_cartesian_udp.py`（`enable_motion=false`，默认不发运动命令）。正式 stamp 工作区几何仍以本机 local 配置为准；该脚本首轮 bring-up 使用 permissive workspace，只验证 UDP 年龄、FK/IK 与控制步串联。

### 低速 MoveJ 命令响应

2026-07-23 在用户确认机械臂和末端周围净空、操作员在场且软件急停可用后，运行 `scripts/test_rm65_command_response.py`。测试仅使用 J6，以 `1%` 速度执行 `+0.5° / -0.5°` 往返 4 次，并用已有 5 ms 周期 UDP realtime push 检测关节位置首次变化。运动起点约 13.432°，结束后回到约 13.430°。

| 指标 | 中位数 | 最小值 | 最大值 |
| --- | ---: | ---: | ---: |
| `rm_movej()` SDK 调用时间 | 12.482 ms | 10.144 ms | 16.535 ms |
| 命令到 UDP 首次运动 | 236.699 ms | 229.605 ms | 245.481 ms |
| 命令到目标位置 | 1408.803 ms | 870.621 ms | 1435.334 ms |

首次运动以 J6 相对起点变化至少 0.02°，或 UDP 报告速度至少 0.1°/s 为判据；本次实际均由约 0.021°～0.023° 的位置变化触发。UDP 采样周期会给结果引入约 5 ms 的检测量化误差。

4 次命令到运动延迟集中在 230～245 ms，而 SDK 调用本身仅约 10～17 ms。由此推断主要延迟来自低速 MoveJ 的控制器轨迹规划与启动，不是主机 SDK 调用或直连网线 RTT。约 237 ms 相当于 50 Hz 控制循环的近 12 个周期，因此 **低速 MoveJ 不适合作为直接连续遥操作接口**。该结论不等于 `rm_movej_canfd()` 连续透传也具有相同延迟；正式遥操作仍应使用经过安全过滤的 CAN-FD 小步目标，并在完整控制链落地后单独验证。

测试结束时兜底 `rm_set_arm_slow_stop()` 返回 0。随后只读复核确认 J6 静止在约 13.430°，全部关节保持使能，无关节错误、制动错误或控制器系统错误，且无程序在运行。

### 低跟随 CAN-FD 命令响应

同日继续使用 `scripts/test_rm65_command_response.py` 测试 `rm_movej_canfd(follow=False)`。测试仍仅改变 J6，目标缩小为 `+0.2° / -0.2°` 往返 4 次，UDP 首次运动位置阈值为 0.01°。低跟随 CAN-FD 接口没有 `speed_percent` 参数；安全性由极小目标变化、关节限位、超时和最终 slow stop 保证。

| 指标 | 中位数 | 最小值 | 最大值 |
| --- | ---: | ---: | ---: |
| `rm_movej_canfd()` SDK 调用时间 | 0.188 ms | 0.133 ms | 0.595 ms |
| 命令到 UDP 首次运动 | 135.978 ms | 133.348 ms | 146.682 ms |
| 命令到目标位置 | 1514.871 ms | 1459.318 ms | 1520.820 ms |

CAN-FD 调用本身比 MoveJ 快两个数量级，说明主机发送路径不是瓶颈；但 `follow=False` 的控制器低跟随和平滑仍带来约 136 ms 的首次可观测响应，0.2° 目标约需 1.5 秒完成。该结果比低速 MoveJ 的约 237 ms 启动延迟改善约 101 ms，但仍会在遥操作中产生可感知滞后。

按当前单设备结果粗略估算，SpaceMouse 完整状态年龄中位数约 11.8 ms，加上低跟随 CAN-FD 首次运动约 136 ms，操作者输入到机器人状态变化约为 148 ms，尚未包含 safety、IK 和集成循环处理。对于本项目的慢速、局部辅助示教可能可用，但不能仅据单关节小步测试宣称完整遥操作已经达标；最终须以集成 SpaceMouse 链路的主观手感和实测长尾验收。

高跟随 `follow=True` 可能降低控制器平滑延迟，但 SDK 要求发送周期不超过 10 ms。当前项目 nominal 50 Hz 为 20 ms，不满足该要求，因此不得直接启用。若低跟随手感不可接受，应先实现独立稳定的至少 100 Hz 发送器、断流 watchdog、基于实际 `dt` 的速度限制和工作空间过滤，再单独评估高跟随；不能只切换布尔参数。

测试结束时 J6 从约 13.411° 回到约 13.408°，兜底 slow stop 返回 0。随后只读复核确认机械臂稳定、全部关节保持使能、无关节或制动错误、无控制器系统错误，且无程序在运行。

### 高跟随 CAN-FD 流式响应

在用户再次确认运动空间安全后，使用 `scripts/test_rm65_high_follow_response.py` 测试 `rm_movej_canfd(follow=True)`。主机以 200 Hz、5 ms 周期连续发送 J6 三角轨迹：0.1 秒零偏置预热，随后 0.5 秒从 0 平滑增加到 +0.1°，再用 0.5 秒返回起点。每周期目标变化约 0.001°，其他关节保持测试起点。

| 指标 | 实测值 |
| --- | ---: |
| 发送样本 | 221 |
| 平均发送间隔 | 5.001 ms |
| 发送间隔 P95 / P99 | 5.513 / 5.837 ms |
| 最大发送间隔 | 6.189 ms |
| 超过 10 ms | 0 次 |
| SDK 调用中位数 | 0.177 ms |
| SDK 调用 P99 / 最大值 | 0.882 / 1.025 ms |
| tick lateness 中位数 | 0.424 ms |
| tick lateness P99 / 最大值 | 1.070 / 1.627 ms |
| UDP callback 错误 | 0 |
| 变化目标开始到 UDP 首次 0.005° 位移 | 160.450 ms |

主机能够在本次轻载测试中稳定维持 200 Hz，高跟随所要求的“不超过 10 ms”发送周期得到满足。但高跟随没有显示出比低跟随更短的实际位置响应：三角目标约需 25 ms 才增长到 0.005° 检测阈值，扣除这段目标生成时间后，剩余约 135 ms，与低跟随测试的约 136 ms 基本一致。

因此当前约 135 ms 的可观测滞后更可能来自控制器/驱动实际位置响应、极小位移下的执行与测量分辨率，或 UDP 实际位置反馈链路，而不是 Python 调度或 SDK 发送耗时。由于本次只使用极小、低速三角轨迹，不能把该结果外推为所有速度和幅度下的固定延迟，也不能仅凭一次轻载测试证明 Windows 在相机、SpaceMouse 和 recorder 同时运行时仍能稳定维持 200 Hz。

高跟随结束后立即 slow stop 成功；随后只读复核确认 J6 稳定回到约 13.410°，全部关节使能，无关节、制动或控制器系统错误，无程序运行。鉴于高跟随暂未带来可测响应改善，而其调度和 watchdog 要求更严格，第一阶段仍优先使用低跟随模式；只有完整链路手感确实不能接受时，再在集成负载下复测高跟随。

### SpaceMouse 到 J6 低跟随联调

2026-07-23 使用 `scripts/test_spacemouse_rm65_j6.py` 完成第一条受限输入到执行器链路：

```text
SpaceMouse yaw
→ left-button deadman
→ deadzone and input-age check
→ 0.3°/s velocity limit
→ ±0.3° test travel bound
→ joint-limit and per-step safety clipping
→ 50 Hz rm_movej_canfd(follow=False)
→ 200 Hz UDP observation
```

测试只映射 SpaceMouse yaw 到 J6。操作者按住左键并轻微扭转约 1.8 秒，松开左键后循环立即结束并执行 slow stop。结果：

| 指标 | 中位数 | P95 | P99 | 最大值 |
| --- | ---: | ---: | ---: | ---: |
| 50 Hz 控制周期 | 20.048 ms | 20.717 ms | 21.071 ms | 25.107 ms |
| SpaceMouse input age | 8.192 ms | 15.704 ms | 15.948 ms | 15.956 ms |
| UDP robot state age | 1.145 ms | 4.943 ms | 5.415 ms | 5.437 ms |
| CAN-FD SDK 调用 | 0.149 ms | 0.764 ms | 1.004 ms | 1.294 ms |

共执行 90 个 deadman-enabled 控制周期，无过期输入周期、无 UDP callback 错误和 SDK 错误。J6 命令范围约为 13.355°～13.405°，只使用约 0.050° 行程，未触及 ±0.3° 测试边界。松手后 slow stop 返回 0；随后只读复核确认 J6 稳定在约 13.368°，全部关节使能，无关节、制动或控制器错误，无程序运行。

从首次非零 SpaceMouse 命令到 UDP 观测 0.005° 位移为 239.113 ms。该数值包含操作者逐渐扭转、deadzone、0.3°/s 限速积分和机械臂跟踪滞后，不是单独的控制器阶跃响应，不能与前述约 136 ms 的 CAN-FD 阶跃测试直接比较。联调确认的是 deadman、时间新鲜度检查、安全裁剪、50 Hz 调度、CAN-FD 发送和 UDP 反馈能够在真机上共同运行；最终可用性仍需以更大但受控的 J6 行程和后续 XYZ reaching 手感验收。

### 实际型号与六维力能力

2026-07-23 通过 `rm_get_robot_info()` 只读查询，控制器返回：

- `arm_model=RM_65`；
- `arm_dof=6`；
- `force_type=6FB`，SDK 定义为一体化六维力版本；
- 三代控制器。

因此当前真机不是无力传感器的标准末端版本。用户设备标识为 RM65-6F，而控制器采用更具体的 `6FB` 类型标识；软件应以控制器能力查询结果为准。

`scripts/check_rm65_force_sensor.py` 未执行清零、重力标定或运动，连续 20 次调用 `rm_get_force_data()` 均成功返回：

- `Fx / Fy / Fz`，单位 N；
- `Mx / My / Mz`，单位 N·m；
- 原始传感器数据；
- 传感器、工作和工具坐标系下的外力数据。

这确认了末端六维力/力矩可读，可用于后续接触检测、力控、拖动示教和数据记录。官方当前 RM65 参数给出的六维力量程为 200 N / 7 N·m、精度为 ±0.5%FS；实际控制和保护阈值仍须结合末端工具与任务单独验证。

参考资料：

- [睿尔曼 RM65 系列参数及 D-H 模型](https://develop.realman-robotics.com/robot/robotParameter/RM65OntologyParameters/)
- [睿尔曼末端六维力接口 `rm_get_force_data()`](https://develop.realman-robotics.com/robot/apic/classes/force/)

本次空载读取中的补偿后外力仍存在明显静态偏置，说明当前零点、工具质量或重心参数不能直接作为接触阈值。不得为了消除显示值而随手调用 `rm_clear_force_data()`；应在保持当前相机、夹爪和安装结构后，按官方流程完成工具重力与六维力标定，并记录标定姿态、工具配置和结果到本机配置。

外置串口夹爪自身仍无夹持力、开口或电流反馈。腕部六维力传感器测量的是末端整体 wrench，不能未经模型与标定直接解释为指尖夹持力，也不能补出夹爪实际开口。

### 软件限位、示教器急停与实体急停缺口

只读查询得到的控制器软限位与 RM65 官方角度范围一致：

| 关节 | 控制器软限位 |
| --- | ---: |
| J1 | -178°～178° |
| J2 | -130°～130° |
| J3 | -135°～135° |
| J4 | -178°～178° |
| J5 | -128°～128° |
| J6 | -360°～360° |

[RM65 官方本体参数](https://develop.realman-robotics.com/robot/robotParameter/RM65OntologyParameters/)给出的范围与上表完全一致；官方同时给出关节最大角速度 J1/J2 为 180°/s，J3～J6 为 225°/s。项目不采用靠近机械极限的真机撞限位测试。

软件限位 dry-run 分别构造全部关节超过上限和低于下限 10° 的目标，结果在任何 SDK 运动调用前先经过硬限位与 `max_relative_target_deg=1.0` 过滤；`sdk_motion_call_made=false`。现有纯函数限位测试覆盖每周期步长裁剪、关节上下限裁剪和非有限数拒绝，先前 5 项通过；本次在项目指定环境中重新执行基本等价断言通过。该环境未安装 `pytest`，本次未新增依赖。

当前软件限位检查结论：

- **已确认**：官方关节范围与控制器返回值一致；
- **已确认**：目标超过关节上下限时会在 SDK 调用前裁剪；
- **已确认**：单周期最大关节变化为 1°；按 nominal 50 Hz 计算，相当于最多约 50°/s，低于官方关节速度上限；
- **已接入 manual Cartesian runner（离线 mock / dry-run）**：基于真实 `dt` 的末端速度/加速度限制；当前保守初值见 `configs/collection/manual_cartesian.yaml`；
- **已接入 manual Cartesian runner（离线 mock / dry-run）**：固定竖直夹爪的凸四边形 XY 工作区、桌面净空、固定姿态和输入新鲜度检查。

旧 `stamp` 装置的机械臂底座、桌面和夹爪安装未改变，SDK 返回法兰位姿。用户复核的四个边界点（Base 坐标系，单位由 mm 转为 m）依次为 `(-0.456, 0.107)`、`(-0.387, -0.236)`、`(-0.170, -0.420)`、`(-0.150, 0.068)`，构成凸四边形。夹爪固定竖直向下时，旧项目实测法兰到夹爪尖端偏移为 0.178 m。用户确认法兰 Z 为 0.180 m 时夹爪仍与桌面有缝，且抓取任务需要最低到达 0.178 m，因此不额外叠加净空，默认最低法兰 Z 为 0.178 m。几何值保存在本机 `configs/local/rm65_safety.local.yaml`（模板见 `configs/local/rm65_safety.example.yaml`），不会写入共享硬件配置。

Cartesian/SpaceMouse runner 已按“输入时间检查 → 真实 `dt` 速度/加速度限制 → 固定姿态检查 → 工作空间整段检查 → IK → 关节过滤”串到控制步中。当前保守初值：`max_speed_m_s=0.05`、`max_acceleration_m_s2=0.25`、`orientation_tolerance_rad=0.05`、`input_timeout_s=0.1`、`robot_state_timeout_s=0.05`、`max_flange_z_m=0.45`。

2026-07-24 首轮 XYZ 真机验收（5 mm/s、双确认）已通：`commands_sent>0` 且法兰有可见位移。随后发现并修复：

1. **轴映射**：HID 直读需 Compact pyspacemouse LEGACY 的 Y/Z 符号，再套 `vertical_up`；本机手感另需 `base_xy_yaw_deg=90`（Z 正确，XY 转 90°）。
2. **抖动**：真实采集用低跟随 `follow=False` + `radio=50` @ **10 Hz**（与 try_sc 一致）；禁止在 50 Hz 下开高跟随。
3. **Z 漂移（非映射问题）**：命令 `vz=0` / `hold_z` 钉死后，实测 Z 仍漂，是因为 10 Hz 仍用 `max_joint_step_deg=1`（仅 10°/s）重度裁剪 IK，中间关节目标的 FK 高度对不上。修复：关节步进按 **约 50°/s** 随频率缩放（10 Hz → 5°/step），真机 IK 用 **connected arm** 而非离线 Algo。复验后 `delta_z ≈ 0.03 mm`，轴映射确认正确。

### 正式采集时避免再踩坑（硬约束）

后续 manual / SharedAutonomy 采集 runner 必须固定：

- **默认 `control_rate_hz=10`**（2026-07-24 决策）：与 try_sc 手感一致、低跟随 CAN-FD 跟手；相机约 30 FPS，ACT 吃的是 action chunk，不强制 50 Hz 微步。早期文档里的「50 Hz nominal」表示同步/读传感器的余量目标，**不是**采集命令频率的硬性要求。50 Hz 笛卡尔冒烟已通过（Z 稳、无明显抖动），但 `radio=50` 下小步体感偏钝，故不作为默认采集率。
- `max_joint_step_deg ≈ 50/control_rate_hz`（勿把 50 Hz 的 1° 原样搬到 10 Hz）；
- IK：`RealManInverseKinematics.from_arm(connected_arm)`；
- CAN-FD：`follow=false`，`smoothing/radio=50`；
- 步长：try_sc 风格 `move_increment_m`（默认 0.01），不要只用极小 mm/s 却期望明显位移；
- SpaceMouse：HID LEGACY 符号 + `vertical_up` + 本机 `base_xy_yaw_deg=90`；
- 桌面平面 reaching：`lock_z` + `hold_flange_z_m=episode_start_z`（只把 `vz` 置零不够，必须钉绝对高度）；
- 工作区：stamp/`rm65_safety.local.yaml`；运动双确认；`enable_motion` 默认仍为 false。

参考配置块见 `configs/collection/manual_cartesian.yaml` 的 `collection_teleop`。

在用户确认工作区净空、夹爪无物体且示教器在线后，仅对 J6 下发低速测试：

- 起点约 11.182°；
- 目标约 31.182°；
- 速度 1%；
- 用户在示教器点击急停；
- J6 在约 13.433° 停止，总位移约 2.25°，未到达目标；
- 运动开始后约 1.4 秒进入稳定停止；
- 保持静止至 10 秒监测结束；
- 解除示教器急停后，原轨迹未自动恢复。

测试脚本的兜底 slow stop 在第 10 秒监测结束时才发送，因此不是前述停止的原因。停止后控制器仍上电、关节仍使能，且未报告系统或关节错误。

该结果只证明**示教器软件急停能够中断当前轨迹，并且解除后不自动续跑**。停止后控制器仍上电、关节仍使能，也说明它不能替代独立的实体安全装置。

用户已确认当前设备没有通过底座 16 芯接口连接的外置急停按钮盒，因此目前**没有独立实体急停按钮**。控制器只读查询结果为三代控制器；根据睿尔曼官方说明，三代控制器只支持将 **IO1 配置为输入急停复用模式（mode 5）**。四代控制器文档中的第 14 号线专用 `E_STOP` 电路式急停不适用于本机，禁止按四代线号或线色直接接线。

若后续需要增加实体急停，应联系睿尔曼确认三代控制器兼容的外置急停按钮盒及准确接线，或由具备资质的集成人员在确认电气图纸后接入常闭式蘑菇头按钮，并完成 IO1 急停复用配置和真机验证。三代 IO1 复用急停与四代专用电路式下电急停的行为不同，不能直接照搬四代方案。

当前项目决定继续使用已经验证的示教器/控制端软件急停，并接受缺少独立实体急停的剩余风险。运行约束为：

- 急停控件必须在控制端常驻、可见且能立即操作；
- 真机运动时操作员必须在场，不得无人运行；
- 每次运行前检查示教器在线及软件急停可用；
- 网络、控制程序或示教器异常时停止本次运行；
- 当前 `enable_motion` 仍保持 `false`，直至剩余软件限位检查完成；不再以安装实体急停作为项目继续推进的前置条件。

官方依据：

- [外置急停按钮盒：三代控制器仅支持 IO1 急停复用](https://develop.realman-robotics.com/blog/arm/ExternalEmergencyStopButtonBox/)
- [三代控制器 IO 配置：mode 5 为输入急停功能复用](https://develop.realman-robotics.com/robot/json/ioConfig/)
- [三代控制器硬件接口说明](https://develop.realman-robotics.com/robot/quickUseManual/interfaceDescriptionArm/)

可复现入口为：

- `scripts/check_rm65_safety_state.py`
- `scripts/test_rm65_teach_pendant_estop.py`

## 腕部 RGB-D 相机

### 设备、profile 与运行环境

2026-07-23 只读枚举确认腕部相机实际型号为 **Intel RealSense D435i**，不是 D345。Windows 已正常识别 Depth、RGB 和 IMU/HID，SDK 报告当前链路为 USB 3.2，depth scale 约为 `0.001 m/unit`。设备序列号仅在本机配置中保存。

旧 `stamp` 项目的相机序列号与当前连接设备一致，其已验证任务固定使用：

- Color：`640×480 @ 30 FPS`；
- Depth：`640×480 @ 30 FPS`；
- Color 格式：`BGR8`（采集端也可先取 `RGB8` 再转换）；
- Depth 格式：`Z16`；
- 预热：60 帧，约 2 秒。

虽然 D435 的官方最佳深度分辨率为 `848×480`，为复用现有手眼标定与已经完成的 `stamp` 抓取验证，当前项目采用 **640×480 @ 30 FPS**。若后续改为 848×480 或其他 profile，必须重新核对内参、对齐结果和端到端定位误差，不能只缩放旧矩阵。

参考：[RealSense《Tuning depth cameras for best performance》](https://dev.realsenseai.com/docs/tuning-depth-cameras-for-best-performance/)。

仓库默认硬件环境 `sharedautonomy-lr060-cf` 已安装 `pyrealsense2 2.56.5.9235`，并在 Python 3.12.13 下成功读取 D435i。正式相机适配器仍须 lazy import，并保持普通 import 和离线测试不依赖相机 SDK。

### 30 秒双流实测

在预热约 2 秒后，以 `640×480 @ 30 FPS` 同时读取 Depth 与 RGB 30 秒：

| 指标 | 实测值 |
| --- | ---: |
| 同步 frameset | 907 |
| 主机接收频率 | 30.21 Hz |
| Depth 设备时间戳频率 | 29.91 Hz |
| RGB 设备时间戳频率 | 29.98 Hz |
| 等待超时 | 0 |
| Depth frame number 缺口 | 2 |
| RGB frame number 缺口 | 0 |
| 主机接收间隔 P95 | 33.81 ms |
| 主机接收最大间隔 | 78.30 ms |

设备时间戳在本次测试中出现少量非单调修正，而主机 `perf_counter` 时间保持单调。采集接口应同时保存设备 timestamp、frame number 和主机 monotonic 接收时间；控制与同步使用主机 monotonic 时间作为基准，并检测 frame number 缺口，不能把相机设备 timestamp 直接当作统一调度时钟。

当前 Depth/RGB 自动曝光和 RGB 自动白平衡均开启。它们适合接入检查，但正式数据采集前必须在最终工作区照明下确定固定曝光、白平衡或可追溯的自动模式策略，避免视觉分布随运行时间变化。

相机当前可稳定用于双流采集，因此不升级现有固件。只有出现已知固件问题或新 SDK 明确要求时，才另行备份配置并安排升级验证。

### 50 Hz 控制周期 RGB-D 数据年龄

2026-07-23 使用项目 Python 3.12 环境运行 `scripts/check_realsense_latency.py`。相机采用 `640×480 @ 30 FPS` 的同步 Depth Z16 与 Color BGR8，预热 2 秒后持续采样 30 秒；独立采集线程更新最新 frameset，主线程以 50 Hz 读取最新数据。

正式复测结果：

| 指标 | 中位数 | P95 | P99 | 最大值 |
| --- | ---: | ---: | ---: | ---: |
| 主机 frameset 到达间隔 | 33.355 ms | 33.903 ms | 34.767 ms | 64.252 ms |
| 最新 frameset 主机缓存年龄 | 16.651 ms | 31.889 ms | 33.053 ms | 45.303 ms |
| Depth timestamp 到控制周期消费年龄 | 16.868 ms | 32.062 ms | 33.253 ms | 45.118 ms |
| Color timestamp 到控制周期消费年龄 | 26.157 ms | 41.440 ms | 42.670 ms | 54.029 ms |
| Depth/Color timestamp 绝对偏差 | 9.314 ms | 10.144 ms | 11.056 ms | 20.565 ms |
| 50 Hz tick lateness | 0.432 ms | 1.006 ms | 2.562 ms | 17.053 ms |

本轮收到 904 个同步 frameset，Depth 和 Color 均无 frame number 缺口，`wait_for_frames` 无 timeout。主机到达间隔曾出现一次约 64 ms 长间隔，随后短间隔补帧，说明 USB、SDK 或主机调度仍可能造成短时积压。前一轮 30 秒测试还观察到一次约 131 ms 的到达长尾和少量 frame number 缺口；虽然正式复测未重现该幅度，运行时仍须按 `frame_age_ms` 判断数据新鲜度，不能只依赖平均 30 FPS。

Depth 与 Color 均报告 `timestamp_domain.system_time`。Color timestamp 到 Python 收到 frameset 的中位差约 9.102 ms；Depth 中位差为 -0.176 ms，出现轻微负值，说明设备到主机的 system-time 映射不具备严格外部时钟精度。表中的 timestamp age 适合做软件链路新鲜度指标，不应解释为严格的曝光/光子到应用物理延迟。若实验需要真实光学延迟，仍须使用受控 LED、屏幕时间码或高速摄影建立外部基准。

### 手眼标定迁移

旧项目的 640×480 RGB/Depth 内参、Depth→Color 外参和 `T_cam2ee` 已迁移到本机 Git 忽略配置：

- `configs/local/wrist_realsense.local.yaml`
- `configs/local/wrist_realsense_calibration.local.yaml`

共享仓库只提供不含真实序列号和标定值的 `configs/local/wrist_realsense.example.yaml`。迁移值已在旧 `stamp` 抓取任务中经过端到端实机使用，但其 RGB 标定元数据仍为 `valid_image_count=0`、`reprojection_error_px=0.0`，且旧投影路径未使用 RGB 畸变系数。因此当前结论是“可复用的实机基线”，不是完整可审计的标定报告。

运行时应优先读取当前 active profile 的内参，并复用 `T_cam2ee`。只要腕部相机或末端支架发生拆装、松动或相对位姿变化，就必须重新做手眼验证；不能因分辨率保持 640×480 而继续假设外参有效。

固定第三视角相机尚未购买，本次未检查，README 中双相机总检查项保持未完成。

## SpaceMouse

### 设备与驱动边界

2026-07-23 只读 HID 枚举确认设备为 **3Dconnexion SpaceMouse Compact**。频率检查直接使用 `hidapi` 读取原始 HID report，不连接机器人，也不调用遥操作或动作接口。

旧 `stamp` 实现把共享内存循环配置为 200 Hz，但其循环每次读取当前状态后固定 sleep 5 ms；这个数值是软件轮询/缓存写入频率，不代表 HID 硬件报告频率。另外，旧环境中的 `pyspacemouse 1.1.4` 通过 `easyhid` 加载时因缺少 `hidapi.dll` 导入失败，不应直接作为本项目已可用的驱动路径。

本项目默认硬件环境 `sharedautonomy-lr060-cf` 已安装 `hidapi 0.15.0`，并在 Python 3.12.13 下成功直接读取设备。后续正式适配器仍必须 lazy import，并保留无 SpaceMouse 时可运行的离线测试。

### 原始 HID 报告频率

人工持续推拉或旋转帽体，连续读取 10 秒：

| 指标 | 实测值 |
| --- | ---: |
| 测量时长 | 10.005 s |
| 原始 HID reports | 1250 |
| 总报告频率 | 124.90 Hz |
| 平均报告间隔 | 8.006 ms |
| 中位报告间隔 | 8.001 ms |
| P95 报告间隔 | 8.217 ms |
| P99 报告间隔 | 8.299 ms |
| 最大报告间隔 | 16.013 ms |
| 空读取 | 0 |
| report ID 1（平移） | 625 |
| report ID 2（旋转） | 625 |

平移与旋转 report 交替到达，因此两类数据各约 **62.5 Hz**。项目应把约 125 Hz 记录为原始 USB report rate，把约 62.5 Hz 记录为完整 6-DoF 状态的保守刷新频率。

第一阶段 50 Hz nominal control loop 可以按周期读取最近一对平移/旋转状态，不需要把控制循环提升到 125 Hz。适配器必须分别记录平移、旋转和按键的最近更新时间，并在设备断开或输入过期时输出零动作；不能用 200 Hz 软件轮询时间戳伪造新的硬件样本。

可复现检查入口为 `scripts/check_spacemouse_rate.py`。测试时必须持续施加输入，否则静止设备可能不持续发送 motion report，测得的是操作者活动占空比而不是设备报告能力。

### 50 Hz 控制周期输入年龄

2026-07-23 扩展 `scripts/check_spacemouse_rate.py`，使用独立 HID 读取线程接收 report，并由主线程按 50 Hz 读取最新状态。人工持续交替平移和旋转帽体 15 秒，结果如下：

| 指标 | 实测值 |
| --- | ---: |
| 原始 reports | 1873 |
| report ID 1 / ID 2 | 936 / 937 |
| 原始 report 频率 | 124.80 Hz |
| 最新任一 motion report age 中位数 | 3.681 ms |
| 最新任一 motion report age P95 | 6.310 ms |
| 完整 6-DoF state age 中位数 | 11.765 ms |
| 完整 6-DoF state age P95 | 14.315 ms |
| 完整 6-DoF state age P99 | 14.776 ms |
| 完整 6-DoF state age 最大值 | 21.514 ms |
| 50 Hz tick lateness 中位数 | 0.458 ms |
| 50 Hz tick lateness P95 | 1.119 ms |
| 50 Hz tick lateness P99 | 2.917 ms |
| 50 Hz tick lateness 最大值 | 9.315 ms |

完整 6-DoF state age 定义为控制周期采样时，最近一条平移 report 和最近一条旋转 report 中较旧者的年龄。该指标衡量进入控制器的数据新鲜度，不包含操作者开始施力到设备生成 HID report 的物理延迟。

本次正式结果使用项目硬件环境 `sharedautonomy-lr060-cf`、Python 3.12.13 和对应的 `hid.cp312`。此前使用旧项目 Python 3.10 环境得到的 HID 频率相近，但其 `time.sleep` 调度抖动明显更大，已不作为正式 control tick 结果。后续 runner 仍须记录每周期实际 `dt`，不能假设固定 20 ms。

### Codex 沙箱网络注意事项

Codex 的受限执行沙箱可能阻止访问本机局域网设备，表现为 `Test-NetConnection` 失败或 SDK 报 `socket connect err`。这不能单独证明机械臂离线。

排查顺序：

1. 使用 `sharedautonomy-lr060-cf`，不要使用旧环境 `sharedautonomy-lr060` 或其他项目虚拟环境。
2. 核对 Python、LeRobot 和 RealMan SDK 版本。
3. 若用户已通过网页示教器等本机工具确认设备可达，在明确只读操作范围并取得授权后，从沙箱外重跑检查。
4. 只有沙箱外连接仍失败时，再检查控制柜、网卡网段、端口和本机配置。

不要为了验证连通性而发送运动命令、切换运行模式或启用执行器。

## 外置串口软夹爪

### 设备与协议识别

2026-07-23 在不发送任何串口数据的前提下完成设备枚举和 3 秒被动监听：

- Windows 将设备识别为 WCH CH9102 USB 串口；
- 串口配置为 115200 baud、8 data bits、no parity、1 stop bit；
- 被动监听未收到主动上报数据，后续运动命令也未返回应答字节；当前按 command-only 设备处理。

现有驱动的 `0x7B ... BCC ... 0x7D` 帧格式与《MS42DC 步进电机驱控一体用户手册》一致：

- 控制模式 `0x02` 为相对位置控制；
- 方向 `0` 为逆时针，`1` 为顺时针；
- 细分值使用 32；
- 角度和速度均放大 10 倍后以 16-bit unsigned integer 传输；
- 单次相对角度最大为 6553.5°。

参考资料：

- [MS42DC 步进电机驱控一体用户手册](https://www.scribd.com/document/808897253/MS42DC%E6%AD%A5%E8%BF%9B%E7%94%B5%E6%9C%BA%E9%A9%B1%E6%8E%A7%E4%B8%80%E4%BD%93%E7%94%A8%E6%88%B7%E6%89%8B%E5%86%8C)
- 旧项目中的实机标定记录：完整闭合行程为 1872°，约 5.2 圈。

1872° 是当前夹爪机构的实测完整闭合行程，不是协议数值上限。当前实现使用相对转动命令，不能解释为具有固定零点的绝对 `0–1872°` 位置轴。

### 受控开合验证

2026-07-23 在用户确认夹爪附近净空后，使用旧项目原始 `GripperController`、原 Python 环境和原运行参数完成一次：

```text
open 1800° at 20 rad/s
→ close 1872° at 20 rad/s
→ open 1800° at 20 rad/s
```

人工观察确认完整“张开—闭合—张开”循环成功，最终已达到机械最大开口附近。此前从接近最大开口状态继续发送 360° 张开命令没有可见位移，不能据此推断通信失败。

随后在完全张开状态使用仓库 `SerialSoftGripper` 做无初始化等待对照：打开串口后立即发送 360°、2 rad/s 的闭合命令，人工观察到明显闭合。由此确认串口连接后不需要固定 2 秒初始化等待；旧项目中的等待只是保守延时，不应进入当前适配器默认路径。

完整循环和无等待对照命令均未收到串口应答，因此软件仍无法据此判断到位状态或真实位置。

### 目标与反馈接口结论

当前公开协议、旧项目驱动、被动监听和完整开合测试均未提供夹爪自身可用的多圈实际角度、实际开口度、夹持力或电流反馈。电机内部具有位置闭环和堵转保护，不代表上位机能够读取实际位置。机械臂腕部另有六维力传感器，但它测量末端整体 wrench，不是夹爪内部反馈。

第一阶段采用以下接口边界：

- 上层目标使用归一化 `target_open_fraction`，`0.0` 表示闭合，`1.0` 表示张开；
- 硬件适配层保存 `commanded_travel_deg`、方向、速度、命令时间戳和原始响应；
- 明确暴露 `position_feedback_available=False`；
- 不生成或记录伪造的 `actual_angle_deg` / `actual_open_fraction`；
- 归一化中间位置在完成零点、累计误差和实际反馈验证前不承诺可用，第一阶段只使用完全张开/完全闭合两个状态。

RealMan 控制器标准夹爪的 `1–1000` position 与 `rm_get_gripper_state().actpos` 不适用于该外置串口夹爪。仓库中两种适配器必须保持独立，外置夹爪不得从 RealMan 标准夹爪状态伪造反馈。

完整张开和闭合行程已经验证。动作完成时间仍只有旧项目使用的固定等待经验值，尚未通过位置反馈或外部测量得到精确延迟。
