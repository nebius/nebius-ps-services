# Lab 02: Compare matrix precision, error, and throughput

Reduced precision can unlock faster matrix paths, but a faster multiplication is useful only when its numerical error is acceptable. This lab compares four concrete PyTorch matmul modes on H100. You will separate storage dtype, internal matmul policy, and observed accuracy instead of treating every FP32 tensor as a promise of identical arithmetic.

## Before you start

**Theory preparation:** Read Lab 08 for matrix shapes and GEMM work, then Lesson 9 for floating-point formats, matmul policies, accumulation and relative L2 error. Run the complete four-mode comparison after Lesson 9; a dtype name alone does not identify the arithmetic path.

Use one full H100 and the approved PyTorch environment. Begin with smoke-sized matrices; `--matrix-size` overrides the preset size when a controlled square-matrix comparison is needed. Keep all other settings fixed.

Hopper supports BF16, TF32, FP16, and FP8 Tensor Core paths. H100 is not a Blackwell NVFP4 device; later-generation formats are comparison material, not part of this runnable contract.

## Concepts and code path

A matmul precision policy controls the internal arithmetic allowed for FP32 matrix multiplication without changing its input or output storage dtype. `highest` uses FP32 internal arithmetic. `high` permits supported faster implementations with reduced internal precision, falling back to `highest` when those implementations are unavailable. The setting alone does not prove which kernel was selected.

Matrix multiplication combines rows and columns: multiplying `A[M,K]` by `B[K,N]` produces `C[M,N]`, where each output sums `K` products. Counting a multiplication and an addition as two floating-point operations gives approximately `2*M*N*K` FLOPs, or `2*N^3` for square matrices. For example, a 2-by-3 matrix times a 3-by-4 matrix produces eight outputs with three products each.

The L2 norm is the square root of the sum of squared elements: it measures the length of the flattened output vector. Relative L2 error divides the error vector's norm by the nonzero reference vector's norm. For reference [3, 4] and observed [3, 4.1], the reference norm is 5, the error norm is 0.1 and relative error is 0.02. This summarizes the whole output; it does not bound every element as `allclose` does. The supplied random reference is expected to have nonzero norm. An all-zero reference extension needs a separately declared absolute-error or stabilized-denominator rule, not an undefined division by zero.

The lab constructs matrix inputs and an FP32 highest-policy reference, then runs FP32 with highest matmul precision, FP32 with high precision, BF16, and FP16. That reference matches the FP32-highest baseline; it is not an independent FP64 accuracy oracle. Warmed CUDA-event samples measure each mode. The conventional square GEMM numerator is approximately `2*N^3` FLOPs; achieved TFLOP/s is an accounting rate, not proof of a particular emitted instruction.

## Practice

Given an aligned matrix multiply with BF16 inputs, FP32 accumulation, and a trusted FP32 reference result, measure kernel dispatch and error. Change only the hidden width to an irregular shape. Expected observation: the dtype remains BF16 while kernel choice and time may change; neither result is accepted unless its output stays within the declared tolerance.

Run Lab 02's four implemented modes: FP32 with highest matmul precision, FP32 with high precision, BF16, and FP16. Compare error and timing for each corresponding reference. Keep FP8 as a conceptual introduction here; Training Lab 22 owns the Transformer Engine scaling-recipe, output/gradient, and quality checks needed for a supported FP8 experiment.

Run the supplied survey first. Repeat a declared matrix size in a separate job if you want to examine shape effects without simultaneously changing the precision comparison.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/02_tensor_core_precision.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/02_tensor_core_precision.py --profile smoke --matrix-size 2048
```

## Check your results

Inspect each mode's error report, timing distribution, and `achieved_tflops`. The supplied gate is finite output with relative L2 error below 0.1, a deliberately broad teaching check. It is not the course's stricter elementwise tolerance and does not establish training-quality equivalence.

Record dispatch/profiler evidence, dtype ledger, shapes, time, memory, and maximum/relative error.

A dtype is accepted only when the real operation uses the intended path and meets its quality tolerance.

## Investigate the behavior

Explain why BF16's range differs from FP16's and why a matmul policy can affect FP32 performance. Use a selected profiler report before claiming Tensor Core execution; a throughput number alone cannot identify the instruction path.

Reduced precision lowers memory and compute cost only where supported kernels dominate. Scaling, casts, small shapes, fallback kernels, and quality checks can erase the benefit. Accuracy acceptance belongs to the model or application contract.

## If something goes wrong

Non-finite output invalidates timing interpretation. Reduce input scale or investigate precision behavior as a new trial, not by silently relaxing the gate. If a mode is unsupported, retain the explicit failure rather than substituting a dtype.

Avoid assuming every operation in a mixed-precision region executes on Tensor Cores.

## Takeaways and next step

Precision is an accuracy–performance contract. Add stricter elementwise checks and a task-level loss/quality experiment before accepting a production change. FP8 scaling recipes and gradient behavior belong to the Training course's dedicated lab.

Verify eligible kernels and compare outputs; do not infer acceleration from dtype alone.

Separate storage dtype, input dtype, accumulation dtype, and output dtype.
