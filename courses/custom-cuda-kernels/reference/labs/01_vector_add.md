# Lab 01: Launch and validate a bounds-safe vector kernel

Vector addition is simple enough to expose the essential CUDA program structure without hiding it behind an algorithm. This lab allocates device buffers, transfers inputs, launches a one-dimensional kernel, and checks every output against a CPU reference. You will learn the indexing and error-handling pattern that later optimization labs must preserve as they become more complex.

## Before you start

**Theory preparation:** Read Lessons 2–3 for the build, RAII buffer ownership, global indexing, ceiling division, bounds checks, asynchronous errors, complete-output correctness and event timing. Lesson 1 is a conceptual preview; first run in Lesson 3 and return in Lesson 4 for sanitizer/profiler interpretation.

Complete the SM90 build and preflight, then set `COURSE_BUILD_DIR` to the completed build. Use one H100. The smoke case contains 1,003 elements; the full case contains 2^24 elements. Both use 256 threads per block.

Prefer maintained SM90 paths first. Any `sm_90a` architecture-accelerated feature is opt-in and not forward-compatible in the same way as ordinary compute capability 9.0 code.

Choose block sizes by measured SM90 behavior and resource use, not by always selecting the maximum threads. The simple vector kernel is likely bandwidth- or launch-limited and is a teaching baseline, not an H100 peak demonstration.

Use current toolkit tool support for TMA/cluster paths and record any known limitation. Counter permission is a cluster-owner boundary and must fail clearly rather than be bypassed.

## Concepts and code path

The host owns input/reference vectors and device buffers managed through Resource Acquisition Is Initialization (RAII): C++ objects acquire the buffers and release them automatically when their owning objects are destroyed. Each CUDA thread computes a global index from block and thread IDs, checks it against the element count, and writes one sum. Ceiling division launches enough blocks for the tail. Shared helpers check CUDA errors and time warmed repetitions with events; input/output transfers are outside kernel timing.

Absolute tolerance permits a fixed error near zero; relative tolerance scales the allowed error with the reference magnitude. Here every output must be finite and satisfy `abs(observed - expected) <= atol + rtol * abs(expected)`. For a reference of 2, `atol=1e-6` and `rtol=1e-5` allow an absolute difference up to `2.1e-5`. The complete output check, rather than one sample, determines acceptance.

## Practice

### After Lesson 1

Given GEMM at 30 percent of request time and a separate bias/ReLU epilogue at 5 percent, rewriting GEMM alone targets the 30-percent region; changing GEMM and its epilogue together targets at most the combined 35-percent region while assuming enormous maintenance risk. Change the candidate to a CUTLASS/library fused epilogue that may remove intermediate traffic and a launch, but does not eliminate the epilogue arithmetic. Expected observation: maintained matrix-multiplication machinery is reused and the full request—not only the kernel—decides whether the specialization is worth owning.

Complete the decision worksheet before Lab 01 and again for the capstone.

### After Lesson 3

Given `n=1000` and 256 threads per block, ceiling division launches four blocks or 1,024 threads; 24 fail the bounds predicate. Change to floor division, which launches only three blocks. Expected observation: 232 elements remain unwritten, so a fast timing is rejected. Change input/output pointers to overlap despite `__restrict__` and correctness is no longer guaranteed.

Run Lab 01's supplied full case (2^24 elements) and --smoke case (1003 elements), both with 256 threads per block. Extension: parameterize the element count, skip the launch for an empty input, and test 1, 255, 256, and 257 elements against the CPU reference. Pointer-misalignment tests require deliberately offset allocations with preserved valid bounds; the supplied CLI does not provide them.

### After Lesson 4

The following scenario motivates sanitizer scope. It is not implemented by this vector kernel; revisit it after Lesson 7 with the reduction in Lab 04.

