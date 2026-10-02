# Lab 10: Survey library behavior across shapes and dtypes

Matrix libraries select implementations partly from shape and precision, so nearby dimensions can produce noticeably different performance. This lab surveys matrix widths and dtypes while checking each result against its corresponding reference. You will distinguish an exploratory survey from a causal optimization that preserves one original problem and its useful output.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use one H100 and the approved environment. Review GEMM FLOP accounting and dtype tolerances. Each case creates its own operands; outputs at different widths do not share a single reference tensor.

H100 Tensor Cores support several precisions, but the actual path depends on operation, library, shape, and policy. Verify the chosen kernel on the pinned stack rather than relying on a rule from another release.

## Concepts and code path

The host iterates over the predefined shape/precision cases, creates matching operands, computes an FP32 reference, and times the selected matmul. Results include width, dtype, effective TFLOP/s, and numerical acceptability. The lab does not transpose inputs, test arbitrary strides, or implement a padding/cropping optimization.

Given widths 4,095 and 4,096 with the same useful tokens, the larger width may select a more efficient kernel. Change only the padding policy and report useful-token throughput plus padded work. Expected observation: raw kernel time may improve while end-to-end memory or extra computation cancels it; dispatch evidence explains rather than guarantees the result.

## Practice

`labs/10_shape_precision.py` benchmarks matrix multiplication across selected shapes and precision modes. It checks finite results against the declared numerical gate and writes per-case timing, throughput, and error evidence.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 \
  --chdir="$PWD" \
  --output="$PWD/results/10_shape_precision/logs/%j.out" \
  --error="$PWD/results/10_shape_precision/logs/%j.err" \
  slurm/single_gpu.sbatch \
  labs/10_shape_precision.py --profile small
```

## Check your results

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/10_shape_precision/logs/$LAB_JOB_ID.out"
cat "results/10_shape_precision/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require finite and numerically acceptable results for all cases. Compare each `cases` entry's timing, error, width, and dtype. Inspect the implemented numerical gate rather than assuming every precision uses identical error criteria.

For the supplied survey retain each shape, dtype, selected precision policy, reference error, and timing. For the fixed-work padding extension additionally retain original/padded K, identical output shape, padding cost, useful FLOPs, and end-to-end time.

Accept padding only when the end-to-end gain exceeds extra arithmetic and capacity cost.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Cases / case / timing / median (seconds) | `cases.*.timing.median_ms` | `s` |
| Cases / case / effective tflops | `cases.*.effective_tflops` | `FLOPS` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 10_shape_precision \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run the full supplied survey for the selected profile. Keep every case's shape and dtype beside its timing so a faster but mathematically different workload is not presented as a replacement.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/10_shape_precision/logs/%j.out" \
  --error="$PWD/results/10_shape_precision/logs/%j.err" slurm/single_gpu.sbatch labs/10_shape_precision.py --profile small
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/10_shape_precision/logs/%j.out" \
  --error="$PWD/results/10_shape_precision/logs/%j.err" slurm/single_gpu.sbatch labs/10_shape_precision.py --profile large
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Which changes alter the actual FLOP count? Which change precision? Explain why a higher TFLOP/s value can coexist with a longer elapsed time if more work is performed.

Padding increases arithmetic, activation/KV memory, and possibly communication. Lower precision can improve capacity and throughput while increasing error or conversion overhead. A friendlier shape is kept only when normalized end-to-end work improves.

Capture a separate diagnostic run:

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/10_shape_precision/logs/capture-%J-%t.out" \
  --error="results/10_shape_precision/logs/capture-%J-%t.err" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=nsys \
  nsys profile --trace=cuda,nvtx,osrt \
  --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none \
  --discard-environment=true --force-overwrite=false \
  --duration=300 --kill=none --wait=all \
  --output "results/10_shape_precision/profiles/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/10_shape_precision.py --profile small
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. The launcher selects one matching kernel inside `course_measure`, the configured NVTX range for this lab. Its launch-count limit applies after the range and kernel-name filters. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/10_shape_precision/logs/capture-%J-%t.out" \
  --error="results/10_shape_precision/logs/capture-%J-%t.err" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=ncu \
  ncu --target-processes all --nvtx --nvtx-include course_measure/ \
  --kernel-name-base demangled --rename-kernels off \
  --kernel-name "regex:${COURSE_PROFILE_KERNEL:?select the measured kernel from Systems}" \
  --launch-count 1 --set basic --section SpeedOfLight \
  --section MemoryWorkloadAnalysis --section Occupancy --clock-control none \
  --export "results/10_shape_precision/profiles/ncu-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_PYTHON:?source the course runtime}" labs/10_shape_precision.py --profile small
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare aligned and misaligned BF16 cases separately from the FP32 case. Independently compute useful FLOPs before choosing a shape or precision; changed work and error tolerance must accompany any timing claim.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

An out-of-memory case is a capacity result, not zero throughput. A numerical failure disqualifies that precision for the declared gate. Do not relax the tolerance merely to retain the faster row.

Avoid reporting a fast microkernel that increases total padded tokens in the application.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Use the fixed-work extension in Practice to decide whether a friendlier shape benefits the original application after padding costs.

Optimize the real shape distribution, not a single ideal matrix.

Explain how padding can simultaneously improve kernel efficiency and reduce system throughput.

Run Lab 10 as a shape-and-dtype survey. Each independently generated matrix pair has its own corresponding FP32 reference; outputs at different widths cannot share one reference tensor. Extension: freeze matrices A[M,K] and B[K,N], zero-pad only K to K', multiply, and compare the unchanged [M,N] output with the original product. Include padding/copy costs and extra FLOPs in the useful-work comparison.
