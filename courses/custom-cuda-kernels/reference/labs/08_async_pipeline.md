# Lab 08: Double-buffer global-to-shared copies

Asynchronous copies can prepare the next tile while the current tile is being processed, but correct staging and enough independent computation are essential. This lab compares a serial tiled transform with a double-buffered Cooperative Groups copy pipeline. You will trace priming, steady-state work, waits, and buffer reuse while varying arithmetic intensity under a fixed data-movement convention.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

Use the completed SM90 build. Both variants use one block intentionally; this is a local pipeline mechanics experiment, not an HBM-saturating whole-GPU benchmark.

H100 supports advanced asynchronous movement, but extra shared stages consume capacity. The baseline lab uses documented block-scoped APIs; TMA and warp specialization remain separately gated.

## Concepts and code path

Cooperative Groups is CUDA's interface for naming collaborating groups of threads and performing coordinated operations within them. This lab uses the entire thread block as the group participating in asynchronous copies, waits and synchronization. `this_thread_block()` names the group, `cooperative_groups::memcpy_async` issues its copies, and `wait` establishes readiness. Block synchronization protects reuse. Device `fmaf` computation is checked against a CPU `std::fma` reference, preserving single-rounding arithmetic.

The serial kernel loads a tile, synchronizes, computes, and advances. The pipelined kernel primes one shared buffer, starts the next copy into the other buffer, computes the current tile, then waits and synchronizes before reuse. Tail loads are bounded. A sentinel is a deliberately recognizable initial value. Before each validation launch, this lab fills the output with NaN (Not a Number), a special floating-point value. An element the kernel fails to write retains that marker and fails the check that every output is finite. Independent CPU references also detect incorrect finite values in both variants.

Given four tiles with a 5-microsecond load and an 8-microsecond computation per tile, serial execution is about 52 microseconds before overhead. Change to a correct two-stage pipeline. Expected observation: after a 5-microsecond prime, steady stages approach the 8-microsecond compute limit and then drain, but shared-memory use and synchronization can prevent the ideal 37-microsecond bound.

Run Lab 08 to sweep 0, 8, 32 and 128 FMAs per element for serial and pipelined variants. Both read/write the same elements, so logical intensity is 0, 2, 8 and 32 FLOPs per byte under an eight-byte global-traffic convention. Use `--work-iterations N` to isolate a point from 0 through 1024. Both small and full profiles include a partial final tile.

## Practice

`labs/08_async_pipeline.cu` compares serial tiled processing with a two-stage asynchronous copy pipeline across arithmetic intensities. It verifies both full outputs against CPU references and reports timing and pipeline-stage details. The baseline launcher records its output as course result JSON.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/08_async_pipeline/logs/%j.out" \
  --error="$PWD/results/08_async_pipeline/logs/%j.err" \
  slurm/08_async_pipeline.sbatch --workload small
```

## Check your results

Each new job owns `results/08_async_pipeline/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/08_async_pipeline/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/08_async_pipeline/logs/$LAB_JOB_ID.out"
cat "results/08_async_pipeline/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Validation initialization with the NaN sentinel is outside kernel-only timing. A missing write must fail even when the kernel appears faster.

Require complete FP32 reference agreement in both variants and no relevant sanitizer errors. Inspect serial/pipelined distributions at each work point. With eight logical global bytes per element and two FLOPs per FMA, intensities are 0, 2, 8, and 32 FLOPs/byte.

Retain pipeline stages, per-point timing distributions, independent CPU-reference results for both variants, logical work/bytes, stall metrics, and shared-memory cost. This single-block mechanism exercise does not saturate the H100; logical bytes exclude shared traffic, cache effects and transaction padding. Verify copy/compute overlap through profiling rather than inferring it from the API name or timing alone.

Overlap helps only when enough independent compute exists and extra shared memory does not destroy residency.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Cases / case / serial median (seconds) | `cases.*.serial_median_ms` | `s` |
| Cases / case / pipelined median (seconds) | `cases.*.pipelined_median_ms` | `s` |
| Cases / case / work iterations | `cases.*.work_iterations` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
source tools/course_env.sh 08_async_pipeline --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 08_async_pipeline \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

The default sweep uses 0, 8, 32, and 128 FMAs per element. Isolate a point with `--work-iterations` when profiling or checking synchronization; valid values are 0 through 1024.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/08_async_pipeline/logs/%j.out" \
  --error="$PWD/results/08_async_pipeline/logs/%j.err" slurm/08_async_pipeline.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/08_async_pipeline/logs/%j.out" \
  --error="$PWD/results/08_async_pipeline/logs/%j.err" slurm/08_async_pipeline.sanitizer.sbatch synccheck --workload small --work-iterations 32
sbatch --chdir="$PWD" \
  --output="$PWD/results/08_async_pipeline/logs/%j.out" \
  --error="$PWD/results/08_async_pipeline/logs/%j.err" slurm/08_async_pipeline.sanitizer.sbatch racecheck --workload small --work-iterations 32
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Which interval can hide the next copy, and when must the consumer wait? Why might the zero-compute case gain nothing? Explain how double buffering increases shared storage and can affect a future multi-block implementation.

More stages hide longer latency but consume shared memory/registers and lengthen fill/drain. Small tiles or little compute cannot amortize synchronization. Complexity increases race and deadlock risk.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/08_async_pipeline/logs/%j.out" \
  --error="$PWD/results/08_async_pipeline/logs/%j.err" slurm/08_async_pipeline.nsys.sbatch --workload small
```

The native Systems command is in `slurm/08_async_pipeline.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/08_async_pipeline/logs/%j.out" \
  --error="$PWD/results/08_async_pipeline/logs/%j.err" slurm/08_async_pipeline.ncu.sbatch --workload small
```

The native Compute command is in `slurm/08_async_pipeline.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare serial staging with the two-stage pipeline at each fixed work_iterations value. Independently change threads from 256 to 128, rebuild, and explain the shared-memory/occupancy trade-off. Different work_iterations values are separate workload campaigns. Changing `work_iterations` changes the workload; compare per-unit cost and capacity as a workload study, not a like-for-like optimization speedup.

After changing the CUDA source, rerun this lab’s CUDA preparation from the Lab Guide to compile a new private build with the same pinned toolkit. Then repeat the original run and capture commands.

The publisher compares the declared workload fields and source fingerprint; retain the original artifact and do not change input generation, timed scope, or correctness tolerances.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Tail corruption suggests incorrect valid-byte counts or reads from an unfilled stage. Intermittent errors suggest missing readiness or reuse synchronization. Fix those before interpreting timing or compiler instruction selection.

Reading a stage before its asynchronous copy is complete produces intermittent corruption.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Pipelining requires an explicit producer/consumer contract. Extend to multiple blocks only with safe tile ownership, complete reference checks, resource measurements, and end-to-end evidence; do not assume the asynchronous API guarantees overlap.

Model prime, steady-state, and drain phases and validate every synchronization edge.

Draw the two-stage timeline for three tiles.
