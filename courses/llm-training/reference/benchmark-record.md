# Training benchmark record

Record stage, model/revision, tokenizer, dataset or synthetic-data contract,
global tokens/update, microbatch and accumulation, shapes, dtypes, precision
recipe, parallel placement, checkpoint boundary, software/H100 environment,
loss/gradient/update checks, step-time samples, tokens/s, phase memory,
communication, MFU formula, profiler evidence, and keep/reject decision.

Raw data, checkpoints, logs, prompts, and scheduler details remain private.

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
| Training stage, model/tokenizer immutable revisions, dataset/license or synthetic contract | To record | To record |
| Global valid tokens/update, microbatch, accumulation, sequence distribution | To record | To record |
| Loss, every parameter gradient and full update checks; checkpoint/resume boundary | To record | To record |
| Weights, gradients, optimizer, activations, communication and phase-specific peaks | To record | To record |
| Step time, valid tokens/s, MFU formula/peak denominator and HFU distinction | To record | To record |
| Lab 31 input requires-grad, two-GEMM 4TH² numerator, excluded elementwise work, dense BF16 peak source, matmul-only utilization (not full-model MFU) | To record | To record |
| Precision/FP8 recipe, parallel placement, slowest-rank and communication evidence | To record | To record |
| Lab 33 cap/hook, actual startup/measured buckets, slowest-rank step samples, reference/trajectory errors and pending convergence gate | To record | To record |

A faster result is accepted only when its declared correctness, workload,
measurement boundary, and environment constraints all hold. Record rejected
hypotheses and slower candidates; they are useful evidence for the next choice.
