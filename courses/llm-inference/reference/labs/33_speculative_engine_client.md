# Lab 33: Check greedy equivalence in a speculative engine campaign

Real-engine speculation must preserve the intended target output before a throughput comparison is accepted. This optional lab sends a fixed greedy workload to one engine variant and records private response digests. The owning launcher compares target-only and speculative variants, allowing you to separate per-variant response collection from paired correctness and repeated benchmark evidence.

## Before you start

**Theory preparation:** Read Lesson 13 for draft/target compatibility, acceptance/recovery, paired greedy digests and total speculative cost. Lessons 6–7 teach engine lifecycle and the initial AIPerf workflow, and Lesson 9 teaches policy-equivalence campaigns. Complete Lab 23 before this separately qualified live experiment.

Qualify compatible target/draft artifacts, immutable revisions, engine image, and the AIPerf/client environment. This optional live exercise may require more memory than the small mechanics lab. Do not assume arbitrary draft and target models are compatible.

## Concepts and code path

The client submits bounded deterministic requests and hashes returned text without publishing it. A single client record proves only nonempty responses for its variant. The speculative campaign launcher starts independent engine trials, alternates variant order, compares corresponding greedy digests, captures benchmark artifacts, and owns cleanup. It—not the client alone—decides paired equivalence.

## Practice

Set all four artifact variables to reviewed identities before submitting. The shell requires nonempty values, and the launcher checks immutable-revision syntax; these checks do not establish artifact compatibility. Inspect launcher help first.

```bash
umask 077
bash slurm/vllm_speculative_ab.sbatch --help
sbatch slurm/vllm_speculative_ab.sbatch "${COURSE_TARGET_MODEL:?set target model}" "${COURSE_TARGET_REVISION:?set immutable target revision}" "${COURSE_DRAFT_MODEL:?set draft model}" "${COURSE_DRAFT_REVISION:?set immutable draft revision}"
```

## Check your results

Require nonempty responses for each variant and successful launcher pair comparisons across the independent trials. Inspect target/draft identities, variant labels, digests, and benchmark artifacts. A matching short workload is not universal quality equivalence for all prompts or stochastic policies.

## Investigate the behavior

Does acceptance reduce target work enough to offset draft execution and verification? Compare actual output lengths, latency, and throughput under the same load. Keep startup memory and initialization separate from steady serving.

## If something goes wrong

Digest disagreement blocks performance acceptance. Check generation policy, artifacts, and supported engine configuration before assuming a harmless mismatch. Missing dependency or capacity gates leave the extension pending.

## Takeaways and next step

Speculative speedup is conditional on workload, acceptance, and overhead. Report all independent trials and extend quality coverage before generalizing beyond the fixed greedy requests used by this campaign.
