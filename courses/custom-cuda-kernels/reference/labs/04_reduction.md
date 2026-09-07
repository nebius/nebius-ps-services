# Lab 04: Compare atomic, hierarchical, and CUB reductions

A reduction combines many inputs into a small output, so contention and aggregation strategy matter. This lab sums a vector using one atomic per element, warp/block aggregation followed by one atomic per block, and CUB's maintained device reduction. You will understand why reducing communication at each hierarchy level can matter more than making an individual addition faster.

## Before you start

**Theory preparation:** Read Lesson 7 for atomics, shuffle participation, hierarchical reduction, shared partials, CUB workspace and destination reset. Lessons 3–4 supply finite reference checks and race/synchronization tools. State the sum and reset timing boundary before each variant.

Use the completed SM90 build with its CUDA/CUB headers on one H100. Inputs are ones and the reference sum is known. Arbitrary signed or ill-conditioned values are not covered by this fixture.

H100 atomics and warp operations are strong, but contention and memory scope still matter. Use the library implementation unless the need for custom fusion or behavior justifies owning the implementation.

## Concepts and code path

The naive kernel atomically updates one scalar for every element. The hierarchical kernel uses shuffle reductions within warps, shared partials across warps, then a block-level atomic. CUB first queries temporary storage and reuses it for timed calls. Atomic destinations are reset before each sample, outside the event interval; a complete application may need to include that reset cost.

## Practice

Given one block of 1,024 threads, each issuing one global atomic, the output sees 1,024 contending updates. Change to 32-thread warp reductions, a shared-memory combination of warp partials within the block, and one atomic. Expected observation: global atomics drop to one per block, while CUB may still be faster or more robust; both outputs pass the declared floating-point tolerance.

Run Lab 04 with `--smoke` (1,003 elements) and without arguments (2²⁴ elements), comparing per-element atomics, block aggregation, and CUB. Additional sizes or input values require a source-edit/rebuild extension.

Run the partial-tail smoke case and relevant sanitizers before the full vector. All three paths operate on the same input and are reported separately.

```bash
umask 077
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/04_reduction" --smoke
sbatch slurm/sanitizer.sbatch memcheck "${COURSE_BUILD_DIR}/04_reduction" --smoke
sbatch slurm/sanitizer.sbatch synccheck "${COURSE_BUILD_DIR}/04_reduction" --smoke
```

## Check your results

Require all three sums to match the expected count. Inspect per-element versus block atomic counts, CUB temporary bytes, and timing distributions. This all-ones fixture does not characterize floating-point summation error for arbitrary inputs.

Retain operation order, atomic count, block size, time, numerical error, and library result.

Different reduction orders can produce different floating-point results; use declared tolerances rather than bitwise equality.

## Investigate the behavior

Calculate how many atomic updates the block strategy removes. Why do inactive tail lanes contribute zero while still participating in the shuffle/barrier structure? Explain how CUB affects workspace requirements and maintenance effort.

More hierarchical aggregation reduces contention but adds synchronization and temporary storage. Deterministic reductions can cost throughput. Larger blocks reduce partial count while increasing resource use.

## If something goes wrong

An increasing sum across repetitions indicates a missing reset. Tail errors can indicate an invalid shuffle participation assumption. Race or synchronization findings must be resolved before treating a faster reduction as valid.

Avoid optimizing one power-of-two length while failing partial blocks.

## Takeaways and next step

Use hierarchical aggregation and compare against maintained primitives before writing more custom reduction code. Extend with signed values and a high-accuracy reference, explicitly defining the numerical tolerance and including required setup in end-to-end timing.

Test odd-sized inputs and large values against a trusted reduction implementation, and verify the declared policy for empty inputs.

Explain where synchronization is required inside a block reduction.
