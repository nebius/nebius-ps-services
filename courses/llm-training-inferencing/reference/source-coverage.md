# Source-concept coverage

This public-safe map records the source audit without retaining source
identifiers, non-public metadata, or environment-specific examples.

| Topic family | Explanation | Diagram | Practice | Status |
| --- | --- | --- | --- | --- |
| Training lifecycle and objective families | Module 1 | End-to-end training lifecycle | Guided trace | Covered |
| Tokens, token IDs, embeddings, labels, masks, and packing | Module 2 | Text-to-embedding and shifted-label flow | Lab 13 | Covered |
| Decoder stack, Q/K/V, causal attention, MLP, residual, normalization, and logits | Modules 3–4 | Decoder stack and block dataflow | Lab 01 | Covered |
| Forward, loss, gradients, optimizer, evaluation, checkpoint, and resume | Modules 5–6 | Correct training-step flow | Lab 01 plus guided resume exercise | Covered |
| SFT, LoRA, GRPO, rollout/reward/training systems | Modules 7–8 | Adaptation comparison and RL system loop | Labs 05–07 | Covered |
| Config, tokenizer, weights, adapters, quantization metadata, license, and secure loading | Module 9 | Every-artifact verification and loading lifecycle | Lab 16 | Covered |
| Inference lifecycle, prefill, decode, KV cache, sampling, streaming, and stopping | Modules 10–12 | Inference lifecycle and KV read/append | Labs 08, 09, 15, and 17 | Covered |
| Training memory, state-specific mixed precision, gradient scaling, and FP8 recipes | Modules 13–14 | Training-only memory and dtype ledgers | Lab 21 identical-state loss/gradient/update gates and optional Lab 22 warmed FP8 gates | Covered; FP8 live run is dependency-gated |
| Accumulation, checkpointing, DDP/FSDP2, ZeRO-style state sharding, TP/PP/context/sequence/EP, MFU/HFU, and training optimization | Modules 15–17 | State/collective map and metric contract | Labs 02–04, 14, 19, and 21 plus guided advanced-strategy map | Covered; advanced strategies remain bounded mechanics |
| Inference memory, TTFT, ITL, E2E, queue/cache, throughput, tails, open-/closed-loop load, and goodput | Modules 18–19 | Inference-only ledger and latency decomposition | Labs 08, 11, and 15 plus metrics snapshots | Covered |
| Continuous batching, paged/prefix KV, chunked prefill, phase-specific attention, CUDA Graphs, and speculative decoding | Module 20 | Block pool, phase/backend/graph path, and proposal-verification-recovery/bonus flow | Labs 10, 18, 20, and 23; optional companion: GPU Performance Optimization Lab 04 | Covered |
| Quantization and H100 kernel/quality gates | Module 21 | Phase memory context | Guided acceptance matrix | Covered; artifact-specific live lab intentionally deferred |
| Serving DP, TP, PP, EP, and MoE | Module 22 | Replica/model-parallel and directional communication paths | Labs 12 and 19 plus two-node vLLM | Covered |
| P/D disaggregation, rate matching, and tool/agent queues | Module 23 | Queue path with tool results returning through tokenization and prefill | Queueing/rate-match exercise | Covered |
| Separate training and inference optimization | Module 24 | Capstone evidence checklist | Ordered smoke-test | Covered |

Modules 20 and 23 explain multi-LoRA, multimodal serving, and full production
prefill/decode disaggregation as advanced engine extensions. They are not
bundled as core live systems because they require additional adapters,
modalities, workers, or artifact-specific quality baselines. Lab 23 now covers
the source guide's speculative-decoding acceptance mechanics on one H100 while
explicitly avoiding a production-engine speed claim.
