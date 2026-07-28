# 2026-07-28 工作日志

## 今日计划

参见 [plan.md](plan.md)。

## 完成情况

- [x] P0：**ACT smoke**（Linux `sharedautonomy-train`，v001，100 steps，checkpoint `000100`/`last`）；
- [x] P0：Manual 正式采集 **12 条** `phase: train`（3 色 × 2 区 × 2）；批量 `check_episode` 全 PASS；失败样本已删；
- [x] P0：export `shape_pick_place_v1_v002`（12 episodes / 2800 frames）并 `LeRobotDataset` 加载抽查；
- [x] P1：`hardware_setup.md` 第三视角 FOV / 拾取区工作区摘要；
- [x] 收工：`pytest -m core` 全绿；本 log（含「今日理解重点」）；
- [ ] P1 顺延：读 checkpoint 维度笔记（真机推理今日不做；并入下一阶段 ACT-Manual 真训）。

## 实验与结果

| 项目 | 结果 | 结论 |
| --- | --- | --- |
| ACT smoke | `lerobot-train` ACT，v001，100 steps，`batch_size=2`，~52M params；落盘 `outputs/train/act_smoke_v001/checkpoints/{000100,last}` | Week 1「一条命令启动最小训练」闭合；loss 无意义属预期 |
| Train 采集 | 12 条成功；`check_episode` 12/12 PASS；wrist/external 100%；每条仅 1–2 步 `wrist_camera_stale` | 时间同步在正式样本量下稳定；可关 Week 1 轨迹/同步项 |
| Export v002 | `outputs/datasets/shape_pick_place_v1_v002`：12 / 2800；`repo_id=local/shape_pick_place_v1` | 正式 train snapshot 可用；不混入 pilot v001 |
| executed angular 尖峰 | 007–010 的 `angular_norm_max≈62.7` rad/s | 诊断字段 RPY 未 unwrap（≈2π/0.1s）；ACT 吃 joint target，不废数据 |
| `pytest -m core` | 79 passed，20 deselected，~6 s | 7/27 gripper mock 修复后回归绿 |

## 新结论与决策

- Week 1 **技术验收关闭**（采集 / check / export / ACT smoke / 同步样本验证）；收工项已补齐。
- **`repo_id`** `local/shape_pick_place_v1` = 任务语义名；磁盘 `_v001`/`_v002` = 导出快照。训练时 `repo_id` + `root` 成对指定。
- 正式 train 用 **新目录 v002**，不 `--resume` 进 pilot v001；pilot 仅 pipeline，不用于效果评估。
- **下一阶段双轨**（已拍板，并写入 roadmap Week 1.5）：
  1. **近期**：本机继续 Manual 扩量；服务器对 `v002`（12 条）做 ACT-Manual **真训**（不预期效果；摸清耗时与 pipeline）；
  2. **数据够后再并行**：继续训 / 重训 ACT（含真机 rollout 标定数据量）∥ Week 2 SharedAutonomy **采集器**工程；
  3. **正式 SA 对照采集**不抢在 Manual 闭环与 SA runner 稳定之前；
  4. `v002` 仅作种子 / 第一版 ACT-Manual。
- roadmap **周次可调整**：不是圣旨；有更好节奏时改「当前状态」与周次说明，并在 log 留决策（overview 研究问题优先）。
- 12 条 `v002` 是 **Manual 种子集**，不预期 ACT 效果；第一次 rollout 当作数据量标定实验。

## 今日理解重点（15–30 分钟）

自测问题见各条目；**完整参考答案在文末「自测参考答案」**。

### 1. ACT smoke vs ACT-Manual 真训

- **一句话**：smoke 只验证「能读 dataset、能跑 loop、能落 checkpoint」；真训才谈收敛与真机效果。
- **为什么重要**：混淆二者会误判「pipeline 通了 = 策略可用」，或反过来用无效 loss 否定链路。
- **本项目怎么做**：今日 smoke 用 v001、100 steps；下一阶段用 v002 加步数做 ACT-Manual，并补真机 rollout。
- **代码入口**：`docs/roadmap.md` Week 1 训练命令；checkpoint `outputs/train/act_smoke_v001/`。
- **自测问题**：为什么 100-step smoke 的 loss 不能用来判断 12 条数据够不够？

