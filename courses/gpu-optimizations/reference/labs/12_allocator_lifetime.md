# Lab 12: Follow live, cached, and released GPU memory

Deleting a tensor does not necessarily return its memory reservation to the driver, and emptying the cache cannot free a live tensor. This lab makes those distinctions observable through a short allocation lifecycle. You will learn to read allocator counters before diagnosing a leak or adding cache-clearing calls to a performance-sensitive loop.

## Before you start

**Theory preparation:** Read Lesson 8 for allocated/reserved/peak memory, tensor references, caching and fragmentation, using Lesson 1’s fixed workload. Explain when a tensor becomes reclaimable and why releasing cached blocks cannot free live storage.

Use one H100 in a fresh lab process with sufficient free memory. This is an allocation-state exercise, not a speed benchmark. Do not run it inside an important application whose allocator behavior you intend to preserve.

Compare the measured device capacity and site-visible MIG state with the declared full-H100 assumption. Leave safety headroom for libraries, collectives, graph capture, and transient peaks.

## Concepts and code path

The script snapshots a baseline, allocates three tensors, deletes the middle allocation, and introduces a smaller replacement. It then deletes selected tensors, empties the cache while the first tensor remains live, deletes that final tensor, and empties the cache again. Snapshots distinguish live allocated bytes, allocator-reserved bytes, inactive split bytes, retries, and OOM counts.

## Practice

Given a 12-GiB activation live through backward and a 10-GiB optimizer temporary created before it dies, peak live memory is at least 22 GiB plus other state. Change the schedule so the activation’s last use precedes the temporary. Expected observation: peak falls even if `memory_reserved` remains high, showing why allocator reserve and live capacity require separate interpretation.

Run Lab 12 and map each allocation to the point where it becomes reclaimable.

Use smoke first; the larger profile scales allocation sizes. Both runs intentionally manipulate only the allocations and cache of their own process, not cluster configuration.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/12_allocator_lifetime.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/12_allocator_lifetime.py --profile h100
```

## Check your results

Require the lifecycle invariants, including falling allocated bytes after deletion and preservation of the live allocation during `empty_cache`. Compare snapshots by event name. Reserved bytes need not equal allocated bytes at every step.

Record allocated/reserved peaks, snapshots, tensor lifetimes, retries, and out-of-memory context.

Optimize live-state and temporary peaks before tuning allocator knobs.

## Investigate the behavior

Draw each tensor's lifetime across the snapshots. Which bytes can be reused within the process? Which remain live? Why can a cache-clearing operation lower reservation without reducing the memory needed by the application?

Aggressive reuse and in-place updates complicate correctness. Strategies that reduce fragmentation can increase synchronization or reduce caching efficiency. Recomputing state saves capacity by spending compute; sharding saves per-rank state by spending communication.

## If something goes wrong

Unexpected live bytes can indicate a remaining reference or changed allocator behavior. Inspect the controlled lifecycle first. Missing or different allocator counters require an environment-specific explanation, not an invented zero.

Avoid using `empty_cache()` to mask an oversized live working set.

## Takeaways and next step

Separate ownership from reservation before diagnosing memory problems. Extend the lab with a deliberately retained tensor reference, predict which invariant changes, and remove that reference explicitly; do not present routine cache clearing as a leak fix.

Prove the owning lifetime, reduce or reschedule the allocation, then remeasure fragmentation.

Explain why reserved minus allocated is not automatically leaked memory.
