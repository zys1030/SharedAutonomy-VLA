# 2026-07-29 工作计划（Day 5）

## 今日目标

1. **先归因 keep-alive RTT 异常**：配对 A/B 实测 fresh vs keep-alive，给出测量结论（不是推测），决定推理链路连接策略；
2. 然后用 **`act_manual_v003`**（60 条训练，用户昨日已 export `_v003` 并在服务器完成训练）完成 **ACT-Manual 首次真机 rollout**（小规模 held-out 位姿）：go-to-ready → safety 门控 → 双确认开运动，记录成功率与失败模式，形成「还要多少 Manual 数据」的第一版判断。

> 承接 [2026-07-28 log](../2026-07-28/log.md)「下一工作日建议」第 1 条。roadmap Week 1.5 验收只差 rollout 一项。
>
> **进度同步（2026-07-29 上午）**：60 条已 export `shape_pick_place_v1_v003` 并在服务器训出 `act_manual_v003`；rollout 直接基于 v003，**不再用 `act_manual_v002`**。

## 完成标准

- [ ] keep-alive RTT 异常有**基于配对测量**的归因结论（bench 数字入 log），连接策略（keep-alive 去留 / retry 修复）已定；
- [ ] rollout 前 5 分钟补齐 **JPEG vs raw 同帧 action A/B**（`--image-encoding base64|jpeg_b64` 同一观测各打一帧，确认 action 一致量级）；
- [ ] 真机 rollout 至少 **N=6 次尝试**（建议 2–3 个条件 × 2–3 次，含 held-out 初始位姿），逐次记录成功/失败与失败模式（抓取偏移、过早闭合、外漂、超时等）；
- [ ] 全程走本机 safety 链路（`sharedautonomy.robot.safety` + `clip_joint_targets`），local `enable_motion` + CLI `--allow-motion` 双确认；任何异常立即停运动；
- [ ] rollout 结论写入当日 `log.md`：成功率、失败模式分类、RTT/时延是否成为可见问题、是否需要异步推理；
- [ ] 基于失败模式给出下一步判断：继续 Manual 扩量 / 重训 / 修 `infer_dataset` 的优先级；
- [ ] 收工前 `pytest -m core` 全绿；整理 `log.md`（含「今日理解重点」）。

## 任务清单

### P0-1：keep-alive RTT 归因（先于 rollout，~1h）

> 现象：加 keep-alive 后 RTT 从 60–70ms 升至 100+ms。「网络变差」只是 7/28 log 里的推测，不是实测结论；今天用配对实验定性。

- [x] 跑 `scripts/bench_infer_connection.py`：同一帧 payload、同一时间窗内**逐步交替** fresh / keep-alive（30+20+20 对），记录 `rtt_ms` + 客户端分段计时 + 服务端 `timings_ms` + retry 计数；
- [x] 判读（实测结论）：
  - keep-alive **无责且更优**：rtt 稳定 100–110ms（三轮一致）；fresh 75–140ms 漂移，配对差 −14～−35ms；`retries=0`，静默重试排除；
  - 客户端 `sendall` ~1ms 返回，发送缓冲区假设排除；
  - **真凶定位**：响应仅 ~609 字节，但响应体第 1 字节比响应头晚到 **45–58ms**（`read_rest=0`，整段晚到）——小网段被固定延迟；
- [x] 修复：`serve_act_policy.py` `_json_response` 改为 headers+body **单次 write**（一个网段）；
- [ ] 服务器 bundle 同步 + 重启 `:8088` 后复跑 bench 验证（预期 keep-alive rtt → ~50–60ms）；
- [ ] 结论与连接策略决策写入当日 `log.md`（附 bench 摘要数字）；若需改 client 代码，收工前 `pytest -m core`；
- [ ] （顺带）rollout 前 5 分钟补 **JPEG vs raw 同帧 action A/B**。

### P0-2：真机 rollout（今日主线，基于 `act_manual_v003`）

