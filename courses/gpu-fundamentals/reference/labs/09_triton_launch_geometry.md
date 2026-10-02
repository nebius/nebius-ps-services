# Lab 09: Sweep logical work per Triton program

Launch geometry determines how a problem is divided among GPU programs and how much work remains in the final partial program. This Python/Triton lab sweeps logical elements per program while holding four warps per program fixed. You will connect masks and launch counts to correctness without confusing logical tile size with CUDA threads per block.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Use one H100 with the course-qualified Triton package. This is Python learner code, distinct from the later CUDA C++ course. Allow first-use compilation to complete before interpreting warmed measurements.

Exact enabled GPC, SM, and memory-controller counts vary by H100 product and configuration. Course diagrams show containment and data paths, not an exact die floorplan. Measure the actual device rather than hard-coding one SKU count.

Use the CUDA occupancy APIs with the H100 kernel and device limits and actual compiler resource report rather than a generic calculator with another architecture’s limits. Cluster kernels have an additional cluster-occupancy calculation.

## Concepts and code path

In this kernel, `BLOCK_SIZE` selects logical elements per program and `num_warps=4` requests four cooperating warps (128 threads). One thread can handle several elements, so logical block size is not CUDA thread count. The JIT compiler specializes the kernel before warmed timing. Vary logical elements while keeping the four-warp setting fixed.

Ceiling division rounds a quotient upward so the launch covers every element. For 1,003 elements and 256 logical elements per program, round 1003/256 upward to four programs. The last program considers indices 768 through 1023; a bounds mask permits loads and stores only for indices below 1,003, leaving 21 invalid positions masked out. This is a memory-validity rule, not by itself a measurement of branch divergence. These illustrative dimensions explain the calculation; each supplied profile reports its own dimensions.

The kernel computes global element indices from a program ID and a logical offset range, loads valid elements under a mask, performs the expression, and stores valid outputs. The host sweeps supported block sizes and uses ceiling division for program counts. More logical elements can be handled by the same 128 CUDA threads through multiple values per thread.

Given 120 available SMs and 200 equal-duration blocks with one resident block per SM, scheduling needs two waves: 120 blocks, then 80. Change the grid to 121 blocks. Expected observation: the second wave contains one block and the kernel can approach two block durations even though average utilization looks high during the first wave.

Given 320 threads per block, 65,536 32-bit registers per SM, and no tighter shared-memory, thread, or block limit. At 128 registers per thread, a block needs 40,960 registers, permitting one block by this resource bound. Change to 96 registers per thread: it needs 30,720, permitting two before allocation-granularity constraints. Expected observation: check the actual compiled kernel with occupancy APIs; these estimates alone do not establish residency. A register cap can add spills and make the two-block case slower.

This sweep changes logical elements per Triton program with four warps per program; it is not a CUDA threads-per-block sweep. For an optional resource investigation, inspect compiled-kernel register/shared-memory reports and a selected Nsight Compute launch/occupancy report for each case. The supplied script does not emit those measurements, so its timing alone cannot establish occupancy.

## Practice

`labs/09_triton_launch_geometry.py` sweeps three block sizes for a bounds-masked Triton scaling kernel. It checks exact agreement with PyTorch and reports program counts, masked tails, timing, and logical bandwidth.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/09_triton_launch_geometry/logs/%j.out" \
  --error="$PWD/results/09_triton_launch_geometry/logs/%j.err" \
  slurm/09_triton_launch_geometry.sbatch --workload small
```

## Check your results

Each new job owns `results/09_triton_launch_geometry/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/09_triton_launch_geometry/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/09_triton_launch_geometry/logs/$LAB_JOB_ID.out"
cat "results/09_triton_launch_geometry/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require the per-case correctness checks. Inspect `programs`, `elements_per_program`, `masked_tail_elements`, `warps_per_program`, and timing distributions. Reported logical GiB/s is a byte-accounting rate, not measured memory transactions.

Record grid size, block/program size, num_warps and elapsed time. Keep resource output if available, but defer interpretation of registers, shared memory and achieved occupancy until Lessons 4 and 6.

Occupancy describes resident work; it is a latency-hiding input, not a direct performance score.

For the resource/profiler extension, record registers per thread, shared memory per block, blocks and warps per SM, spill traffic, and time.

