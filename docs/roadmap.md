# 项目路线图

本文档维护 SharedAutonomy-VLA 的阶段目标、当前进度和验收标准。项目背景见 [`overview.md`](overview.md)；具体的每日执行项与工作记录分别保存在 [`daily/`](daily/) 下的 `plan.md` 和 `log.md` 中。

## 当前状态

- 当前阶段：**Week 1：硬件、数据与最小训练闭环**（ACT smoke 已过；剩正式 10 条扩量与时间同步长期验证）
- 最近完成的每日计划：[2026-07-28 plan](daily/2026-07-28/plan.md)（Day 4：ACT smoke）
- 最近工作日志：[2026-07-27 log](daily/2026-07-27/log.md)（7/28 log 收工前补）
- 下一步主线：**正式 Manual 采集扩量**（向 10 条）；可选固化 `scripts/train_act_smoke.sh`

## Week 1：硬件、数据与最小训练闭环

- [x] 创建仓库和目录骨架；
- [x] 接入 RM-65B、夹爪、SpaceMouse、腕部 RGB-D 与固定第三视角 RGB 相机；
  - 硬件连通与软件双相机 observation 接线已完成（2026-07-24）；
  - 第三视角支架安装 + FOV 在线验收已完成（2026-07-27）；
  - FOV / 拾取区工作区摘要已写入 [`hardware_setup.md`](hardware_setup.md)（2026-07-28）；
- [x] 确定统一坐标系和第一阶段动作表示；
- [ ] 完成运行时跨设备时间同步；
  - 统一时间戳接口、`ObservationSynchronizer` 与双相机 `CameraSource` 已落地；
  - pilot 采集已用同步观测；尚需长期稳定 running 与更多样本验证；
- [ ] 采集并回放 10 条人工轨迹；
  - check/replay 工具已就绪；**pilot 协议已验收**（red→up ×2、red→down ×1，共 3 条样本）；
  - 正式 10 条：ACT smoke 已通过（2026-07-28），可开扩量；
- [x] 建立数据校验和可视化工具；
  - `check_episode` / `replay_episode`（含 EE 3D、`--json`）已完成（2026-07-24）；
  - **native → LeRobot export 最小版**已完成（2026-07-27）：`lerobot_export` + `export_lerobot_dataset.py` + ADR 0002；
  - LeRobot 侧 `lerobot-dataset-viz` / 加载抽查已用于 export 验收；
- [x] 用小数据完成 ACT smoke test（2026-07-28）；
  - Linux 2×3090，`sharedautonomy-train`，dataset `shape_pick_place_v1_v001`（3 episodes / 1040 frames）；
  - `lerobot-train` ACT，100 steps，`batch_size=2`，~52M params；
  - checkpoint：`outputs/train/act_smoke_v001/checkpoints/{000100,last}`；
  - VLA smoke **未做**（刻意排后）。

验收标准：

> 一条命令开始采集，一条命令检查数据，一条命令启动最小训练。

（三句均已具备最小命令；正式 10 条扩量与时间同步长期验证仍进行中。）

### Week 1 最小命令（验收用）

环境约定：采集 / check / export 用 Windows `sharedautonomy-lr060-cf`；训练用 Ubuntu `sharedautonomy-train`（2×3090）。真机运动须 local `enable_motion=true` **且** CLI `--allow-motion`。

**1. 一条命令开始采集**（示例；路径与 object/destination 按任务改）：

```bash
python scripts/dry_run_cartesian_teleop.py \
  --config-enable-motion \
  --allow-motion \
  --enable-cameras \
  --enable-gripper \
  --record-dir outputs/runs/<run_id>/episode \
  --task-id shape_pick_place_v1 \
  --source-object red \
  --destination up
```

`task_text` 由 `source_object` + `destination` 自动填入任务卡标准句；需改写句时再加 `--task-text`。

**2. 一条命令检查数据**：

```bash
python scripts/check_episode.py outputs/runs/<run_id>/episode
```

**3. 一条命令启动最小训练**（服务器；先 `conda activate sharedautonomy-train`，dataset 已放到仓库 `outputs/datasets/...`）：

```bash
export CUDA_VISIBLE_DEVICES=0
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_DATASETS_OFFLINE=1
export LD_LIBRARY_PATH="$CONDA_PREFIX/lib:${LD_LIBRARY_PATH:-}"

lerobot-train \
  --dataset.repo_id=local/shape_pick_place_v1 \
  --dataset.root="$(pwd)/outputs/datasets/shape_pick_place_v1_v001" \
  --policy.type=act \
  --policy.push_to_hub=false \
  --output_dir=outputs/train/act_smoke_v001 \
  --job_name=act_smoke_v001 \
  --policy.device=cuda \
  --batch_size=2 \
  --steps=100 \
  --save_freq=100 \
  --log_freq=10 \
  --wandb.enable=false
```

训练机前置（已验证）：`conda install ffmpeg -c conda-forge`；若 torchcodec 报 `CXXABI_*` / OpenVINO，需 `LD_LIBRARY_PATH=$CONDA_PREFIX/lib`（可写入该 env 的 `etc/conda/activate.d`）。本地 dataset 勿依赖 Hub：`HF_HUB_OFFLINE=1`。

## Week 2：SharedAutonomy 采集器

- [ ] 目标检测和工作空间标定；
- [ ] 候选目标意图推理；
- [ ] 局部趋近辅助器；
- [ ] 动态 authority；
- [ ] 安全过滤和动作限幅；
  - 关节过滤、Cartesian 纯函数、manual runner 安全链与真机 XYZ 小范围验收已完成（2026-07-24）；
  - 尚需接入正式采集 runner / SharedAutonomy 运行时并长期稳定 running；
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
