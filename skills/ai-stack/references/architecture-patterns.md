# AI Architecture Patterns

Select architecture and interfaces before products. Use the lowest sufficient
rung that satisfies the workload contract and acceptance gates.

## Contents

- Eight-plane model
- AI behavior decision
- Customization ladder
- Agent complexity ladder
- Interoperability ladder
- Inference deployment ladder
- Retrieval ladder
- Training orchestration ladder
- State taxonomy
- Interface contracts

## Eight-Plane Model

### Experience And Client Plane

Usually owned by `app-stack`:

- Product UI, public API, authentication, user workflow, accessibility,
  deadlines, cancellation, approvals, and failure behavior.
- Use normal application protocols first. Add AG-UI for a reusable
  agent-to-frontend event contract, MCP Apps for inline tool-owned UI, or ACP
  for coding-agent editor integration only when justified.

### Agent Application And Control Plane

Owned by `ai-stack` when present:

- Prompt and policy assembly, typed agent kernel, model request construction,
  tool selection, structured output, run budgets, and agent-local context.
- Current Python defaults: OpenAI Agents SDK for intentionally OpenAI-native
  agents; Pydantic AI for bounded agents using non-OpenAI providers or a typed
  provider-neutral boundary.
- May be absent for direct inference or deterministic application logic.

### Workflow And Durability Plane

Owned jointly by AI and application workflow owners:

- Explicit state graphs, checkpoints, interrupts, timers, retries, recovery,
  long waits, compensation, and human interaction.
- Use LangGraph for explicit in-application graph semantics.
- Use Temporal for cross-service durable workflows and external side effects.
- Keep one owner for each retry, checkpoint, and idempotency boundary.

### Capability And Interoperability Plane

Owned by capability providers and `ai-stack` integration owners:

- Direct functions, internal APIs, MCP tools and resources, Agent Skills,
  AGENTS.md, MCP Apps, MCP Tasks, A2A agents, and approved code or browser tools.
- Prefer direct typed functions for private capabilities, MCP for reusable tool
  boundaries, and A2A only for independently deployed agents.
- Keep identity, authorization, approval, schema, cancellation, and trace
  behavior explicit at every boundary.

### Model Access And Gateway Plane

Owned by `ai-stack` and platform owners:

- Internal provider contract, native provider adapters, exact model capability
  profiles, credential injection, routing, fallback, rate limits, budgets, and
  provider telemetry.
- Use agentgateway when a shared platform needs common policy across LLM, MCP,
  A2A, HTTP, and gRPC traffic. Keep it Conditional for a small single-provider
  application.

### Model Execution Plane

Owned by `ai-stack`:

- Hosted model API or self-hosted execution engine, model server, scheduler,
  batching, quantization, decoding, adapters, routing, and accelerator use.
- Keep provider API, gateway, serving control plane, execution runtime, and
  hardware as separate decisions.

### Knowledge, Data, And State Plane

Shared with data owners, scoped here for AI use:

- Authoritative sources, ingestion, transformations, embeddings, indexes,
  retrieval, reranking, graph traversal, conversation state, workflow state,
  long-term memory, synthetic data, artifacts, and provenance.
- Keep authoritative records, workflow checkpoints, semantic memory, external
  knowledge, audit history, and model caches as distinct state classes.

### Trust, Lifecycle, And Operations Plane

Shared with platform, security, and operations:

- Authentication, authorization, tenant isolation, data classification,
  approvals, sandboxing, egress, secrets, supply chain, and audit.
- Training orchestration, checkpoints, registry, evaluation, promotion,
  telemetry, capacity, incident diagnosis, rollout, rollback, and governance.
- Use OpenTelemetry for cross-system correlation and preserve one evaluation
  identity across model, agent, MCP, data, and deployment components.

Define data, control, trust, identity, protocol, state, and telemetry interfaces
between planes. Avoid a product that silently owns several planes unless that
integrated ownership is intentional, evaluated, and reversible.

## AI Behavior Decision

Apply this per capability before choosing an SDK or framework:

