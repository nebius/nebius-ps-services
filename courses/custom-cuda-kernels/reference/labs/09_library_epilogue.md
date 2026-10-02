# Lab 09: Compare a library GEMM with a fused epilogue

The matrix multiplication itself may already be well served by a library, while a following bias and activation add extra work. This lab compares cuBLAS plus a separate bias/ReLU kernel with a source-pinned CUTLASS epilogue path. You will understand the actual implementation choice and its costs before interpreting fusion as a guarantee of superior GEMM performance.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Build with the required reviewed CUTLASS 4.6.1 source and `COURSE_ENABLE_CUTLASS=ON`. Use the declared SM90 image and one H100. Missing CUTLASS is a blocked lab, not an accepted cuBLAS-only substitute.

Hopper Tensor Core instructions used by CUTLASS may require `sm_90a`; the course isolates and documents that architecture-specific build. Do not claim forward compatibility for that binary.

## Concepts and code path

The host builds a CPU reference that exercises positive and clamped ReLU outputs. cuBLAS computes row-major GEMM through the equivalent column-major call, followed by a bias/ReLU kernel. The CUTLASS path uses `OpClassSimt` and an `Sm80` template specialization compiled for the course target. It is FP32 SIMT, not a Hopper Tensor Core collective-builder example. The cuBLAS baseline pins pedantic math mode. It expands the bias into a full source matrix and applies `LinearCombinationRelu` with both alpha and beta set to one; expansion occurs outside timing.

Given an FP32 M×N intermediate, it costs 4MN logical bytes to write and another 4MN to read before the separate epilogue writes the final output. Change to a fused epilogue. Expected observation: it may remove that intermediate round trip and a launch. However, Lab 09 also changes the GEMM implementation and reads an expanded bias matrix, so its time difference cannot be attributed only to epilogue placement or called a Tensor Core speedup. Report the full paths and include bias expansion/storage when evaluating integration cost. A tightly controlled epilogue-only extension must hold the underlying GEMM algorithm constant where supported.

Build the required source-pinned CUTLASS path, enabled by default with COURSE_ENABLE_CUTLASS=ON, and run Lab 09's cuBLAS-plus-epilogue and CUTLASS SIMT cases. Check both against the supplied reference. The program offers small and full shapes, not an arbitrary-shape CLI; edge-shape coverage is an explicit source-edit/rebuild extension. Leave timing claims pending until the pinned source and CUDA image pass on H100.

## Practice

`labs/09_library_epilogue.cu` compares cuBLAS plus separate bias/ReLU with a pinned CUTLASS fused epilogue. It checks both against a CPU reference exercising both ReLU branches and reports timings; the CUTLASS path is FP32 SIMT. The baseline launcher records its output as course result JSON.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 \
  --chdir="$PWD" \
  --output="$PWD/results/09_library_epilogue/logs/%j.out" \
  --error="$PWD/results/09_library_epilogue/logs/%j.err" \
  slurm/single_gpu.sbatch \
  "${COURSE_BUILD_DIR:?set the completed build directory}/09_library_epilogue" --profile small
