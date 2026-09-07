# Lab 10: Measure batched offline generation with vLLM

Offline inference submits a known collection of prompts to an engine without exposing an HTTP service. This lab runs real vLLM generation inside the qualified container and accounts for all measured requests and output tokens. You will distinguish engine batch throughput from online request latency and avoid reporting only the final iteration's tokens as the whole run.

## Before you start

**Theory preparation:** Read Lesson 6 for vLLM, offline versus HTTP execution, container identity, readiness and warm-up. Lessons 1–3 supply artifacts, tokenization, sampling and stopping; Lesson 5 supplies workload identity. Keep cold construction outside the declared warmed generation interval.

Prepare the approved container runner, exact `VLLM_IMAGE_DIGEST`, and immutable model artifact according to the runbook. Keep the engine environment isolated from mechanics PyTorch. One H100 must have enough memory for the model and configured context.

## Concepts and code path

The launcher runs the Python lab in the engine container. The lab constructs vLLM, warms generation, submits prompt batches, validates every returned completion, and aggregates prompt/output token counts and finish reasons across measured iterations. The elapsed window covers measured generation, not cold engine construction or an online queueing workload.

## Practice

Inspect the launcher and lab options before running the pinned default. `--enforce-eager` is an explicit engine option for a separately declared comparison, not an automatic fallback on initialization failure.

```bash
umask 077
bash slurm/vllm_offline.sbatch --help
sbatch slurm/vllm_offline.sbatch --profile smoke
sbatch slurm/vllm_offline.sbatch --profile smoke --enforce-eager
```

## Check your results

Require one completion per prompt, positive prompt counts, bounded positive output counts, and all iterations completed. Inspect total requests/tokens, per-request count summaries, finish reasons, elapsed seconds, and output tokens/s.

## Investigate the behavior

Why can actual output length be less than the maximum token budget? Explain why comparing runs with different stopping behavior can change throughput without improving the engine. Which startup costs are excluded from this measurement?

## If something goes wrong

Missing image identity, artifact mismatch, engine startup failure, or insufficient memory blocks this live lab. Do not replace it with a mechanics simulation. Preserve private logs and reduce the declared workload only as a new trial.

## Takeaways and next step

Offline throughput requires complete work accounting and fixed generation semantics. Next, use the serving clients to measure request-level behavior under concurrency; offline output rate does not establish TTFT or online goodput.
