# Syllabus

## Part I: LLM training

| Module | Topic | Required output | Lab |
| --- | --- | --- | --- |
| 1 | Complete LLM training lifecycle | Stage map from corpus and objective through checkpoints and deployment artifact | Guided lifecycle trace |
| 2 | Tokens, token IDs, embeddings, labels, padding, and packing | Hand-worked representation and shifted-label/mask example | 13 loss masking |
| 3 | Decoder-only architecture | Embedding-to-logits stack and shape trace | 01 tiny transformer train |
| 4 | Q/K/V attention, causal mask, heads, MLP, residuals, and normalization | Decoder-block dataflow and shape trace | 01 tiny transformer train |
| 5 | Forward, loss, gradients, optimizer, and update | Verified parameter update and accumulation/zeroing explanation | 01 tiny transformer train |
| 6 | Evaluation, checkpoints, and exact resume | Held-out, checkpoint-state, and resume-equivalence checklist | Guided failure/recovery exercise |
| 7 | SFT and LoRA | Trainable-parameter and held-out evaluation report | 05 LoRA SFT, 13 loss mask |
| 8 | GRPO objective and system loop | Group advantages, clipping/KL, finite-gradient, rollout, and weight-version reasoning | 06 GRPO objective, 07 trainer |

## Part II: LLM inference

| Module | Topic | Required output | Lab |
| --- | --- | --- | --- |
| 9 | Model artifacts and secure loading | Pinned bundle, license, config, tokenizer, weights, adapter/quantization, and code-execution audit | 16 artifact audit |
| 10 | Autoregressive inference lifecycle | Prompt-to-tokenize-to-prefill-to-decode-to-stop trace | 09 prefill/decode |
| 11 | Prefill, decode, Q/K/V, and KV cache | Phase-specific latency and cache-read/append model | 08 KV cache, 09 prefill/decode |
| 12 | Sampling, stopping, streaming, and quality equivalence | Deterministic and stochastic benchmark profiles | 17 sampling semantics, 15 streaming client |

## Part III: Training performance optimization

| Module | Topic | Required output | Lab |
| --- | --- | --- | --- |
| 13 | Training memory ledger | Parameter, gradient, optimizer, activation, temporary, and allocator estimate | Guided memory exercise |
| 14 | Training precision | Per-state dtype ledger, identical-state loss/gradient/update gates, warmed FP8 recipe evidence, and quality plan | 21 mixed precision and optional 22 Transformer Engine FP8 |
| 15 | Batch size, accumulation, and activation checkpointing | Fixed-effective-batch memory/time/equivalence tradeoff | 02 accumulation, 14 checkpointing |
| 16 | Distributed training parallelism | DDP/FSDP2 evidence plus state and communication maps for tensor, pipeline, context/sequence, and expert parallelism | 03 DDP, 04 FSDP2, 19 tensor-parallel mechanics, and guided strategy map |
| 17 | Training performance workflow | Fixed-token tokens/s, memory, disclosed MFU/HFU boundaries, bottleneck, and keep/reject report | 21 mixed precision plus optimization-course synthesis |

## Part IV: Inference performance optimization

| Module | Topic | Required output | Lab |
| --- | --- | --- | --- |
| 18 | Inference memory ledger | Weights, load peak, prefill temporary, KV, runtime reserve, and capacity estimate | 08 KV cache plus guided exercise |
| 19 | TTFT, ITL/gaps, E2E, throughput, queue, cache, and goodput | Open-/closed-loop benchmark identity and client/server metric-boundary map | 11 client, 15 streaming client, server metrics |
| 20 | Scheduling, KV allocation, attention backends, CUDA Graphs, and speculative decoding | Phase/backend map, graph bucket/fallback evidence, cache A/B, and accepted-prefix/recovery/bonus evidence | 10 offline, 18 padding, 20 prefix cache, 23 speculation, optimization Lab 04 |
| 21 | Quantization | BF16 versus supported-method capacity, kernel, latency, and quality plan | Guided acceptance matrix |
| 22 | Serving DP, TP, PP, EP, and MoE | Two-node state, directional communication, and fit/throughput comparison | 19 tensor parallel, 12 MoE, vLLM two-node |
| 23 | P/D disaggregation and agentic/tool queues | Rate-match model with validated tool-result reinsertion through prefill | Queueing exercise |
| 24 | Training and inference optimization reports | Two separate keep/reject reports | Course capstone |

## Review and completion

- Complete retrieval reviews after modules 5, 12, 17, and 23.
- Explain every acronym used in the final reports.
- Pass semantic correctness labs before timing advanced frameworks.
- Preserve live results from the ordered smoke-test; distinguish observations, inferences, and unverified hypotheses.
