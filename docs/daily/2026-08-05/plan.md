# 2026-08-05 工作计划（Day 10）

## 今日目标

上午：完成 C0 基线判决（r5 vs r6），收口 ACT-C0。下午：C1-lite 40 条 + ACT-C1 诊断。晚间：搭建并完成 **SmolVLA LoRA 微调与部署 smoke**；启动过夜 C0 / C0+C1 串行训练。

## 完成标准

### 上午（已完成）

- [x] r6 多 checkpoint 诊断 + 中部随机对照；
- [x] 锁定 **`act_c0_r5_critical_b8x2` 200k** 为 C0 基线；
- [x] 更新 `log.md`、`roadmap.md`、`datasets.md`；
- [x] 确定下一阶段先做 **C1 颜色基线**（非 SA、非 down）。

### 下午 / 晚间（当前主线）

- [x] 完成 C1-lite **40 条**成功 episode（失败删条，不计入 40）；
- [x] 每条 metadata 正确：`source_object` + `destination=up`；
- [x] `batch_check_episodes --require-success` 全 PASS；
- [x] `summarize_episode_conditions` 确认 red/blue 各 20；
- [x] export 至 `outputs/datasets/shape_pick_place_v1_c1`（40 ep / 8451 frames；新目录，勿覆盖 `c0`）；
- [x] 完成 `act_c1_rb_up_ft50k_from_r5` 50k 首轮训练与固定构型 rollout；
- [x] 明确 ACT 条件通道结论：runtime 虽传入 `task`，但 stock LeRobot ACT 的模型输入不是 `task` / `task_index`；文字条件未进入 ACT 的有效网络输入；
- [x] 搭建 SmolVLA LoRA smoke：训练、加载、同帧红蓝 task A/B 推理与部署链路；
- [x] 收工更新 `log.md`（含 SmolVLA 晚间进展与过夜训练状态）。

## 任务清单

### P0：C1-lite 首轮 40 条

- [x] 桌上**同时**放红块 + 蓝块；间距 ≥ 30 mm；
- [x] **每条 episode 重新摆两块**（见下节「位置随机」）；
- [x] 交替或分批采：`red→up` 20 + `blue→up` 20；
- [x] 干扰色全程不碰、不挪；轨迹只朝目标色；
- [x] 约 80% 干净直抓、20% 接近后小修正；勿故意先伸向错色；
- [x] native run 从 `train-151` 起；check → export `shape_pick_place_v1_c1`。

### P1：采完 40 条后（可明日）

- [x] 确认训练机 `sharedautonomy-train` 已具备 SmolVLA / PEFT 依赖与基础模型缓存；
- [x] 以 `shape_pick_place_v1_c1`（40 ep / 8451 frames）先做 C1-only SmolVLA LoRA smoke，复用现有标准 `task_text`，不再为 ACT 做 one-hot 编码；
- [x] 新建 SmolVLA 输出目录，保存 effective config、checkpoint 与推理配置；不覆盖 `act_c0_r5_critical_b8x2` 或 `act_c1_rb_up_ft50k_from_r5`；
- [x] 搭建 VLA 专用离线推理 / HTTP smoke 路径；不用现有 ACT 服务参数直接冒充 SmolVLA 服务；
- [x] 用相同红蓝摆放分别输入 red / blue，验收 task 改变是否稳定改变 reach 目标（粗趋势有，未达验收门）；
- [x] 评估加入 C0 数据混合：本机 export `c0_c1`（150 ep）；过夜启动 C0-only + C0+C1 串行 50k（后者中断，8-06 续）。

### 已取消 / 延期

- [x] r5 短程微调 → 不做；
- [x] 在现有 ACT-C1 50k 上继续加步或继续用同一设定补采 → 暂停；当前证据首先指向条件通道缺失，不是单纯 steps 不够；
- [x] 继续为 stock ACT 采集“成对构型”以期待文字条件自动生效 → 延后到条件输入真正接入模型后；
- [x] 直接切换 π₀ → 暂不做；先完成更轻量的 SmolVLA smoke；
- [x] 今日开 SA 采集器 → 延到 C1 颜色大致可分；
- [ ] 重算卡顿定量、固定格点 benchmark → 非阻塞。

## ACT-C1 诊断结论（已确认）

本轮 `act_c1_rb_up_ft50k_from_r5` 并非简单的“50k 还不够”。固定构型对照显示：

