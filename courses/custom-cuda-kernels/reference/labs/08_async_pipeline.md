# Lab 08: Double-buffer global-to-shared copies

Asynchronous copies can prepare the next tile while the current tile is being processed, but correct staging and enough independent computation are essential. This lab compares a serial tiled transform with a double-buffered Cooperative Groups copy pipeline. You will trace priming, steady-state work, waits, and buffer reuse while varying arithmetic intensity under a fixed data-movement convention.

## Before you start

**Theory preparation:** Read Lesson 11 for Cooperative Groups, asynchronous global-to-shared copy, wait/synchronization, double buffering and safe reuse. Lessons 4–5 supply NaN sentinels, CPU references and FMA counting; Lessons 6 and 9 supply shared layout and resource costs. Read the pipeline walkthrough before the sweep.

Use the completed SM90 build and read the [pipeline walkthrough](../lab-mechanisms.md). Both variants use one block intentionally; this is a local pipeline mechanics experiment, not an HBM-saturating whole-GPU benchmark.

H100 supports advanced asynchronous movement, but extra shared stages consume capacity. The baseline lab uses documented block-scoped APIs; TMA and warp specialization remain separately gated.

## Concepts and code path

Cooperative Groups is CUDA's interface for naming collaborating groups of threads and performing coordinated operations within them. This lab uses the entire thread block as the group participating in asynchronous copies, waits and synchronization.

The serial kernel loads a tile, synchronizes, computes, and advances. The pipelined kernel primes one shared buffer, starts the next copy into the other buffer, computes the current tile, then waits and synchronizes before reuse. Tail loads are bounded. A sentinel is a deliberately recognizable initial value. Before each validation launch, this lab fills the output with NaN (Not a Number), a special floating-point value. An element the kernel fails to write retains that marker and fails the check that every output is finite. Independent CPU references also detect incorrect finite values in both variants.

## Practice

Given four tiles with a 5-microsecond load and an 8-microsecond computation per tile, serial execution is about 52 microseconds before overhead. Change to a correct two-stage pipeline. Expected observation: after a 5-microsecond prime, steady stages approach the 8-microsecond compute limit and then drain, but shared-memory use and synchronization can prevent the ideal 37-microsecond bound.

Run Lab 08 to sweep 0, 8, 32 and 128 FMAs per element for serial and pipelined variants. Both read/write the same elements, so logical intensity is 0, 2, 8 and 32 FLOPs per byte under an eight-byte global-traffic convention. Use `--work-iterations N` to isolate a point from 0 through 1024. Both smoke and full profiles include a partial final tile. Follow the [pipeline experiment](../lab-mechanisms.md) for predictions, reference checks, profiling and failure analysis.

The default sweep uses 0, 8, 32, and 128 FMAs per element. Isolate a point with `--work-iterations` when profiling or checking synchronization; valid values are 0 through 1024.

```bash
umask 077
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/08_async_pipeline" --smoke
sbatch slurm/sanitizer.sbatch synccheck "${COURSE_BUILD_DIR}/08_async_pipeline" --smoke --work-iterations 32
sbatch slurm/sanitizer.sbatch racecheck "${COURSE_BUILD_DIR}/08_async_pipeline" --smoke --work-iterations 32
```

## Check your results

Require complete FP32 reference agreement in both variants and no relevant sanitizer errors. Inspect serial/pipelined distributions at each work point. With eight logical global bytes per element and two FLOPs per FMA, intensities are 0, 2, 8, and 32 FLOPs/byte.

Retain pipeline stages, per-point timing distributions, independent CPU-reference results for both variants, logical work/bytes, stall metrics, and shared-memory cost. This single-block mechanism exercise does not saturate the H100; logical bytes exclude shared traffic, cache effects and transaction padding. Verify copy/compute overlap through profiling rather than inferring it from the API name or timing alone.

Overlap helps only when enough independent compute exists and extra shared memory does not destroy residency.

## Investigate the behavior

Which interval can hide the next copy, and when must the consumer wait? Why might the zero-compute case gain nothing? Explain how double buffering increases shared storage and can affect a future multi-block implementation.

More stages hide longer latency but consume shared memory/registers and lengthen fill/drain. Small tiles or little compute cannot amortize synchronization. Complexity increases race and deadlock risk.

## If something goes wrong

Tail corruption suggests incorrect valid-byte counts or reads from an unfilled stage. Intermittent errors suggest missing readiness or reuse synchronization. Fix those before interpreting timing or compiler instruction selection.

Reading a stage before its asynchronous copy is complete produces intermittent corruption.

## Takeaways and next step

Pipelining requires an explicit producer/consumer contract. Extend to multiple blocks only with safe tile ownership, complete reference checks, resource measurements, and end-to-end evidence; do not assume the asynchronous API guarantees overlap.

Model prime, steady-state, and drain phases and validate every synchronization edge.

Draw the two-stage timeline for three tiles.
