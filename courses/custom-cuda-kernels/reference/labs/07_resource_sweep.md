# Lab 07: Relate block size, live state, and occupancy limits

More resident warps can help hide latency, but a kernel's registers, local storage, and block geometry constrain residency. This lab sweeps thread-block sizes and separately increases per-thread state. You will read compiler/runtime resource evidence alongside timing, distinguishing theoretical residency from achieved occupancy and avoiding comparisons that silently change the amount of arithmetic.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/07_resource_sweep.json).

Use the completed SM90 build on one H100 and retain compiler resource output. Nsight counter access may require the cluster owner's approved profiler configuration.

Use SM90 limits and compiler output. Thread-block cluster kernels need cluster occupancy APIs and may reduce active blocks further.

## Concepts and code path

A fused multiply-add (FMA) computes `a*b + c` with one final rounding, rather than rounding the product separately before adding. Performance accounting conventionally counts its multiplication and addition as two floating-point operations. The device uses `fmaf`; the source checks the first and last outputs against a CPU `std::fma` reference, not every output element.

A templated kernel maintains several per-thread values through repeated FMAs and writes their sum. For four state values, the host sweeps 64, 128, 256, and 512 threads; separate 256-thread cases use 16 and 64 values. CUDA function attributes report registers/local bytes, and the occupancy API estimates active blocks. Increasing state count changes useful arithmetic as well as resource pressure.

Given 320 threads per block, 65,536 32-bit registers per SM, and no tighter thread, shared-memory, or block limit, using 128 registers/thread allows floor(65,536/40,960)=1 block. Change register demand to 96 registers/thread: the register limit then allows floor(65,536/30,720)=2 blocks. Expected observation: actual allocation granularity and the compiled kernel must be checked with occupancy APIs. If the cap adds spills and enough local-memory traffic to slow execution, reject it despite higher theoretical occupancy.

Build and run Lab 07 across block sizes and inspect compiler resource usage and Nsight counters.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Run the supplied sweep and memory check. Choose one case for a focused profiler investigation rather than treating all kernels' counters as one aggregate occupancy measurement.

```bash
umask 077
python3 tools/submit_lab.py --lab 07_resource_sweep slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/07_resource_sweep" --profile small
python3 tools/submit_lab.py --lab 07_resource_sweep slurm/sanitizer.sbatch memcheck "${COURSE_BUILD_DIR}/07_resource_sweep" --profile small
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 07_resource_sweep --job "$LAB_JOB_ID"
```

Require each case's reference check, noting it samples the first and last output only. Inspect registers/thread, local bytes/thread, active blocks/SM, resident warps, median, and p90. Local storage size alone is not a measured spill-traffic counter.

Retain registers, spill loads/stores, shared bytes, achieved occupancy, eligible warps, and time.

The best resource point supplies enough latency hiding without expensive spills or unnecessary instructions. An L2 persisting-access window is an advanced cache-priority hint for a stable, bounded hot region, not a promise to pin data in L2; it competes for cache capacity and needs an application-level A/B test rather than blanket enablement.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Cases / case / median (seconds) | `cases.*.median_ms` | `s` |
| Cases / case / registers per thread | `cases.*.registers_per_thread` | `none` |
| Cases / case / active blocks per sm | `cases.*.active_blocks_per_sm` | `none` |

Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 07_resource_sweep \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Compare block sizes at fixed state count while keeping the amount of arithmetic fixed. Separately explain the state-count survey's changed arithmetic. Does the fastest case maximize theoretical resident warps, and what profiler evidence could explain a difference?

Larger blocks amortize work and reduce partials but consume resources. Unrolling increases independent work and registers. Launch bounds guide compiler choices but can hurt performance for other shapes or toolchains.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 07_resource_sweep --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/07_resource_sweep" --profile small
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. The default first-launch report checks that collection works; it can select initialization instead of the measured operation. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
python3 tools/submit_lab.py --lab 07_resource_sweep --export=ALL,COURSE_PROFILE_TOOL=ncu slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/07_resource_sweep" --profile small
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then set `COURSE_PROFILE_RANGE=phase_name` when selecting it. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare the block-size cases at state_values=4; then inspect the separate changed-work state survey. Independently change the resource_kernel loop unroll pragma, rebuild, and compare register/stall evidence while preserving all six cases and eight arithmetic iterations.

For the source experiment, rebuild with the same image and build directory, then repeat the original run and capture commands:

```bash
python3 tools/submit_lab.py --lab 07_resource_sweep --wait slurm/build_and_test.sbatch
export COURSE_BUILD_DIR="${COMPLETED_BUILD_DIRECTORY:?completed build/run-JOB_ID directory}"
```

The publisher compares the declared workload fields and source fingerprint; retain the original artifact and do not change input generation, timed scope, or correctness tolerances.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A resource increase can lower occupancy without making execution slower. Do not force a narrative from occupancy alone. Before attributing unexpected local storage to spills, inspect compiler reports and load/store evidence for the selected kernel.

Avoid treating occupancy percentage as the optimization objective.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Tune resources to the bottleneck rather than chasing a maximum occupancy percentage. Extend to full-output correctness and a register-pressure experiment that holds useful work constant before accepting a production transformation.

Select the fastest correct configuration in the measurements and explain its performance using resource and stall evidence.

State why lower occupancy can be faster.