```text
Can one model request solve the task?
  yes -> direct model call
  no  -> Does application code know the exact steps?
           yes -> deterministic workflow containing model calls where needed
           no  -> Must the model choose actions or next steps from results?
                    yes -> agent
                    no  -> deterministic workflow
```

Choose an agent only when the model must select tools or actions, determine
continuation or the next step from observations, control an unknown number of
steps, or run a model-driven observe-act-observe loop. A deterministic loop may
run an unknown number of times when code owns a condition such as cursor
exhaustion. Delegation and persistent sessions can justify agent-runtime
features after this classification, but they do not make known application
control flow agentic. Prefer direct calls and workflows when latency or cost
predictability is critical.

It is normal for one application to combine:

```text
deterministic application logic and workflows
  + direct model calls at explicit steps
  + bounded agents for genuinely dynamic tasks
  + Codex for coding-focused engineering tasks
```

Keep APIs, authoritative data, RBAC, approvals, tool authorization, and known
business transitions outside model discretion.

Classify graph and durability semantics on a separate axis. Explicit branches,
checkpoints, interrupts, timers, long waits, retries, and compensation may wrap
a direct call, deterministic workflow, agent, or mixture; none independently
requires model autonomy.

## Customization Ladder

Escalate only when a lower rung fails a representative quality gate:

1. Existing model and direct prompting.
2. Prompt, structured output, tool, and workflow improvements.
3. Retrieval or source-grounding improvements.
4. Parameter-efficient tuning or adapters.
5. Full fine-tuning or preference optimization.
6. Continued pretraining.
7. Pretraining from scratch.

Diagnose the failure class first. Retrieval does not repair missing task
behavior, and fine-tuning does not provide current private knowledge without a
data path.

## Control-Flow And Execution Ladders

Classify model autonomy first:

1. Normal function or deterministic application code.
2. Single model call with validated structured output.
3. Explicit deterministic workflow containing model or tool steps.
4. Bounded typed single-agent tool loop.
5. Open-ended coding or research harness with planning, files, sandbox, and
   context compaction.
6. Independently deployed remote agent with an A2A or ordinary service boundary.
7. Multi-agent coordination with distinct context, authority, or parallel work.

Classify execution semantics independently:

1. Direct execution.
2. Explicit graph with checkpoints, pause, resume, replay, or human approval.
3. Durable cross-service workflow for timers, external side effects, and
   compensation.

Any execution rung may host a direct call, deterministic workflow, agent, or
mixture. Do not traverse the agent ladder merely to obtain graph or durability
semantics.

Use multi-agent only when decomposition, independent context, specialization,
parallelism, or authority separation produces measured benefit that exceeds
coordination cost and new failure modes.

Prefer explicit workflow edges for known business processes, even when several
steps call a model. Reserve model-led planning and routing for decisions that
cannot be encoded reliably and cheaply. Use agents-as-tools before peer-to-peer
agent conversations.

## Interoperability Ladder

Choose the narrowest boundary that solves the problem:

1. Direct typed function: Private and in-process.
2. Ordinary internal API: Service boundary with known clients.
3. Agent Skill: Reusable procedure, instructions, scripts, references, or assets.
4. AGENTS.md: Repository-local coding-agent instructions.
5. MCP: Reusable discoverable tools, resources, prompts, Apps, or Tasks.
6. AG-UI: Reusable agent-runtime to frontend event protocol.
7. ACP: Coding-agent to editor or terminal client protocol.
8. A2A: Independently deployed agent-to-agent task boundary.
9. agentgateway: Shared routing, identity, policy, quota, and telemetry plane.

These are not mutually exclusive, but every added boundary must have a named
owner, version, trust model, conformance suite, and exit path. Read
`open-agentic-standards.md`, `model-provider-contract.md`,
`mcp-core-conformance.md`, and `mcp-apps-tasks-gateway.md` for detailed
contracts.

## Inference Deployment Ladder

Treat deployment model separately from execution technology:

1. Hosted or serverless model API: Provider owns capacity and execution.
2. Managed dedicated endpoint: Provider manages the service around dedicated or
   selected capacity.
