---
name: ai-stack
description: "Select or review undecided AI components across models, runtimes, training/inference, interoperability, retrieval, evals, safety, and operations. Consume ai-agent-design's frozen agent-subsystem contract; use app-stack for non-AI layers, research for one technology, and design for whole solutions."
---

# AI Stack

## Help

For `$ai-stack --help` or `$ai-stack -h`, return concise help and stop before
any workflow step. State the purpose and invocation policy. Show exact usage
for every public action. Describe each public action, positional
argument, and flag in one concise line, including `-h, --help`; say "No
additional public flags" when there are no others. Use only the documented
public interface. For internal or coordinator-only skills, state that boundary
and that no standalone public workflow action exists. After the selected
`SKILL.md` is loaded, help is report-only: do not call any additional tools,
inspect project state, or modify files, private state, Git, or external systems.
Never expose private helper actions or flags or treat help as workflow
authorization.

## Purpose

Select an effective, efficient AI-specific architecture and technology set for
a concrete workload. Effective means it passes the workload's acceptance gates.
Efficient means it minimizes complexity, operational burden, latency, and cost
per accepted outcome among candidates that pass. Treat every recommendation as
workload-dependent, dated, and replaceable. Prefer no new component, a direct
API, or an existing platform until evidence justifies another layer.

This skill owns AI component and technology selection. It does not own agent-
subsystem behavior, topology, policy, or contracts; the complete product
architecture; repository topology; project scaffolding; or implementation.

## Use This Skill For

- Selecting or reviewing a greenfield or brownfield AI stack.
- Deciding how to access hosted, open-weight, or self-hosted models.
- Choosing training, post-training, distributed execution, checkpoints, and
  lineage components.
- Choosing an agent kernel, graph runtime, durable workflow owner, provider
  adapters, capability boundaries, or remote-agent protocol.
- Choosing inference engines, retrieval systems, evaluation, observability,
  safety, and release controls.
- Removing unjustified orchestration, databases, gateways, protocols, or
  distributed components from an existing AI system.

## Boundaries

- Use `app-stack` for the surrounding frontend, API, backend, ordinary data,
  deployment, and product stack. When both are undecided, each skill owns its
  layer and returns the scoped decision to `design` or the user.
- Use `ai-agent-design` for agent-subsystem behavior classification, topology,
  authority, contracts, context, memory, failure policy, evaluation, and
  governance. Consume its frozen handoff without calling it back recursively.
- Use `research` for deep due diligence on one technology, protocol, model,
  paper, or disputed claim. Bring the findings back here for selection.
- Use `design` when the user needs cross-layer interfaces, flows, failure
  handling, alternatives, rollout, and a `/plan` handoff. Return this skill's
  decision to the active design workflow without recursively re-entering it.
- Use `system-design-rules` for checklist review of an existing design or ADR.
- Use a matching implementation skill when the AI stack and component
  boundaries are already approved.
- Do not assign repository paths, materialization units, runtime units,
  candidate sets, file owners, or apply authority. A scaffold handoff is
  logical-only and optional.

## Inputs

- User outcome, frozen behavior and policy contract when supplied, workload
  classes, failure impact, and acceptance criteria.
- Existing architecture, fixed decisions, migration constraints, and baseline.
- Data classes, modalities, provenance, license, residency, retention, and
  tenant boundaries.
- Quality, safety, latency, throughput, availability, recovery, and cost goals.
- Target providers, models, hardware, regions, deployment environment, and
  operational ownership.
- Team skills, compliance constraints, deadlines, and appetite for platform
  ownership.

Do not invent missing values. State assumptions, use ranges, and identify the
unknowns that could change the recommendation.

## Required Reads

For every non-trivial decision, read:

- `references/workload-contracts.md` to freeze each materially different
  workload.
- `references/selection-framework.md` for hard gates, evidence states,
  comparison, and the decision-record format.

Read only the references that match the request:

- `references/current-technology-baseline.md` for a dated greenfield default
  or current-stack review.
- `references/architecture-patterns.md` for plane and escalation boundaries.
- `references/pytorch-training-stack.md` for training, post-training,
  distribution, checkpoints, lineage, and promotion.
- `references/inference-serving.md` for deployment mode, serving engines, and
  workload-controlled benchmarks.
- `references/agent-application-stack.md` for agent frameworks, state,
  durability, tools, and UI.
