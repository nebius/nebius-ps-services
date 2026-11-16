# Provider-Agnostic Agent Platform

Use for portable application boundaries, model routing, state, tools and
specialists. Agent-framework policy reviewed: 2026-11-13. The preferred general
agent kernel is OpenAI Agents SDK unless explicitly overridden; portability is
an application contract and a tested capability profile, not a brand claim.
Read [SDK integration](openai-agents-sdk-integration.md) for concrete SDK seams.

## Architecture And Smallest Deployment

```text
application-owned behavior, policy, state and runtime facade
  -> direct provider request for direct-call behavior
  -> ordinary workflow for code-owned continuation
  -> OpenAI Agents SDK Agent / Runner for model-directed continuation
  -> qualified model adapter and exact approved endpoint
```

Honor explicit fixed stack decisions, including Pydantic AI. Do not silently
migrate them. If the preferred SDK fails a required gate, report the blocker
and seek an explicit framework override. Never nest one agent kernel inside
another to hide an incompatibility.

Start with one application process, typed domain tools, existing storage when
state is needed, and existing telemetry. A facade is a logical boundary, not
necessarily a service. Add a durable workflow owner for long waits, recovery or
consequential effects; model/tool execution belongs in its activity boundary.
A shared platform additionally needs a real ownership, isolation, governance
or capacity trigger. Gateways, catalogs, queues, caches, object stores and
specialized databases each need their own trigger and owner.

## Ownership

| Concern | Canonical owner |
| --- | --- |
| Business invariants and behavior classification | Application design |
| Agent model/tool interaction loop | Selected SDK |
| Provider translation and explicit extensions | Qualified SDK adapter |
| Functional support | Exact route conformance evidence |
| Operational health and governance eligibility | Application policy/catalog |
| Agent, instruction, schema and tool versions | Release-owned definitions |
| Run, conversation, approval and effect truth | Application state services |
| Authorization, approval and side effects | Deterministic domain boundary |
| Routing, fallback, budgets and retry ownership | Application control |
| Sandbox lifecycle and cleanup | Execution owner |
| Telemetry, evaluation and release identity | Application operations |

Use supported framework primitives instead of recreating model translation,
structured output or the agent loop. With an explicit Pydantic AI override,
use its Model/Provider/profile semantics inside that adapter. Do not turn any
framework's spec, dependency object or session into the domain authority.

## Application Contracts

Define only the needed run, stream, resume, cancel and normalized-event
interfaces. Each mutation binds a verified principal, tenant/project, run,
expected state version and idempotency identity. Resume also binds the exact
approval receipt or external event. Reauthorize and atomically validate the
transition; possession of a run ID is insufficient.

A declarative agent definition names its version, instruction/schema/tool
references, required capabilities, model route policy, deadlines, finite model
and tool budgets, and durability profile. References resolve through approved
release inputs; configuration never contains credentials or grants authority.
Record both the definition and its materialized closure.

Normalize client events such as run started/waiting/completed/failed/cancelled,
model request/delta, tool proposed/approved/completed, and artifact created.
Keep provider events internal. A terminal SDK result is not independent proof
of a business postcondition.

## Model Catalog And Routing

Separate functional support (tools, structured outputs, modalities, reasoning,
context and limits), operational evidence (health, latency, concurrency,
quotas and circuits), and governance (tenant, residency, retention, data class
and approval). Avoid an independently drifting duplicate of SDK capability
metadata; assign a conformance and refresh owner.

Apply policy and capability hard gates before scoring eligible routes.
Resolve the exact provider, endpoint, model/revision, region, adapter/protocol,
settings and release identity. Persist route reasons and any evaluated fallback
plan. One fixed provider need not have a router or shared catalog service.

Routing occurs before execution. Fallback follows a classified failure and
is allowed only on an evaluated policy-valid route. Never route around denied
authorization, unsupported schemas or unknown side effects. Assign one retry
owner per failure class and retain cumulative spend across route changes.

Use the SDK's provider seam first. A custom cross-runtime model contract is
Conditional on real shared consumers; [provider contracts](model-provider-contract.md)
describe that optional seam. Keep provider-specific reasoning, native tools,
caching and service controls explicit. An extension cannot alter domain
identity, authority, idempotency or tool results.

## Tools, Approval And Execution

Use thin typed adapters over domain functions/APIs. The domain boundary owns
current authorization, targets, destinations, exact approved arguments,
idempotency, deadlines, output limits and audit. Existing matching authority
remains valid; only a missing scope delta needs a new decision.

MCP connections are interoperability channels. Authenticate and authorize per
principal/run, allowlist capabilities and egress, sanitize results, and avoid
sharing mutable identity-bearing clients across tenants. Add MCP only for a
reusable independent capability boundary.

Finite tool concurrency, sandbox limits and disjoint child reservations are
application policy. Bound hidden helper work as well as visible tool calls.
An admitted parent does not permit arbitrary helper commands; reconcile lost
acknowledgments by semantic operation identity. Stop new helper admission
before settling the parent and prove all descendants quiescent.

## State And Recovery

Keep canonical conversation/run records, working context, execution checkpoints,
optional semantic memory, artifacts and audit under explicit owners and
retention policies. They may share storage while retaining logical boundaries.
Provider sessions and caches cannot be the sole durable workflow truth.

Select one history strategy. SDK sessions can implement application storage;
when the application already compiles history, avoid an extra independent
session writer. Authenticate and qualify persisted SDK state or reconstruct
from application checkpoints and receipts. Fence old owners and reconcile
unknown effects before replacement execution. Revocation and deletion barriers
apply to restored context, caches, summaries and opaque provider continuations.

Approval is a durable state, not a blocking thread. Cancellation denies new
ordinary dispatch before stopping/draining owned work. Cleanup remains narrowly
authorized after task expiry; report unresolved cleanup independently of the
verified task outcome. Streaming deltas and tool error strings do not settle
unknown execution.

## Specialists And Independent Durability

Prefer one bounded agent. Add agents-as-tools when the parent retains control;
use a handoff when a specialist takes over the conversation. Distributed work
requires durable admission, stable assignment identity, disjoint parent-budget
reservations, bounded lifetime graph growth and deterministic result merge.
Do not infer distributed ownership from an SDK handoff or nested runner.

Use Codex SDK for a coding-harness requirement, behind one explicit bounded
capability. Add app-server integration only for a deep client. Other specialized
harnesses require explicit selection and exact feature/data-policy qualification.
General framework alternatives require the override rule above.

Graph and durability choices are independent of model autonomy. LangGraph is
conditional on explicit graph semantics. Select one durable owner, such as an
existing qualified engine, Temporal, DBOS, Prefect or Restate, from workload
requirements; do not mandate one engine or require agents to justify durability.

## Adoption And Evidence

1. Freeze behavior/policy, accepted stack choices and representative gates.
2. Prove the simplest direct, deterministic or bounded-agent slice.
3. Qualify exact provider capabilities and SDK extension seams.
4. Add only required state, tools, resource limits and recovery boundaries.
5. Test tenant isolation, approval drift, partial streams, unknown effects,
   cancellation, restore barriers, budget exhaustion and upgrades.
6. Compare accepted outcomes, latency and total cost against the baseline.

Keep trace export explicit before execution. Pin prompt, tool, model, adapter,
SDK, policy and data/index identity. Documentation, source checks, runtime
conformance and live outcome qualification are different evidence states.
Do not present a documented feature, successful smoke test or model completion
claim as production readiness.
