# Lab 30: Probe an engine's readiness and request schema

Different inference servers expose different readiness endpoints and request schemas even when they host similar models. This lab sends one bounded request through either an OpenAI-compatible API or a declared TensorRT-LLM/Triton repository profile. You will verify the client/server contract before benchmarking, without treating a successful readiness response as proof of correct generation or sustained throughput.

## Before you start

Use the readiness and request-schema exercise after Lesson 6. Return after Lesson 15 for the separately launched AIPerf campaign and the paper disaggregation exercise. Those later activities do not change what this Python readiness probe measures or make disaggregation part of the supplied service.

**Theory preparation:** Read Lesson 6 for engines versus servers, HTTP/JSON, model discovery, readiness, container identity and distinct OpenAI-compatible/Triton schemas. Lessons 1–3 supply model, token and generation semantics. Qualify the selected server route before the bounded request.

For OpenAI mode, an authorized qualified loopback server must already be running in the same host context as the client. For Triton, set the reviewed repository, matching profile/model/token field, exact image digest, and container runner from the runbook.

Use images and engine versions that explicitly support H100/SM90 and the host driver. Do not combine mechanics dependencies with heavyweight engine environments.

Dynamo disaggregation is advanced and conditional. Two one-GPU nodes can run a bounded path experiment only when the pinned stack supports transfer; they cannot prove production RDMA or multi-worker scaling.

## Concepts and code path

The Python client selects a protocol, probes readiness or model discovery, sends a bounded generation request, and validates a response field. Triton profiles are explicit: `llmapi` uses `tensorrt_llm` with `sampling_param_max_tokens`; `inflight_batcher` uses `ensemble` or `tensorrt_llm_bls` with `max_tokens`. The Triton launcher owns startup and cleanup; this client does not compile an engine.

The launcher exports one validated run ID to the client and passes a results
directory through `--output-dir`. The client writes
`results/30_engine_profile-run-RUN_ID.json`; the matching private server log is
`logs/trtllm-triton-run-RUN_ID.log`. Use that shared identifier to correlate
startup and request evidence. An output directory is not a JSON filename.

## Practice

### After Lesson 6

Given a server process started at time zero, weights ready at 45 seconds, graph warm-up complete at 70 seconds, and the first request sent at 10 seconds, that request's TTFT is not a steady-state measurement. Change the launcher to wait for readiness, run a correctness probe, and warm the declared buckets. Expected observation: the benchmark excludes startup while a separate startup metric retains it.

Begin with the single-GPU lifecycle: run Lab 10 for real vLLM offline generation using its dedicated container launcher, then inspect the readiness, deterministic probe and cleanup in the serving launcher before its first measured run in Lesson 7. Lab 30's default `openai` probe requires an already qualified local server and a matching served model name; the regular benchmark launcher runs Lab 11, not Lab 30. For TensorRT-LLM/Triton, use the dedicated launcher and declare the reviewed repository as `llmapi` with `tensorrt_llm`/`sampling_param_max_tokens`, or `inflight_batcher` with `ensemble` or `tensorrt_llm_bls`/`max_tokens`; the launcher rejects mixed schemas before startup. Treat multi-LoRA and multimodal scenarios as advanced revisits after the core scheduling and measurement lessons, not first-server requirements.

### Run the supplied experiment

The first route assumes an already qualified server and a matching served model name in `COURSE_SERVED_MODEL`. The second route starts the reviewed Triton repository through its dedicated launcher after all required environment variables are set.

```bash
umask 077
python labs/30_engine_profile.py --protocol openai --server-url http://127.0.0.1:8000 --model "${COURSE_SERVED_MODEL:?set the existing served model name}" --profile smoke
bash slurm/trtllm_triton.sbatch --help
sbatch slurm/trtllm_triton.sbatch
```

### After Lesson 15

Given prefill capacity of 200,000 prompt tokens/s, suppose arrivals require 250,000 prompt tokens/s. Decode has enough capacity for the corresponding output workload, but the prefill queue grows; adding decode workers cannot remove that bottleneck. Change the allocation to increase prefill capacity while accounting for KV handoff cost within the TTFT budget. Expected observation: the backlog can drain only if sustained prefill capacity exceeds the offered load and handoff, routing and decode can keep up.

After Lesson 15 and qualification of both images and the container runner, inspect `bash slurm/aiperf.sbatch --help` and submit the campaign with `sbatch slurm/aiperf.sbatch`. It owns its server, workload and cleanup independently of this readiness probe. Treat the disaggregation calculation as a paper exercise: the supplied Dynamo preflight checks GPU visibility only; it does not start phase workers or validate KV transfer.

## Check your results

Require valid generated-response structure and inspect protocol, model identity, request duration, and available usage fields. One bounded request establishes API activation, not a latency distribution, a semantic-quality score, or a benchmark campaign.

In OpenAI mode, the first completion must contain a nonempty text string; an
empty choice object is a failed probe. The AIPerf launcher pins its tokenizer to
the same immutable revision as the server so input and output token counts use
the declared model's vocabulary.

Retain immutable image/revision, arguments, readiness, sanitized logs, workload, metrics, and shutdown result.

Engine comparisons require equivalent artifacts, requests, tokenization, and accepted quality.

Retain arrival model, ISL/OSL, concurrency, completions, service metrics, route/cache decisions, and transfer assumptions.

Two one-GPU nodes can demonstrate control flow but not production-scale disaggregation or RDMA performance.

## Investigate the behavior

Which component owns model conversion, repository layout, server readiness, and client measurement? Why can a healthy server reject a request with the wrong token-budget field? Keep these boundaries separate in your diagnosis.

vLLM can simplify flexible serving; TensorRT-LLM can provide more explicit optimized-engine control; Triton adds deployment/observability structure. Each increases version, artifact, and operational surface compared with a simple PyTorch mechanics runner.

Separate pools scale phases independently but add transfers, more failure modes, and capacity-planning complexity. Cache affinity saves prefill but can concentrate load. Open-loop tests reveal overload while potentially producing long queues and higher test cost.

## If something goes wrong

Mixed Triton schema combinations fail before meaningful measurement. Inspect repository configuration and engine logs rather than trying arbitrary field names. Do not expose the service publicly or use an unrelated endpoint to bypass qualification.

Avoid using an unpinned container or collecting server logs that contain prompt content.

Avoid counting a successful deployment as evidence of better latency or capacity.

## Takeaways and next step

Qualify the exact protocol and artifact before benchmarking. Use the AIPerf launcher for workload campaigns; Dynamo remains a separately gated advanced deployment, not a capability proven by this simple engine probe.

Fail closed until the image is qualified, readiness passes, and the benchmark contract is fixed.

List which engine settings can change scheduler or KV behavior.

Rate-match phases, include KV transfer and queueing, and keep unsupported topology claims pending.

State what evidence is required before KV-aware routing becomes a performance claim.