### 2. `repo_id` 与 `_v00N` 目录

- **一句话**：`repo_id` 是 LeRobot 逻辑数据集名（对齐 `shape_pick_place_v1`）；`_v00N` 是本地导出快照目录。
- **为什么重要**：误改 `repo_id` 会拆散训练配置；覆盖同一 root 会毁掉可复现快照。
- **本项目怎么做**：始终 `local/shape_pick_place_v1`；pilot→v001、train→v002；重导出换新目录。
- **代码入口**：ADR 0002 §1；`scripts/export_lerobot_dataset.py --out-root`。
- **自测问题**：多采一批同任务数据时，应改 `repo_id` 还是换 `_v00N`？

### 3. 时间同步验收看什么

- **一句话**：`check_episode` 汇总 `sync_warning_*` 与相机覆盖率；偶发 `wrist_camera_stale` 多为启动瞬态。
- **为什么重要**：Week 1 关掉「长期同步验证」需要多样本统计，不是只看单条 PASS。
- **本项目怎么做**：12 条 train 双相机 100%；每条 1–2 步 wrist stale、无 external/robot_state stale。
- **代码入口**：`sharedautonomy/data/episode_check.py`、`sharedautonomy/data/sync.py`。
- **自测问题**：`sync_warnings` 很多时，`ok` 会不会变成 false？

### 4. executed angular ≈ 62.7 rad/s 伪影

- **一句话**：该字段是 `(safe_rpy - measured_rpy) / dt`，未做 2π unwrap；±π 边界会打出约 `2π/0.1` 的尖峰。
- **为什么重要**：避免误判为关节失控或废 episode；ACT 监督的是 `joint_target_deg`。
- **本项目怎么做**：007–010 可见；human angular 为 0；joint 仍经 `clip_joint_targets`。
- **代码入口**：`sharedautonomy/control/manual.py`（`executed_angular`）；安全比较用 `math.remainder`。
- **自测问题**：这个尖峰会不会写进 LeRobot 的 `action` 向量？

### 面试式自测

先只读问题，自己作答；答案见文末。

1. 为什么下一阶段要双轨并行，而不是「先做完 SharedAutonomy 再训 ACT」？
2. Manual 人手已经能稳定抓放，Week 2 SharedAutonomy 的验收意义是什么？

## 代码与文档变更

- `docs/hardware_setup.md`：第三视角 FOV / 拾取区工作区结论（P1）；
- `docs/daily/2026-07-28/plan.md`、`docs/roadmap.md`：勾选 Week 1 完成项与双轨下一步；
- 本 log；无新增业务代码（采集 / export / 训练均用既有脚本）。

## 验证

- 服务器：ACT smoke（v001，100 steps）→ checkpoint；
- 真机：12 条 train 录制；操作者确认任务成功；失败已删；
- 离线：批量 `check_episode --json`；export v002；`LeRobotDataset` → `12 2800`；
- `pytest -m core`：79 passed，20 deselected；
- 未做：ACT-Manual 真训、真机 rollout、checkpoint 维度精读、VLA smoke。

## 未完成与阻塞

- checkpoint 维度笔记：顺延到 ACT-Manual 真训时一并做；
- 无阻塞；Week 1 技术项已关。

## 已沉淀到长期文档

- Week 1 完成状态与下一步双轨 → [`roadmap.md`](../roadmap.md)
- FOV / 工作区 → [`hardware_setup.md`](../hardware_setup.md)

## 常用命令（备忘）

```bash
# 服务器 ACT smoke（已跑通；dataset 先放到仓库 outputs/datasets/...）
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

```powershell
# 批量 check（Windows）
Get-ChildItem outputs\runs\shape-pick-place-train-*\episode -Directory | ForEach-Object {
  python scripts/check_episode.py $_.FullName --json
}

