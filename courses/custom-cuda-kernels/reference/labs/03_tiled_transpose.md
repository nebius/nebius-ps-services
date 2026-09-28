# Lab 03: Coalesce a transpose and reduce shared-memory conflicts

A transpose naturally makes either reads or writes strided when implemented directly. This lab compares naive, tiled-unpadded, and tiled-padded CUDA transposes. You will see how a shared-memory tile changes data movement and why adding one padding column can improve bank mapping without changing the mathematical output or the number of useful matrix elements.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/03_tiled_transpose.json).

Complete the supplied SM90 transpose comparison after Lesson 6. The CUDA Tile C++ evaluation below is an optional revisit after Lesson 16, using its separately qualified development-image trial; it is not required for this lab or core course completion.

Use the completed SM90 build and one H100. Smoke uses 1003×1020 and full uses 8192×8209: both are rectangular edge cases. Arbitrary or square shapes require a source-edit/rebuild extension.

SM90 transaction and bank metrics should confirm the model. The general coalescing principle is portable; exact profiler metric names and optimal tile geometry depend on toolkit and kernel resource use.

The CUDA Tile C++ extension is version-gated and optional for H100. It does not change the required CUDA C++20/SM90 path or completion criteria.

## Concepts and code path

The naive kernel directly reverses row/column indices. Tiled variants cooperatively load contiguous rows, synchronize, and read the tile in transposed order for coalesced output. Shared arrays have 32 or 33 columns; the latter changes the bank mapping. Bounds checks protect edge tiles, and all block threads must reach the barrier even when some do not own a valid output.

Given lane `i` writing a transposed column with address stride equal to the output row width, which is the input row count, global stores scatter. Change to a `32×32` shared tile: global stores coalesce but column reads map to the same bank. Add one padding column. Expected observation: global transaction efficiency remains high and shared-bank conflicts fall; the supplied 1003×1020 small test checks those predicates and barriers on partial edge tiles, alongside reasoning and sanitizer evidence.

Run Lab 03's naive, tiled-unpadded, and tiled-padded variants on its supplied rectangular edge shapes: 1003×1020 for small and 8192×8209 for full. A square shape or arbitrary dimensions require a source-edit/rebuild extension with corresponding reference and sanitizer checks; the executable has no shape option.

Given the accepted padded transpose, express the same 32-by-32 tile load/transpose/store in CUDA Tile C++. Change no semantics or workload. Expected observation: source may become shorter, but adoption requires correct results for the same edge cases, supported sanitizer/profile tooling, understood generated memory transactions, and no unacceptable compile/runtime regression versus the CUDA C++ fallback.

In a separate CUDA 13.3 development-image trial, read the official principles, record the exact immutable image, and write a porting design for one existing lab. Do not change the required build or claim execution until that profile passes compiler and H100 gates.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Run all three variants and both memory/race checks. Keep the same matrix dimensions and reference when comparing variants; sanitizer reports are separate from timing evidence.

```bash
umask 077
python3 tools/submit_lab.py --lab 03_tiled_transpose slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/03_tiled_transpose" --profile small
python3 tools/submit_lab.py --lab 03_tiled_transpose slurm/sanitizer.sbatch memcheck "${COURSE_BUILD_DIR}/03_tiled_transpose" --profile small
python3 tools/submit_lab.py --lab 03_tiled_transpose slurm/sanitizer.sbatch racecheck "${COURSE_BUILD_DIR}/03_tiled_transpose" --profile small
```

Racecheck retains the same shapes, variants and repetitions. The launcher uses
the allocated CPU count for analysis workers and a per-stream synchronization
limit of two launches to control host tracking-memory growth. This changes
instrumented scheduling, so keep its timings separate from the clean benchmark.
An out-of-memory kill or a partial capture is not a passing sanitizer result.

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 03_tiled_transpose --job "$LAB_JOB_ID"
```

Require all three complete CPU-reference comparisons. Inspect `naive`, `tiled_unpadded`, and `tiled_padded` distributions and the declared shared-column counts. Use a focused profiler report to establish bank-conflict and global-transaction behavior rather than inferring it solely from timing.

Retain transactions, bank-conflict metrics, effective bandwidth, time, and correctness.

The padded tile is accepted when fewer conflicts translate into lower end-to-end transpose time.

For the optional CUDA Tile C++ evaluation, retain toolkit and API versions, architecture target, generated-code inspection, reference comparison, sanitizer result, profiler evidence, timing distribution, and a keep/defer decision.

Adopt the abstraction only when it preserves the operation contract and provides a maintainable path on the qualified deployment targets.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Naive median (seconds) | `naive_median_ms` | `s` |
| Tiled unpadded median (seconds) | `tiled_unpadded_median_ms` | `s` |
| Tiled padded median (seconds) | `tiled_padded_median_ms` | `s` |

Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 03_tiled_transpose \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Map a warp's load and transposed shared-memory read addresses. How does the extra column alter bank indices? Why can padding help the shared phase but still leave another resource as the overall limiter?

Padding consumes extra shared memory; larger tiles improve amortization but can reduce occupancy. Shared-memory staging adds instructions and barriers and loses when the original access or caches were already sufficient.

Higher-level tiles may improve clarity and portability of intent while limiting low-level control or depending on a less mature compiler implementation. Maintaining two implementations also increases test cost.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 03_tiled_transpose --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/03_tiled_transpose" --profile small
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. The default first-launch report checks that collection works; it can select initialization instead of the measured operation. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
python3 tools/submit_lab.py --lab 03_tiled_transpose --export=ALL,COURSE_PROFILE_TOOL=ncu slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/03_tiled_transpose" --profile small
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then set `COURSE_PROFILE_RANGE=phase_name` when selecting it. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare naive, unpadded-tile, and padded-tile transpose. Independently test tile_width=16 instead of 32, update the reported shared-column counts to match, rebuild, and inspect bank conflicts at fixed rows and columns.

For the source experiment, rebuild with the same image and build directory, then repeat the original run and capture commands:

```bash
python3 tools/submit_lab.py --lab 03_tiled_transpose --wait slurm/build_and_test.sbatch
export COURSE_BUILD_DIR="${COMPLETED_BUILD_DIRECTORY:?completed build/run-JOB_ID directory}"
```

The publisher compares the declared workload fields and source fingerprint; retain the original artifact and do not change input generation, timed scope, or correctness tolerances.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Failures near matrix edges suggest mismatched input/output guards. Barrier divergence or reading unwritten shared entries requires fixing synchronization/indexing, not increasing allocation blindly. Inspect the exact sanitizer location.

Omitting bounds checks makes only tile-aligned matrices appear correct.

Avoid treating source-level convenience or successful compilation as proof of runtime correctness, performance, or portability.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Tiling coordinates global access and local reuse; padding changes bank layout. Add square and additional edge shapes only with new full-reference and sanitizer checks before generalizing the performance conclusion.

Validate partial tiles and map adjacent lanes to adjacent global addresses in both phases.

Draw lane addresses for load, shared placement, and store.

Keep CUDA Tile C++ isolated as an optional CUDA 13.3 evaluation; the core course remains CUDA C++20, SM90, CMake, and library baselines whose target qualification is recorded separately.

List the evidence required to promote this optional evaluation from advanced/deferred to an accepted H100 lab.
