# Lab 05: Reuse neighboring values with a shared-memory halo

Stencil operations compute each output from nearby input values, so adjacent threads can reuse much of the same data. This lab implements a one-dimensional three-point stencil with a shared tile and two halo values. You will follow interior, block-edge, and global-edge behavior while verifying the boundary condition instead of assuming every neighboring address exists.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/05_tiled_stencil.json).

Use the completed SM90 build on one H100. The small case has 1,003 elements, deliberately leaving a partial block. The boundary convention is zero outside the input domain.

The H100 unified L1/shared-memory carveout and block residency influence tile size. Profile actual reuse and resource limits rather than assuming shared memory always beats cache.

## Concepts and code path

Each thread loads its central value into shared memory. Boundary threads load the neighboring halo values where valid, and a barrier makes those loads visible before computation. The output is `0.25*left + 0.5*center + 0.25*right`. A CPU loop implements the same zero-boundary convention. Only the tiled implementation is supplied; there is no timed naive GPU baseline in this lab.

Given a 256-output block and radius one, naive code requests up to 768 input values, while a cooperative tile needs about 258 unique values before cache effects. Change `n` to 257 so the second block is partial. Expected observation: every launched thread still reaches the barrier, only valid outputs are stored, and sanitizer and correctness checks pass for the edge case.

Run Lab 05 with `--profile small` (1,003 elements) and without arguments (2²⁴ elements). Inspect interior, block-boundary and global-edge outputs; the small fixture also exercises a partial block.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Run the small fixture with memory and race checking. The full case changes the element count but preserves the stencil and boundary rule.

```bash
umask 077
python3 tools/submit_lab.py --lab 05_tiled_stencil slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/05_tiled_stencil" --profile small
python3 tools/submit_lab.py --lab 05_tiled_stencil slurm/sanitizer.sbatch memcheck "${COURSE_BUILD_DIR}/05_tiled_stencil" --profile small
python3 tools/submit_lab.py --lab 05_tiled_stencil slurm/sanitizer.sbatch racecheck "${COURSE_BUILD_DIR}/05_tiled_stencil" --profile small
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 05_tiled_stencil --job "$LAB_JOB_ID"
```

Require full FP32 CPU-reference agreement and no relevant sanitizer errors. Inspect kernel distributions, `shared_bytes_per_block`, and `halo_values_per_full_block`. Correct output and timing measurements cannot establish a speedup without a measured baseline.

Retain global-load estimate, shared bytes, synchronization, time, and maximum error.

Tiling helps when reuse repays load and synchronization overhead.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Kernel median (seconds) | `kernel_median_ms` | `s` |
| Shared bytes per block | `shared_bytes_per_block` | `bytes` |
| Halo values per full block | `halo_values_per_full_block` | `none` |

Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 05_tiled_stencil \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Trace one interior point, a block-boundary point, and the final valid input. Which values are reused by neighboring threads? Why must masked threads still respect the block barrier?

Larger tiles improve halo amortization but consume shared memory and can reduce occupancy. Complex boundary logic may diverge. For little reuse, staging and barriers cost more than they save.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 05_tiled_stencil --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/05_tiled_stencil" --profile small
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. The default first-launch report checks that collection works; it can select initialization instead of the measured operation. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
python3 tools/submit_lab.py --lab 05_tiled_stencil --export=ALL,COURSE_PROFILE_TOOL=ncu slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/05_tiled_stencil" --profile small
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then set `COURSE_PROFILE_RANGE=phase_name` when selecting it. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare the global-memory stencil with shared-memory staging. Independently test threads=128 instead of 256, rebuild, and explain halo overhead versus occupancy at fixed elements.

For the source experiment, rebuild with the same image and build directory, then repeat the original run and capture commands:

```bash
python3 tools/submit_lab.py --lab 05_tiled_stencil --wait slurm/build_and_test.sbatch
export COURSE_BUILD_DIR="${COMPLETED_BUILD_DIRECTORY:?completed build/run-JOB_ID directory}"
```

The publisher compares the declared workload fields and source fingerprint; retain the original artifact and do not change input generation, timed scope, or correctness tolerances.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Errors only at block boundaries often implicate halo loading; errors at the global edge may indicate the wrong boundary convention. Inspect invalid shared/global reads before changing tile size.

A divergent early return before a block-wide barrier can deadlock.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Shared tiling makes reuse explicit but adds synchronization and halo overhead. Add a correct direct-global reference kernel as an extension, then compare the same workload and include memory-traffic evidence before claiming a performance benefit.

Keep every participating thread on the same barrier path and mask invalid data explicitly.

Identify all halo loads and boundary conditions for one block.
