# 2026-07-28 工作计划（Day 4）

## 今日目标

用 `shape_pick_place_v1_v001`（3 pilot episodes）在 LeRobot 0.6 上跑通 **ACT 训练 smoke**，闭合 Week 1「一条命令启动最小训练」；smoke 通过后启动 **Manual 正式采集**（向 roadmap 10 条目标推进）。

> 承接 [2026-07-27 log](2026-07-27/log.md)「下一工作日建议」。
>
> **进度**：Week 1 主线与收工项已完成（见下方完成标准）。**今日余时**进入 roadmap **Week 1.5**：本机继续 Manual 扩量 ∥ 服务器对 `v002` 做 ACT-Manual 真训。

## 完成标准

- [x] **ACT smoke**：最小训练配置可启动、能读完本地 dataset、至少跑完 smoke 步数/epoch 并落 checkpoint（**不求效果**）；
  - 服务器 `sharedautonomy-train`：100 steps，`outputs/train/act_smoke_v001/checkpoints/{000100,last}`；~52M params；
- [x] 训练命令与关键超参记入当日 log（或 `configs/policy/` 草稿配置），可复现；
- [x] smoke 通过后：至少再采 **2 条** `phase: train` 的 Manual episode（建议补未覆盖组合，如 `yellow`/`blue` × `up`/`down`），每条 `check_episode` PASS；
  - 实际：**12 条** train（3 色 × 2 区 × 2），批量 `check_episode` 均 PASS；任务成功样本（失败已删）；
- [x] 新 episode 可 export 进 dataset（新 `_v00N` 或 `--resume`，二选一写进 log）；
  - `outputs/datasets/shape_pick_place_v1_v002`：12 episodes / 2800 frames；`repo_id=local/shape_pick_place_v1`；
- [x] 收工前 `pytest -m core` 全绿（补 7/27 gripper mock 修复后的回归）；
  - 79 passed，20 deselected；
- [x] 收工前整理 `log.md`（含「今日理解重点」）。

### 今日余时完成标准（Week 1.5 起手）

- [ ] 服务器：`v002` 已 scp；ACT-Manual **真训已启动**（非 smoke；新 `output_dir`，如 `act_manual_v002`）；
- [ ] 本机：在 12 条之外继续 Manual 扩量（能采几条算几条；每条 `check_episode` PASS；失败即删/重录）；
- [ ] 新采条目数与 run_id、真训 steps / `output_dir` 补记当日 [log.md](log.md)（可收工或明日开篇补一句）。

## 任务清单

### P0：ACT 训练 smoke（今日主线）

- [x] 确认训练环境：Linux 训练机（2×3090）+ Conda `sharedautonomy-train`；dataset `outputs/datasets/shape_pick_place_v1_v001` 已 scp 并可 load；
- [x] 选定 smoke 策略：**ACT**（joint 7 维 + 双 RGB + `task`；与 ADR 0002 一致）；VLA 今日不做；
- [x] 准备最小训练配置：
  - `repo_id` / `root` 指向 v001；
  - `observation.images` = `wrist` + `external`；
  - `observation.state` + `action` 均为 7 维；
  - smoke：`steps=100`，`batch_size=2`；
- [x] 跑通训练 loop（`lerobot-train`），确认无 feature/shape/dtype 错误；
- [x] 确认 checkpoint：`outputs/train/act_smoke_v001/checkpoints/000100` 与 `last`；
- [x] 记录：命令一行版、训练机、耗时、已知限制（数据太少导致 loss 无意义属预期）→ 收工写入 `log.md`。

### P0：smoke 通过后 — Manual 正式采集（起步）

- [x] 场景按任务卡摆好（三色块 + A4 UP/DOWN）；go-to-ready + 双确认运动/夹爪；
- [x] 采集命令沿用 pilot 链路：`--task-id shape_pick_place_v1`、`--source-object`、`--destination`、`--task-text`；
- [x] metadata 写 `phase: train`（与 pilot 区分）；
- [x] 优先补 **pilot 未覆盖** 的组合（当前已有 `red→up`×2、`red→down`×1）；
  - 已覆盖完整 6 组合 × 2 次（含 yellow/blue × up/down）；
- [x] 每条：`check_episode` → 目视或 `replay_episode`；失败即重录（任务卡 §5）；
  - 12/12 `check_episode` PASS（双相机 100%；`wrist_camera_stale` 每条仅 1–2 步）；失败样本已删；任务成功由操作者确认；
- [x] **今日目标条数**：smoke 后至少 **+2 条** 成功样本；时间充裕再冲 **+3～5 条**（累计向 10 条靠拢）。
  - 实际 **+12 条**，已超过 roadmap Week 1「10 条」目标。

### P0：今日余时 — Manual 扩量 ∥ ACT-Manual 真训（Week 1.5）

> 双轨：本机采、服务器训。不预期 12 条出效果；真机 rollout 可留到真训跑完后再做。

**服务器（GPU）**

