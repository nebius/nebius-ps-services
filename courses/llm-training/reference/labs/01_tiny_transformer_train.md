# Lab 01: Trace a complete tiny-transformer training step

A training step transforms token IDs into predictions, evaluates their error, propagates gradients, and updates parameters. This lab makes that entire cycle small enough to inspect on one H100. You will connect model structure to loss, gradient, update, memory, and throughput evidence instead of accepting a decreasing scalar as the only sign that training works.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use the Training environment on one H100. The model and synthetic batches are local; no external model download is needed.

H100 Tensor Cores favor supported matrix shapes and reduced-precision inputs, while fused attention reduces intermediate HBM traffic. The layer dependency remains sequential even though each operation contains extensive parallel work.

H100 accelerates the dense work, making optimizer, launch, and communication phases more visible. BF16 commonly avoids FP16 loss scaling, but numerical checks still apply.

## Concepts and code path

`tiny_lm.py` owns the decoder model and synthetic batch construction; `common.py` owns device checks and result handling. The lab orchestrates BF16 forward computation, cross-entropy, backward, and optimizer updates. It tracks parameter movement and gradient norms, then separates baseline allocation from the incremental peak of the timed region. This is a plumbing and performance exercise, not a useful pretrained language model.

Given `B=2`, `S=1024`, `H=4096`, and 32 query heads, each head dimension is 128 and the hidden activation holds about 16 MiB in BF16. Change to eight KV heads for GQA. Expected observation: Q keeps 32 heads while K/V projection and cache dimensions shrink fourfold; the model’s logits and loss contract must remain valid.

Trace the shape and dtype from token IDs to logits, then inspect loss, backward, and the parameter update. The script performs the complete training step; it has no forward-only mode.

Packed-model loss/gradient equivalence and gradient accumulation require separate reference experiments; this script performs one complete update on its supplied unpacked batch.

## Practice

`labs/01_tiny_transformer_train.py` trains a small decoder-only transformer with BF16 computation and FP32 parameters. It checks finite gradients and actual parameter updates, then writes loss, throughput, gradient, and memory measurements; checkpoint saving is optional.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_tiny_transformer_train/logs/%j.out" \
  --error="$PWD/results/01_tiny_transformer_train/logs/%j.err" \
  slurm/01_tiny_transformer_train.sbatch --workload small
```

## Check your results

Each new job owns `results/01_tiny_transformer_train/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/01_tiny_transformer_train/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/01_tiny_transformer_train/logs/$LAB_JOB_ID.out"
cat "results/01_tiny_transformer_train/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require finite loss and gradients, gradients for every trainable parameter, and a nonzero tracked parameter update. Inspect initial/final loss, gradient norms, tokens/s, step time, and baseline/incremental memory. A short synthetic run does not prove held-out quality or convergence.

Record shapes, parameter counts, activations, logits, loss, and finite gradients.

Shape traces expose which dimensions drive compute, memory, and parallel partition choices.

Retain pre/post parameters, loss, gradient norms, accumulation steps, effective tokens, and update count.

Numerical closeness is required before timing accumulation strategies.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Median step (seconds) | `median_step_ms` | `s` |
| Tokens per second | `tokens_per_second` | `tokens/s` |
| Initial loss | `initial_loss` | `none` |
| Final loss | `final_loss` | `none` |
| Memory / peak allocated bytes | `memory.peak_allocated_bytes` | `bytes` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 01_tiny_transformer_train \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run small first. Saving a checkpoint is optional and creates private state; it does not perform the deterministic resume-equivalence experiment, which is owned by Lab 24.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_tiny_transformer_train/logs/%j.out" \
  --error="$PWD/results/01_tiny_transformer_train/logs/%j.err" slurm/01_tiny_transformer_train.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_tiny_transformer_train/logs/%j.out" \
  --error="$PWD/results/01_tiny_transformer_train/logs/%j.err" slurm/01_tiny_transformer_train.sbatch --workload small --save-checkpoint
```

For the guided candidate, run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_tiny_transformer_train/logs/%j.out" \
  --error="$PWD/results/01_tiny_transformer_train/logs/%j.err" slurm/01_tiny_transformer_train.sbatch --workload small --zero-grad-fill
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Write tensor shapes from token IDs through vocabulary logits. Identify which tensors require gradients and why integer token IDs do not. Explain why optimizer state and saved activations contribute differently to memory.

Increasing the number of heads, hidden width, layer count or sequence length changes capacity and cost in different ways. GQA reduces K/V state and related work but changes architecture. Fused kernels reduce traffic yet have shape/dtype/mask support boundaries that must be verified.

At fixed microbatch size, more accumulation steps increase the effective batch size and place more computation between optimizer updates. At fixed effective batch size, accumulation instead allows smaller microbatches, with possible launch and synchronization overhead. Gradient clipping can stabilize training but adds reduction work and must be included consistently in comparisons.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_tiny_transformer_train/logs/%j.out" \
  --error="$PWD/results/01_tiny_transformer_train/logs/%j.err" slurm/01_tiny_transformer_train.nsys.sbatch --workload small
```

The native Systems command is in `slurm/01_tiny_transformer_train.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `forward`, `backward`, `optimizer`, and `zero_grad` inside `lab_workload`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_tiny_transformer_train/logs/%j.out" \
  --error="$PWD/results/01_tiny_transformer_train/logs/%j.err" slurm/01_tiny_transformer_train.ncu.sbatch --workload small
```

The native Compute command is in `slurm/01_tiny_transformer_train.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare the default gradient release with --zero-grad-fill at fixed model, data, seed and update count. Inspect the zero_grad range and complete-step time, require the same finite-loss/update checks, and compare final loss within numerical tolerance. Independently inspect whether clearing traffic or buffer allocation dominates before choosing a policy.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate forward and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Missing gradients suggest a detached path, frozen parameter, or unused module. Non-finite loss requires checking data, precision, and update scale before timing. A flat tracked parameter invalidates the update gate even if backward completed.

Avoid treating the number of heads as independent of hidden width and head dimension.

Forgetting to divide microbatch loss changes the effective gradient magnitude.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Training acceptance needs observable learning signals and state changes, not merely a completed loop. Use this architecture as the reference mental model for masking, recomputation, precision, and distributed training exercises.

Verify `hidden = heads × head_dim` for standard attention and trace every residual-compatible tensor.

Explain which tensors grow with batch, sequence, hidden width, and vocabulary.

Match summed or averaged loss semantics and count optimizer steps explicitly.

State when gradients are created, accumulated, consumed, and cleared.
