# Design Workflow Reference

Use this reference for medium or deep designs, greenfield applications,
multiple unfamiliar technologies, unclear architecture choices, non-trivial
`system-design-rules` decision review, or any design that will become a
committed artifact.

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

For brownfield work, inspect the current system before proposing changes:

- `README.md`, `docs/design.md`, `design.md`, ADRs, runbooks, changelog, and
  repo-specific instructions
- package manifests, lockfiles, build files, deployment configs, schemas, and
  generated artifacts
- source modules, public interfaces, command entrypoints, routes, handlers,
  workers, tests, fixtures, migrations, and observability surfaces
- related features that already solve a similar problem

Map the existing architecture in concise terms: components, responsibilities,
data ownership, call flow, extension points, validation paths, and constraints.

For greenfield work, confirm that there is no relevant existing implementation
after a quick local check. Then design from requirements, research-backed
technology choices, and the user's intended deployment or operating context.

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

### 4. Design Solution

Build the design from behavior and constraints before selecting tools. Define:

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

Brownfield designs should name likely files/modules and how the design fits the
existing architecture. Greenfield designs should name the initial project
shape, runtime, framework, storage, deployment target, and bootstrap order.

### 5. Apply `system-design-rules` And Evaluate Alternatives

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

### 6. Create Implementation Plan

The final design should be ready for Codex `/plan`. The plan content should be
specific enough that implementation can start without re-deciding architecture.

Use this template:

```text
/plan

Objective:
- ...

Design Summary:
- ...

Selected Option:
- ...

Rejected Alternatives:
- ...

Assumptions And Open Questions:
- ...

Design Review:
- `research` used/skipped: ...
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

Docs And Changelog:
- ...

Rollout And Rollback:
- ...

Risks And Stop Conditions:
- ...
```

When the active Codex surface cannot switch to plan mode, output the same
content under `/plan handoff`.

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
- Design is complete and code should change: use `/plan`, then the relevant
  implementation, infrastructure, frontend, testing, or alignment skill.
- Design needs substantial topic, feature-requirement, product, standard, or
  technology due diligence: use `research`, then return to `design` for
  synthesis and `/plan` handoff.
- Design exposes changed docs or contracts after implementation: run `align` on
  the changed surfaces.
