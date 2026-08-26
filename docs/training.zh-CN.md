# SmolVLA 训练：Manual 与 Shared Autonomy

语言：[English](training.md) | 简体中文

> 状态：本文记录解释锁定对照所需的训练事实和去敏曲线。数据集、视频和 checkpoint 的外部发布当前暂缓；原始日志和本机产物继续保留在 private 范围内。

## 1. 范围

本文记录 Manual 与 Shared Autonomy 对照使用的两条最终 expert-only
SmolVLA 训练 run。两条 run 使用相同的策略族和训练配方，区别在于示教
数据的采集模式。

对应的真机闭环评测见 [`results.zh-CN.md`](results.zh-CN.md)。训练 loss
不能替代该评测：两份训练日志都没有 validation split 或 validation loss。
输入数据契约和发布状态见 [`datasets.zh-CN.md`](datasets.zh-CN.md)。

## 2. 锁定训练配方

| 项目 | Manual | Shared Autonomy |
| --- | ---: | ---: |
| 策略族 | SmolVLA | SmolVLA |
| 适配方式 | Expert-only | Expert-only |
| 训练步数 | 50,000 | 50,000 |
| 记录精度 | bf16 | bf16 |
| Batch 配置 | `8 × 2` | `8 × 2` |
| 数据集 episodes | 70 | 70 |
| 数据集 frames | 15,829 | 14,212 |
| 训练时 evaluation split | `0.0` | `0.0` |

模型输入两路 `480 × 640` RGB 观测视频和 10 维 state，输出 7 维 action，
action chunk size 为 50。

训练配置记录的模型初始化为：

- Base policy：`lerobot/smolvla_base`
- Vision-language backbone：`HuggingFaceTB/SmolVLM2-500M-Video-Instruct`

这里记录上游标识是为了复现。上游许可和微调 checkpoint 的外部再发布权限
独立于本项目代码发布，当前仍暂缓确认和发布。

### Optimizer 与 scheduler 配置

| 配置项 | 值 |
| --- | ---: |
| 初始 optimizer learning rate | `1.0e-4` |
| Optimizer betas | `0.9, 0.95` |
| Weight decay | `1.0e-10` |
| Gradient clipping norm | `10` |
| Warmup steps | `1,000` |
| Decay steps | `30,000` |
| Decay learning rate | `2.5e-6` |

## 3. 训练 telemetry

下图使用两条 run 各自记录的 50 个周期指标点，覆盖 step 1,000 到
step 50,000。图中只展示 training loss、gradient norm 和 learning rate。

![SmolVLA expert-only 50k 训练曲线](../assets/training_curves.svg)

*图 1：两条最终 expert-only run 的训练 loss、gradient norm 和 learning
rate。端点标签表示最后一个记录点的数值。*

| Run | 最后记录的 train loss | 最后记录的 gradient norm | 最后记录的 learning rate |
| --- | ---: | ---: | ---: |
| Manual 70 | `0.016` | `0.481` | `2.5e-6` |
| Shared Autonomy 70 | `0.013` | `0.466` | `2.5e-6` |

以上是训练 telemetry，不是 validation 或部署分数。图中有意不放
`samples/sec`，因为它可能受共享主机/GPU 负载影响，也不是模型质量指标。

## 4. Checkpoint 选择

每条 run 选用最终训练步之后的 `050000` model artifact。准备好的 artifact
包含推理所需的模型权重和 policy 前后处理文件。

Optimizer、scheduler 和随机状态组成的 `training_state` 不在准备的模型产物
中。因此 checkpoint 可以用于推理或作为新的 fine-tuning 起点，但不能作为
精确的中途 resume 包。

本机验证确认两个最终模型产物均可读取且结构完整。它们不包含在当前 release
中，本文也不发布其本机路径和 checksum。

## 5. 复现边界

当前公开曲线是根据 private training telemetry 生成的、经过清理的静态展示
资产。原始日志和绘图输入不属于当前 public release，因此不承诺从公开仓库
重新生成这张图。重新训练仍需要对应的本机数据集、LeRobot/SmolVLA 环境和
原始训练配置。

## 6. 解释限制

- 两条 run 的 episode 数匹配，且 nominal training recipe 相同；但 episode
  时长不同，所以 frame 数不同。
- 两条 run 都没有 validation split，因此曲线不能证明泛化能力。
- 最终闭环对照是 [`results.zh-CN.md`](results.zh-CN.md) 中的固定 36 条件
  评测，不是最后的 training loss。
- 本实验不证明任意连续位置、yaw、相机、物体、机器人或部署环境下的性能。
