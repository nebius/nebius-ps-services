# Lab 19: Reduce gradients when they become ready

Backward produces parameter gradients at different times, creating opportunities to communicate an earlier gradient while later computation continues. This lab compares serialized reduction with gradient-ready asynchronous reductions across two H100 nodes. You will examine bucket construction order, observed readiness order, and the final wait rather than assuming that asynchronous submission guarantees useful overlap.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

**Advanced fabric route:** use the separate Soperator cluster with two eight-H100 workers (16 GPUs), healthy intra-node NVLink/NVSwitch and active inter-node InfiniBand. The base two one-GPU TCP workers are useful for local labs but cannot establish this fabric’s performance.

Pass distributed preflight and understand backward dependencies. The bucket option requires at least two positive sizes. This is a bounded synthetic gradient schedule, not a replacement DDP implementation for arbitrary models.

One rank participates on each eight-H100 worker. This isolates an inter-node dependency schedule; it does not measure intra-node switch saturation.

## Concepts and code path

The source constructs parameter work for ascending and descending bucket-size schedules. Post-accumulate hooks observe completed gradients and launch asynchronous all-reduces; the serialized reference waits until backward completes before reducing. Both paths join required communication before the step boundary ends. Reference checks compare reduced gradients, and timing uses the slowest rank.

The check converts each BF16 bucket to FP32 before subtraction. For reference
gradient `g`, its calculated scale is `s = max(abs(g))`; the code-selected
tolerances require `abs(actual - g) <= 0.01 * abs(g) + 0.01 * s` element by
element. For example, a reference maximum of `5e-6` gives an absolute allowance
of `5e-8`, rather than `0.01`. An all-zero reference requires exact zeros.
Invalid or nonfinite buckets fail the rank-wide MIN consensus before warmup
or measurement, so one rank cannot accept a result rejected by another.

Given 30 milliseconds of backward work and an 8-millisecond final gradient reduction, all 8 milliseconds are exposed. Change to three buckets whose reductions start during backward and leave a 2-millisecond final wait. Expected observation: step time improves even if summed collective duration exceeds 8 milliseconds; rank timelines must show independent overlap and unchanged gradients.

Run Lab 19 with several bucket schedules and draw the critical path.

## Practice

`labs/19_gradient_overlap.py` compares serialized gradient exchange with all-reduces launched when gradients become ready, using ascending and descending bucket orders. It checks equivalent finite gradients and writes readiness order, step timings, and timing ratios.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/19_gradient_overlap/logs/%j.out" \
  --error="$PWD/results/19_gradient_overlap/logs/%j.err" \
  slurm/19_gradient_overlap.sbatch --workload small
```

## Check your results

Each new job owns `results/19_gradient_overlap/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/19_gradient_overlap/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/19_gradient_overlap/logs/$LAB_JOB_ID.out"
cat "results/19_gradient_overlap/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require serialized/overlapped gradient agreement, finite gradients, and finite ratios. Inspect `observed_gradient_ready_bucket_indices`, step medians, and serialized collective timing. A shorter step time including communication completion is compatible with overlap but needs a timeline for the causal claim.

Each schedule's `gradient_validation` records the tolerances and rank-zero
per-bucket element count, reference magnitude, maximum absolute error, and
maximum error divided by the reference magnitude. Small errors retain their
full precision. The zero-reference normalized error is `null`; its exact-zero
check remains mandatory. Agreement between the two schedules does not detect
an error shared by both and does not prove that communication occurred.

Retain gradient-ready times, collective ranges, overlap, exposed communication, bucket bytes, and step time.

Keep the schedule that shortens the step, not the one with the most apparent overlap.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Schedules / ascending / serialized step median (seconds) | `schedules.ascending.serialized_step_median_ms` | `s` |
| Schedules / ascending / overlapped step median (seconds) | `schedules.ascending.overlapped_step_median_ms` | `s` |
| Schedules / descending / overlapped step median (seconds) | `schedules.descending.overlapped_step_median_ms` | `s` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 19_gradient_overlap \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Begin with 8 and 24 MiB buckets. Within each artifact, the serialized and overlapped variants share identical input work. Repeat that command for the two dashboard slots. A separate `--bucket-mib 16 16` investigation changes synthetic parameter grouping; treat it as a workload study, not an identical-model speedup. Reversing `8 24` to `24 8` changes nothing because both sorted schedules are already measured.

