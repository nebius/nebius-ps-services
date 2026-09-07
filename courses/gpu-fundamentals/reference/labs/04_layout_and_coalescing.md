# Lab 04: Evaluate strided access and the cost of repacking

Tensor shape does not reveal how neighboring values are arranged in memory. This lab compares a contiguous tensor, a transposed strided view, and a contiguous copy of that view while preserving each case's mathematical meaning. You will decide whether repacking is worthwhile by including its one-time cost and the number of future reuses.

## Before you start

**Theory preparation:** Read Lessons 1 and 4 for timing, views, strides, materialization and a logical byte ledger. The first run in Lesson 4 studies storage and accessed addresses. Revisit it after Lesson 7 to interpret coalescing, useful bandwidth and whether reuse repays a layout copy.

Run on one H100 in the Fundamentals environment. A logical index identifies an element; strides map changes in its indices to storage offsets. For a contiguous 2-by-3 matrix, strides [3, 1] mean one row step skips three stored elements and one column step skips one. Transposing swaps its logical axes, producing shape [3, 2] and strides [1, 3] without copying values. Such a view shares the underlying storage. The smoke and H100 profiles choose different square sizes; neither exposes an arbitrary stride-layout sweep.

At Lesson 4, use this lab to identify shared storage versus new allocations and build the byte ledger. Lesson 7 returns to the same experiment for transaction efficiency and repacking break-even analysis. You do not need that later performance interpretation to complete the first memory-hierarchy activity.

H100’s large HBM bandwidth and unified L1/shared-memory resources are powerful only when accesses have locality and enough concurrency. The capacity and bandwidth figures vary by H100 model, so labs report the detected device.

Use profiler sector/request metrics for the actual SM90 kernel rather than assuming a fixed transaction count from a simplified diagram. The address calculation and access width still determine the causal expectation.

## Concepts and code path

The workload applies the same pointwise expression to each layout. A transpose changes the index-to-address mapping without copying storage; making it contiguous performs a real copy. The lab separately times operations and repacking, checks corresponding outputs, and computes a reuse break-even from unrounded medians. PyTorch may choose an effective iteration order, so a strided view is not guaranteed to be slow.

## Practice

### After Lesson 4

Given a 32-by-32 tile whose 1,024 FP32 values would otherwise be read four times, the naive ledger is 16 KiB of HBM reads. Change to one cooperative 4 KiB load into shared memory and four reuses. Expected observation: measured HBM traffic falls only to the extent that caches were not already satisfying those reads. Elapsed time improves only if the saved memory cost exceeds the extra staging and synchronization cost; fewer bytes can still accompany a slower kernel.

This is a traffic-accounting example; the supplied PyTorch layout experiment does not implement shared-memory tiling.

Use Lab 04 to identify the accessed addresses and build a logical byte ledger. Preview Lab 05's inputs and repeated operations, but defer its full arithmetic-intensity and roofline interpretation until Lesson 10. Revisit effective bandwidth after coalescing in Lesson 7.

### Run the supplied experiment

Use the supplied cases before introducing a new layout. Keep the complete result, including strides and repack timing, so a future reader can reconstruct the trade-off.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/04_layout_and_coalescing.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/04_layout_and_coalescing.py --profile h100
```

### After Lesson 7

Given 32 lanes loading adjacent FP32 values from an aligned starting address, the useful footprint is 128 bytes and can occupy four 32-byte sectors. Change the same lanes to follow a column mapping with a 4,096-byte stride. They now touch 32 sectors for the same useful values. Expected observation: more sectors are requested for the same logical bytes. This is a specified lane-address model, not a promise about every transposed PyTorch tensor: a framework kernel may adapt its iteration order to strides. Measure sector traffic and elapsed time separately; extra transactions do not guarantee a proportional slowdown.

Run Lab 04 with contiguous and transposed layouts and predict which dimension should be assigned to adjacent lanes.

## Check your results

Require `allclose`, confirmation that the view is strided, and confirmation that the repacked tensor is contiguous. Inspect `timings`, `useful_bandwidth_gib_per_s`, and `repack.break_even_reuses`. Useful bandwidth counts logical input/output bytes, not measured physical HBM transactions.

Retain tensor shape/strides, logical input/output bytes and measured kernel time. These establish the memory path; cache behavior and arithmetic-intensity interpretation are later refinements, not measurements inferred from tensor size alone.

A high cache-hit rate is useful only when the access pattern and working set make those hits meaningful.

Record shape, strides, bytes, time, and profiler memory-transaction metrics.

Coalescing improves useful bytes per transaction; it does not guarantee overall speed if computation or launch overhead dominates.

## Investigate the behavior

Use `copy_cost / (strided_time - packed_time)` only when the denominator is positive. If the packed operation is no faster, there is no finite timing break-even. Explain how repeated reuse changes the application decision.

A shared-memory tile costs loads, stores, address arithmetic, space, and barriers. Caching may already provide enough reuse. `empty_cache()` can return unused cached blocks to the system but is not a routine kernel optimization and can add allocation overhead.

Repacking improves later access and library eligibility but consumes bandwidth, memory, and launch time. A direct strided view is often better for one light use; a packed representation can win across many expensive reuses.

## If something goes wrong

Comparing a transposed result against the untransposed reference confuses semantics with layout. Verify logical indexing before diagnosing precision. Treat tiny timing differences as inconclusive until repeated runs establish a stable sign.

Avoid counting tensor sizes once when an algorithm rereads or materializes intermediates many times.

Avoid calling a tensor contiguous without checking the actual operation's lane-to-address mapping.

## Takeaways and next step

Optimize the full data path, not just the operation after a free-looking conversion. A useful extension adds a downstream consumer and measures repack plus all consumers together against the original strided pipeline.

Build a byte ledger for every read, write, temporary, and reuse before proposing a memory optimization.

Explain why shared memory can help one algorithm and add pure overhead to another.

Describe the address generated by adjacent lanes and transform layout or indexing to reduce wasted transactions.

Draw four lane addresses for a contiguous and a strided load.
