# Agent Application Stack

Use this reference to design and select frameworks for production agentic
applications. Keep the agent kernel, workflow durability, model providers,
tools, open protocols, state, security, and evaluation independently replaceable.

Baseline date: 2026-08-16
Agent-framework preference and SDK integration reviewed: 2026-11-13.
Other product profiles retain their dated evidence and require revalidation.

## Contents

- Final recommended stack
- Architectural planes
- Framework decision model
- Framework profiles
- Application patterns
- Skills, MCP, and remote agents
- State and memory
- Tool and guardrail design
- Logical implementation handoff
- Test and release gates
- Official sources

## Final Recommended Stack

Use this as the greenfield default for a Python-first organization:

```text
AI control flow:
  Direct model call when one request is sufficient
  Deterministic application workflow when the operation sequence is known
  Agent only for model-selected actions or observation-driven next steps

Model and agent runtime:
  Official task API or an existing qualified adapter for direct calls
  Ordinary application code for deterministic model workflows
  OpenAI Agents SDK Agent / Runner for general agentic workloads
  Honor explicit overrides and fixed project stacks; block failed hard gates
  Codex SDK for a coding-focused specialist requirement
  Codex app-server only for deep embedded client integration

Explicit graph orchestration:
  LangGraph only when branches, checkpoints, interrupts, replay, or recovery
  require a first-class state graph

Cross-service durable execution:
  One qualified durable workflow owner selected from workload evidence
  Temporal, DBOS, Prefect, Restate, or an existing engine are candidates

Model access:
  Selected SDK provider seams and explicit model settings
  Exact provider/endpoint/model/protocol capability qualification
  Application operational and governance catalog only when needed
  Custom cross-runtime contract only for proven shared consumers
  Shared gateway only when platform routing and policy justify it

Tools and context:
  Direct typed functions first
  MCP for independently deployed or reusable capabilities
  Agent Skills for reusable procedures
  AGENTS.md for repository instructions

User interaction:
  Normal application APIs and UI first
  AG-UI for a reusable runtime-to-frontend event contract
  MCP Apps for inline UI owned by an MCP capability
  ACP for coding-agent editor integration

Agent interoperability:
  Agents-as-tools first
  A2A only for independently deployed remote agents

State:
  PostgreSQL for authoritative application, approval, and audit records
  Framework checkpointer for execution state
  Object storage for large immutable artifacts
  Vector retrieval only for selected semantic memory or knowledge

Security and operations:
  OIDC or workload identity, policy enforcement, approvals, and sandboxing
  OpenTelemetry when cross-runtime or cross-service correlation is required
  MLflow when evaluation, experiment, artifact, or promotion lineage lacks an
  existing organizational authority
```

Do not install every layer for every application. Direct calls use a task API
or an existing qualified adapter; deterministic workflows use ordinary code.
A bounded assistant can use OpenAI Agents SDK, typed functions and existing
storage without a graph, durable engine, MCP, gateway or new database.
Pydantic AI remains valid when explicitly selected. Read
[SDK integration](openai-agents-sdk-integration.md) for preference, override,
provider qualification and application ownership rules.

## Architectural Planes

### Experience And Client Plane

Own product UI, API, authentication, deadlines, cancellation, user approvals,
and accessible failure behavior. Use:

- normal HTTP or WebSocket application interfaces by default;
- AG-UI when several runtimes or frontends need one agent event contract;
- MCP Apps when an MCP tool needs an inline interactive view;
- ACP when a coding agent must integrate with an editor or terminal client.

Keep user identity authoritative here and propagate a signed or otherwise
verified principal context into downstream policy boundaries.

### Agent Kernel Plane

Own portable prompt assembly, typed dependencies, model calls, bounded tool
loops, structured output, and run context.

Current preferred general agent kernel: OpenAI Agents SDK unless explicitly
overridden or a fixed project choice already applies. This preference neither
requires OpenAI models nor authorizes migration. Direct requests need no
agent kernel. A failed required gate blocks the preferred choice and calls for
an explicit override, not silent substitution.

Keep the kernel small. Do not let it become the authoritative store, workflow
scheduler, secret manager, or provider gateway.

Place an application-owned logical runtime facade above portable and native
runtimes. It owns stable run, stream, resume, cancel, and normalized event
contracts, but it may stay in-process until a deployment trigger exists.

### Orchestration And Durability Plane

