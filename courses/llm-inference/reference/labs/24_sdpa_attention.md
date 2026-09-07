# Lab 24: Compare materialized attention with SDPA dispatch

Attention can either materialize a large score matrix or use a more memory-efficient implementation that avoids keeping the entire matrix. This lab compares an explicit reference with PyTorch scaled-dot-product attention for prefill-like and decode-like shapes. You will preserve masks and numerical meaning while inspecting backend dispatch, peak memory, and phase-specific timing.

## Before you start

**Theory preparation:** Read Lesson 2’s attention equation and causal-position rules, Lesson 4’s head/cache shapes and Lesson 11’s materialized versus fused backend selection. Reuse Optimizations Lessons 2–3 and 8 for timing, profiling and incremental peak memory.

Use one H100 in the mechanics environment. Review query length, KV length, head dimension, and causal masking. A decode-shaped query with a longer KV history requires the correct intended mask semantics.

Hopper-aware fused attention paths can use Tensor Cores and specialized data movement, but support is version- and shape-specific. Verify on the pinned PyTorch or engine build.

## Concepts and code path

The source generates Q/K/V tensors and evaluates materialized attention versus SDPA on corresponding inputs. It separately measures warmed timing, incremental peak memory, and profiler dispatch keys. Prefill uses a long query; decode-like work uses a short query with retained history. No complete model, tokenizer, or serving endpoint is involved.

## Practice

Given `S=4096` with 32 heads, a materialized score tensor has hundreds of millions of elements per batch. Change to a supported fused SDPA backend at identical inputs and mask. Expected observation: intermediate memory drops and kernel structure changes; output tolerance, selected backend, and both prefill and decode timing determine acceptance.

Run Lab 24 for prefill-like and decode-like shapes and record the selected backend.

Run both phase shapes together and retain their query/KV lengths and causal flags. The larger profile is a new memory/shape point, not a service workload.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/24_sdpa_attention.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/24_sdpa_attention.py --profile h100
```

## Check your results

Require `all_cases_close` at BF16 `rtol=1e-2, atol=1e-2`. Inspect each case's timing, incremental peak bytes, dispatch keys, and maximum error. The observed backend belongs to the actual shape, dtype, mask, and environment.

Retain shapes, mask, dtype, backend, kernel names, peak memory, time, and numerical error.

One attention backend is not universally best across phases and shapes.

## Investigate the behavior

Estimate the size of the explicit score matrix and compare it with observed incremental memory. Why might avoiding that matrix matter more for prefill than for a one-query decode step? Explain why backend names require profiler evidence.

Fused kernels save traffic but may require padding, impose mask constraints, or use more shared memory/registers. A prefill winner may not be a decode winner. Custom attention belongs only after maintained backends are exhausted.

## If something goes wrong

A mask mismatch changes the mathematical problem and invalidates timing comparison. If the expected backend is unavailable, record actual dispatch or the failure rather than claiming a fused path from the API name alone.

Avoid timing a fallback while assuming a named fused backend executed.

## Takeaways and next step

Attention optimization is phase- and shape-dependent. Use Lab 32 for an independently repeated attention capstone; only a separate live-serving experiment can establish TTFT, inter-token latency, or request throughput effects.

Verify dispatch and evaluate each representative phase separately.

State why KV-cache layout matters more during decode.
