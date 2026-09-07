# Lab 10: Survey library behavior across shapes and dtypes

Matrix libraries select implementations partly from shape and precision, so nearby dimensions can produce noticeably different performance. This lab surveys matrix widths and dtypes while checking each result against its corresponding reference. You will distinguish an exploratory survey from a causal optimization that preserves one original problem and its useful output.

## Before you start

**Theory preparation:** Read Lesson 9 and Fundamentals Lesson 9 for shape-dependent dispatch, storage versus compute precision and error checks, using Lesson 2’s timing rules. Lesson 7 is a code/layout preview; run the precision comparison after Lesson 9.

Use one H100 and the approved environment. Review GEMM FLOP accounting and dtype tolerances. Each case creates its own operands; outputs at different widths do not share a single reference tensor.

H100 Tensor Cores support several precisions, but the actual path depends on operation, library, shape, and policy. Verify the chosen kernel on the pinned stack rather than relying on a rule from another release.

## Concepts and code path

The host iterates over the predefined shape/precision cases, creates matching operands, computes an FP32 reference, and times the selected matmul. Results include width, dtype, effective TFLOP/s, and numerical acceptability. The lab does not transpose inputs, test arbitrary strides, or implement a padding/cropping optimization.

## Practice

Given widths 4,095 and 4,096 with the same useful tokens, the larger width may select a more efficient kernel. Change only the padding policy and report useful-token throughput plus padded work. Expected observation: raw kernel time may improve while end-to-end memory or extra computation cancels it; dispatch evidence explains rather than guarantees the result.

Run Lab 10 as a shape-and-dtype survey. Each independently generated matrix pair has its own corresponding FP32 reference; outputs at different widths cannot share one reference tensor. Extension: freeze matrices A[M,K] and B[K,N], zero-pad only K to K', multiply, and compare the unchanged [M,N] output with the original product. Include padding/copy costs and extra FLOPs in the useful-work comparison.

Run the full supplied survey for the selected profile. Keep every case's shape and dtype beside its timing so a faster but mathematically different workload is not presented as a replacement.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/10_shape_precision.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/10_shape_precision.py --profile h100
```

## Check your results

Require finite and numerically acceptable results for all cases. Compare each `cases` entry's timing, error, width, and dtype. Inspect the implemented numerical gate rather than assuming every precision uses identical error criteria.

For the supplied survey retain each shape, dtype, selected precision policy, reference error, and timing. For the fixed-work padding extension additionally retain original/padded K, identical output shape, padding cost, useful FLOPs, and end-to-end time.

Accept padding only when the end-to-end gain exceeds extra arithmetic and capacity cost.

## Investigate the behavior

Which changes alter the actual FLOP count? Which change precision? Explain why a higher TFLOP/s value can coexist with a longer elapsed time if more work is performed.

Padding increases arithmetic, activation/KV memory, and possibly communication. Lower precision can improve capacity and throughput while increasing error or conversion overhead. A friendlier shape is kept only when normalized end-to-end work improves.

## If something goes wrong

An out-of-memory case is a capacity result, not zero throughput. A numerical failure disqualifies that precision for the declared gate. Do not relax the tolerance merely to retain the faster row.

Avoid reporting a fast microkernel that increases total padded tokens in the application.

## Takeaways and next step

Use the fixed-work extension in Practice to decide whether a friendlier shape benefits the original application after padding costs.

Optimize the real shape distribution, not a single ideal matrix.

Explain how padding can simultaneously improve kernel efficiency and reduce system throughput.
