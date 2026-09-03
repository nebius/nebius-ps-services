---
name: ai-agent-design
description: "Design provider-neutral production AI agent subsystems: behavior and topology, contracts, tools, context, memory, authority, durability, security, evals, and operations. Use ai-stack when AI technology selection remains, design/app-stack for whole products, and implementation skills for code."
---

# AI Agent Design

## Help

For `$ai-agent-design --help` or `$ai-agent-design -h`, return concise help and
stop before any workflow step. State the purpose and invocation policy. Show exact usage
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

Design the complete AI-agent subsystem around a deterministic control and
policy shell with probabilistic reasoning only where it earns its complexity.
Apply production patterns, policies, and contracts; use `ai-stack` for the
workload-driven AI component and technology decision only when a model-backed
capability remains; then synthesize the result into a decision-complete logical
architecture.

This skill owns agent-specific design. It does not own the surrounding product
architecture, technology due diligence, repository topology, implementation,
or live execution.

## Use This Skill For

- Designing a new bounded agent, agent-enabled workflow, durable agent
  process, or multi-agent subsystem.
- Revising an agent architecture whose control flow, authority, context,
  memory, tools, state, recovery, evaluation, or operational boundaries are
  materially undecided.
- Selecting among single-agent, agents-as-tools, deterministic routing,
  orchestrator-worker, parallel-worker, remote-agent, or event-driven patterns.
- Turning agent requirements into typed contracts, risk policies, failure
  handling, evaluation gates, rollout controls, and a logical implementation
  handoff.

## Boundaries

- Use `design` when the user needs the complete cross-layer product solution.
  Accept one scoped delegation from that active workflow, return this skill's
  agent-subsystem decision once, and do not recursively re-enter it.
- Use `app-stack` for frontend, API, ordinary backend, data, deployment, and
  other non-AI layers.
- Use `ai-stack` for model, agent runtime, graph or durable workflow engine,
  retrieval, interoperability, evaluation, safety, and observability technology
  selection. Pass a frozen behavior and policy contract, require it not to
  reopen those decisions, and do not duplicate its vendor and framework
  selection corpus. When an active `ai-stack` workflow delegates a disputed
  provisional contract here, return only the frozen contract to that caller
  and skip a nested `ai-stack` invocation.
- Use `research` for one disputed or volatile framework, protocol, model, SDK,
  paper, or product claim, then bring the bounded findings back here or to
  `ai-stack`.
- Use `system-design-rules` for checklist review of an already drafted generic
  architecture or ADR when no agent-specific redesign is requested.
- Use the relevant implementation skill only after the design and stack are
  approved. This skill never writes code or assigns repository paths.

## Inputs

- Desired outcome, users or callers, workload classes, and failure impact.
- Success metrics, unacceptable outcomes, evaluation data, and acceptance
  thresholds.
- Existing architecture, baseline behavior, fixed decisions, constraints, and
  migration or compatibility requirements.
- Data classifications, tenant and authorization boundaries, provenance,
  freshness, retention, residency, and privacy constraints.
- Proposed tools and effects, identities, scopes, approval needs, autonomy
  ceiling, and human operating model.
- Latency, throughput, availability, recovery, cost, ownership, rollout, and
  compliance requirements.

Do not invent decision-changing inputs. Use explicit assumptions or ranges and
identify unknowns that can change the recommendation.

## Required Reads

- Read `references/system-classification-and-patterns.md` for every substantive
  request before accepting agentic control flow or multi-agent topology.
- Read `references/runtime-architecture-and-sessions.md` when an accepted
  agent needs a runtime boundary, durable session, sandbox, asynchronous
  execution, user control, or process-failure recovery.
- Read `references/contracts-context-and-memory.md` when an agentic capability
  is accepted or when task, context, memory, provenance, or handoff contracts
  are in scope.
- Read `references/tools-authority-and-security.md` when the system uses tools,
  external data, retrieval, code execution, remote agents, credentials, tenant
  data, approvals, or side effects.
- Read `references/durability-failures-and-effects.md` when work is long-running,
  asynchronous, concurrent, multi-agent, externally effectful, replayed, or
  expected to survive process failure.
- Read `references/evaluation-observability-and-governance.md` for production,
  release, rollout, incident, privacy, or readiness work.

For version-sensitive products, APIs, protocols, SDKs, frameworks, or
standards, verify current official documentation or route the focal question
through `research`. Keep durable policies vendor-neutral.

