# Current AI Technology Baseline

Use this dated profile as a shortlist for a Python-first production AI system.
It is not a universal stack and does not replace workload contracts, official
source verification, compatibility tests, target evaluation, or owner review.

Baseline date: 2026-08-16
Agent-framework preference reviewed: 2026-11-13; other dated claims must be
revalidated when selected.

## Contents

- Baseline rules
- Training and model lifecycle
- Model access
- Agent applications and durability
- Capabilities and interoperability
- Agent-facing UI
- Inference and serving
- Retrieval and data
- Evaluation and operations
- Revalidation triggers

## Baseline Rules

For each selected component, record:

- `Status`: `Required`, `Conditional`, `Deferred`, or `Rejected`.
- `Evidence`: `Measured`, `Officially documented`, or `Assumed`.
- `Requirement`: the current need it satisfies.
- `Acceptance gate`: the target-specific proof it must pass.
- `Switch condition`: the observable event that reopens the decision.
- `Owner`: the team or role that operates and recovers it.

Begin with the current system or no new component. The statuses below are
starting positions only; the frozen workload decides the final status.

Do not embed a mutable "best model" list. Select an exact provider, model ID,
revision or immutable alias, region, endpoint, adapter, tokenizer, prompt or
chat template, tool schema, and evaluation identity for each deployment.

## Training And Model Lifecycle

| Capability | Baseline | Starting status | Adoption trigger | Revisit or replacement trigger |
| --- | --- | --- | --- | --- |
| Core framework | PyTorch | Conditional | A training, tuning, or owned open-weight workload exists. | A qualified platform or specialized framework must own the loop. |
| Model and data libraries | Transformers and Datasets | Conditional | A compatible model and dataset workflow benefits from their maintained abstractions. | The selected architecture or data path requires a stronger native library or custom loop. |
| Launch and portability | Accelerate | Conditional | More than one launcher or hardware environment is expected and the abstraction removes code. | An existing platform owns launch semantics or a specialized framework must own the mesh. |
| Parameter-efficient tuning | PEFT | Conditional | Parameter-efficient adaptation passes the target quality, memory, and artifact gates. | A measured full-tuning gain justifies its compute, memory, and artifact cost. |
| Post-training | TRL | Conditional | A supported post-training algorithm is required and TRL passes the target gates. | A provider-native or specialized stack wins the exact quality and operations gate. |
| Replicated multi-GPU | DDP first | Conditional | One accelerator passes correctness and memory gates but misses the duration or throughput target. | Replicated state no longer fits or DDP fails the scaling gate. |
| Native state sharding | FSDP2 with `fully_shard` | Conditional | Model-state sharding is required. | FSDP2 plus native parallelism cannot meet memory, throughput, recovery, or support needs. |
| Multi-axis large-model training | Megatron Core or NeMo candidate | Deferred | Do not adopt from the baseline. | Reopen only when FSDP2 plus native parallelism cannot meet the measured requirements. |
| Distributed checkpoint | PyTorch Distributed Checkpoint | Conditional | Distributed training requires reshardable or resumable state. | The chosen framework owns a better qualified checkpoint path. |
| Experiment and artifact lineage | MLflow or an existing organizational authority | Conditional | Training or promotion needs reproducible lineage and the existing platform does not provide it. | Another authority satisfies the immutable lineage, recovery, and promotion contract with lower ownership cost. |

Use this escalation ladder:

```text
single accelerator
  -> DDP when replicated state fits and more throughput is required
  -> FSDP2 when model-state sharding is required, possibly bypassing DDP
  -> FSDP2 plus one measured additional parallelism axis
  -> specialized multi-axis framework
```

Keep source data, transformed data, training checkpoints, serving artifacts,
evaluation records, and registry promotion distinct. A checkpoint is valid only
after a representative resume test. Production serves an immutable qualified
artifact, not a mutable training checkpoint directory.

Read `pytorch-training-stack.md` for the complete decision tree.

## Model Access

