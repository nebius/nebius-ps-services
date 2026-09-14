# Lab 26: Diagnose a slow training-data producer

A training accelerator can wait because its next batch is not ready, even when the model itself is efficient. This lab compares a serial producer with worker-prefetched loading using deterministic sample identities and a configurable producer delay. You will distinguish configured queue capacity, observed readiness waits, and genuine whole-loop throughput without treating a CPU wait proxy as a measured GPU idle interval.

## Before you start

**Theory preparation:** Read Lesson 9 for sample ownership, prefetch, pinning and batch readiness. Reuse Optimizations Lessons 2 and 6 for timing with CPU timers and CUDA events, and transfer lifetimes.

Use one H100 and enough allocated CPUs for worker processes. The dataset is synthetic and deterministic. This lab exposes worker, prefetch, batch-count, and producer-delay options; it does not benchmark real storage.

A single H100 can expose CPU or storage tails quickly; two nodes may increase shared-filesystem pressure without proving a GPU issue.

## Concepts and code path

Samples contain deterministic IDs and token content; the collator adds producer-time metadata. DataLoader uses prefetch only with positive worker counts. The consumer transfers tokens, executes bounded GPU work and records readiness and step durations. SHA-256 digests fingerprint ordered sample IDs and token bytes: matching digests support unchanged content and order for that serialization, not data quality. Queue-depth samples are snapshots; actual consumer waits show whether preparation delays progress.

## Practice

Given 20-millisecond GPU steps and batch-ready intervals with a 30-millisecond p95, the GPU waits at the tail. Change to a versioned offline-tokenized dataset and four persistent workers while preserving sample IDs. Expected observation: ready-time tails shrink and tokens/s rises; if the tokenizer revision or sample sequence differs, the trial is rejected despite better utilization.

Run Lab 26 across worker and prefetch settings with a deterministic sample sequence.

Change one producer setting at a time while keeping all data and consumer work fixed. Both commands include the serial reference and the selected worker-prefetched path.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/26_input_pipeline.py --profile smoke --workers 2 --prefetch 2
sbatch slurm/single_gpu.sbatch labs/26_input_pipeline.py --profile smoke --workers 2 --prefetch 4
```

## Check your results

Require identical sample order/content, complete consumption, and positive timings. Compare `batch_ready_ms`, `consumer_step_ms`, `wall_seconds`, and tokens/s. The `gpu_idle_gap_proxy_ms` field is a host readiness proxy; use a timeline to establish actual GPU idleness.

Record batch-ready time, queue depth, CPU time, H2D time, idle gaps, tokens/s, and sample-order digest.

The pipeline is improved only if the GPU receives equivalent batches sooner.

## Investigate the behavior

Does a larger queue absorb producer variation or merely reserve more host memory? Compare configured capacity with observed queue depth. Explain why adding workers eventually stops helping if the consumer or another resource becomes limiting.

More workers and deeper queues consume RAM and can magnify I/O contention. Offline preprocessing improves steadiness but increases artifact management and reduces online flexibility.

## If something goes wrong

A digest mismatch means the workload changed and invalidates comparison. Worker failures can arise from multiprocessing or CPU limits. Do not suppress missing batches to make throughput look better.

Increasing workers changes random data order because worker seeding is incomplete.

## Takeaways and next step

Input optimization must preserve data completeness and order semantics. Add a real dataset and an actual training consumer as a new experiment, retaining the identity checks and measurement of the complete loop.

Verify order and content first, then tune the slowest producer stage.

State where packing belongs relative to tokenization, batching, and transfer.
