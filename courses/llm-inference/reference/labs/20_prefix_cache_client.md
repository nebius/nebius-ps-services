# Lab 20: Investigate reusable prompt prefixes in a live engine

Repeated prompt prefixes can reuse previously computed KV state, but visible text repetition alone does not prove a cache hit. This lab compares repeated-prefix and unique-prefix request cohorts against a local engine. You will inspect prompt-token comparability and engine cache evidence, then separate client observations from the controlled cache-enabled/cache-disabled campaign that establishes a causal comparison.

## Before you start

**Theory preparation:** Read Lessons 6–7 for server lifecycle and client metrics, then Lesson 10 for exact prefix identity, cold/warm cohorts, cache policy and paired output equivalence. Run the CPU retention model first and use the owning live launcher for the independent engine trials.

Qualify the vLLM image, client environment, and immutable model revision. Use the prefix-cache launcher, which owns engine restarts, policy variants, metrics, and cleanup. Keep prompts and cache-related raw artifacts private.

Prefix reuse saves H100 prefill compute and KV writes but keeps HBM occupied. The value depends on reuse frequency, prefix length, active load, and engine block/hash implementation.

## Concepts and code path

The Python client builds repeated and unique prefix cohorts, sends bounded greedy requests, and records latency and generated-token counts. It checks that prompt-token cohorts are comparable. The owning launcher supplies the broader disabled/enabled experiment, repeats independent engine trials, and invokes the separate output-equivalence helper. Client cohort timing alone cannot establish cache causality.

## Practice

Given a 1,024-token shared system prefix stored as 64 blocks of 16 tokens, a second identical request can reuse all 64. Change token 257. Expected observation: only the first 16 complete blocks remain reusable, later blocks must be recomputed, and output equivalence plus cache-occupancy cost determines whether retention is worthwhile.

Run Lab 20's repeated-prefix and unique-prefix cohorts through `slurm/vllm_prefix_cache.sbatch` for the cache-disabled/cache-enabled comparison. The launcher performs three independent engine restarts per policy and alternates disabled/enabled order. As an extension, add a token-controlled near-match cohort and inspect which complete blocks remain reusable.

Inspect the launcher options and run its default bounded campaign only after engine qualification. Each policy receives independent restarts and the run records must remain separate.

```bash
umask 077
bash slurm/vllm_prefix_cache.sbatch --help
sbatch slurm/vllm_prefix_cache.sbatch
```

## Check your results

Require nonempty responses and comparable prompt-token cohorts. Inspect repeated/unique cohort summaries, `prompt_token_ratio`, engine cache queries/hits, and launcher-owned paired output checks. Startup and cache priming must not be silently mixed with steady-state requests.

Record tokenized prefix identity, hit/miss, reused tokens, TTFT, memory, model revision, and adapter identity.

Benefits depend on repetition and cache residency; cache memory competes with active-request capacity.

## Investigate the behavior

Which exact token prefix is shared, and where does a near-match diverge? Explain why model/tokenizer identity and cache policy must be fixed. Distinguish shorter prompt work from genuinely reused computation.

Retaining more prefixes improves hit opportunity while reducing free KV and possibly increasing eviction churn. Cross-tenant reuse can create privacy or timing concerns and needs explicit policy.

## If something goes wrong

No observed hit may reflect token differences, insufficient reusable blocks, eviction, or disabled caching. Investigate engine metrics before inferring a broken cache. A prompt-size imbalance invalidates the intended cohort comparison.

Avoid comparing visible strings instead of exact token IDs and model context.

## Takeaways and next step

Cache claims require identity, workload, and engine evidence together. Repeat the controlled campaign across at least three independent trials and carry its quality gate into any application-level prefix reuse experiment.

Key cache entries by every semantic input that changes KV and validate hit correctness.

List conditions that should invalidate a prefix entry.
