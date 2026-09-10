# Lab 29: Inspect quantized weight and KV storage mechanics

Quantization can reduce stored payload size while adding scale metadata, conversion work, and numerical error. This lab implements symmetric INT8 weight and KV representations and compares their reconstruction with BF16 references. You will learn why a smaller logical representation does not by itself prove lower resident memory, native INT8 execution, or a faster language-model service.

## Before you start

**Theory preparation:** Read Lesson 12 for scales, integer encoding and reconstruction. Fundamentals Lesson 9 introduces precision and relative L2. The nonzero-reference policy and separate weight/KV checks are defined below.

Use one H100 in the mechanics environment. Read the scale and error walkthrough below before interpreting the numerical gates. Both BF16 and INT8 copies remain resident for the A/B checks, so the process is intentionally not memory-minimized.

H100 supports FP8 operations, but INT4/FP4 and engine-specific formats have distinct kernel and architecture boundaries. Blackwell-only NVFP4 must not be presented as an H100 path.

## Concepts and code path

Symmetric quantization divides a value by a positive scale, rounds it to an integer and clamps it to the supported range. Reconstruction multiplies the integer by that scale. With illustrative scale 0.10, value 0.26 becomes integer 3 and reconstructs as 0.30: compression has introduced error 0.04. This hand calculation uses a deliberately simple fixed scale; the lab derives each tensor's scale from its maximum absolute value and the integer limit 127. Weight-operation error can differ from weight-reconstruction error because matrix multiplication combines many reconstructed values.

The L2 norm is the square root of summed squared elements, treating a tensor as one long vector. Relative L2 error is `norm(observed - reference) / norm(reference)` for a nonzero reference norm. This is the lab's actual rule; it does not add epsilon to that denominator. A value below 0.02 limits aggregate relative tensor error, not every element's error and not language-model quality. The generated random references are expected to be nonzero. Testing zero-reference tensors is an extension that must first define an absolute-error or explicitly stabilized-denominator policy.

The program derives scales, quantizes values to INT8, reconstructs them, and compares the resulting weight operation and KV tensors with references. The weight path dequantizes before BF16 computation; it is not a native quantized GEMM kernel. Timing separates weight paths and KV dequantization, while byte reports describe logical payloads and scales.

## Practice

Given a 64-GiB budget for weights, KV cache and workspaces, BF16 weights occupying 14 GiB leave 50 GiB for cache and workspaces. If quantized weights and their metadata occupy 8 GiB under the same memory budget, they leave 6 GiB more for those uses. Change only the weight representation and hold ISL/OSL plus quality set fixed. Expected observation: available cache capacity rises if workspace and other runtime requirements remain unchanged, but latency improves only if the selected H100 kernel consumes the format efficiently; fallback/dequantization evidence explains any regression.

Run Lab 29 to measure logical weight/KV storage, dequantization cost, and relative error. Treat supported engine kernels as a separate advanced profile until an exact image and artifact are qualified.

Run the supplied smoke comparison before changing quantization granularity or scales. A larger shape is a separate numerical and conversion-cost experiment, not automatic evidence of engine memory savings.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/29_quantization.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/29_quantization.py --profile h100
```

## Check your results

Require finite output and both weight/KV relative L2 errors below 0.02. Inspect logical byte accounting, `weight_timing`, `kv_dequantization_timing`, and `memory_scope`. These tensor-error gates are not a language-model quality evaluation.

For a separately qualified quantized-engine experiment, retain method, calibration/scale policy, kernel/backend, memory, TTFT, ITL, throughput, and quality evaluation. The supplied tensor mechanics establish logical storage, conversion costs and numerical error only.

A quantized profile is accepted only for models and shapes supported by the actual engine.

## Investigate the behavior

Calculate the ideal payload reduction, then add scales and reconstructed buffers. Why can dequantize-then-compute be slower than the BF16 baseline? Which supported engine kernel would be necessary to test a true low-precision execution benefit?

Coarser scales save metadata and can increase error; finer scales do the reverse. Weight-only quantization can improve model fit while leaving KV limits unchanged. KV quantization helps long-lived concurrency but can add per-token overhead.

## If something goes wrong

Large reconstruction error can indicate outliers, scale granularity, or saturation. Do not relax the 0.02 threshold after seeing a failure. An OOM with both copies resident does not disprove the logical compression ratio.

Avoid declaring success from checkpoint size without proving runtime memory or quality.

## Takeaways and next step

Separate representation, kernel support, allocation, latency, and quality claims. A production extension requires a pinned supported engine/artifact and actual memory, timing, and task-quality measurements under fixed workload semantics.

Evaluate weights, KV, kernels, latency, capacity, and quality as one system.

Explain which workload benefits most from weight versus KV quantization.
