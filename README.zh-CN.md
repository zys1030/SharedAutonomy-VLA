# SharedAutonomy-VLA

语言：[English](README.md) | 简体中文

SharedAutonomy-VLA 是一个面向真实机器人的研究系统，用于研究：与纯手动遥操作相比，窄范围、局部的共享自主辅助，是否能够产生更好的示范数据和更好的学习型机器人策略。

项目贯通完整实验链路：

```text
人工示范
   → 结构化 episode 记录
   → 兼容 LeRobot 的数据集导出
   → 任务条件策略训练
   → 带安全门控的闭环评估
```

本项目是工程与方法研究，不声称这个简单的抓取放置任务必须使用 VLA，也不声称共享自主在所有场景下都优于手动控制。

## 当前状态

当前仓库提供源代码与公开文档。数据集、视频和训练 checkpoint 的外部发布暂缓，待进一步确认数据所有权、发布范围以及上游模型条款。

因此，仓库入口不包含原始数据集、模型权重、训练日志、机器本地配置或私有调试记录。

## 主要结果

锁定的对比实验使用 `shape_pick_place_v1` 任务的 `block_rotation_rq2` 变体：

> 拿起红色方块并将其放入 UP 区域。

两个策略均使用 70 条示范、相同的 SmolVLA expert-only 训练方案、50,000 个训练步、bf16 精度和 `8 × 2` batch 配置。成对的真实机器人评估包含 36 个条件：9 个离散 XY 位置乘以 4 个初始 wrap-90 yaw，每个策略在每个条件下执行一次。

| 训练数据来源 | Episodes | Frames | 严格成功数 |
| --- | ---: | ---: | ---: |
| 手动示范 | 70 | 15,829 | 25 / 36 (69.4%) |
| 共享自主示范 | 70 | 14,212 | 34 / 36 (94.4%) |

![Manual 与 Shared Autonomy 成功率、配对结果和位置热图](assets/results_paired.svg)

这是固定成对网格上的描述性结果，不代表重复试验方差，也不代表对更广泛部署场景的泛化能力。完整协议和失败分析见 [`docs/results.zh-CN.md`](docs/results.zh-CN.md)。

## 方法

手动采集时，操作员控制笛卡尔运动和夹爪。共享自主采集时，操作员仍保留这些控制职责，同时由一个受约束的 yaw 辅助器根据外部相机提出局部 J6 对齐建议。该辅助器不会独立执行完整任务。

学习型策略接收同步的视觉观测、机器人状态和任务文本。策略给出的动作建议仍然经过系统中同一套局部仲裁与安全监督器。

```text
SpaceMouse / 任务文本 / 相机 / 机器人状态
                    ↓
             人工或策略建议
                    ↓
             仲裁与安全过滤
                    ↓
              RM65 机械臂 + 夹爪
                    ↓
              结构化 episode 数据
```

## 文档

| 文档 | 说明 |
| --- | --- |
| [`docs/overview.zh-CN.md`](docs/overview.zh-CN.md) | 研究问题、系统架构、数据接口、策略路线和范围 |
| [`docs/tasks/shape_pick_place_v1.zh-CN.md`](docs/tasks/shape_pick_place_v1.zh-CN.md) | 稳定的任务契约和发布边界 |
| [`docs/datasets.zh-CN.md`](docs/datasets.zh-CN.md) | 锁定的数据集事实、数据契约、血缘和发布状态 |
| [`docs/results.zh-CN.md`](docs/results.zh-CN.md) | 锁定的 36 条件评估协议与结果 |
| [`docs/training.zh-CN.md`](docs/training.zh-CN.md) | 训练方案、脱敏 telemetry 曲线和 checkpoint 选择 |
| [`docs/limitations.zh-CN.md`](docs/limitations.zh-CN.md) | 结果解释限制与负结果边界 |
| [`docs/hardware.zh-CN.md`](docs/hardware.zh-CN.md) | 公开硬件角色、主机划分、时序和安全边界 |
| [`docs/engineering_conventions.zh-CN.md`](docs/engineering_conventions.zh-CN.md) | 项目在代码、检查、配置、文档和安全方面的工程约定 |
| [`tools/README.zh-CN.md`](tools/README.zh-CN.md) | 诊断、benchmark 和实验流程支持工具说明 |

## 安装

项目目标 Python 版本为 3.12，核心 LeRobot 依赖固定为 0.6.0。请只安装当前任务需要的可选依赖组：

```bash
# 核心开发和离线测试
pip install -e ".[dev]"

# 数据集导出和 LeRobot 数据集工具
pip install -e ".[dataset]"

# SmolVLA 训练或推理依赖
pip install -e ".[smolvla]"

# 回放和绘图工具
pip install -e ".[dev,visualization]"

# 硬件集成；仅在机器人主机上使用
pip install -e ".[dev,hardware,visualization]"
```

硬件身份、标定、工作空间几何、ready pose、夹爪设置和连接参数应放在基于 `configs/local/*.example.yaml` 模板的、被忽略的本地配置中，不得提交到仓库。