## Design Workflow

### 1. Freeze The Agent Workload Contract

Record:

- outcome, actors, scope, baseline, and failure impact;
- representative tasks and acceptance gates;
- data, authority, effects, environment, scale, economics, and ownership;
- fixed decisions, explicit non-goals, assumptions, and blocking unknowns.

Use a separate contract for materially different online, batch, retrieval,
agent-execution, or evaluation workloads. Do not let one shared model collapse
different workload contracts.

### 2. Classify Behavior Before Architecture

Classify each AI-enabled capability independently:

- `Deterministic code`: normal software owns all decisions.
- `Direct model call`: one bounded application-level inference operation
  produces the result; bounded transport or schema-repair retries may occur
  while application code still owns continuation and no tools or effects are
  added.
- `Deterministic AI workflow`: application code owns steps, transitions, and
  continuation; model calls occur only at explicit steps.
- `Agent`: the model chooses actions, tools, continuation, or observation-driven
  next steps within explicit budgets and policy boundaries.

Use **deterministic where possible, agentic where necessary**. Unknown loop
count, multiple model calls, tools, persistence, or delegation do not alone
make a workflow agentic. If a simpler class passes representative acceptance
gates, recommend it and stop adding agent autonomy.

### 3. Select The Smallest Sufficient Pattern

Apply this phase only to capabilities classified as `Agent`. Deterministic
code, direct model calls, and deterministic AI workflows skip the agent-pattern
ladder. For an agentic capability, start with one bounded agent and escalate
only for a named requirement:

1. one agent with typed tools;
2. deterministic router or agents-as-tools;
3. parallel independent workers with deterministic merge;
4. orchestrator-workers for dynamic decomposition and synthesis;
5. independently deployed remote agents across real ownership boundaries;
6. choreography or peer collaboration only when centralized coordination is
   the wrong ownership model and conflict/reconciliation policies are explicit.

Separate model autonomy from execution topology and durability. A graph,
queue, workflow engine, event bus, or remote deployment does not make a node
agentic.

### 4. Define The Governed System

Define only the contracts and controls required by the accepted workloads:

- task envelope, agent definition, budgets, and termination rules;
- for accepted agents, trusted control, model-directed execution, durable
  session state, and versioned provider-neutral interfaces; reject unneeded
  runtime or session layers for simpler behavior classes;
- tool schema, identity, authorization, action risk, approval, idempotency,
  preview, verification, and audit;
- context sources, authorization-before-relevance, provenance, freshness,
  conflict handling, token budgets, and memory classes;
- canonical business state, execution checkpoints, append-only history,
  projections, concurrency, event delivery, and artifact ownership;
- failure taxonomy, retry ownership, deadlines, circuit breakers, bulkheads,
  compensation, reconciliation, cancellation, and safe terminal states;
- prompt-injection defenses, sandboxing, egress, tenant isolation, secrets,
  privacy, and retention;
- evaluation layers, trace model, release identity, promotion gates,
  observability, rollout, rollback, incidents, and owners.

For accepted agents, make the observe, decide, authorize, persist effect
intent, act, record receipt, verify, and terminate loop explicit. Define
visible progress, steer, cancel, approval, escalation, notification, and
cleanup controls when work is long-running or consequential. Treat a model
completion claim as a proposal for independent verification, not as proof.

Keep authentication, authorization, approvals, policy transitions, effect
execution, and postcondition verification deterministic and outside model
discretion.

### 5. Obtain The AI Stack Decision

Use `ai-stack` after behavior, policy, and workload constraints are frozen when
at least one model-backed capability remains. If every selected capability is
deterministic code, that classification takes precedence even when the request
initially asked for AI-stack review: record
`Skipped - no AI component required` and do not invoke AI technology
selection.
If this workflow was entered from an active `ai-stack` request solely to settle
a disputed provisional behavior, topology, policy, or contract decision,
return that frozen decision to the originating workflow and skip the remainder
of this phase; the originating `ai-stack` then performs component selection
exactly once.

Pass it:

- outcome and workload identifier;
- behavior classification and why simpler classes did or did not pass;
- acceptance gates and failure impact;
- data, tenant, privacy, region, license, and retention constraints;
- autonomy ceiling, tool inventory, action risks, and approval requirements;
- context, retrieval, state, durability, interoperability, and deployment needs;
- quality, safety, latency, throughput, availability, recovery, and cost goals;
- fixed decisions, owners, baseline, assumptions, and blocking unknowns.

