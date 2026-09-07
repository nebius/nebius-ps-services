# Benchmark record

Record the lab ID, run ID, public software versions, GPU family, compute
capability, profile, shapes, dtypes, seed, warm-up, repetitions, correctness,
raw sample summary, memory, profiler evidence, observation, inference, and
keep/reject decision. Keep raw artifacts private.

Apply the lab's declared correctness criterion. For elementwise comparisons
without a different declared criterion, use FP32 `rtol=1e-5, atol=1e-6` and
FP16/BF16 `rtol=1e-2, atol=1e-2`. Broader teaching checks or finite-value checks
support only their stated diagnostic scope; they do not establish production
numerical equivalence.

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
| CPU/resident-GPU/transfer-inclusive timing boundaries | To record | To record |
| Useful byte/FLOP accounting, roofline assumptions, view/repack break-even | To record | To record |
| Collective message size, algorithm/bus bandwidth, slowest-rank time | To record | To record |

A faster result is accepted only when its declared correctness, workload,
measurement boundary, and environment constraints all hold. Record rejected
hypotheses and slower candidates; they are useful evidence for the next choice.
