# Inference And Serving

Use this reference for hosted model APIs, managed endpoints, self-hosted
inference, execution engines, model servers, serving control planes, gateways,
routing, caching, quantization, decoding optimizations, and capacity.

## Contents

- Layer model
- Deployment-mode decision
- Execution-engine decision
- Optimization triggers
- Routing and distributed cache
- Benchmark protocol
- Reliability and operations
- Candidate-family guidance

For current vLLM, SGLang, TensorRT-LLM, KServe, Ray Serve, quantization, and
hardware defaults, read `current-technology-baseline.md`. For exact model,
runtime, hardware, quantization, tokenizer, tool, deployment, and telemetry
edges, read `compatibility-contracts.md`.

## Layer Model

Keep these layers distinct:

1. Deployment model: Hosted or serverless API, managed dedicated endpoint,
   self-hosted service, or embedded execution.
2. Execution engine: Loads model artifacts and executes forward passes and
   decoding on the target hardware.
3. Model server: Exposes request, streaming, batching, health, and model-loading
   interfaces around one or more engines.
4. Serving control plane: Manages replicas, autoscaling, rollout, placement,
   model lifecycle, and sometimes traffic policy.
5. Gateway and policy boundary: Authenticates callers, applies quotas, validates
   schemas, records audit data, and normalizes external APIs.
6. Router: Selects model, adapter, replica, region, or prefill/decode worker.
7. Cache and distributed state: Prefix or KV cache, response cache, routing
   metadata, or transfer fabric.
8. Accelerator and infrastructure platform: Hardware, drivers, network,
   storage, scheduler, and failure domains.

A hosted provider may collapse all layers behind an API. A self-hosted design
must explicitly own each required layer. The phrase `managed inference` describes
an ownership model, not an execution engine category.

## Deployment-Mode Decision

### Hosted Or Serverless API

Prefer when model availability, data terms, regional support, quality, latency,
quotas, and economics meet the contract and infrastructure ownership is not a
requirement.

Validate:

- data use, retention, training use, region, encryption, identity, and audit;
- model and version pinning, deprecation, fallback, and availability;
- rate limits, concurrency, context, payload, streaming, tool, and batch limits;
- latency and cost at the real prompt and output distribution;
- portability of prompts, tool schemas, evaluations, and application contracts.

### Managed Dedicated Endpoint

Consider when dedicated capacity, selected model artifacts, network isolation,
regional placement, predictable performance, or provider-managed operations are
required.

Validate which layers the provider actually owns. Dedicated capacity does not
automatically guarantee warm availability, fixed performance, custom runtime
support, rollback semantics, or cost efficiency.

### Self-Hosted Service

Require a concrete reason such as unsupported model or artifact, residency,
custom kernels or decoding, hardware control, predictable high utilization,
specialized routing, or total-cost advantage at measured scale.

Account for accelerator supply, capacity planning, drivers, images, model
storage, startup time, autoscaling, upgrades, security, on-call, incident
response, and idle capacity. Compare total ownership, not only accelerator
hourly price.

### Embedded Or Edge

Use when offline operation, local privacy, device latency, bandwidth, or device
integration is a hard requirement. Validate model size, quantization, hardware,
thermal and power limits, distribution, updates, rollback, and telemetry.

## Execution-Engine Decision

Evaluate engines against the exact model and hardware:

- model architectures, multimodality, adapters, structured output, tool calls,
  and custom code;
- supported weight, checkpoint, and quantization formats;
- accelerator architecture, precision, kernels, tensor or pipeline parallelism,
  and multi-node support;
- scheduler, continuous batching, streaming, preemption, cancellation, and
  priority behavior;
- prefix caching, speculative decoding, long-context features, and
  prefill/decode disaggregation;
- API compatibility, observability, failure behavior, upgrade path, and
  community or vendor support.

Examples to investigate include vLLM, SGLang, NVIDIA TensorRT-LLM, provider
runtimes, and specialized modality runtimes. They have different model,
hardware, API, and operational trade-offs. Verify current support from official
project documentation and benchmark the exact artifact.

## Optimization Triggers

Keep every optimization `Conditional` until its trigger is measured.

### Quantization

Trigger: Model memory, bandwidth, throughput, or cost fails a target and a
supported lower-precision artifact is available.

Gate: Task quality and safety, calibration requirements, kernel support,
artifact portability, latency-throughput behavior, and operational complexity.
Do not infer quality from nominal bit width.

### Continuous And Dynamic Batching

Trigger: Concurrent requests leave accelerator capacity underused.

Gate: Throughput improvement at accepted time-to-first-token and inter-token
latency, fairness, cancellation, priority, and overload behavior.

### Prefix Or Prompt Caching

Trigger: A material fraction of requests share stable token prefixes.

Gate: Cache hit rate, saved prefill work, memory cost, eviction, invalidation,
model and adapter identity, tenant isolation, and privacy. Never share cache
state across trust boundaries without a proven key and isolation model.

