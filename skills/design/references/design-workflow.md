# Design Workflow Reference

Use this reference for medium or deep designs, greenfield applications,
multiple unfamiliar technologies, unclear architecture choices, non-trivial
`system-design-rules` decision review, or any design that will become a
committed artifact.

## AI Application Control-Flow Rule

For each AI-enabled capability, have `ai-agent-design` use the least-agentic
sufficient level:

1. **Deterministic code**: no model judgment is needed.
2. **Direct model call**: one model request can solve the task.
3. **Deterministic workflow containing model calls**: application code knows
   the sequence rules and owns every transition and continuation condition;
   model calls are explicit steps inside that workflow.
4. **Agent**: the model must choose an action, continuation, or next step from
   observations, including an unknown step count controlled by the model, or
   the task needs a model-driven observe-act-observe loop.

Use this decision table:

| Question | If yes | If no |
| --- | --- | --- |
| Is model judgment unnecessary? | Deterministic code | Continue |
| Can one model request solve the task? | Direct model call | Continue |
| Does application code own the operation and continuation rules? | Deterministic workflow | Continue |
| Must the model choose which application tool or action to use? | Agent candidate | Normal workflow |
| Must the model decide the next step from previous results? | Agent | Workflow |
| Must the model decide whether an unknown-count loop continues? | Agent | Workflow |
| Does the task need a model-driven observe-act-observe loop? | Agent | Direct call or workflow |
| Does it require delegation between specialists? | An agent runtime may help | Probably unnecessary |
| Does it require persistent agent sessions? | An agent runtime may help | Direct API may suffice |
| Is latency or cost predictability critical? | Prefer direct call or workflow | Agent may be acceptable |

Apply this tree in order:

```text
New task
  -> Is model judgment required?
       no  -> deterministic code
       yes -> Can one model call solve it?
                yes -> direct model API
                no  -> Do we know the steps?
                         yes -> deterministic workflow, with model calls
                         no  -> Must the model choose actions or next steps?
                                  yes -> agent
                                  no  -> deterministic workflow
```

The application architecture can use all four behavior classes at once:

```text
Application
  -> deterministic code
  -> direct model calls
  -> deterministic AI workflows
  -> agentic tasks
```

Keep APIs, PostgreSQL or other authoritative state, RBAC, approvals, tool
authorization, and known business transitions outside model discretion.
Delegation and persistent sessions help choose an agent runtime only after the
work requires agentic control flow. Tool use or several model calls alone do
not require an agent. Classify orchestration and durability separately: a graph
or durable workflow may wrap a direct call, deterministic workflow, agent, or
mixture, and does not itself create model autonomy.

## Phase Checklist

### 1. Understand Requirements

Capture only what changes the design:

- users and primary workflows
- desired behavior and acceptance signals
- constraints, non-goals, deadlines, and risk tolerance
- quality attributes such as reliability, latency, scale, security,
  observability, cost, compliance, and maintainability
- external systems, data classes, and operational ownership
- open questions that block or materially change the design

Prefer a working assumption over a blocking question when the assumption is
safe, reversible, and easy to validate later.

### 2. Understand Existing System

For every selected project, including an unused prototype, read its design
documents before proposing changes and then inspect its implemented paths:

- applicable instructions and the selected project's `README.md`, canonical
  design and requirements, ADRs, runbooks and changelog; use declared ownership
  to resolve candidates such as `docs/design.md` and `design.md`
- package manifests, lockfiles, build files, deployment configs, schemas, and
  generated artifacts
- source modules, public interfaces, command entrypoints, routes, handlers,
  workers, tests, fixtures, migrations, and observability surfaces
- related features that already solve a similar problem

Map the existing architecture in concise terms: components, responsibilities,
data ownership, call flow, extension points, validation paths, and constraints.

Trace executable behavior through affected callers and consumers. Tests are
supporting evidence, not a substitute for reading code. Do not use a sibling
project's design as the selected project's authority. If documentation is
missing, disclose the gap and proceed with bounded code-grounded planning. If
source is inaccessible, name the uncertainty rather than claiming alignment.

