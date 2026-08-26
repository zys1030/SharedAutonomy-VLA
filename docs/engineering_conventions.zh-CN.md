# 工程约定

语言：[English](engineering_conventions.md) | 简体中文

本文说明 SharedAutonomy-VLA 在开发、验证和维护过程中的稳定工程约定。机器专属联调流程、本机配置值、实验工作记录和内部计划不属于这份公开项目约定。

## 1. 环境与检查

- 支持的 Python 范围为 `>=3.12,<3.13`；LeRobot 版本固定在 `pyproject.toml` 中。
- Ruff 负责格式与静态检查，pytest 负责离线行为检查。
- 只安装当前工作需要的可选依赖组。
- 除非改动确实需要且依赖决策已有记录，否则不引入新的框架或服务。

日常代码改动运行与范围相称的检查：

```powershell
python -m ruff check <changed paths>
python -m ruff format --check <changed paths>
pytest -m core
```

真机联调、release staging，或修改数据集映射、夹爪行为及其他 extended 路径前，运行全量测试。真机验证是独立、由操作者控制的步骤；离线测试通过不代表已经完成硬件验收。

## 2. 代码与接口规则

- 代码、标识符、日志信息、public API docstring 和 schema 字段使用英文。
- Public class、function、配置对象和跨模块数据结构使用类型标注。
- 观测、动作、配置和 episode 元数据优先使用类型化 `dataclass`。只在明确的序列化或外部协议边界使用无结构字典。
- 字段名显式包含单位：`_m`、`_deg`、`_s`、`_hz` 和 `_ns`。
- 供应商 SDK 调用只放在机器人和设备适配层；采集、策略、数据和评测层不直接调用硬件 SDK。
- 硬件与可选依赖使用 lazy import。没有连接设备或安装硬件包时，核心 import、离线工具和单元测试仍应可用。
- 安全检查靠近命令执行点并保持可测试；人工或策略路径都不得绕过 `sharedautonomy.robot.safety`。

## 3. 日志与 episode 数据

文本日志用于说明低频运行事件；episode 记录用于保存高频观测、动作、时序和安全元数据，两者不能混用。

- 库模块使用 `logging.getLogger(__name__)`，不自行配置 handler。
- 进程入口统一配置 console 和文件日志。
- `INFO` 用于连接状态、模式切换、运行开始/结束、effective configuration、输出保存和安全干预，不用于逐 step 控制数据。
- `WARNING` 与 `ERROR` 说明操作、设备和可恢复性，但不输出凭据、原始图像或大体积 payload。
- 观测，以及相互区分的 `human`、`assist` 和经过安全过滤的 `executed` 动作，写入结构化 episode recorder。
- 运行中断时仍保留已有元数据、事件和终止原因。

常规运行输出可以包含：

```text
outputs/runs/<run_id>/
├── effective_config.yaml
├── metadata.json
├── run.log
├── events.jsonl
└── episode/
```

运行输出属于本地产物，不提交到仓库。Effective configuration 必须排除凭据和其他 secret。

## 4. 配置与硬件安全

YAML 是项目的人可读配置格式。覆盖优先级依次为共享默认值、工作流配置、本机 override 和显式 CLI 参数。真实运行应记录合并后的 effective configuration。

共享配置只包含可复用、可公开的默认值。控制器地址、端口、设备身份、标定、工作区几何、ready pose、数据根目录和凭据保存在被忽略的本机文件或环境变量中。公开模板位于 `configs/local/*.example.yaml`；真实值使用被忽略的 `*.local.yaml` 文件。

物理运动默认关闭。允许运动的运行必须同时通过有效的本机配置门控和显式命令行 motion gate。任务执行前依次完成 mock、dry-run、只读检查和操作者控制的低速验证。公开安全边界见 [`hardware.zh-CN.md`](hardware.zh-CN.md)。

## 5. 文档与兼容性

- `README.md` 是公开入口；稳定的方法、任务、数据、训练、结果、限制和硬件事实分别放在对应主题文档中。
- 公开叙述性 Markdown 使用英文默认文件和镜像的简体中文 `.zh-CN.md` 文件。两页在顶部互链，正文使用同语言文档链接。
- 代码、JSON、CSV、YAML、图片和其他语言无关产物由中英文文档共同引用。
- 公开事实以代码、配置、机器可读结果或其他冻结来源为准，不在多个叙述页面维护互相冲突的数字。
- Schema、数据集 feature layout、配置契约或公开协议发生破坏兼容性的变化时，必须给出明确的兼容性说明，并在适用处更新版本。

改动应保持局部且可验证。处理聚焦问题时，避免无关重构、全仓格式化或依赖变更。
