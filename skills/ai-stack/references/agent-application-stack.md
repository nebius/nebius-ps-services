# Agent Application Stack

Use this reference to design and select frameworks for production agentic
applications. Keep the agent kernel, workflow durability, model providers,
tools, open protocols, state, security, and evaluation independently replaceable.

Baseline date: 2026-08-07

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
Agent kernel:
  Pydantic AI for typed, provider-neutral bounded agents

Explicit graph orchestration:
  LangGraph only when branches, checkpoints, interrupts, replay, or recovery
  require a first-class state graph

Cross-service durable execution:
  Temporal for timers, long waits, external side effects, compensation, and
  workflow recovery

Model access:
  Narrow application-local seam for one intentionally selected provider
  Internal capability-aware ModelProvider contract when multiple providers,
  routing, evaluated fallback, self-hosting, regional/data policy, or shared
  platform ownership creates semantic portability requirements
  Native provider adapters for OpenAI and Anthropic
  Native or verified OpenAI-compatible adapter for Nebius Token Factory
  agentgateway when shared platform routing and policy are justified

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

Do not install every layer for every application. A bounded read-only assistant
can use Pydantic AI, one narrow provider seam, direct functions, and PostgreSQL
without LangGraph, Temporal, MCP, A2A, a gateway, or a new telemetry platform.

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

Own prompt assembly, typed dependencies, model calls, bounded tool loops,
structured output, agent-local policies, and run context.

Current default: Pydantic AI for Python provider-neutral agents.

Keep the kernel small. Do not let it become the authoritative store, workflow
scheduler, secret manager, or provider gateway.

### Orchestration And Durability Plane

Own explicit transitions, checkpoints, interrupts, timers, retries, and
recovery.

- LangGraph: Own in-application graph state and model/tool workflow topology.
- Temporal: Own durable cross-service workflows and non-idempotent side effects.
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

Own provider configuration, model capability resolution, credential injection,
routing, budgets, fallback, error normalization, and telemetry. Use a narrow
application-local seam for one intentionally selected provider. Implement the
full contract in `model-provider-contract.md` only when multiple providers,
routing, evaluated fallback, self-hosting, regional or data policy, or shared
platform ownership creates semantic portability requirements.

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

Select the simplest sufficient framework that implements required semantics:

```text
deterministic function
  -> one structured model call
  -> bounded typed agent
  -> explicit graph
  -> durable cross-service workflow
  -> agent harness for open-ended coding or research
  -> independently deployed remote agent
  -> multi-agent network
```

Require a measured reason to move upward.

### Decision Table

| Requirement | Preferred choice | Add or switch when |
| --- | --- | --- |
| Python, provider-neutral, typed bounded agent | Pydantic AI | A provider-native SDK or another ecosystem offers required semantics with lower total complexity |
| Simple high-level agent with broad integrations | LangChain `create_agent` | Explicit graph state, replay, or recovery requires LangGraph directly |
| Explicit state graph, checkpoints, interrupts, time travel | LangGraph | Cross-service timers and irreversible side effects require Temporal outside the graph |
| Long-running business process and durable side effects | Temporal | Work is short-lived, read-only, and safely replayable inside the agent runtime |
| OpenAI-native agent | OpenAI Agents SDK | Provider portability or custom graph semantics become required |
| Claude-native coding, file, sandbox, or Skill agent | Claude Agent SDK | A provider-neutral service agent is the actual requirement |
| Anthropic-hosted durable agent session | Claude Managed Agents | Provider neutrality, lifecycle control, or non-beta stability is required |
| Microsoft or .NET enterprise agent stack | Microsoft Agent Framework | Exact language, package, feature lifecycle, non-Microsoft ecosystem fit, or portability fails the gate |
| Google and A2A-oriented agent | Google ADK | Cross-provider behavior or non-Google operations favor the default kernel |
| AWS and Bedrock-oriented agent | Strands Agents | AWS alignment does not outweigh ecosystem coupling |
| Retrieval-centric agent or index workflow | LlamaIndex components | Retrieval is not the dominant abstraction |
| Local model-agnostic developer agent | goose | A product service or custom application framework is required |
| Role-based agent team library | CrewAI, Conditional | Explicit control, durability, and evaluation pass against simpler agents-as-tools |
| Open-ended coding or research harness | Provider harness or Deep Agents-style runtime | Bounded service behavior is more appropriate |