When design prose and implemented code disagree, code always wins as the
current-state baseline. Report each material conflict in this form:

| Document claim and reference | Implemented behavior and reference | Design impact and disposition |
| --- | --- | --- |
| What the project design says | What the executable path does | Follow code; identify proposal changes and spec-owner correction |

Do not rewrite code to satisfy stale prose or directly correct canonical docs.
Send findings and design decisions to `maintain-project-specs`. Code precedence
describes the current system; it does not certify a defect as correct, override
safety, or prevent an intentional user-requested change.

Separate code existence from application usage. Confirm no users or dependent
consumers through explicit user context or reliable project evidence before
applying the unused-greenfield refactor exception. Sparse code, an unreleased
label, missing docs or a local-only checkout are insufficient evidence. Ask if
usage is unknown and the proposed refactor relies on this exception.
Inspect all relevant prototype code. Only skip source discovery when no
relevant implementation exists; then design from requirements and intended
deployment or operating context without inventing local constraints.

### 3. Use `research` For Missing Knowledge

List unfamiliar or version-sensitive facts before designing. When the
`research` skill is installed and relevant, use it for topic,
feature-requirement, architecture-pattern, product, standard, and technology
due diligence. Bring the resulting evidence back into `design` for synthesis,
option selection, and `/plan` creation.

Only do direct research inside `design` for tiny clarifications or when
`research` is unavailable or not applicable. In that fallback path, research
against current official documentation whenever available.

Use official-source priority:

1. Official vendor documentation or API reference.
2. Official repository, examples, release notes, or migration guide.
3. Local code and tests for repo-specific wrappers or conventions.
4. Other sources only as leads, clearly marked if not verified.

For each technology, capture design-relevant facts:

- supported architecture and integration patterns
- current API or CLI syntax that affects the design
- limits, quotas, compatibility rules, lifecycle, or deprecations
- security, authentication, permission, and secret-handling implications
- deployment, migration, rollback, and observability considerations
- testability and local-development constraints

If a fact cannot be verified from official docs, say that it is unverified and
avoid making it a hard design dependency.

### 4. Use Specialist Skills

