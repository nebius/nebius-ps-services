# Course map

The complete portable artifact is [index.html](index.html). It contains all lesson prose, diagrams, exercises, answers, review cues, and exact embedded lab source.

## Part I: LLM training

| Phase | Modules | Main deliverable |
| --- | --- | --- |
| Frame the training lifecycle | 1 | Distinction among pretraining, continued pretraining, SFT, LoRA adaptation, reward-guided optimization, evaluation, and deployment artifacts |
| Represent and compute | 2–4 | Text-to-token-ID-to-embedding trace, decoder stack, and Q/K/V, attention, and MLP dataflow |
| Learn and preserve | 5–6 | Correct forward/loss/backward/update sequence plus evaluation and checkpoint/resume contract |
| Adapt | 7–8 | LoRA SFT accounting and simplified GRPO objective reasoning with explicit non-claims |

## Part II: LLM inference

| Phase | Modules | Main deliverable |
| --- | --- | --- |
| Verify and load | 9 | Pinned config, tokenizer, weights, generation, license, adapter/quantization, remote-code, warm-up, and readiness checklist |
| Generate | 10–11 | Prompt-tokenization-prefill-decode-stop lifecycle and exact KV-cache read/append model |
| Control output | 12 | Deterministic/stochastic sampling and quality-equivalence profile |

## Part III: Training performance optimization

| Phase | Modules | Main deliverable |
| --- | --- | --- |
| Budget memory and precision | 13–14 | Training-only parameter, gradient, optimizer, activation, temporary, precision, and scale ledger plus matched FP32/BF16/FP16 and optional FP8 evidence |
| Change batching and recomputation | 15 | Fixed-effective-batch accumulation/checkpointing experiment |
| Distribute | 16 | DDP/FSDP2 evidence plus a state/communication selection map for tensor, pipeline, context/sequence, and expert parallelism |
| Decide | 17 | Equivalent-work training benchmark with tokens/s, memory, disclosed MFU/HFU boundaries, and one justified keep/reject decision |

## Part IV: Inference performance optimization

| Phase | Modules | Main deliverable |
| --- | --- | --- |
| Budget and measure | 18–19 | Inference-only weights/load/prefill/KV/reserve ledger plus TTFT, ITL, E2E, throughput, queue, cache, arrival-model, and goodput contract |
| Schedule and compress | 20–21 | Continuous batching, paged/prefix KV, chunked prefill, phase-specific attention, CUDA Graph, speculative decoding, and quantization acceptance evidence |
| Place and rate-match | 22–23 | Serving DP/TP/PP/EP state and communication map plus disaggregated/agentic queue model |
| Decide | 24 | Separate training and inference optimization reports |

## Assessment model

- Each numbered module is one lesson with retrieval, a worked example, a practice task, a visible answer key, and a spaced-review cue.
- Every lab requires a prediction, primary evidence, interpretation, and troubleshooting record.
- Training changes require loss/gradient/parameter-update checks; serving changes require response correctness plus latency/throughput and capacity evidence.
- Completion requires both one-node and two-node live evidence after the cluster is provisioned.

## Recommended pacing

Plan for about 60 guided hours: 23 hours of lessons and review, 30 hours of labs, and 7 hours for the two capstone reports. Complete Parts I and II before optimizing: the training and inference mechanisms define what later measurements mean.