| Capability | Baseline | Starting status | Adoption trigger |
| --- | --- | --- | --- |
| Direct request | Official task API or existing qualified adapter | Conditional | One bounded call satisfies the workload |
| Agent model access | Preferred SDK provider seams with exact capability profile | Conditional | Agentic behavior requires model calls |
| Multi-provider routing | Application eligibility policy plus qualified adapters | Conditional | Several routes, regions or providers are required |
| Self-hosted inference | Verified adapter to the selected endpoint | Conditional | Data, model control, capacity or economics justify ownership |
| Shared gateway | Existing qualified gateway or evaluated alternative | Conditional | Shared policy/credential/traffic ownership warrants a service |

OpenAI Agents SDK preference does not select a provider. Qualify every required
provider/model/endpoint/protocol combination, including non-OpenAI routes.
Do not infer Responses, tools, structured outputs or retention from API shape.
Read `model-provider-contract.md` for exact-route gates and conditional shared
contracts; read `openai-agents-sdk-integration.md` for supported SDK seams.

## Agent Applications And Durability

| Capability | Baseline | Starting status | Adoption trigger |
| --- | --- | --- | --- |
| General agent kernel | OpenAI Agents SDK | Conditional | Model-owned actions/continuation are required; honor explicit overrides and fixed stacks |
| Alternative general kernel | Pydantic AI or another qualified framework | Conditional | Explicit user override or accepted fixed project choice |
| Bounded specialist | Selected kernel with scoped tools and budgets | Conditional | A separate responsibility improves accepted outcomes |
| Coding specialist | Codex SDK | Conditional | Coding-harness behavior is required |
| Deep Codex client | App-server integration | Conditional | Product client needs the lower-level integration surface |
| Integration library | Selected maintained integrations | Conditional | They reduce total complexity without adding a second kernel |
| Explicit state graph | LangGraph | Conditional | First-class graph semantics are required |
| Durable execution | Existing qualified owner, Temporal, DBOS, Prefect or Restate | Conditional | Recovery, long waits, timers or effects require durability |

A failed hard gate blocks the preferred SDK; present alternatives and seek an
explicit override. Never silently select another framework or infer universal
SDK superiority. Keep one primary agent loop and one owner per retry, effect
and checkpoint boundary. Graphs and durability may wrap deterministic or
agentic work. No optional layer becomes mandatory because an SDK is preferred.

Use the application-owned facade, state, tools, policy, budgets and evaluation
boundaries in `provider-agnostic-agent-platform.md`. Start in-process and add
services only for a demonstrated workload or ownership trigger.

## Capabilities And Interoperability

Use this escalation order:

1. Direct typed function for a private in-process capability.
2. Normal internal API across an ordinary service boundary.
3. MCP for an independently deployed reusable capability used by agent hosts.
4. Agents-as-tools for a bounded delegated agent capability.
5. A2A v1.0 for independently owned, deployed, opaque remote agents.

| Capability | Baseline | Starting status | Switch condition |
| --- | --- | --- | --- |
| Reusable procedure | Agent Skills | Conditional | The host supports Skills and the knowledge belongs in procedure rather than code. |
| Repository instructions | AGENTS.md | Conditional | Coding agents operate in the repository. |
| MCP core | Revision `2026-07-28` with an exact official Tier 1 SDK version | Conditional | A reusable independent capability boundary exists. |
| Python MCP productivity | FastMCP at an exact qualified release | Conditional | It reduces Python server/client code and passes Tier 1 interoperability. |
| Local MCP transport | `stdio` | Conditional | Server and host share the local machine or process boundary. |
| Remote MCP transport | Streamable HTTP | Conditional | The server is independently deployed or serves multiple remote clients. |
| MCP embedded UI | MCP Apps revision `2026-01-26` | Conditional | The UI belongs to the capability and every target host passes compatibility and accessibility tests. |
| MCP durable operation handle | MCP Tasks at an exact selected revision | Conditional | Long operations need a protocol-visible handle and the selected Draft revision is explicitly accepted and qualified. |
| Remote agent protocol | A2A v1.0 | Conditional | Independent agent ownership makes a tool or ordinary API inadequate. |

MCP core revisions use dates. Never call MCP `2026-07-28` "V2." SDK,
framework, Apps, Tasks, host, gateway, and protocol core versions are separate.
Use an official Tier 1 SDK as conformance authority and FastMCP only as a
qualified productivity layer.

An MCP Task is not a workflow engine. Back it with Temporal, a job system,
provider batch API, queue worker, or explicit database state machine.