Given a shared-memory reduction missing one barrier, 100 ordinary runs happen to match. Change only scheduling with a different block size and failures appear. Expected observation: racecheck may identify the shared-memory hazard; synccheck checks invalid synchronization usage and is not a general detector for every missing barrier; after repair, Systems locates the region and Compute explains it, while final timing comes from a clean uninstrumented executable.

Start with `sbatch slurm/sanitizer.sbatch memcheck "${COURSE_BUILD_DIR:?set the completed build directory}/01_vector_add" --smoke`, then apply the profiler launchers to the same completed vector lab. Choose one timing or memory question rather than every counter. The allow-listed `racecheck`, `initcheck`, and `synccheck` modes have different scopes; revisit them with the shared-memory labs as those mechanisms are introduced. Return to this validation sequence during the capstone.

### Run the supplied experiment

In Lesson 3, run the partial-block smoke case, confirm its reference check, then run the larger case. These are the first execution and timing exercises.

```bash
umask 077
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/01_vector_add" --smoke
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR}/01_vector_add"
```

After Lesson 4 defines Compute Sanitizer and memory checking, revisit the smoke case with memcheck. Sanitizer timing is diagnostic and must not be used as ordinary benchmark timing.

```bash
umask 077
sbatch slurm/sanitizer.sbatch memcheck "${COURSE_BUILD_DIR:?set the completed build directory}/01_vector_add" --smoke
```

## Check your results

Require the full CPU-reference comparison at FP32 `rtol=1e-5, atol=1e-6` in Lesson 3, and no relevant memcheck errors on the Lesson 4 revisit. Inspect element count, sample count, median, and p90. A successful launch alone does not prove every tail element was written correctly.

Record hotspot share, library/compiler trials, unmet requirement, portability, test burden, and end-to-end target.

The smallest maintained surface that meets the requirement is the preferred design.

Retain shape, grid/block, correctness error, CUDA errors, warm-up, samples, and effective bandwidth.

Correct edge coverage precedes launch tuning.

Retain tool versions, command, exit status, issue summary, selected range/kernel, and minimal relevant counters.

Finding no sanitizer errors supports the tested paths and inputs, not every possible execution.

## Investigate the behavior

Calculate the block count and number of inactive final-block threads for 1,003 elements. Which bytes are read and written per useful element? Explain why transfer-inclusive application time differs from the distribution of kernel execution times.

Higher-level paths may leave a narrow optimization opportunity but carry wider testing and upgrade coverage. Handwritten specialization can remove exact overhead while creating more variants, qualification work, and long-term risk.

Bounds guards support arbitrary sizes but can create a partially active final warp. Removing them is safe only with a proven exact-shape contract. Grid-stride loops improve generality while changing instruction and cache behavior.

Sanitizers and profilers can be extremely slow and perturb scheduling. Focused edge tests reduce cost, but production acceptance still requires representative shapes. More metrics can force more replay passes.

## If something goes wrong

Tail-only mismatches suggest missing bounds checks. Illegal access errors require checking allocation sizes and index arithmetic. Investigate the first CUDA failure instead of allowing later operations to obscure its origin.

Avoid rewriting a famous kernel that contributes little to the real workload.

Checking only launch submission misses asynchronous execution errors.

Profiling every kernel with every counter changes runtime and overwhelms analysis.

## Takeaways and next step

Correct indexing, ownership, and checks precede tuning. An extension can parameterize sizes 1, 255, 256, and 257; empty input must skip the launch. These cases are not existing command-line options.

Reject custom CUDA unless it addresses a material gap that existing paths cannot solve and your team can maintain the implementation.

Name the evidence that would make you stop before writing code.

Check the launch, synchronize at the validation boundary, compare every output to a trusted reference, and treat restricted-pointer promises as part of that operation contract.

Derive grid size and inactive threads for three input lengths.

Validate, sanitize, locate, then inspect one hypothesis-driven kernel.

Put correctness, sanitizer, system timeline, and kernel counters in the correct order.
