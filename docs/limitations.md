# Limitations and Negative Results

Language: English | [简体中文](limitations.zh-CN.md)

The main result is a descriptive comparison on a fixed real-robot grid. It is useful evidence about this particular data-collection and training pipeline, but it does not establish universal superiority, causal mechanism, or broad deployment performance.

## 1. Evaluation design limits

- Each policy was evaluated once for each of 36 paired conditions. Within-condition repeatability and rollout variance were not measured.
- The grid was deliberately selected rather than sampled from a deployment distribution. Its percentages should not be interpreted as expected success over arbitrary operating conditions.
- The two datasets match episode count but not frame count because episode lengths differ.
- The comparison does not include confidence intervals, statistical error bars, or repeated seeds.
- The evaluation does not isolate every possible mechanism of assisted collection with separate yaw-assistance or authority ablations.

## 2. Scope of the physical task

The locked evaluation uses:

- one robot setup;
- one red cube;
- one target region, `UP`;
- one instruction;
- fixed camera and lighting conditions;
- categorical XY positions and four wrap-90 yaw values.

The result therefore does not establish performance over other objects, target regions, instructions, continuous poses, lighting, cameras, robots, or deployment sites. Exact site coordinates are intentionally represented only by public categorical condition IDs.

## 3. Measurement and interpretation limits

Success and quality annotations were adjudicated by human observation supported by rollout video and replay notes. The public record preserves normalized labels and notes, but it is not an automated vision-based success metric.

Shared Autonomy recovered more conditions than Manual on this grid, and the strongest difference appeared in the far-distance band. These are diagnostic associations within this experiment. They do not by themselves prove that yaw assistance is the sole cause of the improvement or that the same difference will persist under another task or training setup.

The project also does not compare the locked SmolVLA result against a fully reported classical rule baseline. The simple task can be approached with conventional vision and control, so the result should not be read as evidence that learning is necessary.

## 4. Development negative results

Earlier development produced two public-facing lessons:

- The handle-based SmolVLA line did not reach the deployment threshold.
- The red/yellow two-object C1 experiments did not establish reliable language-to-object binding.

These outcomes motivated the final focus on rotated-cube geometry and shared-autonomy data quality. Specialized `color_fork`, critical SmolVLA, intermediate training, and raw diagnostic implementations remain private; only the high-level lessons are part of the public record.

The runtime may retain optional interfaces such as post-close hold and corrective episodes, but they are not part of the locked Manual-versus-Shared-Autonomy recipe and should not be presented as enabled mainline components.

## 5. Release and reproducibility limits

The current code/document release excludes raw native recordings, standalone
private videos, training logs, model weights, machine-local configuration, and
private field notes. The exported datasets and final checkpoints are hosted
externally; their public links, licenses, and provenance are maintained in
[`datasets.md`](datasets.md) and [`training.md`](training.md) without changing
the locked evaluation labels or aggregate counts.

The public repository should publish only sanitized records and reproducible metadata. Raw site coordinates, machine identities, local paths, intermediate checkpoints, runtime outputs, and contaminated legacy snapshots remain outside the public release.
