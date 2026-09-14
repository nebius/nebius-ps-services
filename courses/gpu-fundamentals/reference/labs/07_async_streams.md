# Lab 07: Separate submission time from device completion

GPU work is normally queued asynchronously, so a short Python call does not mean a short GPU operation. This lab compares host submission time, serial device execution, and independent-stream execution for matrix work. It helps you understand when parallel queues can expose concurrency and why shared GPU resources can still prevent a speedup.

## Before you start

**Theory preparation:** Read Lab 08 for the matrix product and Lesson 8 for independent streams, event dependencies, buffer lifetime and waiting for both streams before recording the end event. Distinguish overlapping resident arithmetic from host-to-device transfer overlap before comparing paths.

Use one H100 after the timing and transfer lessons. Inputs are resident on the device. This is a compute-stream experiment, not a demonstration of a complete host-input pipeline.

## Concepts and code path

The program creates independent matrix computations, times host enqueue calls, and uses CUDA events for device intervals. The concurrent path schedules independent work on separate streams and joins dependencies before observing the result. Correct ordering is part of the algorithm: a consumer must not read an unfinished producer's tensor just because Python has returned.

## Practice

Given a 5-millisecond copy followed by 8 milliseconds of independent compute, a serial schedule takes about 13 milliseconds after warm-up. Change to a correctly event-ordered double buffer. Expected observation: steady-state iterations can approach 8 milliseconds, but the first still fills and the last drains; two immediate CPU timestamps measure only submission.

This is a schematic copy/compute pipeline, not the supplied resident-matrix experiment. GPU Optimizations Lab 19 implements that extension.

Run the paired serial and stream paths together. Repeat the larger profile separately to investigate whether a single operation already consumes most of the device's available resources.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/07_async_streams.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/07_async_streams.py --profile h100
```

## Check your results

Require `allclose` between corresponding serial and concurrent results. Compare `host_enqueue_median_ms`, `sequential_device`, and `independent_streams_device`. Do not divide the FLOP count by host submission time and call the result device throughput.

## Investigate the behavior

Mark the start, finish, and join on a two-stream timeline. If streams do not improve elapsed time, ask whether GEMMs already saturate compute or memory resources. Use a profiler timeline to establish actual overlap.

## If something goes wrong

Intermittent incorrect results after an edit suggest a missing dependency or unsafe tensor lifetime. Restore explicit producer/consumer ordering before benchmarking. Instrumented runs can change scheduling, so preserve unprofiled timing separately.

## Takeaways and next step

Streams express independent work; they do not manufacture additional hardware capacity. A useful extension introduces one genuine dependency and demonstrates why its consumer must wait while unrelated work remains eligible to execute.
