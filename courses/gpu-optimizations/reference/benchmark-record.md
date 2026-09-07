# Benchmark record

Record the immutable workload, correctness criterion, independent variable,
software and H100 environment, timer boundary, warm-up, raw sample summary,
memory, profiler evidence, observation, inference, and keep/reject decision.

Microbenchmarks must use CUDA events and an end-to-end wall-time check.
Distributed trials must include per-rank work, topology, message volume, and
exposed communication. Never publish one best sample as the result.

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
| Suspected limiter and measured critical-path fraction | To record | To record |
| Exposed collective time, fixed total work, scaling efficiency | To record | To record |
| Copy-ready gaps, allocation lifetime, compile/capture/replay costs | To record | To record |
| Labs 19/20 stream mode, slots, batches, work/sink, whole-loop distribution and final-drain correctness; separate timeline overlap and pool capacity | To record | To record |

A faster result is accepted only when its declared correctness, workload,
measurement boundary, and environment constraints all hold. Record rejected
hypotheses and slower candidates; they are useful evidence for the next choice.

## Networking comparison addendum

Before a tuning run, record the effective site configuration privately, then
freeze one profile difference. Record IP interface and RDMA HCA selection
separately; do not publish their identifying names or raw logs.

| Networking evidence | Baseline | Candidate |
| --- | --- | --- |
| Platform capability versus selected transport versus qualified GDR path | To record | To record |
| Benchmark source, per-node binary hashes, MPI mode, NCCL headers and loaded library | To record | To record |
| Message bytes, float/sum, buffer mode, rank count, validation enabled | To record | To record |
| Time unit, algbw, normalized busbw, both incorrect-element counts | To record | To record |
| Three independent per-size values, median and range across runs | To record | To record |
| Diagnostic versus timing run, exit status and complete size curve | To record | To record |
| Single job-local override and unchanged QP splitting/interface policy | To record | To record |
| Follow-up application time and exposed collective time | To record | To record |

A parsed log proves only the validated content. A benchmark run does not prove
fabric administration, remote binary parity or application benefit. Keep
negative or inconclusive results and missing evidence visible.