Own explicit transitions, checkpoints, interrupts, timers, retries, and
recovery.

- LangGraph: Own in-application graph state and model/tool workflow topology.
- Selected qualified workflow engine: Own durable cross-service workflows and
  non-idempotent side effects.
- Queue or job system: Own bounded asynchronous work that does not need a full
  workflow engine.

Do not duplicate the same retry or checkpoint responsibility in all three.

### Capability Plane

Own tools, data access, code execution, browsers, files, and external systems.

- Direct typed function: Default for private in-process capability.
- Internal API: Default across an ordinary service boundary.
- MCP: Use for reusable discoverable capability across agent hosts.
- A2A: Use only when the capability is an independently deployed agent rather
  than a tool or deterministic service.

### Model Access Plane

Own provider configuration, exact capability resolution, credentials, routing,
budgets, fallback, errors and telemetry. Start with the selected SDK's supported
provider seam and run-scoped configuration. Supplement capability evidence with
application-owned governance/health metadata. Introduce the optional contract in
`model-provider-contract.md` only for a proven cross-runtime shared consumer.

Use provider SDKs behind adapters. Do not expose provider SDK types in domain
workflows.

### State, Memory, And Knowledge Plane

Own authoritative records, workflow checkpoints, conversation state, semantic
memory, external knowledge, indexes, and artifacts. Keep each state class under
an explicit schema, retention, deletion, tenant, and migration policy.

### Trust And Policy Plane

Own authentication, authorization, tenant isolation, data classification,
allowed models, tool scopes, approval, sandboxing, egress, quotas, and audit.
Keep deterministic controls outside model prompts.

### Evaluation And Operations Plane

Own trace correlation, evaluation datasets, graders, release gates, capacity,
cost, rollout, rollback, incident response, and version identity.

## Framework Decision Model

Consume the model-autonomy classification from `ai-agent-design` first. For a
direct stack-only request, any local result is provisional:

```text
deterministic function
  -> one structured model call
  -> deterministic workflow containing model calls
  -> bounded agent loop
  -> agent harness for open-ended coding or research
  -> independently deployed remote agent
  -> multi-agent network
```

Then classify execution semantics independently:

```text
direct execution
  -> explicit graph
  -> durable cross-service workflow
```

Any execution rung may wrap a direct call, deterministic workflow, agent, or
mixture. Require a measured reason to move upward on either axis.

### Decision Table

| Requirement | Preferred choice | Add or switch when |
| --- | --- | --- |
| One bounded model request | Official provider task API or existing qualified direct-call adapter | Add an agent only if the model must own actions or continuation |
| One provider-specific embedding, transcription, realtime, or other specialized request | Official provider SDK plus the task-specific API | Portable direct access satisfies the exact feature, or the workload becomes a workflow or agent |
| Known sequence with model steps | Ordinary application workflow plus explicit provider calls | The model must choose actions or observation-driven next steps |
| General typed agent | OpenAI Agents SDK | Explicit override/fixed project choice; failed hard gates require a reported blocker |
| Bounded specialist | Selected kernel with narrow tools and budgets | A distinct harness requirement justifies an explicitly selected specialist |
| Explicit alternative kernel | Pydantic AI or another evaluated framework | User override or fixed project choice plus passing workload gates |
| Coding-focused engineering specialist | Codex SDK | A deep embedded client also requires Codex app-server; a broader orchestrator may instead expose Codex through one explicit MCP boundary |
| Simple high-level agent with broad integrations | Selected default kernel plus only the required integrations | An explicit override may select LangChain `create_agent` only when its maintained integrations measurably reduce total complexity |
| Explicit state graph, checkpoints, interrupts, time travel | LangGraph around the selected deterministic or agent control flow | Cross-service timers and irreversible side effects require a qualified durable workflow owner outside the graph |
| Long-running business process and durable side effects | Selected qualified workflow engine around direct calls, deterministic workflows, agents, or mixtures | Work is short-lived, read-only, and safely replayable without durable orchestration |
| Claude-native coding, file, sandbox, or Skill agent | Claude Agent SDK | A provider-neutral service agent is the actual requirement |
| Anthropic-hosted durable agent session | Claude Managed Agents | Provider neutrality, lifecycle control, or non-beta stability is required |
| Microsoft or .NET enterprise agent stack | Microsoft Agent Framework | Exact language, package, feature lifecycle, non-Microsoft ecosystem fit, or portability fails the gate |
| Named Google ADK semantic requirement under explicit framework override | Google ADK | Provider-neutral behavior or ordinary A2A integration remains sufficient in the default kernel |
| Named Strands or AWS-native agent semantic requirement under explicit framework override | Strands Agents | Bedrock model access alone remains sufficient in the default kernel |
| Retrieval-centric agent or index workflow | LlamaIndex components | Retrieval is not the dominant abstraction |
| Local model-agnostic developer agent | goose | A product service or custom application framework is required |
| Role-based agent team library | CrewAI, Conditional | Explicit control, durability, and evaluation pass against simpler agents-as-tools |
| Open-ended coding or research harness | Provider harness or Deep Agents-style runtime | Bounded service behavior is more appropriate |

