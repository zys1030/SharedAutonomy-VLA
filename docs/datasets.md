# 数据集与采集清单

本文档是 **native 采集池** 与 **LeRobot 导出快照** 的当前状态索引：每份数据「是什么、从哪来、训过谁」。课程采集配方（C0 归档 / C1-lite）见 **§2.1**。设计规则（schema、`repo_id`、特征映射）见 [ADR 0002](decisions/0002-lerobot-export-mapping.md)；任务定义见 [任务卡](tasks/shape_pick_place_v1.md)；当日过程写 [daily log](daily/)，**事实变更同步回本表**。

`outputs/datasets/`、`outputs/runs/`、`outputs/train/` 均为运行产物，不入库。

---

## 1. 命名约定


| 概念         | 约定                                                                               |
| ---------- | -------------------------------------------------------------------------------- |
| `repo_id`  | 始终 `local/shape_pick_place_v1`（任务语义不变就不改）                                        |
| 快照目录       | `outputs/datasets/shape_pick_place_v1_<tag>/`；`<tag>` = `v001` / `v002` / `c0` … |
| 覆盖策略       | **禁止**覆盖已有 `--out-root`；追加用 `--resume`，新实验换新目录                                   |
| native run | `outputs/runs/shape-pick-place-<phase>-NNN/`，`episode/` 下含 `metadata.json`       |
| 训练目录       | `outputs/train/<job_name>/`；与快照一对一或一对多，见 §4                                      |


`<tag>` **语义建议**

- `v00N`：全任务卡（Phase 3）Manual 快照，按导出次序递增；
- `cN`：课程学习阶段快照（见 §2），与 `v00N` 并行编号，互不覆盖。

---



## 2. 课程阶段（C0–C3）

承接任务卡 §7 与 2026-07-29 rollout 结论（60 条 Phase 3 不足以学 6 条件绑定）。阶段用于**采集难度梯度**，不是 LeRobot schema 版本。可执行采集配方见 **§2.1**。


| 阶段     | 场景                                                 | 目的                |
| ------ | -------------------------------------------------- | ----------------- |
| **C0** | 视野内**只有目标块**；单条件（`blue → up`）；拾取区随机 `(x,y,yaw)` | 验证「看块→抓→放」基础环路可收敛 |
| **C1** | **红+蓝同桌**；目标为 `red→up` 或 `blue→up`；另一色为干扰；**暂不做 down / 黄** | 学会按条件选对颜色再抓放 |
| **C2** | 3 色 × 1 区 或 1 色 × 2 区等                             | 过渡到完整组合           |
| **C3** | 完整 6 组合 + 拾取区随机；可与既有 `v003` 混合                     | 回到任务卡全量；混合训练复用旧数据 |


判决顺序：先训 `act_c0` → 单块 rollout 可用 → 进 **C1**（颜色选择）；C1 颜色大致可分后再考虑 down / 黄 / SharedAutonomy 对照。

### 2.1 采集方案（C0 / C1）

#### C0（已完成；配方归档）

| 项 | 约定 |
| --- | --- |
| 场景 | 桌上**仅蓝色块**；`source_object=blue`，`destination=up` |
| 成功标准 | 抓住把手有效区 → 放到 A4 **UP** 半区；失败条删除不计入 |
| 空间覆盖 | 近/中/远 × 左/中/右；格内 ±2–3 cm 抖动；长短边轮换；评估可用固定格点，训练用分层+抖动 |
| 实际构成（90 ep） | 直接抓为主；纠偏段 / 偏差修正 / 闭合前局部调整混合 / 远侧定向补采穿插（见 §4） |
| 训练基线 | `act_c0_r5_critical_b8x2` **200k**（勿覆盖）；细节见 §5 |
| 停损 | 边际收益变空间权衡（如 r6）→ 锁基线，不再以加步/大规模补数为 C0 主策略 |

历史判决出处：[`daily/2026-07-31/log.md`](daily/2026-07-31/log.md)（+20 分层配方）、[`daily/2026-08-03/`](daily/2026-08-03/)、[`daily/2026-08-05/log.md`](daily/2026-08-05/log.md)（基线锁定）。

#### C1-lite：红蓝同桌、只学颜色（native 60 ep；已 export 40 ep，新增 20 条配对消歧待处理）

**目标**：模型在**两色同时在场**时，按 `task_text` / `source_object` 抓对颜色并放到 `up`；暂不实现 SharedAutonomy；暂不做 `down`、不做黄块。

