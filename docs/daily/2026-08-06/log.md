# 2026-08-06 工作日志

## 今日计划

参见 [plan.md](plan.md)。

## 收工摘要

**上午–中午**：确认 SmolVLA rollout 缺 `--enable-gripper` 导致「模型已预测闭合、硬件未执行」；补上后真机可闭爪。完成 **`smolvla_c0_90ep_lora_50k_b8x2` 50k** 定性 rollout：水平对准明显优于 ACT，粗估抓取成功率约 **50%**；主失败模式为**闭合高度偏高 + 闭爪瞬时轻移**。复测昨日 **C1-only 50k**（夹爪已开）：有红蓝分辨趋势，**红更稳、蓝偏中间**。

| 维度 | 结果 |
| --- | --- |
| 夹爪门控 | 未 `--enable-gripper` → `gripper_commanded` 恒 false；开启后可闭爪 |
| SmolVLA C0 50k | 水平对准强；约 50% 抓起；高度/闭爪时机是主瓶颈；抖动大于 ACT |
| SmolVLA C1 50k（复测） | 有分色趋势；red 更准，blue 偏红蓝中间 |
| 推理模式 | `rollout_smolvla_policy` **逐步同步** `/infer`（无 ACT 式 async / `reset_every`） |
| C0+C1 重训 | **tmux 运行中**，上午约 **9k / 50k**；待训满后再做红蓝对照 |

**下午–晚间**：完成 **`smolvla_c0_c1_lora_50k_b8x2_r2` 50k** 训练（训毕 loss ≈ **0.045**，仍有下降空间）并做初步红蓝 rollout。整体明显优于此前 **25k** checkpoint；共 **1 次抓取成功、1 次接近成功**（高度失败）；抖动仍明显。相对上午 C0-only 50k，**水平对准变差**，疑为颜色条件干扰 + 150 条轨迹仍不充分。

| 维度 | 结果 |
| --- | --- |
| C0+C1 50k 训毕 | loss ≈ 0.045；150 ep / 32193 frames |
| 构型 A（红蓝远、各抓一次×交换×2 = 4 次） | **构型1**：四次朝向均正确（选红朝红、选蓝朝蓝）；**构型2**：1 次该朝红却朝蓝，其余 3 次正确 |
| 构型 B（红蓝较近、各抓一次 = 2 次） | 两次均抓在**红蓝中间**，有相对目标色的偏向 |
| vs 25k | 明显更好 |
| vs C0-only 50k（上午） | 水平对齐退步，疑颜色干扰 |
| 抓取 | 1 成功 + 1 近成功（高度失败）；抖动仍明显 |

## 完成情况

- [x] 诊断并确认「动作末维已闭合、`gripper_commanded=false`」= 缺 `--enable-gripper`（非模型/服务端 bug）；
- [x] 开启夹爪后确认真机可闭爪；
- [x] `smolvla_c0_90ep_lora_50k_b8x2` **50k** 定性真机 rollout（`--source-object red --destination up`；`--control-hz 10`）；
- [x] 复测 `smolvla_c1_rb_up_lora_50k_b8x2` 50k（夹爪开启）红蓝分辨观感；
- [ ] C0 的 30k / 40k 多 checkpoint 对照：未做（今日只测了 50k）；
- [ ] 固定格点 / 严格成功率统计：未做（约 50% 为现场粗估）；
- [x] C0+C1 混合 LoRA：已在 `tmux` 开训；训满 **50k**（`smolvla_c0_c1_lora_50k_b8x2_r2`；训毕 loss ≈ 0.045）；
- [x] C0+C1 训满 50k + 红蓝固定构型初步 rollout（远距 4 次 + 近距 2 次；定性，未做严格成功率统计）；
- [ ] 固定格点 / 严格「抓对颜色率」统计：未做。

## 实验与结果

