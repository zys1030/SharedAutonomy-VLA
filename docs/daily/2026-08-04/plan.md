# 2026-08-04 工作计划（Day 9）

## 今日目标

确认 80 条 C0 上 `act_c0_r5_critical_b8x2` 的过夜训练结果，完成 100k / 150k / 200k checkpoint 的现场 rollout 对照，确定当前主力 checkpoint；并完成远侧补采与 90 ep 数据准备，启动 `act_c0_r6` 训练。

## 完成标准

- [x] 确认 `act_c0_r5_critical_b8x2` 训练至 **200k**，checkpoint 齐全，loss 约 `0.024–0.025`；
- [x] 完成 200k checkpoint 的首次抓取诊断 rollout：22 次，17 次成功；
- [x] 完成 150k / 100k 轻量对照：150k 长边 5 次、100k 短边 5 次；
- [x] 记录各轮位置、偏差、碰到、抓取、掉落和夹爪冗余等现场现象；
- [x] 与 `act_c0_r4_critical` 100k 的 6/18 做定性对比；
- [x] 暂定 200k 为当前主力 checkpoint；
- [x] 更新当日 `log.md`（含「今日理解重点」）；
- [x] 将 r5 训练结果补入 [`docs/datasets.md`](../datasets.md) §5；
- [x] C0 扩至 **90 ep / 19518 frames**，完成 check / resume / SCP；
- [x] 重建关键帧索引并启动 `act_c0_r6_critical_b8x2` 训练（续训与 rollout 见 8-05）。

## 任务清单

### P0：已完成

- [x] 核对过夜训练：`act_c0_r5_critical_b8x2` 训至 **200k**；checkpoint 齐全；loss 约 `0.024–0.025`；
- [x] 200k rollout：长边 7/9、短边 7/9、非长短边正对 3/4，合计 **17/22**；
- [x] 150k 轻量 rollout：长边 **2/5**，未见优于 200k；
- [x] 100k 轻量 rollout：短边 **1/5**，远侧偏近失败更突出；
- [x] 暂定 `200k` 为当前主力 checkpoint，旧 checkpoint 保留只读对照；
- [x] 补采 **10 条远侧示教**（`train-141`…`150`），C0 达 **90 / 19518**；
- [x] episode check、LeRobot `--resume`、训练机 SCP；
- [x] 重建 90 ep 关键帧索引；启动 `act_c0_r6_critical_b8x2` 新目录训练（不覆盖 r5）。

### P1：延期至 8-05

- [x] r6 多 checkpoint 真机对照与中部随机测试 → **见 8-05**；
- [x] 决定 r5 vs r6 主力基线 → **见 8-05**。

## 当天不做（已遵守）

- 不直接在已有 r5 200k 上做小学习率微调；
- 不修改 `reset_every=25`、chunk runner 或 temporal ensemble；
- 不进 C1、VLA、RL。

## 当日结论

- r5-200k 为当时 C0 主力（17/22）；远侧仍有偏近短板；
- 已用 +10 远侧数据启动 r6 验证；不覆盖 r5。

## 背景

| 项目 | 状态 |
| --- | --- |
| 数据 | C0 由 80/17327 扩至 **90/19518** |
| r5 | 80 ep，200k，loss≈0.024–0.025，rollout 17/22 |
| r6 | 90 ep，新目录关键帧加权训练已启动 |