| 项 | 约定 |
| --- | --- |
| 场景 | 拾取区**同时**放红块 + 蓝块；间距 ≥ 30 mm（任务卡 §2.3） |
| 条件 | 仅两种：`red → up`、`blue → up`；每条 episode 只完成一个条件 |
| CLI | `--source-object red\|blue --destination up`（勿漏标，否则颜色监督无效） |
| 干扰块 | 非目标色留在桌上、**不碰、不挪走**；轨迹全程朝目标色 |
| 放置 | 一律 `up`；down 半区本阶段不用 |
| 空间 | 目标与干扰各自随机 `(x,y,yaw)`；**目标相对干扰的左右/前后大致均衡**（避免「总是抓左边」捷径） |
| 示教风格 | **约 80% 干净直接抓**；约 20% 接近后小幅修正（对齐 C0 有效经验）；勿故意先伸向错误颜色再改 |
| 失败处理 | 抓错色 / 碰飞干扰 / 未放到 up → **删条重采**，不硬计入 |

**建议条数（首轮）**

| 批次 | 条数 | 构成 | 用途 |
| --- | --- | --- | --- |
| 首轮 | **40** | `red→up` ×20 + `blue→up` ×20 | 颜色绑定 smoke；够训一版看会不会串色 |
| 可选加量 | +20～40 | 按失败模式补：串色 → 补「近邻干扰 / 交换左右」；抓偏 → 按 C0 对准补 | 凑到 **60～80** 作薄基线 |
| 上限（本阶段） | **≤80** C1 新条 | 仍不够分色再查条件注入 / 图像，而不是盲目堆到 150+ | 避免再开一轮 C0 式无限补数 |

可与已有 **C0 `blue→up` 单块数据混合训练**（保留抓放先验）；**不要**把未筛选的 `v003` 六条件全集直接灌进 C1 主训。

**run_id / 快照**

- native：接续 `shape-pick-place-train-151` 起（或当日空号段）；metadata 写清 `source_object` / `destination`；
- export：新目录 `outputs/datasets/shape_pick_place_v1_c1`（**禁止**覆盖 `..._c0`）；追加用 `--resume`；
- check：`batch_check_episodes --require-success`；条件统计用 `summarize_episode_conditions`。

**训练注意（重要）**

1. **不要在 `act_c0_r5_critical_b8x2` 上直接 `--resume` 续训同一 job。** r5 只见过单块 blue；同目录续训会污染 C0 基线对照，且 optimizer/步数语义混乱。
2. **新开** `outputs/train/act_c1_rb_up_...`（新 `output_dir` / `job_name`）。
3. 推荐：`c0` 快照（或子集）+ `c1` 快照 **混合、从头训**；若要热启，也只是「新目录加载 r5 权重再训」，不是 resume r5 的 train_config。首轮也可 **仅 C1 40 条** 做纯分色 smoke，再决定是否加回 C0。
4. 关键帧加权可沿用 C0 流程；超参先沿用 r5（batch、fp16、`reset_every=25`），不为此开网格搜索。
5. Rollout 协议：桌上始终红+蓝；各色至少测若干次；**主指标 = 抓对颜色率**，其次才是抓稳/放到 up。

**验收门（首轮后）**

| 现象 | 动作 |
| --- | --- |
| 明显串色（经常抓干扰色） | +20 近邻/交换左右；核对 `task_text` 是否进训练 |
| 颜色对、对准差 | 少补对准示教；或混合更多 C0，勿只堆条数 |
| 两色大致可分、中部可用 | 锁 C1 薄基线；再考虑 SA 或 down/黄 |
| 40 条完全无颜色信号 | 先查 export / 条件字段，停采 |

**本阶段不做**

- SharedAutonomy 采集；`down`；黄块；完整 6 条件；在 r5/r6 目录上覆盖或 resume 当 C1。

---



## 3. 已导出快照

`repo_id` 均为 `local/shape_pick_place_v1`。帧数以本机 / 服务器 `LeRobotDataset` 加载为准；若与下表不一致，以加载结果改表。


