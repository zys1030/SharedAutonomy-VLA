# 2026-07-28 工作日志

## 今日计划

参见 [plan.md](plan.md)。

## 完成情况

- [x] P0：**ACT smoke**（Linux `sharedautonomy-train`，v001，100 steps，checkpoint `000100`/`last`）；
- [x] P0：Manual 正式采集起步 **12 条** `phase: train`（3 色 × 2 区 × 2）；批量 `check_episode` 全 PASS；失败样本已删；
- [x] P0：export `shape_pick_place_v1_v002`（12 episodes / 2800 frames）并 `LeRobotDataset` 加载抽查；
- [x] P1：`hardware_setup.md` 第三视角 FOV / 拾取区工作区摘要；
- [x] 收工：`pytest -m core` 全绿；本 log（含「今日理解重点」）；
- [x] P1：读 ACT checkpoint 维度（真训后核对）；`state/action` 7 维；双 RGB `[3,480,640]`；`chunk_size=n_action_steps=100`；
- [x] Week 1.5：服务器 ACT-Manual 真训完成（`v002` 12 episodes → **50000** steps，`act_manual_v002`）；
- [x] Week 1.5：本机 Manual 扩量至 **60 条**（6 组合 × 10；`train-001`…`060`）；`batch_check_episodes` 60/60 PASS；
- [x] 今晚：离线开环抽查（episode0×20 帧；`mae_joints≈1.05°`）；
- [x] 今晚：云端 ACT 推理 HTTP 通路（`serve_act_policy.py` + 本机 `client_act_infer.py --mode dataset-remote` 已通）；
- [x] 今晚：真机观测 → `/infer` dry-run（`dry_run_act_observe_infer.py`；ready 下 10Hz 开环打印；**未下发**）；
- [ ] 真机控臂 rollout（场景 + safety + 运动）；压 RTT 至可闭环；60 条尚未 export `_v003`。

## 实验与结果

| 项目 | 结果 | 结论 |
| --- | --- | --- |
| ACT smoke | `lerobot-train` ACT，v001，100 steps，`batch_size=2`，~52M params；落盘 `outputs/train/act_smoke_v001/checkpoints/{000100,last}` | Week 1「一条命令启动最小训练」闭合；loss 无意义属预期 |
| Train 采集（种子） | 12 条成功；`check_episode` 12/12 PASS；wrist/external 100%；每条仅 1–2 步 `wrist_camera_stale` | 时间同步在正式样本量下稳定；可关 Week 1 轨迹/同步项 |
| Export v002 | `outputs/datasets/shape_pick_place_v1_v002`：12 / 2800；`repo_id=local/shape_pick_place_v1` | 正式 train 种子 snapshot；不混入 pilot v001 |
| ACT-Manual 真训 | `v002`（12 ep）→ `outputs/train/act_manual_v002`；先 3k 再 resume 至 **50000**；`batch_size=2`；单卡；~9–15 step/s；`checkpoints/last/pretrained_model/` | 非 smoke 真训 pipeline 闭合；**不预期** 12 条出效果；供 rollout 标定 |
| Manual 扩量 | **60** 条（黄/红/蓝 × up/down × 10）；`summarize_episode_conditions` 均衡；`batch_check_episodes` 60/60 PASS；任务成功由操作者确认 | Week 1.5 扩量达标；native 尚未进 `_v003` |
| 离线开环（A） | ep0 前 20 帧：`mae_joints≈1.05°`，`max_abs≈3.8°`（j2）；夹爪 MAE≈0.017；preprocessor + `select_action` | 量级合理；`gt`=`action`（命令目标）非实测 state |
| 云端推理（B2） | 服务器 `:8088`；本机 `dataset-remote` 收回 7 维 action；与离线 frame0 预测一致 | 本机↔云端 HTTP 通路闭合 |
| 真机观测 dry-run | `dry_run_act_observe_infer.py`：需等腕部 buffer；ready 下循环；action 平滑外漂（开环播 chunk）；**RTT 稳态 ~500–600ms**（首包可达数秒；偶发数秒尖峰） | 通路通；**撑不住真 10Hz 闭环**；非「模型乱跳」 |
| RTT 归因（待下 session 收尾） | `/health` 本机秒回；`/infer_dataset` 本机曾 **15s 超时**或 frame0 **视频时间戳 400**；开环多帧说明 ACT 前向不慢 | **部署延迟主因更像传图(~2.4MB JSON)+网络**；`infer_dataset` 另有 bug，勿作延迟基准 |
| executed angular 尖峰 | 007–010 的 `angular_norm_max≈62.7` rad/s | 诊断字段 RPY 未 unwrap（≈2π/0.1s）；ACT 吃 joint target，不废数据 |
| `pytest -m core` | 79 passed，20 deselected，~6 s | 7/27 gripper mock 修复后回归绿 |

