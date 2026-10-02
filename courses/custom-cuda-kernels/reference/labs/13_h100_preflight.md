# Lab 13: Build and identify the H100 CUDA execution target

A custom kernel depends on a compiler, target architecture, runtime, and physical device that must agree. This lab establishes the required H100/SM90 execution target before any optimization experiment. You will separate successful source compilation from successful device activation and preserve the exact build identity needed to interpret later correctness, sanitizer, and performance results.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

The CUDA development image must contain Python 3, CMake, NVCC and NVTX3.
Use the reviewed CUTLASS 4.6.1 tree and require Apptainer on both workers.
Set `COURSE_CONTAINER_RUNNER` to the absolute shared `slurm/container_runner.example.sh` path.

Export the cluster owner's reviewed `COURSE_CONTAINER_RUNNER`, immutable `CUDA_IMAGE_DIGEST` and approved `CUTLASS_ROOT`. The runner must be executable. The build job writes a unique `build/run-<build-job-id>` directory; use that completed directory for later commands. Required binaries use CUDA C++20 with `CMAKE_CUDA_ARCHITECTURES=90`.

Compute capability 9.0 is the default. Labs requiring Hopper architecture-specific instructions use an explicit isolated `90a` target and state that the resulting binary is architecture-specific.

## Concepts and code path

Lab 13 reports runtime and driver-supported CUDA API versions. Retain the compiler version separately from the build log; reporting versions is distinct from executing a compiler test.

CMake describes how to configure and build the project. CTest is its test runner: it executes registered tests and reports their outcomes. It cannot supply tests that the project has not defined and does not replace separate sanitizer or profiler runs.

The build launcher configures a unique CMake build directory, builds targets, and runs CTest in the declared environment. Lab 13 calls the shared CUDA device checks and reports H100 properties. `common.cuh` centralizes checked CUDA calls, owned buffers, reference comparisons, and event timing for later labs. Passing preflight does not exercise every kernel or sanitizer.

Build prerequisite (complete before the baseline):

```bash
export COURSE='custom-cuda-kernels'
bash slurm/build_and_test.sbatch --help
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/13_h100_preflight/logs/%j.out" \
  --error="$PWD/results/13_h100_preflight/logs/%j.err" --wait slurm/build_and_test.sbatch
```

Keep the exact completed build directory as `COURSE_BUILD_DIR`; use the course README for the optional SM90a build and `COURSE_CLUSTER_BUILD_DIR`.

## Practice

`labs/13_h100_preflight.cu` verifies the allocated GPU satisfies the course device contract and reports compute capability, SM count, memory, CUDA runtime, and driver API versions. It does not benchmark a kernel. The baseline launcher records its output as course result JSON.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 \
  --chdir="$PWD" \
  --output="$PWD/results/13_h100_preflight/logs/%j.out" \
  --error="$PWD/results/13_h100_preflight/logs/%j.err" \
  slurm/single_gpu.sbatch \
  "${COURSE_BUILD_DIR:?set the completed build directory}/13_h100_preflight"
```

## Check your results

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/13_h100_preflight/logs/$LAB_JOB_ID.out"
cat "results/13_h100_preflight/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require successful compiler/build/CTest records and exactly one visible full non-MIG H100 with compute capability 9.0. Record the image and source identities privately. Compiler availability alone does not prove device execution.

Retain compiler/toolkit, driver, architecture flags, H100 properties, build options, and exit status.

A successful local parse is not CUDA compilation; a successful build is not H100 runtime proof.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Sm count | `sm_count` | `none` |
| Global memory bytes | `global_memory_bytes` | `bytes` |
| Runtime version | `runtime_version` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same profile. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 13_h100_preflight \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Submit the build first and wait for success. Read its `.out` log, then set
`COURSE_BUILD_DIR` to the printed absolute build directory before submitting
preflight; do not point it at an unrelated or stale build.

Continue only after the build job succeeds. Read its log and replace the path below
with that job's completed directory. Save the CUDA selectors for later logins:

```bash
export COURSE_BUILD_DIR='<absolute completed build directory from the job log>'
declare -p CUDA_IMAGE_DIGEST CUTLASS_ROOT COURSE_CONTAINER_RUNNER COURSE_BUILD_DIR \
  >> "$HOME/courses/.runtime/$COURSE.sh"
sbatch --export=ALL,COURSE_PROFILE_TOOL=none,COURSE_CAPTURE=0 --chdir="$PWD" \
  --output="$PWD/results/13_h100_preflight/logs/%j.out" \
  --error="$PWD/results/13_h100_preflight/logs/%j.err" slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/13_h100_preflight"
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Explain which component compiles CUDA C++ and which launches the resulting binary. Why is the required SM90 build distinct from the optional SM90a profile? Identify what must be requalified after a toolkit change.

Embedding SASS fixes code generation and avoids JIT; embedding PTX adds driver-JIT flexibility but startup and driver dependence. Debug line information improves tools while changing build cost and potentially optimization.

**Nsight Systems: not applicable.** This device-attribute preflight establishes hardware capability, not kernel performance; use the reported attributes and readiness checks. Inspect the measured or modeled fields in this lab's dashboard; retain the artifact and its stated scope.

## If something goes wrong

A wrong device, missing compiler, incompatible library source, or failed test blocks its dependent labs. Do not alter drivers or silently change the target architecture to bypass the stated platform contract.

Using `native` hides the architecture contract and creates unreproducible binaries.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Reproducible kernel work begins with an explicit toolchain and execution target. Proceed to vector addition, then collect sanitizer and profiler evidence separately; no preflight result implies an optimization speedup.

Target SM90 explicitly and fail closed when the full non-MIG H100 contract is not met.

Explain when `sm_90a` is allowed and why its binary boundary matters.
