# SharedAutonomy-VLA Agent Rules

本文件只记录进入仓库后必须遵守的工程边界。项目背景见 `docs/overview.md`；每日工作流见 `docs/daily/README.md`；代码风格与测试见 `docs/engineering_conventions.md`。

## 基本原则

- 默认使用中文解释；代码、变量名、命令、日志和技术术语保持英文。
- 本项目 Windows 端默认 Conda 环境为 `sharedautonomy-lr060-cf`。涉及 LeRobot、RealMan SDK、硬件连通性或项目测试时，优先使用该环境；不要使用已知存在问题的旧环境 `sharedautonomy-lr060`。
- 修改前先阅读**与任务直接相关**的实现与配置；优先做最小、局部、可验证的修改。
- 不主动引入依赖，不做与当前任务无关的重构、重命名或格式化。
- 每次修改后运行与改动相称的测试；若未验证，必须明确说明。
- 仓库内文本文件使用 UTF-8 编码。

## AI 协作约定

- **默认不预读** `docs/roadmap.md`、当日或上一日 `plan.md` / `log.md`，除非用户要求安排当天任务、收尾整理，或明确 @ 了这些文件。
- 问答、评审、方案讨论优先 **Ask**；需要改代码时再用 **Agent**。
- 探索范围优先由用户 `@` 文件或目录限定；无指定时只读直接相关的模块，避免全仓扫描。
- 验证默认 `pytest -m core`；全量 `pytest` 仅在用户要求、改 schema/夹爪、或真机联调前运行。

## 测试策略

- 日常改动和 AI 辅助会话默认只跑核心测试：`pytest -m core`。
- 全量测试在真机联调前、收尾整理或改动 `sharedautonomy/data/`、夹爪适配层时运行：`pytest`。
- 不为 `scripts/` 里的本地辅助函数（如 `summarize_*`）新增单元测试；真机验收依赖 `scripts/test_*.py` 和 `scripts/check_*.py` 手动执行。
- 仅在改动安全、运动门控、teleop runner、IK、实时状态或 HID 映射时新增或修改测试。

## 真机安全与硬件边界

- 机械臂运动必须默认关闭；除非用户明确要求，不得启用运动、夹爪闭合或其他物理执行器。
- Codex 沙箱内的局域网探测或硬件 SDK 连接失败，不能直接判定设备离线。若用户已从本机确认设备可达，应在说明只读范围并取得授权后，在沙箱外使用 `sharedautonomy-lr060-cf` 重试。
- 真实硬件 SDK、相机和 SpaceMouse 依赖必须 lazy import；没有硬件时，普通 import、单元测试和离线工具不应失败。
- 真机接口改动必须保留 mock 或纯函数测试路径，且不得绕过 `sharedautonomy.robot.safety` 的安全检查。
- 新建或调整配置时，不在共享配置或文档中新增机器本地信息；这些信息属于 `configs/local/` 或环境变量。
- 不得批量删除文件或目录；禁止使用 `del /s`、`rd /s`、`rmdir /s`、`Remove-Item -Recurse`、`rm -rf`。

## 数据、配置与输出

- 运行时配置必须可追溯；后续 runner 应保存 effective config、代码版本和必要的设备元数据。
- 高频控制数据写入结构化 episode recorder，不使用普通文本日志逐步打印。
- `data/`、`datasets/`、`recordings/`、`outputs/`、模型权重及本地配置均视为运行产物，除非明确要求，不得修改或删除。

## 文档索引

| 文档 | 何时阅读 |
| --- | --- |
| `docs/overview.md` | 需要项目背景、架构、任务定义时 |
| `docs/roadmap.md` | 规划阶段任务或核对验收标准时 |
| `docs/daily/README.md` | 创建/更新每日 plan 或 log 时 |
| `docs/hardware_setup.md` | 真机联调、安全验证时 |
| `docs/engineering_conventions.md` | 改代码风格、日志、配置约定时 |
