# Lab 04: Compare atomic, hierarchical, and CUB reductions

A reduction combines many inputs into a small output, so contention and aggregation strategy matter. This lab sums a vector using one atomic per element, warp/block aggregation followed by one atomic per block, and CUB's maintained device reduction. You will understand why reducing communication at each hierarchy level can matter more than making an individual addition faster.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/04_reduction.json).

Use the completed SM90 build with its CUDA/CUB headers on one H100. Inputs are ones and the reference sum is known. Arbitrary signed or ill-conditioned values are not covered by this fixture.

H100 atomics and warp operations are strong, but contention and memory scope still matter. Use the library implementation unless the need for custom fusion or behavior justifies owning the implementation.

## Concepts and code path

The naive kernel atomically updates one scalar for every element. The hierarchical kernel uses shuffle reductions within warps, shared partials across warps, then a block-level atomic. CUB first queries temporary storage and reuses it for timed calls. Atomic destinations are reset before each sample, outside the event interval; a complete application may need to include that reset cost.

Given one block of 1,024 threads, each issuing one global atomic, the output sees 1,024 contending updates. Change to 32-thread warp reductions, a shared-memory combination of warp partials within the block, and one atomic. Expected observation: global atomics drop to one per block, while CUB may still be faster or more robust; both outputs pass the declared floating-point tolerance.

Run Lab 04 with `--profile small` (1,003 elements) and without arguments (2²⁴ elements), comparing per-element atomics, block aggregation, and CUB. Additional sizes or input values require a source-edit/rebuild extension.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Run the partial-tail small case and relevant sanitizers before the full vector. All three paths operate on the same input and are reported separately.

```bash
umask 077
python3 tools/submit_lab.py --lab 04_reduction slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/04_reduction" --profile small
python3 tools/submit_lab.py --lab 04_reduction slurm/sanitizer.sbatch memcheck "${COURSE_BUILD_DIR}/04_reduction" --profile small
python3 tools/submit_lab.py --lab 04_reduction slurm/sanitizer.sbatch synccheck "${COURSE_BUILD_DIR}/04_reduction" --profile small
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 04_reduction --job "$LAB_JOB_ID"
```

Require all three sums to match the expected count. Inspect per-element versus block atomic counts, CUB temporary bytes, and timing distributions. This all-ones fixture does not characterize floating-point summation error for arbitrary inputs.

Retain operation order, atomic count, block size, time, numerical error, and library result.

Different reduction orders can produce different floating-point results; use declared tolerances rather than bitwise equality.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Per element atomic median (seconds) | `per_element_atomic_median_ms` | `s` |
| Warp block atomic median (seconds) | `warp_block_atomic_median_ms` | `s` |
| Cub median (seconds) | `cub_median_ms` | `s` |

Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 04_reduction \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Calculate how many atomic updates the block strategy removes. Why do inactive tail lanes contribute zero while still participating in the shuffle/barrier structure? Explain how CUB affects workspace requirements and maintenance effort.

More hierarchical aggregation reduces contention but adds synchronization and temporary storage. Deterministic reductions can cost throughput. Larger blocks reduce partial count while increasing resource use.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 04_reduction --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/04_reduction" --profile small
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. The default first-launch report checks that collection works; it can select initialization instead of the measured operation. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
python3 tools/submit_lab.py --lab 04_reduction --export=ALL,COURSE_PROFILE_TOOL=ncu slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/04_reduction" --profile small
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then set `COURSE_PROFILE_RANGE=phase_name` when selecting it. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare per-element atomics, block aggregation, and the CUB reference. Independently change threads from 256 to 128, retaining a power-of-two warp multiple, rebuild, and compare atomic traffic with kernel duration.

For the source experiment, rebuild with the same image and build directory, then repeat the original run and capture commands:

```bash
python3 tools/submit_lab.py --lab 04_reduction --wait slurm/build_and_test.sbatch
export COURSE_BUILD_DIR="${COMPLETED_BUILD_DIRECTORY:?completed build/run-JOB_ID directory}"
```

The publisher compares the declared workload fields and source fingerprint; retain the original artifact and do not change input generation, timed scope, or correctness tolerances.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

An increasing sum across repetitions indicates a missing reset. Tail errors can indicate an invalid shuffle participation assumption. Race or synchronization findings must be resolved before treating a faster reduction as valid.

Avoid optimizing one power-of-two length while failing partial blocks.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Use hierarchical aggregation and compare against maintained primitives before writing more custom reduction code. Extend with signed values and a high-accuracy reference, explicitly defining the numerical tolerance and including required setup in end-to-end timing.

Test odd-sized inputs and large values against a trusted reduction implementation, and verify the declared policy for empty inputs.

Explain where synchronization is required inside a block reduction.
