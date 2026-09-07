# Lab 09: Sweep logical work per Triton program

Launch geometry determines how a problem is divided among GPU programs and how much work remains in the final partial program. This Python/Triton lab sweeps logical elements per program while holding four warps per program fixed. You will connect masks and launch counts to correctness without confusing logical tile size with CUDA threads per block.

## Before you start

**Theory preparation:** Read Lesson 3 for Triton program instances, logical element blocks, cooperating warps, ceiling division and masked tails, plus Lesson 1 for reference checks and timing. The first run studies geometry; revisit after Lesson 6 for residency and occupancy interpretation.

Use one H100 with the course-qualified Triton package. This is Python learner code, distinct from the later CUDA C++ course. Allow first-use compilation to complete before interpreting warmed measurements.

Exact enabled GPC, SM, and memory-controller counts vary by H100 product and configuration. Course diagrams show containment and data paths, not an exact die floorplan. Measure the actual device rather than hard-coding one SKU count.

Use the SM90 occupancy APIs and actual compiler resource report rather than a generic calculator with another architecture’s limits. Cluster kernels have an additional cluster-occupancy calculation.

## Concepts and code path

Ceiling division rounds a quotient upward so the launch covers every element. For 1,003 elements and 256 logical elements per program, round 1003/256 upward to four programs. The last program considers indices 768 through 1023; a bounds mask permits loads and stores only for indices below 1,003, leaving 21 invalid positions masked out. This is a memory-validity rule, not by itself a measurement of branch divergence. These illustrative dimensions explain the calculation; each supplied profile reports its own dimensions.

The kernel computes global element indices from a program ID and a logical offset range, loads valid elements under a mask, performs the expression, and stores valid outputs. The host sweeps supported block sizes and uses ceiling division for program counts. More logical elements can be handled by the same 128 CUDA threads through multiple values per thread.

## Practice

### After Lesson 3

Given 120 available SMs and 200 equal-duration blocks with one resident block per SM, scheduling needs two waves: 120 blocks, then 80. Change the grid to 121 blocks. Expected observation: the second wave contains one block and the kernel can approach two block durations even though average utilization looks high during the first wave.

Use Lab 09 to inspect launch geometry and predict grid size, cooperating warps and the partial final wave. At this stage compare geometric coverage and elapsed time; revisit resource-limited active warps and residency in Lesson 6.

### Run the supplied experiment

The supplied sweep includes its own launch choices and reference checks. Run both profiles separately to see how problem size changes program count and masked tail elements.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/09_triton_launch_geometry.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/09_triton_launch_geometry.py --profile h100
```

### After Lesson 6

Given 320 threads per block, 65,536 32-bit registers per SM, and no tighter shared-memory, thread, or block limit. At 128 registers per thread, a block needs 40,960 registers, permitting one block by this resource bound. Change to 96 registers per thread: it needs 30,720, permitting two before allocation-granularity constraints. Expected observation: check the actual compiled kernel with occupancy APIs; these estimates alone do not establish residency. A register cap can add spills and make the two-block case slower.

Return to Lab 11 and run its H100 grid-tail probe now that resident blocks and partial waves have meaning; keep this measurement separate from Lesson 5's lane-mask model. Run Lab 09 as a sweep of logical elements per Triton program with four warps per program; this is not a CUDA threads-per-block sweep. Retain its correctness and timing results. For the resource extension, inspect compiled-kernel register/shared-memory reports and a selected Nsight Compute launch/occupancy report for each case; then compare with the occupancy/resource sweep in Custom CUDA Lab 07. Do not report register or occupancy measurements that Lab 09 itself does not emit.

## Check your results

Require the per-case correctness checks. Inspect `programs`, `elements_per_program`, `masked_tail_elements`, `warps_per_program`, and timing distributions. Reported logical GiB/s is a byte-accounting rate, not measured memory transactions.

Record grid size, block/program size, num_warps and elapsed time. Keep resource output if available, but defer interpretation of registers, shared memory and achieved occupancy until Lessons 4 and 6.

Occupancy describes resident work; it is a latency-hiding input, not a direct performance score.

For the resource/profiler extension, record registers per thread, shared memory per block, blocks and warps per SM, spill traffic, and time.

Enough occupancy hides the relevant latency; more occupancy has no value if the limiting pipeline is already saturated.

## Investigate the behavior

Calculate the program count and final mask for one size by hand. Explain why fewer programs need not mean faster execution: larger logical tiles can change registers, scheduling, and per-program work.

More threads or warps per block can expose more work but consume more registers and shared memory per block. More blocks improve wave count only until per-SM resources, dependency stalls, or another pipeline becomes limiting.

Larger tiles increase reuse or instruction-level parallelism but consume registers and shared memory. Smaller blocks can raise residency yet reduce coalescing or reuse. The best point is the one that removes the measured latency without creating a larger cost.

## If something goes wrong

A tail-only mismatch points first to the load/store masks or index formula. A compiler/import failure blocks this Triton exercise, not every PyTorch lab. Record it as an environment issue without inventing substitute results.

Maximizing threads per block can increase register pressure or reduce blocks resident per SM.

Avoid treating theoretical occupancy as a target independent of instruction-level parallelism or memory behavior.

## Takeaways and next step

Geometry tuning must preserve indexing and useful work. Register usage and achieved occupancy are not emitted by this lab; obtain compiler and profiler evidence as an extension before explaining a timing change through those resources.

Choose launch geometry from work coverage, memory access, resource use, and measured latency hiding.

Explain grid, block, warp, lane, SM, and Tensor Core without treating them as synonyms.

Select the resource configuration that produces the best controlled result, not the largest occupancy number.

State why 100 percent occupancy can still be memory- or compute-bound.
