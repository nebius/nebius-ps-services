# Lab 34: Preserve outputs while changing a serving policy

A scheduling or caching policy should not silently change the intended greedy response while appearing to improve latency. This lab collects greedy response digests for one policy variant. You will understand how its results become a paired correctness gate inside the chunked-prefill or prefix-cache campaign, rather than treating one successful client invocation as proof that two policies are equivalent.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Qualify the live engine and client environment, immutable model revision, and campaign prerequisites. Use the owning policy launcher rather than manually mixing server variants. Digests and raw responses remain private evidence.

## Concepts and code path

Each response digest is SHA-256 of the returned text's encoded bytes. Compare matched prompts, sampling settings and artifact identity within the owning campaign.

The client selects a declared fixed workload, sends greedy seeded requests, requires generated text, and writes response digests with workload and variant identity. It does not start an engine or compare another record itself. The campaign launcher manages policy changes, restarts, paired comparison, independent trials, and cleanup, then associates benchmark evidence with the same policy contract.

The chunked-prefill launcher fixes `VLLM_BATCH_INVARIANT=1`, `TRITON_ATTN`, and
`--enforce-eager` for both policies in both phases. Greedy decoding and a fixed
seed alone do not prevent floating-point execution differences from changing
the chosen token. The eager setting removes compiled and CUDA Graph execution
from this scheduling comparison; its timings describe that controlled execution
mode. Qualify compiled serving separately before making production performance
claims. These controls do not replace the exact paired-digest check.

## Practice

`labs/34_policy_equivalence_client.py` sends fixed greedy workloads to serving-policy variants, requires nonempty responses, and records private response digests. The paired campaign rejects policy changes that alter outputs.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/34_policy_equivalence_client/logs/%j.out" \
  --error="$PWD/results/34_policy_equivalence_client/logs/%j.err" slurm/34_policy_equivalence_client.sbatch
```

## Check your results

Each new job owns `results/34_policy_equivalence_client/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/34_policy_equivalence_client/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/34_policy_equivalence_client/logs/$LAB_JOB_ID.out"
cat "results/34_policy_equivalence_client/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require nonempty greedy responses and matching paired digests in the launcher-owned comparison. Inspect model/revision, workload, and variant labels before pairing records. A client's `all_greedy_responses_nonempty` gate alone does not establish cross-policy equivalence.

The launcher exports one run ID to both policies' child processes. Each digest
record's `run_id` must match the ID in its `chunked-prefill-ab-run-<run-id>`
directory; use the variant and trial fields in the filename to distinguish
records within that campaign.

The launcher writes native AIPerf exports beneath its printed campaign directory. Use each variant's `profile.json` and `profile.jsonl` for latency, observed token counts and throughput. Compare the saved `inputs.json` files across variants to verify the same synthetic workload. These exports remain separate from the greedy-digest JSON used by this lab's dashboard.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Correctness of selected results | `correctness` | Boolean pass |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot; the two slots select the paired engine variants from the same trial. The publisher requires matching greedy response digests; these panels establish equivalence, not serving throughput. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 34_policy_equivalence_client \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run the chunking comparison. This campaign uses the helper to check paired greedy-output equivalence.

```bash
bash slurm/34_policy_equivalence_client.sbatch --help
sbatch --chdir="$PWD" \
  --output="$PWD/results/34_policy_equivalence_client/logs/%j.out" \
  --error="$PWD/results/34_policy_equivalence_client/logs/%j.err" slurm/34_policy_equivalence_client.sbatch
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Why must prompt content, tokenization, output budget, seed, and sampling policy remain fixed? Which settings should change in a chunking experiment, and which in a prefix-cache experiment? Separate correctness probes from load-generator measurements.

Capture a separate diagnostic run:

This diagnostic profiles the GPU server that receives the client requests. Read `slurm/34_policy_equivalence_client.nsys.sbatch` for the native Nsight command, readiness check, acknowledged start/stop controls and process cleanup. The job waits for report export before checking its results. Keep these diagnostic runs separate from normal timing runs.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/34_policy_equivalence_client/logs/%j.out" \
  --error="$PWD/results/34_policy_equivalence_client/logs/%j.err" slurm/34_policy_equivalence_client.nsys.sbatch
```

The native Systems command is in `slurm/34_policy_equivalence_client.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

The launcher profiles the **GPU server**, while the client measures requests. Open the emitted `.nsys-rep` in Systems; expand CUDA API, GPU kernels, copies, and worker-process rows. The launcher triggers `/start_profile` after server readiness and `/stop_profile` after the request campaign, using the engine’s CUDA profiler API. Match that interval to the client artifact timestamps. Require actual request activity inside the capture; initialization alone is insufficient. Server NVTX availability depends on the pinned engine; use its CUDA kernels and request interval when named phases are absent.

The chunked-prefill campaign starts and stops capture for every correctness
probe and every AIPerf trial, with a fresh server for each. Inspect reports from
both phases for both policy variants; a captured correctness probe alone does
not establish that the benchmark requests were captured.

Guided comparison: Compare chunked prefill disabled/enabled using the same prompt/output budgets and paired greedy outputs. Independently inspect prefill/decode interference and choose the policy against the measured latency objective.

**Nsight Systems evidence:** Capture the owned GPU server and its worker descendants while the client supplies requests. Open the server .nsys-rep and expand the GPU worker process tree, CUDA streams and runtime NVTX rows. Inspect the request interval after readiness; client-only activity or startup-only kernels are not sufficient. Compare launch gaps and prefill/decode activity with the matching unprofiled AIPerf exports. Use this lab's Grafana panels to check paired greedy-equivalence evidence. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Mismatched labels or artifacts invalidate record pairing. Different digests require investigation before accepting performance numbers. Do not delete inconvenient responses or pair records from different campaigns merely because their outputs happen to match.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Serving-policy optimization needs an explicit semantic gate as well as timing. Preserve this paired check when expanding workloads, and add task-quality evaluation where exact greedy-text equality is not the intended contract.