| 项目 | 结果 | 结论 |
| --- | --- | --- |
| 无 `--enable-gripper` 日志 | `action` 末维可到 ~0.03–0.09，但 `gripper_commanded=false`，`state` 末维保持 1.0 | 推理有闭爪意图，执行层 noop |
| C0 SmolVLA 50k 定性 | 水平对准优于 ACT；粗估约 **50%** 抓起；多数失败=闭合高度偏高；闭爪时轻移；抖动明显大于 ACT；卡顿不明显 | 视觉/对准强于 ACT；抓取环路（高度+闭时机）未充分收敛；抖动为次要矛盾 |
| C1 SmolVLA 50k 复测（开夹爪） | 一定程度分辨红蓝；**red 更准**；**blue 更靠红蓝中间**（相对 red 的趋近强度更弱） | 语言条件通道有效但偏弱/不对称；可能数据量、步数或颜色先验不均；不宜仅凭步数加长下结论 |
| 控制环 | 10 Hz；现场 RTT 多在 ~45–90 ms；逐步阻塞 `/infer` | 周期 100 ms 内有余量 → 卡顿不明显符合同步推理预期 |
| C0+C1 SmolVLA 50k 训毕 | loss ≈ **0.045**；150 ep | 未充分收敛；加长步数或扩数据仍有空间 |
| C0+C1 远距构型（4 次） | 构型1：四次朝向全对；构型2：1 次选红却朝蓝、3 次对 | 颜色门在远距下大体有效；偶有指令–朝向错配 |
| C0+C1 近距构型（2 次） | 红蓝各抓一次；均落**中间**，有目标色偏向 | 距离近时分色不够硬，倾向平均策略 |
| C0+C1 vs 25k | 明显更好 | 50k + 混合数据值得继续 |
| C0+C1 vs C0-only 50k | 水平对准退步 | 疑 C1 颜色条件与 C0 对准监督冲突；150 ep 仍少 |
| C0+C1 抓取 | 1 成功 + 1 近成功（高度偏高失败） | 抓取环路有进展；高度仍是主瓶颈之一 |
| C0+C1 抖动 | 仍明显 | 与 C0-only 类似；非当前主矛盾 |

### C0+C1 SmolVLA 50k：rollout 协议（定性）

**远距（红蓝固定、相距较远）**

- 流程：红蓝位置固定 → 红抓一次、蓝抓一次 → 交换红蓝位置后再各抓一次，共 **4 次**。
- **构型1**：四次机械臂朝向均与指令一致（选红朝红、选蓝朝蓝）。
- **构型2**：其中 **1 次**该朝红却朝蓝；其余 **3 次**朝向正确。

**近距（红蓝相对较近）**

- 流程：固定红蓝位置 → 红抓一次、蓝抓一次，共 **2 次**。
- 现象：两次均抓在**红蓝中间**，但对目标颜色有相对偏向。

**横向对照**

- 相对此前 **25k** checkpoint：**明显更好**（含 1 次成功抓取、1 次近成功）。
- 相对上午 **C0-only 50k**：**水平方向对齐变差**，可能受颜色条件干扰；150 条轨迹训练仍不充分。
- **抖动**依然明显；**loss 0.045** 暗示训练尚未吃满。

### C0 SmolVLA 50k：失败模式（定性）

1. **主因**：闭合高度相对偏高 → 水平很准仍「夹空」或未夹牢；
2. **次因**：闭合瞬间仍有轻微平移；
3. **抖动**：关节动作比 ACT 抖，但当前不是主失败源；关键帧加权是合理下一刀（优先改善抓取瞬间，而非先压抖）。

### Rollout 命令要点（本机）

```powershell
python scripts/rollout_smolvla_policy.py `
  --ip <arm-ip> `
  --infer-url "http://<server>:8089" `
  --source-object red `
  --destination up `
  --steps 300 `
  --control-hz 10 `
  --image-encoding jpeg_b64 `
  --config-enable-motion `
  --allow-motion `
  --enable-gripper `
  --gripper-config configs/local/gripper_serial.local.yaml
