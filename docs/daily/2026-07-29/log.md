# 2026-07-29 工作日志

## 今日计划

参见 [plan.md](plan.md)。

## 完成情况

- [x] P0-1：keep-alive RTT 配对 bench + `_json_response` 单次 write 修复 + 复测（rtt mean 52.9 / p95 60.2）；
- [x] rollout 前 A/B：观测敏感度（木块三位置 action 差 4–5°）、JPEG vs raw 同帧（关节差 <0.2°）；
- [x] `scripts/rollout_act_policy.py` + `sharedautonomy/policies/act/rollout.py`（blocking/async、`reset_every` 本地 chunk 队列）；
- [x] dry-run 30 步（blocking，`reset_every=25`）+ 真机 rollout **9 次**（双确认 + go-to-ready + safety clip）；
- [x] 第一版「还要多少 Manual 数据」判断（见下表与结论）；
- [x] `pytest -m core` 全绿（83 passed）；
- [x] P0-3：C0 采集 20 条（`train-061`…`080`）+ 补采 10 条（`081`…`090`）+ 纠偏段 10 条（`091`…`100`）；batch check 全 PASS；export `shape_pick_place_v1_c0`（30 / 6346，091+ 待 resume）；`act_c0` 20k 训完 + rollout 4 次 + 离线 MAE；`act_c0_r2` 30k 训完 + rollout 3 次；
- [x] 收工「今日理解重点」与自测参考答案；
- [ ] roadmap Week 1.5 全文勾选同步（rollout 项已在本 log 结论中关闭）——延至明日。

## 实验与结果

### Rollout 逐次记录（真机，`act_manual_v003`，`reset_every=25`，blocking）

| # | 条件 | 场景扰动 | 结果 | 主要失败模式 | 备注 |
| --- | --- | --- | --- | --- | --- |
| 1 | blue → down | 默认布局 | 失败 | `grasp_miss` | 接近目标，反复尝试未夹住 |
| 2 | red → down | 默认 | 失败 | `condition_confusion` | 仍朝 **blue** 方向 reaching |
| 3 | yellow → down | 默认 | 失败 | `condition_confusion` + `grasp_miss` | 同 #2，朝 blue，未抓到 |
| 4 | blue → down | **三块整体换位置** | 失败 | `spatial_prior_bias` | 不跟指定色块，更像朝**固定/平均区域** |
| 5 | yellow → down | 默认（块位已变） | 失败 | `false_grasp_then_place` | 未夹住仍**闭爪移向 down** 放置 |
| 6 | yellow → up | 默认 | 失败 | `destination_confusion` | reaching 偏差；仍往 **down** 侧移动并在 down 上方松爪 |
| 7 | yellow → up | **目标整体推向 up** | 失败 | `spatial_prior_bias` + `false_grasp_then_place` | 夹爪朝**三块中间**；未夹住仍闭爪往 up |
| 8 | yellow → up | **目标整体推向 down** | 失败 | `destination_confusion` | 夹爪跟随块群但**最终仍往 down** |
| 9 | yellow → up | **三块推向 up** | 失败 | `destination_confusion` + `non_termination` | 未跟块群中间，偏 down 空区→移到 up 松爪；**超时后仍在放置区微调** |

**汇总**：成功 **0 / 9**；无撞桌/危险外漂；reaching 粗方向多数合理；**6 条件文本/颜色绑定不稳定**；place 半区与「是否已抓住」门控不可靠。

### 其他验证

| 项目 | 结果 | 结论 |
| --- | --- | --- |
| RTT bench + 单次 write 修复 | keep-alive rtt mean **52.9** / p95 **60.2**（修复前 100–110） | 7/28「阻塞触顶」中 ~50ms 为响应分段 bug；keep-alive 保留 |
| 观测敏感度 A/B | 木块三位置首步 action j2/j3 差 **~4–5°** | 模型看观测；昨日「移块无响应」= chunk 盲播 |
| JPEG vs raw 同帧 A/B | 关节差 **<0.2°**；raw rtt ~653ms vs jpeg ~137ms | rollout 继续 `jpeg_b64` |
| dry-run 30 步 | replan ×2；queue 0→24→0→24；replan rtt ~105–141ms | runner 队列逻辑正确；reaching action 方向合理 |
| `pytest -m core` | **83 passed** | 回归绿 |

