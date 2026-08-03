# 数据集与采集清单

本文档是 **native 采集池** 与 **LeRobot 导出快照** 的当前状态索引：每份数据「是什么、从哪来、训过谁」。设计规则（schema、`repo_id`、特征映射）见 [ADR 0002](decisions/0002-lerobot-export-mapping.md)；任务定义见 [任务卡](tasks/shape_pick_place_v1.md)；当日过程写 [daily log](daily/)，**事实变更同步回本表**。

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

承接任务卡 §7 与 2026-07-29 rollout 结论（60 条 Phase 3 不足以学 6 条件绑定）。阶段用于**采集难度梯度**，不是 LeRobot schema 版本。


| 阶段     | 场景                                                 | 目的                |
| ------ | -------------------------------------------------- | ----------------- |
| **C0** | 视野内**只有目标块**；单条件（建议 `blue → up`）；拾取区随机 `(x,y,yaw)` | 验证「看块→抓→放」基础环路可收敛 |
| **C1** | 加 1 个干扰块（或换色 / 换区）；仍偏简单                            | 引入选择或区域歧义         |
| **C2** | 3 色 × 1 区 或 1 色 × 2 区等                             | 过渡到完整组合           |
| **C3** | 完整 6 组合 + 拾取区随机；可与既有 `v003` 混合                     | 回到任务卡全量；混合训练复用旧数据 |


判决顺序：先训 `act_c0` → 单块 rollout；能稳定抓放再进 C1，否则先查条件注入/训练策略。

---



## 3. 已导出快照

`repo_id` 均为 `local/shape_pick_place_v1`。帧数以本机 / 服务器 `LeRobotDataset` 加载为准；若与下表不一致，以加载结果改表。


| 快照                         | 来源 runs           | ep / frames | 条件分布                  | 场景备注                           | 对应训练                            | 状态                             |
| -------------------------- | ----------------- | ----------- | --------------------- | ------------------------------ | ------------------------------- | ------------------------------ |
| `shape_pick_place_v1_v001` | `pilot-001`…`003` | 3 / 1040    | red→up ×2，red→down ×1 | Phase 0/1 pilot；`phase: pilot` | `act_smoke_v001`（100 steps）     | 管道 smoke；**不作效果评估**            |
| `shape_pick_place_v1_v002` | `train-001`…`012` | 12 / 2800   | 黄/红/蓝 × up/down × 2   | Phase 3 种子；三块在场；`phase: train` | `act_manual_v002`（50k）          | 首轮真训标定；勿覆盖                     |
| `shape_pick_place_v1_v003` | `train-001`…`060` | 60 / 12886  | 6 组合 × 10             | Phase 3 扩量；含 v002 的 12 条       | `act_manual_v003`（150k）         | 真机 rollout 0/9；**保留**供后续 C3 混合 |
| `shape_pick_place_v1_c0`   | `train-061`…`100` | 40 / 8701   | blue→up × 40（30 干净 + 10 纠偏段） | **C0**：单块、无干扰；`destination=up`；`081`…`090`、`091`…`100` 经 `--resume` 追加 | `act_c0`（20k）；`act_c0_r2`（30k）；`act_c0_r3`（30k） | 2026-07-29；已 scp 服务器；r3 rollout 十余次，至少 2 次完整抓取放置，恢复重试出现但低位会推块，打圈未改善 |


计划中、尚未落地：


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
| `train-061`…`080` | 20    | C0 blue→up，单块     | **c0**    | 2026-07-29 初版 export      |
| `train-081`…`090` | 10    | C0 blue→up 补采（单块） | **c0**    | batch check PASS；`--resume` 追加 |
| `train-091`…`100` | 10    | C0 blue→up **纠偏段**（接近—修正） | **c0**    | batch check PASS；`--resume` 追加（40 / 8701） |


---



## 5. 训练 run ↔ 数据


| 训练 `output_dir`                 | 数据根                            | steps   | 用途 / 结论                   |
| ------------------------------- | ------------------------------ | ------- | ------------------------- |
| `outputs/train/act_smoke_v001`  | `.../shape_pick_place_v1_v001` | 100     | Week 1 训练命令验收             |
| `outputs/train/act_manual_v002` | `.../shape_pick_place_v1_v002` | 50k     | 首轮真训；不预期效果                |
| `outputs/train/act_manual_v003` | `.../shape_pick_place_v1_v003` | 150k    | 首次真机 rollout：链路通，6 条件绑定失败 |
| `outputs/train/act_c0`          | `.../shape_pick_place_v1_c0`（20 ep） | 20k     | C0 初版；离线 MAE `mae_joints≈1.01°`；rollout 4 次：reaching 对、抓偏 ~3cm；**不** resume v003 |
| `outputs/train/act_c0_r2`       | `.../shape_pick_place_v1_c0`（30 ep） | 30k     | rollout 3 次：明显改善，**1/3 抓起**（抓本体非把手，`grasp_wrong_height`）；打圈仍在；保留作对照 |
| `outputs/train/act_c0_r3`       | `.../shape_pick_place_v1_c0`（**40 ep / 8701** frames，含 10 纠偏） | 30k（已完成） | 能闭合（`reset_every=25`）；≥2 次完整抓放；主瓶颈水平偏差场 1–4cm；暂不收口；下一批拟 +20 → `act_c0_r4` |


推理服务加载的是 checkpoint 目录（如 `.../checkpoints/last/pretrained_model`），与 dataset root 可分开指定；换策略时两者都要核对。

---



## 6. 维护规则

1. **每次成功 export** 后：在 §3 增/改一行（来源 runs、条件分布、ep/frames、对应训练）。
2. **每次有意义的 native 扩量**（新阶段或 +10 条级）：更新 §4；不必每条都写。
3. **每次新开训练 job**：更新 §5；写明是否 resume、是否覆盖旧 checkpoint 目录。
4. 过程叙事、失败模式、命令备忘仍写当日 `log.md`；本表只保留可检索事实。
5. 机器本地路径细节（IP、绝对盘符）不写本文件。