## 新结论与决策

- Week 1 **技术验收关闭**（采集 / check / export / ACT smoke / 同步样本验证）。
- **`repo_id`** `local/shape_pick_place_v1` = 任务语义名；磁盘 `_v001`/`_v002` = 导出快照。训练时 `repo_id` + `root` 成对指定。
- 正式 train 用 **新目录 v002**，不 `--resume` 进 pilot v001；pilot 仅 pipeline，不用于效果评估。
- **Week 1.5 进度（收工时）**：
  1. ACT-Manual 真训已完：`v002` × **50k** steps → `act_manual_v002`；
  2. Manual native 已扩至 **60** 条（每条件 10）；批量结构检查全 PASS；
  3. **云端推理 + 真机观测 dry-run 已通**（`:8088` `/infer`）；RTT~550ms 待压；下一 session 再 safety/开运动；
  4. 正式 SA 对照采集仍等 Manual 闭环与 SA runner 稳定；
  5. `v002` 仍是种子快照；60 条是更大 native 池，**勿覆盖 v002**。
- 推理部署决策：**ACT 在 GPU 服务器常驻**；本机 client 发观测、收回 action；tonight 未开运动。
- 开环 `gt` = dataset `action` = **joint_target 命令**，不是 `observation.state` 实测角。
- **部署起始姿态**：训练数据几乎都从 ready 出发 → rollout 前应 `go-to-ready`；非 ready 上动作离谱属 OOD，不代表模型「训废」。
- **RTT / 10Hz（换 session 继续）**：
  - 真机 `/infer` 端到端稳态 **~500–600ms**（有效 ≈1.5–2Hz）；目标 10Hz 需 ≲100ms。
  - 载荷：双图 480×640×3 ≈1.8MB raw，base64 JSON ≈**2.4MB/步**。
  - `/health` OK；`/infer_dataset`：**frame0** 易触发 LeRobot `FrameTimestampError`（query t=0 vs video 0.1s）；其后本机计时曾 **15s Timeout** → **勿用 `infer_dataset` 测延迟**（疑视频解码/索引实现问题，与部署传图路径分离）。
  - 白天服务器开环多帧快 → ACT 前向不是 500ms 主因；**优先怀疑传图+网络**；优化方向：JPEG/压缩协议、有线局域网、服务端分段计时、`infer_dataset` 修复（`episode_data_index`、避开 t=0、禁止全库扫帧）。
- roadmap **周次可调整**：不是圣旨；有更好节奏时改「当前状态」与周次说明（overview 研究问题优先）。
- 12 条真训 **不预期** ACT 效果；第一次真机 rollout 当作数据量标定实验。

## 今日理解重点（15–30 分钟）

自测问题见各条目；**完整参考答案在文末「自测参考答案」**。

### 1. ACT smoke vs ACT-Manual 真训

- **一句话**：smoke 只验证「能读 dataset、能跑 loop、能落 checkpoint」；真训才谈拟合与真机效果。
- **为什么重要**：混淆二者会误判「pipeline 通了 = 策略可用」，或反过来用无效 loss 否定链路。
- **本项目怎么做**：smoke 用 v001、100 steps；真训用 v002、50k steps → `act_manual_v002`；效果仍靠 rollout 标定。
- **代码入口**：`docs/roadmap.md` Week 1 训练命令；checkpoint `outputs/train/act_manual_v002/`。
- **自测问题**：为什么 50k-step 真训跑完，仍不能直接说「12 条数据够了」？

### 2. `repo_id` 与 `_v00N` 目录

- **一句话**：`repo_id` 是 LeRobot 逻辑数据集名（对齐 `shape_pick_place_v1`）；`_v00N` 是本地导出快照目录。
- **为什么重要**：误改 `repo_id` 会拆散训练配置；覆盖同一 root 会毁掉可复现快照。
- **本项目怎么做**：始终 `local/shape_pick_place_v1`；pilot→v001、种子 train→v002；60 条扩量若导出应换 `_v003`（勿盖 v002）。
- **代码入口**：ADR 0002 §1；`scripts/export_lerobot_dataset.py --out-root`。
- **自测问题**：多采一批同任务数据时，应改 `repo_id` 还是换 `_v00N`？

