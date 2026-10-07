# Lab 01: Compare CPU timers and CUDA events

This lab measures one GPU operation three ways to expose a common benchmarking mistake: timing only the host's submission of asynchronous work. You will compare unsynchronized host time, synchronized host time, and CUDA-event time. The goal is to understand the question answered by each timer before choosing one for an optimization experiment.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

A CPU submission can finish before its GPU work. The comparisons below distinguish that submission interval from waiting for the result and timing the device operations themselves.

Use one allocated H100 with no unaccounted competing workload. The launcher restores the prepared Optimizations runtime automatically. Review the distinction between enqueuing an operation and waiting until its result is ready.

H100 peak rates make small host gaps and poorly amortized collectives proportionally more visible. Record clock behavior, sharing mode, framework build, and detected SKU because they change the attainable baseline. Keep the allocation and software configuration fixed; do not lock clocks or reconfigure sharing as part of these labs.

Short H100 kernels make timer overhead and accidental synchronization significant. Use enough repeated work for stable resolution, but do not batch iterations so aggressively that cache state or scheduling changes the workload.

## Concepts and code path

The program creates resident operands, repeatedly launches the operation, and records separate host and device timing paths. The synchronized CPU-timer case waits for the GPU operation before stopping the clock. CUDA events measure an interval on the device timeline. Neither instrument automatically includes application work that occurs outside its start and stop markers.

Given a baseline processing 4,096 tokens in 100 milliseconds, a candidate processing 3,072 tokens in 82 milliseconds does not demonstrate faster execution of equivalent work. Change the candidate to process the same tokens and produce outputs within tolerance. Expected observation: only then may its repeated latency distribution be compared, and a 10-percent reduction in kernel latency reduces total serial latency by only 2 percent if that kernel occupies one fifth of the original time.

Given a CPU call returning after 0.05 milliseconds, CUDA events reporting 1.0 millisecond, and synchronized wall time reporting 1.3 milliseconds, submission, device execution, and complete-call latency are all plausible. Change the measurement by synchronizing immediately after every launch. Expected observation: if the original schedule overlapped host submission or independent device work, these waits can remove that overlap and increase wall time. Compare the timelines to determine whether this occurred; even a correct clock can alter the schedule it measures.

## Practice

`labs/01_timing_basics.py` times the same GPU operation using an unsynchronized host timer, a synchronized host timer, and CUDA events. It writes the three measurements and checks finite output.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_timing_basics/logs/%j.out" \
  --error="$PWD/results/01_timing_basics/logs/%j.err" \
  slurm/01_timing_basics.sbatch --workload small
```

## Check your results

Each new job owns `results/01_timing_basics/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/01_timing_basics/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/01_timing_basics/logs/$LAB_JOB_ID.out"
cat "results/01_timing_basics/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Inspect `host_without_sync_median_ms`, `host_with_sync_median_ms`, and `cuda_events`. Confirm `finite_output`; this is a finite-value sanity check, not a numerical reference comparison. No exact relation among noisy individual samples is required.

Record sanitized environment, inputs, outputs, tolerances, warm-up, repetitions, and the single change under test.

Results are comparable only when all declared invariants hold.

Retain the supplied timing fields, timer type, synchronization placement, and warm-up state. Lab 01 retains host-time medians and CUDA-event min/median/p90 summaries; Lab 02 reports medians only. Neither result file retains the individual samples. Add raw-sample export as an extension before investigating additional percentiles or individual outliers; do not infer a distribution from one median.

Use device time to study a kernel and end-to-end time to judge a product path.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Host without sync median (seconds) | `host_without_sync_median_ms` | `s` |
| Host with sync median (seconds) | `host_with_sync_median_ms` | `s` |
| Cuda events / median (seconds) | `cuda_events.median_ms` | `s` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
source tools/course_env.sh 01_timing_basics --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 01_timing_basics \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run the small case first, then the larger profile. Compare the included operations and completion waits within each profile; do not attribute a cross-profile change solely to a timer mechanism.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_timing_basics/logs/%j.out" \
  --error="$PWD/results/01_timing_basics/logs/%j.err" slurm/01_timing_basics.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_timing_basics/logs/%j.out" \
  --error="$PWD/results/01_timing_basics/logs/%j.err" slurm/01_timing_basics.sbatch --workload large
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Draw where each timer begins and ends relative to host enqueue and device completion. Explain why synchronized wall time can exceed event time and why unsynchronized timing can appear implausibly small for large arithmetic work.

Throughput, latency, memory, numerical error, startup cost, and maintainability can move in opposite directions. Define the primary metric and guardrails before measuring so a candidate cannot choose its own success criterion afterward.

CUDA events measure elapsed time between device markers, not a sum of active kernel durations. They exclude host work and queueing outside the marker interval, but dependency waits and idle gaps caused by delayed host submission between markers can be included. Use a profiler to separate those gaps from active GPU work. Waiting for the required work makes a CPU measurement include completion, but a device-wide wait can destroy overlap if placed inside the schedule. Profiler overhead makes traces diagnostic rather than acceptance timing.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_timing_basics/logs/%j.out" \
  --error="$PWD/results/01_timing_basics/logs/%j.err" slurm/01_timing_basics.nsys.sbatch --workload small
```

The native Systems command is in `slurm/01_timing_basics.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. The launcher selects the first matching kernel inside `course_measure`, which wraps the GEMM in the CUDA-event timing loop. Initialization and the earlier host-timer loops are outside that range. Use Systems to identify the GEMM kernel, then verify the Compute report's kernel, launch geometry and NVTX range before interpreting its counters. Set `COURSE_PROFILE_KERNEL` to its observed name or a matching regular expression when narrowing the selection. This single-kernel capture does not measure either host-timer loop or end-to-end application latency.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_timing_basics/logs/%j.out" \
  --error="$PWD/results/01_timing_basics/logs/%j.err" slurm/01_timing_basics.ncu.sbatch --workload small
```

The native Compute command is in `slurm/01_timing_basics.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare unsynchronized host time, synchronized wall time, and CUDA-event time for the same GEMM. Independently choose the correct boundary for a CPU-originating request and explain why enqueue time is not latency.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Unexpectedly large host timings may include queued work, initialization, or contention. Isolate the run and inspect warm-up and synchronization placement. Do not discard inconvenient samples without a stated exclusion rule.

Optimizing first and attempting to reconstruct the baseline afterward loses causal evidence.

Synchronizing every operation removes overlap and measures an artificial schedule.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Every reported duration needs a named timer, included operations and completion condition. Use CUDA events for a device interval and a CPU timer with the required synchronization for the complete application request. Next, identify accidental synchronization inside a repeated workload in Lab 02.

Freeze correctness and workload identity, save the baseline, and reject contaminated comparisons.

Name the independent variable and every controlled variable for your next experiment.

Synchronize where dependencies or the measurement require it, and report which operations each metric includes.

Explain what a CUDA event excludes that application wall time includes.
