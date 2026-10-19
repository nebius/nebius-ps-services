# Cluster smoke-test runbook

**Hardware scope:** local GPU checks use one full, non-MIG H100. A qualified advanced cluster can supply one-GPU allocations. Distributed work follows the advanced course's separate fabric qualification.

This is a target-qualification checklist, not the lesson execution order.
Follow the [course](../index.html) for the learning route and complete each
exercise's relevant safety/setup gate before running it. Distributed and optional
checks qualify those paths; they are not prerequisites for earlier single-GPU
lessons that do not use them.

Run from this course root after the [Lab Guide](../../lab-guide.html#lab-preparation)
has prepared the available lab software. Check its completion summary for failures
or hardware skips affecting the selected lab. Launchers restore the saved runtime
automatically; [VERSIONS.md](../VERSIONS.md) describes its scope.
Preparation creates private log directories before Slurm opens its output.
For these read-only local dependency checks, select the ordinary lab runtime:

```bash
source tools/course_env.sh 01_tiny_transformer_train --lab
"$COURSE_PYTHON" -m pip check
python3 tools/validate_course.py
```

Raw logs, checkpoints, traces, and result
JSON remain private. Use [evidence-security.md](evidence-security.md) to prepare
a separately reviewed aggregate report.

## Causal objective and one-GPU correctness

```bash
sbatch slurm/32_learning_basics.sbatch --device cuda
sbatch slurm/13_loss_masking.sbatch --workload small
sbatch slurm/01_tiny_transformer_train.sbatch --workload small
sbatch slurm/02_gradient_accumulation.sbatch --workload small
sbatch slurm/14_activation_checkpointing.sbatch --workload small
sbatch slurm/21_mixed_precision_training.sbatch --workload small
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

Lab 14 compares no checkpointing, alternate-block checkpointing and all-block checkpointing. Loss and every
trainable parameter gradient, including token and position embedding weights,
must agree at BF16 rtol 0.01 and atol 0.01. Its inputs are integer token IDs:
there is no continuous input gradient to compare. Check the loss and every
trainable parameter gradient independently; agreement in one is not evidence
that the other is correct.

## Adaptation and reward-guided objectives

```bash
sbatch slurm/05_lora_sft.sbatch --workload small
sbatch slurm/06_grpo_objective.sbatch --workload small
sbatch slurm/07_grpo_trainer.sbatch --workload small
```

External model exercises require pinned, approved artifacts. Check trainable
state, label/response boundaries, reward variance, and finite updates. Tiny
runs demonstrate the objective and implementation; they do not establish
held-out model quality or production training efficiency.

## Precision, resume, and data efficiency

```bash
sbatch slurm/24_checkpoint_resume.sbatch --workload small
sbatch slurm/25_sequence_packing.sbatch --workload small
sbatch slurm/26_input_pipeline.sbatch --workload small
sbatch slurm/27_fused_graph_trace.sbatch --workload small
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
sbatch slurm/22_transformer_engine_fp8.sbatch --workload small
```

Record the resolved version and recipe. Collect warmed delayed-scaling samples
before and after timing, and require every output and gradient relative-L2
sample to remain within its declared threshold. The lab defaults are 0.2 for
outputs and 0.3 for gradients; record any recipe-specific change explicitly.
These FP8 thresholds are not the BF16 allclose tolerance used in Lab 14.
A missing dependency leaves this extension pending.

## Profiler and causal capstone

```bash
sbatch slurm/30_training_profiler.sbatch --workload small
sbatch slurm/31_training_capstone.sbatch --experiment trials --workload small
```

Retain all three fresh-process capstone records and confirm the launcher
alternates candidate order. Separate profiling from acceptance timing; report
each lab's actual time and memory fields and a rejected hypothesis. Lab 30 is a
profiler exercise. Lab 31 measures a single-GPU linear update, logical row/token
throughput and peak memory; its optional utilization uses a matmul-only FLOP
numerator, not full-model MFU. Communication measurements belong to the separate
distributed labs. A pending environment or H100 gate is never passed.

## Distributed practice

Use [Advanced Labs](../../advanced-gpu-communication/index.html) for placement,
transport, collective and application checks on its qualified two-worker,
sixteen-H100 fabric. Follow its lab-specific acceptance criteria; two one-GPU
TCP workers do not establish fabric performance. These checks are not gates
for the local experiments above.