Enough occupancy hides the relevant latency; more occupancy has no value if the limiting pipeline is already saturated.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Sweeps / 128 / time / median (seconds) | `sweeps.128.time.median_ms` | `s` |
| Sweeps / 256 / time / median (seconds) | `sweeps.256.time.median_ms` | `s` |
| Sweeps / 512 / time / median (seconds) | `sweeps.512.time.median_ms` | `s` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 09_triton_launch_geometry \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

The supplied sweep includes its own launch choices and reference checks. Run both profiles separately to see how problem size changes program count and masked tail elements.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/09_triton_launch_geometry/logs/%j.out" \
  --error="$PWD/results/09_triton_launch_geometry/logs/%j.err" slurm/09_triton_launch_geometry.sbatch --workload small
sbatch --chdir="$PWD" \
  --output="$PWD/results/09_triton_launch_geometry/logs/%j.out" \
  --error="$PWD/results/09_triton_launch_geometry/logs/%j.err" slurm/09_triton_launch_geometry.sbatch --workload large
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Calculate the program count and final mask for one size by hand. Explain why fewer programs need not mean faster execution: larger logical tiles can change registers, scheduling, and per-program work.

More threads or warps per block can expose more work but consume more registers and shared memory per block. More blocks improve wave count only until per-SM resources, dependency stalls, or another pipeline becomes limiting.

Larger tiles increase reuse or instruction-level parallelism but consume registers and shared memory. Smaller blocks can raise residency yet reduce coalescing or reuse. The best point is the one that removes the measured latency without creating a larger cost.

Capture a separate diagnostic run:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/09_triton_launch_geometry/logs/%j.out" \
  --error="$PWD/results/09_triton_launch_geometry/logs/%j.err" slurm/09_triton_launch_geometry.nsys.sbatch --workload small
```

The native Systems command is in `slurm/09_triton_launch_geometry.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open the printed `.nsys-rep` in Systems. Expand NVTX and CUDA rows, select `course_measure`, then inspect CUDA API calls, copies, kernel launches, and idle gaps within that interval. Follow a launch to GPU execution before attributing a CPU range to device work.

For one kernel, use the same fixed workload in a separate Compute capture. In Systems, identify a kernel that performs the operation this lab investigates. Set `COURSE_PROFILE_KERNEL` to a regular expression matching that kernel and repeat the Compute capture. Verify the selected kernel and NVTX range before interpreting its counters; initialization-only evidence does not explain the lab's measured work.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/09_triton_launch_geometry/logs/%j.out" \
  --error="$PWD/results/09_triton_launch_geometry/logs/%j.err" slurm/09_triton_launch_geometry.ncu.sbatch --workload small
```

The native Compute command is in `slurm/09_triton_launch_geometry.ncu.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

Open `.ncu-rep` → **Details → Speed Of Light**, **Memory Workload Analysis**, and **Occupancy**. Record kernel duration, memory throughput/traffic, and the limiting resource. Counters are diagnostic evidence; replay duration is not end-to-end application latency. Annotate a smaller phase with `annotated_operation(operation, "phase_name")` in Python, or `CaptureRange region("phase_name")` around a CUDA launch, then select `--nvtx-include phase_name/` in the native Compute command. Keep annotations opt-in and outside clean timing paths.

Guided comparison: Compare the existing logical tile sizes at fixed elements. Independently choose the winning tile from duration and tail-mask evidence, then check whether its occupancy explains the choice.

**Nsight Systems evidence:** Capture the executable inside the Slurm GPU worker/container; submission and result publication remain outside capture. Open the worker .nsys-rep. Expand NVTX, CUDA API and CUDA GPU rows; locate course_measure and follow host submissions into the GPU streams. Inspect launch gaps, kernels and copies relevant to this lab, then test its named tuning control with another unprofiled run. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

A tail-only mismatch points first to the load/store masks or index formula. A compiler/import failure blocks this Triton exercise, not every PyTorch lab. Record it as an environment issue without inventing substitute results.

Maximizing threads per block can increase register pressure or reduce blocks resident per SM.

Avoid treating theoretical occupancy as a target independent of instruction-level parallelism or memory behavior.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Geometry tuning must preserve indexing and useful work. Register usage and achieved occupancy are not emitted by this lab; obtain compiler and profiler evidence as an extension before explaining a timing change through those resources.

Choose launch geometry from work coverage, memory access, resource use, and measured latency hiding.

Explain grid, block, warp, lane, SM, and Tensor Core without treating them as synonyms.

Select the resource configuration that produces the best controlled result, not the largest occupancy number.

State why 100 percent occupancy can still be memory- or compute-bound.
