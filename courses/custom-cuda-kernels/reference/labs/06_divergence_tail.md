# Lab 06: Group unequal work while preserving output order

Neighboring lanes that execute different amounts of work can leave some lanes inactive while others continue. This lab compares alternating long/short work with a grouped arrangement that preserves every input's assigned calculation. You will include the packing and scattering needed to restore logical order, avoiding a misleading comparison that treats data reorganization as free.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use the completed SM90 build on one H100. Even-indexed elements perform 32 fused multiply-add operations (FMAs) and odd-indexed elements perform four. Each FMA computes `a*b + c` with one final rounding; it counts as two floating-point operations. The supplied modes are divergent and grouped paths, not a configurable balanced/skewed scheduling suite.

Calculate concurrent blocks from SM90 registers, shared memory, and block limits rather than SM count alone. Library/persistent kernels may use scheduling policies beyond a simple static grid.

## Concepts and code path

The baseline branches on logical index parity. The grouped path packs long tasks together, runs their corresponding iteration counts, then scatters outputs back to original indices. A CPU reference and cross-path comparison enforce preserved work. Timings separately report grouped core, packing/scattering overhead, and the joined grouped path. The printed wave estimate assumes one block per SM; it is not measured occupancy.

Given mixed lane tasks costing 2 or 20 iterations, one warp executes both paths. Change to sorted tasks so warps are uniform; branch evidence improves. Separately repartition the same total tasks from 241 to 240 blocks on a device with capacity for 240 resident blocks; do not obtain the change by dropping work. Expected observation: the first addresses lane masks, the second removes a tail wave; neither necessarily repays sorting or altered task granularity end to end.

Run Lab 06's divergent and grouped paths with the same logical long/short work. Compare the complete grouped timing, including packing and scattering, with the divergent baseline. Its printed wave counts assume one block per SM and are not measured residency. Balanced/skewed task and explicit grid-wave surveys are separate exercises in GPU Optimizations Lab 15; they are not modes in this executable.

## Practice

`labs/06_divergence_tail.cu` compares divergent work with grouped execution plus packing and scattering. It checks both logical outputs against a CPU reference and each other, then reports kernel and complete-path costs. The baseline launcher records its output as course result JSON.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 \
  --chdir="$PWD" \
  --output="$PWD/results/06_divergence_tail/logs/%j.out" \
  --error="$PWD/results/06_divergence_tail/logs/%j.err" \
  slurm/single_gpu.sbatch \
  "${COURSE_BUILD_DIR:?set the completed build directory}/06_divergence_tail" --profile small
```

## Check your results

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/06_divergence_tail/logs/$LAB_JOB_ID.out"
cat "results/06_divergence_tail/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require both CPU-reference comparisons and agreement between restored outputs. Inspect long/short counts, `divergent`, `grouped_core_only`, `pack_and_scatter_overhead`, and `grouped_end_to_end` distributions. A faster core can coexist with a slower complete grouped path.

Retain active-lane metrics, per-block work, grid waves, tail duration, preprocessing, and total time.

Apply the remedy at the level where uneven work is introduced.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Divergent median (seconds) | `divergent_median_ms` | `s` |
| Grouped end to end median (seconds) | `grouped_end_to_end_median_ms` | `s` |
| Grouped core only median (seconds) | `grouped_core_only_median_ms` | `s` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 06_divergence_tail \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run the paired small case and memory check before increasing problem size. The primary grouped timing includes reorganization; do not substitute the core-only number in the final comparison.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/06_divergence_tail/logs/%j.out" \
  --error="$PWD/results/06_divergence_tail/logs/%j.err" slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/06_divergence_tail" --profile small
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/06_divergence_tail/logs/%j.out" \
  --error="$PWD/results/06_divergence_tail/logs/%j.err" slurm/sanitizer.sbatch memcheck "${COURSE_BUILD_DIR}/06_divergence_tail" --profile small
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Trace an odd logical index through its packed position and return mapping. Which work is unchanged and which overhead is added? Use compiler/profiler evidence to inspect the actual branch and residency behavior.

Sorting improves branch coherence while harming locality and spending bandwidth. Dynamic queues balance work but serialize on atomics. More blocks fill waves but increase redundant setup.

Capture a separate diagnostic run:

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/06_divergence_tail/logs/capture-%J-%t.out" \
  --error="results/06_divergence_tail/logs/capture-%J-%t.err" \
  "${COURSE_CONTAINER_RUNNER:?select the qualified runner}" "${CUDA_IMAGE_DIGEST:?select the qualified image}" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=nsys \
  nsys profile --trace=cuda,nvtx,osrt \
  --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none \
  --discard-environment=true --force-overwrite=false \
  --duration=300 --kill=none --wait=all \
  --output "results/06_divergence_tail/profiles/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_BUILD_DIR:?set the completed build directory}/06_divergence_tail" --profile small
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/06_divergence_tail/logs/capture-%J-%t.out" \
  --error="results/06_divergence_tail/logs/capture-%J-%t.err" \
  "${COURSE_CONTAINER_RUNNER:?select the qualified runner}" "${CUDA_IMAGE_DIGEST:?select the qualified image}" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=ncu \
  ncu --target-processes all --nvtx --nvtx-include course_measure/ \
  --kernel-name-base demangled --rename-kernels off \
  --kernel-name "regex:${COURSE_PROFILE_KERNEL:?select the measured kernel from Systems}" \
  --launch-count 1 --set basic --section SpeedOfLight \
  --section MemoryWorkloadAnalysis --section Occupancy --clock-control none \
  --export "results/06_divergence_tail/profiles/ncu-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_BUILD_DIR:?set the completed build directory}/06_divergence_tail" --profile small
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare divergent execution with grouping plus pack/scatter. Independently test threads=128 instead of 256, rebuild, and retain both preprocessing passes in the application decision.

For the source experiment, rebuild with the same image and build directory, then repeat the original run and capture commands:

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/06_divergence_tail/logs/%j.out" \
  --error="$PWD/results/06_divergence_tail/logs/%j.err" --wait slurm/build_and_test.sbatch
export COURSE_BUILD_DIR="${COMPLETED_BUILD_DIRECTORY:?completed build/run-JOB_ID directory}"
```

The publisher compares the declared workload fields and source fingerprint; retain the original artifact and do not change input generation, timed scope, or correctness tolerances.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Incorrect output order indicates a mapping defect, not numerical error. Odd-sized extensions require careful long-count calculation. Do not claim measured grid-tail utilization from the simple one-block-per-SM estimate.

Increasing block size does not repair a skewed work distribution.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Regrouping is useful only when its complete cost is justified. A follow-on experiment can amortize packing over repeated reuse, with the same logical outputs and timing that includes packing and all repeated uses.

Separate lane masks, block duration, and grid coverage before tuning.

Name one distinct fix for each imbalance class.
