# 2026-08-03 工作计划（Day 8）

## 今日目标

完成 C0 从 40 条到 60 条的定向扩采后，基于现有 60 条实现关键帧起点加权训练；若训练及时完成，部署新 checkpoint 并做少量首次抓取 rollout。

## 完成标准

- [x] 新增 **20 条有效轨迹**：14 条 clean + 6 条水平修正，全部通过 episode 检查；
- [x] `shape_pick_place_v1_c0` 追加到 **60 episodes**，不覆盖既有快照；
- [x] `act_c0_r4` 使用 60 条数据从头训练 **100k steps**，loss 曾低至约 `0.04`，最终约 `0.05–0.06`，并保留 checkpoint；
- [x] 在 `reset_every=25` 下完成 `act_c0_r4` 真机 rollout 19 次，记录 6 次完整抓放成功；
- [ ] 完成关键帧起点加权采样实现，保持每个样本来自原始连续 episode chunk；
- [ ] 用现有 60 条数据训练新的关键帧版本 checkpoint；
- [ ] 若训练及时完成，在 `reset_every=25` 下部署新 checkpoint 并做少量首次抓取 rollout；
- [ ] 收工时记录采样器验证、训练配置、checkpoint 和 rollout 结果；长期文档沉淀可延期。

## 任务清单

### P0：今天必须完成

- [x] 完成 20 条定向扩采、检查、追加 export、scp、`act_c0_r4` 训练与 baseline rollout。
- [ ] 明确关键事件检测：
  - [ ] 每条 episode 找到首次 gripper open→close 的抓取事件；
  - [ ] 找到放置阶段最后一次 close→open 的释放事件；
  - [ ] 生成全局 `frame_index → weight / window_type` 索引。
- [ ] 实现关键帧起点加权 sampler：
  - [ ] 普通起点权重 `1`；
  - [ ] 抓取窗、放置窗起点先试权重 `5`；
  - [ ] 不裁剪、不拼接 episode；每次仍从原始起点生成连续 action chunk；
  - [ ] 验证采样索引不跨 episode，batch shape 与原 ACT 训练一致。
- [ ] 用一个新目录训练关键帧版本（建议 `act_c0_r4_critical`），不覆盖 `act_c0_r4`；
  - [ ] 先做短 smoke，确认 sampler / loss / checkpoint 正常；
  - [ ] smoke 通过后按与 baseline 可比的训练规模启动正式训练。

### P1：有时间再做

- [ ] 将关键帧版本 checkpoint 同步到服务器并启动推理服务；
- [ ] 在 `reset_every=25` 下做 3–6 次诊断 rollout；
- [ ] 每次只统计首次闭合：重复尝试统一记失败；
- [ ] 记录 reaching、首次闭合 XY/Z 偏差、是否抓起、是否放置成功和打圈现象；
- [ ] 与 `act_c0_r4` 的 19 次现场结果做定性比较，不把少量 rollout 当正式 benchmark。

## 开始前条件

- [x] 使用 `sharedautonomy-lr060-cf` 进行采集、检查和 export；
- [x] 真机运动前确认 local 配置与 CLI 双重运动许可，完成双确认；
- [x] 确认夹爪、双相机、录制目录和任务条件可用；
- [x] 使用固定 `reset_every=25`，不在采集日修改 rollout runner；
- [x] 确认双卡 3090 环境、数据快照路径和离线模型缓存可用；
- [x] 旧的 `c0` 快照、`act_c0_r3` checkpoint 只读，不覆盖。
- [ ] 确认 LeRobot 0.6 当前 dataset / DataLoader 接口和 ACT chunk 采样入口；
- [ ] 确认关键帧索引使用 LeRobot 全局 frame index，并保留 episode 边界。

## 今天不做

- C1、`_v004` 和完整 6 条件扩展；
- Pi0 / 其他 VLA LoRA 训练与部署；
- 强化学习、residual RL 或 DAgger 管线；
- 为了消除打圈而修改 chunk、temporal ensemble 或 rollout runner；
- 新增 episode、恢复重试数据和动态目标数据；
- 同时做 sampler 加权与 chunk 内 loss 加权；
- 大规模固定格点 benchmark、r3/r4 完整消融和长期文档整理。

## 待决策

- 关键帧窗口先采用抓取前后约 15/10 帧、放置释放前后约 15/5 帧是否合适；
- 起点权重先试 `5`，还是需要 `8`；
- 关键帧 sampler 相对 baseline 是否改善首次闭合 XY/Z 和成功率；
- 若训练后仍明显失败，是否转向视觉 / 动作表示问题，而不是继续增加 episode；
- 不因 loss 未达到 `0.001` 就盲目增加训练步数，先看首次抓取 rollout。