### C0 课程学习（下午，`shape_pick_place_v1_c0`）

| 项目 | 结果 | 结论 |
| --- | --- | --- |
| C0 采集 | `train-061`…`080` ×20 + `081`…`090` ×10；`batch_check_episodes` 全 PASS；`blue→up` 单块无干扰 | 见 [`datasets.md`](../datasets.md) |
| Export | 初版 20 ep → `--resume` 追加 10 ep；**30 / 6346**；已 scp 服务器 | 勿覆盖 v002/v003 |
| `act_c0` 训练 | 20 ep × **20k** steps；checkpoint `outputs/train/act_c0/checkpoints/last` | 训练日志未留档（前台跑、无 `tee`） |
| 离线开环 MAE | ep0×20 帧：`mae_joints=1.010°`，`max_abs=2.789°`，`gripper_mae=0.033`（`meta.episodes` 索引；避开 `resolve_dataset_frame_index` 全库扫描） | 与 `act_manual_v002`（12 ep×50k）的 **1.05°** 同量级 → **20k 步充分**；rollout 抓偏更像数据覆盖/闭环纠偏，非步数不足 |
| C0 rollout（`act_c0`） | **4 次**（单块 `blue→up`） | reaching 朝 blue 正确；末端距块 **~3cm 内** 再抓；能抓起；每 chunk replan 有「小圈再朝 up」artifact（blocking + `reset_every=25`） |
| `act_c0_r2` 训练 | 30 ep × **30k** steps；`output_dir=outputs/train/act_c0_r2`；`tee` 留 `act_c0_r2.log` | 训完即 rollout |
| C0 rollout（`act_c0_r2`） | **3 次**（同口径单块 `blue→up`） | 明显更接近抓取；**1/3 抓起**（但从**木块本体**抓起，非把手）；打圈 artifact 仍存在 |
| 纠偏段补采 | `train-091`…`100` ×10（接近—修正段，约占批次 1/3）；`batch_check_episodes --require-success` PASS；已 check 未 export | 待 `--resume` 进 c0 → `act_c0_r3`（40 ep）从头训；不覆盖 `act_c0`/`act_c0_r2` |
| 打圈归因修正 | `act_manual_v003`（60 ep × 150k，6 条件）rollout **无**打圈 | 非 chunk 架构必然；更像**单条件/小数据下 chunk 首步过冲先验**，随数据量与多样性应消退；`--reset-every 10` 作可选缓解，不动 runner |

**C0 判决（修订版）**：单块基础环路成立；**边际收益测量成立**（20→30 条：0/4 → 1/3 抓起，~3cm 抓偏明显缩小）；剩余两个短板各有定性——**闭环 grasp 精度** → 纠偏段数据（`091`…`100` 已采）；**把手 vs 本体高度语义** → 双 RGB 单目视角下 z 轴差异难分辨（MAE 1.0° 噪声以下，开环测不出），对策见「新结论」。

## 新结论与决策

