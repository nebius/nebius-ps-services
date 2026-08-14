# Current AI Technology Baseline

Use this dated profile as a shortlist for a Python-first production AI system.
It is not a universal stack and does not replace workload contracts, official
source verification, compatibility tests, target evaluation, or owner review.

Baseline date: 2026-08-14

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

| Capability | Baseline | Starting status | Switch condition |
| --- | --- | --- | --- |
| One intentionally selected provider | Narrow application-local seam around the native SDK | Conditional | An online or batch model workload selects one provider; multiple providers or platform policy creates semantic portability needs. |
| Multiple providers or self-hosting | Internal capability-aware `ModelProvider` contract | Conditional | The application must route, fall back, self-host, satisfy regional policy, or share a model platform. |
| OpenAI direct text or reasoning generation | Official OpenAI SDK plus Responses API | Conditional | OpenAI is approved and Responses supports the requested generation workload. |
| OpenAI direct task-specific workload | Official OpenAI SDK plus the documented embeddings, audio, realtime, image, moderation, or other task API | Conditional | The workload is direct but not served by the selected Responses capability. |
| Anthropic | Native adapter using the Messages API semantics | Conditional | Anthropic is an approved target. |
| Nebius Token Factory | Verified OpenAI-compatible adapter and exact capability profile | Conditional | The selected model, endpoint, and adapter pass the same conformance suite. |
| Self-hosted runtime | Verified adapter for the selected inference endpoint | Conditional | Open weights, data boundary, custom kernels, capacity, or economics justify ownership. |
| Shared traffic and policy plane | `agentgateway` | Conditional | Several applications, providers, MCP servers, or agent services would otherwise duplicate routing and policy controls. |

Changing only API key, base URL, and model name proves connectivity, not
semantic compatibility. Record modalities, tool behavior, JSON Schema subset,
streaming events, limits, reasoning controls, caching, safety metadata,
residency, retention, errors, and rate limits per exact target.

Automatic fallback is a separate evaluated deployment route. It must preserve
required semantics and pass quality, safety, latency, tool, structured-output,
and data-policy gates.

Read `model-provider-contract.md` only when the full portability condition is
met.

## Agent Applications And Durability

Do not select an agent component until the workload rejects a direct model call
and a deterministic workflow. A known sequence remains application code even
when several steps call a model or tool.

| Capability | Baseline | Starting status | Switch condition |
| --- | --- | --- | --- |
| OpenAI-native agent kernel | OpenAI Agents SDK | Conditional | The model must choose actions or observation-driven next steps and the system is intentionally OpenAI-first. |
| Non-OpenAI or provider-neutral Python agent kernel | Pydantic AI | Conditional | A bounded typed agent using Anthropic, Gemini, another non-OpenAI provider, or a provider-neutral boundary is required. |
| Coding-focused engineering specialist | Codex SDK | Conditional | Engineering work needs coding-focused threads, repository operations, or programmatic Codex automation. |
| Deep Codex product client | Codex app-server | Conditional | Product integration needs Codex authentication, conversation history, approvals, or streamed events; ordinary CI or jobs remain on the SDK path. |
| Integration components | Selected LangChain packages | Conditional | Their maintained integrations remove more code than they add. |
| Conventional model-tool loop | Selected OpenAI Agents SDK or Pydantic AI kernel | Conditional | The workload is genuinely agentic; switch to LangChain `create_agent` only when required maintained integrations measurably reduce total complexity. |
| Explicit state graph | LangGraph | Conditional | Nodes, edges, typed state, branches, bounded cycles, checkpoints, interrupts, replay, or graph recovery must be first-class. |
| Durable cross-service workflow | Temporal around the selected deterministic, agentic, or mixed control flow | Conditional | Timers, long approvals, external side effects, compensation, or process/infrastructure recovery are required. |
| Claude-native coding or sandbox profile | Claude Agent SDK | Conditional | Claude-native filesystem, shell, sandbox, hooks, subagents, MCP, Skills, permissions, or resumable sessions define the workload. |

Choose one primary agent kernel for each genuinely agentic capability.
LangChain and LangGraph are not competing whole-stack choices: current
`create_agent` uses the LangGraph runtime, while direct LangGraph use is
justified only when the application owns explicit graph semantics. Codex is a
separate engineering specialist rather than the default kernel for general AI
tasks.

LangGraph may checkpoint deterministic or agent execution state. Temporal owns
durable business-process state and side effects around direct calls,
deterministic workflows, agents, or mixtures. Assign each retry, timeout,
idempotency, and recovery responsibility to one layer; graph or durability
requirements do not independently justify an agent.

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