- 红左蓝右：输入 red ×2 均朝右，输入 blue ×2 也均朝右；
- 蓝左红右：输入 red / blue 共 4 次均到两块中间并偏蓝；
- 同一构型下切换 red / blue 没有稳定改变输出。

因此当前 ACT 学到的是视觉 / 运动先验与位置折中，没有学到“文字条件 → 目标颜色选择”。项目 runtime 会把 `task` 放入 batch，但 LeRobot ACT 的标准模型输入是图像、state 与可选 `observation.environment_state`；数据集里的 `task_index` 只是任务元数据，不会自动变成 ACT 条件 embedding。结论是：不再把继续增加 ACT steps 当作首要修复手段，改用原生支持语言条件的 SmolVLA 验证。

## 位置随机（40 条怎么摆）

**要随机。** 每条 episode **红、蓝两块都要重新摆** `(x, y, yaw)`，不要 40 条固定同一布局。

| 项 | 做法 |
| --- | --- |
| 随机什么 | 两块各自在拾取区内的位置 + 朝向（任务卡 §2.3） |
| 间距 | 中心距 ≥ 30 mm；不压边、不碰 A4 |
| 相对位置 | **目标在干扰的左/右、前/后大致各半**（例如 red 20 条里约 10 次目标在左、10 次在右） |
| 不必先做 | C0 式 9 格点全扫；首轮中等随机即可 |
| 避免 | 40 条全「蓝在左、红在右」或全固定间距——模型会学空间捷径而非颜色 |
| 自检 | 采完跑 `summarize_episode_conditions`；肉眼看几条 external 帧，确认两色位置确实在变 |

**简单操作节奏**：每条开始前把两块都挪一下；采 red 时心里记一下「这次红在左还是右」，下一批刻意换一侧，保证 20 条里左右大致均衡。

## 采集命令备忘

```bash
# red → up（示例；路径按当日 run_id 改）
python scripts/dry_run_cartesian_teleop.py \
  --config-enable-motion --allow-motion \
  --enable-cameras --enable-gripper \
  --record-dir outputs/runs/shape-pick-place-train-151/episode \
  --task-id shape_pick_place_v1 \
  --source-object red \
  --destination up

# blue → up：仅改 --source-object blue 与 record-dir
```

采后：

```bash
python scripts/batch_check_episodes.py --run-glob "shape-pick-place-train-1*" --require-success
python scripts/summarize_episode_conditions.py --run-glob "shape-pick-place-train-1*" --success-only
```

## 开始前条件

- [x] C0 基线已锁（r5-200k）；
- [x] 红、蓝两块齐全，拾取区可达；
- [ ] 真机运动双重许可；
- [x] 已读 [`datasets.md` §2.1 C1-lite](../datasets.md)；
- [x] SmolVLA 基础模型、PEFT 依赖与训练机显存条件确认（双卡 b8×2 FP16 可用）。

## 今天不做

- 不在 r5/r6 目录 resume 当 C1 训练；
- 不做 `down`、黄块、完整 6 条件；
- 不继续 ACT-C1 加步或在未接入条件通道前扩大 C1 采集；
- 不直接开 π₀ 训练；
- 不做 SharedAutonomy 采集；
- 不继续 ACT-C0 加步 / 补采。

## 待决策

- [x] 下一阶段：C1 颜色基线优先于 SA；
- [x] 首轮规模：**40 条**（20+20）；
- [x] 首轮 ACT-C1 rollout 已完成：文字条件未形成有效颜色选择；
- [x] 下一步策略：先做 SmolVLA C1-only smoke，再决定是否加入 C0 混合；
- [x] 已启动 C0-only + C0+C1 混合 LoRA 过夜训练（C0+C1 未完成，见 log）；
- [ ] SmolVLA 颜色条件正式验收门：固定构型稳定分色 + 抓放可用（8-06 起）。

## 背景

| 项目 | 状态 |
| --- | --- |
| C0 | r5-200k ACT 基线；90 ep 单块 blue；已收口 |
| C1-lite | 60 ep native；`c1` 40 ep + `c0_c1` 150 ep 合并快照 |
| SmolVLA | C1 50k 训完 + 部署链路通；C0 50k 训完待 rollout；C0+C1 50k 中断 |
| 配方 | [`datasets.md` §2.1](../datasets.md) |
