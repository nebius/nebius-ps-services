# Lab 00: Build and identify the H100 CUDA execution target

A custom kernel depends on a compiler, target architecture, runtime, and physical device that must agree. This lab establishes the required H100/SM90 execution target before any optimization experiment. You will separate successful source compilation from successful device activation and preserve the exact build identity needed to interpret later correctness, sanitizer, and performance results.

## Before you start

**Theory preparation:** Read Lessons 1–2 for host/device execution, toolchain stages and architecture targets. Follow the image, build and allocation requirements below before running.

Follow the [CUDA build runbook](../cluster-smoke-test.md). The cluster owner supplies a reviewed container runner, immutable CUDA development image, and approved CUTLASS source. Required binaries use CUDA C++20 with `CMAKE_CUDA_ARCHITECTURES=90`.

Compute capability 9.0 is the default. Labs requiring Hopper architecture-specific instructions use an explicit isolated `90a` target and state that the resulting binary is architecture-specific.

## Concepts and code path

Lab 00 reports runtime and driver-supported CUDA API versions. Retain the compiler version separately from the build log; reporting versions is distinct from executing a compiler test.

CMake describes how to configure and build the project. CTest is its test runner: it executes registered tests and reports their outcomes. It cannot supply tests that the project has not defined and does not replace separate sanitizer or profiler runs.

The build launcher configures a unique CMake build directory, builds targets, and runs CTest in the declared environment. Lab 00 calls the shared CUDA device checks and reports H100 properties. `common.cuh` centralizes checked CUDA calls, owned buffers, reference comparisons, and event timing for later labs. Passing preflight does not exercise every kernel or sanitizer.

## Practice

For a planning exercise, consider the optional cluster probe below. Revisit its implementation only after Lesson 15; it is not required for this first build.

Given a build configured for architecture 90 on an H100, the ordinary vector and fusion labs should load. Change the build configuration to enable the optional Lab 10 cluster-launch probe in its separate SM90a target. This isolation follows the course packaging policy; the probe does not demonstrate an architecture-accelerated instruction. Expected observation: Lab 10 is built only when explicitly enabled and is kept separate from the required SM90 binaries.

Configure CMake, build, run Lab 00 through Slurm, and inspect resource output.

Submit the build first and wait for its success. Then set `COURSE_BUILD_DIR` to that job's actual completed directory before submitting preflight; do not point it at an unrelated or stale build.

```bash
umask 077
bash slurm/build_and_test.sbatch --help
sbatch slurm/build_and_test.sbatch
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/00_h100_preflight"
```

## Check your results

Require successful compiler/build/CTest records and exactly one visible full non-MIG H100 with compute capability 9.0. Record the image and source identities privately. Compiler availability alone does not prove device execution.

Retain compiler/toolkit, driver, architecture flags, H100 properties, build options, and exit status.

A successful local parse is not CUDA compilation; a successful build is not H100 runtime proof.

## Investigate the behavior

Explain which component compiles CUDA C++ and which launches the resulting binary. Why is the required SM90 build distinct from the optional SM90a profile? Identify what must be requalified after a toolkit change.

Embedding SASS fixes code generation and avoids JIT; embedding PTX adds driver-JIT flexibility but startup and driver dependence. Debug line information improves tools while changing build cost and potentially optimization.

## If something goes wrong

A wrong device, missing compiler, incompatible library source, or failed test blocks its dependent labs. Do not alter drivers or silently change the target architecture to bypass the stated platform contract.

Using `native` hides the architecture contract and creates unreproducible binaries.

## Takeaways and next step

Reproducible kernel work begins with an explicit toolchain and execution target. Proceed to vector addition, then collect sanitizer and profiler evidence separately; no preflight result implies an optimization speedup.

Target SM90 explicitly and fail closed when the full non-MIG H100 contract is not met.

Explain when `sm_90a` is allowed and why its binary boundary matters.