- **Week 1.5 rollout 首轮回合已完成**：链路（观测→云端 infer→chunk 播放→safety 下发）成立；**不是** RTT/异步推理主矛盾。
- **第一版数据量判断**：60 条 `v003` 已学到**安全粗粒度结构**（朝目标区域 reaching、尝试闭合、朝 A4 放置区移动、无撞桌），但**不足以稳定学习 6 条件**（颜色 + up/down）绑定；主瓶颈是**条件分辨与 grasp 精度**，非 runner 调参。
- **失败模式分类**（rollout 记录用）：`grasp_miss` / `false_grasp_then_place` / `condition_confusion` / `spatial_prior_bias` / `destination_confusion` / `non_termination`。
- **下一步优先级（收工版）**：① `091`…`100` `--resume` 进 c0 → `act_c0_r3`（40 ep，从头训，不覆盖 r2）；② r3 rollout 复测（前 2–3 次复用 r2 块位保证可比）；③ 打圈观察是否随数据量消退；④ `resolve_dataset_frame_index` 扫描 fallback 待修；⑤ Phase 3 `_v004` 与异步推理继续**暂缓**。
- **打圈归因（修订）**：非 chunk 架构 artifact——`v003`（60 ep × 150k，6 条件）同 runner 同 `reset_every=25` 不打圈；`act_c0`/`act_c0_r2`（20–30 ep 单条件）都打。倾向解释：**单条件小数据下 replan chunk 首步的「先调整再出发」过冲先验**；预计随数据量/多样性消退，不动 chunk 策略，`--reset-every 10` 留作可选缓解。
- **把手语义失败（新失败模式 `grasp_wrong_height`）**：r2 唯一一次成功是从木块本体抓起而非把手。操作者确认演示 100% 抓把手 → 不是坏样本；诊断为**双 RGB 单目视角下把手/本体 z 轴差异视觉不可分辨**（对应关节差 ~1°，低于开环 MAE 噪声）。对策：采集时**把手高度悬停 1–2s 再下降闭合**（形成可分辨帧段）；`replay_episode` 检查腕部帧把手可分辨性；中期可评估腕部深度（ADR 0002 §7 `--include-depth`），当前不动 schema。
- **边际收益方法论（已验证）**：单变量推进（20→30→40 ep），同位姿复测对照；+50% 数据换来 0→非零成功，sample-efficiency 曲线未平 → 继续按课程补数据，而非换表示/换架构。
- **动作空间讨论（记录，不行动）**：笛卡尔 EE-delta 在小数据 + 空间泛化上理论更优（overview §3.2 原本就写 Δx,Δy,Δz；ADR 0002 为对接 ACT/直接 replay 选了关节空间），但切换面覆盖 export schema、在线 IK、safety 校验与全部已训 checkpoint；且治不了当前 3cm/把手语义问题。**待 C0/C1 结论落定后，用同一份数据 export EE 版做干净 A/B 再决策**（届时开 ADR 0003）。
- **chunk 策略（已决策）**：`--reset-every 25` + `--infer-mode blocking`；本地 refill 队列；不优先 temporal ensemble。
- **runner（已决策）**：`scripts/rollout_act_policy.py`（不复用 teleop runner）；`sharedautonomy/policies/act/live_infer.py` 与 dry-run 共用观测 HTTP 层。

## 今日理解重点（15–30 分钟）

### 1. 开环 MAE 与闭环 rollout 测的是两件事

- **一句话**：离线开环 MAE 衡量「policy 在训练分布上的逐帧拟合度」；闭环 rollout 还叠加误差累积、分布漂移与**视觉不可分辨**导致的语义错误——MAE 好不等于 rollout 好。
- **为什么重要**：`act_c0` MAE 1.01° 打平 `act_manual_v002` 的 1.05°，但 rollout 差 3cm；「抓本体不抓把手」的关节差 ~1° 完全淹没在 MAE 噪声里。诊断闭环问题必须上真机或至少做闭环回放。
- **本项目怎么做**：MAE 仅用于横向比较「训练是否充分」（同口径：ep0×20 帧、preprocessor + `select_action`）；任务级成功/失败模式只信 rollout 表。
- **代码入口**：`sharedautonomy/policies/act/runtime.py`（`infer` / `infer_dataset_frame`）；本 log C0 实验表。
- **自测问题**：为什么 `mae_joints=1.01°` 的模型 rollout 还会差 3cm 抓不上？把手/本体混淆为什么开环测不出？

### 2. 纠偏（recovery）数据与 DAgger 直觉

- **一句话**：全干净演示里没有「偏了怎么办」的状态—动作对；掺少量纠偏段（偏→果断修回）给闭环 OOD 状态补上监督。
- **为什么重要**：BC 学的是逐帧条件映射 P(action|state)；只要纠偏段每帧动作仍指向目标，模型学到的是「偏态 → 修回」，不会学到「故意走偏」。
- **本项目怎么做**：`train-091`…`100` 十条纠偏（接近时刻意偏 2–3cm 再修回）；比例 ~1/3 上限（防止偏态成为分布主体）；纠偏必须果断、一次成型，犹豫条目按失败删；`replay_episode` 目视抽检纠偏质量。
- **代码入口**：`scripts/replay_episode.py`；metadata `success=true` 之外纠偏质量靠人工把关。
- **自测问题**：纠偏数据为什么不会教模型「先走偏」？比例失控（如 80% 纠偏）会发生什么？

