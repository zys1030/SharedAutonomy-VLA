# 任务卡 v1：多形状语言条件 Pick-and-Place

本文档是 **Phase 1–3 采集与评测** 的可执行任务说明。项目背景、范式定位与研究问题见 [`../overview.md`](../overview.md)（§1.2）；机器本地坐标微调写在 `configs/local/`（不入库）。

本任务为**可控试验床**：语言条件分色/分区抓放，用于验证「Manual / SharedAutonomy 采数 → ACT/VLA」闭环；不追求在本任务上超越规则基线（色块识别 + 笛卡尔控制）。

**状态**：2026-07-27 首版，对应现场布置（三色块 + A4 UP/DOWN 放置区）。

---

## 1. 任务摘要

操作者阅读一条英文指令，用 SpaceMouse 遥操作机械臂：**从左侧拾取区抓起指定物体，放到右侧 A4 纸的 UP 或 DOWN 半区**。

```text
object_id     ∈ {yellow, red, blue}   # 颜色与形状一一对应，id 只用颜色
destination_id ∈ {up, down}
```

共 **6** 种条件组合。每条 episode 只完成 **一个** `(source, destination)` 对。

---

## 2. 现场物体与区域

### 2.1 可操作物体（固定种类，不替换）

| `object_id` | 外观 | 尺寸（标称） | 备注 |
| --- | --- | --- | --- |
| `yellow` | 黄色等边三角形 | 边长 68 mm | 尖角抓取可能更难，单独记成功率 |
| `red` | 红色圆形 | 直径 60 mm | |
| `blue` | 蓝色矩形 | 40 mm × 60 mm | 长边朝向采集时可随机 |

### 2.2 放置区（固定地标，整阶段不挪动）

- **载体**：一张 A4 白纸，贴在桌面**右侧**（操作者视角）。
- **分区**：纸面中间一条**黑线**，将 A4 分为两半：
  - **UP**：黑线上方半区（纸上印有 `"UP"` 标注）
  - **DOWN**：黑线下方半区（纸上印有 `"DOWN"` 标注）
- **语义**：`up` / `down` 指 A4 上的半区，与机器人 base 坐标系的 Z 无关。
- **要求**：第三视角（external）能同时看到拾取区与整张 A4（含 UP/DOWN 文字或黑线）。

### 2.3 拾取区（每 episode 随机）

- **位置**：桌面**左侧**，与 A4 放置区分离，三者物体均落在拾取区内。
- **随机量**（每条 episode 重新采样）：
  - 各物体中心在拾取区矩形内的 `(x, y)`
  - 各物体绕竖直轴的 `yaw`（建议 `0° / 90° / 180° / 270°` 四选一，或连续 `0°–360°`）
- **约束**：
  - 任意两物体轮廓最小间距 ≥ **30 mm**
  - 物体不超出拾取区边界，不压到 A4 或桌边
  - 物体底面贴桌，不叠放
- **不随机**：A4 位置、相机支架、光照（Phase 1 保持固定）

### 2.4 拾取区矩形（机器人 base 系）

| 参数 | 值（base 系，单位 m） | 说明 |
| --- | --- | --- |
| `pick_x_min` | `-0.42` | 拾取区 x 下界 |
| `pick_x_max` | `-0.15` | 拾取区 x 上界 |
| `pick_y_min` | `-0.09` | 拾取区 y 下界 |
| `pick_y_max` | `0.17` | 拾取区 y 上界 |
| `min_separation_m` | `0.03` | 物体中心最小间距 |

首次 pilot 前仍建议慢速 dry-run 核对可达性；若需微调，只改 `configs/local/`。

**工作区多边形**：`configs/local/rm65_safety.local.yaml` 里的 `polygon_xy_m` 按**夹爪尖端** XY 校验（不是法兰）。拾取区更新后运行 `python scripts/check_workspace_pick_zone.py` 确认四角在 polygon 内；模板见 `configs/robot/rm65_safety.example.yaml`。