Use the actual named skill, not a summary of its description: resolve its
catalog location or sibling folder, load `SKILL.md`, read its required
references and follow its bounded advisory workflow. Reuse instructions already
loaded for this task. A skill handoff does not require a subagent or grant
delegation authority. This follows the
[Agent Skills activation model](https://agentskills.io/client-implementation/adding-skills-support).

Pass only the bounded question, relevant requirements and acceptance gates,
quality attributes, current implementation, fixed decisions, migration costs,
team/operational context, environment constraints and research evidence.
`design` owns the complete solution and plan; specialists return their scoped
decisions, evidence, assumptions and unresolved constraints to it.

| Condition | Skill and returned decision |
| --- | --- |
| Nebius services, infrastructure, deployment or integration materially affect the solution | `nebius`: provider constraints, feasible services, prerequisites, lifecycle owners, evidence and later validation needs |
| AI behavior, topology, authority, contracts, context, memory, durability, effects, evaluation or governance are unsettled | `ai-agent-design`: frozen four-class capability map and logical subsystem, including its applicable `ai-stack` result |
| AI components are unsettled and no behavior/contract decision remains | `ai-stack`: models, access, training, inference, SDK/runtime, durability technology, interoperability, retrieval and AI operations decisions |
| Application technology is undecided or being reconsidered | `app-stack`: frontend/client, web/API/runtime, services, data, asynchronous work, deployment and observability choices |

Apply these handoffs in the table's order for the scopes present:

1. Consult Nebius before finalizing provider-dependent choices, even when the
   provider or surrounding stack is fixed. Use its design-only path. Read only
   matching categories, including AI-service integration for Nebius AI services;
   do not let provider guidance become a second model/runtime selection owner.
   Credentials, inventory and provisioning preflight are not design
   prerequisites. Distinguish documented support from unknown project capacity,
   quota, identities or readiness; identify later checks without running them.
2. Let `ai-agent-design` freeze behavior and policy before component selection.
   Preserve deterministic code, direct calls, deterministic AI workflows and
   agents as separate capability classes. Its `ai-stack` handoff occurs only
   when model-backed capabilities remain; preserve any fixed component choices. If it returns deterministic-only behavior,
   record that no AI component is required and skip AI selection.
3. Reuse an AI component decision returned by `ai-agent-design`. Call `ai-stack`
   directly only for still-unresolved component choices with settled behavior.
   Preserve its provisional-classification and caller-aware disputed-contract
   return rules; do not re-enter an active workflow or redo component selection.
4. Give `app-stack` the completed AI decisions as fixed inputs and scope its
   work to the surrounding application. Its own AI routing must not duplicate
   those decisions. Preserve `Required | Conditional | Deferred | Rejected`
   component status, evidence, rationale, ownership and revisit triggers.

Skip fixed application, AI-behavior and AI-component scopes unless the request
reopens them. A fixed Nebius provider still requires relevant provider guidance.
Reuse completed handoffs in the active task. If new blocking evidence conflicts
with a fixed decision, return the bounded conflict to its owner; do not silently
override it or start a recursive design/stack loop. Integrate returned interfaces,
data/control flows, failure handling, rollout and validation in the full design.

#### Missing-Specialist Research Fallback

If an applicable skill cannot be loaded, state which scope is unavailable and
use `research` for the bounded knowledge gap. This also applies when a loaded
adviser reports a missing nested specialist; preserve the completed decisions.
`research` supplies evidence, while `design` synthesizes the fallback decision;
do not transfer stack selection or whole-product design ownership to research.

Prioritize current official vendor documentation, API references, specifications,
release notes and official source. When that coverage is unavailable or
insufficient, consult reputable established community or engineering sources.
Explain the coverage gap and source tier; corroborate material claims where
possible and label community-only claims, assumptions and uncertainty. Do not
promote an anecdote into verified provider behavior or live availability.

If `research` is also unavailable, apply the same source hierarchy directly and
disclose that limitation. Continue independent planning, but do not call a plan
implementation-ready with unresolved material facts. Unavailable tools, explicit-
only invocation, permissions, execution limits and workflow ownership are not
missing-knowledge exceptions and cannot be bypassed by this fallback.

For each relevant scope, report `used` with the consumed decision, `skipped`
with its reason, or `unavailable - research fallback` with the evidence and
remaining uncertainty (including direct research if that skill is unavailable).
Do not claim specialist invocation from a proposed handoff or final-answer text.

### 5. Design Solution

Build the detailed solution from behavior and constraints using the selected or
fixed stack. Define:

- component boundaries and responsibilities
- technology choices and why each one is necessary
- APIs, commands, events, schemas, storage, or other contracts
- data ownership, persistence, migrations, retention, and derived data
- control flow, state transitions, concurrency, idempotency, and retries
- error model, partial failure behavior, rollback, and recovery
- security, privacy, credentials, authorization, and auditability
- observability, metrics, logs, traces, dashboards, alerts, and runbooks
- tests, acceptance checks, validation commands, and evaluation criteria

For serial multi-layer applications, design by vertical feature slice before
planning broad layer work. A vertical slice ties one user-visible or
system-visible behavior through the relevant layers, for example
frontend -> API -> service -> database. Capture:

- end-to-end trigger, request, state change, response, and user-visible result
- each layer's responsibility and ownership
- contracts between layers, including API shape, validation, error model, and
  persistence expectations
- data lifecycle across authoritative and derived state
- tests and acceptance checks that prove the slice works through its boundaries

Use horizontal foundation steps only when they are true prerequisites for
multiple slices, such as schema contracts, auth, migrations, shared test
harnesses, infrastructure safety, or observability needed before safe delivery.

Before finalizing, compare the proposal with inspected callers, interfaces,
data ownership, persistence, configuration and workflows. For each mismatch,
either redesign the proposal to fit or identify an intentional requested
transition with affected consumers and regression checks. Unresolved material
conflicts remain open decisions, not implementation-ready assumptions.

When confirmed unused greenfield code contains an anti-pattern, cite concrete
harm against the intended behavior rather than style preference. Plan the
smallest necessary refactor as a prerequisite to dependent feature work, update
all affected consumers and tests, and keep one canonical implementation. Do not
retain legacy aliases, wrappers, dual paths or compatibility shims unless the
user explicitly asks. Existing users or unknown usage do not qualify for this
exception; surface any decision needed to change their established contracts.
Do not silently add a compatibility layer as a substitute for that decision.

For any existing code, name likely files/modules and integration points,
including intentional removals and replacements. Where no implementation
exists, name the initial project shape, runtime, framework, storage, deployment
target and bootstrap order. Planning a refactor does not itself authorize it.

### 6. Apply `system-design-rules` And Evaluate Alternatives

For standard, deep, architecture-heavy, ADR-like, cross-boundary, or
hard-to-reverse designs, use `system-design-rules` when it is installed and
relevant before locking the recommended solution. Apply its checklist as
advisory input over the design that `design` is synthesizing, not as a separate
final artifact unless the user asks for one.

Focus the review on categories that can change the solution:

- business outcome and explicit non-goals
- domain boundaries, component ownership, and team ownership
- API, command, event, schema, and integration contracts
- data ownership, lifecycle, privacy, retention, and migration
- reliability, idempotency, concurrency, recovery, and rollback
- security, permissions, secrets, auditability, and governance
- observability, operations, deployment safety, cost, and scale

For light local designs, apply only the relevant categories yourself or state
why a full `system-design-rules` pass is not warranted. If the user only asks
to critique an already-written proposal, hand off to `system-design-rules`
directly instead of continuing the full `design` workflow.

Compare options only at the depth needed for the decision. Include:

- baseline/current design or simplest possible approach
- recommended design
- one simpler, more conservative, or lower-risk alternative when meaningful

For each option, state what improves, what worsens, cost, risk, operational
burden, migration effort, reversibility, and revisit trigger.

### 7. Create Implementation Plan

The final design should be ready for the host's plan handoff. Make it specific
enough to implement without re-deciding architecture. For design-only work,
use available host planning mode or a plan tool. When implementation is already
requested, produce the plan with a plan tool or response without entering a
write-prohibiting mode. An already-active host Plan Mode still prevents execution.

Use this template:

```text
/plan

Objective:
- ...

Design Summary:
- ...

Existing-System Evidence And Conflicts:
- selected project and documents read: ...
- implemented paths and consumers inspected: ...
- document/code conflicts, with code taking precedence: ...
- proposal conflicts resolved or still blocking readiness: ...
- intentional changes and confirmed unused-greenfield evidence, if relevant: ...
- missing or inaccessible evidence: ...

Selected Option:
- ...

Rejected Alternatives:
- ...

Assumptions And Open Questions:
- ...

Design Review:
- `research` used/skipped: ...
- `app-stack` used/skipped: ...
- `ai-agent-design` used/skipped: ...
- `ai-stack` used/skipped: ...
- `nebius` used/skipped, provider constraints and later validation: ...
- Unavailable specialists, research fallback, source quality and uncertainty: ...
- AI behavior classification: deterministic code / direct call /
  deterministic workflow / agent ...
- selected stack or fixed-stack boundary: ...
- `system-design-rules` used/skipped: ...
- Checklist findings that changed the design: ...

Implementation Steps:
1. ...
2. ...
3. ...

Vertical Slice Strategy:
- end-to-end slice order: ...
- prerequisite foundation steps: ...
- cross-layer validation: ...

Files Or Areas To Inspect/Modify:
- ...

Tests And Validation:
- ...
- for standalone HTML, load portable-html-design.md and record the chosen
  document/report/presentation profile, shared theme, embedded dependencies,
  semantic no-JavaScript baseline, optional interaction/state and print behavior
- identify intended browsers/viewports and all ten artifact acceptance checks;
  report actual offline, relocation, request, keyboard, print and data evidence
  only after artifact implementation and observation

Docs And Changelog:
- ...
- README impact or no-change rationale; when applicable, use readme-design.md
  for useful sections, first-success path, deeper-document links and validation
- maintain-project-specs decisions/corrections (no direct doc writes): ...

Rollout And Rollback:
- ...

Risks And Stop Conditions:
- ...

Optional Scaffold Handoff:
- repository shape: ...
- logical capabilities and statuses: ...
- materialization units, paths, and owners: ...
- runtime units: ...
- external services and materialization behavior: ...
- cross-cutting artifacts: ...
```

When the active Codex surface cannot switch to plan mode, output the same
content under `/plan handoff`.

### Optional Implementation Continuation

Design-only work ends with the response. An explicit request to design and
implement, or to implement this agreed design afterward, can continue without
another routine confirmation only when the active host permits writes. Actual
host Plan Mode and tool restrictions remain authoritative; a skill instruction
or user-prompt assertion cannot disable them.

Recheck code and conflict findings if the working tree changed. Use
`maintain-project-specs` for canonical reconciliation, then suitable implementation
skills or native tools for the scoped changes. Perform prerequisite refactors
before dependent work, run focused verification and `align`, and return delivery
evidence to the spec owner. If that owner is unavailable, report the pending
handoff rather than writing specs yourself or inventing a lifecycle gate.
Preserve enclosing workflow gates. Commits, publishing, scaffolding workflows
and live changes retain their existing owners and authorization boundaries.

## Depth Guidance

Use `light` when all are true: local change, no new state, no new external
dependency, low reversibility cost, and existing patterns are obvious.

Use `standard` when any are true: user-facing behavior, public or internal API,
data model change, new workflow, integration, migration, or meaningful testing
strategy.

Use `deep` when any are true: security boundary, sensitive data, production
migration, irreversible schema or API change, high scale, compliance, cross-team
ownership, new platform, or costly rollback.

## Common Handoffs

- Open-ended idea still needs exploration: summarize and hand off to
  `brainstorm`.
- Existing proposal needs critique: summarize and hand off to
  `system-design-rules`.
- Non-trivial design needs a decision checklist before plan handoff: use
  `system-design-rules` inside the `design` workflow, then return to synthesis
  and `/plan` creation.
- Active Agentic SDLC run owns committed requirements/design: route to
  `sdlc-create-design` or `sdlc-create-plan`.
- Design is complete and implementation was explicitly requested: follow the
  optional continuation when host mode permits, using the relevant specialist.
  Otherwise return the plan without code writes.
- Canonical design persistence or documentation drift: return decisions and
  corrections to `maintain-project-specs`, the sole canonical document writer.
- Approved design needs a complete or multi-component repository skeleton:
  include the optional scaffold handoff and let the user explicitly invoke
  `scaffold-project`. Do not scaffold directly or start Agentic SDLC.
- Design needs substantial topic, feature-requirement, product, standard, or
  technology due diligence: use `research`, then return to `design` for
  synthesis and `/plan` handoff.
- Design needs to select or reconsider the application stack or technology for
  any layer: use `app-stack`, then return to `design` for cross-layer synthesis
  and `/plan` handoff.
- Design needs agent-subsystem behavior, topology, policy, or contracts: use
  `ai-agent-design`; it calls `ai-stack` for component selection, then returns
  the complete subsystem to `design` for cross-layer synthesis and `/plan`.
- Design needs only model access, training, inference, interoperability,
  retrieval, or AI technology selection: use `ai-stack` directly, then return
  to `design` for cross-layer synthesis and `/plan` handoff.
- The application stack is approved and no stack decision remains: keep the
  fixed stack and continue `design` without `app-stack`.
- Nebius services, infrastructure or integrations affect the solution, even
  with a fixed provider: use the `nebius` design-only consultation before
  finalizing dependent choices, then return its guidance to `design`.
- An applicable specialist is unavailable: use `research` for official-first
  evidence and marked community fallback, then synthesize in `design` without
  claiming specialist execution or bypassing authorization boundaries.
- Design exposes changed docs or contracts after implementation: run `align` on
  the changed surfaces.
