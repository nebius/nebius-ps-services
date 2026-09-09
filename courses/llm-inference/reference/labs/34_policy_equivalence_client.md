# Lab 34: Preserve outputs while changing a serving policy

A scheduling or caching policy should not silently change the intended greedy response while appearing to improve latency. This lab collects deterministic response digests for one policy variant. You will understand how its results become a paired correctness gate inside the chunked-prefill or prefix-cache campaign, rather than treating one successful client invocation as proof that two policies are equivalent.

## Before you start

**Theory preparation:** Read Lessons 6–7 and 9 for engine restarts, matched workloads, greedy response digests and paired policy comparison. Use the chunking campaign after Lesson 9; read Lesson 10’s prefix-identity theory before the prefix-cache campaign. The same client serves two distinct policy questions.

Qualify the live engine and client environment, immutable model revision, and campaign prerequisites. Use the owning policy launcher rather than manually mixing server variants. Digests and raw responses remain private evidence.

## Concepts and code path

Each response digest is SHA-256 of the returned text's encoded bytes. Compare matched prompts, sampling settings and artifact identity within the owning campaign.

The client selects a declared fixed workload, sends greedy seeded requests, requires generated text, and writes response digests with workload and variant identity. It does not start an engine or compare another record itself. The campaign launcher manages policy changes, restarts, paired comparison, independent trials, and cleanup, then associates benchmark evidence with the same policy contract.

## Practice

Choose the campaign corresponding to the lesson you are testing. Both use this helper as a correctness component; they are separate experiments with different policy variables.

```bash
umask 077
bash slurm/vllm_chunked_prefill_ab.sbatch --help
sbatch slurm/vllm_chunked_prefill_ab.sbatch
```

## Check your results

Require nonempty greedy responses and matching paired digests in the launcher-owned comparison. Inspect model/revision, workload, and variant labels before pairing records. A client's `all_greedy_responses_nonempty` gate alone does not establish cross-policy equivalence.

## Investigate the behavior

Why must prompt content, tokenization, output budget, seed, and sampling policy remain fixed? Which settings should change in a chunking experiment, and which in a prefix-cache experiment? Separate correctness probes from load-generator measurements.

## If something goes wrong

Mismatched labels or artifacts invalidate record pairing. Different digests require investigation before accepting performance numbers. Do not delete inconvenient responses or pair records from different campaigns merely because their outputs happen to match.

## Takeaways and next step

Serving-policy optimization needs an explicit semantic gate as well as timing. Preserve this paired check when expanding workloads, and add task-quality evaluation where exact greedy-text equality is not the intended contract.
