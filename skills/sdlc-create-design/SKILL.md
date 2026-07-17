---
name: sdlc-create-design
description: "Use only as part of the Agentic SDLC workflow; use when `docs/design.md` must be created or updated from `docs/requirements.md`, gathered context, and codebase evidence. This skill owns design.md, stable FEAT IDs, design decisions, alternatives, vertical end-to-end feature flow, and implementation-ready validation boundaries."
---

# Create Design

## Purpose

Convert requirements and gathered context into evidence-backed architecture
and feature designs in `docs/design.md`.

## When To Use

- Requirements exist but design is missing.
- Approved requirements or context changed and design must be updated.
- An SDLC failure classification routes to a design defect or spec gap.

## When Not To Use

- Do not use to create local execution plans.
- Do not use to implement code or modify tests.
- Do not use to rewrite requirements.
- Do not use for non-SDLC design docs, ADRs, or `/plan` handoffs; use
  `design` for those.

## Inputs

- `docs/requirements.md`.
- Feature context packs.
- Existing `docs/design.md` when present.
- Current codebase shape.
- Technology, vendor, security, operational, and testing constraints.

## Required Reads

- Requirements.
- Context packs.
- Existing design.
- Project README, architecture docs, tests, configs, and relevant source files.
- Active SDLC run state and latest checkpoint when a run exists.

## Writes

- `docs/design.md`.
- Feature blocks using `FEAT-*`.
- Design decisions, design change log, and local design fingerprint.

## Process

- Use `assets/templates/design.md.template` when creating the file.
- Understand requirements first: summarize product behavior, non-goals,
  acceptance criteria, priorities, constraints, and open questions.
- Understand the existing system before designing: inspect current components,
  owners, public interfaces, data flow, tests, deployment/configuration, and
  nearby patterns.
- Use gathered context as the source of technology and vendor truth. If
  version-sensitive, vendor, internal, codebase, or test context is missing or
  unverifiable, route to `sdlc-gather-context` instead of guessing.
- Map `REQ-*` blocks to stable `FEAT-*` blocks. Preserve existing feature IDs
  and append new IDs instead of renumbering.
- Define architecture, component boundaries, APIs or commands, data flow,
  control flow, state, error, security, observability, operations, rollout,
  rollback, and ownership models.
- For serial multi-layer application features, define the vertical end-to-end
  feature flow and layer map, such as UI -> API -> service -> database. Record
  each layer's responsibility, boundary contract, persistence expectations,
  and cross-layer validation path. Use foundation-only design boundaries only
  for true prerequisites such as schema contracts, auth, migrations, shared
  harnesses, or safety preflights.
- For non-trivial or hard-to-reverse decisions, compare the baseline/current
  approach, the selected design, and one simpler or more conservative
  alternative when meaningful. Record the selected option, rejected
  alternatives, trade-offs, reversibility, and revisit trigger.
- Define implementation boundaries, validation, test, evaluation, TDD success
  criteria, rollback, and done definition per ready feature.
- Mark features as ready, draft, blocked, or stale and update the decision log,
  change log, and design fingerprint.

## Idempotency

- Same requirements and context must not duplicate features.
- Requirement changes update affected feature blocks only.
- Existing feature IDs remain stable.
- When execution is already prepared, update design only in the registered
  integration worktree, supersede the old locked plan through coordinator
  routing, and preserve started assignments/commits/resources. Never rewrite
  or reset the active execution history.

## Failure Handling

- If design cannot satisfy requirements, mark `DESIGN_DEFECT` or `SPEC_GAP`.
- If context is missing, route to `sdlc-gather-context`.
- If requirements are contradictory, route to `sdlc-create-requirements`.
- If a feature is not implementable without a risky or irreversible choice,
  keep it draft or blocked and record the decision needed before planning.

## Must Not

- Create local execution plans.
- Implement code.
- Modify tests.
- Delete feature blocks without explicit requirement removal.
- Ignore acceptance criteria.

## Completion Criteria

- `docs/design.md` exists.
- Every P0 requirement maps to at least one feature.
- Every ready feature has selected and rejected options, implementation
  boundaries, vertical flow or layer map when applicable, validation, test,
  evaluation, rollout, rollback, and done criteria.
- Open design questions are explicit.

## SDLC Invariants

- Treat `docs/requirements.md` and `docs/design.md` as committed product truth.
- Only `sdlc-create-requirements` writes `docs/requirements.md`; only `sdlc-create-design`
  writes `docs/design.md`. Other skills route spec changes to those owners.
- Keep run state, plans, evidence, steering, screenshots, and transcripts under `~/.codex/sdlc-runs/<project-id>/<run-id>/`.
- When an active run exists, reload `current-state.json` and the latest
  checkpoint before changing phase or writing evidence.
- Work on one feature at a time unless the user explicitly asks for a different SDLC shape.
- Classify every failure before retrying or routing backward.
- Use MCP servers for browser, GitHub, internal docs, Slack, Confluence, Jira, and other external systems when they are available and appropriate.
- Treat hooks as invariant guardrails only; do not make hooks orchestrate the workflow.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return a concise result with:

- Scope handled and current `REQ-*` or `FEAT-*` IDs.
- Files or local state written.
- Evidence created or checked.
- Failure classification and next recommended skill when blocked.
- Confirmation that private SDLC state was kept out of committed project files.

## References

- Use `assets/templates/design.md.template` when creating the corresponding artifact.