Do not choose a framework from integration count alone. Evaluate control flow,
state, provider portability, tool schema behavior, durability, security,
observability, deployment, lifecycle, and exit cost.

## Framework Profiles

These are candidate profiles, not exceptions to the selection policy. Replacing
OpenAI Agents SDK as the general kernel requires an explicit override or fixed
project choice. Component-only integrations need ordinary requirement and
complexity gates; they do not require a framework override.

### Pydantic AI

Evaluate under an explicit framework override or existing fixed Pydantic AI
stack. Revalidate the following dated capabilities for the selected release:

- typed inputs, outputs, dependencies, tools, and validation;
- direct model requests without creating an agent;
- model and provider separation with resolved capability profiles;
- native and OpenAI-compatible provider support;
- a clean path to Nebius Token Factory through a verified OpenAI-compatible
  provider profile;
- MCP client integration;
- testability through model and tool substitution;
- capabilities, declarative Agent Specs, and optional Harness specialists;
- optional durable execution integrations;
- OpenTelemetry instrumentation and Pydantic Evals.

Use it for portable direct requests, bounded single-agent applications,
structured workflows controlled by application code, and agents invoked as
steps inside another workflow. Use its direct model API when a one-request task
needs low-level `ModelResponse` control. A no-tools single-request `Agent` is
also appropriate for direct-call behavior when typed parsing, validation
retries, or typed dependencies are required; the use of that primitive does
not make a known sequence agentic.

Keep provider-specific settings explicit and keep canonical state, routing
governance, tools, authorization, budgets, telemetry, and evaluation
application-owned. Use capabilities for reusable runtime behavior and Harness
only when a richer portable specialist environment is a requirement.

Do not treat Pydantic validation as authorization. Do not share one MCP client
or toolset across users when that object carries one identity or credential.
Construct per-user or per-run clients where identity differs.

### LangChain

Use selected LangChain components for model integrations, tools, loaders,
retrievers and middleware without replacing the kernel. Use `create_agent`
only under an explicit alternative-kernel choice, when its bounded tool loop
and generated graph behavior meet the workload gates.

Do not use broad convenience abstractions when direct typed functions are
clearer. Avoid framework-global state and provider types in the domain layer.

### LangGraph

Use LangGraph directly when the application requires:

- explicit nodes, edges, branches, and bounded cycles;
- typed graph state;
- checkpoints and durable thread state;
- pause, resume, and human approval;
- replay, state inspection, and time travel;
- controlled recovery after tool or provider failure.

Make side effects before an interrupt or replay boundary idempotent. A node can
restart from its beginning, so do not assume line-level continuation.

Do not use LangGraph for every agent. A graph adds state schemas, persistence,
migration, concurrency, replay, and operational ownership.

### Durable Workflow Owner

Select one qualified workflow engine around the direct, deterministic,
agentic, or mixed control flow when work spans services or must survive process
failure, long waits, human approvals, provider outages, timers, or irreversible
side effects. Compare Temporal, DBOS, Prefect, Restate, and any existing owner
against language support, replay semantics, operations, recovery, and cost.

Keep durable workflow code deterministic where the selected engine requires
it. Execute model calls, MCP calls, and external side effects in activities or
the engine's equivalent side-effect boundary. Persist version identities and
idempotency keys. Map MCP Task handles to workflow or activity identity when
Tasks are exposed.

Do not put token-by-token model streaming inside durable workflow history.
Store references or bounded summaries instead.

### OpenAI Agents SDK

Preferred for general agentic workloads unless explicitly overridden. Use the
standard runner and supported adapters; keep authority, canonical state,
durability, budgets and independent verification application-owned. Qualify
non-OpenAI routes rather than assuming API-shape equivalence.

