# Provider-Agnostic Agent Platform

Use this reference when a Python application needs portable model access,
agent loops, specialist agents, runtime routing, or provider-native escape
hatches. It refines the general agent and provider references without making a
distributed platform mandatory.

Baseline date: 2026-08-16

## Contents

- Architecture judgment
- Control and ownership boundaries
- Deployment profiles
- Pydantic AI execution boundary
- Application contracts
- Model catalog and routing
- Provider extensions and native runtime escapes
- Tools, MCP, and approvals
- State, events, and durability
- Specialist agents
- Reliability, security, observability, and evaluation
- Adoption sequence

## Architecture Judgment

Adopt this default:

```text
application-owned runtime facade
  -> Pydantic AI direct model API for portable direct requests
  -> Pydantic AI Agent for portable model-directed loops
  -> provider Model and Provider implementations
  -> exact approved model endpoint
```

Pydantic AI owns provider translation and the portable model/tool interaction
loop. The application owns domain behavior, state, routing policy,
authorization, tool execution, budgets, observability, evaluation, and release
identity.

This is provider-agnostic at the application boundary, not a claim that
providers are equivalent. Preserve differentiated reasoning controls, native
tools, caching, context management, safety settings, and service tiers through
explicit provider adapters.

Keep the least-agentic ladder:

1. Deterministic application code.
2. Direct model call.
3. Deterministic workflow containing explicit model calls.
4. One bounded Pydantic AI agent.
5. Pydantic AI capability or Harness specialist.
6. Provider-native runtime after an escape decision.
7. Independently deployed or multi-agent topology after an ownership trigger.

Do not put Pydantic AI around a provider-native agent runtime. Both own the
interaction loop and their state, tool, delegation, and event semantics do not
compose as nested model adapters.

## Control And Ownership Boundaries

| Concern | Canonical owner | Pydantic AI role |
| --- | --- | --- |
| Business workflow and invariants | Application or durable workflow | Invoked at explicit steps |
| Portable model/tool loop | Pydantic AI | Primary owner |
| Provider translation | Pydantic AI `Model` and `Provider` | Primary owner |
| Functional model profile | Resolved Pydantic AI profile plus verified catalog | Execution input |
| Operational and governance metadata | Application control plane | Consumer |
| Agent, prompt, and schema versions | Application registry or source | Materialized input |
| Canonical run, conversation, and approval state | Application state services | Returns messages and usage |
| Tool authorization and side effects | Domain service or tool gateway | Thin typed adapter |
| Routing, fallback policy, and budgets | Application router and budget controller | Executes selected plan |
| Telemetry and evaluation identity | Application OTel and evaluation layer | Instrumented component |
| Provider-native harness | Specialized runtime adapter | Separate path |

The runtime facade is an architectural boundary, not necessarily a network
service. Keep it in-process until independent deployment, shared ownership,
load isolation, or policy centralization provides a measurable benefit.

## Deployment Profiles

### Lean Application

Start here for one service or team:

```text
application process
  -> in-process runtime facade
  -> Pydantic AI
  -> thin domain tools
  -> existing application database when durable state is required
  -> existing telemetry path
```

Do not add a gateway, queue, vector store, Redis, object store, workflow engine,
or separate control plane without a workload trigger.

### Durable Application

Add only when runs survive request or process boundaries:

```text
API
  -> durable workflow
       -> Pydantic AI model or agent activity
       -> idempotent domain activity
       -> durable approval wait
  -> application state and event store
```

Model calls and tool calls are activities. Workflow transitions, timers,
replay, approval waits, and compensation remain deterministic.

### Shared Agent Platform

Use when several applications or teams need shared governance:

```text
agent API
  -> application-owned runtime facade
  -> versioned agent, prompt, tool, and model catalogs
  -> deterministic policy and route planner
  -> Pydantic AI workers
  -> policy-enforcing tool or MCP gateway
  -> provider-neutral state, telemetry, and evaluation
```

A network gateway, shared quota service, queue, cache, or multiple specialized
stores remains Conditional even in this profile. Select each from an explicit
scale, data, latency, durability, or ownership requirement.