- [ ] **前置检查**（~15 min）：服务器 `:8088` 进程确认在跑、checkpoint 为 `act_manual_v003`、代码为最新（bundle 同步后需重启）；`client_act_infer.py --mode health --repeat 10` 确认网络状态；
- [ ] **JPEG vs raw 同帧 A/B**（~5 min）：dry-run `--once` 各跑一次，对照 action 向量；
- [ ] **rollout runner 确认/补齐**：基于 `dry_run_act_observe_infer.py` 的观测链路 + 下发路径（关节目标 + 夹爪命令）；确认 chunk 播放节奏与 `n_action_steps` 截断策略（防外漂）；不改写已稳定的观测代码；
- [ ] **场景与安全**：按任务卡摆三色块 + A4 UP/DOWN；`go-to-ready`；safety 门控 dry 一遍；双确认开运动；
- [ ] **小规模 rollout**：先 1–2 次低速/近距离试探，确认动作方向合理后再计正式尝试；逐次记录结果；
- [ ] **失败模式记录**：每次失败标注类型与发生阶段（reaching / grasp / lift / place）；必要时留 native 记录或截图（高频数据入 episode recorder，不写 Markdown）。

### P1：有时间再做

- [ ] 修 `infer_dataset`：`meta.episodes` 的 `dataset_from_index/to_index` O(1) 索引、删全库扫描 fallback、避开 t=0 或放大 `tolerance_s`（方向已定，见 7/28 log）；
- [ ] 若 rollout 中时延问题可见（动作迟钝/尖峰）：评估并启动**异步推理 + chunk 重放**（后台线程发 infer + 线程安全 action 队列）；
- [ ] 若 rollout 结论指向数据不足：继续 Manual 扩量（`_v003` 已是 60 条快照，**勿覆盖**）；
- [ ] roadmap「当前状态」与 Week 1.5 勾选更新（rollout 完成后）。

## 开始前条件

- [ ] Conda `sharedautonomy-lr060-cf`（本机 client / 控臂）；服务器 `sharedautonomy-train` 上 `serve_act_policy.py` 已用 `act_manual_v003/checkpoints/last/pretrained_model` 启动；
- [ ] 机械臂、双相机、夹爪、ready pose 参数与 7/28 dry-run 一致；
- [ ] 部署起始姿态：rollout 前 `go-to-ready`（训练数据均从 ready 出发；非 ready 属 OOD）；
- [ ] 真机运动默认关闭；仅在 rollout 时双确认开启；急停/断电手段就位。

## 今天不做

- SharedAutonomy 采集器 / authority / 意图推理（等 Manual 闭环与 SA runner 稳定，见 roadmap 决策）；
- VLA LoRA smoke；
- 追求高成功率或调参刷指标（今天第一轮 rollout 是数据量标定实验）；
- 覆盖 `v002`/`v003` 数据集目录或 `act_manual_v002`/`act_manual_v003` checkpoint；
- 用 `infer_dataset` 测延迟或当主推理路径（修复前列为禁用）；
- 未验证安全性前做连续长时间自动 rollout。

## 待决策

- [ ] rollout 动作下发链路：是复用/微调 manual runner 的发送路径，还是新建最小 rollout 脚本（原则：不绕过 safety，最小新增代码）；
- [ ] chunk 播放策略：阻塞式逐步 `select_action` vs 每步带 `reset`；`n_action_steps` 截断取值（先按训练值 100，外漂可见再调）；
- [ ] 正式尝试的条件选择（哪些颜色 × 区域、held-out 位姿怎么定义）；
- [ ] 是否当场导出 rollout 失败上下文供后续纠错（Week 5 前置，轻量即可）。

## 参考链接

- 任务卡：[`docs/tasks/shape_pick_place_v1.md`](../../tasks/shape_pick_place_v1.md)
- 昨日 log（RTT 归因 / 接口约定 / 常用命令）：[`../2026-07-28/log.md`](../2026-07-28/log.md)
- ACT-Manual checkpoint（rollout 用）：`outputs/train/act_manual_v003/checkpoints/last/pretrained_model/`
- 云端推理：`scripts/serve_act_policy.py`、`scripts/client_act_infer.py`、`scripts/dry_run_act_observe_infer.py`
- 连接配对基准：`scripts/bench_infer_connection.py`
- Roadmap Week 1.5：[`docs/roadmap.md`](../../roadmap.md)