Do not choose a framework from integration count alone. Evaluate control flow,
state, provider portability, tool schema behavior, durability, security,
observability, deployment, lifecycle, and exit cost.

## Framework Profiles

### Pydantic AI

Use as the current default agent kernel for Python when these are important:

- typed inputs, outputs, dependencies, tools, and validation;
- model and provider separation;
- native and OpenAI-compatible provider support;
- a clean path to Nebius Token Factory through a verified OpenAI-compatible
  provider profile;
- MCP client integration;
- testability through model and tool substitution;
- optional durable execution integrations.

Use it for bounded single-agent applications, structured workflows controlled by
application code, and agents invoked as steps inside another workflow.

Do not treat Pydantic validation as authorization. Do not share one MCP client
or toolset across users when that object carries one identity or credential.
Construct per-user or per-run clients where identity differs.

### LangChain

Use selected LangChain components for model integrations, tools, loaders,
retrievers, middleware, and simple agents. Use `create_agent` when the workflow
is a bounded tool loop and the generated graph behavior is sufficient.

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

### Temporal

Use Temporal outside the agent kernel when work spans services or must survive
process failure, long waits, human approvals, provider outages, timers, or
irreversible side effects.

Keep workflow code deterministic. Execute model calls, MCP calls, and external
side effects in activities. Persist version identities and idempotency keys.
Map MCP Task handles to Temporal workflow or activity identity when Tasks are
exposed.

Do not put token-by-token model streaming inside durable workflow history.
Store references or bounded summaries instead.

### OpenAI Agents SDK

Use for an intentionally OpenAI-first profile when agents, agents-as-tools,
handoffs, tools, sessions, guardrails, human approval, tracing, and MCP match the
product.

Use the Responses model path for native OpenAI capabilities. A custom
OpenAI-compatible base URL does not prove that another provider supports the
same Responses or tool behavior. Run provider conformance tests.

Review tracing export policy. Disable or replace the default processor where
organizational data policy requires another telemetry path.

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
durability, tracing, and framework maturity against Pydantic AI and LangGraph.

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

Use:

```text
FastAPI or existing application service
  -> Pydantic AI agent
  -> narrow application-local seam around the selected native provider SDK
  -> direct typed tools
  -> existing authoritative store when durable records or audit are required
  -> OpenTelemetry when cross-service trace correlation is required
```

Add the full capability-aware `ModelProvider` contract only when multiple
providers, routing, evaluated fallback, self-hosting, regional/data policy, or
shared platform ownership creates semantic portability requirements. Add MCP
only for independently reusable tools. Add LangGraph only when explicit graph
semantics appear.

### Durable Approval Workflow

Use:

```text
application API
  -> Temporal workflow
  -> Pydantic AI or LangGraph activity
  -> authorization and approval service
  -> idempotent tool or MCP activity
  -> verification activity
```

Keep business truth and approval records in the authoritative database.

### Explicit Research Or Operations Graph

Use:

```text
LangGraph
  nodes: plan, retrieve, analyze, request approval, execute, verify
  checkpointer: execution state only
  agent nodes: Pydantic AI or LangChain components
  external side effects: Temporal activity or idempotent service call
```

Do not use an unconstrained supervisor when the transitions are known.

### Coding Agent

Use:

```text
AGENTS.md
  + reviewed Agent Skills
  + Claude Agent SDK, goose, Microsoft harness, or another coding harness
  + MCP for approved reusable developer tools
  + ACP when integrating with an editor client
  + sandbox and repository-scoped credentials
```

Keep repository instructions and Skills versioned with source or an approved
catalog. Make write, command, network, and deployment actions approval-aware.

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
- Temporal workflow state: Durable business-process progress.
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

- agent kernel and typed application dependencies;
- narrow provider seam or conditionally justified full `ModelProvider`;
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

- Pydantic AI: <https://ai.pydantic.dev/>
- LangChain agents: <https://docs.langchain.com/oss/python/langchain/agents>
- LangGraph: <https://docs.langchain.com/oss/python/langgraph/overview>
- Temporal: <https://docs.temporal.io/>
- OpenAI Agents SDK: <https://openai.github.io/openai-agents-python/>
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