### 3. ACT chunk 边界行为：「打圈」不是架构税

- **一句话**：blocking + `reset_every=N` 下每次 replan 输出新 chunk，新旧 chunk 动作不保证连续；但「打圈」是否出现取决于**模型**，不是架构必然。
- **为什么重要**：`act_manual_v003`（60 ep × 150k）与 `act_c0`（20 ep × 20k）同 runner 同参数，前者不打后者打——说明打圈是单条件小数据下 chunk 首步过冲先验，预计随数据量消退；误判为架构问题会引向 temporal ensemble 等过度工程。
- **本项目怎么做**：维持 `--reset-every 25` + blocking；`--reset-every 10` 为可选缓解（纯 CLI）；观察 r3（40 ep）是否减轻，作为归因的第三个数据点。
- **代码入口**：`sharedautonomy/policies/act/rollout.py`（refill 队列）；`scripts/rollout_act_policy.py`。
- **自测问题**：为什么说「v003 不打圈」排除了「打圈 = chunk 架构 artifact」？单条件数据为什么更容易打出小圈？

### 4. 课程式采集 vs 全量平铺

- **一句话**：C0（单块单条件）→ C1（加干扰）→ C3（全 6 条件）的难度梯度，比直接采 6 条件更能用好每条数据；各阶段数据全部复用进后续混合训练。
- **为什么重要**：v003 证明 60 条 Phase 3 平铺学不出 6 条件绑定（「去中间」成为最优平均策略）；20 条 C0 已学会 reaching + 粗糙抓放——同样的条数，组织结构决定效果。
- **本项目怎么做**：`docs/datasets.md` §2 维护 C0–C3 定义；每阶段判决信号决定是否进阶；v003 保留供 C3 混合。
- **代码入口**：[`docs/datasets.md`](../datasets.md)；任务卡 §7。
- **自测问题**：为什么不能把 C0 的 80 条目标直接平摊成 6 条件各 13 条？

### 5. 离线评估脚本的索引陷阱（`resolve_dataset_frame_index`）

- **一句话**：LeRobot 0.6 的 episode 索引在 `dataset.meta.episodes`（`dataset_from_index/to_index`）；自建的 `episode_data_index` 探测分支失效后会掉进 O(N) 全库扫描，每次 `dataset[i]` 还解码两路 MP4。
- **为什么重要**：今天 600% CPU 烧 20 分钟无一帧结果，就是 fallback 触发后的视频解码风暴；同一坑已咬两次（`infer_dataset`、MAE 脚本）。
- **本项目怎么做**：临时脚本统一走 `meta.episodes`；正式修复 = 删 `runtime.py` 扫描分支（见「未完成」）。
- **代码入口**：`sharedautonomy/policies/act/runtime.py:174`（`resolve_dataset_frame_index`）。
- **自测问题**：为什么这个 fallback 的代价是「20 分钟」而不是「慢 10%」？

### 面试式自测

先只读问题，自己作答；答案见文末。

1. 一个模型离线 MAE 1.0°，另一个 1.2°，能否判定前者 rollout 成功率更高？为什么？
2. 采 10 条纠偏轨迹时，纠偏段里操作者犹豫了 3 秒来回晃动，这条该留吗？依据是什么？
3. 如果 r3（40 ep）rollout 打圈明显减轻，支持什么结论？如果反而加重呢？
4. `act_c0_r2` 从头训而不是 resume `act_c0`，为什么？

## 代码与文档变更

- `sharedautonomy/policies/act/live_infer.py`：HTTP 客户端 + 真机观测构建（dry-run 改引用）；
- `sharedautonomy/policies/act/rollout.py`：chunk 播放、blocking/async refill、`clip_joint_targets` 下发；
- `scripts/rollout_act_policy.py`：真机 rollout CLI；
- `scripts/serve_act_policy.py`：`_json_response` 单次 write（RTT 修复）；
- `tests/test_act_rollout.py`：rollout 单元测试；
- 本 log、`plan.md` 勾选更新；
- [`docs/datasets.md`](../datasets.md)：native 池与 LeRobot 快照清单（C0 / `act_c0_r2`）。

