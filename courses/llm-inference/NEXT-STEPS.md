# Where to Go Next

These optional directions extend inference
mechanics into evolving serving systems and established advanced measurement
concepts. They do not change the course's pinned environments or add completion
gates. Documentation support is not proof of a working H100 deployment.
Start with goodput or kernel selection; approach the distributed topics only
after understanding request timelines, cache ownership and transport costs.

## Dynamo cache-aware routing across memory tiers

Cache-aware routing selects a worker using both reusable prefix state and
current load. When cached state can move between GPU memory, CPU memory and
storage, the router also needs accurate information about where that state
resides. Working offload and useful routing visibility are separate capabilities.
**Investigate**: Could offloading succeed while the router remains unaware
of the reusable prefix, and how would that change request placement?
**Scope**: Evolving distributed serving. Support is backend/version-specific;
development documentation includes planned or incompletely validated paths.
Extra capacity and transport must be qualified beyond the two-node baseline.
[Study KV-aware routing](https://docs.nvidia.com/dynamo/dev/knowledge-base/concepts/system-architecture/kv-aware-routing) and
[the offloading support matrix](https://docs.nvidia.com/dynamo/dev/knowledge-base/modular-components/router/offloading-support-matrix).

## FlashInfer and inference-kernel selection

FlashInfer supplies optimized building blocks for attention, matrix
multiplication and MoE execution. It is a kernel layer, not a complete
replacement for a serving engine. Moving beyond the SDPA lab means checking
shape eligibility, cache layout, initialization cost and whether a faster
kernel improves whole requests.
**Investigate**: How would you separate first-use compilation, warmed kernel
time and end-to-end request latency?
**Scope**: Evolving open-source project; its release page records v0.6.18 on
August 29, 2026. H100 is supported, but individual low-bit or
architecture-specific paths have narrower requirements.
[Read FlashInfer's support information](https://github.com/flashinfer-ai/flashinfer) and
[release highlights](https://flashinfer.ai/releases/).

## Speculative decoding beyond one draft model

Speculative decoding proposes candidate tokens and uses a target model to
verify them. Newer proposal methods use learned draft features, model-specific
multi-token prediction (MTP), or repeated token patterns. Their setup costs
and acceptance behavior differ, so selecting a method is a workload decision
rather than an automatic speedup.
**Investigate**: When could a repetition-based proposal outperform a neural
draft, and what happens on nonrepetitive prompts?
**Scope**: Evolving TensorRT-LLM features, including EAGLE 3 and MTP-related
paths. Check the exact model, draft, backend and sampling guarantees; a method
name alone establishes neither H100 support nor distribution-preserving output.
[Compare TensorRT-LLM speculative methods](https://nvidia.github.io/TensorRT-LLM/features/speculative-decoding.html).

## Quantization-aware distillation with Model Optimizer

Quantization-aware distillation uses a higher-precision teacher's outputs to
help a quantized student recover quality lost during compression. It extends
post-training quantization evaluation into a deployment decision that also
has a training cost. The goal is preserved application behavior, not merely
a smaller checkpoint.
**Investigate**: Does recovered benchmark accuracy also preserve the quality
of the intended application?
**Scope**: Evolving NVIDIA Model Optimizer workflow. FP8 deployment can target
Hopper; native NVFP4 compute targets Blackwell. Budget teacher/student memory
and training separately. This is deployment-oriented reading; training
mechanics remain owned by the Training course.
[Study Model Optimizer's QAT and QAD recipes](https://github.com/NVIDIA/Model-Optimizer/blob/main/examples/llm_qat/README.md).

## Multimodal encode–prefill–decode serving

Multimodal requests may require an encoder to turn images or other inputs
into representations the language model consumes. Separating encoding from
prefill and decode creates another worker pool, queue and transfer boundary.
Text-token throughput alone no longer describes the whole request.
**Investigate**: Where should encoder waiting, execution and transfer time
appear in time to first token?
**Scope**: Evolving, model-specific distributed architecture. Backend support
differs for input forms such as image URLs and precomputed embeddings.
Transport, resource allocation and GPU capacity require separate qualification;
this is not a required two-H100 exercise.
[Read Dynamo's encoder-disaggregation guide](https://docs.dynamo.nvidia.com/dynamo/multimodal/encoder-disaggregation).

## AIPerf goodput and service-level attainment

Goodput counts acceptable requests completed per unit time. Service-level
attainment is the fraction of requests meeting a stated target, such as both
first-token and inter-token latency limits. These measures complement
aggregate throughput: more acceptable completions per second can coexist
with an unacceptable fraction of slow requests.
**Investigate**: Can the highest-goodput configuration fail a requirement
that 99% of requests meet every service-level objective (SLO)?
**Scope**: Established measurement distinction in evolving AIPerf tooling,
not Blackwell-specific. Use a qualified endpoint and check the client's
metric definitions, denominator and version.
[Study AIPerf goodput and attainment](https://docs.nvidia.com/aiperf/tutorials/metrics-analysis/benchmark-goodput-with-ai-perf).
