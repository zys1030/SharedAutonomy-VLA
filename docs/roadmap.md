# 项目路线图

本文档维护 SharedAutonomy-VLA 的阶段目标、当前进度和验收标准。项目背景见 [`overview.md`](overview.md)；具体的每日执行项与工作记录分别保存在 [`daily/`](daily/) 下的 `plan.md` 和 `log.md` 中。

**使用说明**：周次标题是规划骨架，不是不可改的排期圣旨。若现场节奏或证据显示更好顺序（例如先闭合 Manual 采集–训练–部署，再并行 SharedAutonomy 工程），应更新本文「当前状态」与相关周次说明，并在当日 `log.md` 记下决策；`overview.md` 的研究问题与交付优先级仍优先遵守。

## 当前状态

- 当前阶段：**Week 1 已关闭** → **Manual 双轨扩量期**（先采+训，再训+SA）
- 最近完成的每日计划：[2026-07-28 plan](daily/2026-07-28/plan.md)（Day 4：ACT smoke + Manual 扩量）
- 最近工作日志：[2026-07-28 log](daily/2026-07-28/log.md)
- 下一步主线（2026-07-28 拍板）：
  1. **近期**：本机继续 Manual 扩量采集；服务器用 `v002`（12 条）跑 ACT-Manual **真训**（不预期效果；标定 pipeline 与耗时）；
  2. **数据量够后再并行**：继续训 / 重训 ACT-Manual（含真机 rollout）∥ 开工 Week 2 SharedAutonomy **采集器**工程；
  3. **正式 SA 对照采集**不抢在 Manual 闭环与 SA runner 稳定之前；
  4. `v002` 仅作种子 / 第一版 ACT-Manual。

## Week 1：硬件、数据与最小训练闭环

- [x] 创建仓库和目录骨架；
- [x] 接入 RM-65B、夹爪、SpaceMouse、腕部 RGB-D 与固定第三视角 RGB 相机；
  - 硬件连通与软件双相机 observation 接线已完成（2026-07-24）；
  - 第三视角支架安装 + FOV 在线验收已完成（2026-07-27）；
  - FOV / 拾取区工作区摘要已写入 [`hardware_setup.md`](hardware_setup.md)（2026-07-28）；
- [x] 确定统一坐标系和第一阶段动作表示；
- [x] 完成运行时跨设备时间同步；
  - 统一时间戳接口、`ObservationSynchronizer` 与双相机 `CameraSource` 已落地；
  - pilot + 正式 **12 条** train 均用同步观测；批量 `check_episode`：双相机 100% 覆盖，仅偶发 `wrist_camera_stale`（每条 1–2 步，启动瞬态量级）；
- [x] 采集并回放 10 条人工轨迹；
  - check/replay 工具已就绪；**pilot 协议已验收**（red→up ×2、red→down ×1，共 3 条样本）；
  - 正式 train：**12 条**（3 色 × 2 区 × 2），`check_episode` 全 PASS；任务成功由操作者确认（失败已删）；
  - 导出：`outputs/datasets/shape_pick_place_v1_v002`（12 episodes / 2800 frames）；
- [x] 建立数据校验和可视化工具；
  - `check_episode` / `replay_episode`（含 EE 3D、`--json`）已完成（2026-07-24）；
  - **native → LeRobot export 最小版**已完成（2026-07-27）：`lerobot_export` + `export_lerobot_dataset.py` + ADR 0002；
  - LeRobot 侧 `lerobot-dataset-viz` / 加载抽查已用于 export 验收（含 v002）；
- [x] 用小数据完成 ACT smoke test（2026-07-28）；
  - Linux 2×3090，`sharedautonomy-train`，dataset `shape_pick_place_v1_v001`（3 episodes / 1040 frames）；
  - `lerobot-train` ACT，100 steps，`batch_size=2`，~52M params；
  - checkpoint：`outputs/train/act_smoke_v001/checkpoints/{000100,last}`；
  - VLA smoke **未做**（刻意排后）。

验收标准：

> 一条命令开始采集，一条命令检查数据，一条命令启动最小训练。

（三句均已具备最小命令；正式 train 扩量、时间同步样本验证与收工项均已闭合。详见 [2026-07-28 log](daily/2026-07-28/log.md)。）

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

## Week 1.5：Manual 扩量与 ACT-Manual 闭环（插入；原 Week 2/3 部分前移）

> 2026-07-28 调整：不要求「先做完 SharedAutonomy 再碰真训」。先用 Manual 闭合采集–训练–部署，并用 rollout 回答「要多少数据」。

- [ ] Manual 继续扩量（在 12 条种子之上；按条件补条数，失败不硬计入）；
- [ ] ACT-Manual 真训（先用 `v002`，后续换更大 `_v00N`）；记录命令、步数、墙钟耗时；
- [ ] 真机 rollout（小规模 held-out 位姿）；用失败模式决定下一波采集量；
- [ ] （并行，可稍后启动）Week 2 SharedAutonomy 采集器工程——见下节。

验收标准（本插入阶段）：

> 能在服务器稳定跑完非 smoke 的 ACT-Manual；至少做过一轮真机 rollout，并形成「还要多少 Manual 数据」的判断。

## Week 2：SharedAutonomy 采集器

> 可与 ACT-Manual 训练 / 扩量**并行**（GPU vs 本机工程）。正式 SA 大批量对照采集仍等本周验收通过后再开。

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

> ACT-Manual 的**第一版真训与 rollout**已前移到 Week 1.5；本周侧重同分布对照集与 ACT-SA，以及更完整的 Manual 正式集。

- [ ] Manual 数据集（对照实验规模；可继承 1.5 扩量成果）；
- [ ] SharedAutonomy 数据集；
- [ ] 数据清洗和质量统计；
- [ ] ACT-Manual（完整版 / 与 SA 可比设置）；
- [ ] ACT-SharedAutonomy；
- [ ] 真机 rollout 与第一版对照结果。

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