```

（真实 IP / 本地串口仅存 `configs/local/`。）

### 关于「默认是否异步推理」

**否。** `rollout_smolvla_policy.py` 每个控制步：采图 → `POST /infer` **阻塞等待** → 下发关节/夹爪 → rate limit。没有 ACT 的 `--mode async|blocking` 或 client 侧 `reset_every` chunk 队列。卡顿不明显，主要因为 10 Hz 下 RTT 通常小于 100 ms 周期。

## 新结论与决策

- SmolVLA **视觉对准**已明显强于 ACT-C0；当前瓶颈更像 **grasp 时序/高度**，不是「看不见物体」；
- 夹爪默认关闭是安全设计；真机验收必须显式 `--enable-gripper`（与 ACT rollout 一致）；
- C1-only 已能形成不对称分色（红强蓝弱），支持继续走 **C0+C1 混合** 而非再堆 ACT-C1；
- 关键帧加权值得做，但建议排在：**先完成 C0+C1 重训与对照 rollout** 之后，或作为 C0 高度失败仍主导时的并行实验；
- 暂不因抖动先上 async / 本地 chunk 播放；同步 10 Hz 已可接受。
- C0+C1 **50k 混合**相对 25k 有实质进步，颜色门在远距下大体可用（4 次里最多 1 次朝向错配）；近距仍偏「抓中间」；
- C0+C1 相对 C0-only 出现**水平对准退步**，暂归因于颜色干扰 + 150 ep 不足，而非立刻否定混合路线；
- 训毕 loss **0.045** → 加长步数或扩 `c0_c1` 数据是合理下一刀；严格「抓对颜色率」量化仍待补。

## 今日理解重点（15–30 分钟）

### 1. 推理意图 ≠ 硬件执行（gripper gate）

- **一句话**：日志里的 `gripper_open_fraction`（来自 action）与 `gripper_commanded`（是否真正下发夹爪）是两层。
- **为什么重要**：缺 `--enable-gripper` 时会把「模型不会闭爪」误判成策略失败。
- **本项目怎么做**：默认 `_NoOpGripper`；`--enable-gripper` 才挂 `_ThresholdGripper`（0.5 边沿）。
- **代码入口**：`scripts/rollout_smolvla_policy.py`（`_NoOpGripper` / `_ThresholdGripper`）。
- **自测问题**：为何 action 末维已 <0.1，而 `gripper_commanded` 仍可全程 false？

### 2. SmolVLA rollout 是逐步同步推理

- **一句话**：每步 HTTP `/infer` 阻塞；无 ACT 式 client 异步 refill。
- **为什么重要**：解释「为何卡顿不明显」与「抖动是否来自 async」要分开谈。
- **本项目怎么做**：`control_hz=10` 时周期 100 ms；RTT ~50–70 ms 通常仍能跟上。
- **代码入口**：`scripts/rollout_smolvla_policy.py` 主循环中的 `http_client.post("/infer", ...)`。
- **自测问题**：SmolVLA 与 ACT rollout 在「异步」上差在哪？

### 3. 对准好仍抓不起：高度与闭爪时机

- **一句话**：水平对准是必要条件；闭合高度偏高 + 闭爪瞬时位移会把「看起来对准」变成失败。
- **为什么重要**：下一刀应优先抓取环路（关键帧/高度相关监督），而不是先压轨迹抖动。
- **本项目怎么做**：C0 定性已定位该失败模式；关键帧加权可作为候选，但不替代 C0+C1 颜色主线。
- **代码入口**：ACT 侧已有 critical 加权先例（`train_act_critical` / datasets 记录）；SmolVLA 尚未同等落地。
- **自测问题**：为何「水平很准」仍可能约一半抓不起？

### 4. 颜色条件的不对称（红强蓝弱）

- **一句话**：语言条件有效，但不等于对称分色；红更准、蓝偏中间是数据/先验/步数综合现象。
- **为什么重要**：避免只加步数；应用固定构型 A/B 与「抓对颜色率」量化，并优先看 C0+C1 混合是否改善。
- **本项目怎么做**：C1-only 复测定性记录；正式结论等混合模型对照。
- **代码入口**：`serve_smolvla_policy` task 文本；数据集 `c1` / `c0_c1`。
- **自测问题**：为何「能偏蓝」仍不足以说颜色门已过？

### 面试式自测

1. 为什么开夹爪后，多数 step 的 `gripper_commanded` 仍可能是 false？
2. 同步 10 Hz 下卡顿不明显，能否推出「异步更好」？
3. C0 对准强于 ACT，是否应立刻用 SmolVLA 替换 ACT-C0 基线？

## 代码与文档变更

- 本日志：[`docs/daily/2026-08-06/log.md`](log.md)；计划勾选见 [`plan.md`](plan.md)；
- 索引：[`docs/daily/README.md`](../README.md)；
- 代码未改（今日以真机诊断与文档为主）。

## 验证

- 真机：C0 SmolVLA 50k 定性；C1 SmolVLA 50k 开夹爪复测；**C0+C1 SmolVLA 50k** 红蓝远/近距初步 rollout；
- 未做：严格格点成功率与「抓对颜色率」量化；C0 30k/40k。

## 未完成与阻塞

- C0 多 checkpoint（30k/40k）与固定协议成功率；
- C0+C1：**严格量化**（固定格点、抓对颜色率、近距 A/B 重复次数）；
- C0+C1 对准退步与近距「抓中间」：待扩数据或加长步数后再判；
- SmolVLA 关键帧加权配方未实现；
- SharedAutonomy 采集仍延后至颜色门更清晰。

## 已沉淀到长期文档

- 详细 rollout 观感暂留本 log；训完 C0+C1 并有量化对照后再同步 [`docs/datasets.md`](../datasets.md) / [`docs/roadmap.md`](../roadmap.md)。

## 下一工作日建议

1. **优先**：C0+C1 固定协议 rollout，主指标 **抓对颜色率**（远距交换 4 次 + 近距重复）；补严格统计；
2. 评估是否 **加长步数**（loss 0.045 未饱和）或扩 `c0_c1` 数据（150 ep 偏少）；
3. 若水平对准仍弱于 C0-only → 分析颜色干扰（数据配比 / 近距构型 / 是否需更多 C0 占比）；
4. 高度失败仍主导时 → SmolVLA **关键帧**实验（对齐 ACT critical）；
5. 抖动 / async：暂不排期。

## 自测参考答案

### 理解重点

1. **gripper gate — 自测问题**
   - 参考答案：默认 `_NoOpGripper.command_open_fraction` 恒返回 false，不驱动串口；只有 `--enable-gripper` 才真正下发。

2. **逐步同步 — 自测问题**
   - 参考答案：SmolVLA client 每步阻塞 `/infer`；ACT 另有 async 后台 refill 与 `reset_every` 本地 chunk 队列。

3. **高度失败 — 自测问题**
   - 参考答案：夹爪有效抓取窗口对高度敏感；闭合偏高或闭爪时平移会使指尖错过有效接触，即使 XY 对准。

4. **红强蓝弱 — 自测问题**
   - 参考答案：偏色趋势 ≠ 稳定按指令选色；需 A/B 构型下「指令色成功率」与错误模式（平均策略/先验）对照。

### 面试式自测

1. **为何多数 step `gripper_commanded` 仍 false**
   - 参考答案：`_ThresholdGripper` 只在开/关状态穿越 0.5 阈值时下发一次；稳态保持开或关时返回 false。

2. **同步不卡 ≠ 异步更好**
   - 参考答案：RTT < 控制周期时同步已够用；异步主要服务高延迟或要播完整 chunk，不能单独解决高度/抖动。

3. **是否立刻替换 ACT-C0 基线**
   - 参考答案：不宜。ACT r5 在抖动与抓取稳定性上仍可能更稳；SmolVLA 先证明颜色门与抓取环路，再谈替换或双轨部署。
