# Lab 30: Inspect the operators in a tiny training step

A training-step profile helps connect forward, loss, backward, and optimizer work to expensive operators. This lab captures a bounded tiny-model step and emits a short operator summary. You will use that evidence to form a hypothesis for the capstone while recognizing that one cold profiled step is neither a steady-state benchmark nor a complete utilization analysis.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/30_training_profiler.json).

Use one H100 with functioning PyTorch CPU/CUDA profiling in the Training environment. Keep raw diagnostic artifacts private. Aggregated operator entries describe the supplied training loop; profiling a different candidate workload requires a separate matched trace.

## Concepts and code path

The program constructs the tiny model and batch, profiles one training step, and sorts aggregated events by self-device time. It publishes a bounded list rather than a raw trace. The listed device/CPU durations help locate work, but inclusive durations can overlap or nest; summing arbitrary rows does not reconstruct elapsed step time.

`--seed` initializes the random generators before constructing the model and
batch. Reuse it to hold those inputs fixed across profiles; it does not make
device timing deterministic.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Capture the small profile first and identify one plausible bottleneck. Do not change several model or precision settings at once while collecting the evidence intended to explain a baseline.

```bash
umask 077
"$COURSE_PYTHON" labs/30_training_profiler.py --help
python3 tools/submit_lab.py --lab 30_training_profiler slurm/single_gpu.sbatch labs/30_training_profiler.py --profile small
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 30_training_profiler --job "$LAB_JOB_ID"
```

Confirm finite loss and inspect `top_operators` and `top_operator_sort`. The check does not compare full gradients or updates. No Chrome trace, warmed step-time distribution, or complete model FLOP utilization is emitted by this lab.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Loss | `loss` | `none` |

Select two successful, equivalent diagnostic runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot; the two slots are independent diagnostic repetitions. Their instrumented durations are not acceptance timings. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 30_training_profiler \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Map one expensive operator to forward or backward work in the model. Does the table suggest a large arithmetic kernel or many small operations? What timeline evidence would you need to distinguish launch starvation from device execution time?

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 30_training_profiler --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/single_gpu.sbatch labs/30_training_profiler.py --profile small --external-only
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `lab_workload`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

The Compute command selects the first matrix kernel inside `training_step`, excluding model and batch construction. The external capture keeps `--external-only`, so it does not nest the internal CUDA profiler. One forward kernel does not represent the complete backward/update step. Verify the selected kernel and its enclosing NVTX range against Systems before interpreting counters. Clean executions retain the original callable and do not enter these capture annotations.

```bash
python3 tools/submit_lab.py --lab 30_training_profiler '--export=ALL,COURSE_PROFILE_TOOL=ncu,COURSE_PROFILE_RANGE=training_step,COURSE_PROFILE_KERNEL=.*(gemm|gemv|nvjet).*' slurm/single_gpu.sbatch labs/30_training_profiler.py --profile small --external-only
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then set `COURSE_PROFILE_RANGE=phase_name` when selecting it. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Map the expensive operators to forward/backward phases and CUDA launches. Independently select one suspected kernel and collect Compute evidence; validate any proposed gain in a separate unprofiled training lab.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate lab_workload and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Missing CUDA activity invalidates device conclusions even if CPU events appear. First-use initialization can dominate this cold sample. Preserve it as diagnostic evidence and collect a warmed unprofiled baseline separately.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

A profile proposes a causal hypothesis; it does not prove a speedup. Use Lab 31's controlled update comparison and independent trials, adding a focused profile of the actual candidate workload when explaining its result.
