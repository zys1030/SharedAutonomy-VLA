# 项目背景与系统设计

本文档维护 SharedAutonomy-VLA 的长期稳定说明：研究问题、任务定义、系统架构、数据格式、评测协议与交付物。阶段进度见 [roadmap.md](roadmap.md)；每日执行见 [daily/](daily/)；硬件验证见 [hardware_setup.md](hardware_setup.md)。

---

## 1. 项目简介

本项目研究一个具体问题：

> **局部、结构化且不能独立完成完整任务的共享控制辅助，能否提高人类示教数据的采集效率和质量，并进一步提升 ACT/VLA 等端到端机器人策略的学习效果？**

项目将搭建一个真实机器人数据闭环：

1. 操作者阅读任务指令，并通过 SpaceMouse 遥操作机械臂；
2. 共享控制模块根据人类控制指令推断当前目标，提供局部趋近、安全约束和动作修正；
3. 系统同步记录图像、机器人状态、人类动作、辅助动作和最终执行动作；
4. 使用采集数据训练任务条件 ACT 基线和小型 VLA；
5. 将策略部署到真实机械臂；
6. 在策略失败时由人类接管并完成恢复；
7. 将纠错轨迹重新加入数据集，再次微调模型。

最终目标不是保留人在控制回路中，而是使用共享控制系统作为**高质量训练数据的生产工具**，训练可以根据视觉、语言和机器人状态自主完成任务的策略。

---

## 2. 核心概念：两个策略不在同一层级

### 2.1 数据采集阶段的局部辅助器

局部辅助器只负责：

- 候选目标方向上的局部趋近；
- 工作空间边界和桌面碰撞约束；
- 末端运动平滑和速度限制；
- 接近目标后的夹爪开合辅助；
- 根据意图置信度和人机冲突动态调整辅助强度。

它**不能独立完成完整任务**，也不直接读取任务的真实语言标签。

### 2.2 最终学习得到的 ACT/VLA 策略

最终策略需要独立完成完整任务：

```text
视觉观测 + 任务条件/语言指令 + 机器人状态
                        ↓
              机械臂与夹爪动作序列
```

因此，局部辅助器是数据采集阶段的“脚手架”，ACT/VLA 是拆掉脚手架后执行完整任务的自主策略。

---

## 3. 任务定义

### 3.1 主任务：语言条件的多目标抓取放置

```text
source_object ∈ {red, blue, yellow}
destination   ∈ {left, right}
```

共 6 种基础任务，例如：Pick up the red block and place it in the left region.

**当前现场任务卡**（三色 + 三种形状 + A4 UP/DOWN 放置区、拾取区随机化与 6 条英文指令）见 [`tasks/shape_pick_place_v1.md`](tasks/shape_pick_place_v1.md)。

### 3.2 第一阶段动作空间

```text
action = [Δx, Δy, Δz, gripper_angle]
```

初期保持末端姿态固定。稳定后再考虑姿态控制、更复杂抓取方向、灵巧手低维手型或触觉。

### 3.3 任务随机化

木块位置、放置区、背景光照、未见位置组合、可选中途目标切换。

---

## 4. 硬件与计算资源

### 4.1 机器人平台

- RealMan RM65（RM65-6F，`force_type=6FB`）；
- 二指软夹爪；
- 腕部 RGB-D + 固定第三视角 RGB；
- SpaceMouse；
- 可选：灵巧手与触觉。

第三视角相机在采集与部署期间保持固定安装。若用于共享控制定位，通过桌面单应性映射 XY；若仅作 ACT/VLA RGB 输入，不以精确外参标定为前置。

### 4.2 Windows 控制机

无独显 GPU，承担硬件连接、遥操作、共享控制、安全过滤、episode 记录与策略本地执行权。

### 4.3 Ubuntu GPU 服务器

2 × RTX 3090，64 GB RAM：ACT/VLA 训练与离线验证。采集只需 Windows；推理部署时服务器经有线局域网提供 action chunk，底层控制与安全始终在 Windows 本地。

---

## 5. 系统架构

```mermaid
flowchart LR
    subgraph Windows[Windows Robot Host - no discrete GPU]
        T[Task instruction] --> H[Human operator]
        H -->|SpaceMouse command| I[Intent inference]
        H -->|Human action| A[Arbitrator]

        C[Wrist RGB-D + fixed external RGB] --> O[Synced observation]
        Robot[RM-65B + gripper] --> S[Robot state]
        S --> O

        I --> L[Local assist policy]
        L -->|Assist action| A
        I -->|Belief / confidence| A
        A --> SF[Local safety supervisor]
        SF -->|Executed action| Robot

        O --> R[Dataset recorder]
        H -->|Human action| R
        L -->|Assist action| R
        SF -->|Executed action / authority| R
        T --> R

        O --> PC[Policy client]
        PC -->|Suggested policy action| SF
    end

    R --> D[LeRobot-style dataset]

    subgraph Ubuntu[Ubuntu GPU Server]
        Train[ACT / VLA training and analysis]
        Infer[ACT / VLA inference service]
    end

    D -->|Offline dataset transfer| Train
    PC <-->|Wired LAN: observations / action chunks| Infer

    Infer --> E[Real-robot evaluation]
    E -->|Failure| X[Human intervention]
    X --> CD[Corrective demonstrations]
    CD --> D
```