```

## Check your results

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/09_library_epilogue/logs/$LAB_JOB_ID.out"
cat "results/09_library_epilogue/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require both CPU-reference checks and both ReLU branches in the fixture. Inspect cuBLAS-plus-separate and CUTLASS-fused distributions, dimensions, version, and comparison scope. This comparison changes GEMM implementation as well as epilogue placement.

Retain library versions, algorithm/kernel, shapes, launches, time, memory traffic, and numerical error.

Prefer the library path unless a qualified fused path improves the real application.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Cublas plus separate bias relu median (seconds) | `cublas_plus_separate_bias_relu_median_ms` | `s` |
| Cutlass fused bias relu median (seconds) | `cutlass_fused_bias_relu_median_ms` | `s` |
| Rows | `rows` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 09_library_epilogue \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run both required paths and a memory check. The supplied GEMM dimensions `(M, N, K)` are `(128, 192, 96)` for small and `(1024, 1536, 768)` for full; arbitrary dimensions require a source edit and rebuild.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/09_library_epilogue/logs/%j.out" \
  --error="$PWD/results/09_library_epilogue/logs/%j.err" slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/09_library_epilogue" --profile small
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/09_library_epilogue/logs/%j.out" \
  --error="$PWD/results/09_library_epilogue/logs/%j.err" slurm/sanitizer.sbatch memcheck "${COURSE_BUILD_DIR}/09_library_epilogue" --profile small
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Count the extra output pass in the cuBLAS path and the expanded-bias storage in CUTLASS. Why can a fused SIMT implementation lose to a faster library GEMM? Which initialization/copy costs would matter for a one-shot application?

CUTLASS offers composition and source control but creates template/build complexity and qualification work. A separate epilogue is simpler and covers more shapes, while fusion can save launches and intermediate traffic when no other operation needs the intermediate.

Capture a separate diagnostic run:

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/09_library_epilogue/logs/capture-%J-%t.out" \
  --error="results/09_library_epilogue/logs/capture-%J-%t.err" \
  "${COURSE_CONTAINER_RUNNER:?select the qualified runner}" "${CUDA_IMAGE_DIGEST:?select the qualified image}" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=nsys \
  nsys profile --trace=cuda,nvtx,osrt \
  --cuda-trace-scope=process-tree --sample=none --cpuctxsw=none \
  --discard-environment=true --force-overwrite=false \
  --duration=300 --kill=none --wait=all \
  --output "results/09_library_epilogue/profiles/nsys-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_BUILD_DIR:?set the completed build directory}/09_library_epilogue" --profile small
```

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
srun --nodes=1 --ntasks=1 --gpus-per-task=1 --cpus-per-task=8 --time=00:15:00 --kill-on-bad-exit=1 \
  --chdir="$PWD" --output="results/09_library_epilogue/logs/capture-%J-%t.out" \
  --error="results/09_library_epilogue/logs/capture-%J-%t.err" \
  "${COURSE_CONTAINER_RUNNER:?select the qualified runner}" "${CUDA_IMAGE_DIGEST:?select the qualified image}" \
  env -u DEBUGINFOD_URLS COURSE_CAPTURE=1 COURSE_PROFILE_TOOL=ncu \
  ncu --target-processes all --nvtx --nvtx-include course_measure/ \
  --kernel-name-base demangled --rename-kernels off \
  --kernel-name "regex:${COURSE_PROFILE_KERNEL:?select the measured kernel from Systems}" \
  --launch-count 1 --set basic --section SpeedOfLight \
  --section MemoryWorkloadAnalysis --section Occupancy --clock-control none \
  --export "results/09_library_epilogue/profiles/ncu-%q{SLURM_JOB_ID}-%q{SLURM_STEP_ID}-%q{SLURM_PROCID}-%p" \
  "${COURSE_BUILD_DIR:?set the completed build directory}/09_library_epilogue" --profile small
```

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare the library GEMM plus epilogue with the available fused path at fixed GEMM dimensions. Independently change the separate epilogue threads from 256 to 128, rebuild, and determine whether the GEMM still dominates.

For the source experiment, rebuild with the same image and build directory, then repeat the original run and capture commands:

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/09_library_epilogue/logs/%j.out" \
  --error="$PWD/results/09_library_epilogue/logs/%j.err" --wait slurm/build_and_test.sbatch
export COURSE_BUILD_DIR="${COMPLETED_BUILD_DIRECTORY:?completed build/run-JOB_ID directory}"
```

The publisher compares the declared workload fields and source fingerprint; retain the original artifact and do not change input generation, timed scope, or correctness tolerances.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Library layout or leading-dimension mistakes often produce structured output errors. A CUTLASS `can_implement` failure must be resolved within the pinned configuration. Do not bypass the reference or version gate to report a timing.

Avoid comparing a cold autotuning library call with a warmed custom candidate.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Preserve strong GEMM implementations when customizing surrounding work. A further Tensor Core or broadcast-bias implementation is a new candidate requiring its own supported configuration, full correctness, and application-boundary comparison.

Warm both paths, use repeated samples, verify dispatch, and include integration overhead. The included FP32 SIMT path teaches epilogue ownership; it is not a Tensor Core performance claim.

Explain what the epilogue owns and what the GEMM library continues to own.
