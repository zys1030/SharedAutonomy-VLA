# `shape_pick_place_v1` 任务定义

语言：[English](shape_pick_place_v1.md) | 简体中文

本文档定义 `shape_pick_place_v1` 的稳定公开任务契约。最终公开实验使用 `block_rotation_rq2` 变体；具体实验网格和结果见 [`results.zh-CN.md`](../results.zh-CN.md)。现场精确几何、ready pose、标定和操作者 checklist 保持私有。

## 1. 任务身份

| 字段 | 公开值 |
| --- | --- |
| `task_id` | `shape_pick_place_v1` |
| `evaluation_variant` | `block_rotation_rq2` |
| 指令 | `Pick up the red cube and place it in the UP region.` |
| 物体 | 红色方块 |
| 目标区域 | `UP` |

## 2. 目标区域语义

`UP` 是固定任务表面上可由视觉识别的目标区域名称。它是任务空间标签，不是机器人 base 坐标系的正 Z 方向。

运行时接口可以表示其他 destination，但 `DOWN` 不属于公开 `block_rotation_rq2` 评测。纸张尺寸、放置坐标、相机标定和场地布局等精确值不在公开任务契约中维护。

## 3. 任务契约

每个 episode 包含一次按指令执行的抓取放置尝试。任务记录保留以下信息：

- `collection_mode`：`manual` 或 `shared_autonomy`；
- 任务文本：上面的标准英文指令；
- 观测与动作细节：由运行时数据接口定义。

任务层面的成功，指抓住并抬起目标物体，将其释放在命名目标区域内，并且没有因安全中止而结束。具体硬成功判定和失败类型规则由 [`results.zh-CN.md`](../results.zh-CN.md) 统一维护。

## 4. 公开 episode 元数据

```yaml
task_id: shape_pick_place_v1
evaluation_variant: block_rotation_rq2
task_text: "Pick up the red cube and place it in the UP region."
collection_mode: manual  # manual | shared_autonomy
```

机器可读的评测字段维护在 [`results.json`](../results.json) 与 [`evaluation_records.csv`](../evaluation_records.csv) 中。

## 5. 公开边界

公开任务契约不声称策略具备连续位姿、物体、语言、相机、光照或机器人泛化能力。精确坐标和机器专属运动参数属于本机配置与私有硬件备注。任何真机执行仍必须经过项目 motion gate 和安全监督器。
