# Lab 08: Map PyTorch operations to GPU activity

One line of tensor code can trigger several framework operations and many GPU kernels. This lab profiles a small projection, normalization, and activation chain so you can relate high-level intent to execution cost. It is an introductory evidence-reading exercise: the output helps you choose what to inspect next, not prove an optimization by itself.

## Before you start

**Theory preparation:** Read Lesson 2 for framework, runtime and driver responsibilities. The operation chain and profiler vocabulary are defined below. Lab 01 explains warm-up and completed-work timing; Lesson 3 adds the physical hierarchy.

Use one H100 and an environment in which PyTorch Profiler can capture CPU and CUDA activity. Keep profiler and scheduler output private. Run this before the deeper Nsight exercises in GPU Optimizations.

## Concepts and code path

A matrix multiplication combines rows and columns: shapes M×K and K×N produce M×N values. For example, `[1, 2]` times the column `[3, 4]` gives 11. The multiply-plus-add convention counts approximately `2*M*N*K` floating-point operations. GEMM names general matrix multiplication, often written `C = alpha*A*B + beta*C`. In this lab, Python `@` performs the projection; `*` multiplies corresponding elements. A projection changes feature coordinates; broadcasting applies the same bias vector to every output row.

Layer normalization subtracts a feature vector's mean and divides by the square root of its variance plus a small epsilon. Learned scale and offset can follow. GELU, the Gaussian error linear unit, is the nonlinear activation `x*Phi(x)`, where Phi is the standard normal cumulative probability: it attenuates strongly negative inputs and approaches the identity for strongly positive ones. Activations are intermediate values; an activation function transforms them. BF16 is a 16-bit format with a wide exponent range and fewer precision bits than FP32. Lesson 9 develops that accuracy trade-off.

A profiler records execution activity and groups related events. Self time excludes recorded child events; inclusive time includes them. For example, a parent with 10 microseconds of inclusive time and a 7-microsecond child has 3 microseconds of self time under that nesting. Adding parent-inclusive and child time would double-count the child. GPU overlap and framework event attribution also mean a table sum is not automatically application wall time.

CPU and CUDA activity collection associates framework operations with the kernels they submit. Profiling adds overhead; use an unprofiled timer for a performance comparison.

The workload multiplies BF16 activations by weights, applies layer normalization, adds bias, applies GELU, then squares and averages the output. Warm-up precedes the profiled loop. The profiler aggregates events and the program selects the ten largest self-CUDA-time entries. Although the scalar is named `loss`, this workload has no backward pass or optimizer step.

## Practice

Start with smoke shapes to learn the event table. Use the larger profile only as a separately declared shape comparison; retain the shape in your notes.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/08_operator_to_kernels.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/08_operator_to_kernels.py --profile h100
```

## Check your results

Require a finite scalar and captured CUDA events. Inspect `unique_profile_events`, `cuda_events_with_device_time`, and each top event's `calls` and `self_cuda_time_us`. These gates confirm executed work, not numerical equivalence against a reference.

The profiler reads PyTorch's `self_device_time_total` field from averaged
events and reports the captured CUDA device durations as `self_cuda_time_us`.

## Investigate the behavior

Match each expensive event to the expression that can cause it. Distinguish self time from inclusive time before summing rows. Ask whether repeated small launches or one large GEMM dominates the profile.

## If something goes wrong

An empty CUDA table is missing evidence, not proof that the workload costs nothing. Check CUDA activity support and profiler warnings. If event attributes differ in your qualified environment, record the API mismatch before modifying the reporting code.

## Takeaways and next step

Operator names provide a starting map, not a one-to-one kernel identity. Next, select a focused workload in GPU Optimizations and combine a timeline with uninstrumented timings before deciding which operation deserves attention.
