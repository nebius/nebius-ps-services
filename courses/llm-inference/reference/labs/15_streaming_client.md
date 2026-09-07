# Lab 15: Observe first content and streaming gaps

Users experience streamed generation as a wait for first content followed by a sequence of arrivals. This lab records those client-side events from a loopback service. You will distinguish first-content latency, inter-chunk gaps, and total completion time, while recognizing that an HTTP chunk can contain multiple tokens and is not itself a token-level timing unit.

## Before you start

**Theory preparation:** Read Lessons 6–7 for the service lifecycle, concurrent clients, SSE event parsing, first-content timing, chunk gaps and token-aware metric limits. Keep Lesson 3’s stopping/sampling and Lesson 5’s workload fixed before interpreting arrivals.

Qualify the engine and client environments and use the supplied streaming launcher. It owns the local server lifecycle. Keep prompts, responses, and raw streaming logs private and preserve the immutable model revision.

H100 throughput can keep rising with batching after TTFT/ITL become unacceptable. Measure the complete throughput-latency curve instead of quoting one maximum point.

## Concepts and code path

Concurrent client tasks open streaming completion requests, parse arriving content, and timestamp the first nonempty content and subsequent chunks. The client aggregates first-content and completion metrics over the campaign. It counts received characters but does not tokenize each arrival, so its gap statistic cannot be relabeled ITL or TPOT.

## Practice

Given 16 closed-loop clients producing 8,000 output tokens/s with 120-millisecond p95 TTFT, increase to open-loop arrivals beyond service capacity. Change the arrival model while holding requests and generation policy fixed. Expected observation: instantaneous GPU throughput may remain high while queueing and p95 TTFT grow without bound; goodput falls because more requests miss the SLO.

Run Labs 11 and 15 and reconcile their client boundaries with server metrics. Use AIPerf for token-aware ITL/TPOT. Contrast these with Lab 25's synthetic operator timings: batch-row updates per second are not output tokens per second, and neither a recurrent step nor input projection is a service TTFT measurement.

Inspect request-count and concurrency options before changing load. The baseline launcher uses bounded generation and a loopback endpoint, avoiding any requirement to expose the service publicly.

```bash
umask 077
bash slurm/vllm_streaming_benchmark.sbatch --help
sbatch slurm/vllm_streaming_benchmark.sbatch
```

## Check your results

Require all streams to produce content. Inspect `median_ttft_ms`, completion latency, `median_inter_chunk_gap_ms`, request throughput, and received characters. Treat TTFT here as the client's first-content observation; transport buffering can affect it.

Every stream must also reach its `[DONE]` marker without an engine error event.
Content followed by an error or a truncated connection fails the campaign before
the client writes a result artifact.

Retain clock boundary, metric definitions, percentiles, warm-up, concurrency, token counts, failures, and service objectives.

Report rate and latency together; a system can improve one while harming the other.

## Investigate the behavior

Draw request start, first content, later chunks, and completion. Why can one received chunk contain several tokens generated during the preceding gap? Compare these observations with server metrics without assuming their clocks and boundaries are identical.

Higher concurrency improves system tokens/s until saturation but reduces per-user rate and increases tails. Native engine benchmarks isolate server work; endpoint clients include the public protocol. Use both for different boundaries.

## If something goes wrong

Empty streams, malformed events, or premature termination are failures, not short successful requests. A missing gap statistic may mean too few content arrivals to form an interval; it is not zero token latency.

Avoid calling average decode duration “ITL” without measuring individual token gaps.

## Takeaways and next step

Measure what the client actually observes and name it accurately. Use the qualified AIPerf workflow for token-aware ITL/TPOT and repeat fixed-workload trials before making a serving-latency claim.

Define every metric formula and collection boundary before comparing systems.

Explain why TTFT can rise while GPU utilization and total throughput rise.
