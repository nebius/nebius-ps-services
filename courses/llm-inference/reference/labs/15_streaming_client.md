# Lab 15: Observe first content and streaming gaps

Users experience streamed generation as a wait for first content followed by a sequence of arrivals. This lab records those client-side events from a loopback service. You will distinguish first-content latency, inter-chunk gaps, and total completion time, while recognizing that an HTTP chunk can contain multiple tokens and is not itself a token-level timing unit.

## Before you start

Use the [Lab Guide](../../../lab-guide.html#lab-preparation-scripts) once to prepare this course and lab number before submitting jobs.

Qualify the engine and client environments and use the supplied streaming launcher. It owns the local server lifecycle. Keep prompts, responses, and raw streaming logs private and preserve the immutable model revision.

H100 throughput can keep rising with batching after TTFT/ITL become unacceptable. Measure the complete throughput-latency curve instead of quoting one maximum point.

## Concepts and code path

Concurrent client tasks open streaming completion requests, parse `data:` events containing generated content, and timestamp the first nonempty content and subsequent chunks. The client aggregates first-content and completion metrics over the campaign. It counts received characters but does not tokenize each arrival, so its gap statistic cannot be relabeled ITL or TPOT.

The supplied client runs a finite closed-loop workload: each thread starts another request after its previous request completes. Increasing concurrency changes that workload. It reports median and p90 latency, requests/s and received characters; it does not measure tokens/s, p95 latency or SLO goodput. An open-loop overload experiment would require a separate arrival-rate generator and a declared rejection/timeout policy. That experiment is outside this launcher.

Reconcile client boundaries with server metrics. AIPerf's inter-token latency (ITL) is a request average, while inter-chunk latency (ICL) describes chunk gaps. Synthetic batch-row updates per second are not output tokens per second, and a recurrent tensor operation is not a service time-to-first-token (TTFT) measurement.

For the initial token-aware AIPerf exercise, qualify the native runtime/model prerequisites and use the supplied `slurm/15_streaming_client.aiperf.sbatch` launcher. It owns the bounded loopback server, readiness, workload and cleanup. Compare requested and observed token counts, denominators and errors before broadening the workload. The prepared runtime selects `COURSE_VLLM` and `COURSE_AIPERF` automatically; each runs in its isolated native environment.

## Practice

`labs/15_streaming_client.py` reads concurrent loopback response streams and verifies completion markers and content. It records time to first content, inter-chunk gaps, end-to-end latency, and request throughput; chunks are not necessarily tokens.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/15_streaming_client/logs/%j.out" \
  --error="$PWD/results/15_streaming_client/logs/%j.err" slurm/15_streaming_client.sbatch
```

## Check your results

Each new job owns `results/15_streaming_client/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/15_streaming_client/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

After the streaming-client job completes, inspect its state and measured results on the login node. The second command prints the exact course JSON paths and numeric fields used by this dashboard. For a direct CPU run, use job `0`.

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/15_streaming_client/logs/$LAB_JOB_ID.out"
cat "results/15_streaming_client/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

The separate AIPerf job prints its private `results/aiperf-run-RUN_ID` artifact
directory. Inspect its native JSON summary and per-request JSONL exports for
completed requests, errors, token counts, latency units and throughput. Those
exports use AIPerf's schema; `tools/inspect_results.py` and this lab's dashboard
consume the streaming client's course JSON. Keep the two measurement contracts
separate, and use the streaming-client results for the publication commands below.

Require all streams to produce content. Inspect `median_ttft_ms`, completion latency, `median_inter_chunk_gap_ms`, request throughput, and received characters. Treat TTFT here as the client's first-content observation; transport buffering can affect it.

Every stream must also reach its `[DONE]` marker without an engine error event.
Content followed by an error or a truncated connection fails the campaign before
the client writes a result artifact.

Retain clock boundary, metric definitions, percentiles, warm-up, concurrency, token counts, failures, and service objectives.

Report rate and latency together; a system can improve one while harming the other.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Median ttft (seconds) | `median_ttft_ms` | `s` |
| P90 ttft (seconds) | `p90_ttft_ms` | `s` |
| Median e2e (seconds) | `median_e2e_ms` | `s` |
| Median inter chunk gap (seconds) | `median_inter_chunk_gap_ms` | `s` |
| Request throughput per second | `request_throughput_per_second` | `requests/s` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
source tools/course_env.sh 15_streaming_client --lab
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 15_streaming_client \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select your workspace and profile. Require **Correctness of selected results** to be `1` for both slots and **Selected comparison generation** to match the publisher's confirmation. Summary panels always show the currently published pair. Set the time picker to **Experiment start** through **Experiment end** for telemetry, then select the allocated GPU worker and its local GPU indices. GPU activity, framebuffer memory, power, temperature, and node panels provide context; they cannot time individual short kernels or establish exclusive attribution.

## Investigate the behavior

### Workload variations

Inspect request-count and concurrency options before changing load. The baseline launcher uses bounded generation and a loopback endpoint, avoiding any requirement to expose the service publicly.

```bash
bash slurm/15_streaming_client.sbatch --help
sbatch --chdir="$PWD" \
  --output="$PWD/results/15_streaming_client/logs/%j.out" \
  --error="$PWD/results/15_streaming_client/logs/%j.err" slurm/15_streaming_client.sbatch
```

For token-aware metrics, run the separate AIPerf campaign with the qualified native vLLM and AIPerf runtimes described above. Its supplied workload fixes concurrency at four; it accepts model and revision arguments, not the streaming launcher's request-count and concurrency arguments.

```bash
bash slurm/15_streaming_client.aiperf.sbatch --help
sbatch --chdir="$PWD" \
  --output="$PWD/results/15_streaming_client/logs/%j.out" \
  --error="$PWD/results/15_streaming_client/logs/%j.err" slurm/15_streaming_client.aiperf.sbatch
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Draw request start, first content, later chunks, and completion. Why can one received chunk contain several tokens generated during the preceding gap? Compare these observations with server metrics without assuming their clocks and boundaries are identical.

Higher concurrency can improve aggregate throughput until saturation, while increasing latency under contention; the direction and size of the effect require measurement. Compare this client's requests/s, first-content latency and completion latency without converting characters or chunks to tokens. Native engine benchmarks and endpoint clients observe different boundaries.

Capture a separate diagnostic run:

This diagnostic profiles the GPU server that receives the client requests. Read `slurm/15_streaming_client.nsys.sbatch` for the native Nsight command, readiness check, acknowledged start/stop controls and process cleanup. The job waits for report export before checking its results. Keep these diagnostic runs separate from normal timing runs.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/15_streaming_client/logs/%j.out" \
  --error="$PWD/results/15_streaming_client/logs/%j.err" slurm/15_streaming_client.nsys.sbatch
```

The native Systems command is in `slurm/15_streaming_client.nsys.sbatch`. The [GPU Performance Tools reference](../../../gpu-performance-tools/index.html) explains its flags.

The launcher profiles the **GPU server**, while the client measures requests. Open the emitted `.nsys-rep` in Systems; expand CUDA API, GPU kernels, copies, and worker-process rows. The launcher triggers `/start_profile` after server readiness and `/stop_profile` after the request campaign, using the engine’s CUDA profiler API. Match that interval to the client artifact timestamps. Require actual request activity inside the capture; initialization alone is insufficient. Server NVTX availability depends on the pinned engine; use its CUDA kernels and request interval when named phases are absent.

Guided comparison: Change `CONCURRENCY`, the streaming launcher's third positional argument after `MODEL` and `REQUESTS`, while holding the model, request count and immutable revision fixed. Predict its effect on the measured fields, verify correctness, and inspect the named report views. Independently choose one additional value of the same control, repeat unprofiled, and explain why the result supports or rejects the prediction. Changing `concurrency` changes the workload; compare per-unit cost and capacity as a workload study, not a like-for-like optimization speedup.

Use the same qualified image and runner for both unprofiled submissions:

```bash
STREAM_MODEL='Qwen/Qwen2.5-0.5B-Instruct'
STREAM_REVISION='7ae557604adf67be50417f59c2c2f167def9a775'
sbatch --chdir="$PWD" \
  --output="$PWD/results/15_streaming_client/logs/%j.out" \
  --error="$PWD/results/15_streaming_client/logs/%j.err" slurm/15_streaming_client.sbatch "$STREAM_MODEL" 8 2 "$STREAM_REVISION"
sbatch --chdir="$PWD" \
  --output="$PWD/results/15_streaming_client/logs/%j.out" \
  --error="$PWD/results/15_streaming_client/logs/%j.err" slurm/15_streaming_client.sbatch "$STREAM_MODEL" 8 4 "$STREAM_REVISION"
```

Save both streaming-client result paths for the workload comparison. Do not pass `--concurrency` to either Slurm launcher or substitute the native AIPerf exports for these course result artifacts.

**Nsight Systems evidence:** Capture the owned GPU server and its worker descendants while the client supplies requests. Open the server .nsys-rep and expand the GPU worker process tree, CUDA streams and runtime NVTX rows. Inspect the request interval after readiness; client-only activity or startup-only kernels are not sufficient. Compare launch gaps and prefill/decode activity against the clean client latency panels. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

## If something goes wrong

Empty streams, malformed events, or premature termination are failures, not short successful requests. A missing gap statistic may mean too few content arrivals to form an interval; it is not zero token latency.

Do not confuse total decode duration, AIPerf's average ITL, and individual token-arrival gaps. Record the tool's formula and token denominator.

Publication failure is separate from benchmark failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. Counter permission errors or an empty capture require readiness repair before a profiling claim.

## Takeaways and next step

Measure what the client actually observes and name it accurately. Use the qualified AIPerf workflow with its documented metric definitions and repeat fixed-workload trials before making a serving-latency claim.

Define every metric formula and collection boundary before comparing systems.

Explain why TTFT can rise while GPU utilization and total throughput rise.
