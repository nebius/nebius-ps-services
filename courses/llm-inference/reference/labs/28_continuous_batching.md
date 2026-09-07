# Lab 28: Compare full-prefill and chunked scheduling

Long prompt processing can delay requests that are ready to generate another token. This lab models how full non-preemptible prefill and bounded prefill chunks affect admission opportunities under fixed arrivals. You will trace request progress and conserved work in abstract service quanta before testing the same scheduling idea in a real engine.

## Before you start

**Theory preparation:** Read Lesson 9 for arrivals, admission, continuous batching, chunked prefill, work budgets and conserved work. Lessons 5, 7 and 8 supply workload, latency and cache-capacity context. Distinguish abstract service quanta from measured GPU milliseconds.

Use the mechanics environment and read the [scheduler walkthrough](../lab-mechanisms.md). The wrapper checks H100, but scheduling is a CPU simulation. Its equal-cost work units are deliberately not an H100 latency model.

Mixed prefill/decode batches combine large and narrow shapes with different memory behavior. Engine kernels and scheduler versions determine whether a policy is efficient on the pinned H100 stack.

## Concepts and code path

The simulator tracks arrivals, remaining prompt work, output work, first-output times, and completion. Each quantum supplies 256 abstract work units. Full 2048-unit prefill occupies eight quanta and blocks admission during that dispatch; smaller chunks allow decisions between dispatches. Decode-ready work is prioritized in the chunked policy. A 256-sized chunk control helps distinguish budget effects.

## Practice

Given a 4,096-token prompt arriving while eight decode requests need one token each, prefill-first delays every decode until the prompt completes. Change to 512-token chunks with decode interleaving. Expected observation: decode ITL improves, while prompt TTFT and total kernel/scheduler overhead may rise; keep the policy only against the weighted workload and SLOs.

Run Lab 28 with fixed arrivals and 256 abstract work units per service quantum. Compare full non-preemptible prefill, 64-token chunks, and a 256-token chunk-size control. A full 2048-token prefill occupies eight quanta, not one free scheduling tick. Arrivals during that interval wait; bounded chunks allow admission between dispatches. Inspect per-request first-token latency and conserved token work using the [scheduler walkthrough](../lab-mechanisms.md). Then run `slurm/vllm_chunked_prefill_ab.sbatch` for three independent fixed-ISL/OSL streaming trials per policy. The launcher restarts the engine for every trial, alternates policy order, owns readiness and cleanup, and records separate AIPerf artifacts; H100 results remain a completion gate.

Run the fixed-arrival fixture before editing policies. All policies must complete the same declared work; simulator time is reported in quanta, not milliseconds.

```bash
umask 077
sbatch slurm/single_gpu.sbatch labs/28_continuous_batching.py --profile smoke
```

## Check your results

Require equivalent scheduled work and completion of every request. Inspect each policy's trace, dispatch duration, first-output latency, maximum active requests, and completion times. These modeled latencies are not measured service TTFT or token-aware ITL.

For the separate live-engine campaign, retain queue depth, scheduled prefill/decode tokens, batch composition, TTFT, ITL, throughput, and memory. Keep those measurements separate from the simulator’s abstract service-time records.

The chosen policy should meet latency objectives at the required offered load and workload mix. The simulator assigns equal abstract cost to prefill and decode tokens and excludes real launch, kernel and scheduler costs. Its trace teaches admission and blocking, not milliseconds or vLLM internals. Compare modeled service time rather than dispatch count alone, then test the hypothesis with the real engine.

## Investigate the behavior

Locate an arrival during a long full-prefill dispatch and calculate its waiting time. How does chunking change the next admission opportunity? Explain which conclusions depend on the model's equal unit-cost assumption.

Smaller chunks improve decode responsiveness but may reduce prefill throughput and increase launches. Larger token budgets increase throughput but can worsen queueing and KV pressure. Fairness can conflict with maximum aggregate rate.

## If something goes wrong

Long prefill that consumes no modeled time, or output work that disappears, indicates a broken time/work model. Recheck dispatch duration and request completion before comparing policies. Do not convert quanta to GPU milliseconds without an independently justified model.

Avoid comparing policies at different admitted request rates or silently dropping overload.

## Takeaways and next step

Scheduling changes who waits as well as aggregate progress. After engine qualification, run `slurm/vllm_chunked_prefill_ab.sbatch` for independent streaming/AIPerf trials and Lab 34's paired output checks; only those runs support live-service claims.

Hold arrivals fixed and report completions, failures, queue growth, and service-level goodput.

Explain the latency/efficiency tradeoff in prefill chunk size.
