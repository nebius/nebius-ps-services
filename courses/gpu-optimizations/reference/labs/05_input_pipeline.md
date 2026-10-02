# Lab 05: Locate waiting in a DataLoader pipeline

GPU utilization can be limited by preparing and delivering the next batch rather than by the device computation itself. This lab compares zero and four DataLoader workers on deterministic synthetic samples. Its phase measurements teach you to distinguish waiting for a batch, transferring it, and consuming it, without pretending that synthetic CPU work represents a storage benchmark.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use one H100 and a compute-node environment that supports DataLoader worker processes and pinned memory. The launcher must have enough CPU resources. Pinning is enabled in both variants; positive-worker prefetch is fixed at two.

H100 can consume data rapidly enough that shared storage and modest CPU transforms become the limiting stage. Compare one node in isolation before interpreting two-node storage contention.

## Concepts and code path

A dataset maps an index to a sample; collation assembles samples into a batch. Deterministic per-index generation keeps content fixed while the loader compares zero and four workers. Zero workers prepare data in the caller; four prepare ahead with prefetch factor two. Batches contain eight samples and use pinned memory. The program synchronizes each batch to measure readiness, transfer and consumption separately. These serialized measurements do not establish copy/compute overlap.

Given 12-millisecond GPU steps and batch-ready intervals with a 9-millisecond median but 25-millisecond p95, tail starvation creates visible gaps. Change workers from four to eight and observe a 20-millisecond p95 plus doubled storage latency. Expected observation: the queue improves, but adding more workers after the storage knee is rejected; verify sample IDs and order remain correct.

## Practice

`labs/05_input_pipeline.py` compares serial and worker-backed DataLoaders feeding pinned batches to the GPU. It checks checksums and pinned inputs, then writes timing evidence for the explicitly serialized pipeline components.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_input_pipeline/logs/%j.out" \
  --error="$PWD/results/05_input_pipeline/logs/%j.err" \
  slurm/05_input_pipeline.sbatch --workload small
```

## Check your results

Each new job owns `results/05_input_pipeline/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/05_input_pipeline/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/05_input_pipeline/logs/$LAB_JOB_ID.out"
cat "results/05_input_pipeline/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require `checksum` and `all_batches_pinned`. Compare `batch_ready_gap`, `h2d`, `device_consumption`, and `end_to_end` phase distributions for `num_workers_0` and `num_workers_4`. End-to-end here is per batch; the JSON does not report a whole-campaign throughput measurement.

Both checksums must be finite. Infinity and NaN (not a number) invalidate the run before a successful result is written; investigate the computation before interpreting its timing.

Retain the supplied component and per-batch end-to-end timing distributions; the component measurements deliberately serialize boundaries and do not themselves prove overlap. Lab 05 does not emit whole-campaign throughput: add a complete-loop wall timer and actual consumed-sample count for that extension rather than inverting a median batch time. Also collect batch-ready timestamps, sample-order checks, host memory, and a profiler timeline showing GPU idle gaps. CPU utilization and storage latency require separate host/storage observations; synthetic samples do not measure filesystem performance.

The best setting keeps the GPU supplied without unstable queue growth or excessive host memory.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Variants / num workers 0 / phases / end to end / median (seconds) | `variants.num_workers_0.phases.end_to_end.median_ms` | `s` |
| Variants / num workers 4 / phases / end to end / median (seconds) | `variants.num_workers_4.phases.end_to_end.median_ms` | `s` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 05_input_pipeline \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run the fixed comparison first, then increase only the number of measured batches. Startup is warmed separately, and the program checks the checksum and pinning status.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_input_pipeline/logs/%j.out" \
  --error="$PWD/results/05_input_pipeline/logs/%j.err" slurm/05_input_pipeline.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_input_pipeline/logs/%j.out" \
  --error="$PWD/results/05_input_pipeline/logs/%j.err" slurm/05_input_pipeline.sbatch --workload small --batches 100
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Does worker parallelism reduce readiness gaps enough to offset its overhead? Which component dominates the batch boundary? Do not add component medians and assume their sum equals the median total.

More workers and prefetch raise memory, file descriptors, context switches, and storage concurrency. Offline preprocessing reduces online CPU work but creates a versioned data artifact that must match tokenizer or transform semantics.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_input_pipeline/logs/%j.out" \
  --error="$PWD/results/05_input_pipeline/logs/%j.err" slurm/05_input_pipeline.nsys.sbatch --workload small
```

The native Systems command is in `slurm/05_input_pipeline.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `lab_workload`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. The launcher selects one matching kernel inside `lab_workload`, the configured NVTX range for this lab. Its launch-count limit applies after the range and kernel-name filters. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/05_input_pipeline/logs/%j.out" \
  --error="$PWD/results/05_input_pipeline/logs/%j.err" slurm/05_input_pipeline.ncu.sbatch --workload small
```

The native Compute command is in `slurm/05_input_pipeline.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Use `--batches` as the single control in the existing Practice commands. Predict its effect on the measured fields, verify correctness, and inspect the named report views. Independently choose one additional value of the same control, repeat unprofiled, and explain why the result supports or rejects the prediction. Changing `batches` changes the workload; compare per-unit cost and capacity as a workload study, not a like-for-like optimization speedup.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate lab_workload and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Worker startup errors require inspecting the multiprocessing environment. Unpinned batches fail the declared comparison. More workers can be slower for small synthetic samples; that is a valid result, not a reason to discard the baseline.

Avoid measuring synthetic in-memory data and claiming a storage-pipeline improvement.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Parameterizing pinning and prefetch is an explicit extension, not an existing CLI feature. Preserve sample IDs, omit prefetch for zero workers, and add whole-loop timing and a real-data trial before making throughput or storage claims.

Tune the real slowest producer stage and recheck device utilization end to end.

Draw the queue between each producer and consumer stage.

Run Lab 05's supplied comparison of zero versus four loader workers on synthetic CPU-generated samples. Pinning is enabled, and the four-worker case uses prefetch factor two; these are fixed settings, not exposed sweep options. Extension: parameterize pin_memory and the positive-worker prefetch factor, vary one at a time, and preserve sample IDs and model work. Do not pass prefetch_factor to a zero-worker loader. Add a separate real-data trial before drawing storage conclusions.
