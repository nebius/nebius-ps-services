# Lab 01: Choose a valid GPU timing boundary

This lab measures one GPU operation three ways to expose a common benchmarking mistake: timing only the host's submission of asynchronous work. You will compare unsynchronized host time, synchronized host time, and CUDA-event time. The goal is to understand the question answered by each timer before choosing one for an optimization experiment.

## Before you start

Lesson 1 uses the comparison worksheet and reasoning below as a planning exercise. Complete Lesson 2 before running or interpreting the timing experiment.

**Theory preparation:** Read Lessons 1–2 for a fixed workload, warm-up, synchronized host timing, CUDA events and sample distributions. Fundamentals Lessons 2 and 9 supply the matrix-product and numerical-format meanings used by the workload.

Use one allocated H100 with no unaccounted competing workload. Activate the Optimizations environment. Review the distinction between enqueuing an operation and waiting until its result is ready.

H100 peak rates make small host gaps and poorly amortized collectives proportionally more visible. Record clock behavior, sharing mode, framework build, and detected SKU because they change the attainable baseline. Keep the allocation and software configuration fixed; do not lock clocks or reconfigure sharing as part of these labs.

Short H100 kernels make timer overhead and accidental synchronization significant. Use enough repeated work for stable resolution, but do not batch iterations so aggressively that cache state or scheduling changes the workload.

## Concepts and code path

The program creates resident operands, repeatedly launches the operation, and records separate host and device timing paths. Synchronization makes the host boundary include completion. CUDA events measure an interval on the device timeline. Neither instrument automatically includes application work that occurs outside its start and stop markers.

## Practice

### After Lesson 1

Given a baseline processing 4,096 tokens in 100 milliseconds, a candidate processing 3,072 tokens in 82 milliseconds does not demonstrate faster execution of equivalent work. Change the candidate to process the same tokens and produce outputs within tolerance. Expected observation: only then may its repeated latency distribution be compared, and a 10-percent reduction in kernel latency reduces total serial latency by only 2 percent if that kernel occupies one fifth of the original time.

Use the benchmark worksheet to write the invariant set for one later single-GPU lab. Capture its local environment from the first measurement lab after setup; inspect Lab 00 only as a topology preview here and run that two-node preflight in Lesson 11 before distributed experiments.

### After Lesson 2

Given a CPU call returning after 0.05 milliseconds, CUDA events reporting 1.0 millisecond, and synchronized wall time reporting 1.3 milliseconds, submission, device execution, and complete-call latency are all plausible. Change the measurement by synchronizing immediately after every launch. Expected observation: if the original schedule overlapped host submission or independent device work, these waits can remove that overlap and increase wall time. Compare the timelines to determine whether this occurred; even a correct clock can alter the schedule it measures.

Run Labs 01 and 02 and label every synchronization point.

### Run the supplied experiment

Run the smoke case first, then the larger profile. Compare timer boundaries within each profile; do not attribute a cross-profile change solely to a timer mechanism.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/01_timing_basics.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/01_timing_basics.py --profile h100
```

## Check your results

Inspect `host_without_sync_median_ms`, `host_with_sync_median_ms`, and `cuda_events`. Confirm `finite_output`; this is a finite-value sanity check, not a numerical reference comparison. No exact relation among noisy individual samples is required.

Record sanitized environment, inputs, outputs, tolerances, warm-up, repetitions, and the single change under test.

Results are comparable only when all declared invariants hold.

Retain the supplied timing fields, timer type, synchronization placement, and warm-up state. Lab 01 retains host-time medians and CUDA-event min/median/p90 summaries; Lab 02 reports medians only. Neither result file retains the individual samples. Add raw-sample export as an extension before investigating additional percentiles or individual outliers; do not infer a distribution from one median.

Use device time to study a kernel and end-to-end time to judge a product path.

## Investigate the behavior

Draw where each timer begins and ends relative to host enqueue and device completion. Explain why synchronized wall time can exceed event time and why unsynchronized timing can appear implausibly small for large arithmetic work.

Throughput, latency, memory, numerical error, startup cost, and maintainability can move in opposite directions. Define the primary metric and guardrails before measuring so a candidate cannot choose its own success criterion afterward.

CUDA events measure elapsed time between device markers, not a sum of active kernel durations. They exclude host work and queueing outside the marker interval, but dependency waits and idle gaps caused by delayed host submission between markers can be included. Use a profiler to separate those gaps from active GPU work. Full synchronization is accurate for a boundary but can destroy overlap if placed inside the schedule. Profiler overhead makes traces diagnostic rather than acceptance timing.

## If something goes wrong

Unexpectedly large host timings may include queued work, initialization, or contention. Isolate the run and inspect warm-up and synchronization boundaries. Do not discard inconvenient samples without a stated exclusion rule.

Optimizing first and attempting to reconstruct the baseline afterward loses causal evidence.

Synchronizing every operation removes overlap and measures an artificial schedule.

## Takeaways and next step

Every reported duration needs a named boundary. Use device events for scoped device work and synchronized wall time for an application boundary. Next, identify accidental synchronization inside a repeated workload in Lab 02.

Freeze correctness and workload identity, save the baseline, and reject contaminated comparisons.

Name the independent variable and every controlled variable for your next experiment.

Synchronize only at deliberate boundaries and report which boundary each metric spans.

Explain what a CUDA event excludes that application wall time includes.
