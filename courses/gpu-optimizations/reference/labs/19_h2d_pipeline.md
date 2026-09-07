# Lab 19: Overlap H2D copies with a bounded input ring

A nonblocking copy can free the CPU while the GPU still executes copies and kernels sequentially. This lab separates those two effects. It compares a single-stream input path with a copy/compute pipeline using the same pinned buffers, deterministic batches and dense matrix multiplications. You will establish the dependencies that make reuse safe, measure the complete loop, and use a trace to determine whether independent work actually overlaps.

## Before you start

**Theory preparation:** Read Lesson 6 for pinned input slots, DataLoader versus stream prefetch, inference mode, ready/completion events and safe ring reuse. Lessons 1–2 supply exact-output and joined-loop checks; Fundamentals Lesson 8 supplies stream ordering. Predict fill, overlap and drain before running.

Read Lessons 2, 3 and 6, and complete Fundamentals' transfer/pinning exercise. Use one full H100 with the course's qualified PyTorch environment. No datasets, model downloads or storage access are required. The smoke profile uses 512-square FP32 matrices; h100 uses 2048-square matrices. The defaults use two slots and sixteen batches. Keep CPU and pinned-memory consumption within your allocation; eight slots is an explicit upper bound, not a recommended setting.

## Concepts and code path

`run_pipeline` allocates one pinned host matrix, device input and output per slot, plus a shared dense weight matrix. Each batch has a different exactly representable value. A matrix filled with 1/width preserves that value after multiplication because the width is a power of two. Every output element must match, catching stale or incompletely transferred data. This is a deliberately controlled dense-compute workload, not a representative neural network.

A used slot cannot be filled again until its compute-complete event has finished. The copy stream records readiness after H2D. The compute stream waits for readiness, performs `--work` GEMMs, records the exact check and then records completion. Serial mode uses the compute stream for copies too; pipeline mode changes only copy-stream placement. Initial device setup is synchronized before timing. See Lesson 6's input-slot diagram for the ownership cycle.

## Practice

As a schematic example, assume each batch needs 3 ms of H2D and 5 ms of compute, with no shared-resource contention. Four serialized batches take 32 ms. With two safe slots, copy batch 1 while computing batch 0, and continue until the fourth compute finishes: ideal elapsed time is 3 + 4 × 5 = 23 ms, including fill and drain. These are illustrative assumptions, not H100 measurements. If the CPU takes 10 ms to prepare every batch, the transfer schedule cannot remove that producer limit.

Run the bounded input-ring experiment. Unlike Lab 05's deliberately serialized phase measurements, it reports a joined whole-loop duration. Compare serial and pipeline modes with two slots, then use one slot as an overlap-negative control. The data is generated in memory; no storage or DataLoader worker performance is claimed.

From this course directory, compare the same workload in fresh jobs. The JSON result appears in the results directory with a unique run identifier. Repeat each configuration at least three times in independent jobs for acceptance; the iteration samples within one process are not independent trials.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/19_h2d_pipeline.py --mode serial --slots 2
sbatch slurm/single_gpu.sbatch labs/19_h2d_pipeline.py --mode pipeline --slots 2
sbatch slurm/single_gpu.sbatch labs/19_h2d_pipeline.py --mode pipeline --slots 1
```

After unprofiled timing, capture short diagnostic runs separately. The existing launcher produces private Nsight reports and summary text. Inspect the `h2d_loop` interval, excluding allocation and initialization before it.

```bash
umask 077
sbatch slurm/nsys_single_gpu.sbatch labs/19_h2d_pipeline.py --mode pipeline --slots 2 --warmup 1 --iterations 2
```

## Check your results

Require `every_batch_matches_exact_reference` and `pipeline_drained`. `whole_loop_samples_ms` and the min/median/p90 summary include host fills, copies, GEMMs, device checks, slot waits and final drain. They exclude initial allocations. `pinned_pool_bytes` is the explicit input pool, not total host memory or allocator reserve. With smoke and two slots, it is 2 × 512 × 512 × 4 = 2,097,152 bytes; this is allocation arithmetic, not measured RSS.

Retain the batch count and GEMMs per batch alongside the reported samples and pool size. Inspect `slot_reuse_wait` and `pipeline_drain` for ownership stalls; use the copy/compute correlation described below to assess overlap.

## Investigate the behavior

Follow `h2d_submit` and `consume_batch` to their corresponding device rows. The copy for a later batch may intersect a current batch's GEMM; the kernel consuming a batch must follow its own copy. One slot deliberately removes this runway. Increase only `--work` to change compute/transfer balance, then only `--slots` to examine buffering costs. More streams or a continuous kernel row does not establish full SM utilization.

## If something goes wrong

A failed reference is a data-integrity failure, even when the run appears faster. Check both ready and reuse dependencies before timing again. Allocation failure requires smaller bounded resources, not unlimited pinning. A pipeline slower than serial is a valid result for small work or contention. CPU fill may become the bottleneck; this lab does not measure loader workers or disk latency.

## Takeaways and next step

Host asynchrony, producer prefetch and device overlap are separate mechanisms. A complete answer identifies the dependency that protects each buffer, the overlap visible in a trace, and the joined-loop result. Transfer this design to a real loader only after preserving batch IDs and pinning lifetime. Then study Lab 20's reverse direction, where the CPU must wait before reading a transferred output.
