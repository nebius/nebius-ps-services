# Lab 20: Investigate reusable prompt prefixes in a live engine

Repeated prompt prefixes can reuse previously computed KV state, but visible text repetition alone does not prove a cache hit. This lab compares repeated-prefix and unique-prefix request cohorts against a local engine. You will inspect prompt-token comparability and engine cache evidence, then separate client observations from the controlled cache-enabled/cache-disabled campaign that establishes a causal comparison.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/20_prefix_cache_client.json).

Qualify the vLLM image, client environment, and immutable model revision. Use the prefix-cache launcher, which owns engine restarts, policy variants, metrics, and cleanup. Keep prompts and cache-related raw artifacts private.

Prefix reuse saves H100 prefill compute and KV writes but keeps HBM occupied. The value depends on reuse frequency, prefix length, active load, and engine block/hash implementation.

## Concepts and code path

The Python client builds repeated and unique prefix cohorts, sends bounded greedy requests, and records latency and generated-token counts. It checks that prompt-token cohorts are comparable. The owning launcher supplies the broader disabled/enabled experiment, repeats independent engine trials, and invokes the separate output-equivalence helper. Client cohort timing alone cannot establish cache causality. The launcher enables `VLLM_BATCH_INVARIANT=1` and selects `TRITON_ATTN` for every policy and phase. Greedy decoding alone does not prevent floating-point differences when cache hits change attention shapes. Keep these settings fixed and retain the exact output-equivalence gate; timings describe this qualified configuration, not every attention backend.

Given a 1,024-token shared system prefix stored as 64 blocks of 16 tokens, a second identical request can reuse all 64. Change token 257. Expected observation: only the first 16 complete blocks remain reusable, later blocks must be recomputed, and output equivalence plus cache-occupancy cost determines whether retention is worthwhile.

Run Lab 20's repeated-prefix and unique-prefix cohorts through `slurm/vllm_prefix_cache.sbatch` for the cache-disabled/cache-enabled comparison. Each policy receives three fresh-server correctness probes followed by three fresh-server cohort measurements. The launcher alternates disabled/enabled order within each phase. As an extension, add a token-controlled near-match cohort and inspect which complete blocks remain reusable.

## Practice

Run the experiment commands on the login node. Save the printed JSON paths; job submission alone is not a result.

Inspect the launcher options and run its default bounded campaign only after engine qualification. Each policy receives independent restarts and the run records must remain separate.

```bash
umask 077
bash slurm/vllm_prefix_cache.sbatch --help
python3 tools/submit_lab.py --lab 20_prefix_cache_client slurm/vllm_prefix_cache.sbatch
```

Keep a fixed profile for a comparison. If both profiles appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

## Check your results

After the submitted job completes, inspect its state and measured results on the login node. The second command prints the exact JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
sacct -j "${LAB_JOB_ID:?submitted job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 20_prefix_cache_client --job "$LAB_JOB_ID"
```

Require nonempty responses and comparable prompt-token cohorts. Inspect repeated/unique cohort summaries, `prompt_token_ratio`, engine cache queries/hits, and launcher-owned paired output checks. Startup and cache priming must not be silently mixed with steady-state requests.

Record tokenized prefix identity, hit/miss, reused tokens, TTFT, memory, model revision, and adapter identity.

Benefits depend on repetition and cache residency; cache memory competes with active-request capacity.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Repeated prefix / median e2e (seconds) | `repeated_prefix.median_e2e_ms` | `s` |
| Unique prefix / median e2e (seconds) | `unique_prefix.median_e2e_ms` | `s` |
| Repeated prefix / median prompt tokens | `repeated_prefix.median_prompt_tokens` | `none` |
| Prompt token ratio | `prompt_token_ratio` | `none` |

Select the successful, unprofiled cache-disabled and cache-enabled cohort artifacts from the same trial and profile, after the paired greedy-output check passes. Each artifact includes both repeated-prefix and unique-prefix cohorts; compare matching cohorts across the two policy slots. Retain the other trials separately to assess variation. On the login node, set the paths to the selected cohort result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 20_prefix_cache_client \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

Which exact token prefix is shared, and where does a near-match diverge? Explain why model/tokenizer identity and cache policy must be fixed. Distinguish shorter prompt work from genuinely reused computation.

Retaining more prefixes improves hit opportunity while reducing free KV and possibly increasing eviction churn. Cross-tenant reuse can create privacy or timing concerns and needs explicit policy.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 20_prefix_cache_client --export=ALL,COURSE_PROFILE_TOOL=nsys slurm/vllm_prefix_cache.sbatch
```

The launcher profiles the **GPU server**, while the client measures requests. Open the emitted `.nsys-rep` in Systems; expand CUDA API, GPU kernels, copies, and worker-process rows. The launcher triggers `/start_profile` after server readiness and `/stop_profile` after the request campaign, using the engine’s CUDA profiler API. Match that interval to the client artifact timestamps. Require actual request activity inside the capture; initialization alone is insufficient. Server NVTX availability depends on the pinned engine; use its CUDA kernels and request interval when named phases are absent.

Guided comparison: Compare prefix caching disabled/enabled for the same request cohorts and three counterbalanced trials. Independently contrast repeated-prefix and unique-prefix results; choose caching only after the paired greedy-output check passes.

**Nsight Systems evidence:** Capture the owned GPU server and its worker descendants while the client supplies requests. Open the server .nsys-rep and expand the GPU worker process tree, CUDA streams and runtime NVTX rows. Inspect the request interval after readiness; client-only activity or startup-only kernels are not sufficient. Compare launch gaps and prefill/decode activity against the clean client latency panels. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

No observed hit may reflect token differences, insufficient reusable blocks, eviction, or disabled caching. Investigate engine metrics before inferring a broken cache. A prompt-size imbalance invalidates the intended cohort comparison.

Avoid comparing visible strings instead of exact token IDs and model context.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Cache claims require identity, workload, and engine evidence together. Repeat the controlled campaign across at least three independent trials and carry its quality gate into any application-level prefix reuse experiment.

Key cache entries by every semantic input that changes KV and validate hit correctness.

List conditions that should invalidate a prefix entry.
