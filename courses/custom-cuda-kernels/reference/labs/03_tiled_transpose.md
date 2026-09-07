# Lab 03: Coalesce a transpose and reduce shared-memory conflicts

A transpose naturally makes either reads or writes strided when implemented directly. This lab compares naive, tiled-unpadded, and tiled-padded CUDA transposes. You will see how a shared-memory tile changes data movement and why adding one padding column can improve bank mapping without changing the mathematical output or the number of useful matrix elements.

## Before you start

Complete the supplied SM90 transpose comparison after Lesson 6. The CUDA Tile C++ evaluation below is an optional revisit after Lesson 16, using its separately qualified development-image trial; it is not required for this lab or core course completion.

**Theory preparation:** Read Lesson 6 for row/column addressing, coalescing, shared tiles, bank conflicts, padding and unconditional block barriers. Reuse Lessons 3–4 for guarded rectangular edges, reference checks and sanitizer scope before comparing layouts.

Use the completed SM90 build and one H100. Smoke uses 1003×1020 and full uses 8192×8209: both are rectangular edge cases. Arbitrary or square shapes require a source-edit/rebuild extension.

SM90 transaction and bank metrics should confirm the model. The general coalescing principle is portable; exact profiler metric names and optimal tile geometry depend on toolkit and kernel resource use.

The CUDA Tile C++ extension is version-gated and optional for H100. It does not change the required CUDA C++20/SM90 path or completion criteria.

## Concepts and code path

The naive kernel directly reverses row/column indices. Tiled variants cooperatively load contiguous rows, synchronize, and read the tile in transposed order for coalesced output. Shared arrays have 32 or 33 columns; the latter changes the bank mapping. Bounds checks protect edge tiles, and all block threads must reach the barrier even when some do not own a valid output.

## Practice

### After Lesson 6

Given lane `i` writing a transposed column with address stride equal to the output row width, which is the input row count, global stores scatter. Change to a `32×32` shared tile: global stores coalesce but column reads map to the same bank. Add one padding column. Expected observation: global transaction efficiency remains high and shared-bank conflicts fall; a 1000×1000 edge test checks those predicates and barriers for that case, alongside reasoning and sanitizer evidence.

Run Lab 03's naive, tiled-unpadded, and tiled-padded variants on its supplied rectangular edge shapes: 1003×1020 for smoke and 8192×8209 for full. A square shape or arbitrary dimensions require a source-edit/rebuild extension with corresponding reference and sanitizer checks; the executable has no shape option.

### Run the supplied experiment

Run all three variants and both memory/race checks. Keep the same matrix dimensions and reference when comparing variants; sanitizer reports are separate from timing evidence.

```bash
umask 077
sbatch slurm/single_gpu.sbatch "${COURSE_BUILD_DIR:?set the completed build directory}/03_tiled_transpose" --smoke
sbatch slurm/sanitizer.sbatch memcheck "${COURSE_BUILD_DIR}/03_tiled_transpose" --smoke
sbatch slurm/sanitizer.sbatch racecheck "${COURSE_BUILD_DIR}/03_tiled_transpose" --smoke
```

### After Lesson 16

Given the accepted padded transpose, express the same 32-by-32 tile load/transpose/store in CUDA Tile C++. Change no semantics or workload. Expected observation: source may become shorter, but adoption requires correct results for the same edge cases, supported sanitizer/profile tooling, understood generated memory transactions, and no unacceptable compile/runtime regression versus the CUDA C++ fallback.

In a separate CUDA 13.3 development-image trial, read the official principles, record the exact immutable image, and write a porting design for one existing lab. Do not change the required build or claim execution until that profile passes compiler and H100 gates.

## Check your results

Require all three complete CPU-reference comparisons. Inspect `naive`, `tiled_unpadded`, and `tiled_padded` distributions and the declared shared-column counts. Use a focused profiler report to establish bank-conflict and global-transaction behavior rather than inferring it solely from timing.

Retain transactions, bank-conflict metrics, effective bandwidth, time, and correctness.

The padded tile is accepted when fewer conflicts translate into lower end-to-end transpose time.

For the optional CUDA Tile C++ evaluation, retain toolkit and API versions, architecture target, generated-code inspection, reference comparison, sanitizer result, profiler evidence, timing distribution, and a keep/defer decision.

Adopt the abstraction only when it preserves the operation contract and provides a maintainable path on the qualified deployment targets.

## Investigate the behavior

Map a warp's load and transposed shared-memory read addresses. How does the extra column alter bank indices? Why can padding help the shared phase but still leave another resource as the overall limiter?

Padding consumes extra shared memory; larger tiles improve amortization but can reduce occupancy. Shared-memory staging adds instructions and barriers and loses when the original access or caches were already sufficient.

Higher-level tiles may improve clarity and portability of intent while limiting low-level control or depending on a less mature compiler implementation. Maintaining two implementations also increases test cost.

## If something goes wrong

Failures near matrix edges suggest mismatched input/output guards. Barrier divergence or reading unwritten shared entries requires fixing synchronization/indexing, not increasing allocation blindly. Inspect the exact sanitizer location.

Omitting bounds checks makes only tile-aligned matrices appear correct.

Avoid treating source-level convenience or successful compilation as proof of runtime correctness, performance, or portability.

## Takeaways and next step

Tiling coordinates global access and local reuse; padding changes bank layout. Add square and additional edge shapes only with new full-reference and sanitizer checks before generalizing the performance conclusion.

Validate partial tiles and map adjacent lanes to adjacent global addresses in both phases.

Draw lane addresses for load, shared placement, and store.

Keep CUDA Tile C++ isolated as an optional CUDA 13.3 evaluation; the core course remains CUDA C++20, SM90, CMake, and library baselines whose target qualification is recorded separately.

List the evidence required to promote this optional evaluation from advanced/deferred to an accepted H100 lab.