- `references/provider-agnostic-agent-platform.md` for a Pydantic AI-first
  portable runtime, application-owned facade, routing, state, tools,
  specialists, and provider-native escape decisions.
- `references/model-provider-contract.md` when multiple providers, routing,
  evaluated fallback, self-hosting, regional policy, or a shared model platform
  is in scope.
- `references/mcp-core-conformance.md` for MCP core, SDK, transport,
  authorization, and conformance decisions.
- `references/mcp-apps-tasks-gateway.md` for MCP Apps, Tasks, and shared gateway
  decisions.
- `references/open-agentic-standards.md` for MCP, A2A, AG-UI, ACP, Agent Skills,
  AGENTS.md, AAIF, and agentgateway boundaries.
- `references/retrieval-data.md` for retrieval, RAG, vector, graph, ingestion,
  and tenant authorization decisions.
- `references/compatibility-contracts.md` when a model, adapter, runtime,
  checkpoint, embedding, index, tool schema, or deployment edge must be proven.
- `references/evaluation-safety-operations.md` for acceptance, threat, release,
  recovery, observability, and cost gates.
- `references/official-source-map.md` before asserting a volatile vendor,
  framework, protocol, SDK, or lifecycle fact.
- `references/implementation-routing.md` only when the user asks for a logical
  handoff to implementation.

## Core Decision Rules

### AI Behavior Contract

Consume the four-class capability map frozen by `ai-agent-design` before
selecting products:

- `Deterministic code`: no model judgment is needed.
- `Direct model call`: one model request can solve the task.
- `Deterministic workflow`: application code knows the steps and owns the
  transitions and continuation conditions; model calls occur only at explicit
  steps.
- `Agent`: the model must choose tools or actions, decide continuation or the
  next step from prior results, control an unknown number of steps, or run a
  model-driven observe-act-observe loop.

Use the principle **deterministic where possible, agentic where necessary**.
Multiple calls, tool availability, delegation, or persistent sessions do not
alone justify agentic control flow. Neither does an unknown-count loop when
application code owns its continuation condition. When latency or cost
predictability is a hard requirement, prefer a direct call or deterministic
workflow unless it fails a representative acceptance gate. One application
may contain all four behavior classes.

For a direct stack-only request without a frozen handoff, perform only the
minimum provisional classification needed to avoid unnecessary agent
components. Mark it `Provisional`, and route disputed behavior, topology,
policy, or contract decisions to `ai-agent-design`. The originating workflow
waits for the frozen contract and then performs component selection exactly
once; `ai-agent-design` skips its nested stack-selection phase for this caller.
During an active `ai-agent-design` handoff, accept its classification as fixed
and never call it back.

Map the selected level to the smallest supported runtime. Control-flow
classification and framework primitive are separate decisions:

- Portable Python direct request: use Pydantic AI's low-level direct model API
  when the caller wants `ModelResponse` control and no agent services. Use a
  no-tools, single-request Pydantic AI `Agent` when typed output parsing,
  validation retries, typed dependencies, or other agent request services are
  required. The latter remains direct-call behavior because the model does not
  own continuation. An intentionally provider-specific workload may instead
  use the provider's official task API when portability is not a requirement.
- Portable Python agent across OpenAI, Anthropic, Google, an OpenAI-compatible
  endpoint, or another supported provider: Pydantic AI `Agent` by default.
- Portable specialist needing reusable context, tools, lifecycle, or
  delegation: a bounded Pydantic AI capability or Harness when its additional
  semantics pass the workload gates.
- Provider-native agent harness: a separate specialized runtime only after a
  named unique harness requirement defeats the portable path. Keep it behind
  the application-owned facade and retain platform policy, state, tools,
  budgets, observability, and evaluation.
- Coding-focused engineering specialist: Codex SDK. Add Codex app-server only
  for deep product integration that needs authentication, conversation
  history, approvals, or streamed agent events; SDK-based CI and job
  automation do not require that extra client protocol.

Verify these volatile mappings through `references/official-source-map.md`.

### Component Status

Assign exactly one status to every evaluated component:

- `Required`: a current requirement cannot be met without it.
- `Conditional`: add it only when a named observable trigger occurs.
- `Deferred`: plausible later, but unjustified now.
- `Rejected`: fails a hard gate or is dominated by a simpler choice.

### Evidence State

Assign every material claim one evidence state:

