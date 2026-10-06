# Lab 28: Compare full-prefill and chunked scheduling

Long prompt processing can delay requests that are ready to generate another token. This lab models how full non-preemptible prefill and bounded prefill chunks affect admission opportunities under fixed arrivals. You will trace request progress and conserved work in abstract service quanta before testing the same scheduling idea in a real engine.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

Use the mechanics environment. The wrapper checks H100, but scheduling is a CPU simulation. Its equal-cost work units are deliberately not an H100 latency model.

Mixed prefill/decode batches combine large and narrow shapes with different memory behavior. Engine kernels and scheduler versions determine whether a policy is efficient on the pinned H100 stack.

## Concepts and code path

The simulator tracks arrivals, remaining prompt work, output work, first-output times, and completion. Each quantum supplies 256 abstract work units. Full 2048-unit prefill occupies eight quanta and blocks admission during that dispatch; smaller chunks allow decisions between dispatches. Decode-ready work is prioritized in the chunked policy. A 256-sized chunk control helps distinguish budget effects.

Given a 4,096-token prompt arriving while eight decode requests need one token each, prefill-first delays every decode until the prompt completes. Change to 512-token chunks with decode interleaving. Expected observation: decode ITL improves, while prompt TTFT and total kernel/scheduler overhead may rise; keep the policy only against the weighted workload and SLOs.

The fixture has fixed arrivals and 256 abstract work units per service quantum. It compares full non-preemptible prefill, 64-token chunks, and a 256-token chunk-size control. A full 2048-token prefill occupies eight quanta, not one free scheduling tick. Arrivals during that interval wait; bounded chunks allow admission between dispatches. Inspect per-request first-token latency and conserved token work in the result's per-request records.

## Practice

`labs/28_continuous_batching.py` simulates unchunked and chunked-prefill scheduling for fixed arrivals and output demands. It checks completed modeled work and records waiting/completion behavior in abstract service quanta, not milliseconds.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/28_continuous_batching/logs/%j.out" \
  --error="$PWD/results/28_continuous_batching/logs/%j.err" \
  slurm/28_continuous_batching.sbatch --workload small
```

## Check your results

Each new job owns `results/28_continuous_batching/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/28_continuous_batching/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/28_continuous_batching/logs/$LAB_JOB_ID.out"
cat "results/28_continuous_batching/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require equivalent scheduled work and completion of every request. Inspect each policy's trace, dispatch duration, first-output latency, maximum active requests, and completion times. These modeled latencies are not measured service TTFT or token-aware ITL.

For the separate live-engine campaign, retain queue depth, scheduled prefill/decode tokens, batch composition, TTFT, ITL, throughput, and memory. Keep those measurements separate from the simulator’s abstract service-time records.

The chosen policy should meet latency objectives at the required offered load and workload mix. The simulator assigns equal abstract cost to prefill and decode tokens and excludes real launch, kernel and scheduler costs. Its trace teaches admission and blocking, not milliseconds or vLLM internals. Compare modeled service time rather than dispatch count alone, then test the hypothesis with the real engine.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Nonpreemptible full prefill / median first token latency quanta | `nonpreemptible_full_prefill.median_first_token_latency_quanta` | `none` |
| Chunked 64 / median first token latency quanta | `chunked_64.median_first_token_latency_quanta` | `none` |
| Chunked 256 control / median first token latency quanta | `chunked_256_control.median_first_token_latency_quanta` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
source tools/course_env.sh 28_continuous_batching --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 28_continuous_batching \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select the workspace and profile. Require **Correctness of selected results** to equal 1 and **Selected comparison generation** to match publication confirmation. Compare the selected artifact fields and experiment timestamps. This dashboard omits GPU telemetry because this recipe cannot attribute device activity to its result.

## Investigate the behavior

### Workload variations

Run the fixed-arrival fixture before editing policies. All policies must complete the same declared work; simulator time is reported in quanta, not milliseconds.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/28_continuous_batching/logs/%j.out" \
  --error="$PWD/results/28_continuous_batching/logs/%j.err" slurm/28_continuous_batching.sbatch --workload small
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Locate an arrival during a long full-prefill dispatch and calculate its waiting time. How does chunking change the next admission opportunity? Explain which conclusions depend on the model's equal unit-cost assumption.

Smaller chunks improve decode responsiveness but may reduce prefill throughput and increase launches. Larger token budgets increase throughput but can worsen queueing and KV pressure. Fairness can conflict with maximum aggregate rate.

**Nsight Systems: not applicable.** This discrete CPU scheduling model has no GPU server or CUDA kernels. Inspect the measured or modeled fields in this lab's dashboard; retain the artifact and its stated scope.

## If something goes wrong

Long prefill that consumes no modeled time, or output work that disappears, indicates a broken time/work model. Recheck dispatch duration and request completion before comparing policies. Do not convert quanta to GPU milliseconds without an independently justified model.

Avoid comparing policies at different admitted request rates or silently dropping overload.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Scheduling changes who waits as well as aggregate progress. After engine qualification, run `slurm/34_policy_equivalence_client.sbatch` for independent streaming/AIPerf trials and Lab 34's paired output checks; only those runs support live-service claims.

Hold arrivals fixed and report completions, failures, queue growth, and service-level goodput.

Explain the latency/efficiency tradeoff in prefill chunk size.
