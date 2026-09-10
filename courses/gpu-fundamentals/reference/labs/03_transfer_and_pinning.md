# Lab 03: Measure pageable and pinned host transfers

Pinned memory can help the GPU transfer data directly, but allocating pinned buffers and requesting a nonblocking copy do not automatically overlap transfers with computation. This lab measures four host-to-device copy combinations with an explicit completion boundary. You will learn what these timings establish and what still requires a timeline experiment.

## Before you start

**Theory preparation:** Read Lab 01 for complete timing and exact-copy checks, then Lesson 8 for pageable versus pinned storage, nonblocking submission and completion. Predict which wait makes each copied value safe before running the four transfer modes.

Use one H100 and sufficient host memory for both pageable and pinned buffers. Pinning consumes a limited host resource. The default smoke transfer is 64 MiB; use the size override for a bounded comparison.

H100 can execute copies and compute concurrently under supported paths, but the CPU, PCIe/fabric topology, pinning limits, and framework stream semantics remain part of the experiment.

## Concepts and code path

The program creates one pageable input, copies its contents into a pinned allocation, and reuses a GPU destination. It compares blocking and nonblocking submission for each source type. Every measured copy is followed by device synchronization, so the host duration includes completion. The final exact-copy check validates the destination contents; the lab does not run a producer/consumer pipeline.

## Practice

Use Lab 03 to compare pageable/pinned transfer modes and Lab 07 to compare serial and independent-stream execution of resident matrix work. GPU Optimizations Lab 19 extends these principles to a complete H2D copy/compute pipeline.

Compare two transfer sizes in separate jobs. Allocation and initial pinning are outside the copy timing, so record that exclusion when relating results to your application.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/03_transfer_and_pinning.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/03_transfer_and_pinning.py --profile smoke --size-mib 128
```

## Check your results

Require `exact_copy` and inspect all four `modes`, their median milliseconds, and effective GiB/s. The rate counts payload bytes in one direction. It is not HBM bandwidth and does not establish simultaneous copy and compute.

For the later overlap extension, keep a timeline, CUDA-event measurements, end-to-end wall time, transfer sizes, and dependency description.

Overlap is proven when the timeline and end-to-end time show concurrent useful work, not when two API calls were issued on different streams.

## Investigate the behavior

Which costs are excluded by reusing the buffers? Why can `non_blocking=True` have little effect when the host immediately waits? Compare the size trend before assuming a hardware link has reached its practical limit.

Pinned memory speeds asynchronous transfers but consumes a scarce OS resource and can hurt system behavior if overused. More streams increase possible overlap while making lifetime, ordering, and debugging more complex.

## If something goes wrong

Pinned-allocation failure may reflect host limits rather than insufficient GPU memory. Stop or reduce the declared allocation. If copies appear implausibly fast, confirm the timer includes synchronization and the intended number of bytes.

In a later overlap experiment, synchronizing after every operation would serialize the schedule. Retain the supplied per-copy waits here: they are required to measure completed transfers.

## Takeaways and next step

Pinning is an enabling mechanism, not an overlap guarantee. Next, use Lab 07 to reason about independent streams; a real input pipeline additionally needs safe buffer lifetime, a producer, and an explicit consumer dependency.

For the later end-to-end overlap experiment, place events around device work and use one final synchronization.

Identify every dependency that prevents two operations from overlapping.