Read [SDK integration](openai-agents-sdk-integration.md) for provider seams,
conversation strategy, approvals, recovery, guardrail coverage, tool concurrency,
trace export and upgrade qualification. Do not nest this SDK inside Pydantic AI
or make its use contingent on selecting an OpenAI model.

### Codex SDK And App Server

Use the Codex SDK for coding-focused threads, engineering agents embedded in
internal tools, CI, and programmatic job automation. Keep repository scope,
sandbox, approvals, skills, MCP access, credentials, and write authority
explicit.

Add Codex app-server when building a deep product client that needs the Codex
protocol for authentication, conversation history, approvals, and streamed
agent events. The Python Codex SDK already controls a local app-server; do not
add a second direct protocol client unless the product needs that lower-level
surface. For ordinary CI or background automation, use the SDK without making
app-server a separately owned application component.

### Claude Agent SDK

Use for Claude-native coding, filesystem, command, browser, hook, subagent,
MCP, Skill, and session workloads that need the Claude Code agent loop in an
application process.

Treat its permissions and allowed-tool configuration as part of the security
contract. Put sensitive code execution in a sandbox with bounded filesystem,
network, process, and credential access.

Use the Anthropic API adapter rather than Claude Agent SDK for a generic bounded
service agent that only needs model calls and typed tools.

### Claude Managed Agents

Use as a provider-specific hosted execution profile when Anthropic-managed
sessions, sandboxes, Skills, MCP, and multi-agent behavior meet the product and
data requirements.

Treat beta lifecycle, retention, compliance, region, session identity, and
credential-vault behavior as blocking review items. Keep a portable domain and
evaluation contract outside the hosted runtime.

### Microsoft Agent Framework

Use conditionally for Microsoft Foundry, Azure, .NET, or organizations migrating
from Semantic Kernel or AutoGen. Its current framework combines agents,
harnesses, and graph workflows with sessions, middleware, MCP, telemetry,
checkpointing, and human interaction.

At the baseline date, the .NET and Python 1.0 lines are generally available;
Go and selected features or extensions remain preview. Qualify the exact
language, package, feature, provider, and hosting surface instead of assigning
one lifecycle label to the whole framework. Do not start new greenfield work
directly on AutoGen or Semantic Kernel without reviewing Microsoft's current
migration direction.

### Google ADK

Use conditionally for Google-oriented agents, Gemini integrations, Vertex AI,
and A2A-first deployments. Verify provider portability, deployment model,
session state, tool semantics, evaluation, and non-Google integration before
selecting it as the organization-wide default.

### Strands Agents

Use conditionally for AWS and Bedrock-aligned teams or when its provider and
tool model fits. Verify portability beyond AWS, MCP behavior, workflow
durability, tracing, and framework maturity against the preferred kernel and any explicit alternatives.

### goose

Use as a local-first developer agent and MCP interoperability host. Use custom
distributions only with an owner, reviewed configuration, pinned models,
allowlisted servers, and sandbox controls.

### CrewAI And Other Team Frameworks

Treat role-based multi-agent frameworks as Conditional. Require a controlled
evaluation against:

```text
one typed agent with tools
  + agents-as-tools
  + explicit deterministic routing
```

Reject the team framework when it adds conversation and coordination cost
without measurable task success, isolation, parallelism, or authority benefits.

## Application Patterns

### Bounded Service Agent

First decide whether the task needs an agent. Prefer:

```text
FastAPI or existing application service
  -> official task API or qualified adapter for one-request tasks
  -> official provider SDK for intentionally native task APIs
  -> deterministic application workflow for known sequences
  -> OpenAI Agents SDK for typed dynamic loops unless explicitly overridden
  -> specialized runtime only for an explicitly selected harness requirement
  -> application-owned logical runtime facade and canonical state
  -> direct typed tools
  -> existing authoritative store only when durable records or audit are required
  -> OpenTelemetry when cross-service trace correlation is required
```

Use supported SDK provider seams for qualified model routes. Add a custom
cross-runtime model contract only when real shared consumers require it. Add MCP
only for independently reusable tools. Add LangGraph only when explicit graph
semantics appear, independently of whether any node is agentic.

### Durable Approval Workflow

Use:

