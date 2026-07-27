# SharedAutonomy-VLA

> 面向真实机器人操作的共享控制辅助示教、模仿学习与 VLA 纠错微调闭环

## 项目状态

- 当前阶段：**Week 1 / 硬件、数据与最小训练闭环**
- 平台：Windows 控制机 + RM-65B + 腕部 RGB-D / 第三视角 RGB + SpaceMouse + 二指软夹爪
- 训练：Ubuntu 22.04，2 × RTX 3090
- 目标：跑通采集 → 训练 → 部署 → 纠错 → 再微调的完整链路

## 文档导航

| 文档 | 用途 |
| --- | --- |
| [docs/overview.md](docs/overview.md) | 研究问题、架构、任务定义、数据格式、评测与交付物 |
| [docs/roadmap.md](docs/roadmap.md) | 4–6 周阶段目标、当前进度、验收标准 |
| [docs/daily/](docs/daily/) | 每日 `plan.md` / `log.md` 与工作流程 |
| [docs/hardware_setup.md](docs/hardware_setup.md) | 硬件能力、延迟、安全验证结论 |
| [docs/engineering_conventions.md](docs/engineering_conventions.md) | 代码风格、日志、配置、测试约定 |
| [docs/decisions/](docs/decisions/) | 长期接口与设计决策（ADR） |
| [AGENTS.md](AGENTS.md) | AI 与协作者的工程边界（精简版） |

最近记录：[2026-07-24 plan](docs/daily/2026-07-24/plan.md) · [2026-07-23 log](docs/daily/2026-07-23/log.md)

## 当前主线

```text
SpaceMouse → human action → safety filter → RM-65B
    → synchronized observation → dataset recorder → replay / visualization
```

代码入口：`sharedautonomy/control/manual.py`、`sharedautonomy/robot/safety.py`、`sharedautonomy/data/schema.py`。

## 快速开始

环境：Windows 端 Conda `sharedautonomy-lr060-cf`（Python 3.12 + LeRobot 0.6.0 + RealMan SDK）。

```powershell
# 安装开发依赖
pip install -e ".[dev]"

# 日常验证（安全关键路径）
pytest -m core

# 离线 teleop dry-run（默认不运动）
python scripts/dry_run_manual_cartesian.py

# 检查已录制的 native episode（文本摘要；issues 非空时 exit 1）
python scripts/check_episode.py outputs/runs/<run_id>/episode

# 机器可读 JSON（便于批量过滤）
python scripts/check_episode.py outputs/runs/<run_id>/episode --json

# 可视化回放（wrist + external + EE 3D；←/→ 步进，--hz 自动播放）
python scripts/replay_episode.py outputs/runs/<run_id>/episode
python scripts/replay_episode.py outputs/runs/<run_id>/episode --hz 5
```

机器本地 IP、标定与串口配置放在 `configs/local/`（见 `configs/local/*.example.yaml`），不要提交到仓库。

## 安全提示

真机运动默认关闭；启用运动需配置与 CLI 双重确认。策略与遥操作输出必须经过 `sharedautonomy.robot.safety`。详见 [hardware_setup.md](docs/hardware_setup.md) 与 [overview.md §12](docs/overview.md#12-安全要求)。

## License

待确定。Citation 见 [overview.md](docs/overview.md#17-license-与-citation)。
