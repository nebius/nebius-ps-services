# Lab 34: Tune serving goodput under latency objectives

Goodput is the rate of successfully completed requests that also meet declared service-level objectives. A server can increase raw throughput while violating latency limits. Use NVIDIA AIPerf to compare two concurrency levels on the same two-worker Dynamo deployment, holding the synthetic request corpus, model revision, generation parameters and latency objectives fixed across the comparison.

## Before you start

Complete [environment setup](../../../README.md#how-to-set-up-the-lab) once. This lab uses the [assigned Grafana dashboard](../grafana/34_serving_goodput.json).

Use the dedicated two-worker, sixteen-H100 cluster prepared in shared environment setup. Verify local NVLink/NVSwitch and inter-node InfiniBand readiness. Keep driver, software, allocation and other workloads fixed; the two one-GPU TCP workers cannot establish this fabric's performance. The `small` and `large` names select workload sizes, not optimization or profiling modes.

Reuse the joint vendor environment and pinned model prepared in
[Lab 32](32_dynamo_disaggregation.md). Source `env/vendor-environment.sh` in
this submission shell. Retain the complete model cache and repository metadata
for offline tokenizer lookup; do not substitute a weights-only copy.

## Concepts and code path

AIPerf measures 128 requests against a time-to-first-token (TTFT) objective of at most 1000 ms and a per-request average inter-token latency (ITL) objective of at most 50 ms. These are teaching thresholds. Throughput counts all completed requests per second; goodput counts only requests meeting both objectives. For example, 100 completed requests in 10 seconds give 10 requests/s throughput; if 70 meet both objectives, goodput is 7 requests/s.

Here, **workload size means both prompt length and generated output length**:

| Per-run setting | `small` | `large` |
| --- | --- | --- |
| Target input tokens per request | 256 | 2048 |
| Exact generated tokens per request | 32 | 128 |
| Measured requests | 128 | 128 |

Both profiles use the same model and hardware allocation. The `large` name selects the larger workload; it does not select or detect a GPU model. Longer prompts increase prompt-processing work (prefill), and longer outputs increase token-generation work (decode). Concurrency is a separate setting: `--concurrency 8` allows up to eight requests in flight, while `--concurrency 16` allows up to sixteen, for either profile.

The client supplies matching minimum and maximum output lengths and ignores the end-of-sequence token. The server's completion count includes reasoning tokens, even when they are not visible in the answer. AIPerf uses server-reported usage rather than estimating token counts from decoded text. Chat formatting can add prompt tokens beyond the input-generation target, so compare the actual server counts between runs.

The lab checks the actual transmitted requests, complete response streams and server token counts. It hashes the ordered request payloads and requires identical inputs and token work across the pair. Answer wording may differ when concurrency changes; output signatures are diagnostic and do not determine acceptance. This is a synthetic capacity exercise, not a semantic-quality evaluation or a test of inference determinism. Matching visible text is unnecessary, but missing usage, failed requests or early termination invalidate the measurement.

Keep the existing model, tokenizer, two-worker aggregated layout, round-robin routing, cache policy and engine settings fixed. The shared worker configuration remains the same as in Labs 32 and 33; this experiment does not depend on those settings producing identical answers. Only concurrency changes. There is no AIPerf warm-up phase; the deployment's readiness request remains outside measurement. AIPerf p99 ITL summarizes per-request average intervals, not every individual token gap.

## Practice

Retain the complete Hugging Face cache snapshot produced by shared environment setup, including
its repository metadata. The client selects the pinned tokenizer repository
and revision with `HF_HUB_CACHE` set to that snapshot's cache root and offline
mode enabled. AIPerf 0.12's offline resolver expects a repository ID rather
than an absolute tokenizer directory. A custom runtime wrapper must preserve
`HF_HUB_CACHE`, `HF_HUB_OFFLINE` and `TRANSFORMERS_OFFLINE` for the client.

Submit the two unprofiled jobs from the login node, one after the other after completion, and retain their printed job numbers.

```bash
python3 tools/submit_lab.py --lab 34_serving_goodput slurm/vendor_job.sbatch labs/34_serving_goodput.py --profile small --model-dir "$MODEL_PATH" --concurrency 8
python3 tools/submit_lab.py --lab 34_serving_goodput slurm/vendor_job.sbatch labs/34_serving_goodput.py --profile small --model-dir "$MODEL_PATH" --concurrency 16
```

Logs stay under `results/34_serving_goodput/logs/`. A submission receipt is not a measurement; wait for successful completion before selecting artifacts.

## Check your results

Confirm both completed job states and inspect the actual JSON paths. Set `BASELINE_RESULT` and `CANDIDATE_RESULT` to those artifacts, never to stdout or profiler reports.

```bash
sacct -j "${LAB_JOB_ID:?job number}" --format=JobID,State,ExitCode
"$COURSE_PUBLISH_PYTHON" tools/inspect_results.py --lab 34_serving_goodput --job "$LAB_JOB_ID"
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 34_serving_goodput \
  --baseline "${BASELINE_RESULT:?baseline JSON}" --candidate "${CANDIDATE_RESULT:?candidate JSON}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; reviewed current generation otherwise}"
```

| Dashboard panel | Measurement path | Display unit |
| --- | --- | --- |
| Requests meeting both SLOs | `goodput_per_second` | reqps |
| All completed requests | `requests_per_second` | reqps |
| P99 first-token latency | `ttft_p99_ms` | s |
| P99 request-mean token interval | `request_mean_itl_p99_ms` | s |

Select workspace and profile in Grafana. Require **Correctness of selected results** to equal 1 and **Selected comparison generation** to match confirmation. Set the time picker to **Experiment start** through **Experiment end** and select the allocated workers with **GPU worker**, then choose their local indices with **GPU index on selected workers**. Sampled utilization is context, not a per-kernel explanation or proof of transport selection.

## Investigate the behavior

Read profile_export_aiperf.json together with profile_export.jsonl, profile_export_raw.jsonl and outputs.json. Check `measurement_contract`, `workload_sha256`, requested output length and the actual length arrays. Raw exports and inputs.json stay in the private vendor folder. The raw requests prove which prompts and controls were sent; the final streaming usage proves completed token work. These exports describe the same recorded requests, not independent measurements.

Distinguish successful requests from SLO-passing requests; zero goodput can be a valid result. Repeat the 8/16 pair in reverse order to observe variation, then optionally try concurrency 32 against the same baseline. Explain whether queueing, decode work or the network limits useful capacity. Neither higher concurrency nor a speedup is required for a successful investigation. The 128-request exercise is a small teaching sample, not a production capacity certification. Use this lab’s server capture command below to inspect GPU execution alongside AIPerf measurements.

Keep diagnostic captures separate from acceptance timings. For distributed work, retain each rank's report and placement record; compare the same application phase across ranks. Nsight Compute replay is inappropriate for live collectives: investigate a separately isolated local kernel when kernel-level evidence is needed.

Capture a separate diagnostic run:

```bash
python3 tools/submit_lab.py --lab 34_serving_goodput slurm/vendor_job.sbatch labs/34_serving_goodput.py --profile small --model-dir "$MODEL_PATH" --concurrency 8 --capture systems
```

**Nsight Systems evidence:** Capture the owned GPU server and its worker descendants while the client supplies requests. Open server-rank0.nsys-rep and server-rank1.nsys-rep from the private vendor folder. Expand the GPU worker process trees, CUDA streams and NCCL activity during AIPerf requests after model startup. Compare gaps and kernel activity with client TTFT/ITL; GPU utilization is not request latency. Model startup alone is an incomplete serving trace. Reports are diagnostic; publish the separate unprofiled baseline and candidate. The capture must contain the exercise itself, not only initialization. If it does not, treat it as incomplete.

The lab starts CUDA collection through both workers after readiness and stops
collection before shutting down the engines. Both control responses must confirm
success. It waits up to 180 seconds for both reports before stopping the servers.
Missing or empty reports fail the job; inspect request-phase GPU activity and
profiler warnings in both reports before accepting the capture.

Open large distributed reports one at a time to limit viewer memory use.
Warnings about missing CUDA events or incomplete NCCL correlations limit what
the timeline can prove, even when the job succeeds. Retain those warnings with
your observations; do not treat an absent event as proof that no work occurred.
Compare each worker's recorded activity within its capture window. Aligning
individual requests across hosts requires separately established clock bounds.

## If something goes wrong

A missing worker, vendor field, transport, completed request or correctness check is missing evidence, never zero performance. Read the lab's private job logs and fix the failing prerequisite before another trial. Do not change drivers, network configuration or registration modules inside an experiment.

Artifacts from the earlier output-equivalence exercise lack the fixed-work evidence and cannot be reused; run a fresh pair. If server usage is missing or generated lengths differ, inspect the raw request controls and the pinned runtime before retrying. Do not substitute client token estimates, ignore a failed request or tune determinism to make answer text agree.

Publication failure is separate from benchmark failure. Retain successful JSON artifacts and republish with the reviewed generation after ingestion is repaired. Vendor versions are qualification candidates until the designated runtime and sixteen-GPU live checks pass.

## Takeaways and next step

Explain what changed, which observation supports the mechanism, and which alternative explanation remains. Repeat matched unprofiled runs and describe variation; retain a slower candidate when it disproves the initial hypothesis. Finish with the independent investigation above and a bounded decision for this workload and topology.
