# Lab 22: Qualify a warmed Transformer Engine FP8 recipe

FP8 execution relies on scaling and quantization state, not simply changing a tensor dtype. This optional lab compares BF16 and FP8 forward/backward behavior for a Transformer Engine linear layer using a delayed-scaling recipe. You will inspect numerical samples before and after timing so a favorable warmed result cannot conceal unstable recipe behavior.

## Before you start

**Theory preparation:** Read Lesson 7 for Transformer Engine, FP8 formats, amax history, delayed scaling, warm recipe state and output/gradient checks. Reuse Lessons 4 and 6 for differentiation and incremental peak memory. Qualify the optional dependency before this separate linear-layer experiment.

Qualify an exact compatible Transformer Engine build on H100 before running. This optional dependency is not a core-course completion gate. Its `te.Linear` workload differs from Lab 21's tiny transformer, so their timings are not a matched-model comparison.

## Concepts and code path

The implementation uses `te.autocast` with a delayed-scaling recipe and amax history. It establishes matched linear-layer comparisons, warms scaling state, captures output/gradient error samples, and measures repeated execution. Memory accounting distinguishes steady-state allocation from incremental peak. Quantized representations and scaling metadata contribute to the actual memory footprint.

## Practice

Inspect the output/gradient threshold options and run the smoke profile only after environment qualification. Record the exact recipe and installed version with the result.

```bash
umask 077
python labs/22_transformer_engine_fp8.py --help
sbatch slurm/single_gpu.sbatch labs/22_transformer_engine_fp8.py --profile smoke
```

## Check your results

Require finite outputs, gradients, and errors for every numerical sample. Default maximum relative L2 errors are 0.2 for outputs and 0.3 for gradients. Check the warmed validation state before and after timing, not only the best sample.

## Investigate the behavior

Explain what an amax history estimates and why early calls can differ from warmed calls. Compare numerical stability, timing, and incremental memory. Why does a short linear-layer experiment not establish transformer training quality?

## If something goes wrong

Missing FP8 support, incompatible packages, or recipe failures leave the extension pending. Do not substitute deprecated autocast examples or silently run BF16 while labeling it FP8. Preserve failed numerical samples.

## Takeaways and next step

An FP8 claim belongs to an exact recipe, workload, and validated environment. A further experiment should introduce changing activation scales and a real training objective while keeping explicit output, gradient, and quality gates.
