---
name: sdlc-create-design
description: "Use only as part of the Agentic SDLC workflow; use when `docs/design.md` must be created or updated from `docs/requirements.md` and gathered feature context. This Agentic SDLC skill owns design.md and stable FEAT IDs."
---

# Create Design

## Purpose

Convert requirements and gathered context into implementable architecture and feature designs in `docs/design.md`.

## When To Use

- Requirements exist but design is missing.
- Approved requirements or context changed and design must be updated.
- An SDLC failure classification routes to a design defect or spec gap.

## When Not To Use

- Do not use to create local execution plans.
- Do not use to implement code or modify tests.
- Do not use to rewrite requirements.

## Inputs

- `docs/requirements.md`.
- Feature context packs.
- Existing `docs/design.md` when present.
- Current codebase shape.

## Required Reads

- Requirements.
- Context packs.
- Existing design.
- Project structure, tests, and relevant source files.

## Writes

- `docs/design.md`.
- Feature blocks using `FEAT-*`.
- Design decisions, design change log, and local design fingerprint.

## Process

- Use `assets/templates/design.md.template` when creating the file.
- Map `REQ-*` blocks to stable `FEAT-*` blocks.
- Preserve existing feature IDs and append new IDs instead of renumbering.
- Define architecture, component boundaries, data flow, control flow, state, error, security, and observability models.
- Define validation, test, evaluation, TDD success criteria, rollback, and done definition per ready feature.
- Mark features as ready, draft, blocked, or stale and update the change log.

## Idempotency

- Same requirements and context must not duplicate features.
- Requirement changes update affected feature blocks only.
- Existing feature IDs remain stable.

## Failure Handling

- If design cannot satisfy requirements, mark `DESIGN_DEFECT` or `SPEC_GAP`.
- If context is missing, route to `sdlc-gather-context`.
- If requirements are contradictory, route to `sdlc-create-requirements`.

## Must Not

- Create local execution plans.
- Implement code.
- Modify tests.
- Delete feature blocks without explicit requirement removal.
- Ignore acceptance criteria.

## Completion Criteria

- `docs/design.md` exists.
- Every P0 requirement maps to at least one feature.
- Every ready feature has validation, test, evaluation, and implementation boundaries.
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

When using this skill, capture durable, reusable, public-safe learnings back
into this skill's local source materials before completion when the current task
contract allows source edits. Update the narrowest appropriate surface:
`SKILL.md` for runtime rules, `references/` for detailed guidance, `assets/`
for reusable templates, `scripts/` for deterministic helpers, and README or
changelog entries for human-facing or release-note updates.

If the current task is explicitly read-only/report-only, or source writes are
outside this skill's task contract, do not edit skill sources; report the
skipped source update instead.

Do not capture secrets, private URLs, customer data, raw logs, one-off local
state, or unverified/vendor-specific claims. If a useful learning is not safe,
not evidence-backed, or outside this skill's scope, report that it was skipped.

## Output Contract

Return a concise result with:

- Scope handled and current `REQ-*` or `FEAT-*` IDs.
- Files or local state written.
- Evidence created or checked.
- Failure classification and next recommended skill when blocked.
- Confirmation that private SDLC state was kept out of committed project files.

## References

- Use `assets/templates/design.md.template` when creating the corresponding artifact.
