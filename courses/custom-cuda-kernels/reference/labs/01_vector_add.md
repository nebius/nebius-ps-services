# Lab 01: Launch and validate a bounds-safe vector kernel

Vector addition is simple enough to expose the essential CUDA program structure without hiding it behind an algorithm. This lab allocates device buffers, transfers inputs, launches a one-dimensional kernel, and checks every output against a CPU reference. You will learn the indexing and error-handling pattern that later optimization labs must preserve as they become more complex.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Complete the SM90 build and preflight, then set `COURSE_BUILD_DIR` to the completed build. Use one H100. The small case contains 1,003 elements; the full case contains 2^24 elements. Both use 256 threads per block.

Prefer maintained SM90 paths first. Any `sm_90a` architecture-accelerated feature is opt-in and not forward-compatible in the same way as ordinary compute capability 9.0 code.

Choose block sizes by measured SM90 behavior and resource use, not by always selecting the maximum threads. The simple vector kernel is likely bandwidth- or launch-limited and is a teaching baseline, not an H100 peak demonstration.

Use current toolkit tool support for TMA/cluster paths and record any known limitation. Counter permission is a cluster-owner boundary and must fail clearly rather than be bypassed.

## Concepts and code path

The host owns input/reference vectors and device buffers managed through Resource Acquisition Is Initialization (RAII): C++ objects acquire the buffers and release them automatically when their owning objects are destroyed. Each CUDA thread computes a global index from block and thread IDs, checks it against the element count, and writes one sum. Ceiling division launches enough blocks for the tail. Shared helpers check CUDA errors and time warmed repetitions with events; input/output transfers are outside kernel timing.

Absolute tolerance permits a fixed error near zero; relative tolerance scales the allowed error with the reference magnitude. Here every output must be finite and satisfy `abs(observed - expected) <= atol + rtol * abs(expected)`. For a reference of 2, `atol=1e-6` and `rtol=1e-5` allow an absolute difference up to `2.1e-5`. The complete output check, rather than one sample, determines acceptance.

Given GEMM at 30 percent of request time and a separate bias/ReLU epilogue at 5 percent, rewriting GEMM alone targets the 30-percent region; changing GEMM and its epilogue together targets at most the combined 35-percent region while assuming enormous maintenance risk. Change the candidate to a CUTLASS/library fused epilogue that may remove intermediate traffic and a launch, but does not eliminate the epilogue arithmetic. Expected observation: maintained matrix-multiplication machinery is reused and the full request—not only the kernel—decides whether the specialization is worth owning.

Before choosing custom code, record the measured hotspot, the maintained alternatives and the acceptance criterion.

Given `n=1000` and 256 threads per block, ceiling division launches four blocks or 1,024 threads; 24 fail the bounds predicate. Change to floor division, which launches only three blocks. Expected observation: 232 elements remain unwritten, so a fast timing is rejected. Change input/output pointers to overlap despite `__restrict__` and correctness is no longer guaranteed.

The following shared-memory reduction scenario explains sanitizer scope; it is not implemented by the vector-addition kernel.

Given a shared-memory reduction missing one barrier, 100 ordinary runs happen to match. Change only scheduling with a different block size and failures appear. Expected observation: racecheck may identify the shared-memory hazard; synccheck checks invalid synchronization usage and is not a general detector for every missing barrier; after repair, Systems locates the region and Compute explains it, while final timing comes from a clean uninstrumented executable.

Start with:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_vector_add/logs/%j.out" \
  --error="$PWD/results/01_vector_add/logs/%j.err" slurm/01_vector_add.sanitizer.sbatch memcheck --workload small
```

Then apply the native profiler commands to the same completed vector lab. Choose one timing or memory question rather than every counter. The allow-listed `racecheck`, `initcheck`, and `synccheck` modes have different scopes; revisit them with the shared-memory labs as those mechanisms are introduced. Return to this validation sequence during the capstone.

## Practice

`labs/01_vector_add.cu` adds two vectors with a bounds-checked CUDA kernel, verifies every result against a CPU reference, and reports launch geometry and timing for the selected block size. The baseline launcher records its output as course result JSON.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_vector_add/logs/%j.out" \
  --error="$PWD/results/01_vector_add/logs/%j.err" \
  slurm/01_vector_add.sbatch --workload small
```

## Check your results

