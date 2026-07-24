# 工程约定

本文定义 SharedAutonomy-VLA 当前阶段的代码风格、日志、配置和输出约定。它服务于真实机器人数据闭环的可维护性与可追溯性；具体硬件参数、数据集 schema、训练协议和实验结果在对应功能实际落地后另行记录。

## 适用范围与阶段边界

- 当前 Windows 开发与硬件验证默认使用 Conda 环境 `sharedautonomy-lr060-cf`，不使用已知存在问题的旧环境 `sharedautonomy-lr060`。
- 已在该环境验证：Python 3.12.13、LeRobot 0.6.0、RealMan `Robotic_Arm` 1.1.5、pyserial 3.5。
- 采用轻量工具链：Ruff、pytest、标准库 `logging` 与 YAML 配置。
- 暂不引入 Hydra、OmegaConf、Pydantic、WandB、MLflow、全局日志服务或预提交框架。
- 暂不定义最终 LeRobot dataset schema、训练超参数、实验指标或批量实验协议；这些内容必须以实际接口和 smoke test 为依据。

## 代码风格

### 格式与静态检查

- 使用 `pyproject.toml` 中的 Ruff 规则：Python 3.12、行宽 110。
- 新增或修改 Python 文件后，优先运行：

  ```powershell
  python -m ruff check sharedautonomy tests scripts
  python -m ruff format --check sharedautonomy tests scripts
  ```

- 修复格式问题时可以使用 `python -m ruff format`；不做无关的全仓格式化。
- 使用 pytest 验证行为。硬件不可用时，应优先覆盖纯函数、mock 和配置解析路径。
- 测试分两层：`core`（安全与 teleop 关键路径，日常默认）和 `extended`（schema、夹爪等）。日常与 AI 辅助改动默认 `pytest -m core`；全量在真机联调前或收尾时运行。
- 不为 `scripts/` 本地辅助函数写单元测试；详见 `AGENTS.md` 测试策略与 AI 协作约定。
- 项目背景与架构见 `docs/overview.md`；README 仅作导航入口。

### 编写规则

- 代码、变量、模块、日志信息和 public API docstring 使用英文；说明性 Markdown 文档默认使用中文。
- 为 public class、function、配置对象和跨模块数据结构写类型标注。
- 配置、观测、动作与 episode 元数据等数据对象优先使用 `dataclass`；只有确有必要时才使用松散的 `dict[str, Any]`。
- 单位必须显式体现于字段名：距离使用 `_m`，角度使用 `_deg`，时间使用 `_s`，频率使用 `_hz`，单调时间戳使用 `_ns`。
- 代码中的硬件实现只放在 `sharedautonomy.robot`、`sharedautonomy.devices` 等适配层。采集、策略、数据与评测层不得直接调用供应商 SDK。
- 硬件或可选依赖必须 lazy import。普通模块 import、单元测试和离线工具不能因缺少相机、SpaceMouse 或机器人 SDK 而失败。
- 安全检查应靠近运动命令执行点，且必须可测试；不得绕过 `sharedautonomy.robot.safety`。

## 日志、事件和 episode 数据

日志用于理解程序运行和硬件状态；episode 数据用于训练、回放和分析。二者不得混用。

### 文本日志

- 模块内使用 `logging.getLogger(__name__)`，不在库模块内配置 handler、日志级别或输出文件。
- CLI 或 runner 在进程入口统一配置 console handler 与运行目录下的 `run.log`。
- `INFO` 用于连接、断开、模式切换、采集开始/结束、配置摘要、保存结果和安全干预等低频事件。
- `DEBUG` 仅用于短期诊断；控制循环不得按 step 输出 `INFO` 日志，以免影响控制频率并淹没关键事件。
- `WARNING` 与 `ERROR` 必须说明操作、设备和可恢复性，但不得输出 token、密钥或原始图像等大体积数据。

### 结构化事件与数据

