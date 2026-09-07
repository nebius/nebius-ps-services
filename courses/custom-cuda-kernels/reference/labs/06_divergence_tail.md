# Lab 06: Group unequal work while preserving output order

Neighboring lanes that execute different amounts of work can leave some lanes inactive while others continue. This lab compares alternating long/short work with a grouped arrangement that preserves every input's assigned calculation. You will include the packing and scattering needed to restore logical order, avoiding a misleading comparison that treats data reorganization as free.

## Before you start

**Theory preparation:** Read Lessons 5, 9 and 10 for FMA work, resources/occupancy, divergence, grouping, packing/scattering and tail waves. Lessons 3–4 supply CPU references and profiling. Count the same long/short work and restore output order before comparing joined costs.

Use the completed SM90 build on one H100. Even-indexed elements perform 32 fused multiply-add operations (FMAs) and odd-indexed elements perform four. Each FMA computes `a*b + c` with one final rounding; recall that Lab 07 counts it as two floating-point operations. The supplied modes are divergent and grouped paths, not a configurable balanced/skewed scheduling suite.

Calculate concurrent blocks from SM90 registers, shared memory, and block limits rather than SM count alone. Library/persistent kernels may use scheduling policies beyond a simple static grid.

## Concepts and code path

The baseline branches on logical index parity. The grouped path packs long tasks together, runs their corresponding iteration counts, then scatters outputs back to original indices. A CPU reference and cross-path comparison enforce preserved work. Timings separately report grouped core, packing/scattering overhead, and the joined grouped path. The printed wave estimate assumes one block per SM; it is not measured occupancy.

## Practice

Given mixed lane tasks costing 2 or 20 iterations, one warp executes both paths. Change to sorted tasks so warps are uniform; branch evidence improves. Separately repartition the same total tasks from 241 to 240 blocks on a device with capacity for 240 resident blocks; do not obtain the change by dropping work. Expected observation: the first addresses lane masks, the second removes a tail wave; neither necessarily repays sorting or altered task granularity end to end.

Run Lab 06's divergent and grouped paths with the same logical long/short work. Compare the complete grouped timing, including packing and scattering, with the divergent baseline. Its printed wave counts assume one block per SM and are not measured residency. Balanced/skewed task and explicit grid-wave surveys are separate exercises in GPU Optimizations Lab 15; they are not modes in this executable.

Run the paired smoke case and memory check before increasing problem size. The primary grouped timing includes reorganization; do not substitute the core-only number in the final comparison.

```bash
umask 077
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/06_divergence_tail" --smoke
sbatch slurm/sanitizer.sbatch memcheck "${COURSE_BUILD_DIR}/06_divergence_tail" --smoke
```

## Check your results

Require both CPU-reference comparisons and agreement between restored outputs. Inspect long/short counts, `divergent`, `grouped_core_only`, `pack_and_scatter_overhead`, and `grouped_end_to_end` distributions. A faster core can coexist with a slower complete grouped path.

Retain active-lane metrics, per-block work, grid waves, tail duration, preprocessing, and total time.

Apply the remedy at the level where uneven work is introduced.

## Investigate the behavior

Trace an odd logical index through its packed position and return mapping. Which work is unchanged and which overhead is added? Use compiler/profiler evidence to inspect the actual branch and residency behavior.

Sorting improves branch coherence while harming locality and spending bandwidth. Dynamic queues balance work but serialize on atomics. More blocks fill waves but increase redundant setup.

## If something goes wrong

Incorrect output order indicates a mapping defect, not numerical error. Odd-sized extensions require careful long-count calculation. Do not claim measured grid-tail utilization from the simple one-block-per-SM estimate.

Increasing block size does not repair a skewed work distribution.

## Takeaways and next step

Regrouping is useful only when its complete cost is justified. A follow-on experiment can amortize packing over repeated reuse, with the same logical outputs and an explicitly expanded timing boundary.

Separate lane masks, block duration, and grid coverage before tuning.

Name one distinct fix for each imbalance class.
