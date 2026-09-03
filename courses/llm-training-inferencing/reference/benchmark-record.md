# LLM experiment record

- Training or inference phase:
- Model ID and immutable revision if used:
- Dataset/workload description and license check:
- Random course run ID and private artifact location:
- Node count, GPU count, MIG state, and topology class (no node or device IDs):
- Training or serving environment lock file:
- Driver, CUDA, PyTorch or vLLM, NCCL, and Slurm versions:
- Batch, microbatch, sequence lengths, dtype, and parallelism:
- Warm-up and measured window:
- Correctness or quality criterion:
- Memory: weights, gradients, optimizer, activations, and/or KV cache:
- Training metric: tokens/s, step time, model FLOP utilization (MFU) if derived:
- Serving metric: TTFT, inter-token latency, end-to-end p50/p90/p99, tokens/s:
- Sanitized aggregate evidence summary:
- Conclusion and next experiment:

Never present a latency or throughput result without its workload shape,
concurrency, and software/hardware context. Keep raw results, profiler output,
server logs, and Slurm output private. Share only a separately reviewed summary
that follows [evidence-security.md](evidence-security.md).