## 能力与快速开始

下面的命令使用 `<episode_dir>`、`<dataset_root>` 等占位符。完整参数请对任意脚本运行 `--help` 查看。

### 离线检查 episode

这些检查不会连接机器人硬件：

```bash
pytest -m core
python scripts/dry_run_manual_cartesian.py
python scripts/check_episode.py <episode_dir>
python scripts/check_episode.py <episode_dir> --json
python scripts/replay_episode.py <episode_dir> --step 0 --hz 5
python scripts/plot_evaluation_results.py
```

`check_episode.py` 会校验 native episode 并输出统计摘要。`replay_episode.py` 需要 `metadata.json`、`steps.jsonl` 以及被引用的 `images/` 文件；省略 `--hz` 后可以使用方向键手动逐步回放。

### 采集示范

`collect_demonstrations.py` 使用 SpaceMouse 和 RM-65 接口。需要录制视觉 episode 时加上 `--enable-cameras`：

```bash
python scripts/collect_demonstrations.py --ip <RM65_IP> --record-dir <run_dir>/episode --enable-cameras --collection-mode manual --task-id shape_pick_place_v1 --source-object red --destination up
```

主要控制参数是 `--duration-s`、`--steps` 和 `--control-hz`；`--collection-mode` 支持 `manual` 和 `shared_autonomy`；`--task-text`、`--source-object` 与 `--destination` 用于写入任务元数据。只有配置侧门控为真（本地配置中的 `enable_motion: true` 或 `--config-enable-motion`），并且提供 `--allow-motion` 时才会启用运动。省略运动参数即可运行无运动预览。`--enable-gripper` 和 `--go-to-ready` 同样要求已启用运动。

### 导出本地 LeRobot 数据集

导出过程不会访问硬件，并可接受一个或多个 native episode 目录：

```bash
python scripts/export_lerobot_dataset.py <episode_dir> --out-root <dataset_root> --repo-id local/shape_pick_place_v1
```

默认导出为视频。使用 `--no-videos` 可改为将图像写入 parquet，使用 `--no-diag` 可省略诊断列，使用 `--allow-aborted` 可包含中止 episode，使用 `--resume` 可向已有数据集追加。字段顺序和单位见 [`docs/datasets.zh-CN.md`](docs/datasets.zh-CN.md)。

### 训练、serve 与 rollout

训练需要本地导出的数据集、上游模型资源和对应训练依赖。锁定的训练方案与发布边界见 [`docs/training.zh-CN.md`](docs/training.zh-CN.md)；公开仓库不包含项目数据集和最终 checkpoint。

推理 server 不会连接机器人硬件，也不会启用运动：

```bash
python scripts/serve_smolvla_policy.py --checkpoint <checkpoint_dir> --dataset-root <dataset_root> --dataset-repo-id <repo_id> --device cuda --port 8089
python scripts/serve_act_policy.py --checkpoint <checkpoint_dir> --dataset-root <dataset_root> --dataset-repo-id <repo_id> --device cuda --port 8088
```

对应的 rollout 客户端读取相机和机器人状态，并向 server 请求推理。默认只打印动作，不发送运动指令：

```bash
python scripts/rollout_smolvla_policy.py --ip <RM65_IP> --infer-url http://<policy_host>:8089 --steps 10 --task-text "Pick up the red cube and place it in the UP region."
python scripts/rollout_act_policy.py --ip <RM65_IP> --infer-url http://<policy_host>:8088 --steps 10 --task-text "Pick up the red cube and place it in the UP region."
```

常用 rollout 参数包括 `--infer-url`、`--steps`、`--duration-s`、`--control-hz`、`--reset-every` 和任务元数据参数。要发送关节指令，还必须同时满足两个运动门控、本地硬件配置、由操作员控制的 ready pose 与安全流程，并确保急停可用。详见 [`docs/hardware.zh-CN.md`](docs/hardware.zh-CN.md)。

## 安全边界

物理运动默认关闭。允许运动的运行必须同时满足有效的本地配置门控和显式命令行运动门控。安全监督器始终位于人工或策略建议与机器人之间，并执行工作空间、速度、步长、新鲜度、夹爪、死手柄和急停检查。

成功导入、dry-run 或 SDK 连接检查都不代表可以执行运动。硬件调试必须由操作员按受控流程进行，并确保急停路径可用。详见 [`docs/hardware.zh-CN.md`](docs/hardware.zh-CN.md)。

## 发布边界

源代码采用 [MIT License](LICENSE)。数据集、视频、训练 checkpoint、上游模型权重和媒体资源可能需要单独确认所有权、署名和许可证，因此这些资产目前不会作为仓库入口的一部分发布。

## 引用

如需引用项目方法和锁定对比实验，请引用本仓库，并参阅 [`docs/results.zh-CN.md`](docs/results.zh-CN.md) 中的评估协议。只有在相关数据集和 checkpoint 正式发布后，才会补充其外部引用信息。
