# 2026-08-06 工作计划（Day 11）

## 今日目标

完成昨夜训好的 **C0-only SmolVLA LoRA**（`smolvla_c0_90ep_lora_50k_b8x2`）真机 rollout，判断单块抓放是否优于 C1-only；在训练机 **`tmux` 内重训** **C0+C1 混合** LoRA 50k（补完昨夜 SIGHUP 中断的 JOB2）。

## 完成标准

- [x] `smolvla_c0_90ep_lora_50k_b8x2`：**50k** 定性 rollout（闭合、对准、抓取失败模式已记录）；30k/40k 未测 → 延期；
- [x] `smolvla_c0_c1` 混合 LoRA：**50k 训毕**（`smolvla_c0_c1_lora_50k_b8x2_r2`；loss ≈ 0.045）；dataset `shape_pick_place_v1_c0_c1`，150 / 32193；
- [x] 长训已在 **`tmux`** 中运行（避免再次 SIGHUP）；
- [x] 今日 `log.md` 已写；[`datasets.md`](../datasets.md) §5 待 C0+C1 job 名最终确认后再改。

## 任务清单

### P0：C0 SmolVLA rollout

- [x] 训练机 `serve_smolvla_policy`：`smolvla_c0_90ep_lora_50k_b8x2` **50k**；`--dataset-root` = `outputs/datasets/shape_pick_place_v1_c0_90ep`；
- [x] 本机 `rollout_smolvla_policy`（含 `--enable-gripper`）；现场用 red/up 定性（非严格仅蓝块协议）；
- [x] 对照：开夹爪后**可闭合**；水平对准优于 ACT；粗估约 50% 抓起；主失败=高度偏高；
- [x] 复测 C1-only 50k（开夹爪）：有分色趋势，红强蓝弱；
- [ ] 30k/40k 多 checkpoint：延期（50k 已够定性决策）；
- [x] 「不闭合」根因已排除为缺 `--enable-gripper`（非归一化/chunk bug）。

### P0：C0+C1 混合 LoRA 重训

- [x] 确认 `shape_pick_place_v1_c0_c1` 在训练机可 load（150 / 32193）；
- [x] **新** `output_dir` 已开训（`tmux`）；勿覆盖昨夜中断目录；
- [x] `accelerate launch` 双卡 b8×2、FP16；超参与昨夜一致；
- [x] `tmux` 内运行；**50k 训毕**；`checkpoints/050000` 已落盘；

### P1：C0+C1 训完后（可明日早）

- [x] 红蓝同桌 rollout：远距交换 4 次（构型1 全对 / 构型2 1 次错向）+ 近距各抓 1 次（均偏中间）；定性完成；
- [ ] 严格 **抓对颜色率** 统计与固定格点协议：未做；
- [x] 与 C1-only / C0-only 粗对照：混合明显优于 25k；水平对准弱于 C0-only 50k，疑颜色干扰。
- [ ] **关键帧热启**：`scripts/train_smolvla_critical.py` 已落地；待训练机构建 `critical_frames.json` 并启动 `smolvla_c0_c1_lora_crit_from50k_25k_b8x2`（+25k）。

## 开始前条件

- [ ] 训练机 `smolvla_c0_90ep_lora_50k_b8x2/checkpoints/050000`（或 040000/030000）存在；
- [ ] 本机真机运动双重许可（仅 rollout 需要）；
- [ ] SmolVLA 推理服务与 Windows rollout 脚本已同步到两端。

## 训练命令备忘（C0+C1 重训，Bash）

```bash
tmux new -s smolvla_c0_c1
cd ~/SharedAutonomy-VLA && conda activate sharedautonomy-train

export CUDA_VISIBLE_DEVICES=0,1
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1
export LD_LIBRARY_PATH="$CONDA_PREFIX/lib:${LD_LIBRARY_PATH:-}"

accelerate launch --multi_gpu --num_machines=1 --num_processes=2 \
  --mixed_precision=fp16 --dynamo_backend=no "$(which lerobot-train)" \
  --dataset.repo_id=local/shape_pick_place_v1 \
  --dataset.root="$(pwd)/outputs/datasets/shape_pick_place_v1_c0_c1" \
  --policy.type=smolvla \
  --policy.pretrained_path=/home/ustc17/models/smolvla/smolvla_base \
  --policy.vlm_model_name=/home/ustc17/models/smolvla/SmolVLM2-500M-Video-Instruct \
  --policy.load_vlm_weights=true --policy.use_peft=false --policy.push_to_hub=false \
  --output_dir=outputs/train/smolvla_c0_c1_lora_50k_b8x2_r2 \
  --job_name=smolvla_c0_c1_lora_50k_b8x2_r2 \
  --batch_size=8 --steps=50000 --save_freq=5000 --log_freq=1000 \
  --wandb.enable=false --peft.method_type=LORA --peft.r=64 --peft.lora_alpha=64 \
  2>&1 | tee outputs/train/smolvla_c0_c1_lora_50k_b8x2_r2/train.log
```

