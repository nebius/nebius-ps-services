# Lab 14: Compare checkpointing on alternate blocks and all blocks

Activation checkpointing saves memory by recomputing selected forward work during backward instead of retaining every intermediate. This lab compares three matched tiny-transformer paths and checks loss plus all trainable parameter gradients. You will connect checkpoint boundaries to the memory–compute trade-off rather than assuming that lower peak allocation always produces a better training configuration.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use one H100 with the course's Training environment. The experiment computes a forward pass and parameter gradients. Inputs are integer token IDs, so an input-token gradient is not defined.

Extra H100 compute can make recomputation worthwhile when capacity enables a more efficient microbatch, but added kernels and launch overhead remain visible. Sequence length strongly changes the activation benefit.

## Concepts and code path

The program prepares matched model state and batches for no checkpointing, alternate-block checkpointing and all-block checkpointing. The output key `selective` checkpoints complete transformer blocks at odd indices; `full` checkpoints every listed transformer block. These are this script's result labels. NVIDIA Megatron's selective activation recomputation instead targets selected modules within layers, which this lab does not implement. It verifies loss and gradients, then measures forward, loss, and backward time plus incremental peak allocation. The reported `step_time` excludes optimizer work and optimizer state: no optimizer is constructed or updated in this lab.

Given a block saving 6 GiB of activations and costing 12 milliseconds to recompute, full checkpointing enables microbatch two instead of one but adds 12 milliseconds. Change to checkpoint only a 5-GiB attention intermediate costing 4 milliseconds to replay. Expected observation: if microbatch two still fits, selective recomputation retains most capacity benefit with less step-time penalty at fixed global tokens.

The supplied variants use no checkpointing, alternate-block checkpointing and all-block checkpointing on the same tiny-transformer forward/loss/backward path. Compare checkpoint policies within this workload; durations from a different model are not comparable. There is no optimizer update; complete-update equivalence is a separately implemented extension.

## Practice

`labs/14_activation_checkpointing.py` compares eager training with selective and full activation checkpointing. It checks losses and every trainable parameter gradient agree and writes step timing and incremental peak-memory measurements.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 \
  --chdir="$PWD" \
  --output="$PWD/results/14_activation_checkpointing/logs/%j.out" \
  --error="$PWD/results/14_activation_checkpointing/logs/%j.err" \
  slurm/single_gpu.sbatch \
  labs/14_activation_checkpointing.py --profile small
```

## Check your results

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/14_activation_checkpointing/logs/$LAB_JOB_ID.out"
cat "results/14_activation_checkpointing/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require separate loss and gradient agreement for the alternate-block and all-block paths at BF16 `rtol=0.01, atol=0.01`. Inspect checkpointed block indices, forward/backward-only `step_time`, and `median_incremental_peak_bytes`. Token and position embedding parameters are included in the gradient checks; optimizer-update equivalence is not tested here.

Retain fixed batch/sequence settings, loss and every parameter-gradient comparison, forward/loss/backward timing, peak memory, and recomputed regions. Do not relabel those timings as complete optimizer-step measurements.

Compare loss and gradients at the implemented boundary first, and report the compute cost of the memory saving. An end-to-end training decision additionally requires equivalent optimizer updates and the same useful data per update.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Eager / step time / median (seconds) | `eager.step_time.median_ms` | `s` |
| Selective / step time / median (seconds) | `selective.step_time.median_ms` | `s` |
| Full / step time / median (seconds) | `full.step_time.median_ms` | `s` |
| Eager / median incremental peak bytes | `eager.median_incremental_peak_bytes` | `bytes` |
| Full / median incremental peak bytes | `full.median_incremental_peak_bytes` | `bytes` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 14_activation_checkpointing \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run all three variants together with the small profile. Use the large profile only after equivalence passes; its larger model/context is a new memory-pressure experiment.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/14_activation_checkpointing/logs/%j.out" \
  --error="$PWD/results/14_activation_checkpointing/logs/%j.err" slurm/single_gpu.sbatch labs/14_activation_checkpointing.py --profile small
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/14_activation_checkpointing/logs/%j.out" \
  --error="$PWD/results/14_activation_checkpointing/logs/%j.err" slurm/single_gpu.sbatch labs/14_activation_checkpointing.py --profile large
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Which forward intermediates must be recreated for each checkpointed block? Compare memory saved per added millisecond. To apply this reasoning to a complete training loop, explain why persistent optimizer state can reduce the percentage saving in total memory; it is absent from this lab's measured allocation.

Smaller microbatches can reduce GEMM efficiency. More accumulation delays updates and may add synchronization complexity. Recomputation saves memory at a variable compute cost; offload is a different trade that spends transfer bandwidth and is advanced material here.

Capture a separate diagnostic run:

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/14_activation_checkpointing/logs/capture-%J-%t.out" \
  --error="results/14_activation_checkpointing/logs/capture-%J-%t.err" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=nsys \
  nsys profile --trace=cuda,nvtx,osrt \
  --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none \
  --discard-environment=true --force-overwrite=false \
  --duration=300 --kill=none --wait=all \
  --output "results/14_activation_checkpointing/profiles/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/14_activation_checkpointing.py --profile small
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `lab_workload`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

The Compute command selects the first matrix kernel inside `checkpoint_step`, excluding model and batch construction. It diagnoses the first baseline pass; compare checkpoint modes with the complete Systems traces, memory measurements and gradient checks. Verify the selected kernel and its enclosing NVTX range against Systems before interpreting counters. Clean executions retain the original callable and do not enter these capture annotations.

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/14_activation_checkpointing/logs/capture-%J-%t.out" \
  --error="results/14_activation_checkpointing/logs/capture-%J-%t.err" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=ncu \
  ncu --target-processes all --nvtx --nvtx-include checkpoint_step/ \
  --kernel-name-base demangled --rename-kernels off \
  --kernel-name "regex:${COURSE_PROFILE_KERNEL:?select the measured kernel from Systems}" \
  --launch-count 1 --set basic --section SpeedOfLight \
  --section MemoryWorkloadAnalysis --section Occupancy --clock-control none \
  --export "results/14_activation_checkpointing/profiles/ncu-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/14_activation_checkpointing.py --profile small
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare checkpointing disabled/enabled at the same shape. Independently calculate bytes saved per added millisecond and select the policy for a stated memory budget.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate lab_workload and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Gradient disagreement after introducing randomness may reflect unmatched RNG handling or state. Missing gradients suggest a disconnected recomputation path. Stop at the failed equivalence check rather than treating memory savings as success.

Avoid changing microbatch count without holding effective batch or optimizer schedule fixed.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Recomputation trades additional computation for lower activation memory use. A further exercise can implement operator-level policies, but it must preserve this lab's complete loss/gradient gates and explicitly identify the new checkpoint boundaries.

Compare equivalent updates and choose the smallest recomputation set that meets capacity.

Explain why accumulation and checkpointing solve different memory terms.
