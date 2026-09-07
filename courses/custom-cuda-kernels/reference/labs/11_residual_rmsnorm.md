# Lab 11: Fuse residual addition with row-wise RMS normalization

Residual addition followed by RMSNorm is a common transformer data path with opportunities to reuse values before writing them back to memory. This lab implements a small FP32 fused row kernel and validates it against a CPU reference. You will understand the row reduction and normalization architecture before generalizing it to large hidden widths, learned scales, or reduced-precision storage.

## Before you start

**Theory preparation:** Read Lesson 13 for residual addition, RMSNorm, mean-square reduction, epsilon and scale weights. Lessons 6–8 supply shared-memory cooperation and reductions; Lessons 3–5 supply references and fusion traffic. Derive the fixed-width row calculation before timing.

Use the completed SM90 build on one H100. The supplied width is fixed at 256 with unit scale weights; smoke has 17 rows and full has 4096. Hidden-size and dtype switches are not implemented.

BF16/FP16 inputs with FP32 reduction are a practical H100 path. Vector width and block shape must fit actual hidden sizes; do not infer speed from a toy row.

## Concepts and code path

One block owns one row and one thread owns one column. Each thread adds input and residual, retains that value, and contributes its square to shared memory. A tree reduction computes the row sum of squares; all threads normalize using `rsqrt(mean_square + 1e-5)` and apply their scale. The CPU reference matches the fixed-width, unit-scale fixture. There is no timed unfused GPU baseline.

## Practice

Given a BF16 row of width 4096, separate kernels write 8 KiB and reread another 8 KiB of residual per row: 16 KiB of logical intermediate traffic. Change to fused addition and RMSNorm with FP32 sum-of-squares. Expected observation: intermediate traffic falls, but odd width 4103, zero rows, and large magnitudes must pass recipe tolerance; resource evidence decides whether retaining values causes spills. For a hand calculation, let x+skip=[3,4], learned scale=[1,1], and temporarily use epsilon=0 to simplify arithmetic. The mean square is (9+16)/2=12.5, so output is approximately [0.8485,1.1314]. The actual lab uses epsilon=1e-5 to keep the denominator positive for an all-zero row. RMSNorm scales by root mean square; unlike LayerNorm, it does not first subtract the row mean.

Run Lab 11's supplied FP32 mechanics baseline: hidden width 256, unit scale weights, and 17 rows for --smoke or 4096 rows by default. It checks a CPU reference and repeated kernel timing; it does not expose hidden-size or dtype switches. Extension: replace one-element-per-thread indexing with a strided per-thread loop, reduce each thread's partial sum, mask tails, and test widths 255, 256, 4096, and 4103. Define empty-row handling on the host, reject nonpositive width, test non-unit learned weights, and add BF16/FP16 load/store conversion with FP32 reduction. Compare with a separately implemented unfused reference before any performance claim.

Run the smoke reference and relevant memory/race checks. The full profile changes row count only and does not test the width-generalization problem.

```bash
umask 077
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/11_residual_rmsnorm" --smoke
sbatch slurm/sanitizer.sbatch memcheck "${COURSE_BUILD_DIR}/11_residual_rmsnorm" --smoke
sbatch slurm/sanitizer.sbatch racecheck "${COURSE_BUILD_DIR}/11_residual_rmsnorm" --smoke
```

## Check your results

Require complete FP32 CPU-reference agreement and no relevant sanitizer errors. Inspect timing, accumulation dtype, and epsilon. These gates do not prove BF16/FP16 behavior, arbitrary-width correctness, or a speedup over an unfused implementation.

For the supplied lab record its fixed FP32 shape, epsilon=1e-5, unit scale, CPU-reference error, and CUDA-event samples. For extensions additionally record actual dtype/shape support, arbitrary scale tests, zero/extreme inputs, reduction strategy, unfused GPU reference, launches, logical bytes, measured traffic, and recipe-specific tolerances.

A fast result is useful only if stability and error remain acceptable across representative rows. Qualify each specialized or approximate-math path against recipe-specific tolerances and the real input range.

## Investigate the behavior

Why can the sum of the input and residual stay in a register through the reduction? Which barrier makes the final sum safe to read? Explain why merely launching 256 threads cannot process a 4096-column row correctly with this indexing.

Keeping residual values in registers can raise pressure; rereading them adds traffic. One-pass reductions can be less stable or more complex. Fusion reduces modularity and needs variants for dtype/layout.

## If something goes wrong

Wrong normalization across every column suggests an incorrect reduction or divisor. Race findings require checking shared-memory synchronization. Non-unit-scale extensions must also update the CPU reference to include those weights.

Accumulating squared BF16 values in BF16 loses accuracy for large hidden dimensions.

## Takeaways and next step

Generalize with strided per-thread column loops, tail masks, partial-sum reduction, and FP32 accumulation. Test widths 255, 256, 4096, and 4103, then add reduced-precision loads/stores and a separately implemented unfused baseline.

Accumulate in FP32, validate edge shapes, and compare with a trusted unfused reference.

Derive the reads/writes removed by fusion.
