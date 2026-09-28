# Lab 14: Diagnose synchronization, launch, memory, and compute limits

This workshop supplies controlled examples of four bottleneck classes so you can practice choosing the right evidence. Instead of opening a profiler and guessing, you will select one case, compare baseline and candidate, and explain the causal change. Each case has a different mechanism; a single metric such as utilization cannot diagnose all four.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/14_profiler_bottlenecks.json).

Use an allocated H100 with the course environment and the profiler available through the supplied launcher. Confirm supported Nsight sets/sections on that compute node. Keep reports private and use one profiler per run.

At the first profiler lesson, use the synchronization case and recognize the
other bottleneck patterns without adopting their interventions yet. Return to
launch, memory and compute cases after the corresponding local-optimization
lessons; use the complete casebook before the capstone.

## Concepts and code path

The script selects `sync`, `launch`, `memory`, or `compute`, then constructs the chosen baseline or optimized callable and its reference. It warms execution, records unprofiled timing, exposes the `profile_region` NVTX range, then checks numerical equivalence before writing a result. Reject the collected timing if that check fails. The compute candidate changes precision under a declared error gate; the pointwise memory case has no portable algorithmic FLOP count. The synchronization case deliberately reuses Lab 02's immediate-versus-deferred scalar retrieval as a known control; the new task is locating host waits in an NVTX-scoped trace. The memory case reuses Lab 03's pointwise expression; Lab 03 teaches compilation cost, while this workshop asks whether a selected kernel's measured traffic supports a memory-bound diagnosis. Do not count rerunning these controls as discovering two new optimizations.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Compare the synchronization baseline and candidate, then capture a separate baseline timeline. Keep diagnostic profiling separate from the uninstrumented timing comparison.

```bash
umask 077
python3 tools/submit_lab.py --lab 14_profiler_bottlenecks slurm/single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile small --case sync --mode baseline
python3 tools/submit_lab.py --lab 14_profiler_bottlenecks slurm/single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile small --case sync --mode optimized
python3 tools/submit_lab.py --lab 14_profiler_bottlenecks slurm/nsys_single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile small --case sync --mode baseline
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

The compute case keeps the same matrix width, scaled inputs and useful operation
count while `--mode optimized` changes FP32 to BF16. Its dtype and logical minimum
I/O bytes therefore change with mode; repeated runs of the same mode must retain
both. Require the declared elementwise error gate before comparing timings.

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 14_profiler_bottlenecks --job "$LAB_JOB_ID"
```

Require `numerically_equivalent` and `finite_output`. Inspect `case`, `mode`, timing, workload details, and the selected range. A profile from another case cannot explain this pair's result.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Timing / median (seconds) | `timing.median_ms` | `s` |
| Checksum | `checksum` | `none` |

Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 14_profiler_bottlenecks \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

For synchronization, locate host waits; for launches, kernel spacing; for memory, traffic and access efficiency; for compute, the selected arithmetic path. Distinguish logical byte estimates from measured transactions and compare the exact selected kernel.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 14_profiler_bottlenecks --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile small --case sync --mode baseline
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `profile_region`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. The launcher selects one matching kernel inside `profile_region`, the configured NVTX range for this lab. Its launch-count limit applies after the range and kernel-name filters. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
python3 tools/submit_lab.py --lab 14_profiler_bottlenecks --export=ALL,COURSE_PROFILE_TOOL=ncu slurm/single_gpu.sbatch labs/14_profiler_bottlenecks.py --profile small --case sync --mode baseline
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then set `COURSE_PROFILE_RANGE=phase_name` when selecting it. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare `baseline` and `optimized` with the same `--case` and workload. Predict the timing and timeline differences, verify correctness, and inspect the named report views. Independently select another existing case and repeat both modes unprofiled; explain why its bottleneck needs a different repair.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate profile_region and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Unsupported profiler sections or permissions are missing evidence, not zero stalls. Compilation and correctness failures disqualify the candidate. Avoid nesting profilers or using their perturbed timings to manufacture an apparent improvement.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Write one hypothesis, one observed mechanism, and one bounded conclusion per case. Apply the same sequence to a real hotspot only after establishing its workload and correctness contract.