- [ ] scp `outputs/datasets/shape_pick_place_v1_v002` 到训练机仓库对应路径；
- [ ] `conda activate sharedautonomy-train`；确认 `HF_HUB_OFFLINE=1`、`LD_LIBRARY_PATH=$CONDA_PREFIX/lib`；
- [ ] 启动 ACT-Manual 真训（相对 smoke：**加大 `steps`**，新目录，勿覆盖 `act_smoke_v001`）：
  - `repo_id=local/shape_pick_place_v1`
  - `root=.../shape_pick_place_v1_v002`
  - 建议：`output_dir=outputs/train/act_manual_v002`，`steps` 先试 **2000–5000**（可按墙钟再调）；`batch_size` 可仍为 2 或视显存上调；
  - 命令模板见 [roadmap](../../roadmap.md) smoke 段，改 `root` / `output_dir` / `steps` / `job_name` 即可；
- [ ] 确认训练 loop 已稳定跑起来（无 feature/shape 报错）；checkpoint 策略自定（如 `save_freq` 与 steps 同量级）。

**本机（采集）**

- [ ] 继续 Manual 扩量：`phase: train`；6 组合可继续各加条数（优先位置随机）；
- [ ] run_id 建议接续 `shape-pick-place-train-013` 起；
- [ ] 每条：`check_episode` PASS；任务失败不计入、重录或删除；
- [ ] （可选，余时够）新样本先不急着 export；凑一批再 `_v003` 或 `--resume` 进新目录（**勿覆盖 v002**，因服务器可能正在用）。

### P1：有时间再做

- [x] 正式样本累计达 **5 条** 时 export 一版 `_v002` 并做加载抽查；
  - `shape_pick_place_v1_v002`：12 episodes / 2800 frames，`LeRobotDataset` 可 load；
- [x] `hardware_setup.md` 补充第三视角 FOV / 拾取区工作区结论（无本地机密）；
- [ ] 读一遍 ACT checkpoint 维度，为后续 rollout 留笔记（可并入真训结束后）；
- [ ] 真机 rollout（建议等本次 `act_manual_v002` 跑完；今日不做强求）。

## 开始前条件

- [x] Conda `sharedautonomy-lr060-cf`（采集/export）；训练机 Linux SSH 已通 + `sharedautonomy-train`；
- [x] `outputs/datasets/shape_pick_place_v1_v001` 已存在且 `LeRobotDataset` 可 load；
- [x] `outputs/datasets/shape_pick_place_v1_v002` 已存在（12 / 2800）；
- [x] 机械臂、双相机、夹爪、扩展工作区、ready pose 参数与 7/27 pilot 一致（正式采集前再确认）；
- [x] 真机运动默认关闭；采集时 local `enable_motion` + CLI `--allow-motion` 双确认。

## 今天不做

- SharedAutonomy 采集器 / authority / 意图推理（等 Manual 数据更够、再与 ACT 并行开工）；
- 追求 ACT **收敛效果**或今日内必须完成真机 rollout（真训先跑通即可）；
- VLA LoRA smoke；
- 为凑数牺牲质量（失败样本不硬计入）；
- Hub push、完整超参搜索、数据增广；
- 覆盖正在被训练使用的 `v002` 目录。

## 待决策

- [x] **训练跑在哪**：Linux 训练机（2×3090）；dataset scp 到服务器；
- [x] **LeRobot 入口**：直接用 `lerobot-train`（暂未包 `scripts/train_act_smoke.py`；命令见 roadmap）；
- [x] smoke 通过后第一条正式采集走 **Phase 2**（补色/补区）还是继续 **Phase 1** 巩固（建议：至少 1 条换色验证泛化）。
  - 实际直接按任务卡 Phase 3：完整 6 组合 × 2 次。
- [ ] 真训 `steps` / `batch_size` 最终取值（先开跑，按显存与墙钟再调；记入 log）。

## 已决策

- [x] Week 1 第三句验收标准是 **ACT smoke**，不是 VLA；
- [x] 训练数据先用 v001（3 pilot）；正式 `train` episode 与 pilot 分 `phase` 字段；
- [x] export 映射不变（ADR 0002）；smoke 不使用 `diag.*` 作 policy 输入；
- [x] pilot 3 条 **不用于** 效果评估，仅用于打通 train pipeline；
- [x] 服务器：`conda install ffmpeg -c conda-forge`；`LD_LIBRARY_PATH=$CONDA_PREFIX/lib:...`（torchcodec / OpenVINO vs 系统 libstdc++）；离线 `HF_HUB_OFFLINE=1`。
- [x] 正式 train 导出用新目录 `_v002`（不 `--resume` 进 v001）；pilot 与 train 分 phase / 分 snapshot。
- [x] **Week 1.5 节奏**：近期本机扩量 ∥ 服务器训 v002；数据够后再「继续 ACT ∥ SA 工程」；正式 SA 对照采集等 runner 稳定；roadmap 周次可调整。

## 参考链接

- 任务卡：[`docs/tasks/shape_pick_place_v1.md`](../../tasks/shape_pick_place_v1.md)
- Export 映射：[`docs/decisions/0002-lerobot-export-mapping.md`](../../decisions/0002-lerobot-export-mapping.md)
- Pilot dataset：`outputs/datasets/shape_pick_place_v1_v001`（3 episodes / 1040 frames）
- Train dataset：`outputs/datasets/shape_pick_place_v1_v002`（12 episodes / 2800 frames）
- 当日 log：[`log.md`](log.md)
- ACT smoke checkpoint：`outputs/train/act_smoke_v001/checkpoints/`
- 采集配置：`configs/collection/manual_cartesian.yaml`
- Roadmap（含 Week 1.5）：[`docs/roadmap.md`](../../roadmap.md)
