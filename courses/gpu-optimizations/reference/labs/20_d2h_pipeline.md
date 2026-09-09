# Lab 20: Drain a bounded D2H output pipeline

A GPU can finish producing an output while a copy or CPU consumer still owns its data. This lab builds the missing completion path. You will compare serial output handling, worker overlap, pinned-buffer reuse, asynchronous copies and a dedicated egress stream. Each stage checks every output and waits for all consumers, so an apparently faster run cannot simply abandon queued work.

## Before you start

**Theory preparation:** Read Lesson 8 for allocation lifetime, bounded output ownership and host-read readiness. Reuse Lessons 2 and 6 for complete-loop timing and stream dependencies. The worker model and five comparison modes are explained below.

Read Lessons 6 and 8 and understand Lab 19's ready/reuse events. Use one qualified H100 environment. The synthetic workload produces 512-square FP32 outputs in smoke mode or 2048-square outputs in h100 mode. Defaults allow two in-flight slots and two worker threads. `--sink-ms` is a controlled blocking delay, not a measurement of a disk, network or Python postprocessor.

## Concepts and code path

`run_pipeline` uses dense matrix multiplication to generate a unique constant output for each batch. Its source and destination stay owned by a slot until consumption finishes. The producer records a ready event. Copies on the egress stream wait for readiness and record a copied event; `consume_after_copy` synchronizes that event before any CPU read. `check_output` validates every element, applies the synthetic sink delay, and returns the batch ID.

`OutputSlot` holds a future. Acquiring a busy slot waits for its consumer and propagates failure; it never releases a failed future as a successful result. At most one task per slot is outstanding. Final drain acquires every slot, joins the streams, and verifies that all batch IDs were consumed exactly once. The ownership diagram in Lesson 8 shows why copy completion and consumer completion are different boundaries.

A worker thread runs a task inside the same process and shares its memory. CPython is the standard Python implementation; bytecode is the instruction form executed by its interpreter. In a GIL-enabled build, the global interpreter lock allows only one thread to execute that bytecode at a time, but this lab's blocking sleep releases it. A separate process has its own interpreter; sending objects to it can require serialization, which encodes them for communication, and extra memory copies. This is why an I/O-like sink and CPU-heavy Python processing need different concurrency experiments.

The five modes form a progression. `serial` mode consumes inline. `workers` mode keeps blocking copies but submits host work to threads. `pooled` mode additionally preallocates pinned destinations. `nonblocking` mode removes the host copy wait but still uses one GPU stream. `pipeline` mode additionally moves copies to an egress stream. Pinning and device pools exist in every mode; only the pinned destination allocation location changes at the pooling stage.

## Practice

Suppose production takes 4 ms, D2H 2 ms and one blocking consumer 8 ms per output. Serial execution costs 14 ms per output. A steady pipeline still cannot exceed its slowest effective stage: one consumer can finish only one output per 8 ms. Two workers could reduce that sink limit to about 4 ms if the sink permits concurrency, but two buffers, startup, contention and draining can constrain the result. These values are a reasoning exercise. They explain why adding an egress stream cannot cure an already-saturated sink.

Run the bounded output-pipeline experiment. Compare each neighboring mode, using the same worker/slot count and synthetic sink delay. Then set sink delay to zero to see whether the pipeline's benefits still outweigh its overhead for this small workload. Keep Lab 12 as the separate allocated/reserved-memory experiment; Lab 20 adds transfer and consumer ownership.

Run from this course directory. Keep slots, workers, sink delay and input shape fixed when comparing neighboring modes. Private result files use independent IDs. Run at least three independent jobs per accepted comparison and alternate their order.

```bash
umask 077
for mode in serial workers pooled nonblocking pipeline; do
  sbatch slurm/single_gpu.sbatch labs/20_d2h_pipeline.py --mode "$mode" --slots 2 --workers 2 --sink-ms 2
 done
sbatch slurm/single_gpu.sbatch labs/20_d2h_pipeline.py --mode pipeline --sink-ms 0
```

Profile only a short diagnostic window after measuring unprofiled behavior. Reports are created under a private results subdirectory; do not publish raw traces.

```bash
umask 077
sbatch slurm/nsys_single_gpu.sbatch labs/20_d2h_pipeline.py --mode pipeline --warmup 1 --iterations 2
```

## Check your results

Require `every_output_matches_exact_reference` and `all_outputs_consumed_once`. `whole_loop_samples_ms` includes GPU production, D2H, exact CPU checking, synthetic sink delays, backpressure and final drain. Initial device buffers and preallocated host pools are outside the timer. Per-output pinned allocations in serial/workers occur inside it. `in_flight_destination_capacity_bytes` describes the slot capacity; it excludes allocator caching, transient reference tensors and other host objects, and is not a measured peak.

Use the annotations described below to explain where output handling waits. Correctness, complete consumption and the timing boundary above must remain satisfied in every mode.

## Investigate the behavior

Inspect `produce_output`, `d2h_submit`, `host_consume`, `output_backpressure` and `output_drain`. Correlate annotations with device activity rather than interpreting their host lengths as copy durations. First determine whether workers remove host serialization, then whether pooling reduces allocation work, then whether separate streams overlap independent copies and kernels. Vary sink delay and worker count separately. A slow sink can remain the limit after transfer overlap succeeds.

## If something goes wrong

Stale values indicate premature host reads, device overwrites or destination reuse. Preserve event waits and do not replace them with a guessed sleep. A consumer exception must fail the result and drain executor activity through structured cleanup. Long backpressure with correct results means the bounded queue is working; inspect the slow consumer before expanding it. Threads overlap blocking I/O here; GIL-bound Python computation may require a separate process design with serialization and memory costs accounted for.

## Takeaways and next step

The measured operation ends only when all required outputs have been consumed. Explain which event protects a host read and which future protects reuse. To apply the design to another workload, replace the synthetic delay with a safe real sink and retain exact output checks, bounded ownership and final draining. Do not claim a storage improvement from the supplied synthetic sink.
