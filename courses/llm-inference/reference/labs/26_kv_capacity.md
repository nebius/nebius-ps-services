# Lab 26: Calculate ideal KV storage for MHA, GQA, and MQA

KV-cache capacity depends on stored key/value heads, not simply the number of query heads. This lab calculates ideal storage for multi-head, grouped-query, and multi-query attention and checks a small tensor allocation's byte arithmetic. You will learn to estimate whether a proposed workload is plausible before allocating it, while keeping logical tensor bytes distinct from full engine memory.

## Before you start

**Theory preparation:** Read Lesson 4 for MHA/GQA/MQA, K/V head counts, head dimension, dtype bytes and the cache-capacity formula. Use Fundamentals Lesson 4’s distinction between logical tensor bytes and allocator behavior before interpreting the one-token probe.

Use one H100 and review the KV formula. The declared model has 32 layers, 32 query heads, head dimension 128, and two-byte elements. Sequence length and request concurrency are configurable positive values.

Use the actual H100 HBM capacity and selected KV dtype. FP8 KV support and kernels are engine/version-specific; do not infer support from hardware format capability alone.

## Concepts and code path

The script computes bytes per sequence position as `2 * layers * kv_heads * head_dim * dtype_bytes`, where the leading two accounts for K and V. It scales that value by sequence and concurrency for each head layout. A one-token GQA tensor probe checks logical byte accounting; it does not allocate every full configuration.

## Practice

Given 32 layers, 8 KV heads, head dimension 128, BF16 K/V, ideal KV is `32×8×128×2×2 = 131,072` bytes or 128 KiB per token. At 8,192 tokens one request uses about 1 GiB ideal KV. Change from 32 KV heads to 8 with the same query heads. Expected observation: ideal KV drops fourfold, but measured free capacity is lower after weights, block rounding, workspaces, graph pools, and reserve.

Run Lab 08 for fixed-MHA cached-versus-recomputed generation mechanics. Run Lab 26 for MHA/GQA/MQA ideal KV calculations and its one-token GQA logical-byte probe; neither run measures full sequence-cache allocator usage for every layout. Extension: allocate K/V tensors for each declared layer/head/sequence/concurrency configuration, synchronize and record allocated/reserved memory before and after, and separate tensor bytes from allocator and workspace overhead. Reduce the declared workload before allocation if its estimate exceeds available memory.

Vary sequence length or concurrency independently. These commands change arithmetic estimates, not the size of a fully materialized production cache for every case.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/26_kv_capacity.py --profile smoke --sequence 4096 --concurrency 8
sbatch slurm/single_gpu.sbatch labs/26_kv_capacity.py --profile smoke --sequence 8192 --concurrency 8
```

## Check your results

Require `probe_bytes_match`. Inspect each case's KV-head count, bytes per token, and ideal GiB, plus the list of excluded runtime overheads. Ideal capacity excludes weights, workspaces, allocator reservation, fragmentation, and engine metadata.

Retain formula inputs, bytes/token, sequence lengths, concurrency, block overhead, and observed memory.

Capacity planning must reserve weights, temporaries, graph pools, fragmentation, and runtime headroom in addition to ideal KV bytes.

## Investigate the behavior

Explain why doubling context doubles ideal KV bytes and why reducing KV heads can lower storage without reducing query-head count. Which terms must be adjusted for quantized KV and its scale metadata?

GQA/MQA and lower KV precision increase capacity but can change architecture or quality. Reserving a larger KV pool raises admission capacity while leaving less workspace/headroom and increasing the cost of cache pressure.

## If something goes wrong

An estimate larger than available memory is a reason to reduce the declared workload before allocation. Check GiB versus GB and the K/V factor before treating an unexpected number as an engine defect.

Avoid using query-head count instead of KV-head count for grouped-query models.

## Takeaways and next step

Capacity arithmetic is a planning tool, not a measured fit result. Extend with safe bounded allocations and baseline/peak allocator readings, then validate a real engine's reservation and admission behavior separately.

Compute ideal KV first, then add allocator and engine overhead from measurement.

Derive KV bytes/token for a model with 32 layers, 8 KV heads, head size 128, and BF16 storage.