### 2.5 放置区（A4）与 base 坐标

**Phase 1–3 不必在任务卡里写 A4 的 base 系矩形。**

- ACT/VLA 通过 **图像** 识别 A4、黑线与 UP/DOWN 标注，放置目标是**半区语义**，不是固定 `(x, y)`。
- A4 贴好后**保持不动**即可；整阶段视为固定地标。
- 仅在以下情况需要你把放置区位置告诉项目（记入 `configs/local/`，不入共享文档）：
  1. **共享控制**用桌面单应性做 XY 辅助（需角点或中心标定）；
  2. 想在软件里做**放置落点是否越界**的自动检查（可选）；
  3. 后续做 **A4 平移泛化**实验，需要记录训练时的锚点位置。

若以上都不急，**现在不用量 place 区域坐标**。

---

## 3. 六条英文指令

采集前向操作者展示 **其中一条**（屏幕/打印/口头均可）；episode 元数据须记录对应的 `object_id` 与 `destination_id`。

| # | `object_id` | `destination_id` | `task_text`（标准英文） |
| --- | --- | --- | --- |
| 1 | `yellow` | `up` | Pick up the yellow triangle and place it in the UP region. |
| 2 | `yellow` | `down` | Pick up the yellow triangle and place it in the DOWN region. |
| 3 | `red` | `up` | Pick up the red circle and place it in the UP region. |
| 4 | `red` | `down` | Pick up the red circle and place it in the DOWN region. |
| 5 | `blue` | `up` | Pick up the blue rectangle and place it in the UP region. |
| 6 | `blue` | `down` | Pick up the blue rectangle and place it in the DOWN region. |

**VLA 可选改写**（评测泛化用，训练初期可只用上表标准句）：

- Place the yellow triangle in the upper area. / … lower area.
- Put the red circle on the UP side of the paper.
- Move the blue block to DOWN.

**ACT 条件字段**：`object_id` + `destination_id`（与上表一一对应）。

---

## 4. 动作与采集协议

| 项 | 约定 |
| --- | --- |
| 动作空间 | `[Δx, Δy, Δz, gripper]`，末端姿态固定（与 overview §3.2 一致） |
| 控制频率 | 10 Hz（`collection_teleop`） |
| 相机 | 腕部 RGB-D + 固定 external RGB，双路写入 observation |
| 每条时长 | 建议 **15–40 s** 或 **~50–150 步**（先 pilot，再定上限） |
| 模式 | Phase 1 仅 **Manual**（`a_executed = a_human`）；SharedAutonomy 后续接入 |
| 运动门闩 | local `enable_motion=true` **且** CLI `--allow-motion` |
| 夹爪 | SpaceMouse **右键边沿**触发开合（stamp 同款：open 1800° / close 1872° @ 20 rad/s）；采集加 `--enable-gripper`，配置见 `configs/robot/gripper_serial.example.yaml` |

### 4.1 Ready pose（每条 episode 开始前）

配置见 `configs/collection/manual_cartesian.yaml` → `ready_pose`：

| 项 | 值 |
| --- | --- |
| `joint_position_deg` | `[0, 0, 90, 0, 90, 0]` |
| `gripper_open_fraction` | `1.0`（完全张开） |
| `canfd_follow` / `canfd_smoothing` | `false` / `50`（与 try_sc `rm_movej_canfd` 一致） |
| `settle_s` | `2.0`（发令后等待，避免 teleop 立刻 hold 顶掉到位） |
| 姿态 | 夹爪竖直向下；到位后 teleop 冻结该姿态的 RPY |
| 净空 | 夹爪尖端距桌面 **> 150 mm** |
| XY | 大致在拾取区上方即可；相对中心横向偏 **3–5 cm** 可接受 |

