# 2026-07-28 工作计划（Day 4）

## 今日目标

用 `shape_pick_place_v1_v001`（3 pilot episodes）在 LeRobot 0.6 上跑通 **ACT 训练 smoke**，闭合 Week 1「一条命令启动最小训练」；smoke 通过后启动 **Manual 正式采集**（向 roadmap 10 条目标推进）。

> 承接 [2026-07-27 log](2026-07-27/log.md)「下一工作日建议」。

## 完成标准

- [x] **ACT smoke**：最小训练配置可启动、能读完本地 dataset、至少跑完 smoke 步数/epoch 并落 checkpoint（**不求效果**）；
  - 服务器 `sharedautonomy-train`：100 steps，`outputs/train/act_smoke_v001/checkpoints/{000100,last}`；~52M params；
- [ ] 训练命令与关键超参记入当日 log（或 `configs/policy/` 草稿配置），可复现；
- [ ] smoke 通过后：至少再采 **2 条** `phase: train` 的 Manual episode（建议补未覆盖组合，如 `yellow`/`blue` × `up`/`down`），每条 `check_episode` PASS；
- [ ] 新 episode 可 export 进 dataset（新 `_v00N` 或 `--resume`，二选一写进 log）；
- [ ] 收工前 `pytest -m core` 全绿（补 7/27 gripper mock 修复后的回归）；
- [ ] 收工前整理 `log.md`（含「今日理解重点」）。

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
- [ ] 记录：命令一行版、训练机、耗时、已知限制（数据太少导致 loss 无意义属预期）→ 收工写入 `log.md`。

### P0：smoke 通过后 — Manual 正式采集（起步）

- [ ] 场景按任务卡摆好（三色块 + A4 UP/DOWN）；go-to-ready + 双确认运动/夹爪；
- [ ] 采集命令沿用 pilot 链路：`--task-id shape_pick_place_v1`、`--source-object`、`--destination`、`--task-text`；
- [ ] metadata 写 `phase: train`（与 pilot 区分）；
- [ ] 优先补 **pilot 未覆盖** 的组合（当前已有 `red→up`×2、`red→down`×1）；
- [ ] 每条：`check_episode` → 目视或 `replay_episode`；失败即重录（任务卡 §5）；
- [ ] **今日目标条数**：smoke 后至少 **+2 条** 成功样本；时间充裕再冲 **+3～5 条**（累计向 10 条靠拢）。

### P1：有时间再做

- [ ] 正式样本累计达 **5 条** 时 export 一版 `_v002` 并做加载抽查；
- [x] `hardware_setup.md` 补充第三视角 FOV / 拾取区工作区结论（无本地机密）；
- [ ] 训练 smoke 余力：读一遍 checkpoint 维度，为后续 rollout 留笔记（真机推理今日不做）。

## 开始前条件

- [x] Conda `sharedautonomy-lr060-cf`（采集/export）；训练机 Linux SSH 已通 + `sharedautonomy-train`；
- [x] `outputs/datasets/shape_pick_place_v1_v001` 已存在且 `LeRobotDataset` 可 load；
- [ ] 机械臂、双相机、夹爪、扩展工作区、ready pose 参数与 7/27 pilot 一致（正式采集前再确认）；
- [x] 真机运动默认关闭；采集时 local `enable_motion` + CLI `--allow-motion` 双确认。

## 今天不做

- SharedAutonomy 采集器 / authority / 意图推理（Week 2）；
- 追求 ACT 收敛或真机 rollout（smoke 仅验证 pipeline）；
- VLA LoRA smoke（ACT 通过后再排）；
- 为凑满 10 条牺牲质量（失败样本不硬计入成功条数）；
- 一次性采满 6 组合 × 多条（按任务卡 Phase 2→3 渐进）；
- Hub push、完整超参搜索、数据增广。

## 待决策

- [x] **训练跑在哪**：Linux 训练机（2×3090）；dataset scp 到服务器；
- [x] **LeRobot 入口**：直接用 `lerobot-train`（暂未包 `scripts/train_act_smoke.py`；命令见 roadmap）；
- [ ] smoke 通过后第一条正式采集走 **Phase 2**（补色/补区）还是继续 **Phase 1** 巩固（建议：至少 1 条换色验证泛化）。

## 已决策

- [x] Week 1 第三句验收标准是 **ACT smoke**，不是 VLA；
- [x] 训练数据先用 v001（3 pilot）；正式 `train` episode 与 pilot 分 `phase` 字段；
- [x] export 映射不变（ADR 0002）；smoke 不使用 `diag.*` 作 policy 输入；
- [x] pilot 3 条 **不用于** 效果评估，仅用于打通 train pipeline；
- [x] 服务器：`conda install ffmpeg -c conda-forge`；`LD_LIBRARY_PATH=$CONDA_PREFIX/lib:...`（torchcodec / OpenVINO vs 系统 libstdc++）；离线 `HF_HUB_OFFLINE=1`。

## 参考链接

- 任务卡：[`docs/tasks/shape_pick_place_v1.md`](../../tasks/shape_pick_place_v1.md)
- Export 映射：[`docs/decisions/0002-lerobot-export-mapping.md`](../../decisions/0002-lerobot-export-mapping.md)
- 已有 dataset：`outputs/datasets/shape_pick_place_v1_v001`
- 导出命令：[`2026-07-27/log.md`](2026-07-27/log.md)「常用命令」
- ACT smoke checkpoint：`outputs/train/act_smoke_v001/checkpoints/`
- 采集配置：`configs/collection/manual_cartesian.yaml`
- Roadmap Week 1：[`docs/roadmap.md`](../../roadmap.md)