Each new job owns `results/01_vector_add/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/01_vector_add/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/01_vector_add/logs/$LAB_JOB_ID.out"
cat "results/01_vector_add/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require the full CPU-reference comparison at FP32 `rtol=1e-5, atol=1e-6`, and no relevant memcheck errors. Inspect element count, sample count, median, and p90. A successful launch alone does not prove every tail element was written correctly.

Record hotspot share, library/compiler trials, unmet requirement, portability, test burden, and end-to-end target.

The smallest maintained surface that meets the requirement is the preferred design.

Retain shape, grid/block, correctness error, CUDA errors, warm-up, samples, and effective bandwidth.

Correct edge coverage precedes launch tuning.

Retain tool versions, command, exit status, issue summary, selected range/kernel, and minimal relevant counters.

Finding no sanitizer errors supports the tested paths and inputs, not every possible execution.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Kernel median (seconds) | `kernel_median_ms` | `s` |
| Blocks | `blocks` | `none` |
| Threads per block | `threads_per_block` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 01_vector_add \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run the 1,003-element small case, confirm every output, then run the larger case. Calculate how many threads are inactive in the final block.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_vector_add/logs/%j.out" \
  --error="$PWD/results/01_vector_add/logs/%j.err" slurm/01_vector_add.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_vector_add/logs/%j.out" \
  --error="$PWD/results/01_vector_add/logs/%j.err" slurm/01_vector_add.sbatch
```

Run memcheck to detect invalid memory accesses. Its instrumented timing is diagnostic and must not be used as ordinary benchmark timing.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_vector_add/logs/%j.out" \
  --error="$PWD/results/01_vector_add/logs/%j.err" slurm/01_vector_add.sanitizer.sbatch memcheck --workload small
```

For the guided candidate, keep the same small elements:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_vector_add/logs/%j.out" \
  --error="$PWD/results/01_vector_add/logs/%j.err" slurm/01_vector_add.sbatch --workload small --threads 128
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Calculate the block count and number of inactive final-block threads for 1,003 elements. Which bytes are read and written per useful element? Explain why transfer-inclusive application time differs from the distribution of kernel execution times.

Higher-level paths may leave a narrow optimization opportunity but carry wider testing and upgrade coverage. Handwritten specialization can remove exact overhead while creating more variants, qualification work, and long-term risk.

Bounds guards support arbitrary sizes but can create a partially active final warp. Removing them is safe only with a proven exact-shape contract. Grid-stride loops improve generality while changing instruction and cache behavior.

Sanitizers and profilers can be extremely slow and perturb scheduling. Focused edge tests reduce cost, but production acceptance still requires representative shapes. More metrics can force more replay passes.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_vector_add/logs/%j.out" \
  --error="$PWD/results/01_vector_add/logs/%j.err" slurm/01_vector_add.nsys.sbatch --workload small
```

The native Systems command is in `slurm/01_vector_add.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/01_vector_add/logs/%j.out" \
  --error="$PWD/results/01_vector_add/logs/%j.err" slurm/01_vector_add.ncu.sbatch --workload small
```

The native Compute command is in `slurm/01_vector_add.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare 256 with 128 threads per block using the same 1,003 small elements. Independently test 512 threads and explain the final-block waste and Compute occupancy; keep the fastest correct measured configuration.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Tail-only mismatches suggest missing bounds checks. Illegal access errors require checking allocation sizes and index arithmetic. Investigate the first CUDA failure instead of allowing later operations to obscure its origin.

Avoid rewriting a famous kernel that contributes little to the real workload.

Checking only launch submission misses asynchronous execution errors.

Profiling every kernel with every counter changes runtime and overwhelms analysis.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Correct indexing, ownership, and checks precede tuning. An extension can parameterize sizes 1, 255, 256, and 257; empty input must skip the launch. These cases are not existing command-line options.

Reject custom CUDA unless it addresses a material gap that existing paths cannot solve and your team can maintain the implementation.

Name the evidence that would make you stop before writing code.

Check the launch, synchronize at the validation boundary, compare every output to a trusted reference, and treat restricted-pointer promises as part of that operation contract.

Derive grid size and inactive threads for three input lengths.

Validate, sanitize, locate, then inspect one hypothesis-driven kernel.

Put correctness, sanitizer, system timeline, and kernel counters in the correct order.

Run Lab 01's supplied full case (2^24 elements) and --workload small case (1003 elements), both with 256 threads per block. Extension: parameterize the element count, skip the launch for an empty input, and test 1, 255, 256, and 257 elements against the CPU reference. Pointer-misalignment tests require deliberately offset allocations with preserved valid bounds; the supplied CLI does not provide them.