**自动到位**（对齐 try_sc 的 `move_to_init_on_connect` / `reset`）：开运动时 teleop **默认**先发一次 `rm_movej_canfd(follow=False, radio=50)` 到 Ready pose，再进入 SpaceMouse 循环。可用 `--no-go-to-ready` 跳过。腕部相机不必看见物体；场景靠第三视角。

---

## 5. 成功 / 失败判定

**成功**（满足全部）：

1. 指令指定的物体被夹起并离开拾取区；
2. 物体被放入 A4 上正确的 **UP** 或 **DOWN** 半区；
3. 松爪后物体主要留在该半区内（允许轻微滑动，人眼 + `replay_episode` 可判）；
4. 无碰撞、无越出软件工作区、无急停。

**失败 / 重录**：

- 抓错物体、放错半区、物体掉出桌面、中途卡死、episode 明显中断。
- 是否重录：pilot 阶段建议 **失败即重录**；正式扩量时同一条件可保留有限失败样本作分析。

---

## 6. 随机化与泛化（采集 vs 部署）

| 元素 | 采集 | 部署 / 测试 |
| --- | --- | --- |
| 三物体位姿 | 拾取区内 **随机** | 拾取区内 **未见过** 的位置与朝向 |
| A4 放置区 | **固定**（Phase 1–3） | 先固定；数据够多后再测整纸平移 2–3 cm |
| 相机 / 支架 | **固定** | **固定** |
| 光照 | Phase 1 **固定** | 后期可加轻微变化 |
| 指令 | 6 条标准句 | 可加改写句（§3） |

模型应学习 **「看物体 + 看 UP/DOWN 区 + 听指令」**，而不是记忆单一 `(x, y)`。测试时必须使用 **held-out 物体位姿**。

---

## 7. 分阶段 rollout（建议顺序）

| 阶段 | 范围 | 目的 |
| --- | --- | --- |
| **Phase 0** | 单色单区，如 `red` → `up`，物体位置固定 | 验证抓取 + 放置 + 录制链路 |
| **Phase 1** | 单色单区，拾取区内 **随机位姿** | 验证位置变化下仍能完成 |
| **Phase 2** | 3 色 × 1 区 或 1 色 × 2 区 | 引入选择或区域歧义 |
| **Phase 3** | 完整 6 组合 + 拾取区随机 | 与本文档任务卡一致 |
| **Phase 4** | + SharedAutonomy 对照采集 | 研究主线 |

当前建议：**从 Phase 0 或 Phase 1 开第一条 pilot**，不要一步采满 6×N 条。

---

## 8. Episode 元数据（每条必记）

写入 episode / `metadata.json`（字段名以实现为准）：

```yaml
task_id: shape_pick_place_v1
object_id: red                   # yellow | red | blue
destination_id: up               # up | down
task_text: "Pick up the red circle and place it in the UP region."
collection_mode: manual        # manual | shared_autonomy
phase: pilot                     # pilot | train | eval
# 可选：三物体初始 pose、拾取区采样种子、是否成功
```

---

## 9. 与 overview 原设计的关系

| overview 原字段 | 本任务卡 |
| --- | --- |
| `red, blue, yellow` 色块 | `object_id` 用 **颜色**；现场形状唯一（三角 / 圆 / 矩形），英文指令里保留形状词便于 VLA |
| `left, right` 放置区 | 改为 A4 上 **`up, down`** 半区 |
| 6 组合 | 仍为 **3 × 2 = 6** |

研究问题、共享控制与 ACT/VLA 路线不变；仅任务几何与指令与现场布置对齐。

---

## 10. 今日可执行 checklist（第一条 pilot）

- [ ] 用慢速 dry-run 确认拾取区建议矩形全程可达
- [ ] external 画面同时覆盖拾取区与 A4 UP/DOWN
- [ ] 选 Phase 0 或 Phase 1 的一条指令（建议从 `red` → `up` 开始）
- [ ] 摆放物体 → 采集 → `check_episode` → `replay_episode` 目视验收
- [ ] 在当日 log 记录：拾取区矩形是否需微调、哪种形状最难抓
