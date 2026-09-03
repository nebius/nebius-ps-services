# System Classification and Agent Patterns

Use this reference to decide whether agentic control flow is justified and to
select the smallest sufficient coordination pattern.

## Contents

- Behavior classification
- Independent architecture axes
- Pattern ladder
- Multi-agent decision rules
- Anti-patterns
- Decision worksheet

## Behavior Classification

Classify each capability, not the entire product, at the lowest level that can
pass representative acceptance gates.

| Class | Decision owner | Use when | Do not upgrade merely because |
| --- | --- | --- | --- |
| Deterministic code | Application | Rules and outputs are fully specified | The logic has many branches |
| Direct model call | Application around one bounded inference operation | One application-level operation can produce the accepted output | Bounded transport, structured-output, or schema-repair retries are used while code still owns continuation |
| Deterministic AI workflow | Application | Code knows steps, transitions, and continuation | There are multiple calls, tools, queues, or an unknown-count code-owned loop |
| Agent | Model inside policy bounds | The model must choose tools, actions, continuation, or observation-driven next steps | A framework calls its primitive an agent |

Require evidence that the next lower class fails a named acceptance gate before
escalating. One product may use all four classes for different capabilities.
A retry does not change the class unless it introduces model-owned
continuation, tool choice, effects, or an application workflow with additional
semantic steps.

## Independent Architecture Axes

Do not collapse these decisions:

```text
model autonomy:
  deterministic code -> direct call -> deterministic AI workflow -> agent

coordination:
  one unit -> deterministic router -> parallel workers -> orchestrator-workers
  -> independent remote participants

execution durability:
  in-process -> persisted graph/checkpoint -> durable cross-service workflow

deployment:
  same process -> ordinary service boundary -> capability protocol -> remote agent
```

A durable workflow can contain no agent. An agent may be short-lived and
in-process. A graph can represent deterministic or agentic nodes. Remote
deployment reflects ownership and isolation, not intelligence.

## Pattern Ladder

### One Bounded Agent

Default when one context, tool boundary, owner, and policy domain can complete
the task. Give it typed tools, explicit budgets, deterministic authorization,
and a safe terminal state.

Avoid splitting by persona, prompt role, or organizational chart when one
agent can satisfy the same acceptance gates.

### Deterministic Router

Use application rules to select a specialist when routing inputs are stable,
auditable, and cheaply testable. This is preferable to a supervisor agent when
the routing decision is known.

### Agents as Tools

Use one coordinator that invokes a bounded specialist through a typed task and
result contract. The coordinator owns budgets, authority, cancellation, and
integration. The specialist does not inherit every coordinator tool or memory.

### Parallel Independent Workers

Use when tasks are independent, parallelism materially improves latency or
coverage, and results can be merged or adjudicated deterministically. Freeze
inputs and merge rules before dispatch. Bound fan-out and cancellation.

### Orchestrator-Workers

Use when decomposition is dynamic, the number or shape of subtasks is not known
in advance, specialists require different least-privilege contexts, and one
owner must synthesize the outcome.

The orchestrator owns:

- task graph and immutable task identifiers;
- worker contracts and authority ceilings;
- dependency readiness and concurrency limits;
- result validation, conflict policy, and synthesis;
- cancellation, budgets, retries, and terminal status.

Workers own only their assigned task and return typed evidence. They never
silently expand scope or mutate the shared plan.

### Remote Agent Boundary

Use only when another deployed or organizational unit owns an independent
task-level capability and must hide its internal implementation. Put identity,
authorization, quotas, deadlines, data policy, versioning, and telemetry at the
boundary. Use an ordinary API or tool protocol when the remote unit is really a
deterministic service or capability.

### Event-Driven Choreography

Use selectively when participants are independently owned, no central owner
can or should control the complete process, and eventual consistency is an
accepted business property.

Require:

- versioned event contracts and authoritative producers;
- correlation and causation identifiers;
- idempotent consumers and duplicate-delivery handling;
- explicit conflict, timeout, compensation, and reconciliation policies;
- bounded discovery and lifecycle ownership;
- end-to-end observability without a hidden central coordinator.

Prefer orchestration when one product owner must explain, pause, resume,
approve, cancel, or recover the end-to-end process.

## Multi-Agent Admission Gates

Add more than one agent only when at least one is current and measurable:

- parallelism materially improves an acceptance metric;
- context isolation prevents quality or security failure;
- tools or credentials require distinct authority boundaries;
- specialist models or environments pass a quality/cost gate;
- independent team or deployment ownership requires a remote boundary;
- dynamic decomposition beats one bounded agent or deterministic routing.

Also require:

- one owner for shared task truth;
- typed task, result, error, and cancellation contracts;
- deterministic authority and effect policies;
- conflict and synthesis rules;
- global and per-agent budgets;
- trajectory and end-to-end evaluation.

Reject multi-agent topology when its only rationale is role-play, novelty,
framework availability, integration count, or an assumption that more model
calls improve quality.

## Common Anti-Patterns

- Agent for a fixed sequence: use normal code or a deterministic AI workflow.
- Supervisor for known routing: use an application router.
- Peer chat as coordination: use typed tasks, artifacts, and state transitions.
- Shared universal toolset: assign least-privilege tools and credentials per
  agent.
- Shared unbounded context: pass the minimum authorized task context.
- Hidden recursion: cap depth, fan-out, steps, tokens, time, and cost.
- Model-owned approval: approvals and authorization remain deterministic.
- Framework-driven architecture: select behavior and contracts before products.
- Event bus as architecture: introduce events only with ownership, delivery,
  consistency, and recovery semantics.
- Multi-agent before baseline: compare against one agent and deterministic
  routing on the same representative tasks.

## Decision Worksheet

For each capability, record:

```text
Outcome:
Representative task:
Baseline:
Behavior class:
Why the lower class fails:
Decision owner:
Pattern:
Authority ceiling:
Step/time/token/cost budgets:
State and durability need:
Failure and cancellation path:
Acceptance gate:
Owner:
Revisit trigger:
```

## Primary Sources

- [OpenAI, A practical guide to building agents](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/)
- [Anthropic, Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)

Use these sources for durable architecture principles. Verify current SDK or
framework claims separately through `ai-stack` or `research`.
