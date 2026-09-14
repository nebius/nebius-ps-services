# Lab 05: Locate waiting in a DataLoader pipeline

GPU utilization can be limited by preparing and delivering the next batch rather than by the device computation itself. This lab compares zero and four DataLoader workers on deterministic synthetic samples. Its phase measurements teach you to distinguish waiting for a batch, transferring it, and consuming it, without pretending that synthetic CPU work represents a storage benchmark.

## Before you start

**Theory preparation:** Read Lesson 6 for producer-consumer readiness, pinning and input starvation. Use Lesson 2's CPU timers and CUDA events to separate preparation, transfer and device work. The DataLoader settings are explained below.

Use one H100 and a compute-node environment that supports DataLoader worker processes and pinned memory. The launcher must have enough CPU resources. Pinning is enabled in both variants; positive-worker prefetch is fixed at two.

H100 can consume data rapidly enough that shared storage and modest CPU transforms become the limiting stage. Compare one node in isolation before interpreting two-node storage contention.

## Concepts and code path

A dataset maps an index to a sample; collation assembles samples into a batch. Deterministic per-index generation keeps content fixed while the loader compares zero and four workers. Zero workers prepare data in the caller; four prepare ahead with prefetch factor two. Batches contain eight samples and use pinned memory. The program synchronizes each batch to measure readiness, transfer and consumption separately. These serialized measurements do not establish copy/compute overlap.

## Practice

Given 12-millisecond GPU steps and batch-ready intervals with a 9-millisecond median but 25-millisecond p95, tail starvation creates visible gaps. Change workers from four to eight and observe a 20-millisecond p95 plus doubled storage latency. Expected observation: the queue improves, but adding more workers after the storage knee is rejected; verify sample IDs and order remain correct.

Run Lab 05's supplied comparison of zero versus four loader workers on synthetic CPU-generated samples. Pinning is enabled, and the four-worker case uses prefetch factor two; these are fixed settings, not exposed sweep options. Extension: parameterize pin_memory and the positive-worker prefetch factor, vary one at a time, and preserve sample IDs and model work. Do not pass prefetch_factor to a zero-worker loader. Add a separate real-data trial before drawing storage conclusions.

Run the fixed comparison first, then increase only the number of measured batches. Startup is warmed separately, and the program checks the checksum and pinning status.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/05_input_pipeline.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/05_input_pipeline.py --profile smoke --batches 100
```

## Check your results

Require `checksum` and `all_batches_pinned`. Compare `batch_ready_gap`, `h2d`, `device_consumption`, and `end_to_end` phase distributions for `num_workers_0` and `num_workers_4`. End-to-end here is per batch; the JSON does not report a whole-campaign throughput measurement.

Both checksums must be finite. Infinity and NaN (not a number) invalidate the run before a successful result is written; investigate the computation before interpreting its timing.

Retain the supplied component and per-batch end-to-end timing distributions; the component measurements deliberately serialize boundaries and do not themselves prove overlap. Lab 05 does not emit whole-campaign throughput: add a complete-loop wall timer and actual consumed-sample count for that extension rather than inverting a median batch time. Also collect batch-ready timestamps, sample-order checks, host memory, and a profiler timeline showing GPU idle gaps. CPU utilization and storage latency require separate host/storage observations; synthetic samples do not measure filesystem performance.

The best setting keeps the GPU supplied without unstable queue growth or excessive host memory.

## Investigate the behavior

Does worker parallelism reduce readiness gaps enough to offset its overhead? Which component dominates the batch boundary? Do not add component medians and assume their sum equals the median total.

More workers and prefetch raise memory, file descriptors, context switches, and storage concurrency. Offline preprocessing reduces online CPU work but creates a versioned data artifact that must match tokenizer or transform semantics.

## If something goes wrong

Worker startup errors require inspecting the multiprocessing environment. Unpinned batches fail the declared comparison. More workers can be slower for small synthetic samples; that is a valid result, not a reason to discard the baseline.

Avoid measuring synthetic in-memory data and claiming a storage-pipeline improvement.

## Takeaways and next step

Parameterizing pinning and prefetch is an explicit extension, not an existing CLI feature. Preserve sample IDs, omit prefetch for zero workers, and add whole-loop timing and a real-data trial before making throughput or storage claims.

Tune the real slowest producer stage and recheck device utilization end to end.

Draw the queue between each producer and consumer stage.