- 每个真实运行应有独立 `run_id`，并在输出目录中保存关键安全事件，例如连接、启用或拒绝运动、急停、控制模式切换和异常退出。
- 每步 observation、human action、assist action、executed action、belief、authority 和 safety intervention 属于 episode recorder 的结构化记录，不写入普通日志。
- 需要跨设备时间对齐时，记录带时区的 UTC wall-clock 时间和本机单调时间；推荐字段为 `timestamp_utc` 与 `monotonic_ns`。
- 运行失败或中断也应保存已知元数据、事件和失败原因，避免产生无法解释的残缺结果。

### 推荐运行目录

当采集与评测 runner 实现后，默认输出结构如下：

```text
outputs/runs/<run_id>/
├── effective_config.yaml
├── metadata.json
├── run.log
├── events.jsonl
└── episode/
```

- `effective_config.yaml`：合并本机配置和命令行覆盖后的有效配置，不含 token 等敏感值。
- `metadata.json`：至少记录开始时间、git commit、Python 与关键包版本、设备标识、控制频率和运行模式。
- `events.jsonl`：低频、可审计的结构化事件；不是高频轨迹存储。
- `episode/`：由后续 recorder 按 LeRobot-compatible schema 保存观测、动作和元数据。

## 配置约定

### 格式与覆盖顺序

- 使用 YAML 作为人可读的项目配置格式。
- 初期采用简单的递归合并，不引入通用配置框架。
- 推荐优先级由低到高：共享基础配置 < 工作流配置 < 本机配置 < 显式 CLI 参数。
- 每次实际运行都应保存合并后的 effective config，不能只保存输入配置文件名。
- 只有在多份配置确实重复时，再引入 `extends`；不要为了未来假设提前设计复杂继承层。

### 目录职责

```text
configs/
├── robot/        # 机器人型号能力、共享安全默认值和单位约定
├── collection/   # manual / shared-autonomy 采集流程
├── policy/       # ACT / VLA 训练与部署参数
├── evaluation/   # rollout 与 benchmark 参数
└── local/        # 本机连接、标定、路径等覆盖；默认不纳入版本控制
```

- 共享配置只能保存可公开、可复用的默认值，例如 `enable_motion: false`、共享限制、动作单位和功能开关。
- 本机配置保存 IP、端口、串口、相机序列号、标定文件路径、数据根目录等机器相关值。
- 提交 `configs/local/*.example.yaml` 模板，不提交实际 `*.local.yaml` 文件。调整此机制时，`.gitignore` 必须允许 example 文件被跟踪。
- token、密钥和账户凭据不进入 YAML；使用 `.env` 或系统环境变量，并提供不含真实值的示例文件。
- 过渡说明：当前 `configs/robot/rm65.yaml` 仍包含已验证连接所用的 IP。待配置加载器和 local example 文件落地后，应将该连接信息迁入 `configs/local/rm65.local.yaml`，并以 `configs/local/rm65.example.yaml` 提供可提交模板；迁移前不得破坏现有只读连通性检查。

### 真机运动确认

- 机械臂与夹爪的物理运动默认关闭。
- 后续采集 runner 启用运动时必须满足双重确认：本机配置明确允许运动，且命令行显式提供运动确认开关，例如 `--allow-motion`。
- 缺少任一确认条件时，runner 应拒绝发送运动命令，并在事件记录中写明原因。
- 任何运动配置都必须先通过 mock、低速和空载检查；关节限位、工作空间和急停条件未确认前，不得启用运动。

## 文档与决策边界

- `README.md` 是项目导航入口；长期背景与架构见 `docs/overview.md`，不堆叠临时运行记录。
- `AGENTS.md` 只保存 AI coding agent 必须遵守的短规则。
- 本文保存稳定的工程约定。
- `docs/hardware_setup.md`、`docs/dataset.md` 和 `docs/experiments.md` 分别在硬件参数、数据 schema 和正式实验协议确定后创建；当前不创建空文件。
- 当一个选择会长期影响公开接口、数据格式、配置机制、输出结构或实验解释时，再在 `docs/decisions/` 新增 ADR；小修复和临时调试不需要 ADR。
