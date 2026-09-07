# Lab 03: Compare eager and compiled pointwise execution

Several pointwise operations can repeatedly read and write intermediate tensors. Compilation may combine that work and reduce launches or memory traffic. This lab compares the same expression in eager PyTorch and `torch.compile`, separating the first compiled call from warmed execution so you can reason about both startup cost and repeated-use benefit.

## Before you start

**Theory preparation:** Read Lesson 4 for eager execution, SiLU/tanh composition, fusion, full-graph compilation and separate startup timing, after Lessons 1–3’s correctness/measurement/profiling foundation. Revisit the same code in Lesson 7 for its intermediate-traffic ledger.

Use the qualified Optimizations environment on one H100 with a working compiler backend. Compilation can take longer than the measured steady-state operations; preserve that cost rather than hiding it in warm-up.

Large Tensor Core operations may already be efficient; launch reduction most often matters around small pointwise, indexing, normalization, or control-heavy regions that surround them.

H100 HBM bandwidth is high, but low-intensity chains can still saturate it. Efficient Tensor Core kernels may require layouts or alignments that justify a conversion only with sufficient reuse.

## Concepts and code path

The program constructs resident inputs, defines one pointwise function, wraps it with compilation, and checks eager/compiled output agreement. It records the first-call wall duration separately, then uses repeated CUDA events for both warmed paths. The source does not contain a memory-layout sweep, and compiler fusion is an outcome to inspect, not assume.

## Practice

### After Lesson 4

Given ten 4-microsecond kernels, each preceded by 8 microseconds of non-overlapped dispatch work, the modeled region takes 120 microseconds. Change to a validated compiled region with two 12-microsecond kernels and 16 microseconds of dispatch. Expected observation: warmed execution takes approximately 40 microseconds under this model, but the report must also include compile time, graph breaks, and the shape range that reuses the artifact.

Run Lab 03 with eager and compiled paths and count launches before comparing time.

### Run the supplied experiment

Run the smoke case to verify compilation and correctness. Repeat the H100 profile as a distinct element-count workload and retain the first-call measurement for each process.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/03_compile_fusion.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/03_compile_fusion.py --profile h100
```

### After Lesson 7

Given a 1-GiB intermediate, separate bias and activation write 1 GiB then read it again in addition to their required input/output traffic. Change to a supported fused epilogue. Expected observation: it can eliminate 2 GiB of logical intermediate traffic and one launch if it actually combines these operations and no other consumer needs the intermediate. This is a traffic ledger, not a measured HBM saving: cache reuse, generated kernels, and transactions determine actual HBM traffic. Confirm fusion in a trace and measure selected-kernel traffic before claiming a physical bandwidth reduction.

Reuse Lab 03's eager/compiled outputs to draw the intermediate-traffic ledger; Lesson 4 owns its first execution and compilation-cost comparison. Preview Lab 10's library inputs, then run its shape/dtype survey in Lesson 9. Neither lab implements a layout sweep. Extension: build a short producer → library operation → consumer pipeline and profile where views become implicit copies. Compare retaining the producer's layout with one explicit conversion at a shared boundary; validate the final outputs, aliasing/mutation behavior and the complete pipeline time. Use Fundamentals Lab 04's packing-cost result as a control, not as a second standalone stride experiment.

## Check your results

Require `allclose`. Compare `compiled_first_call_ms`, `eager`, and `compiled` distributions. A faster warmed call may still lose for a short-lived application that pays compilation only to execute a few times.

Record graph breaks, compilation warm-up, launch count, kernel time, and end-to-end time.

Compilation is useful when it removes or fuses real overhead after the one-time compile cost is separated.

Record strides, inserted copies, kernel count, bytes moved, bandwidth, and end-to-end time.

Layout is an interface contract between operators, not a property to optimize in isolation.

## Investigate the behavior

Count eager intermediates conceptually, then inspect a profiler trace to establish actual launch reduction. Estimate a reuse break-even only when the per-call saving is positive, and state which startup costs your numerator includes.

Larger batches improve amortization but increase latency and memory. Compilation adds startup cost and can specialize excessively. Manual fusion reduces modularity and can increase registers, so retain the simplest layer that meets the target.

Fusion saves traffic and launches but can lengthen live ranges, increase registers, reduce occupancy, duplicate a reusable intermediate, or change numerical order. Packing helps downstream work while costing a full read/write and extra storage.

## If something goes wrong

Compiler failures or numerical disagreement block this candidate. Preserve the failing configuration instead of silently replacing the compiled path with eager execution. Very small arrays may primarily expose launch overhead rather than bandwidth.

Keep compilation outside steady-state timing. This lab uses `fullgraph=True`, so an unsupported graph break fails the candidate; in compilation modes that permit eager fallback, inspect that fallback before interpreting performance.

Calling `contiguous()` everywhere moves cost rather than eliminating it.

## Takeaways and next step

Compilation is a candidate with startup, correctness, and steady-state consequences. Next, compare the broader causal workload in Lab 09 and use evidence to decide whether a remaining hotspot justifies custom code.

Warm up the compiled path, verify graph coverage, and compare steady-state equivalent work.

State when simple batching is preferable to a compiler change.

Select a pipeline-wide layout and fuse only where correctness and maintainability remain clear.

Identify the producer and consumer of every expensive layout conversion.
