# 2026-07-29 工作计划（Day 5）



## 今日目标



1. **先归因 keep-alive RTT 异常**：配对 A/B 实测 fresh vs keep-alive，给出测量结论（不是推测），决定推理链路连接策略；

2. 然后用 **`act_manual_v003`**（60 条训练，用户昨日已 export `_v003` 并在服务器完成训练）完成 **ACT-Manual 首次真机 rollout**（小规模 held-out 位姿）：go-to-ready → safety 门控 → 双确认开运动，记录成功率与失败模式，形成「还要多少 Manual 数据」的第一版判断。



> 承接 [2026-07-28 log](../2026-07-28/log.md)「下一工作日建议」第 1 条。roadmap Week 1.5 验收只差 rollout 一项。

>

> **进度同步（2026-07-29 下午）**：RTT 修复 + A/B + rollout runner + **真机 rollout 9 次**已完成；结论见 [log.md](log.md) rollout 表。第一版判断：**链路成立，60 条不足以稳定 6 条件绑定，优先条件分辨导向扩量**。



## 完成标准



- [x] keep-alive RTT 异常有**基于配对测量**的归因结论（bench 数字入 log），连接策略（keep-alive 去留 / retry 修复）已定；

- [x] rollout 前 5 分钟补齐 **JPEG vs raw 同帧 action A/B**（`--image-encoding base64|jpeg_b64` 同一观测各打一帧，确认 action 一致量级）；

- [x] 真机 rollout 至少 **N=6 次尝试**（建议 2–3 个条件 × 2–3 次，含 held-out 初始位姿），逐次记录成功/失败与失败模式（抓取偏移、过早闭合、外漂、超时等）；

- [x] 全程走本机 safety 链路（`sharedautonomy.robot.safety` + `clip_joint_targets`），local `enable_motion` + CLI `--allow-motion` 双确认；任何异常立即停运动；

- [x] rollout 结论写入当日 `log.md`：成功率、失败模式分类、RTT/时延是否成为可见问题、是否需要异步推理；

- [x] 基于失败模式给出下一步判断：继续 Manual 扩量 / 重训 / 修 `infer_dataset` 的优先级；

- [x] 收工前 `pytest -m core` 全绿；
- [x] 收工「今日理解重点」与自测参考答案（log 正文已写）。



## 任务清单



### P0-1：keep-alive RTT 归因（先于 rollout，~1h）



> 现象：加 keep-alive 后 RTT 从 60–70ms 升至 100+ms。「网络变差」只是 7/28 log 里的推测，不是实测结论；今天用配对实验定性。



- [x] 跑 `scripts/bench_infer_connection.py`：同一帧 payload、同一时间窗内**逐步交替** fresh / keep-alive（30+20+20 对），记录 `rtt_ms` + 客户端分段计时 + 服务端 `timings_ms` + retry 计数；

- [x] 判读（实测结论）：

  - keep-alive **无责且更优**：rtt 稳定 100–110ms（三轮一致）；fresh 75–140ms 漂移，配对差 −14～−35ms；`retries=0`，静默重试排除；

  - 客户端 `sendall` ~1ms 返回，发送缓冲区假设排除；

  - **真凶定位**：响应仅 ~609 字节，但响应体第 1 字节比响应头晚到 **45–58ms**（`read_rest=0`，整段晚到）——小网段被固定延迟；

- [x] 修复：`serve_act_policy.py` `_json_response` 改为 headers+body **单次 write**（一个网段）；

- [x] 服务器 bundle 同步 + 重启 `:8088` 后复跑 bench 验证：**keep-alive rtt mean 52.9 / p95 60.2**（修复前 100–110）；`read_first_byte=0`；配对差 −20ms（keep-alive 稳定占优）；

  - **定论**：阻塞式地板 ~53ms ≈ 上行 ~22 + 服务端 ~25 + RTT ~23（部分重叠）；7/28「阻塞触顶 ~110ms」中 ~50ms 是响应分段 bug，非带宽；10Hz 闭环有余量，异步推理继续作备选；

- [x] 结论与连接策略决策写入当日 `log.md`（附 bench 摘要数字）；若需改 client 代码，收工前 `pytest -m core`；

- [x] （顺带）rollout 前 5 分钟补 **JPEG vs raw 同帧 action A/B**。



### P0-2：真机 rollout（今日主线，基于 `act_manual_v003`）



- [x] **前置检查**（~15 min）：服务器 `:8088` 进程确认在跑、checkpoint 为 `act_manual_v003`、代码为最新（bundle 同步后需重启）；`client_act_infer.py --mode health --repeat 10` 确认网络状态；

- [x] **观测敏感度 A/B**（~10 min；回应「移动木块模型无响应」）：ready 下木块位置 A / B 各 `dry_run --once`（首步必 reset），对照 action 向量；差异明显 → 昨日无响应 = chunk 盲播预期行为；几乎相同 → 模型敏感度不足，影响 rollout 结论读法；结果决定 chunk 播放策略（`reset_every` / `n_action_steps` / 异步）；

- [x] **JPEG vs raw 同帧 A/B**（~5 min）：dry-run `--once` 各跑一次，对照 action 向量；

- [x] **rollout runner 确认/补齐**：`scripts/rollout_act_policy.py` + `sharedautonomy/policies/act/rollout.py`；观测链路复用 `live_infer.py`；`reset_every=25` blocking 本地队列；

- [x] **场景与安全**：按任务卡摆三色块 + A4 UP/DOWN；`go-to-ready`；safety 门控；双确认开运动；

