# Lab 09: Follow real-model prefill and continuation

The first generated token normally comes from the final-position logits produced by prefill. Subsequent tokens require continuation passes that consume the previously selected token. This lab runs that schedule explicitly with a pinned Hugging Face model, letting you inspect input length, attention-mask growth, cache positions, and the difference between generated-token count and decode-pass count.

## Before you start

**Theory preparation:** Read Lessons 1–2 for immutable artifacts, Transformers, tokenization, SDPA, DynamicCache, greedy first-token selection and N−1 continuation passes. Use the prerequisite Optimizations timing lesson to mark device completion, and audit the model with Lab 16 first.

Qualify the mechanics environment and the approved immutable model/tokenizer artifact. Use one H100 and keep generated data private. Review artifact auditing in Lab 16 before choosing a different model.

H100 Tensor Cores can efficiently process large prefill matrices; single-token decode often uses narrower matrices and repeatedly streams weights/KV, so the same backend need not be optimal for both phases.

## Concepts and code path

The script tokenizes the prompt, performs a prefill pass with cache creation, and selects the first token from its logits. Each later pass supplies the last selected token and advances mask/cache state. For N requested new tokens, the schedule uses one prefill and N−1 continuation passes. Checks enforce that schedule rather than merely accepting nonempty text.

## Practice

Given a 4,096-token prompt and 32-token output, most matrix-rich work occurs before the first token. Change to a 128-token prompt and 1,024-token output. Expected observation: prefill shrinks, the sequential decode loop dominates completion time and KV lifetime, and transport chunks must be reconciled with server token timestamps before calling their gaps ITL.

Run Lab 09 and annotate phase boundaries, shapes, synchronizations, and output tokens.

Use one-token and multi-token cases to expose the boundary. The one-token request should not require a separate decode pass after prefill.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/09_hf_prefill_decode.py --profile smoke --new-tokens 1
sbatch slurm/single_gpu.sbatch labs/09_hf_prefill_decode.py --profile smoke --new-tokens 8
```

## Check your results

Require the requested token count and advancing mask, cache position, and cache length. Inspect `decode_steps_per_request`, `prefill_timing`, and `decode_timing`; the latter is absent when no continuation pass is needed. These device-phase timings are not client-observed TTFT.

Lab 09 records prompt length, a fixed output-token budget, generated-token counts, decode-forward counts, output shape, and prefill/decode forward-time summaries. It deliberately runs to that budget rather than stopping on EOS, and does not report end-to-end generation time or token arrival gaps. Investigate stopping policy and client-boundary observations separately with the serving/streaming labs; use token-aware engine measurements when reporting ITL or TPOT.

Optimize the phase that dominates the target workload, not an average request that hides ISL and OSL.

## Investigate the behavior

Draw which token is input to each pass and which token is sampled from its output. Why is the last generated token not necessarily already represented in the returned cache when generation stops?

Larger scheduling batches improve decode throughput but add queueing and per-user delay. Streaming improves perceived latency while adding protocol overhead and making chunk gaps an imperfect proxy for token ITL.

## If something goes wrong

An off-by-one cache length or mask length invalidates the schedule. Unsupported cache APIs require artifact/environment qualification, not silently discarding cache checks. Do not confuse an EOS-aware service policy with this fixed-token mechanics contract.

Avoid reporting one end-to-end number without separating queue, prefill, and decode.

## Takeaways and next step

Phase boundaries must follow actual model execution. Use the serving and streaming clients to add queueing, transport, and delivery observations; do not label a device-only prefill sample as end-to-end service latency.

Preserve the full phase decomposition and token counts with each result.

Explain why prefill and decode prefer different batching and kernels.