# 导出 train → v002
python scripts/export_lerobot_dataset.py `
  outputs/runs/shape-pick-place-train-001/episode `
  outputs/runs/shape-pick-place-train-002/episode `
  outputs/runs/shape-pick-place-train-003/episode `
  outputs/runs/shape-pick-place-train-004/episode `
  outputs/runs/shape-pick-place-train-005/episode `
  outputs/runs/shape-pick-place-train-006/episode `
  outputs/runs/shape-pick-place-train-007/episode `
  outputs/runs/shape-pick-place-train-008/episode `
  outputs/runs/shape-pick-place-train-009/episode `
  outputs/runs/shape-pick-place-train-010/episode `
  outputs/runs/shape-pick-place-train-011/episode `
  outputs/runs/shape-pick-place-train-012/episode `
  --out-root outputs/datasets/shape_pick_place_v1_v002 `
  --repo-id local/shape_pick_place_v1

# 加载抽查
python -c "from lerobot.datasets.lerobot_dataset import LeRobotDataset; ds=LeRobotDataset('local/shape_pick_place_v1', root='outputs/datasets/shape_pick_place_v1_v002'); print(ds.num_episodes, ds.num_frames)"
```

## 下一工作日建议

1. **今天余时 / 近几日**：本机继续 Manual 扩量；服务器 scp `v002` 启动 ACT-Manual 真训（新 `output_dir`，加 steps）；
2. 真训结束后做小规模真机 rollout，记录失败模式 → 决定下一波采集量与是否再 export `_v003`；
3. **数据量判断够了之后**（或 GPU 有空且采集不堵手时）：并行开工 Week 2 SA 采集器（检测/标定 → 意图 → 趋近 → authority → 三路动作）；
4. （可选）固化 train 命令模板 / `scripts/train_act_*.sh`；
5. 正式 SA 对照采集 **不要**抢在 Manual 闭环与 SA runner 稳定之前。

## 自测参考答案

### 理解重点

1. **ACT smoke vs 真训 — 为什么 smoke loss 不能判断数据够不够？**
   - 参考答案：100 steps + 极少样本只证明 feature/shape/dtype 与落盘正常；未充分拟合，loss 无评价意义。数据是否够，要靠更长训练后的真机 rollout / 成功率来标定。

2. **`repo_id` vs `_v00N` — 多采一批改哪个？**
   - 参考答案：任务语义不变则保持 `repo_id=local/shape_pick_place_v1`，换新 `--out-root`（如 `_v003`）或对该 root `--resume`。只有任务定义大改才考虑 `shape_pick_place_v2`。

3. **时间同步 — sync_warnings 多会不会让 ok=false？**
   - 参考答案：**不会**。`ok` 只看 hard `issues`；`sync_warnings` 是统计/告警。需看 `sync_warning_step_count` 占比与类型是否系统性恶化。

4. **angular 尖峰 — 会进 action 吗？**
   - 参考答案：**不会**。ADR 0002 的 `action` 是 `joint_target_deg` + gripper；`angular_velocity_rad_s` 仅留在 native / 可选诊断，不进默认 ACT 输入。

### 面试式自测

1. **为什么双轨而不是先做完 SA？**
   - 参考答案：研究问题需要 Manual 基线；overview 优先级是先闭合真实数据闭环。近期是「本机扩量 ∥ 服务器训 v002」；数据够后再「继续训 ACT ∥ 写 SA」。正式 SA 对照采集等 runner 稳了再开。roadmap 周次可按证据调整，不是圣旨。

2. **人手已会抓放，Week 2 SA 验收意义？**
   - 参考答案：SA 是**采集脚手架**（提高效率/质量并记录 human/assist/executed），不是替代人手完成任务。验收是「Manual 与 SA 均可稳定 reaching 并开始抓放」，为 Week 3 对照数据集铺路。