---

## 6. 数据采集模式

### Manual Teleoperation

`a_executed = a_human` — 无辅助基线、硬件验证、采集质量对照。

### SharedAutonomy

`a_executed = arbitration(a_human, a_assist, intent_belief, safety_state)` — 根据 SpaceMouse、机器人状态、候选目标与一致性推断意图，只提供局部辅助。

### Corrective Demonstration

自主部署失败时人工接管 → 恢复 → 保存纠错 episode → 再次微调。

---

## 7. 数据格式

每条 episode 至少保存任务信息、观测（external/wrist RGB、关节、末端、夹爪）、以及 `human` / `assist` / `executed`（及部署阶段的 `policy`）三路动作与共享控制元数据。训练主要监督 `executed` 或纠错动作，其余保留用于消融。

详见 [decisions/0001-runtime-data-interfaces.md](decisions/0001-runtime-data-interfaces.md) 与 `sharedautonomy/data/schema.py`。

---

## 8. 策略路线

**ACT 基线**：结构化任务条件（`object_id` + `destination_id`）→ action chunk。计划 `ACT-Manual` 与 `ACT-SharedAutonomy`。

**小型 VLA**：自然语言 `task_text` → action chunk。计划 `VLA-Manual`、`VLA-SharedAutonomy`、`VLA-SharedAutonomy-Corrective`。优先可在单卡 24 GB 上 LoRA 微调的模型。

---

## 9. 主要研究问题

- **RQ1**：共享控制是否提高示教采集效率？
- **RQ2**：共享控制数据是否更适合训练策略？
- **RQ3**：纠错数据是否能够修复部署失败？
- **RQ4**：ACT 与 VLA 的能力边界是什么？

指标包括成功率、完成时间、轨迹平滑度、人机冲突、泛化与失败类型等。详见原评测协议扩展说明。

---

## 10. 评测协议

6 个任务组合；测试集含 seen、位置泛化、指令改写、可选组合 holdout 与意图切换。Manual 与 SharedAutonomy 使用相同任务分布与可比采集成本；辅助器不得直接访问真实任务标签。

---

## 11. 仓库结构

```text
sharedautonomy-vla/
├── sharedautonomy/     # 库代码：robot, devices, control, data, policies, ...
├── configs/            # 共享配置；机器本地信息在 configs/local/
├── scripts/            # 采集、训练、真机检查脚本
├── tests/              # pytest（core / extended）
└── docs/               # roadmap, daily, hardware_setup, decisions, ...
```

当前已实现模块与路线图进度以 [roadmap.md](roadmap.md) 为准；目录树中的未实现文件为规划占位。

---

## 12. 安全要求

机械限位与软件工作空间、末端速度与单步位移限制、软件急停常驻、夹爪闭合限制、策略输出经安全过滤器、自动 rollout 需操作员在场。真机细则见 [hardware_setup.md](hardware_setup.md)。

---

## 13. 风险与降级方案

抓取不稳定 → reaching / 单物体抓取；VLA 时间不足 → 保留 ACT 完整结果；辅助过强 → 报告 authority 与冲突；灵巧手耗时 → 二指夹爪为主线；数据不足 → 优先位置变化与纠错轨迹。

---

## 14. 最终交付物

可公开仓库、数据采集接口、Manual/SharedAutonomy 数据集示例、ACT/VLA 脚本、纠错闭环、真机演示与可复现配置。

---

## 15. 非目标（4–6 周周期内）

从零训练大型 VLA、DexVLA/π 复现、高保真数字孪生、大规模人试、复杂长时序任务、完整灵巧手栈、多模型横向大比较。

优先级：

```text
真实机器人数据闭环 > 共享控制数据价值 > ACT 稳定基线 > 小型 VLA > 纠错再训练 > 额外模型和仿真
```

---

## 16. 预期项目结论

不预设 SharedAutonomy 一定优于 Manual。无论结果如何，公开失败案例与限制。

---

## 17. License 与 Citation

License 待确定。稳定版本后补充 Citation；外部框架与数据格式按各自许可证引用。