## 验证

- `pytest -m core`：83 passed（覆盖当日全部代码改动：RTT 修复、rollout runner、`live_infer.py`、`test_act_rollout.py`）；收工前因本机 shell 异常未重跑——下午仅数据采集、rollout 与文档变更，代码面无新改动；
- 真机：dry-run 30 步 + rollout 9 次（`act_manual_v003`）+ rollout 4 次（`act_c0`）+ rollout 3 次（`act_c0_r2`）；
- 数据：`batch_check_episodes --require-success` PASS（061–080、081–090、091–100 分批）；`shape_pick_place_v1_c0` `LeRobotDataset` 加载 30 / 6346；
- 未验证：091–100 尚未 export（用户下班前执行 `--resume` + `act_c0_r3` 训练）；全量 `pytest`；失败上下文导出。

## 未完成与阻塞

- `091`…`100` `--resume` 进 c0 + scp + `act_c0_r3`（40 ep × 30k，从头训，`tee` 留日志）——用户收工前启动；
- r3 rollout 复测与 C0/C1 分支决策（明日）；
- roadmap Week 1.5 勾选同步（明日顺手）；
- Phase 3 `_v004` export/重训（与 C 系列并行，非阻塞）；
- `infer_dataset` 修复**正式暂缓**（真机走 `/infer`，延迟基准走 bench；等有离线 dataset 评估需求再修）；
  - **追加（下午）**：`resolve_dataset_frame_index`（`sharedautonomy/policies/act/runtime.py:174`）的全库扫描 fallback 第二次咬人——离线开环 MAE 脚本在服务器 LeRobot 版本上未命中 `"from"/"to"` 分支，退化为逐索引 `dataset[i]` 解码两路 MP4（600% CPU、20+ 分钟无结果，已 Ctrl-C）；临时绕过：用 `dataset.meta.episodes` 的 `dataset_from_index/to_index` 取全局索引。正式修复时把扫描分支删掉并统一走 `meta.episodes`；
- 动作空间（关节 vs EE-delta）A/B 实验：待 C0/C1 结论落定后另立 ADR；
- rollout 失败上下文轻量导出（Week 5 前置，可选）。

## 已沉淀到长期文档

- C0 快照 / native 池 / 训练对应关系 → [`docs/datasets.md`](../datasets.md)（30 / 6346；091–100 待入）；
- C0–C3 课程阶段定义 → `docs/datasets.md` §2；
- rollout 结论与 Week 1.5 勾选 → [`docs/roadmap.md`](../roadmap.md)（今日上午已同步）。

## 下一工作日建议

1. 确认 `act_c0_r3` 训练完成（收工前启动），rollout 3–4 次复测：**前 2–3 次复用 r2 块位**保证 20→30→40 边际曲线可比；记录抓起率、是否仍抓本体、打圈是否减轻；
2. 分支决策：抓起率继续升 + 打圈消退 → C0 关闭，进 **C1**（加干扰块）；仍抓本体 → 采集加「把手高度悬停 1–2s」+ 检查腕部视角，必要时再补批；
3. （顺手）roadmap Week 1.5 勾选同步；
4. （并行可开工）Week 2 SA 采集器脚手架。

## 自测参考答案

### 理解重点

1. **开环 vs 闭环 — MAE 1.01° 为何 rollout 还差 3cm？把手/本体为何开环测不出？**
   - 参考答案：MAE 是在训练分布内逐帧比对预测 action 与录制 action，测的是「见过状态的拟合度」；闭环时小误差每步累积把系统推出训练分布（covariate shift），模型在 OOD 状态的行为 MAE 完全覆盖不到。把手 vs 本体混淆时，预测 action 与「正确 action」的关节差只有 ~1°，对 MAE 的贡献淹没在 1.0° 的平均噪声里——且这两种状态的图像在训练分布中都出现过、都有各自的「正确标签」，模型在分布内「学会」了错误的高度语义，开环逐帧比对当然测不出。