### 3. 时间同步验收看什么

- **一句话**：`check_episode` / `batch_check_episodes` 汇总 `sync_warning_*` 与相机覆盖率；偶发 `wrist_camera_stale` 多为启动瞬态。
- **为什么重要**：关掉「长期同步验证」需要多样本统计，不是只看单条 PASS。
- **本项目怎么做**：60 条 train 双相机 100%；每条仅 1–2 步 wrist stale 量级。
- **代码入口**：`sharedautonomy/data/episode_check.py`；`scripts/batch_check_episodes.py`。
- **自测问题**：`sync_warnings` 很多时，`ok` 会不会变成 false？

### 4. executed angular ≈ 62.7 rad/s 伪影

- **一句话**：该字段是 `(safe_rpy - measured_rpy) / dt`，未做 2π unwrap；±π 边界会打出约 `2π/0.1` 的尖峰。
- **为什么重要**：避免误判为关节失控或废 episode；ACT 监督的是 `joint_target_deg`。
- **本项目怎么做**：007–010 可见；human angular 为 0；joint 仍经 `clip_joint_targets`。
- **代码入口**：`sharedautonomy/control/manual.py`（`executed_angular`）；安全比较用 `math.remainder`。
- **自测问题**：这个尖峰会不会写进 LeRobot 的 `action` 向量？

### 5. 种子集真训 vs native 扩量池

- **一句话**：`v002`（12）已训完；本机 native 已到 60，但尚未进新 LeRobot snapshot。
- **为什么重要**：避免误以为「60 条已经在训」或覆盖正在用的 `v002`。
- **本项目怎么做**：rollout 先用 `act_manual_v002`；确认要重训再 export `_v003`。
- **代码入口**：`scripts/summarize_episode_conditions.py`；`outputs/runs/shape-pick-place-train-*/episode`。
- **自测问题**：现在做 rollout，策略权重对应的是哪份数据？

### 6. 云端推理：观测进、action 出

- **一句话**：服务器 load ACT；本机 HTTP 发观测（或让服务器读 dataset 帧），收回 7 维 joint+gripper 目标；控臂与 safety 仍在本机。
- **为什么重要**：与 overview「GPU 推理 / Windows 执行」一致；今晚只验证通路，不等于真机 rollout。
- **本项目怎么做**：`serve_act_policy.py`（`:8088`）+ `client_act_infer.py`；协议在 `sharedautonomy/policies/act/protocol.py`。
- **代码入口**：`scripts/serve_act_policy.py`；`scripts/client_act_infer.py`；`sharedautonomy/policies/act/runtime.py`。
- **自测问题**：`/infer` 与 `/infer_dataset` 差别是什么？返回的 action 单位是什么？

### 面试式自测

先只读问题，自己作答；答案见文末。

1. 为什么下一阶段要双轨并行，而不是「先做完 SharedAutonomy 再训 ACT」？
2. Manual 人手已经能稳定抓放，Week 2 SharedAutonomy 的验收意义是什么？

## 代码与文档变更

- `scripts/summarize_episode_conditions.py`：按 `(source_object, destination)` 统计条数；
- `scripts/batch_check_episodes.py`：批量调用 `check_episode_dir`，一行 PASS/FAIL；
- `scripts/serve_act_policy.py`：云端 ACT HTTP 服务（`/health` `/infer` `/infer_dataset`）；
- `scripts/client_act_infer.py`：本机哑 client；
- `scripts/dry_run_act_observe_infer.py`：真机双相机+UDP 观测 → `/infer`（不下发）；
- `sharedautonomy/policies/act/protocol.py`、`runtime.py`：JSON 协议与 ACT 推理运行时；
- `docs/hardware_setup.md`：第三视角 FOV / 拾取区工作区结论；
- `docs/daily/2026-07-28/plan.md`、`docs/roadmap.md`：勾选 Week 1 / 1.5 与今晚推理前置；
- 本 log。

## 验证

