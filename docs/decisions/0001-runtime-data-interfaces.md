# ADR 0001：运行时数据接口与时间语义

- 状态：Accepted
- 日期：2026-07-23

## 背景

项目需要在 50 Hz 控制循环中融合 RM-65B、SpaceMouse、腕部 RGB-D、外部 RGB、共享辅助器和安全过滤器的数据，并保留可用于训练、回放和审计的时间信息。设备原始时间戳不在同一时钟域，夹爪等设备还存在命令值可读但实际反馈不可用的情况。

## 决策

1. 运行时公共接口使用 `sharedautonomy.data.schema` 中的强类型 `dataclass`，不以松散字典作为模块间接口。
2. Human、Assist 和 Executed action 统一使用 base frame 下的笛卡尔速度：
   - 平移：`linear_velocity_m_s`，shape `(3,)`；
   - 旋转：`angular_velocity_rad_s`，shape `(3,)`；
   - 夹爪：`gripper_target_open_fraction`，范围 `[0, 1]` 或 `None`。
3. 真实 `dt` 记录在 `ExecutedAction.actual_dt_s`。硬件边界将速度积分并转换为安全关节目标，最终发送值记录为 `joint_target_deg`。
4. 跨设备同步以主机 `received_monotonic_ns` 为准。UTC 时间用于运行审计；设备 timestamp、clock domain 和 sequence/frame number 原样保留，不伪造统一设备时钟。
5. 图像运行时格式固定为：
   - RGB：`uint8`、`(H, W, 3)`、RGB channel order；
   - Depth：`uint16`、`(H, W)`，并显式保存 `depth_scale_m_per_unit`。
6. RM 关节位置和目标使用 degree，末端平移使用 metre，末端姿态使用归一化 `xyzw` quaternion。
7. 不可用的真实反馈使用 `None`。外置串口夹爪的命令开度不能写入 `gripper_actual_open_fraction`。
8. schema 只固定数据语义，不把速度上限、工作空间、过期阈值或图像编码写死；这些属于 effective config、safety policy 和 recorder。

## 后果

- 模块边界能够在运行前拒绝错误 shape、dtype、单位范围、NaN/Inf 和不一致的安全状态。
- LeRobot adapter 和后续 recorder 需要显式完成强类型对象与扁平字典/数据集字段之间的转换。
- schema 版本从 `1.0.0` 开始；破坏兼容性的字段语义变更必须提升 major version 并更新本 ADR 或增加新 ADR。