```text
application API
  -> selected durable workflow
  -> deterministic validation and model-call activities where required
  -> authorization and approval service
  -> idempotent tool or MCP activity
  -> verification activity
  -> optional selected agent-kernel activity only for model-directed work
```

Keep business truth and approval records in the authoritative database. Fixed
branches, long waits, retries, and compensation do not require an agent.

### Explicit Research Or Operations Graph

Use:

```text
LangGraph
  nodes: plan, retrieve, analyze, request approval, execute, verify
  checkpointer: execution state only
  agent nodes: optional; prefer OpenAI Agents SDK for model-owned actions
    or continuation, honoring explicit overrides
  external side effects: durable activity or idempotent service call
```

Do not use an unconstrained supervisor when the transitions are known.

### Coding Agent

Use:

```text
application or engineering workflow
  + Codex SDK for coding-focused threads
  + Codex app-server only for a deep embedded client
  + AGENTS.md
  + reviewed Agent Skills
  + MCP for approved reusable developer tools
  + explicit MCP orchestration when Codex is one specialist in a broader agent
  + sandbox and repository-scoped credentials
```

Keep repository instructions and Skills versioned with source or an approved
catalog. Make write, command, network, and deployment actions approval-aware.
Use another provider-native coding harness only when the workload explicitly
requires that provider's semantics and it passes the same security, sandbox,
evaluation, and operations gates.

### Mixed AI Application

Use explicit ownership rather than one global agent:

```text
Application
  -> deterministic logic, APIs, PostgreSQL, RBAC, and approvals
  -> general AI tasks
       -> direct request or deterministic workflow where sufficient
       -> OpenAI Agents SDK for dynamic tasks unless explicitly overridden
       -> explicitly selected specialist for a distinct harness requirement
  -> engineering tasks
       -> Codex SDK
       -> Codex app-server only for deep client integration
```

### Remote Agent Service

Use A2A v1.0 only when the remote unit owns an independent task-level capability and
must hide its internal implementation. Put identity, policy, quotas, deadlines,
and telemetry at the boundary. Use MCP or a normal API when the remote unit is
really a tool or deterministic service.

### Multi-Agent System

Prefer these patterns in order:

1. Agents-as-tools: One coordinator invokes specialized agents through typed
   contracts.
2. Deterministic router: Application code selects the specialist.
3. Parallel independent agents: Run when results can be merged deterministically.
4. Remote A2A agents: Use across deployment or organizational boundaries.
5. Peer conversation or emergent team: Use only after it wins a measured gate.

Define authority and information boundaries per agent. Do not give every agent
the same tools, credentials, memory, and context.

## Skills, MCP, And Remote Agents

Use the layers together without collapsing them:

```text
Skill
  teaches the agent how to perform a reusable workflow

MCP server
  exposes reusable tools, resources, prompts, apps, or tasks

A2A agent
  owns an independent task-level capability
```

A Skill can instruct an agent to use an MCP server. An MCP server can expose a
tool that starts a durable Task. An A2A agent can internally use Skills and MCP.
Keep the protocols and authority boundaries explicit.

Do not adopt an unstandardized "Skills over MCP" transport as a baseline. Use a
curated Skill catalog and normal Skill packaging until an interoperable
specification is proven.

## State And Memory

Separate:

- Request context: One operation, not durable.
- Conversation history: User interaction continuity with token and retention
  policy.
- Agent working state: Bounded run-local plan, scratch data, and tool results.
- Graph checkpoint: Exact graph execution state.
- Durable workflow state: Business-process progress owned by the selected
  qualified engine.
- Long-term memory: Selected governed facts, preferences, or experience.
- External knowledge: Authoritative data retrieved under ACL.
- Audit history: Actions, approvals, versions, and outcomes.
- KV or prompt cache: Model execution optimization only.

Do not use a vector database as the default store for all memory. Use relational
or document schemas for authoritative facts and vector retrieval only where
semantic lookup is required.

## Tool And Guardrail Design

For every tool declare:

```text
name and version
purpose and owner
input and output JSON Schema
caller and tenant source
required scopes
read or write risk class
idempotency behavior
deadline and cancellation
approval requirement
dry-run and verification path
error taxonomy
trace and audit fields
```

Use read tools before write tools. Separate planning from mutation. Validate
semantic constraints after schema validation. Reject raw shell, SQL, arbitrary
URL, or filesystem path generation unless a constrained sandbox or policy
boundary mediates it.

