# Kernel benchmark record

Record the operation contract, shapes, alignment, dtype, tolerance, grid and
block, compiler/toolkit/image, architecture flags, library versions, warm-up,
CUDA-event samples, effective bandwidth or operation rate, registers, shared
memory, spills, sanitizer result, profiler evidence, end-to-end result,
portability boundary, and keep/reject decision.

Use FP32 `rtol=1e-5, atol=1e-6` and FP16/BF16 `rtol=1e-2, atol=1e-2` unless the
operation documents a stricter criterion. Never report only the fastest sample.

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
| Primary metric, timer, included operations, completion condition, correctness/quality tolerances | To record | To record |
| Warm-up, measured iterations, three independent trial IDs and order | To record | To record |
| Correctness result and maximum error; missing/non-finite data | To record | To record |
| Raw timing distribution, minimum, median, p90/p95 and dispersion | To record | To record |
| Baseline allocated memory, peak allocated/reserved memory, incremental peak | To record | To record |
| Sanitized profiler observation, selected range/kernel, tool version | To record | To record |
| Supporting observation and disconfirming control | To record | To record |
| Decision: keep, reject, or investigate; limitation and next experiment | To record | To record |
| Operation contract, boundary cases, aliasing, layouts and numerical tolerance | To record | To record |
| CUDA/compiler/container/CUTLASS versions, SM90 or qualified SM90a target | To record | To record |
| Reference/library result, memcheck/racecheck/initcheck/synccheck evidence | To record | To record |
| Registers, spills, shared memory, occupancy, selected profiler evidence | To record | To record |
| Kernel and full-application latency, launch/traffic calculation and maintenance decision | To record | To record |

A faster result is accepted only when its declared correctness, workload,
timed operations, and environment constraints all hold. Record rejected
hypotheses and slower candidates; they are useful evidence for the next choice.