- [x] **小规模 rollout**：dry-run 30 步 + 真机 **9 次**（多条件 + 块位扰动）；

- [x] **失败模式记录**：见 [log.md](log.md) rollout 表（reaching / grasp / place / 条件混淆 / 不终止）。



### P1：有时间再做



- [ ] ~~修~~ **暂缓** `infer_dataset`：真机路径 `/infer` 已闭环、延迟基准已由 bench + `timings_ms` 接管，该端点失去主用途；**等有「服务器离线 dataset 帧批量评估」需求再修**（方向已定：O(1) 索引、删全库扫描 fallback、避开 t=0）；

- [x] 若 rollout 中时延问题可见（动作迟钝/尖峰）：评估并启动**异步推理 + chunk 重放**——**结论：未暴露，暂缓**（runner 已留 `--infer-mode async` 接口）；

- [x] 若 rollout 结论指向数据不足：继续 Manual 扩量（`_v003` 已是 60 条快照，**勿覆盖**）——**已决策：条件分辨导向扩量 → `_v004`**；

- [x] roadmap「当前状态」与 Week 1.5 勾选更新（rollout 完成后）。

### P0-3（下午新增）：C0 课程学习采集 + 训练（回到任务卡 §7 Phase 1）

> 上午 rollout 结论：60 条 Phase 3 数据学不出 6 条件绑定，主因是难度梯度缺失（三块同时在场使「去中间」成为最优平均策略）。下午回补 Phase 1：单块、无干扰，先验证「看块→抓→放」基础环路可收敛。依据：任务卡 §7 分阶段 rollout；讨论见上午 log。

- [x] **C0 采集**（目标 ~20 条 + 补 10）：`train-061`…`090`；单块 `blue→up`；batch check 全 PASS；
- [x] **批量验收**：`summarize_episode_conditions` + `batch_check_episodes` 全 PASS；
- [x] **export**：`shape_pick_place_v1_c0`（初版 20 ep + `--resume` 追加 10 → **30 / 6346**）；已 scp 服务器；
- [x] **服务器训练** `act_c0`：20 ep × 20k；离线 MAE 1.01°；rollout 4 次（reaching 对、抓偏 ~3cm）；
- [x] **判决信号（初版）**：基础环路成立 → 任务难度梯度是主因；闭环 grasp 精度待 30 ep 复测；
- [x] **`act_c0_r2`**：30 ep × 30k 训完；rollout 3 次（明显改善，1/3 抓起但抓本体；打圈仍在）；
- [x] **纠偏段补采**：`train-091`…`100` ×10（接近—修正）；batch check PASS；
- [ ] **c0 追加 + `act_c0_r3`**（收工前执行）：`--resume` 091–100 → 40 ep；rsync 服务器；r3 从头训 30k（`tee` 留日志）；
- [ ] **r3 rollout 复测**（明日）：前 2–3 次复用 r2 块位；分支 → C1 / 把手悬停补采；
- [x] 60 条 `_v003` 保留不动，后续 C3 阶段混合训练复用。

## 开始前条件



- [x] Conda `sharedautonomy-lr060-cf`（本机 client / 控臂）；服务器 `sharedautonomy-train` 上 `serve_act_policy.py` 已用 `act_manual_v003/checkpoints/last/pretrained_model` 启动；

- [x] 机械臂、双相机、夹爪、ready pose 参数与 7/28 dry-run 一致；

- [x] 部署起始姿态：rollout 前 `go-to-ready`（训练数据均从 ready 出发；非 ready 属 OOD）；

- [x] 真机运动默认关闭；仅在 rollout 时双确认开启；急停/断电手段就位。



## 今天不做



- SharedAutonomy 采集器 / authority / 意图推理（等 Manual 闭环与 SA runner 稳定，见 roadmap 决策）；

- VLA LoRA smoke；

- 追求高成功率或调参刷指标（今天第一轮 rollout 是数据量标定实验）；

- 覆盖 `v002`/`v003` 数据集目录或 `act_manual_v002`/`act_manual_v003` checkpoint；

- 用 `infer_dataset` 测延迟或当主推理路径（修复前列为禁用）；

- 未验证安全性前做连续长时间自动 rollout。



## 待决策



- [x] rollout 动作下发链路：新建 `scripts/rollout_act_policy.py`（不绕过 safety，不复用 teleop runner）；

- [x] chunk 播放策略：`--reset-every 25` + blocking 本地 refill；异步作备选；

- [x] 正式尝试的条件选择：blue/red/yellow × up/down + 块位整体扰动（见 log rollout 表）；

- [ ] 是否当场导出 rollout 失败上下文供后续纠错（Week 5 前置，轻量即可）——未做，留后续。



## 参考链接



- 任务卡：[`docs/tasks/shape_pick_place_v1.md`](../../tasks/shape_pick_place_v1.md)

- 昨日 log（RTT 归因 / 接口约定 / 常用命令）：[`../2026-07-28/log.md`](../2026-07-28/log.md)

- **今日 log（rollout 表）**：[log.md](log.md)

- ACT-Manual checkpoint（rollout 用）：`outputs/train/act_manual_v003/checkpoints/last/pretrained_model/`

- 云端推理：`scripts/serve_act_policy.py`、`scripts/client_act_infer.py`、`scripts/dry_run_act_observe_infer.py`

- **Rollout**：`scripts/rollout_act_policy.py`

- 连接配对基准：`scripts/bench_infer_connection.py`

- Roadmap Week 1.5：[`docs/roadmap.md`](../../roadmap.md)