| 快照                         | 来源 runs           | ep / frames | 条件分布                  | 场景备注                           | 对应训练                            | 状态                             |
| -------------------------- | ----------------- | ----------- | --------------------- | ------------------------------ | ------------------------------- | ------------------------------ |
| `shape_pick_place_v1_v001` | `pilot-001`…`003` | 3 / 1040    | red→up ×2，red→down ×1 | Phase 0/1 pilot；`phase: pilot` | `act_smoke_v001`（100 steps）     | 管道 smoke；**不作效果评估**            |
| `shape_pick_place_v1_v002` | `train-001`…`012` | 12 / 2800   | 黄/红/蓝 × up/down × 2   | Phase 3 种子；三块在场；`phase: train` | `act_manual_v002`（50k）          | 首轮真训标定；勿覆盖                     |
| `shape_pick_place_v1_v003` | `train-001`…`060` | 60 / 12886  | 6 组合 × 10             | Phase 3 扩量；含 v002 的 12 条       | `act_manual_v003`（150k）         | 真机 rollout 0/9；**保留**供后续 C3 混合 |
| `shape_pick_place_v1_c0`   | `train-061`…`150` | **90 / 19518** | blue→up × 90 | **C0**：单块、无干扰；`destination=up`；构成见 §4（44 直接抓 + 16 纠偏段 + 20 闭合前局部调整混合 + 10 远侧目标定向补采） | `act_c0`…`r4`；`r4_critical`；**`r5_critical_b8x2`（80 ep，200k，C0 基线）**；`r6_critical_b8x2`（90 ep，至 400k，远侧实验） | 2026-08-05：**基线锁定 r5-200k**；r6 不替换；停止以加步/大规模补数为 C0 主策略 |
| `shape_pick_place_v1_c1`   | `train-151`…`190` | **40 / 8451** | `blue→up` ×20，`red→up` ×20 | **C1-lite**：红蓝同桌；目标色以外的块作为干扰；一律放置到 `up` | `act_c1_rb_up_ft50k_from_r5`（50k） | 2026-08-05：首轮 40 ep / 8451 frames 已完成；基于 C0 r5-200k 权重热启动；待红蓝同桌 rollout |


仍计划中、尚未落地：


| 快照（拟定）                     | 预期内容                  | 备注                       |
| -------------------------- | --------------------- | ------------------------ |
| `shape_pick_place_v1_v004` | 条件分辨导向扩量后的 Phase 3 全集 | 勿覆盖 v002/v003；与 C 系列独立决策 |


---



## 4. Native 采集池（含未进快照）

按 `run_id` 区间维护。条件分布可用：

```text
python scripts/summarize_episode_conditions.py --run-glob "shape-pick-place-train-*" --success-only
python scripts/batch_check_episodes.py --run-glob "shape-pick-place-train-*" --require-success
```


| run 区间            | 条数（约） | 内容                | 已进快照      | 备注                     |
| ----------------- | ----- | ----------------- | --------- | ---------------------- |
| `pilot-001`…`003` | 3     | red×up/down pilot | v001      |                        |
| `train-001`…`012` | 12    | Phase 3 种子        | v002、v003 |                        |
| `train-013`…`060` | 48    | Phase 3 扩至 6×10   | v003      |                        |
| `train-061`…`080` | 20    | C0 blue→up，单块直接抓 | **c0**    | 2026-07-29 初版 export      |
| `train-081`…`090` | 10    | C0 blue→up 补采，直接抓 | **c0**    | batch check PASS；`--resume` 追加 |
| `train-091`…`100` | 10    | C0 blue→up **纠偏段**（接近—修正） | **c0**    | batch check PASS；`--resume` 追加（当时 40 / 8701） |
| `train-101`…`114` | 14    | C0 blue→up **直接抓** | **c0**    | 2026-08-03；batch check PASS；`--resume` 追加 |
| `train-115`…`120` | 6     | C0 blue→up **偏差修正** | **c0**    | 2026-08-03；batch check PASS；`--resume` 追加后合计 **60 / 13028** |
| `train-121`…`140` | 20    | C0 blue→up **闭合前局部调整混合**（有的最后明显调整，有的边接近边调；未再细分类型） | **c0**    | 2026-08-03；batch check PASS；`--resume` 追加后合计 **80 / 17327** |
| `train-141`…`150` | 10    | C0 blue→up **远侧目标定向补采** | **c0**    | 2026-08-04；新增后 C0 合计 **90 ep / 19518 frames**；用于下一轮远侧表现验证 |
| `train-151`…`170` | 20    | C1 blue→up；红蓝同桌；目标蓝块，红块为干扰 | **c1**    | 2026-08-05；metadata 标注 `source_object=blue`、`destination=up` |
| `train-171`…`190` | 20    | C1 red→up；红蓝同桌；目标红块，蓝块为干扰 | **c1**    | 2026-08-05；metadata 标注 `source_object=red`、`destination=up`；C1 首轮合计 **40 ep** |
| `train-191`…`210` | 20    | C1 配对消歧：5 组固定布局，每组依次 `red→up`、`blue→up`、交换红蓝位置后再 `red→up`、`blue→up`；奇数 episode 抓红，偶数 episode 抓蓝 | 待 export | 2026-08-05；每组 4 条；新增后 native C1 合计 **60 ep**；metadata 应保持 `source_object` / `destination=up` |


