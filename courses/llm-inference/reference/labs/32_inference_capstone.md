# Lab 32: Build a causal attention optimization report

This capstone deliberately reuses Lab 24's explicit-attention versus SDPA comparison with the same BF16 tensors and mathematical mask. Lab 24 establishes backend and numerical behavior; the new competency here is making a defensible decision from independent trials, reversed variant order and an aggregate result that passes evidence validation. You will verify output equivalence and collect independent timing/memory trials before explaining the observed mechanism. The required deliverable is an attention microbenchmark report; online latency and throughput require a separate qualified serving campaign with its own workload contract.

## Before you start

**Theory preparation:** Read Lesson 11’s attention-backend comparison and Lesson 16’s independent, counterbalanced causal trial procedure. Reuse Lessons 2 and 4 for masks/shapes and Optimizations for event timing, memory and focused profiling. Complete Lab 24 before the attention-only capstone.

Use one H100 and complete Lab 24. Each invocation is one fresh-process trial; the campaign launcher runs three with alternating variant order. Keep raw results and any profiler artifacts private.

No latency, throughput, engine-activation, or scaling claim is complete until measured on the declared H100 nodes with the exact pinned runtime. CPU simulations check their modeled calculations; separate client tests check protocol handling. Neither establishes live engine or service performance.

## Concepts and code path

The lab builds corresponding baseline and SDPA callables, compares outputs, warms each path, and records CUDA-event distributions and incremental peak allocation. It emits a provisional observation with the variant order. The source's workload labels describe the bounded attention shape; no HTTP server, queue, tokenizer, or autoregressive model loop is launched.

## Practice

Given a mixed workload whose baseline reaches 12,000 tokens/s but violates p95 ITL during long prefills, enable chunking and observe 11,500 tokens/s with ITL inside the SLO and unchanged quality. Change to a short-prompt-only workload where chunking adds overhead. Expected observation: keep a workload-specific profile rather than a universal setting; pending live campaigns remain explicitly pending.

Required mechanics deliverable: submit slurm/capstone_three_trials.sbatch for three fresh-process Lab 32 attention comparisons with alternating variant order, correctness, memory, repeated CUDA-event timing, and a profiler-backed explanation. Lab 32 does not launch a service or measure TTFT/ITL. Conditional serving deliverable: after engine/environment qualification, use slurm/vllm_chunked_prefill_ab.sbatch for the supported chunking A/B campaign, following its profile and benchmark-client instructions. Run baseline/candidate campaigns at declared loads and keep at least three independent trials per comparison. Only the live campaign can support service-latency, throughput, failure, or quality conclusions.

Use the campaign launcher for the three-process comparison. Profile the same attention workload separately if needed to explain dispatch or memory behavior; instrumented duration is not acceptance timing.

```bash
umask 077
python labs/32_inference_capstone.py --help
sbatch slurm/capstone_three_trials.sbatch --profile smoke
```

## Check your results

Require output allclose at BF16 `rtol=1e-2, atol=1e-2` in every trial. Inspect maximum error, variant order, distributions, incremental peaks, and provisional/publication status. No single trial can establish a repeatable campaign result.

The aggregator requires complete, matching profile, H100/software, workload and
measurement-option fields. Every timing and the derived ratio must be finite
and positive; missing fields, boolean timings and non-finite values cannot
produce a keep decision.

Keep separate records. Mechanics: input shape/dtype/mask, reference error, variant order, raw timing samples, memory, selected kernels, trace, and keep/reject decision. Serving: immutable model/engine configuration, ISL/OSL and load, sampling/stops, output/quality checks, TTFT/ITL or documented proxies, completion latency, throughput, failures, queue/cache metrics, and independent trials. Mark serving evidence pending if no live engine ran.

Publish only claims demonstrated by the declared model, engine, H100 nodes, and workload.

## Investigate the behavior

Does the measured memory difference match the materialized score-matrix explanation? Does the timing conclusion survive order reversal? Explain how a large attention-kernel improvement could have a smaller end-to-end service impact.

The best aggregate-throughput setting may not maximize SLO goodput. More KV reservation can reduce workspaces; chunking can trade prompt throughput for decode latency; quantization can trade quality or kernel support for fit.

## If something goes wrong

Mask or numerical disagreement rejects the candidate. Missing trials, changed shapes, or unsupported backends prevent a comparable aggregate. Preserve unfavorable observations instead of selecting only the fastest candidate sample.

Avoid tuning to one benchmark point while ignoring overload, failure rate, or quality.

## Takeaways and next step

Deliver a bounded mechanism-backed attention report. For service claims, separately qualify and run the chunked-prefill/AIPerf campaign with fixed arrivals, ISL/OSL, quality gates, and at least three independent engine trials.

Report the operating envelope and reject any candidate that violates correctness or service constraints.

State the strongest supported claim and one unsupported generalization.
