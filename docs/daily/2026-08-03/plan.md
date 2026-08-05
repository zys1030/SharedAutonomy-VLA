# 2026-08-03 工作计划（Day 8）

## 今日目标

完成 C0 从 40 条到 60 条的定向扩采后，基于现有数据实现关键帧起点加权训练与 rollout；根据 100k 结果再补闭合前局部调整数据，将 C0 扩到 80 条并完成 check / export。

## 完成标准

- [x] 新增 **20 条有效轨迹**（`101`…`120`）：14 条直接抓 + 6 条偏差修正，全部通过 episode 检查；
- [x] `shape_pick_place_v1_c0` 先追加到 **60 episodes / 13028**，不覆盖既有快照；
- [x] `act_c0_r4` 使用 60 条数据从头训练 **100k steps**，loss 曾低至约 `0.04`，最终约 `0.05–0.06`，并保留 checkpoint；
- [x] 在 `reset_every=25` 下完成 `act_c0_r4` 真机 rollout 19 次，记录 6 次完整抓放成功；
- [x] 完成关键帧起点加权采样实现，保持每个样本来自原始连续 episode chunk；
- [x] 用 60 条数据训练关键帧版本 checkpoint（`act_c0_r4_critical`，100k）；
- [x] 部署关键帧版本 checkpoint 并在 `reset_every=25` 下做首次抓取 rollout（18 次，6/18）；
- [x] 再补 **20 条** `train-121`…`140`（闭合前局部调整混合：有的最后明显调整，有的边接近边调；未再细分），batch check 20/20 PASS；
- [x] `--resume` 追加进 `shape_pick_place_v1_c0`，现 **80 episodes / 17327 frames**；`datasets.md` / 当日 log 已更新；
- [x] 收工时记录采样器验证、训练配置、checkpoint 和 rollout 结果；长期文档沉淀可延期。
- [x] 80 条快照用于服务器重建关键帧索引，并启动下一版关键帧训练（`act_c0_r5_critical_b8x2`：双卡每卡 batch=8，effective=16，fp16，新目录从头训）；
- [ ] 上述 `b8x2` 训练跑完并记录最终 loss / checkpoint → **延期至 2026-08-04**（8-03 晚间启动 12.5k 后 resume 过夜续训）；
- [ ] 若有时间：部署新 checkpoint 并做少量首次抓取 rollout → **延期至 2026-08-04**。

## 任务清单

### P0：今天必须完成

- [x] 完成 20 条定向扩采（`101`…`120`）、检查、追加 export、scp、`act_c0_r4` 训练与 baseline rollout。
- [x] 明确关键事件检测：
  - [x] 每条 episode 找到首次 gripper open→close 的抓取事件；
  - [x] 放置阶段不纳入关键窗加权（本轮不做释放事件加权）；
  - [x] 生成全局 `frame_index → weight / window_type` 索引；60 条时验证 60/60 唯一 close，13028 frames / 1860 关键窗帧。
- [x] 实现关键帧起点加权 sampler：
  - [x] 普通起点权重 `1`；
  - [x] 抓取窗起点权重 `5`，窗口为闭合前 20 帧、闭合后 10 帧；
  - [x] 不裁剪、不拼接 episode；每次仍从原始起点生成连续 action chunk；
  - [x] 验证采样索引不跨 episode；索引直接扫描 LeRobot raw `action`。
- [x] 用一个新目录训练关键帧版本（`act_c0_r4_critical`），不覆盖 `act_c0_r4`；
  - [x] 先做短程训练（约 20k），确认 sampler / loss / checkpoint 正常；20k 真机表现差；
  - [x] 单卡 resume 至 **100k**，完成正式训练与部署。
- [x] 按 100k rollout 结论补采闭合前局部调整数据：
  - [x] `train-121`…`140` ×20；未强制区分「最后明显调整」与「边接近边调」；
  - [x] `batch_check_episodes --require-success` 20/20 PASS；
  - [x] `--resume` 进 `shape_pick_place_v1_c0` → **80 / 17327**。

### P1：有时间再做

- [x] 将关键帧版本 checkpoint 同步到服务器并启动推理服务；
- [x] 在 `reset_every=25` 下做诊断 rollout（实际 18 次）；
- [x] 每次只统计首次闭合：重复尝试统一记失败；
- [x] 记录 reaching、首次闭合 XY/Z 偏差、是否抓起、是否放置成功和打圈现象；
- [x] 与 `act_c0_r4` 的 19 次现场结果做定性比较，不把少量 rollout 当正式 benchmark。
- [x] 80 条数据重建关键帧索引，并启动 `act_c0_r5_critical_b8x2`（双卡 batch 8×2）；
- [ ] 训练完成、记录结果；可选 rollout → **延期至 2026-08-04**。

## 开始前条件

- [x] 使用 `sharedautonomy-lr060-cf` 进行采集、检查和 export；
- [x] 真机运动前确认 local 配置与 CLI 双重运动许可，完成双确认；
- [x] 确认夹爪、双相机、录制目录和任务条件可用；
- [x] 使用固定 `reset_every=25`，不在采集日修改 rollout runner；
- [x] 确认双卡 3090 环境、数据快照路径和离线模型缓存可用；
- [x] 旧的 `c0` 快照、`act_c0_r3` checkpoint 只读，不覆盖。
- [x] 确认 LeRobot 0.6 当前 dataset / DataLoader 接口和 ACT chunk 采样入口；
- [x] 确认关键帧索引使用 LeRobot 全局 frame index，并保留 episode 边界。

## 今天不做

- C1、`_v004` 和完整 6 条件扩展；
- Pi0 / 其他 VLA LoRA 训练与部署；
- 强化学习、residual RL 或 DAgger 管线；
- 为了消除打圈而修改 chunk、temporal ensemble 或 rollout runner；
- 专门采失败恢复重试集或动态目标数据；
- 同时做 sampler 加权与 chunk 内 loss 加权；
- 大规模固定格点 benchmark、r3/r4 完整消融；
- 对 `121`…`140` 再人工细分成互斥类型标签（本日不强制）。

## 待决策

- [x] 关键帧窗口采用抓取前 20 帧、闭合后 10 帧；放置阶段不加权；
- [x] 起点权重先试 `5`；
- [x] 索引直接扫描 LeRobot raw `action`；每条 episode 只接受首次 open→close；
- [x] 关键帧 sampler 相对 baseline：水平偏差明显缩小到约 1 cm 内，成功率 6/18 vs 旧版 6/19，提升不显著但失败形态已变；
- [x] 下一步优先补闭合前局部对准数据（已采 `121`…`140` ×20），而不是继续给 60 条加训练步数或单纯加大 `W`；
- [x] 80 条下一版：新目录从头训 + 重建关键帧索引；不 resume `act_c0_r4_critical`；吞吐上采用大 batch（双卡 8×2 / 12500 steps，按样本量对齐单卡 8×25000）；
- 不因 loss 未达到 `0.001` 就盲目增加训练步数，先看首次抓取 rollout。