## 训练命令备忘（C0+C1 关键帧热启，Bash）

**先建索引**（与 ACT 相同工具；对 `c0_c1` 快照跑一次即可）：

```bash
cd ~/SharedAutonomy-VLA && conda activate sharedautonomy-train
python scripts/build_critical_frame_index.py \
  --dataset-repo-id local/shape_pick_place_v1 \
  --dataset-root "$(pwd)/outputs/datasets/shape_pick_place_v1_c0_c1" \
  --output "$(pwd)/outputs/datasets/shape_pick_place_v1_c0_c1/critical_frames.json" \
  --pre-frames 20 --post-frames 10 --weight 5.0
```

**热启 LoRA + 关键帧加权**（新目录；`use_peft=true` 加载 50k adapter；本 job 再训 25k，约 4h 可看中段 ckpt）：

```bash
tmux new -s smolvla_c0_c1_crit
cd ~/SharedAutonomy-VLA && conda activate sharedautonomy-train

export CUDA_VISIBLE_DEVICES=0,1
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1
export LD_LIBRARY_PATH="$CONDA_PREFIX/lib:${LD_LIBRARY_PATH:-}"

CKPT="$(pwd)/outputs/train/smolvla_c0_c1_lora_50k_b8x2_r2/checkpoints/050000/pretrained_model"
# 若 050000 下无 pretrained_model/，改成 checkpoints/050000

mkdir -p outputs/train/smolvla_c0_c1_lora_crit_from50k_25k_b8x2

accelerate launch --multi_gpu --num_machines=1 --num_processes=2 \
  --mixed_precision=fp16 --dynamo_backend=no \
  scripts/train_smolvla_critical.py \
  --critical-index "$(pwd)/outputs/datasets/shape_pick_place_v1_c0_c1/critical_frames.json" \
  --dataset.repo_id=local/shape_pick_place_v1 \
  --dataset.root="$(pwd)/outputs/datasets/shape_pick_place_v1_c0_c1" \
  --policy.type=smolvla \
  --policy.pretrained_path="$CKPT" \
  --policy.vlm_model_name=/home/ustc17/models/smolvla/SmolVLM2-500M-Video-Instruct \
  --policy.load_vlm_weights=true --policy.use_peft=true --policy.push_to_hub=false \
  --output_dir=outputs/train/smolvla_c0_c1_lora_crit_from50k_25k_b8x2 \
  --job_name=smolvla_c0_c1_lora_crit_from50k_25k_b8x2 \
  --batch_size=8 --steps=25000 --save_freq=5000 --log_freq=1000 \
  --wandb.enable=false --peft.method_type=LORA --peft.r=64 --peft.lora_alpha=64 \
  2>&1 | tee outputs/train/smolvla_c0_c1_lora_crit_from50k_25k_b8x2/train.log
```

注意：

- **不要**在 `smolvla_c0_c1_lora_50k_b8x2_r2` 同目录 `--resume`；关键帧是新配方，用新 `output_dir`。
- `use_peft=true` = 加载已有 adapter；若报缺 `adapter_config.json`，检查 `$CKPT` 是否指到含 adapter 的目录（或 `ls` 确认是 full vs lora）。
- 八点看点：`checkpoints/0005000` / `0010000`（及 log loss）；不必等满 25k 再决策。

## 今天不做

- 不继续 ACT-C1 加步或 one-hot 改 ACT；
- 不 resume 昨夜中断的 `smolvla_c0_c1_lora_50k_b8x2` 同目录（无 5k ckpt）；
- 不开 SharedAutonomy 采集；
- 不重复训已完成的 `smolvla_c0_90ep_lora_50k_b8x2`。

## 待决策

- [x] C0 可闭爪且对准强 → **C0+C1 混合仍作 C1 主训配方**；
- [x] 「不闭合」已归因部署门控，非再加 C0 步数优先项；
- [ ] 抓取高度失败：先等混合对照，再决定是否上 SmolVLA 关键帧加权；
- [ ] 抖动：次要，暂不排 async / 加长训练专门压抖。

## 背景

| 项目 | 状态 |
| --- | --- |
| ACT-C0 | r5-200k 基线锁定 |
| SmolVLA C1-only | 50k；开夹爪复测：红强蓝弱分色趋势 |
| SmolVLA C0-only | 50k rollout：对准强、约 50% 抓起、高度为主失败 |
| SmolVLA C0+C1 | **50k 训毕**；初步 rollout：远距颜色门大体有效，近距偏中间；1 成功 + 1 近成功 |
| 数据 | `c0_90ep` 90/19518；`c0_c1` 150/32193 |
