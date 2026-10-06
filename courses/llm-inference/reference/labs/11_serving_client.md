# Lab 11: Measure concurrent completion requests

A serving system's performance includes request handling and completion delivery, not just GPU kernels. This lab sends bounded concurrent requests to a local OpenAI-compatible endpoint and measures the entire client campaign. You will relate request throughput, output-token rate, and completion latency while keeping the model revision and generation settings fixed.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

Qualify the native vLLM runtime, model, and client environment. The supplied launcher owns server startup, readiness, client execution, metric snapshots, and cleanup. Teaching HTTP remains loopback-only; no public endpoint is required.

## Concepts and code path

A bounded thread pool overlaps blocking HTTP requests. Requests use a 120-second timeout. Request errors abort the run, and every response must report a positive completion-token count before results are written.

The client schedules requests with a concurrency limit, records completion durations, and counts generated tokens returned by the service. The campaign wall clock supports aggregate requests/s and output tokens/s. This nonstreaming route does not expose the first token's arrival time or individual token intervals.

## Practice

`labs/11_serving_client.py` sends concurrent requests to a loopback completion server, requires nonempty generated outputs, and records end-to-end latency plus request and output-token throughput. The launcher owns the server lifecycle.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/11_serving_client/logs/%j.out" \
  --error="$PWD/results/11_serving_client/logs/%j.err" slurm/11_serving_client.sbatch
```

## Check your results

Each new job owns `results/11_serving_client/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/11_serving_client/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

Record the job number printed by this lab's successful submission. Require `COMPLETED` and exit code `0:0`, then read that job's logs and open its printed JSON path. Never select a result from an older job.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/11_serving_client/logs/$LAB_JOB_ID.out"
cat "results/11_serving_client/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require every request to complete with generated tokens. Inspect request count, concurrency, latency summaries, request throughput, and output tokens/s. The launcher retains server evidence separately; a completed HTTP request is not a semantic-quality assessment.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Median e2e (seconds) | `median_e2e_ms` | `s` |
| P90 e2e (seconds) | `p90_e2e_ms` | `s` |
| Request throughput per second | `request_throughput_per_second` | `requests/s` |
| Output tokens per second | `output_tokens_per_second` | `tokens/s` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
source tools/course_env.sh 11_serving_client --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 11_serving_client \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Run the launcher after its native serving runtime is prepared. Inspect positional model/concurrency/revision options before changing load; a concurrency sweep must preserve the same artifact and prompt policy.

```bash
bash slurm/11_serving_client.sbatch --help
sbatch --chdir="$PWD" \
  --output="$PWD/results/11_serving_client/logs/%j.out" \
  --error="$PWD/results/11_serving_client/logs/%j.err" slurm/11_serving_client.sbatch
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

As concurrency grows, does aggregate throughput improve while individual completion latency worsens? Distinguish actual generated tokens from requested maximum tokens. Explain why request throughput alone is misleading when output lengths differ.

Capture a separate diagnostic run:

This diagnostic profiles the GPU server that receives the client requests. Read `slurm/11_serving_client.nsys.sbatch` for the native Nsight command, readiness check, acknowledged start/stop controls and process cleanup. The job waits for report export before checking its results. Keep these diagnostic runs separate from normal timing runs.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/11_serving_client/logs/%j.out" \
  --error="$PWD/results/11_serving_client/logs/%j.err" slurm/11_serving_client.nsys.sbatch
```

The native Systems command is in `slurm/11_serving_client.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

The launcher profiles the **GPU server**, while the client measures requests. Open the emitted `.nsys-rep` in Systems; expand CUDA API, GPU kernels, copies, and worker-process rows. The launcher triggers `/start_profile` after server readiness and `/stop_profile` after the request campaign, using the engine’s CUDA profiler API. Match that interval to the client artifact timestamps. Require actual request activity inside the capture; initialization alone is insufficient. Server NVTX availability depends on the pinned engine; use its CUDA kernels and request interval when named phases are absent.

Guided comparison: Vary the launcher's second positional argument, `CONCURRENCY`, while holding its first argument, `MODEL`, and third argument, `REVISION`, fixed. Inspect `bash slurm/11_serving_client.sbatch --help` for the `[MODEL] [CONCURRENCY] [REVISION]` order and append those values after the script path in the existing submission command. The launcher passes concurrency to the client; it does not accept a `--concurrency` launcher flag. Predict its effect on the measured fields, verify correctness, and inspect the named report views. Independently choose one additional value, repeat unprofiled, and explain whether the result supports the prediction. Changing concurrency changes the workload; compare per-unit cost and capacity as a workload study, not a like-for-like optimization speedup.

**Nsight Systems evidence:** Capture the owned GPU server and its worker descendants while the client supplies requests. Open the server .nsys-rep and expand the GPU worker process tree, CUDA streams and runtime NVTX rows. Inspect the request interval after readiness; client-only activity or startup-only kernels are not sufficient. Compare launch gaps and prefill/decode activity against the clean client latency panels. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Connection failures require readiness and server-log inspection. Empty or failed responses invalidate the campaign, not just its slowest samples. Do not point the client at an unrelated external endpoint to bypass local startup.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Client measurements need declared arrival/load and output semantics. Use Lab 15 for streaming arrival observations and AIPerf with Lesson 7's metric definitions, then repeat accepted comparisons across at least three independent engine trials.
