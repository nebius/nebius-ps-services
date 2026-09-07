# Inference benchmark record

Record model, tokenizer, immutable revisions, engine/image, generation settings,
ISL/OSL distribution, concurrency, arrival model, warm-up, completions/failures,
TTFT, ITL/TPOT definition, request and token throughput, goodput objective,
weight/KV memory, cache behavior, output/quality checks, profiler evidence, and
keep/reject decision. Keep prompts, outputs, raw logs, and host details private.

## Comparison worksheet

Copy this worksheet into the private experiment record before changing one
factor. Fill both columns for equivalent work; mark unavailable evidence
explicitly. Retain raw artifacts privately and publish only separately reviewed
aggregates following [evidence-security.md](evidence-security.md).

| Field | Baseline | Candidate / observation |
| --- | --- | --- |
| Date and random course run ID (keep artifact location private) | To record | To record |
| Lab, source/commit identity, exact arguments, single change | To record | To record |
| H100 SKU, node/rank count, MIG state, topology class (no asset IDs) | To record | To record |
| Approved environment lock or immutable image; driver/CUDA/framework/NCCL/Slurm versions | To record | To record |
| Input shapes, layouts, dtype, seeds, batch/workload semantics | To record | To record |
| Primary metric, completion boundary, correctness/quality tolerances | To record | To record |
| Warm-up, measured iterations, three independent trial IDs and order | To record | To record |
| Correctness result and maximum error; missing/non-finite data | To record | To record |
| Raw timing distribution, minimum, median, p90/p95 and dispersion | To record | To record |
| Baseline allocated memory, peak allocated/reserved memory, incremental peak | To record | To record |
| Sanitized profiler observation, selected range/kernel, tool version | To record | To record |
| Supporting observation and disconfirming control | To record | To record |
| Decision: keep, reject, or investigate; limitation and next experiment | To record | To record |
| Artifact/tokenizer/template immutable revisions, engine/container and quantization metadata | To record | To record |
| ISL/OSL distribution, workload/license, arrival pattern, concurrency, sampling and stops | To record | To record |
| TTFT, ITL/TPOT, end-to-end p50/p90/p99, request and output-token throughput | To record | To record |
| Output token totals, finish reasons, errors, quality/correctness and SLO goodput | To record | To record |
| Weights/KV/workspace/graph-pool memory; queue, cache hits and preemptions | To record | To record |
| Lab 27 physical block IDs, growth, rejected-reservation state, release and exact reuse | To record | To record |
| Lab 28 modeled service quanta, arrival-relative first token, conserved work, full/chunked dispatch trace; not engine milliseconds | To record | To record |
| Cold startup versus readiness/steady state; replication/TP/PP/EP placement | To record | To record |
| Lab 36 tier capacities, TTL/LRU, namespace/restart, hit source, modeled foreground/write costs and assumed bandwidth; no engine timing | To record | To record |

A faster result is accepted only when its declared correctness, workload,
measurement boundary, and environment constraints all hold. Record rejected
hypotheses and slower candidates; they are useful evidence for the next choice.