## Pydantic AI Execution Boundary

When one model call is sufficient, the behavior remains a direct call. Use
Pydantic AI's low-level direct API when the caller wants `ModelResponse`
control and no agent request services. Use a no-tools, single-request `Agent`
when typed output parsing, validation retries, typed dependencies, or similar
request services are required. Selecting that primitive does not make the
behavior agentic; model-owned tool choice, continuation, or observation-driven
next steps do.

Prefer these framework primitives instead of recreating them:

- `Model` and `Provider` for provider integration;
- resolved model profiles for supported execution features;
- model settings for portable controls;
- provider-specific settings for explicit extensions;
- typed dependencies for run-scoped service references;
- typed tools and toolsets for model-visible capabilities;
- typed output for application-facing results;
- capabilities for reusable runtime behavior;
- Agent Specs when declarative portability is valuable;
- Harness when a portable specialist needs a composable environment;
- instrumentation and Pydantic Evals for trace and quality evidence.

Agent Specs are configuration, not the business contract. Resolve their
references through application registries, validate strict outputs with Python
types at runtime, and record the final materialized versions. At the baseline
date, an Agent Spec `output_schema` guides the model and returns a dictionary
but does not enforce the schema's properties or required fields at runtime;
pass a typed `output_type` when strict application validation is required.

Dependencies carry identities and service references. Do not turn the
dependency object into mutable canonical workflow state.

## Application Contracts

Own a small runtime interface above Pydantic AI and any specialized runtime:

```python
from collections.abc import AsyncIterator
from typing import Protocol


class AgentRuntime(Protocol):
    async def run(self, request: "RunRequest") -> "RunResult": ...

    def stream(self, request: "RunRequest") -> AsyncIterator["RunEvent"]: ...

    async def resume(self, request: "ResumeRunRequest") -> "RunResult": ...

    async def cancel(self, request: "CancelRunRequest") -> None: ...
```

This interface normalizes application outcomes and events. It does not attempt
to normalize every provider or native harness feature. Each mutating request
contains the verified principal and tenant context, run ID, expected run-state
version, and idempotency identity. A resume request also carries the exact
approval receipt or external-event identity when applicable. The facade
reauthorizes the mutation and performs a compare-and-swap state transition;
possession of a run ID or stale approval is never sufficient.

Keep agent definitions declarative and versioned:

```python
from typing import Literal

from pydantic import BaseModel, Field


class CapabilityRequirement(BaseModel):
    name: str
    required: bool = True
    minimum: str | int | float | None = None


class RoutingPolicy(BaseModel):
    strategy: Literal["balanced", "quality", "latency", "cost"] = "balanced"
    allowed_providers: set[str] | None = None
    denied_providers: set[str] = Field(default_factory=set)
    preferred_regions: list[str] = Field(default_factory=list)
    max_run_cost_usd: float | None = None


class PlatformAgentDefinition(BaseModel):
    id: str
    version: str
    pydantic_agent_spec_ref: str | None = None
    instructions_ref: str
    output_schema_ref: str | None = None
    toolsets: list[str] = Field(default_factory=list)
    capabilities: list[CapabilityRequirement] = Field(default_factory=list)
    routing: RoutingPolicy = Field(default_factory=RoutingPolicy)
    max_model_requests: int = 20
    max_tool_calls: int = 30
    timeout_seconds: int = 120
    durable: bool = False
```

Avoid mutable class defaults in Pydantic models. This platform registry
envelope is deliberately not named `AgentSpec`: Pydantic AI owns that distinct
declarative type. The optional reference may resolve and materialize a
Pydantic AI Agent Spec, while platform-only routing, budgets, tool policy, and
release identity remain in this envelope. It contains references and policy,
never credentials, business implementation code, or authorization assumptions
hidden in prompt text. In this sketch, `None` means no additional agent-level
provider restriction; governance policy still filters every route.

Normalize client-facing events such as:

```text
run.started
routing.selected
model.request.started
model.output.delta
tool.requested
tool.approval.required
tool.completed
agent.step.completed
artifact.created
run.waiting
run.completed
run.failed
run.cancelled
```

Do not expose raw provider event objects as the stable client contract.

## Model Catalog And Routing

Separate three metadata classes:

- Functional: tools, strict structured output, modalities, reasoning controls,
  native tools, caching, context management, and limits.
- Operational: endpoint, region, latency, concurrency, quotas, error rate,
  health, and circuit state.
- Governance: tenant allowance, data class, retention, residency, contract,
  approval, and experimental status.

Use Pydantic AI's resolved model profile as framework evidence. Supplement it
with verified application metadata; do not copy profile facts into an
independently drifting catalog without a synchronization and conformance owner.

Application code requests capabilities, not a literal provider model ID.
Resolve a complete immutable route plan for every run:

```text
derive requirements
  -> apply tenant, data, region, and retention policy
  -> filter functional capabilities and context limits
  -> filter health, circuit, concurrency, and quota
  -> score eligible candidates
  -> choose primary
  -> build an ordered, policy-valid fallback plan
```

Apply hard constraints before weighted scoring. Quality, latency, cost, health,
and affinity weights are versioned platform policy, not prompt text. Persist
the policy version, candidates, reasons, primary, fallback order, and resolved
provider model identity.

Routing chooses before execution. Fallback responds to a classified failure.
Do not retry authorization, schema, policy, or unsafe partial-execution
failures on another provider.

## Provider Extensions And Native Runtime Escapes

Keep provider-specific model behavior in provider adapter configuration:

- reasoning or thinking controls;
- native search, code, or other provider tools;
- caching and context compaction;
- safety and service-tier settings;
- background, continuation, or provider-state optimizations.

An extension may change model behavior. It may not change a domain tool's
authorization, input, output, idempotency, or side-effect contract.

Treat a native agent SDK as a separate runtime only when all are true:

1. A workload requires a named harness feature rather than merely the model.
2. The portable Pydantic AI path was evaluated against that requirement.
3. State ownership, tool mediation, normalized events, budgets, and evaluation
   remain explicit.
4. Data retention, residency, lifecycle status, and exit cost pass hard gates.
5. The decision and rollback trigger are recorded.

Examples include a provider-managed autonomous workspace, a materially better
coding sandbox, native delegated-agent semantics, or a provider-hosted session
that is itself part of the product. Provider origin alone is not sufficient.

Provider-side session state, caches, and compaction are optimizations. The
application must still reconstruct required workflow state without them.

## Tools, MCP, And Approvals

Treat tools as typed domain APIs. A model-visible adapter should normally do
only this:

```text
validate typed request
  -> call tool gateway with principal, tenant, run, and idempotency context
  -> return bounded and sanitized typed result
```

The domain service or tool gateway owns:

- current-principal authorization;
- agent and tool policy;
- approval requirements;
- exact approved arguments;
- idempotency and transaction behavior;
- timeout and retry semantics;
- output size and secret filtering;
- immutable audit events.

Approval is a durable workflow state. Persist the exact validated proposal,
reason, agent and model version, requester, approver, decision, and expiry.
Execute the exact approved payload; require a new approval if arguments change.

MCP provides interoperability, not trust. Allowlist servers and tools,
namespace names, authenticate per principal where needed, constrain network and
data classes, sanitize results, and authorize every tool call even when the MCP
connection is authenticated.

## State, Events, And Durability

Keep state classes distinct:

| State | Typical owner | Rule |
| --- | --- | --- |
| Run and workflow state | Durable application store | Survives restart and resume |
| Logical conversation | Application store | Provider-neutral canonical history |
| Working context | Run store or bounded cache | Reconstructible and retention-aware |
| Long-term semantic memory | Search or vector capability when justified | Provenance and deletion required |
| Artifacts | Existing object or artifact store when needed | Context receives references and excerpts |
| Audit | Append-only security record | Independent from conversation retention |
| Provider session or cache | Provider optimization | Never the sole canonical copy |

