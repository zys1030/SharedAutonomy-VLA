# 2026-08-05 工作计划（Day 10）

## 今日目标

上午：完成 C0 基线判决（r5 vs r6），收口 ACT-C0。**下午起**：按 [`datasets.md` §2.1](../datasets.md) 启动 **C1-lite** 首轮采集——红+蓝同桌、仅 `up`，先采 **40 条**（`red→up` ×20 + `blue→up` ×20）。

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
- [x] export 至 `outputs/datasets/shape_pick_place_v1_c1`（新目录，勿覆盖 `c0`）；
- [ ] 收工更新 `log.md`（含摆放随机是否均衡的自检）。

## 任务清单

### P0：C1-lite 首轮 40 条

- [x] 桌上**同时**放红块 + 蓝块；间距 ≥ 30 mm；
- [x] **每条 episode 重新摆两块**（见下节「位置随机」）；
- [x] 交替或分批采：`red→up` 20 + `blue→up` 20；
- [x] 干扰色全程不碰、不挪；轨迹只朝目标色；
- [x] 约 80% 干净直抓、20% 接近后小修正；勿故意先伸向错色；
- [x] native run 从 `train-151` 起；check → export `shape_pick_place_v1_c1`。

### P1：采完 40 条后（可明日）

- [ ] 新目录开训 `act_c1_rb_up_*`（**勿** resume `act_c0_r5` 同 job）；
- [ ] 首轮 rollout：主看**抓对颜色率**；
- [ ] 按验收门决定是否 +20～40 条。

### 已取消 / 延期

- [x] r5 短程微调 → 不做；
- [x] 今日开 SA 采集器 → 延到 C1 颜色大致可分；
- [ ] 重算卡顿定量、固定格点 benchmark → 非阻塞。

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
- [ ] 红、蓝两块齐全，拾取区可达；
- [ ] 真机运动双重许可；
- [ ] 已读 [`datasets.md` §2.1 C1-lite](../datasets.md)。

## 今天不做

- 不在 r5/r6 目录 resume 当 C1 训练；
- 不做 `down`、黄块、完整 6 条件；
- 不做 SharedAutonomy 采集；
- 不继续 ACT-C0 加步 / 补采。

## 待决策

- [x] 下一阶段：C1 颜色基线优先于 SA；
- [x] 首轮规模：**40 条**（20+20）；
- [ ] 训完 40 条后：纯 C1 smoke 还是 C0+C1 混合 → **等首轮 rollout 再定**。

## 背景

| 项目 | 状态 |
| --- | --- |
| C0 | r5-200k 基线；90 ep 单块 blue；已收口 |
| C1-lite | 0/40；红蓝同桌，仅 up |
| 配方 | [`datasets.md` §2.1](../datasets.md) |