Apply guardrails at multiple layers:

- Model: Instructions, structured output, provider safety, and bounded context.
- Agent: Step, token, cost, time, tool, and retry budgets.
- Tool: Authentication, authorization, validation, approval, and idempotency.
- Sandbox: Filesystem, process, network, CPU, memory, and credential boundaries.
- Data: Tenant and document ACL before retrieval.
- Output: Schema, sensitive data, provenance, and user-visible risk.
- Workflow: Allowed transitions and safe terminal states.

Do not use a model-based guardrail as the authorization layer.

## Logical Implementation Handoff

Describe only logical capabilities and boundaries:

- application-owned runtime facade and typed application dependencies;
- qualified SDK model/provider boundary and explicit override disposition;
- provider-specific settings and any justified native runtime escape;
- conditionally justified cross-runtime `ModelProvider` only when required;
- direct tools, ordinary APIs, and independently reusable MCP capabilities;
- graph state, durable workflow state, long-term memory, knowledge, and audit
  as separate stores where present;
- product UI, AG-UI, MCP Apps, or ACP responsibility;
- authorization, sandbox, evaluation, telemetry, and immutable release identity;
- component status, evidence, owner, acceptance gate, and revisit trigger.

Do not assign repository paths, source or runtime units, candidate manifests,
file owners, or apply authority. An explicitly invoked scaffold or project
workflow owns physical topology and must preserve the approved logical choices.

## Test And Release Gates

Require:

- Domain tests: Tool schemas, policies, state transitions, and error mappings.
- Provider conformance: Run `model-provider-contract.md` tests for every exact
  model target.
- Agent scenarios: Success, refusal, unsafe request, wrong tool, malformed tool
  arguments, provider outage, and budget exhaustion.
- Workflow recovery: Crash, retry, resume, duplicate delivery, cancellation,
  approval timeout, and partial side effect.
- MCP interoperability: Official SDK reference client and server plus selected
  production host and FastMCP client.
- Security: Prompt injection, indirect injection, confused deputy, SSRF, data
  exfiltration, cross-tenant access, sandbox escape assumptions, and credential
  leakage.
- Evaluation: Task success, tool correctness, groundedness, safety, latency,
  token use, cost per accepted outcome, and human acceptance.
- Release identity: Pin application, framework, provider adapter, model, prompt,
  tool schemas, Skills, MCP servers, policies, data, and evaluators.

Promote through offline evaluation, integration environment, shadow or replay,
small canary, and controlled rollout. Keep a rollback path that restores the
complete compatible identity, not only the model name.

## Official Sources

- Pydantic AI: <https://pydantic.dev/docs/ai/>
- Pydantic AI direct model requests: <https://pydantic.dev/docs/ai/core-concepts/direct/>
- Pydantic AI Harness: <https://pydantic.dev/docs/ai/harness/>
- Pydantic AI Anthropic models: <https://pydantic.dev/docs/ai/models/anthropic/>
- Pydantic AI Google models: <https://pydantic.dev/docs/ai/models/google/>
- LangChain agents: <https://docs.langchain.com/oss/python/langchain/agents>
- LangGraph: <https://docs.langchain.com/oss/python/langgraph/overview>
- Temporal: <https://docs.temporal.io/>
- OpenAI Agents SDK: <https://openai.github.io/openai-agents-python/>
- OpenAI direct text generation: <https://developers.openai.com/api/docs/guides/text>
- Codex SDK: <https://developers.openai.com/codex/sdk/>
- Codex app-server: <https://developers.openai.com/codex/app-server/>
- Claude Agent SDK: <https://platform.claude.com/docs/en/agent-sdk/overview>
- Claude Managed Agents: <https://platform.claude.com/docs/en/agents-and-tools/managed-agents/overview>
- Microsoft Agent Framework: <https://learn.microsoft.com/agent-framework/overview/>
- Google Agent Development Kit: <https://google.github.io/adk-docs/>
- Strands Agents: <https://strandsagents.com/>
- goose: <https://block.github.io/goose/>
- Agent Skills: <https://agentskills.io/specification>
- AGENTS.md: <https://agents.md/>
- A2A v1.0: <https://a2a-protocol.org/latest/>
- A2A specification: <https://a2a-protocol.org/latest/specification/>
- ACP: <https://agentclientprotocol.com/>
- AG-UI: <https://docs.ag-ui.com/>
