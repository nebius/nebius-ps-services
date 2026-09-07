# Lab 05: Reuse neighboring values with a shared-memory halo

Stencil operations compute each output from nearby input values, so adjacent threads can reuse much of the same data. This lab implements a one-dimensional three-point stencil with a shared tile and two halo values. You will follow interior, block-edge, and global-edge behavior while verifying the boundary condition instead of assuming every neighboring address exists.

## Before you start

**Theory preparation:** Read Lesson 8 for a stencil neighborhood, shared interiors/halos, zero boundary values and the block barrier, using Lessons 3–4 and 6 for guarded loads and shared-memory validation. Derive one edge output before running the fixed weighted stencil.

Use the completed SM90 build on one H100. The smoke case has 1,003 elements, deliberately leaving a partial block. The boundary convention is zero outside the input domain.

The H100 unified L1/shared-memory carveout and block residency influence tile size. Profile actual reuse and resource limits rather than assuming shared memory always beats cache.

## Concepts and code path

Each thread loads its central value into shared memory. Boundary threads load the neighboring halo values where valid, and a barrier makes those loads visible before computation. The output is `0.25*left + 0.5*center + 0.25*right`. A CPU loop implements the same zero-boundary convention. Only the tiled implementation is supplied; there is no timed naive GPU baseline in this lab.

## Practice

Given a 256-output block and radius one, naive code requests up to 768 input values, while a cooperative tile needs about 258 unique values before cache effects. Change `n` to 257 so the second block is partial. Expected observation: every launched thread still reaches the barrier, only valid outputs are stored, and sanitizer and correctness checks pass for the edge case.

Run Lab 05 with `--smoke` (1,003 elements) and without arguments (2²⁴ elements). Inspect interior, block-boundary and global-edge outputs; the smoke fixture also exercises a partial block.

Run the smoke fixture with memory and race checking. The full case changes the element count but preserves the stencil and boundary rule.

```bash
umask 077
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/05_tiled_stencil" --smoke
sbatch slurm/sanitizer.sbatch memcheck "${COURSE_BUILD_DIR}/05_tiled_stencil" --smoke
sbatch slurm/sanitizer.sbatch racecheck "${COURSE_BUILD_DIR}/05_tiled_stencil" --smoke
```

## Check your results

Require full FP32 CPU-reference agreement and no relevant sanitizer errors. Inspect kernel distributions, `shared_bytes_per_block`, and `halo_values_per_full_block`. Correct output and timing measurements cannot establish a speedup without a measured baseline.

Retain global-load estimate, shared bytes, synchronization, time, and maximum error.

Tiling helps when reuse repays load and synchronization overhead.

## Investigate the behavior

Trace one interior point, a block-boundary point, and the final valid input. Which values are reused by neighboring threads? Why must masked threads still respect the block barrier?

Larger tiles improve halo amortization but consume shared memory and can reduce occupancy. Complex boundary logic may diverge. For little reuse, staging and barriers cost more than they save.

## If something goes wrong

Errors only at block boundaries often implicate halo loading; errors at the global edge may indicate the wrong boundary convention. Inspect invalid shared/global reads before changing tile size.

A divergent early return before a block-wide barrier can deadlock.

## Takeaways and next step

Shared tiling makes reuse explicit but adds synchronization and halo overhead. Add a correct direct-global reference kernel as an extension, then compare the same workload and include memory-traffic evidence before claiming a performance benefit.

Keep every participating thread on the same barrier path and mask invalid data explicitly.

Identify all halo loads and boundary conditions for one block.