For compaction, preserve policy-compliant canonical history, maintain a
provider-neutral checkpoint, rebuild context from recent messages and durable
facts, and use provider-native compaction only as an optional optimization.

Use durable execution when a run waits beyond a request, survives restarts,
has timers or external events, waits for approval, or coordinates multiple
side effects. Persist route and model identity per attempt. Side-effecting
activities need domain-enforced idempotency; never blindly replay them.

## Specialist Agents

Use specialists only when separate context, tools, authorization, model
capabilities, deployment ownership, or evaluation creates a real boundary.
Do not start with a swarm.

Every specialist declares:

- typed input and output;
- minimum context rather than the whole parent transcript;
- allowed tools and data classes;
- principal and authorization scope;
- model capabilities and route policy;
- step, token, time, cost, and parallelism budgets;
- deterministic completion and failure contract;
- evaluation suite and release identity.

Preferred patterns, in order:

1. Deterministic application router to one specialist.
2. Manager invoking specialists as typed tools.
3. Parallel independent fan-out with a deterministic join.
4. Durable graph or workflow with bounded agent steps.
5. Remote agent protocol across an independent ownership boundary.

Pydantic AI capabilities are suitable for reusable cross-cutting behavior such
as model selection, context loading, tool filtering, delegation, or
instrumentation. Use Harness for a richer portable Coder, Researcher, or custom
specialist environment only when its additional lifecycle and context features
are requirements. Use Codex for a coding-focused harness requirement rather
than treating it as the general service-agent runtime.

## Reliability, Security, Observability, And Evaluation

Assign one retry owner per failure class. Bound aggregate attempts across HTTP,
provider SDK, Pydantic AI, router, and durable workflow layers. Define stable
application errors and keep provider bodies internal unless safe to expose.

Enforce a run budget before and after expensive operations. Include wall time,
model requests, tool calls, input and output tokens, cost, steps, and parallel
agents. Model self-reported completion is not a budget control.

Propagate principal, tenant, purpose, request, run, and session identity to
every tool call. Never expose credentials to the model. Treat prompts,
retrieved text, websites, files, code comments, MCP output, and remote-agent
messages as untrusted content. Sandboxed execution needs per-run isolation,
bounded resources, restricted egress, scoped credentials, and controlled
artifact export.

Use OpenTelemetry as the platform trace contract. Record route reasons, model
and provider identity, attempts, usage, estimated cost, tool and approval
events, latency, status, and resolved component versions. Keep sensitive prompt
and response capture policy-controlled.

Evaluate more than final prose:

- typed output validity and task correctness;
- tool choice, arguments, authorization attempts, and side effects;
- number of model and tool steps;
- latency, cost, and fallback behavior;
- citation or evidence quality where required;
- recovery under injected provider, tool, MCP, and worker failures;
- consistency across every candidate route.

Shadow, canary, and progressively roll out new agent, prompt, model, tool, and
routing versions. Roll back the complete compatible identity, not only a model
alias.

## Adoption Sequence

1. Freeze workloads and classify each capability.
2. Introduce an in-process runtime facade around the current portable path.
3. Move portable Python direct and agent execution onto Pydantic AI where it
   passes target conformance and evaluation gates.
4. Keep provider-specific settings in explicit adapters.
5. Put meaningful tools behind deterministic policy and domain boundaries.
6. Persist provider-neutral run, conversation, approval, and effect state.
7. Add capability-aware routing only when more than one eligible route exists.
8. Add bounded fallback only after replay and semantic conformance tests.
9. Add durable execution, shared services, specialists, or native runtimes only
   on their named triggers.
10. Record exact versions and validate the complete release identity.

Reject these defaults:

- one provider agent SDK per provider behind a misleading common interface;
- a custom model protocol duplicating Pydantic AI in the portable path;
- provider sessions as canonical memory;
- authorization or irreversible side effects controlled by prompts;
- automatic cross-provider replay after an unverified side effect;
- every tool, store, gateway, workflow engine, or specialist installed up
  front;
- raw provider events or provider error bodies as public API contracts.