### Speculative Decoding

Trigger: Decode is memory-bound, output latency is limiting, and a compatible
draft or speculative method can achieve useful acceptance.

Gate: Acceptance rate, added model or head memory, quality equivalence, tail
latency, concurrency, throughput, and operational cost. It may help lower-load
latency while reducing capacity or adding complexity at other loads.

### Chunked Prefill

Trigger: Long prompts block decode work or violate latency fairness.

Gate: Time-to-first-token, inter-token latency, total throughput, scheduler
fairness, memory, and behavior across short and long requests.

### Prefill And Decode Disaggregation

Trigger: Prompt and decode phases have materially different hardware or scaling
needs, interfere with each other, and workload scale can amortize transfer and
control-plane overhead.

Gate: KV transfer latency and bandwidth, failure handling, placement, routing,
cache ownership, autoscaling, observability, and total cost. Do not add it for a
small single-replica service.

### Adapter Multiplexing Or Multi-LoRA

Trigger: Many compatible adapters share one base model and dedicated replicas
waste capacity.

Gate: Adapter load and eviction, isolation, routing, versioning, latency,
memory, compatibility, rollback, and noisy-neighbor behavior.

### KV-Aware Routing

Trigger: Multiple replicas serve shared-prefix or continued-session traffic and
routing can materially increase cache reuse.

Gate: Cache identity, locality signal accuracy, routing overhead, imbalance,
failover, stale metadata, tenant isolation, and measured hit-rate benefit. A KV
router is unjustified when there is no reusable state or only one replica.

## Routing And Distributed Cache

Define routing order and authority:

- caller identity, tenant, model and adapter entitlement;
- model capability, version, region, health, capacity, and policy;
- session or prefix locality when safe;
- priority, deadline, fairness, quota, and cost policy;
- failover and fallback rules;
- retry ownership and idempotency;
- trace and decision records.

Avoid independent retries at gateway, router, server, and client. Assign one
retry owner per failure boundary and propagate deadlines and cancellation.

Treat distributed KV state as ephemeral execution state. Do not make correctness
depend on cache presence. Define loss, eviction, replica failure, transfer
failure, and version-change behavior.

## Benchmark Protocol

Freeze and record:

- model, revision, tokenizer, templates, adapters, quantization, runtime, image,
  drivers, kernels, hardware, topology, and configuration;
- prompt and output length distributions, request mix, model and adapter mix,
  streaming, tool or structured-output behavior, and shared-prefix pattern;
- offered request rates or concurrency levels, warmup, duration, seeds, and
  repetitions;
- quality and safety dataset and acceptance thresholds;
- all optimization flags and cache state.

Measure:

- time to first token;
- inter-token latency or time per output token;
- end-to-end p50, p95, and p99;
- input and output token throughput;
- requests or accepted tasks per second;
- queue time, active sequences, batch size, preemption, and rejection rate;
- accelerator utilization, memory, cache use, CPU, network, storage, and power
  when relevant;
- errors, cancellations, timeouts, cold start, rollout, and failure recovery;
- cost per accepted request or task at target availability and capacity buffer.

Plot or report the latency-throughput frontier. Do not compare one engine at
maximum throughput with another at a different latency target.

## Reliability And Operations

Define:

- readiness only after model and required artifacts are usable;
- startup, warmup, model download, corruption, and partial-load behavior;
- overload response, admission control, queue bounds, timeouts, and shedding;
- replica, node, zone, network, storage, and provider failure behavior;
- model and runtime rollout, canary, compatibility, rollback, and cache reset;
- autoscaling signals that account for queueing, active tokens, memory, and
  startup delay rather than CPU alone;
- observability from gateway through router, server, engine, and accelerator;
- capacity reserve, on-call owner, runbooks, and cost alerts.

## Candidate-Family Guidance

| Layer or need | Candidate families to verify | Selection boundary |
| --- | --- | --- |
| Hosted API | Model-provider API or model-as-a-service catalog | Quality, data terms, quotas, regions, versioning, latency, cost |
| Dedicated managed endpoint | Cloud or model-platform managed endpoint | Runtime control, capacity, isolation, networking, rollout, ownership |
| High-throughput open-weight serving | vLLM or SGLang-style engines | Exact model and hardware support, scheduler, API, benchmark, operations |
| NVIDIA-specific optimized execution | TensorRT-LLM-style engine | NVIDIA hardware, build flow, supported model and quantization, measured benefit |
| Kubernetes serving control | KServe or equivalent | Existing Kubernetes ownership, CRDs, autoscaling, rollout, router integration |
| Python application serving | Ray Serve or equivalent | Existing Ray ownership, composability, routing, autoscaling, fault model |

Do not recommend a family because it is popular. Verify current features,
release status, and exact compatibility from primary documentation.
