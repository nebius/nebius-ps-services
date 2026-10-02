# Lab 30: Probe an engine's readiness and request schema

Different inference servers expose different readiness endpoints and request schemas even when they host similar models. This lab sends one bounded request through either an OpenAI-compatible API or a declared TensorRT-LLM/Triton repository profile. You will verify the client/server contract before benchmarking, without treating a successful readiness response as proof of correct generation or sustained throughput.

## Before you start

Complete the [Lab Guide](../../../README.md#how-to-set-up-the-lab) before starting.

This lab checks readiness and request schemas using prepared qualified engines. Each launcher owns its loopback server and client in one Slurm allocation. The separately launched AIPerf campaign and paper disaggregation exercise are optional activities; neither changes what the Python readiness probe measures.

For OpenAI mode, set `VLLM_IMAGE_DIGEST` and `COURSE_CONTAINER_RUNNER`, and select a model and immutable revision already present in the prepared cache. The launcher runs offline and starts vLLM on the allocated worker. For Triton, set the reviewed repository, matching profile/model/token field, exact image digest, and container runner for this environment.

Use images and engine versions that explicitly support H100/SM90 and the host driver. Do not combine mechanics dependencies with heavyweight engine environments.

Dynamo disaggregation is advanced and conditional. Two one-GPU nodes can run a bounded path experiment only when the pinned stack supports transfer; they cannot prove production RDMA or multi-worker scaling.

## Concepts and code path

The Python client selects a protocol, probes readiness or model discovery, sends a bounded generation request, and validates a response field. Triton profiles are explicit: `llmapi` uses `tensorrt_llm` with `sampling_param_max_tokens`; `inflight_batcher` uses `ensemble` or `tensorrt_llm_bls` with `max_tokens`. Both launchers own startup, bounded readiness and cleanup; this client does not compile an engine.

The Triton launcher uses the prepared image's `mpirun -n 1` to initialize a
one-rank Message Passing Interface (MPI) environment inside the allocated GPU
task. TensorRT-LLM imports MPI even for this single-GPU qualification. Starting
the server directly under `srun` can make it inherit an incompatible Slurm MPI
environment. The image must therefore provide both `mpirun` and `tritonserver`.
It passes `--oversubscribe` to MPI so the backend can spawn its worker alongside
the launcher when Slurm advertises one MPI task slot. This retains the same
single-GPU allocation and CPU budget; it does not request additional resources.
The launcher disables configuration auto-completion, following the matching
TensorRT-LLM launcher: a temporary Python auto-completion process can initialize
and finalize the MPI rank before the persistent model instance starts. Prepare
complete `config.pbtxt` files, including explicit batch and transaction-policy
settings that match the reviewed model YAML. For `llmapi`, copy the effective
`triton_config.max_batch_size` and `triton_config.decoupled` values into
`max_batch_size` and `model_transaction_policy.decoupled`; do not change them
to work around startup errors.

The launcher exports one validated run ID to the client and passes a results
directory through `--output-dir`. The client writes
`results/30_engine_profile-run-RUN_ID.json`; the matching private server log is
`results/30_engine_profile/logs/vllm-server-run-RUN_ID.log` or
`results/30_engine_profile/logs/trtllm-triton-run-RUN_ID.log`. Use that shared identifier to correlate
startup and request evidence. An output directory is not a JSON filename.

Given a server process started at time zero, weights ready at 45 seconds, graph warm-up complete at 70 seconds, and the first request sent at 10 seconds, that request's TTFT is not a steady-state measurement. Change the launcher to wait for readiness, run a correctness probe, and warm the declared buckets. Expected observation: the benchmark excludes startup while a separate startup metric retains it.

The OpenAI launcher starts the prepared vLLM image with the selected cached model and pins both model and tokenizer revisions. For TensorRT-LLM/Triton, use the dedicated launcher and declare the reviewed repository as `llmapi` with `tensorrt_llm`/`sampling_param_max_tokens`, or `inflight_batcher` with `ensemble` or `tensorrt_llm_bls`/`max_tokens`; the launcher rejects mixed schemas before startup. Multi-LoRA and multimodal workloads are optional extensions.

Given prefill capacity of 200,000 prompt tokens/s, suppose arrivals require 250,000 prompt tokens/s. Decode has enough capacity for the corresponding output workload, but the prefill queue grows; adding decode workers cannot remove that bottleneck. Change the allocation to increase prefill capacity while accounting for KV handoff cost within the TTFT budget. Expected observation: the backlog can drain only if sustained prefill capacity exceeds the offered load and handoff, routing and decode can keep up.

After qualifying both images and the container runner, inspect `bash slurm/15_streaming_client.aiperf.sbatch --help` and submit the optional campaign with:

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/15_streaming_client/logs/%j.out" \
  --error="$PWD/results/15_streaming_client/logs/%j.err" slurm/15_streaming_client.aiperf.sbatch
```

 That Lab 15 campaign owns its server, workload, profiler reports and cleanup independently of this readiness probe. The disaggregation calculation is a paper exercise: the supplied Dynamo preflight checks GPU visibility only; it does not start phase workers or validate KV transfer.

## Practice

`labs/30_engine_profile.py` probes a prepared OpenAI-compatible or Triton server and sends one bounded generation request. It validates nonempty generated text and records protocol, request duration, and usage without publishing response text.

Run from this course directory on the login node after the one-time Lab Guide setup. Save the job number; the completed job prints its result paths.

```bash
sbatch --chdir="$PWD" \
  --output="$PWD/results/30_engine_profile/logs/%j.out" \
  --error="$PWD/results/30_engine_profile/logs/%j.err" \
  slurm/30_engine_profile.sbatch \
  Qwen/Qwen2.5-0.5B-Instruct 7ae557604adf67be50417f59c2c2f167def9a775
```

## Check your results

Each new job owns `results/30_engine_profile/jobs/JOB_ID/`: `results/` contains measurements, `profiles/` native captures, `logs/` process logs and `artifacts/` auxiliary output. Scheduler logs remain in `results/30_engine_profile/logs/`. Use the ID returned by this submission.

Inspect the baseline now. After running the variation in Investigate, return here to check and publish the equivalent baseline/candidate pair.

For either launcher, wait for the submitted job to complete and inspect that job's results. The inspector prints exact JSON paths and the numeric fields used by this dashboard:

```bash
export LAB_JOB_ID='<job number printed by this lab submission>'
sacct -j "$LAB_JOB_ID" --format=JobID,State,ExitCode
cat "results/30_engine_profile/logs/$LAB_JOB_ID.out"
cat "results/30_engine_profile/logs/$LAB_JOB_ID.err"
export RESULT_JSON='<exact result path printed by the completed run>'
cat "$RESULT_JSON"
```

Reading JSON is inspection, not validation. Check `lab_id`, `experiment.slurm_job_id`, `correctness` and instrumentation fields; retain every original/aggregate required by this lab.

Require valid generated-response structure and inspect protocol, model identity, request duration, and available usage fields. One bounded request establishes API activation, not a latency distribution, a semantic-quality score, or a benchmark campaign.

In OpenAI mode, the first completion must contain a nonempty text string. In
Triton mode, `text_output` must be a nonempty string; numbers, booleans, lists,
objects and empty values fail the probe. The AIPerf launcher pins its tokenizer to
the same immutable revision as the server so input and output token counts use
the declared model's vocabulary.

Retain immutable image/revision, arguments, readiness, sanitized logs, workload, metrics, and shutdown result.

Engine comparisons require equivalent artifacts, requests, tokenization, and accepted quality.

Retain arrival model, ISL/OSL, concurrency, completions, service metrics, route/cache decisions, and transfer assumptions.

Two one-GPU nodes can demonstrate control flow but not production-scale disaggregation or RDMA performance.

The dashboard reads these completed artifact fields. Each row retains its case and selected slot; the original JSON retains configurations and distributions.

| Dashboard panel | Field under `measurements` | Display unit |
| --- | --- | --- |
| Elapsed (seconds) | `elapsed_ms` | `s` |
| Model count | `model_count` | `none` |

`publish_results.py` validates the selected pair, publishes its metrics and confirms the selection generation. Prepare publishing once using the Lab Guide before running it. Select two successful, equivalent, unprofiled runs in the same workload preset. For programs that measure several implementations in one run, compare those cases within each slot. Use this lab's declared baseline/candidate pairing: change only one permitted control, or keep all controls fixed for repeated qualification. On the login node, set the paths to the printed result files and review the current generation (use `0` for the first selection):

```bash
"$COURSE_PUBLISH_PYTHON" tools/publish_results.py --lab 30_engine_profile \
  --baseline "${BASELINE_RESULT:?printed baseline JSON path}" \
  --candidate "${CANDIDATE_RESULT:?printed candidate JSON path}" \
  --expected-generation "${COMPARISON_GENERATION:?0 initially; otherwise reviewed generation}"
```

In Grafana, select the workspace and profile. Require **Correctness of selected results** to equal 1 and **Selected comparison generation** to match publication confirmation. Compare the selected artifact fields and experiment timestamps. This dashboard omits GPU telemetry because this recipe cannot attribute device activity to its result.

## Investigate the behavior

### Workload variations

The two routes below start their respective prepared servers after the required environment variables are set. Wait for one allocation to finish before submitting the other. Both profiles use the same bounded qualification request.

```bash
bash slurm/30_engine_profile.sbatch --help
sbatch --chdir="$PWD" \
  --output="$PWD/results/30_engine_profile/logs/%j.out" \
  --error="$PWD/results/30_engine_profile/logs/%j.err" slurm/30_engine_profile.sbatch Qwen/Qwen2.5-0.5B-Instruct 7ae557604adf67be50417f59c2c2f167def9a775
bash slurm/30_engine_profile.trtllm.sbatch --help
sbatch --chdir="$PWD" \
  --output="$PWD/results/30_engine_profile/logs/%j.out" \
  --error="$PWD/results/30_engine_profile/logs/%j.err" slurm/30_engine_profile.trtllm.sbatch
```

Keep the workload size fixed for a comparison. If both sizes appear, treat them as separate workload campaigns. Repeat the baseline command to check variation.

Which component owns model conversion, repository layout, server readiness, and client measurement? Why can a healthy server reject a request with the wrong token-budget field? Keep these boundaries separate in your diagnosis.

vLLM can simplify flexible serving; TensorRT-LLM can provide more explicit optimized-engine control; Triton adds deployment/observability structure. Each increases version, artifact, and operational surface compared with a simple PyTorch mechanics runner.

Separate pools scale phases independently but add transfers, more failure modes, and capacity-planning complexity. Cache affinity saves prefill but can concentrate load. Open-loop tests reveal overload while potentially producing long queues and higher test cost.

**Nsight Systems: not applicable.** The HTTP client probes readiness and request schemas. These launchers own server startup and cleanup but do not start a GPU capture; profiling the client cannot capture server kernels. Use the owned serving experiments for GPU traces. Inspect the measured or modeled fields in this lab's dashboard; retain the artifact and its stated scope.

## If something goes wrong

Mixed Triton schema combinations fail before meaningful measurement. Inspect repository configuration and engine logs rather than trying arbitrary field names. Backend import errors require qualifying the image's Python dependencies before repeating startup; GPU visibility alone does not establish backend readiness. Retain any corrected dependency pin with the image and repository identity. Do not expose the service publicly or use an unrelated endpoint to bypass qualification.

An `MPI_Init_thread` or Slurm PMI-support error indicates that MPI startup
failed before model readiness. Confirm the job uses the supplied launcher and
the image's MPI runtime; preserve the private server log and qualify that
combination before repeating the request. Do not change cluster MPI libraries
to repair this single-process engine launch.

Avoid using an unpinned container or collecting server logs that contain prompt content.

Avoid counting a successful deployment as evidence of better latency or capacity.

Publication failure is separate from probe failure. Retain the JSON files and retry the same pair using the generation printed by the failed publisher. A stale-generation rejection means another selection won; review it before replacing it. Missing metrics remain unknown. GPU counter permissions and capture completeness belong to the separately owned profiling experiments.

## Takeaways and next step

Qualify the exact protocol and artifact before benchmarking. Use the AIPerf launcher for workload campaigns; Dynamo remains a separately gated advanced deployment, not a capability proven by this simple engine probe.

Fail closed until the image is qualified, readiness passes, and the benchmark contract is fixed.

List which engine settings can change scheduler or KV behavior.

Rate-match phases, include KV transfer and queueing, and keep unsupported topology claims pending.

State what evidence is required before KV-aware routing becomes a performance claim.
