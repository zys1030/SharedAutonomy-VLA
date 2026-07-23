# 项目路线图

本文档维护 SharedAutonomy-VLA 的阶段目标、当前进度和验收标准。具体的每日执行项与工作记录分别保存在 [`daily/`](daily/) 下的 `plan.md` 和 `log.md` 中。

## 当前状态

- 当前阶段：**Week 1：硬件、数据与最小训练闭环**
- 最近完成的每日计划：[2026-07-23 plan](daily/2026-07-23/plan.md)
- 最近工作日志：[2026-07-23 log](daily/2026-07-23/log.md)
- 下一步主线：把 Cartesian 安全过滤接入 SpaceMouse 控制 runner，并完成 dry-run

## Week 1：硬件、数据与最小训练闭环

- [x] 创建仓库和目录骨架；
- [ ] 接入 RM-65B、夹爪、SpaceMouse、腕部 RGB-D 与固定第三视角 RGB 相机；
  - RM-65B、夹爪、SpaceMouse 和腕部 D435i 已完成首轮接入与检查；
  - 固定第三视角相机尚未购买；
- [x] 确定统一坐标系和第一阶段动作表示；
- [ ] 完成运行时跨设备时间同步；
  - 统一时间戳接口已定义；
  - 尚需接入实际采集 runner；
- [ ] 采集并回放 10 条人工轨迹；
- [ ] 建立数据校验和可视化工具；
- [ ] 用小数据完成 ACT/VLA smoke test。

验收标准：

> 一条命令开始采集，一条命令检查数据，一条命令启动最小训练。

## Week 2：SharedAutonomy 采集器

- [ ] 目标检测和工作空间标定；
- [ ] 候选目标意图推理；
- [ ] 局部趋近辅助器；
- [ ] 动态 authority；
- [ ] 安全过滤和动作限幅；
  - 关节过滤和 Cartesian 纯函数已实现；
  - 尚需接入运行时控制链并完成小范围验收；
- [ ] 同步记录三路动作与 belief。

验收标准：

> Manual 和 SharedAutonomy 均可稳定完成 reaching，并开始抓取放置。

## Week 3：正式数据与 ACT

- [ ] Manual 数据集；
- [ ] SharedAutonomy 数据集；
- [ ] 数据清洗和质量统计；
- [ ] ACT-Manual；
- [ ] ACT-SharedAutonomy；
- [ ] 真机 rollout 与第一版结果。

## Week 4：小型 VLA

- [ ] 语言任务字段；
- [ ] VLA LoRA smoke test；
- [ ] VLA-Manual；
- [ ] VLA-SharedAutonomy；
- [ ] 真机推理；
- [ ] 初步泛化测试；
- [ ] 发布可展示的 GitHub MVP。

## Week 5：纠错数据闭环

- [ ] 策略部署中的人工接管；
- [ ] 保存失败上下文和恢复动作；
- [ ] 采集 corrective episodes；
- [ ] 再次微调；
- [ ] 比较纠错前后性能。

## Week 6：消融、扩展与整理

按优先级选择：

1. 数据规模消融；
2. 等数据量与等采集时间对照；
3. 位置与语言泛化；
4. 意图切换与 ETDL 扩展；
5. 灵巧手低维手型扩展；
6. 仿真接口。

