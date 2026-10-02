# Lab 04: Evaluate strided access and the cost of repacking

Tensor shape does not reveal how neighboring values are arranged in memory. This lab compares a contiguous tensor, a transposed strided view, and a contiguous copy of that view while preserving each case's mathematical meaning. You will decide whether repacking is worthwhile by including its one-time cost and the number of future reuses.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Run on one H100 in the Fundamentals environment. A logical index identifies an element; strides map changes in its indices to storage offsets. For a contiguous 2-by-3 matrix, strides [3, 1] mean one row step skips three stored elements and one column step skips one. Transposing swaps its logical axes, producing shape [3, 2] and strides [1, 3] without copying values. Such a view shares the underlying storage. The small and H100 profiles choose different square sizes; neither exposes an arbitrary stride-layout sweep.

Start by identifying shared storage versus new allocations and counting bytes read and written. Then compare transaction efficiency and the number of reuses needed to repay a repack.

H100’s large HBM bandwidth and unified L1/shared-memory resources are powerful only when accesses have locality and enough concurrency. The capacity and bandwidth figures vary by H100 model, so labs report the detected device.

Use profiler sector/request metrics for the actual SM90 kernel rather than assuming a fixed transaction count from a simplified diagram. The address calculation and access width still determine the causal expectation.

## Concepts and code path

The workload applies the same pointwise expression to each layout. A transpose changes the index-to-address mapping without copying storage; making it contiguous performs a real copy. The lab separately times operations and repacking, checks corresponding outputs, and computes a reuse break-even from unrounded medians. PyTorch may choose an effective iteration order, so a strided view is not guaranteed to be slow.

Given a 32-by-32 tile whose 1,024 FP32 values would otherwise be read four times, the naive count is 16 KiB of requested reads before accounting for cache reuse. Change to one cooperative 4 KiB load into shared memory and four reuses. Expected observation: measured HBM traffic falls only to the extent that caches were not already satisfying those reads. Elapsed time improves only if the saved memory cost exceeds the extra staging and synchronization cost; fewer bytes can still accompany a slower kernel.

This is a traffic-accounting example; the supplied PyTorch layout experiment does not implement shared-memory tiling.

Given 32 lanes loading adjacent FP32 values from an aligned starting address, the useful footprint is 128 bytes and can occupy four 32-byte sectors. Change the same lanes to follow a column mapping with a 4,096-byte stride. They now touch 32 sectors for the same useful values. Expected observation: more sectors are requested for the same logical bytes. This is a specified lane-address model, not a promise about every transposed PyTorch tensor: a framework kernel may adapt its iteration order to strides. Measure sector traffic and elapsed time separately; extra transactions do not guarantee a proportional slowdown.

For each layout, predict which dimension should be assigned to adjacent lanes.

## Practice

`labs/04_layout_and_coalescing.py` times a pointwise expression on contiguous, transposed, and repacked tensors. It checks matching results and reports layout details, copy cost, useful bandwidth, and the estimated reuse needed to repay repacking.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/04_layout_and_coalescing/logs/%j.out" \
  --error="$PWD/results/04_layout_and_coalescing/logs/%j.err" \
  slurm/04_layout_and_coalescing.sbatch --workload small
```

## Check your results

Each new job owns `results/04_layout_and_coalescing/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/04_layout_and_coalescing/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/04_layout_and_coalescing/logs/$LAB_JOB_ID.out"
cat "results/04_layout_and_coalescing/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require `allclose`, confirmation that the view is strided, and confirmation that the repacked tensor is contiguous. Inspect `timings`, `useful_bandwidth_gib_per_s`, and `repack.break_even_reuses`. The field is an effective-bandwidth estimate based on logical input/output bytes, not measured physical HBM transactions.

Retain tensor shape/strides, logical input/output bytes and measured kernel time. These describe logical access and elapsed time; they do not establish physical memory traffic or cache behavior.

A high cache-hit rate is useful only when the access pattern and working set make those hits meaningful.

Record shape, strides, bytes, time, and profiler memory-transaction metrics.

Coalescing improves useful bytes per transaction; it does not guarantee overall speed if computation or launch overhead dominates.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Timings / contiguous / median (seconds) | `timings.contiguous.median_ms` | `s` |
| Timings / strided view / median (seconds) | `timings.strided_view.median_ms` | `s` |
| Repack / break even reuses | `repack.break_even_reuses` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 04_layout_and_coalescing \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Use the supplied cases before introducing a new layout. Keep the complete result, including strides and repack timing, so a future reader can reconstruct the trade-off.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/04_layout_and_coalescing/logs/%j.out" \
  --error="$PWD/results/04_layout_and_coalescing/logs/%j.err" slurm/04_layout_and_coalescing.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/04_layout_and_coalescing/logs/%j.out" \
  --error="$PWD/results/04_layout_and_coalescing/logs/%j.err" slurm/04_layout_and_coalescing.sbatch --workload large
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Use `copy_cost / (strided_time - packed_time)` only when the denominator is positive. If the packed operation is no faster, there is no finite timing break-even. Explain how repeated reuse changes the application decision.

A shared-memory tile costs loads, stores, address arithmetic, space, and barriers. Caching may already provide enough reuse. `empty_cache()` can return unused cached blocks to the system but is not a routine kernel optimization and can add allocation overhead.

Repacking improves later access and library eligibility but consumes bandwidth, memory, and launch time. A direct strided view is often better for one light use; a packed representation can win across many expensive reuses.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/04_layout_and_coalescing/logs/%j.out" \
  --error="$PWD/results/04_layout_and_coalescing/logs/%j.err" slurm/04_layout_and_coalescing.nsys.sbatch --workload small
```

The native Systems command is in `slurm/04_layout_and_coalescing.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/04_layout_and_coalescing/logs/%j.out" \
  --error="$PWD/results/04_layout_and_coalescing/logs/%j.err" slurm/04_layout_and_coalescing.ncu.sbatch --workload small
```

The native Compute command is in `slurm/04_layout_and_coalescing.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare strided and packed operations including the one-time repack cost. Independently calculate the reuse count at which packing pays back; choose packing only if the pipeline reuses the tensor enough times.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Comparing a transposed result against the untransposed reference confuses semantics with layout. Verify logical indexing before diagnosing precision. Treat tiny timing differences as inconclusive until repeated runs establish a stable sign.

Avoid counting tensor sizes once when an algorithm rereads or materializes intermediates many times.

Check tensor contiguity from its layout, for example with `is_contiguous()`. Check coalescing separately from the kernel's lane-to-address mapping: a contiguous tensor does not guarantee coalesced accesses.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Optimize the full data path, not just the operation after a free-looking conversion. A useful extension adds a downstream consumer and measures repack plus all consumers together against the original strided pipeline.

Count bytes for every read, write, temporary, and reuse before proposing a memory optimization.

Explain why shared memory can help one algorithm and add pure overhead to another.

Describe the address generated by adjacent lanes and transform layout or indexing to reduce wasted transactions.

Draw four lane addresses for a contiguous and a strided load.
