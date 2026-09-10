# Lab 11: Measure concurrent completion requests

A serving system's performance includes request handling and completion delivery, not just GPU kernels. This lab sends bounded concurrent requests to a local OpenAI-compatible endpoint and measures the entire client campaign. You will relate request throughput, output-token rate, and completion latency while keeping the model revision and generation settings fixed.

## Before you start

**Theory preparation:** Read Lessons 6–7 for HTTP/JSON, client/server boundaries, endpoint readiness, completion latency and shared-interval throughput. Reuse Lessons 1–5 for artifact, sampling and workload identity. The Lesson 6 visit inspects lifecycle; first measure requests after Lesson 7.

Qualify the vLLM container, model, and client environment. The supplied launcher owns server startup, readiness, client execution, metric snapshots, and cleanup. Teaching HTTP remains loopback-only; no public endpoint is required.

## Concepts and code path

A bounded thread pool overlaps blocking HTTP requests. Requests use a 120-second timeout. Request errors abort the run, and every response must report a positive completion-token count before results are written.

The client schedules requests with a concurrency limit, records completion durations, and counts generated tokens returned by the service. The campaign wall clock supports aggregate requests/s and output tokens/s. This nonstreaming route does not expose the first token's arrival time or individual token intervals.

## Practice

Run the launcher after its required image and runner variables are set. Inspect positional model/concurrency/revision options before changing load; a concurrency sweep must preserve the same artifact and prompt policy.

```bash
umask 077
bash slurm/vllm_benchmark.sbatch --help
sbatch slurm/vllm_benchmark.sbatch
```

## Check your results

Require every request to complete with generated tokens. Inspect request count, concurrency, latency summaries, request throughput, and output tokens/s. The launcher retains server evidence separately; a completed HTTP request is not a semantic-quality assessment.

## Investigate the behavior

As concurrency grows, does aggregate throughput improve while individual completion latency worsens? Distinguish actual generated tokens from requested maximum tokens. Explain why request throughput alone is misleading when output lengths differ.

## If something goes wrong

Connection failures require readiness and server-log inspection. Empty or failed responses invalidate the campaign, not just its slowest samples. Do not point the client at an unrelated external endpoint to bypass local startup.

## Takeaways and next step

Client measurements need declared arrival/load and output semantics. Use Lab 15 for streaming arrival observations and AIPerf for token-aware metrics, then repeat accepted comparisons across at least three independent engine trials.