- 服务器：ACT smoke（v001，100 steps）→ checkpoint；
- 服务器：ACT-Manual（v002，12 ep，**50000** steps）→ `act_manual_v002/checkpoints/last`；
- 真机：60 条 train 录制；操作者确认任务成功；失败已删；
- 离线：`summarize_episode_conditions`（每条件 10）；`batch_check_episodes` 60/60 PASS；export v002（仅前 12）；
- checkpoint 维度：state/action 7；双相机 CHW；chunk 100；
- 离线开环：ep0×20，`mae_joints≈1.05°`；
- 云端 `/infer`：真机观测 dry-run ready 开环；RTT 稳态 ~500–600ms；action 平滑；
- `/infer_dataset`：frame0 时间戳 400；本机计时 15s 超时 → **勿作延迟基准**（换 session 修）；
- `pytest -m core`：79 passed，20 deselected；
- 未做：真机控臂 + safety、RTT→10Hz、晃块对照、60 条 export `_v003`、VLA smoke。

## 未完成与阻塞

- **下一 session 优先**：压 `/infer` RTT（传图压缩 / 局域网 / 服务端分段计时）；修或弃用有问题的 `infer_dataset`；
- 真机 rollout：ready → safety → 双确认开运动；记失败模式；
- 60 条 → `_v003`：等真机 rollout 结论再决定是否导出与重训；
- 无硬阻塞（延迟是工程优化项）。

## 已沉淀到长期文档

- Week 1 / 1.5 完成状态与下一步 → [`roadmap.md`](../roadmap.md)
- FOV / 工作区 → [`hardware_setup.md`](../hardware_setup.md)

## 常用命令（备忘）

```bash
# 服务器 ACT smoke（已跑通）
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

```bash
# 服务器 ACT-Manual 真训（已完成：v002 × 50k → act_manual_v002）
# 先 3000 steps，再对本目录 --resume 至 steps=50000；batch_size=2；单卡
lerobot-train \
  --dataset.repo_id=local/shape_pick_place_v1 \
  --dataset.root="$(pwd)/outputs/datasets/shape_pick_place_v1_v002" \
  --policy.type=act \
  --policy.push_to_hub=false \
  --output_dir=outputs/train/act_manual_v002 \
  --job_name=act_manual_v002 \
  --policy.device=cuda \
  --batch_size=2 \
  --steps=50000 \
  --save_freq=5000 \
  --log_freq=10 \
  --wandb.enable=false
```

```bash
# 服务器：启动 ACT 云端推理（不连臂）
export CUDA_VISIBLE_DEVICES=0
export HF_HUB_OFFLINE=1
export LD_LIBRARY_PATH="$CONDA_PREFIX/lib:${LD_LIBRARY_PATH:-}"

python scripts/serve_act_policy.py \
  --checkpoint outputs/train/act_manual_v002/checkpoints/last/pretrained_model \
  --dataset-root outputs/datasets/shape_pick_place_v1_v002 \
  --host 0.0.0.0 --port 8088 --device cuda
```

```powershell
# 本机哑 client（sharedautonomy-lr060-cf；不连臂）
# 健康检查
python scripts/client_act_infer.py --url http://202.38.78.65:8088 --mode health

# 让服务器读自己的 v002 帧并返回 action（已通）
python scripts/client_act_infer.py `
  --url http://202.38.78.65:8088 `
  --mode dataset-remote `
  --episode-index 0 --frame-index 0

# 本机读 dataset 帧并 POST /infer（传图像；可选下一抽查）
python scripts/client_act_infer.py `
  --url http://202.38.78.65:8088 `
  --mode dataset-local `
  --dataset-root outputs/datasets/shape_pick_place_v1_v002 `
  --episode-index 0 --frame-index 0

# 真机观测 → 云端 /infer（不下发运动；需 serve 已开 + 双相机 local 配置）
python scripts/dry_run_act_observe_infer.py `
  --ip 192.168.1.19 `
  --infer-url http://202.38.78.65:8088 `
  --source-object blue `
  --destination down `
  --once
# 或短循环：去掉 --once，加 --duration-s 20 --control-hz 10
```

**接口约定（简）**

| 项 | 约定 |
| --- | --- |
| 端口 | 默认 `8088` |
| 输入 | `state(7)` float；`wrist`/`external` HWC uint8 RGB base64；`task` 字符串；可选 `reset` |
| 输出 | `action(7)`：joint deg×6 + gripper open_fraction；附 `chunk_size`/`n_action_steps` |
| 端点 | `GET /health`；`POST /infer`；`POST /infer_dataset` |

```powershell
# 条件计数 / 批量 check（Windows；sharedautonomy-lr060-cf）
python scripts/summarize_episode_conditions.py --run-glob "shape-pick-place-train-*"
python scripts/batch_check_episodes.py --run-glob "shape-pick-place-train-*"

