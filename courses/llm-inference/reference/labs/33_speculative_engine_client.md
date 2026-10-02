# Lab 33: Check greedy equivalence in a speculative engine campaign

Real-engine speculation must preserve the intended target output before a throughput comparison is accepted. This optional lab sends a fixed greedy workload to one engine variant and records private response digests. The owning launcher compares target-only and speculative variants, allowing you to separate per-variant response collection from paired correctness and repeated benchmark evidence.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

Qualify compatible target/draft artifacts, immutable revisions, engine image, and the AIPerf/client environment. This optional live exercise may require more memory than the small mechanics lab. Do not assume arbitrary draft and target models are compatible.

## Concepts and code path

The client submits bounded deterministic requests and hashes returned text without publishing it. A single client record proves only nonempty responses for its variant. The speculative campaign launcher starts independent engine trials, alternates variant order, compares corresponding greedy digests, captures benchmark artifacts, and owns cleanup. It—not the client alone—decides paired equivalence.

## Practice

`labs/33_speculative_engine_client.py` sends greedy requests to baseline or speculative engine variants and stores response digests without response text. The launcher compares paired digests across independent restarts before accepting the speculative campaign.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/33_speculative_engine_client/logs/%j.out" \
  --error="$PWD/results/33_speculative_engine_client/logs/%j.err" \
  slurm/33_speculative_engine_client.sbatch \
  "${CLUSTER_TARGET_MODEL:?set target model}" "${CLUSTER_TARGET_REVISION:?set immutable target revision}" "${COURSE_DRAFT_MODEL:?set draft model}" "${COURSE_DRAFT_REVISION:?set immutable draft revision}"
```

## Check your results

Each new job owns `results/33_speculative_engine_client/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/33_speculative_engine_client/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/33_speculative_engine_client/logs/$LAB_JOB_ID.out"
cat "results/33_speculative_engine_client/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require nonempty responses for each variant and successful launcher pair comparisons across the independent trials. Inspect target/draft identities, variant labels, digests, and benchmark artifacts. A matching short workload is not universal quality equivalence for all prompts or stochastic policies.

The launcher writes native AIPerf exports beneath its printed campaign directory. Use each variant's `profile.json` and `profile.jsonl` for latency, observed token counts and throughput. Compare the saved `inputs.json` files across variants to verify the same synthetic workload. These exports remain separate from the greedy-digest JSON used by this lab's dashboard.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Requests | `requests` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot; the two slots select the paired engine variants from the same trial. The publisher requires matching greedy response digests; these panels establish equivalence, not serving throughput. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 33_speculative_engine_client \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Set all four artifact variables to reviewed identities before submitting. The shell requires nonempty values, and the launcher checks immutable-revision syntax; these checks do not establish artifact compatibility. Inspect launcher help first.

```bash
bash slurm/33_speculative_engine_client.sbatch --help
sbatch --chdir="$PWD" \
  --output="$PWD/results/33_speculative_engine_client/logs/%j.out" \
  --error="$PWD/results/33_speculative_engine_client/logs/%j.err" slurm/33_speculative_engine_client.sbatch "${CLUSTER_TARGET_MODEL:?set target model}" "${CLUSTER_TARGET_REVISION:?set immutable target revision}" "${COURSE_DRAFT_MODEL:?set draft model}" "${COURSE_DRAFT_REVISION:?set immutable draft revision}"
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Does acceptance reduce target work enough to offset draft execution and verification? Compare actual output lengths, latency, and throughput under the same load. Keep startup memory and initialization separate from steady serving.

Capture a separate diagnostic run:

This diagnostic profiles the GPU server that receives the client requests. Read `slurm/33_speculative_engine_client.nsys.sbatch` for the native Nsight command, readiness check, acknowledged start/stop controls and process cleanup. The job waits for report export before checking its results. Keep these diagnostic runs separate from normal timing runs.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/33_speculative_engine_client/logs/%j.out" \
  --error="$PWD/results/33_speculative_engine_client/logs/%j.err" slurm/33_speculative_engine_client.nsys.sbatch "${CLUSTER_TARGET_MODEL:?set target model}" "${CLUSTER_TARGET_REVISION:?set immutable target revision}" "${COURSE_DRAFT_MODEL:?set draft model}" "${COURSE_DRAFT_REVISION:?set immutable draft revision}"
```

The native Systems command is in `slurm/33_speculative_engine_client.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

The launcher profiles the **GPU server**, while the client measures requests. Open the emitted `.nsys-rep` in Systems; expand CUDA API, GPU kernels, copies, and worker-process rows. The launcher triggers `/start_profile` after server readiness and `/stop_profile` after the request campaign, using the engine’s CUDA profiler API. Match that interval to the client artifact timestamps. Require actual request activity inside the capture; initialization alone is insufficient. Server NVTX availability depends on the pinned engine; use its CUDA kernels and request interval when named phases are absent.

Guided comparison: Compare the real serving engine with speculation disabled/enabled using identical prompts and bounded greedy responses. Independently relate accepted draft tokens to latency and reject a policy that changes paired response digests.

**Nsight Systems evidence:** Capture the owned GPU server and its worker descendants while the client supplies requests. Open the server .nsys-rep and expand the GPU worker process tree, CUDA streams and runtime NVTX rows. Inspect the request interval after readiness; client-only activity or startup-only kernels are not sufficient. Compare launch gaps and prefill/decode activity with the matching unprofiled AIPerf exports. Use this lab's Grafana panels to check paired greedy-equivalence evidence. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Digest disagreement blocks performance acceptance. Check generation policy, artifacts, and supported engine configuration before assuming a harmless mismatch. Missing dependency or capacity gates leave the extension pending.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Speculative speedup is conditional on workload, acceptance, and overhead. Report all independent trials and extend quality coverage before generalizing beyond the fixed greedy requests used by this campaign.