2. **纠偏数据 — 为什么不会教模型「先走偏」？80% 纠偏会怎样？**
   - 参考答案：BC 学的是 P(action|state) 逐帧映射。纠偏段里每一帧的动作都指向「修回目标」，不存在「故意偏离」这个动作标签，模型无从学起。但 80% 纠偏会让「偏态」成为状态分布的主体：模型大部分训练信号是纠偏动作，闭环时它会更频繁地处于偏态（因为偏态是它「最熟悉」的状态），形成自我强化的偏态循环；同时干净接近段的样本被稀释，首要接近精度反而下降。经验上限 ~1/3。

3. **打圈归因 — 「v003 不打圈」排除了什么？单条件为何更易打圈？**
   - 参考答案：排除了「打圈 = blocking + reset_every 架构必然产物」——同 runner 同参数下 v003 不打，说明架构只提供「不连续的可能性」，是否兑现取决于模型输出的 chunk 内容。单条件数据的轨迹起点高度相似（同一 ready、相似块位），模型在 replan 时 chunk 首几步带有训练分布里「先调整姿态再出发」的强先验，与当前臂形叠加成过冲；6 条件 × 随机块位的 v003 把这个先验稀释掉了。

4. **课程式采集 — 为什么不能把 80 条平摊成 6 条件各 13 条？**
   - 参考答案：6 条件同时在场时「去中间」是条件绑定学不会时的最优平均策略，模型先用容量学了这个捷径，每条件 13 条不足以打破它（v003 每条件 10 条已实证失败）。课程式先让模型在单条件下把「看块→抓→放」基础环路学扎实（条件混淆不存在），再逐级引入选择歧义，每一步的条件绑定都是在已掌握的技能上学增量，样本效率高得多；且前期数据全部复用。

5. **索引陷阱 — 为什么 fallback 的代价是 20 分钟而不是慢 10%？**
   - 参考答案：复杂度从 O(1) 变成 O(N) 且常数极大：全库扫描要对 ~2500 个全局索引逐个 `dataset[i]`，而每次取 item 都触发该帧两路 MP4 的解码（torchcodec 多线程，即 600% CPU 的来源）。2500 次 × 每次数百毫秒解码 ≈ 十几分钟起步，且绝大部分解码出来的帧都被丢弃。这不是「慢一点」，是数量级×常数双重爆炸。

### 面试式自测

1. **MAE 1.0° vs 1.2° 能否判定前者 rollout 更强？**
   - 参考答案：不能。MAE 只在同口径横向比较「训练充分度」时有意义；rollout 成功率还取决于闭环误差累积、OOD 行为、视觉可分辨性与数据构成（纠偏段有无）。两个 MAE 同量级的模型，一个有纠偏数据一个没有，rollout 表现可能完全不同。

2. **纠偏段犹豫了 3 秒来回晃动，这条该留吗？**
   - 参考答案：不留，按失败删重录。犹豫帧的动作不指向目标（或频繁变向），模型会学到「偏了之后先愣一下/晃一下」——这正是要避免的模式。纠偏必须果断、一次成型；`replay_episode` 目视可检出。

3. **r3 打圈减轻 / 加重各说明什么？**
   - 参考答案：减轻 → 支持「打圈 = 单条件小数据的 chunk 首步过冲先验」，预计随 C1 多样性进一步消退，维持不动 runner。加重 → 归因错误，打圈与数据量无关，需要重新审视 chunk 策略（`reset_every`、chunk 连续性约束或 temporal ensemble），且要在 r3 与 r2 同位姿对照下排除场景差异。

4. **`act_c0_r2` 为什么从头训而不是 resume `act_c0`？**
   - 参考答案：数据分布变了（20 → 30 ep），resume 会让模型在已拟合 20 条的参数上继续，对新样本的拟合权重不均、且步数/学习率 schedule 与从头训不可比；更重要的是**对照实验需要单变量**——r1 与 r2 只有「数据量」一个变量不同（同步数口径、同从头训），边际收益才可归因。resume 会把「训练总量」也变成变量。