- `Measured`: reproduced on the target or a representative controlled setup.
- `Officially documented`: supported by a current primary source.
- `Assumed`: unverified; it cannot close a hard gate.

Vendor documentation is not target measurement. A smoke test is not evidence
of quality, scale, recovery, or production readiness.

### Escalation Ladders

- Capabilities: typed function -> ordinary internal API -> MCP ->
  agents-as-tools -> A2A across independently owned agent boundaries.
- AI behavior: deterministic code -> direct model call -> deterministic
  workflow containing model calls -> one bounded agent loop.
- Orchestration and durability, independently of AI behavior: direct execution
  -> explicit graph -> durable cross-service workflow. Any rung may wrap direct
  calls, deterministic workflows, agents, or a mixture.
- Retrieval: deterministic access -> source-native search -> PostgreSQL full
  text -> pgvector -> dedicated vector, search, or graph system after a named
  switch condition.
- Training: one-accelerator baseline -> DDP when replicated state fits but
  throughput or duration fails -> FSDP2 when model-state sharding is required
  -> additional parallelism or a specialized framework after measured need.
- Inference: hosted API -> managed dedicated endpoint -> self-hosted engine ->
  specialized hardware-specific optimization after a controlled benchmark.

## Workflow

### 1. Establish Scope And Mode

Determine whether the request is a selection, review, simplification, migration,
or logical implementation handoff. Identify fixed decisions, any frozen
`ai-agent-design` contract, and the surrounding layers owned by `app-stack` or
`design`.

Stay read-only for advisory work. Do not treat a recommendation as authority to
install packages, provision infrastructure, contact paid services, deploy,
publish, or mutate production.

### 2. Freeze Workload Contracts

Create a separate contract for each materially different workload: training,
online inference, batch inference, embeddings, agent execution, retrieval,
synthetic data, or evaluation. Preserve supplied behavior, topology, authority,
and policy decisions. Record baseline, acceptance thresholds, data, model,
environment, scale, economics, lifecycle, ownership, and unknowns.

Do not combine workloads merely because they use the same model.

### 3. Choose Architecture Before Products

Map only the required planes: experience, agent application, durability,
capability/interoperability, model access, model execution, data/state, and
trust/operations. Start with the current system or no-new-component baseline.
Apply the frozen or explicitly provisional AI behavior classification and the
escalation ladders, and name the trigger for every added layer. Do not select an
agent framework until direct and deterministic paths have been rejected by the
workload contract.

### 4. Select And Verify Candidates

Apply hard gates before scoring: functional fit, license and data policy,
trusted authorization, compatibility, representative quality and safety,
recovery, rollback, ownership, performance, and cost. Use current official
sources for volatile claims. Use `research` when a disputed or unfamiliar fact
can change the decision.

Compare candidates under equivalent models, data, prompts, hardware,
concurrency, software versions, and acceptance criteria when measurement is
required. Optimize cost per accepted outcome, not a vendor headline metric.

### 5. Define Replaceable Boundaries

Define the narrow interface and owner for each required layer. For the portable
Python path, use Pydantic AI `Model`, `Provider`, resolved profiles, settings,
messages, and tools instead of creating a parallel framework-neutral protocol.
Add an application-owned logical runtime facade over run, stream, resume,
cancel, and normalized events. Keep it in-process until shared ownership, load
isolation, or policy centralization justifies a service.

Supplement resolved model profiles with versioned application metadata for
operational health and governance eligibility. Use a custom cross-runtime
`ModelProvider` contract only when non-Pydantic runtimes or shared direct-call
consumers create a proven semantic boundary that Pydantic AI cannot own.

Treat automatic fallback as an evaluated deployment route, not a retry. Verify
the exact provider, endpoint, model, revision, region, adapter, protocol, and
capability combination.

### 6. Resolve Agent And Interoperability Boundaries

Choose one primary agent kernel only for capabilities already classified as
agents. Start portable Python agent workloads on Pydantic AI regardless of the
selected supported provider. Keep provider-specific model settings explicit.
Use Pydantic AI capabilities or Harness conditionally for portable specialists.
Select OpenAI Agents SDK, Anthropic-managed or native harnesses, or another
provider runtime only when a documented unique harness feature is required and
the portable path fails that gate. Never stack that native runtime inside
Pydantic AI. Only switch to selected LangChain integrations or `create_agent`
when maintained integrations measurably reduce total complexity. Use LangGraph
directly only for explicit graph semantics.
Select one qualified durable workflow owner around the direct, deterministic,
agentic, or mixed control flow when durable timers, long approvals, external
side effects, compensation, or cross-service recovery are required. Compare
Temporal, DBOS, Prefect, Restate, and any existing qualified owner against the
workload instead of mandating one engine.