3. Self-hosted service: Team owns runtime, server, routing, capacity, upgrades,
   and recovery.
4. Embedded or edge execution: Application or device owns local execution and
   distribution constraints.

Within a self-hosted or dedicated path, select distinct layers:

- execution engine;
- model server and API;
- serving or control plane;
- gateway and policy boundary;
- replica or model router;
- cache and distributed state;
- accelerator and infrastructure platform.

Do not call the entire set a runtime or managed inference.

## Retrieval Ladder

1. Direct authoritative-source access or deterministic API call.
2. Structured query, SQL, source-native search, or full-text search.
3. Hybrid application retrieval across existing sources.
4. Vector support in an existing authoritative database.
5. Dedicated vector search system.
6. Graph-native retrieval or graph plus vector retrieval.
7. Agentic retrieval across tools and heterogeneous sources.

Select a graph system when relationship-constrained traversal, multi-hop
reasoning, path provenance, or graph-native mutation and query semantics are
first-class requirements. Do not add it merely because entities are related.

Select agentic retrieval only when query planning across sources measurably
improves difficult queries and its latency, cost, reproducibility, and safety are
acceptable.

## Training Orchestration Ladder

1. Single process and single accelerator.
2. Framework-native multi-accelerator execution.
3. Framework-native multi-node execution.
4. Cluster job scheduler with existing training launcher.
5. Training-specific control plane for distributed jobs, recovery, metadata,
   and policy.
6. Multi-stage ML platform with data, training, evaluation, registry, and
   promotion workflows.

Parallelism and orchestration are different decisions. Choose data, tensor,
pipeline, expert, context, or sequence parallelism from model geometry and
hardware topology. Choose the control plane from scheduling, recovery,
multi-tenancy, policy, and operational requirements.

## State Taxonomy

Keep these stores separate unless one product intentionally implements several
with explicit contracts:

- Request context: Data for one model or tool call.
- Conversation or session history: Short-lived interaction continuity.
- Durable workflow checkpoint: Exact execution state for pause, resume, retry,
  and recovery.
- Long-term semantic or profile memory: Retrieved facts or preferences across
  sessions.
- External knowledge: Authoritative documents, records, APIs, and indexes used
  for grounding.
- Audit and episodic history: Immutable or governed record of actions and
  outcomes.
- Model KV or prefix cache: Reusable execution state for inference efficiency,
  not application memory.
- Artifact and experiment state: Models, adapters, datasets, checkpoints,
  metrics, and lineage.

For each store define owner, schema, key, consistency, freshness, retention,
deletion, encryption, tenant boundary, capacity, backup, restore, and migration.

## Interface Contracts

Document at least:

- Model-provider contract: Provider, endpoint, exact model and revision,
  capabilities, credential reference, request and response semantics, streaming,
  tools, structured output, errors, routing, fallback, and trace identity.
- Agent contract: Kernel, workflow, state schema, budgets, allowed providers,
  tools, approvals, recovery, and evaluation identity.
- Tool contract: Identity, authorization, scopes, schema, timeout, retries,
  idempotency, side effects, approval, audit, and error taxonomy.
- MCP contract: Core revision, SDK and framework versions, transport,
  extensions, discovery, caching, host support, auth, tenancy, gateway,
  cancellation, Tasks, Apps, and conformance results.
- Open-agent protocol contract: Skill or AGENTS.md resolution, AG-UI or ACP
  events, A2A capability and task semantics, identity, and lifecycle.
- Retrieval contract: Query, filters, tenant and document ACL, result schema,
  scores, provenance, freshness, top-k, reranking, and deletion semantics.
- Training contract: Code, environment, data snapshot, configuration, topology,
  checkpoint, resume, metrics, and artifact output.
- Serving contract: Protocol, streaming, deadlines, cancellation, batching,
  routing, overload, health, rollout, telemetry, and capacity.
- Evaluation contract: Exact model, prompt, tools, data, index, runtime,
  configuration, dataset, metrics, thresholds, and reproducibility metadata.
