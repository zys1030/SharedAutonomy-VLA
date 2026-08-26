# 数据集与数据血缘

语言：[English](datasets.md) | 简体中文

> 发布状态：本文记录锁定对照所使用的数据集事实，但数据集和视频当前尚未公开发布。本文暂不声明公开仓库 ID、不可变 revision、checksum 或数据集/媒体许可证。

## 1. 锁定对照数据集

主对照使用两份针对同一任务契约采集、并采用相同映射导出的数据集：

| 数据集 | 采集模式 | Episodes | Frames | FPS | 任务 |
| --- | --- | ---: | ---: | ---: | --- |
| Manual 70 | 人工笛卡尔遥操作和夹爪控制 | 70 | 15,829 | 10 | `shape_pick_place_v1 / block_rotation_rq2` |
| Shared Autonomy 70 | 人工笛卡尔/夹爪控制 + 有界 J6 yaw 辅助 | 70 | 14,212 | 10 | `shape_pick_place_v1 / block_rotation_rq2` |

两侧匹配 episode 预算。由于示教时长不同，frame 数不完全相同。稳定任务文本和公开边界见 [`tasks/shape_pick_place_v1.zh-CN.md`](tasks/shape_pick_place_v1.zh-CN.md)。

## 2. 公开数据契约

Native episode 是语义上的 source of truth。它保留同步观测、机器人状态，相互区分的 `human`、`assist` 和经过安全过滤的 `executed` 动作，以及时间戳、采集模式、effective configuration 和安全元数据。

最终 LeRobot export 包含同步的图像、state、action、task 和可选诊断特征；
稳定字段布局见下文。

### 2.1 LeRobot feature layout

v1 导出使用以下稳定字段顺序。关节位置和关节目标使用 degree；
`gripper.pos` 是 `[0, 1]` 范围内的开合比例；笛卡尔高度及其单步差分使用
米。

| Feature | Shape / dtype | 字段顺序或含义 | 空值与校验行为 |
| --- | --- | --- | --- |
| `observation.state` | `(10,)` `float32` | `joint_1.pos` … `joint_6.pos`、`gripper.pos`、`ee.z`、`ee.dz`、`gripper.time_since_close` | 关节状态、commanded 夹爪比例和 `ee.z` 必须为有限值；缺失或非法时 export 失败 |
| `action` | `(7,)` `float32` | `joint_1.pos` … `joint_6.pos`、`gripper.pos` | 实际下发关节目标和夹爪目标必须存在且为有限值；缺失时立即失败 |
| `observation.images.wrist` | `(480, 640, 3)` RGB video | 腕部相机彩色帧 | 每帧及其 color path 必须存在，且为 `uint8` 和预期形状 |
| `observation.images.external` | `(480, 640, 3)` RGB video | 固定第三视角相机彩色帧 | 每帧及其 color path 必须存在，且为 `uint8` 和预期形状 |
| `task` | 每帧字符串 | `metadata.task_text` | 必须存在，并复制到每个导出帧 |

state 向量中的派生通道为：

- `ee.z`：`observation.ee_position_m[2]`，单位为米；
- `ee.dz`：当前 `ee.z` 减去上一帧的值，第一帧为 `0`；
- `gripper.time_since_close`：距离最近一次开→闭边沿的归一化步数，最多计 20 步；夹爪打开时为 `0`。

最终训练 export 使用 RGB 视频，不导出腕部深度、
`gripper_actual_open_fraction`、`assist_action`、`authority` 或完整末端位姿。
可选的 `diag.*` 列记录 deadman、安全干预、时序和 wall-clock 信息，但不属于
默认策略输入。序列化和校验细节仍以 [`schema.py`](../sharedautonomy/data/schema.py)
与 [`lerobot_export.py`](../sharedautonomy/data/lerobot_export.py) 的实现为准。

## 3. 数据血缘

```text
native episode
    → 结构与媒体校验
    → LeRobot-compatible 导出
    → 数据集元数据核对
    → SmolVLA 训练
    → 配对真机评测
```

导出数据被视为可重建的派生产物：每次重导出使用新的 dataset root，不覆盖已有目录；native episode 始终保留为源记录。机器本地 export manifest、绝对源路径、原始视频和操作者记录不属于公开文档发布范围。

## 4. 纳入与排除范围

只有上述两份 70 episode 数据集支撑锁定的 Manual vs Shared Autonomy 结果。早期 ACT、对齐方块、带把手、颜色绑定、部分采集和已被替代的快照都属于开发历史，不得混入当前对照；只有在有助于解释最终实验时，公开文档才摘要说明其作用。

当前代码发布不包含 raw episode、导出数据集、视频、训练日志、模型权重、机器本地配置或 private 现场备注。

## 5. 发布边界

当前代码/文档发布公开的是数据契约和血缘，而不是原始 episode、导出数据集
或视频。未来如发布外部数据集，其公开元数据必须与本文及
[`results.json`](results.json) 中锁定的任务、数量、features 和限制保持一致。
训练配方见 [`training.zh-CN.md`](training.zh-CN.md)，评测结果见
[`results.zh-CN.md`](results.zh-CN.md)，解释边界见
[`limitations.zh-CN.md`](limitations.zh-CN.md)。