```bash
"$COURSE_PYTHON" labs/19_gradient_overlap.py --help
sbatch --chdir="$PWD" \
  --output="$PWD/results/19_gradient_overlap/logs/%j.out" \
  --error="$PWD/results/19_gradient_overlap/logs/%j.err" slurm/19_gradient_overlap.sbatch --workload small --bucket-mib 8 24
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Why may gradient readiness reverse forward construction order? Draw the first communication that can begin and the final exposed tail. Explain how bucket size trades collective startup overhead against earlier readiness.

Overlap can slow both operations through contention even while hiding wall time. Extra streams, buffers, and bucket policies increase memory and complexity. Low-precision collectives add error and cast/scaling work.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/19_gradient_overlap/logs/%j.out" \
  --error="$PWD/results/19_gradient_overlap/logs/%j.err" slurm/19_gradient_overlap.nsys.sbatch --workload small
```

The native Systems command is in `slurm/19_gradient_overlap.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Check exported statistics for every rank, then open representative reports from each worker in Systems. Load large reports in small groups and close them between comparisons. Expand NVTX, CUDA, and NCCL kernel rows. Align step/collective boundaries and compare each rank’s arrival, waiting, and compute intervals. A rank-local trace alone cannot establish communication overlap across the job. This course uses Systems for the live collective schedule and a separate local companion for Compute counters.

To inspect one local backward kernel, submit the unnumbered companion on one
H100. It shares the lab's BF16 bucket sizes and loss but creates no process
group, hooks, or collectives. Defaults are `--workload small`, `--seed 17`,
`--warmup 3`, and buckets `8 24` MiB (`64 192` for `large`). At least two
positive bucket sizes are required. It supports these four options only.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/19_gradient_overlap/logs/%j.out" \
  --error="$PWD/results/19_gradient_overlap/logs/%j.err" slurm/19_gradient_overlap.ncu.sbatch --workload small --bucket-mib 8 24
```

The native Compute command is in `slurm/19_gradient_overlap.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Warmup and forward construction happen before the `gradient_backward` NVTX
range. Compute uses process-scoped NVTX to include autograd worker threads,
kernel replay, and one matching kernel launch. The default kernel regex is
`.*`; set `COURSE_PROFILE_KERNEL` through the submission's `--export` option
to select a different backward kernel. The shared `basic` set includes
SpeedOfLight, MemoryWorkloadAnalysis and Occupancy sections. The launcher
accepts no arbitrary lab path, `NCU_SET`, or `NCU_SECTIONS` override.

After backward, an independent reference differentiates the BF16 product,
including the backward cast to BF16 before multiplication by the input.
Require the printed `gradient_validation.passed` value to be `true`, then open
the printed `.ncu-rep` and verify an actual kernel from `gradient_backward`
with those sections. A missing or empty report fails capture. Native H100
capture qualification remains pending until this check succeeds on the target.
This is one selected kernel, not the whole backward cost or evidence of
communication overlap. Reports and receipts remain private under
`results/19_gradient_overlap/jobs/$JOB_ID/profiles/`; they exclude acceptance timing and
are not result files for the dashboard or capstone aggregator.

Guided comparison: Use `--bucket-mib` as the single control in the existing Practice commands. Predict its effect on the measured fields, verify correctness, and inspect the named report views. Independently choose one additional value of the same control, repeat unprofiled, and explain why the result supports or rejects the prediction.

**Nsight Systems evidence:** Capture inside each participating GPU rank, retaining separate reports for cross-rank correlation. Check exported statistics for every rank, then open representative rank .nsys-rep reports from each worker. Load large reports in small groups and close them between comparisons. Expand CUDA streams, NCCL activity and available NVTX ranges; align collective boundaries and compare arrival, waiting and compute intervals across hosts. Use clock correlation before claiming cross-node overlap. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Inconsistent collective order across ranks can hang even if every bucket is valid locally. Buffer reuse before completion can corrupt gradients. Confirm waits and rank-consistent scheduling before investigating network performance.

A forced synchronization inserted for timing removes the overlap being measured.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Useful overlap is governed by dependencies and the critical path. Extend to a real model only with complete gradient/update references and a profiler-confirmed schedule; keep two-node mechanics distinct from production scaling.

Measure with non-intervening timeline ranges and synchronize only at the step boundary.

Explain the latency-versus-start-time tradeoff in bucket sizing.