Require `ai-stack` to return its scoped component table with technology,
`Required | Conditional | Deferred | Rejected` status, evidence state,
requirement served, owner, acceptance gate, and revisit trigger. Preserve that
decision; do not reopen settled product layers without new blocking evidence.

### 6. Synthesize And Stress The Architecture

Integrate the control-flow and policy design with the `ai-stack` decision when
one was required; otherwise preserve the explicit deterministic-only skip.
Check:

- every probabilistic decision has a bounded input, output, budget, and failure
  path;
- every side effect has one trusted authority, idempotency boundary, approval
  policy, verification path, and audit record;
- every state class has one owner, schema, retention, concurrency, and recovery
  policy;
- when those runtime components are present, control, execution, session state,
  context, memory, policy, tools, and traces communicate through versioned
  interfaces with explicit capability gates;
- every agent and worker has least-privilege tools and context;
- duplicate delivery, stale context, partial success, cancellation, provider
  failure, budget exhaustion, and human timeout reach safe states;
- evaluation and rollout gates bind the exact prompt, model, tool, policy,
  data, index, runtime, and configuration identity.

Compare the selected design with the simplest baseline and at least one viable
alternative when the trade-off is material. Name rejected options and the
observable switch condition that could reopen them.

### 7. Hand Off Without Implementing

Return logical components, interfaces, policies, owners, gates, and sequencing.
Do not assign repository files, create a scaffold, install packages, provision
infrastructure, call paid services, deploy, or mutate data. Route physical
topology and execution to the owning project or implementation workflow.

## Guardrails

- Keep designs public-safe and generic. Do not persist secrets, customer data,
  private endpoints, internal hostnames, proprietary source, or raw traces.
- Treat prompts, retrieved content, tool output, MCP servers, remote agents,
  generated UI, external documentation, and model output as untrusted input.
- Enforce tenant and document authorization before retrieval or context
  assembly; relevance ranking never grants access.
- Treat MCP and remote-agent protocols as interoperability boundaries, not as
  authorization, tenancy, consent, or context-governance systems.
- Treat credential invisibility as necessary but insufficient: every delegated
  action still requires deterministic identity, authorization, target and
  destination constraints, scope, expiry, and audit.
- Require explicit approval bound to the exact action, resource, arguments,
  authenticated approver identity, authorization evidence, decision time,
  separation-of-duties status, authority, expiry, and current state for
  consequential effects. Replan after material drift; do not reuse stale
  approval.
- Never use free-form model text as a command, SQL statement, arbitrary URL,
  filesystem path, policy decision, or credential selector without a trusted
  typed boundary and constrained execution environment.
- Do not add multi-agent topology, long-term memory, a vector database, event
  choreography, MCP, a gateway, a workflow engine, or new infrastructure
  without a current trigger and owner.
- Preserve no legacy compatibility layer unless the user explicitly requires
  one.

## Validation

A design is complete only when:

- every workload has a baseline, behavior class, acceptance gates, and owner;
- every component has one status and every material claim has an evidence
  state;
- tool authority, approvals, context access, state ownership, retries,
  duplicate delivery, recovery, cancellation, and safe terminal states are
  explicit;
- quality, safety, privacy, latency, cost, recovery, rollback, observability,
  incident response, and ownership are covered proportionately;
- volatile claims use current official sources and assumptions cannot close a
  hard gate;
- implementation and live validation remain separately authorized.

Block the recommendation when a required safety, ownership, compatibility,
recovery, or acceptance gate is unknown and no bounded validation can close it.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return:

- scope, workload contracts, fixed decisions, assumptions, and blockers;
- a capability map that explicitly lists deterministic code, direct model
  calls, deterministic AI workflows, and agents, including empty classes;
- simplest baseline, selected topology, control/data flow, and ownership;
- trusted runtime architecture, provider-neutral interfaces, bounded agent
  loop, durable session, checkpoint, and operator controls when present;
- task, agent, tool, context, memory, state, event, approval, audit, and
  release-identity contracts that are present;
- the scoped `ai-stack` component table and its evidence states, or
  `Skipped - no AI component required` for deterministic-only scope;
- security, privacy, failure, recovery, independent outcome verification,
  evaluation, observability, governance, rollout, rollback, and incident
  decisions;
- rejected alternatives, trade-offs, and switch conditions;
- a logical-only implementation handoff with owners and acceptance gates;
- current official sources and unresolved uncertainty.
