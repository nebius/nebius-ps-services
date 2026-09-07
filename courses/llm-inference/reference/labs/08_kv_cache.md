# Lab 08: Compare cached attention with full recomputation

Autoregressive generation repeatedly needs keys and values for earlier positions. A KV cache preserves those tensors so each new step can reuse them instead of recomputing the entire prefix. This lab checks cached and recomputed attention results on a small local model, helping you connect saved arithmetic to the cache memory that remains resident.

## Before you start

**Theory preparation:** Read Lessons 2 and 4 for Q/K/V, scaled dot-product attention, causal positions, cached versus recomputed continuation and MHA storage. Lesson 1 supplies inference mode and fixed parameters. Lesson 2 is a shape preview; run the complete comparison in Lesson 4.

Use one H100 in the mechanics environment. No external model download is required. Review causal attention and the distinction between query heads and stored key/value heads.

## Concepts and code path

The program constructs a small attention workload, forms prompt keys/values, and validates the cached prompt result against full attention. For continuation, it updates the cache and compares cached computation with recomputation over the corresponding prefix. Repeated timings contrast both strategies. The implementation uses a fixed MHA layout; it is not a paged engine allocator or an MHA/GQA/MQA sweep.

Construction, correctness checks, warm-up and both timed paths run inside
`torch.inference_mode()`. No backward graph is retained with the prompt cache
or the generated continuation. This keeps the experiment's computation and
memory behavior within fixed-parameter inference.

## Practice

Run the supplied paired mechanics first. The larger profile changes prompt/generation work and should remain a separately declared point in your cache-memory and timing analysis.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/08_kv_cache.py --profile smoke
sbatch slurm/single_gpu.sbatch labs/08_kv_cache.py --profile h100
```

## Check your results

Require prompt and continuation agreement with the full-attention reference. Inspect `prompt_max_absolute_error`, `decode_max_absolute_error`, `key_value_cache_mib`, and cached/recompute distributions. Cache bytes here describe the implemented tensors, not an engine's complete reservation.

Both maximum absolute errors must be finite and at most `0.02`. NaN or infinity
in either compared result rejects the experiment before timing or publication;
a non-finite comparison is never evidence of agreement.

## Investigate the behavior

Identify which keys/values are newly computed at a continuation step and which are reused. Explain why longer histories increase attention reads even when the earlier projections are cached.

## If something goes wrong

A mismatch appearing only after continuation suggests cache position, concatenation, or mask alignment. Verify these before changing tolerances. Memory estimates must include both K and V and the actual stored dtype.

## Takeaways and next step

Caching exchanges repeated computation for persistent state and growing reads. Next, use Lab 26 for ideal head-layout capacity calculations and Lab 27 for logical-to-physical page ownership; neither substitutes for real-engine allocation measurements.