## Agent-Facing UI

| Capability | Baseline | Starting status | Switch condition |
| --- | --- | --- | --- |
| Product navigation and major workflows | Normal product UI and API | Conditional | Resolve to Required when users interact with the system; never delegate product authentication or primary navigation to an embedded capability. |
| Runtime-to-frontend events | AG-UI | Conditional | Several runtimes or frontends need one interoperable event contract. |
| Capability-owned embedded UI | MCP Apps | Conditional | A specific MCP tool owns the interactive view and host support is qualified. |
| Coding agent to editor/terminal | ACP | Conditional | The product is a coding-agent client. |

For MCP Apps require sandboxed iframe isolation, strict CSP, an explicit bridge
allowlist, host-owned approval UI, output encoding, tenant-bound data,
restricted network and storage, resource integrity, text fallback,
accessibility, and per-host tests. Model-written executable UI remains
Conditional behind stronger sandbox and supply-chain controls.

## Inference And Serving

Choose deployment ownership before the engine:

1. Hosted provider runtime for closed models or when platform ownership is not
   justified.
2. Managed dedicated endpoint for private capacity without full serving-stack
   ownership.
3. Self-hosted serving only for open weights, data boundary, custom kernels,
   predictable capacity, or measured economics.
4. Embedded or edge execution only for bounded offline or local workloads.

For self-hosted open-weight inference, benchmark:

- vLLM first as the general serving baseline;
- SGLang for prefix-heavy, agent-heavy, or structured execution workloads;
- TensorRT-LLM for qualified NVIDIA-specific optimization.

Use the exact model, revision, tokenizer, quantization, hardware, request and
output length distributions, concurrency, prefix reuse, tool/structured-output
behavior, quality gate, and software versions. Compare the full latency-
throughput frontier and cost per accepted outcome. No engine is universally
best.

## Retrieval And Data

Start with authoritative sources and source-native access. For application-
centric retrieval, prefer:

1. PostgreSQL authoritative records.
2. PostgreSQL full-text search.
3. pgvector HNSW after semantic retrieval passes a labeled relevance gate.
4. Qdrant when vector scale, filter behavior, or dedicated ownership requires
   a vector system.
5. OpenSearch when search-centric ranking, analytics, or hybrid text operations
   dominate.
6. Neo4j when relationship and path traversal is a first-class requirement.

Embedding changes are migrations. Bind model, revision, dimensions,
normalization, distance metric, chunking, metadata, filters, tenant policy, and
index configuration. Define re-embedding, dual-read, rollback, deletion, and
mixed-version behavior.

Authorize the caller and tenant before retrieval or context assembly. A vector
filter is not a substitute for trusted document authorization.

## Evaluation And Operations

| Capability | Baseline | Starting status | Switch condition |
| --- | --- | --- | --- |
| Cross-service telemetry | OpenTelemetry | Conditional | More than one runtime or service must share trace identity. |
| Experiment, artifact, trace, and evaluation lineage | MLflow or existing organizational platform | Conditional | Training, evaluation, or deployment promotion requires durable lineage. |
| Deployment identity | Immutable application, model, prompt, tool, data, index, runtime, and policy tuple | Conditional | Resolve to Required before any AI behavior is promoted or released. |

Release gates cover model quality, retrieval, tools, workflow, end-to-end task
success, safety, authorization, latency percentiles, throughput, saturation,
recovery, rollback, cost, and human review where required. Promotion restores
the complete compatible identity, not only a model name.

## Revalidation Triggers

Reopen affected decisions when any of these changes:

- workload, modality, data class, license, region, retention, or tenant policy;
- provider API, model alias, SDK major, tool schema, stream events, safety
  policy, rate limit, or data-handling terms;
- MCP core, extension, SDK, framework, host, gateway, or authorization profile;
- model revision, tokenizer, chat template, adapter, quantization, hardware,
  driver, kernel, or inference engine;
- request shape, concurrency, prefix reuse, quality threshold, latency target,
  availability, recovery objective, or cost;
- team ownership, platform standard, lifecycle status, or support commitment.

Use `official-source-map.md` to refresh documented claims and rerun target
measurement for claims marked `Measured`.