---



## 5. 训练 run ↔ 数据


| 训练 `output_dir`                 | 数据根                            | steps   | 用途 / 结论                   |
| ------------------------------- | ------------------------------ | ------- | ------------------------- |
| `outputs/train/act_smoke_v001`  | `.../shape_pick_place_v1_v001` | 100     | Week 1 训练命令验收             |
| `outputs/train/act_manual_v002` | `.../shape_pick_place_v1_v002` | 50k     | 首轮真训；不预期效果                |
| `outputs/train/act_manual_v003` | `.../shape_pick_place_v1_v003` | 150k    | 首次真机 rollout：链路通，6 条件绑定失败 |
| `outputs/train/act_c0`          | `.../shape_pick_place_v1_c0`（20 ep） | 20k     | C0 初版；离线 MAE `mae_joints≈1.01°`；rollout 4 次：reaching 对、抓偏 ~3cm；**不** resume v003 |
| `outputs/train/act_c0_r2`       | `.../shape_pick_place_v1_c0`（30 ep） | 30k     | rollout 3 次：明显改善，**1/3 抓起**（抓本体非把手，`grasp_wrong_height`）；打圈仍在；保留作对照 |
| `outputs/train/act_c0_r3`       | `.../shape_pick_place_v1_c0`（**40 ep / 8701** frames，含 10 纠偏） | 30k（已完成） | 能闭合（`reset_every=25`）；≥2 次完整抓放；主瓶颈水平偏差场 1–4cm；暂不收口 |
| `outputs/train/act_c0_r4`       | `.../shape_pick_place_v1_c0`（**60 ep / 13028**） | 100k（已完成） | loss 曾约 `0.04`、最终约 `0.05–0.06`；rollout 19 次、6 次完整抓放成功；勿覆盖 |
| `outputs/train/act_c0_r4_critical` | 同上 60 ep；抓取关键帧起点加权（前 20 / 后 10，`W=5`） | 100k（已完成） | 约 20k 时现场差；100k 诊断 18 次、6 成功；水平偏差多数约 1 cm 内；失败更多为把手对准 / 微调方向；勿覆盖 `act_c0_r4` |
| `outputs/train/act_c0_r5_critical_b8x2` | `.../shape_pick_place_v1_c0`（**80 ep / 17327**） | 200k（已完成） | 双卡 batch 8×2，effective 16，fp16，关键帧加权；loss 约 `0.024–0.025`；格点诊断 **17/22**；中部随机约 **8/12**；**C0 部署/对照基线（2026-08-05 锁定）**；勿覆盖 |
| `outputs/train/act_c0_r6_critical_b8x2` | `.../shape_pick_place_v1_c0`（**90 ep / 19518**；含远侧 10 条） | 400k（已完成） | 新目录从头训（非 resume r5）；关键帧加权；远侧有收益，中部随机 300k **7/12**、400k **2/6**；**不替换 r5 基线**；保留作远侧实验对照 |
| `outputs/train/act_c1_rb_up_ft50k_from_r5` | `.../shape_pick_place_v1_c1`（**40 ep / 8451 frames**） | 50k（已完成） | 仅 C1 红蓝同桌数据；基于 `act_c0_r5_critical_b8x2` 200k 权重热启动；用于首轮颜色绑定验证；待 rollout |


推理服务加载的是 checkpoint 目录（如 `.../checkpoints/last/pretrained_model`），与 dataset root 可分开指定；换策略时两者都要核对。

---



## 6. 维护规则

1. **每次成功 export** 后：在 §3 增/改一行（来源 runs、条件分布、ep/frames、对应训练）。
2. **每次有意义的 native 扩量**（新阶段或 +10 条级）：更新 §4；不必每条都写。
3. **每次新开训练 job**：更新 §5；写明是否 resume、是否覆盖旧 checkpoint 目录。
4. 过程叙事、失败模式、命令备忘仍写当日 `log.md`；本表只保留可检索事实。
5. 机器本地路径细节（IP、绝对盘符）不写本文件。

