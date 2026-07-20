---
name: app-stack
description: "Use for selecting, reviewing, simplifying, modernizing, or implementing an application technology stack when the stack is undecided or under review; do not use for isolated implementation when the stack is already fixed. Classify the application and quality attributes, choose the smallest justified architecture, mark optional components, verify volatile choices with official docs, and coordinate matching specialist skills when implementation is requested."
---

# App Stack

## Purpose

Select the smallest application stack that satisfies the actual product,
engineering, and operational requirements. Treat technologies as consequences
of constraints, not as a checklist.

When implementation is requested, own the cross-stack sequence and coordinate
the narrow installed skills that match the selected technologies. Do not
duplicate specialist workflows inside this skill.

## Use This Skill For

- Select a stack for a greenfield application.
- Review whether an existing stack is appropriate or over-engineered.
- Compare application, UI, data, task, workflow, streaming, deployment, and
  observability options.
- Decide whether a component is required now, conditional, or safely deferred.
- Turn an approved stack into an implementation sequence and coordinate the
  matching specialist skills.

## Non-Goals

- Do not force one language, framework, cloud, or deployment model onto every
  application.
- Do not replace `brainstorm` for open-ended ideation, `research` for deep
  technology due diligence, or `design` for a complete solution design.
- When `design` invokes this skill for a scoped stack decision, return that
  decision to the active design workflow instead of routing back to `design`.
- Do not take over isolated framework work when the user has already fixed the
  stack and no stack decision remains.
- Do not add distributed systems, caches, queues, schedulers, workflow engines,
  event streams, or orchestration platforms without a concrete requirement.
- Do not mutate source, infrastructure, databases, credentials, or external
  services when the request is advisory only.

## Inputs

- Product goal, users, primary workflows, and acceptance criteria.
- Existing repository, design, constraints, deployment environment, and team
  capabilities when available.
- Required platforms, integrations, data classes, security or compliance
  boundaries, scale, latency, availability, budget, and operational maturity.
- Requested mode: recommendation, review, modernization, bootstrap, or full
  implementation coordination.

## Required Reads

- Read `references/selection-framework.md` for every non-trivial stack
  decision.
- Read `references/technology-profiles.md` when choosing concrete products or
  evaluating the opinionated Python web profile.
- Read `references/implementation-routing.md` before making changes or
  coordinating specialist skills.
- Inspect the current repository, its instructions, docs, manifests, tests,
  deployment files, and existing patterns before recommending brownfield
  changes.
- Verify version-sensitive product behavior against current official vendor
  documentation. Use `research` for unfamiliar, disputed, high-cost, or
  recommendation-changing choices.

## Workflow

### 1. Establish Mode And Scope

Determine whether the user wants advice, a review, a decision record, a
bootstrap, or implementation. Advisory requests remain read-only. An implicit
skill trigger is not authorization to edit or deploy.

For brownfield work, identify the current stack, its owners, locked constraints,
and the cost of change. For greenfield work, state that no current stack limits
the decision.

### 2. Classify The Application

Classify the primary application archetype and user journey before selecting
products. A system may have secondary archetypes, but keep one dominant shape
unless evidence requires multiple deployable applications.

Capture only inputs that can change the decision: client platforms, rendering
and offline needs, data ownership, transaction boundaries, workload shape,
integrations, quality attributes, delivery constraints, team skills, and
operational capacity.

### 3. Select Architecture Before Products

Choose the simplest viable boundaries first:

- monolith or modular monolith before independently deployed services;
- server-driven UI, SPA, full-stack web framework, native client, or data UI;
- synchronous request, asynchronous task, schedule, durable workflow, or event;
- authoritative data, derived data, cache, search, and analytical stores;
- managed service or self-operation based on ownership and constraints.

Start with the baseline of using the current stack or adding no new component.
Require a named problem, expected benefit, operational owner, and revisit
condition for every added technology.

### 4. Choose And Verify Technologies

Evaluate candidates against the selection framework. Prefer technologies the
team can securely build, test, deploy, recover, upgrade, and operate.

For each category, assign exactly one status:

- `Required`: needed to satisfy a current requirement.
- `Conditional`: add only when the named trigger occurs.
- `Deferred`: plausible later, but unjustified now.
- `Rejected`: evaluated and unsuitable under current constraints.

Do not present marketing performance claims, compatibility claims, lifecycle
status, commands, or current framework recommendations without official
evidence. Mark unverified facts instead of guessing.

### 5. Stress-Test The Decision

Apply the relevant `system-design-rules` categories to data ownership,
reliability, partial failure, security, privacy, scale, cost, observability,
recovery, deployment, and team ownership. Use `design` when the request needs a
complete cross-layer design before implementation and no active `design`
workflow already owns it.

Explicitly test whether each stateful or distributed component can be removed.
Prefer a reversible path and record the condition that would reopen the
decision.

### 6. Return The Stack Decision

Return:

- context, constraints, and assumptions;
- application archetype and architecture summary;
- a table with category, selected technology, status, rationale, and revisit
  trigger;
- data and control flow;
- rejected alternatives and why;
- reliability, security, observability, recovery, and ownership requirements;
- validation needed before adoption;
- an implementation sequence when requested.

Avoid a flat shopping list. Make dependencies and optionality visible.

### 7. Coordinate Implementation When Requested

Read `references/implementation-routing.md`. Discover matching installed skills
from their current metadata and use the narrowest applicable set. Keep one
owner for each file, layer, migration, deployment action, and validation gate.

Implement in vertical slices when the application layers form a serial user
flow. Use foundation-first work only for prerequisites such as repository
scaffolding, contracts, authentication, migrations, shared test harnesses, or
deployment safety.

After changes, align implementation, tests, configuration, documentation,
examples, and changelog entries. Run the narrowest relevant checks before
broadening.

## Guardrails

- Preserve unrelated user changes and existing repository constraints.
- Do not introduce a compatibility shim or parallel legacy path unless the
  user explicitly requests one.
- Treat retries, duplicate delivery, timeouts, overlap, stale caches, partial
  failure, migrations, restore, and rollback as normal design cases.
- Assign one authoritative scheduler to each scheduled operation.
- Require idempotency for retried requests, tasks, schedules, workflows, and
  event consumers where duplicates are possible.
- Keep secrets out of source, examples, logs, reports, and generated files.
- Do not perform live cloud, Kubernetes, database, CI/CD, credential, or
  external-service mutations without explicit authorization and a confirmed
  safe target.
- Prefer static checks, local tests, dry runs, disposable environments, and
  managed stateful services when their cost, residency, portability, and
  ownership trade-offs fit the requirements.
- Do not claim runtime skill activation unless it was observed in the target
  Codex surface.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

For advice or review, return the decision format from Workflow step 6 and state
what evidence could change the recommendation.

For implementation, also return:

- specialist skills used and ownership boundaries;
- files and contracts changed;
- migrations or operational actions performed or deferred;
- validation results;
- remaining risks and revisit triggers.