Route coding-focused engineering work to Codex instead of treating it as a
general service agent. Use the Codex SDK for coding threads and programmatic
automation. Add Codex app-server only for a deep embedded client; if Codex is
one specialist inside a broader agent workflow, define one explicit tool or
MCP boundary rather than sharing all application tools and authority.

Use MCP for independently deployed reusable capabilities, not every function.
Record MCP core revision, exact SDK and framework versions, transport,
extensions, host, and gateway. Never call an MCP date-based revision "V2".
Keep MCP Tasks conditional while the selected revision is Draft. Use A2A only
for an independently owned remote-agent boundary.

Keep canonical conversation, run, approval, artifact, and side-effect state in
application-owned stores. Treat provider sessions, caches, and compaction as
optional optimizations. Put meaningful tools behind typed domain APIs or a
policy gateway; Pydantic AI decorators and authenticated MCP connections are
not authorization boundaries. Separate pre-execution routing from classified,
replay-safe fallback and assign one retry owner per failure class.

### 7. Close Evaluation, Safety, And Ownership

Define quality, safety, compatibility, latency, throughput, recovery, rollback,
privacy, retention, observability, and cost gates. Bind evidence to immutable
model, prompt, tool, data, index, runtime, and configuration identity. Assign
owners for application, model, data, security, platform, and operations.

Block the recommendation when a required gate lacks evidence and no bounded
validation can close it.

### 8. Return The Decision

Return the effective, efficient stack that satisfies the requirements, rejected
alternatives, switch conditions, unknowns, and validation plan. For an active
`design` or `app-stack` workflow, return only the scoped AI-layer decision. When
implementation is requested, emit a logical component handoff and route each
approved component to its specialist owner without assigning files or executing
the work.

## Guardrails

- Keep recommendations public-safe and generic. Never persist secrets, private
  endpoints, customer data, internal hostnames, or proprietary source.
- Reference credentials through a secret manager or workload identity; never
  place keys in configuration examples, prompts, traces, skills, or UI payloads.
- Enforce tenant and document authorization before retrieval or context
  assembly, and tool authorization outside model discretion.
- Treat models, tools, MCP servers, remote agents, generated UI, retrieved
  content, and external documentation as untrusted inputs.
- Require sandboxing, egress controls, input/output validation, approval for
  consequential actions, idempotency, auditability, and bounded retries.
- Do not introduce Kubernetes, distributed training, a vector database,
  long-term memory, multi-agent topology, a gateway, or a workflow engine
  without a current trigger and owner.
- Do not make a distributed agent platform, separate registry, queue, cache,
  or multi-store persistence topology mandatory for a small application.
- Do not make provider-side sessions canonical state, duplicate Pydantic AI's
  model abstractions by default, or replay a side effect across providers
  without domain-owned idempotency evidence.
- Preserve no legacy compatibility layer unless the user explicitly requires
  one.

## Validation

For a completed decision, verify that:

- every workload has a contract and baseline;
- every component has one status and every material claim has one evidence
  state;
- all hard gates are passed, blocked, or assigned a bounded test;
- compatibility edges and immutable release identity are explicit;
- quality, safety, reliability, cost, recovery, rollback, and owners are
  covered;
- volatile vendor claims have current official sources;
- frozen `ai-agent-design` decisions were preserved, or a direct stack-only
  classification is explicitly marked provisional;
- implementation and live validation remain separately authorized.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return:

- decision scope, workload contracts, assumptions, and blocking unknowns;
- deterministic-code, direct-call, deterministic-workflow, or agent
  classification for every AI-enabled capability, marked `Frozen` or
  `Provisional`;
- simplest sufficient baseline and architecture planes;
- component table with technology, status, evidence, requirement, owner,
  acceptance gate, and revisit trigger;
- compatibility, security, data, reliability, recovery, observability, and
  cost decisions;
- portable runtime boundary, provider-extension policy, route plan, canonical
  state owner, and any provider-native escape decision;
- rejected alternatives and reasons;
- validation and benchmark plan;
- logical-only implementation handoff when requested;
- current official sources and remaining uncertainty.