# 导出 train 种子 → v002（仅 001–012；60 条扩量勿覆盖此目录）
python scripts/export_lerobot_dataset.py `
  outputs/runs/shape-pick-place-train-001/episode `
  ... `
  outputs/runs/shape-pick-place-train-012/episode `
  --out-root outputs/datasets/shape_pick_place_v1_v002 `
  --repo-id local/shape_pick_place_v1

# 加载抽查
python -c "from lerobot.datasets.lerobot_dataset import LeRobotDataset; ds=LeRobotDataset('local/shape_pick_place_v1', root='outputs/datasets/shape_pick_place_v1_v002'); print(ds.num_episodes, ds.num_frames)"
```

## 下一工作日建议

1. **优先（换 session）**：确认 `/infer` RTT 主因（本机 localhost 对 `/infer` 传图计时，或服务端分段日志）；上 JPEG/压缩或保局域网；目标端到端 ≲100ms 再谈 10Hz 闭环；
2. 修复或绕过 `infer_dataset`（frame0 时间戳；禁止全库 `dataset[i]` 扫描）；client 打印 RTT；
3. ready + safety 后门控开运动，小规模真机 rollout，记失败模式；
4. 按失败模式决定：是否继续 Manual 扩量、是否 export `_v003`、是否用更大集重训；
5. **数据量判断够了之后**：并行开工 Week 2 SA 采集器；
6. 正式 SA 对照采集 **不要**抢在 Manual 闭环与 SA runner 稳定之前。

## 自测参考答案

### 理解重点

1. **ACT smoke vs 真训 — 为什么 50k 跑完仍不能说 12 条够了？**
   - 参考答案：50k 只证明非 smoke 训练能稳定跑完并落盘；12 条仍可能欠拟合或过拟合窄分布。数据是否够，要靠 held-out 真机 rollout / 成功率与失败模式来标定，不能只看 steps 或 train loss。

2. **`repo_id` vs `_v00N` — 多采一批改哪个？**
   - 参考答案：任务语义不变则保持 `repo_id=local/shape_pick_place_v1`，换新 `--out-root`（如 `_v003`）或对该 root `--resume`。只有任务定义大改才考虑 `shape_pick_place_v2`。

3. **时间同步 — sync_warnings 多会不会让 ok=false？**
   - 参考答案：**不会**。`ok` 只看 hard `issues`；`sync_warnings` 是统计/告警。需看 `sync_warning_step_count` 占比与类型是否系统性恶化。

4. **angular 尖峰 — 会进 action 吗？**
   - 参考答案：**不会**。ADR 0002 的 `action` 是 `joint_target_deg` + gripper；`angular_velocity_rad_s` 仅留在 native / 可选诊断，不进默认 ACT 输入。

5. **种子集 vs 扩量池 — rollout 对应哪份数据？**
   - 参考答案：当前 `act_manual_v002` 权重来自 **v002 的 12 条**。本机 60 条还在 native run 目录，未进新 snapshot；要用 60 条训练需先 export `_v003`（或等价）再重训。

6. **云端推理 — `/infer` vs `/infer_dataset`；单位？**
   - 参考答案：`/infer` 是真机路径（client 上传 state+双图+task）。`/infer_dataset` 本意是服务器读盘烟测，但当前有 frame0 时间戳错误与超时风险，**不要当延迟基准或主路径**。返回 `action`：关节 **deg**×6，夹爪 **open_fraction**（命令目标）。

### 面试式自测

1. **为什么双轨而不是先做完 SA？**
   - 参考答案：研究问题需要 Manual 基线；overview 优先级是先闭合真实数据闭环。今日已扩量∥真训∥云端 `/infer` dry-run；下一步压 RTT 后再 safety/开运动 rollout。roadmap 周次可按证据调整，不是圣旨。

2. **人手已会抓放，Week 2 SA 验收意义？**
   - 参考答案：SA 是**采集脚手架**（提高效率/质量并记录 human/assist/executed），不是替代人手完成任务。验收是「Manual 与 SA 均可稳定 reaching 并开始抓放」，为 Week 3 对照数据集铺路。
