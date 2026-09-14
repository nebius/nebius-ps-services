# Lab 13: Test whether communication can overlap independent compute

Communication is not automatically hidden just because an API is asynchronous. This lab compares serialized compute-plus-all-reduce with a schedule that permits the two independent tasks to overlap across two H100 nodes. You will measure elapsed time until both computation and the collective complete and use a timeline to determine whether any observed improvement really comes from concurrent execution.

## Before you start

**Theory preparation:** Read Lessons 11–12 for the qualified collective path, asynchronous Work handles, independent compute, stream joins, critical paths and slowest-rank completion. Lessons 2–3 explain why a timeline and separate unprofiled timing are needed to establish overlap.

Pass the two-node preflight and keep the topology fixed. The matrix work and collective are deliberately independent. This is a mechanics example, not a production backward bucket implementation.

Two one-H100 nodes teach one-rank-per-node communication and overlap. They do not establish intra-node NVLink/NVSwitch performance or production scaling; network/RDMA capability remains site-specific evidence.

## Concepts and code path

The serialized path completes work in sequence. The overlap path submits collective and compute work with explicit completion handling, then joins before timing ends. Per-iteration rank times are reduced to the slowest rank. Known collective contents support an exact sum check, while the GEMM receives only a finite-output check.

## Practice

Given 20 milliseconds of backward work and a 6-millisecond all-reduce launched only at the end, exposed communication is 6 milliseconds. Change bucket readiness so 4 milliseconds overlaps independent backward work. Expected observation: collective duration can remain 6 while exposed cost falls near 2; a timeline and final dependency, not the API flag, prove the overlap.

This is a backward-pass scheduling example; the supplied lab uses independent matrix work and all-reduce, not gradient buckets. Use Training Lab 33 for a real DDP backward-pass overlap experiment.

Run Lab 08 once through the single-GPU launcher and once through the two-node launcher with the same explicit global batch. Then run Lab 13 on two nodes and construct a critical-path timeline. Its asynchronous path needs profiler evidence before any overlap claim.

Run the paired paths together using the two-node launcher. Repeat the larger profile only after both ranks pass correctness; preserve its changed matrix and message sizes as a separate workload.

```bash
umask 077
sbatch slurm/two_node.sbatch labs/13_collective_overlap.py --profile smoke
sbatch slurm/two_node.sbatch labs/13_collective_overlap.py --profile h100
```

## Check your results

Require `all_reduce_exact`, `compute_is_finite`, and a finite ratio. Compare `serialized_median_ms`, `overlapped_median_ms`, and the slowest-rank timing scope. Finite GEMM output does not prove reference equivalence for an edited compute path.

For the fixed-work scaling comparison in [Lab 08](08_distributed_scaling.md), retain per-rank step time, collective ranges, overlap, message size, imbalance and scaling efficiency. Strong-scaling efficiency usually decreases as local compute shrinks relative to latency and communication.

## Investigate the behavior

Draw compute and communication intervals plus the final join. Compare the ideal `max(compute, communication)` intuition with measured completion, remembering shared resources and launch overhead. Use a profiler before asserting that simultaneous execution occurred.

Communication can be hidden only behind independent compute, often while competing for memory or interconnect resources. Bucket tuning consumes memory and changes scheduling. A locally faster kernel can increase rank skew and leave global time unchanged.

## If something goes wrong

An unrealistically short overlap time may exclude a required wait. Incorrect sums may indicate reused buffers before completion. Restore dependencies and correctness before investigating performance; do not use instrumented durations as acceptance timing.

For the fixed-work scaling comparison in [Lab 08](08_distributed_scaling.md), avoid dividing single-rank time by rank count without holding total work fixed.

## Takeaways and next step

Overlap requires independence, timely submission, available resources, and an honest joined boundary. Training Lab 28 applies related ideas to gradients becoming ready during backward, where the dependency graph is more constrained.

Report fixed-work scaling, exposed communication, and the slowest-rank path.

Identify which portion of a collective actually extends the step.
