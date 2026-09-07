# Cluster smoke-test runbook

This is a target-qualification checklist, not the lesson execution order.
Follow the [syllabus](../SYLLABUS.md) for the learning route and complete each
exercise's relevant safety/setup gate before running it. Distributed and optional
checks qualify those paths; they are not prerequisites for earlier single-GPU
lessons that do not use them.

Run from the Training course root with the approved environment described in
[VERSIONS.md](../VERSIONS.md). Save each trial independently and use
[benchmark-record.md](benchmark-record.md) for its interpretation. Before
submitting any job, restrict files created by the submitting shell:

```bash
umask 077
python -m pip check
python tools/validate_course.py
```

Slurm can create its output before the job script starts, so the mask inside a
launcher does not replace this step. Raw logs, checkpoints, traces, and result
JSON remain private. Use [evidence-security.md](evidence-security.md) to prepare
a separately reviewed aggregate report.

## Gate 1: allocation and communication

```bash
sbatch slurm/two_node.sbatch labs/00_cluster_preflight.py --profile smoke
```

Require one full non-MIG H100 per rank, correct local device binding, two
distinct nodes for world size 2, successful NCCL initialization, and exact
collective correctness. Local dependency checks do not establish this gate.
Lab 00 requires two ranks; it is not a single-GPU allocation probe. The
one-GPU labs in Gate 2 perform their own H100 device check.

## Gate 2: causal objective and one-GPU correctness

```bash
sbatch slurm/single_gpu.sbatch labs/32_learning_basics.py --device cuda
sbatch slurm/single_gpu.sbatch labs/13_loss_masking.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/01_tiny_transformer_train.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/02_gradient_accumulation.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/14_activation_checkpointing.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/21_mixed_precision_training.py --profile smoke
```

Lab 32 can first be studied locally with `--device cpu`. Require a learned
weight near 2, held-out prediction near 6 and unchanged parameters during
inference. That CPU result does not qualify this explicit CUDA run and has no
timing or language-quality interpretation.

Stop on invalid masks, non-finite gradients, missing gradients for any trainable
parameter, or a failed update comparison. Lab 01 reports nonzero finite
gradient norms, a tracked-parameter update delta, baseline allocation, and
incremental timed peak allocation. Lab 21 compares loss, gradients, and updates
from identical state under explicit precision tolerances.

Lab 01 checks gradient presence for every trainable parameter, but measures the
update delta only for its first tracked parameter. This is not an all-parameter
update-equivalence test.

Lab 14 compares eager, selective, and full recomputation. Loss and every
trainable parameter gradient, including token and position embedding weights,
must agree at BF16 rtol 0.01 and atol 0.01. Its inputs are integer token IDs:
there is no continuous input gradient to compare. Check the loss and every
trainable parameter gradient independently; agreement in one is not evidence
that the other is correct.

## Gate 3: adaptation and reward-guided objectives

```bash
sbatch slurm/single_gpu.sbatch labs/05_lora_sft.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/06_grpo_objective.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/07_grpo_trainer.py --profile smoke
```

External model exercises require pinned, approved artifacts. Check trainable
state, label/response boundaries, reward variance, and finite updates. Tiny
runs demonstrate the objective and implementation; they do not establish
held-out model quality or production training efficiency.

## Gate 4: precision, resume, and data efficiency

```bash
sbatch slurm/single_gpu.sbatch labs/24_checkpoint_resume.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/25_sequence_packing.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/26_input_pipeline.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/27_fused_graph_trace.py --profile smoke
```

Resume must reproduce the next batch and update under the stated deterministic
contract. Lab 25 proves packing capacity and attention-boundary mechanics; it
does not execute packed-model loss. Add a corresponding model/label reference
before claiming preserved valid-token loss or packed training throughput. A faster
loader must preserve sample completeness; graph and fusion experiments retain
unprofiled timing and their capture/compile costs.

Run the conditional FP8 gate only after a compatible Transformer Engine build
and its recipe have been qualified in the approved environment:

```bash
sbatch slurm/single_gpu.sbatch labs/22_transformer_engine_fp8.py --profile smoke
```

Record the resolved version and recipe. Collect warmed delayed-scaling samples
before and after timing, and require every output and gradient relative-L2
sample to remain within its declared threshold. The lab defaults are 0.2 for
outputs and 0.3 for gradients; record any recipe-specific change explicitly.
These FP8 thresholds are not the BF16 allclose tolerance used in Lab 14.
A missing dependency leaves this extension pending.

## Gate 5: two-node training mechanisms

```bash
sbatch slurm/two_node.sbatch labs/03_ddp_train.py --profile smoke
sbatch slurm/two_node.sbatch labs/04_fsdp2_train.py --profile smoke
sbatch slurm/two_node.sbatch labs/12_moe_expert_parallel.py --profile smoke
sbatch slurm/two_node.sbatch labs/19_tensor_parallel_linear.py --profile smoke
sbatch slurm/two_node.sbatch labs/28_communication_overlap.py --profile smoke
sbatch slurm/two_node.sbatch labs/29_parallelism_mechanics.py --profile smoke
```

Freeze global valid tokens, microbatch/accumulation, loss reduction, and
optimizer semantics. Record local/global batch, sharding, phase peak memory,
communication, and slowest-rank step time. TP compares partitioned computation
with its replicated reference; EP reports token routing and expert balance.
Two one-GPU nodes prove bounded mechanics, not production NVLink/NVSwitch,
pipeline, context, or expert-parallel scaling.

After Lab 28's readiness model, qualify real DDP buckets and hooks with Lab 33:

```bash
sbatch slurm/two_node.sbatch labs/33_ddp_buckets.py --hook allreduce --bucket-cap-mb 1
sbatch slurm/two_node.sbatch labs/33_ddp_buckets.py --hook allreduce --bucket-cap-mb 0.1
sbatch slurm/two_node.sbatch labs/33_ddp_buckets.py --hook powersgd --bucket-cap-mb 0.1 --warmup 4
```

Inspect startup and measured bucket ledgers rather than treating the cap as an
observed payload. Require valid SGD updates and full-precision reference
agreement for allreduce. Compression reports trajectory errors; a finite short
run does not establish acceptable task convergence. Use the complete guide for
FP16/BF16 qualification and controlled comparisons. After unprofiled trials,
`slurm/nsys_ddp.sbatch` captures each node's torchrun child in a unique private
directory on shared storage. A rank failure terminates the step. Actual Slurm
failure propagation, CUDA tracing and cross-node timelines need target evidence.

## Gate 6: profiler and causal capstone

```bash
sbatch slurm/single_gpu.sbatch labs/30_training_profiler.py --profile smoke
sbatch slurm/capstone_three_trials.sbatch --profile smoke
```

Retain all three fresh-process capstone records and confirm the launcher
alternates candidate order. Separate profiling from acceptance timing; report
each lab's actual time and memory fields and a rejected hypothesis. Lab 30 is a
profiler exercise. Lab 31 measures a single-GPU linear update, logical row/token
throughput and peak memory; its optional utilization uses a matmul-only FLOP
numerator, not full-model MFU. Communication measurements belong to the separate
distributed labs. A pending environment or H100 gate is never passed.
