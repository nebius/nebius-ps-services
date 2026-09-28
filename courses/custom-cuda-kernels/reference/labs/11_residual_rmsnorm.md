# Lab 11: Fuse residual addition with row-wise RMS normalization

Residual addition followed by RMSNorm is a common transformer data path with opportunities to reuse values before writing them back to memory. This lab implements a small FP32 fused row kernel and validates it against a CPU reference. You will understand the row reduction and normalization architecture before generalizing it to large hidden widths, learned scales, or reduced-precision storage.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/11_residual_rmsnorm.json).

Use the completed SM90 build on one H100. The supplied width is fixed at 256 with unit scale weights; small has 17 rows and full has 4096. Hidden-size and dtype switches are not implemented.

BF16/FP16 inputs with FP32 reduction are a practical H100 path. Vector width and block shape must fit actual hidden sizes; do not infer speed from a toy row.

## Concepts and code path

One block owns one row and one thread owns one column. Each thread adds input and residual, retains that value, and contributes its square to shared memory. A tree reduction computes the row sum of squares; all threads normalize using `rsqrt(mean_square + 1e-5)` and apply their scale. The CPU reference matches the fixed-width, unit-scale fixture. There is no timed unfused GPU baseline.

Given a BF16 row of width 4096, separate kernels write 8 KiB and reread another 8 KiB of residual per row: 16 KiB of logical intermediate traffic. Change to fused addition and RMSNorm with FP32 sum-of-squares. Expected observation: intermediate traffic falls, but odd width 4103, zero rows, and large magnitudes must pass recipe tolerance; resource evidence decides whether retaining values causes spills. For a hand calculation, let x+skip=[3,4], learned scale=[1,1], and temporarily use epsilon=0 to simplify arithmetic. The mean square is (9+16)/2=12.5, so output is approximately [0.8485,1.1314]. The actual lab uses epsilon=1e-5 to keep the denominator positive for an all-zero row. RMSNorm scales by root mean square; unlike LayerNorm, it does not first subtract the row mean.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Run the small reference and relevant memory/race checks. The full profile changes row count only and does not test the width-generalization problem.

```bash
umask 077
python3 tools/submit_lab.py --lab 11_residual_rmsnorm slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/11_residual_rmsnorm" --profile small
python3 tools/submit_lab.py --lab 11_residual_rmsnorm slurm/sanitizer.sbatch memcheck "${COURSE_BUILD_DIR}/11_residual_rmsnorm" --profile small
python3 tools/submit_lab.py --lab 11_residual_rmsnorm slurm/sanitizer.sbatch racecheck "${COURSE_BUILD_DIR}/11_residual_rmsnorm" --profile small
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 11_residual_rmsnorm --job "$LAB_JOB_ID"
```

Require complete FP32 CPU-reference agreement and no relevant sanitizer errors. Inspect timing, accumulation dtype, and epsilon. These gates do not prove BF16/FP16 behavior, arbitrary-width correctness, or a speedup over an unfused implementation.

For the supplied lab record its fixed FP32 shape, epsilon=1e-5, unit scale, CPU-reference error, and CUDA-event samples. For extensions additionally record actual dtype/shape support, arbitrary scale tests, zero/extreme inputs, reduction strategy, unfused GPU reference, launches, logical bytes, measured traffic, and recipe-specific tolerances.

A fast result is useful only if stability and error remain acceptable across representative rows. Qualify each specialized or approximate-math path against recipe-specific tolerances and the real input range.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Kernel median (seconds) | `kernel_median_ms` | `s` |
| Elements | `elements` | `none` |
| Epsilon | `epsilon` | `none` |

Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 11_residual_rmsnorm \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Why can the sum of the input and residual stay in a register through the reduction? Which barrier makes the final sum safe to read? Explain why merely launching 256 threads cannot process a 4096-column row correctly with this indexing.

Keeping residual values in registers can raise pressure; rereading them adds traffic. One-pass reductions can be less stable or more complex. Fusion reduces modularity and needs variants for dtype/layout.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 11_residual_rmsnorm --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/11_residual_rmsnorm" --profile small
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. The default first-launch report checks that collection works; it can select initialization instead of the measured operation. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
python3 tools/submit_lab.py --lab 11_residual_rmsnorm --export=ALL,COURSE_PROFILE_TOOL=ncu slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/11_residual_rmsnorm" --profile small
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then set `COURSE_PROFILE_RANGE=phase_name` when selecting it. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare the fused kernel with its CPU reference, then independently replace its shared-tree reduction with a warp/block reduction and rebuild. Preserve rows, width=256, epsilon and tolerance; use memory traffic and stalls to assess the candidate.

For the source experiment, rebuild with the same image and build directory, then repeat the original run and capture commands:

```bash
python3 tools/submit_lab.py --lab 11_residual_rmsnorm --wait slurm/build_and_test.sbatch
export COURSE_BUILD_DIR="${COMPLETED_BUILD_DIRECTORY:?completed build/run-JOB_ID directory}"
```

The publisher compares the declared workload fields and source fingerprint; retain the original artifact and do not change input generation, timed scope, or correctness tolerances.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Wrong normalization across every column suggests an incorrect reduction or divisor. Race findings require checking shared-memory synchronization. Non-unit-scale extensions must also update the CPU reference to include those weights.

Accumulating squared BF16 values in BF16 loses accuracy for large hidden dimensions.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Generalize with strided per-thread column loops, tail masks, partial-sum reduction, and FP32 accumulation. Test widths 255, 256, 4096, and 4103, then add reduced-precision loads/stores and a separately implemented unfused baseline.

Accumulate in FP32, validate edge shapes, and compare with a trusted unfused reference.

Derive the reads/writes removed by fusion.

Run Lab 11's supplied FP32 mechanics baseline: hidden width 256, unit scale weights, and 17 rows for --profile small or 4096 rows by default. It checks a CPU reference and repeated kernel timing; it does not expose hidden-size or dtype switches. Extension: replace one-element-per-thread indexing with a strided per-thread loop, reduce each thread's partial sum, mask tails, and test widths 255, 256, 4096, and 4103. Define empty-row handling on the host, reject nonpositive width, test non-unit learned weights, and add BF16/FP16 load/store conversion with FP32 reduction. Compare with a separately implemented unfused reference before any performance claim.
