---
name: sdlc-create-plan
description: "Use only as part of the Agentic SDLC workflow; use when one ready Agentic SDLC feature from `docs/design.md` needs a locked local execution plan before tests or implementation. Plans are private local artifacts, should preserve vertical end-to-end feature slices when applicable, and must not be committed."
---

# Create Plan

## Purpose

Create a local, locked execution plan for exactly one feature.

## When To Use

- A ready `FEAT-*` needs an execution plan.
- Design or context changed and the old plan must be superseded.
- The SDLC loop reaches the planning phase for the current feature.

## When Not To Use

- Do not use for requirements or design authoring.
- Do not use to write tests or code.
- Do not use to create committed docs.

## Inputs

- One `FEAT-*`.
- Corresponding `REQ-*` blocks.
- Context pack.
- Current repo and feature state.

## Required Reads

- `docs/requirements.md`.
- `docs/design.md`.
- `context/FEAT-*.context.md`.
- Existing source, tests, and local plans for the feature.

## Writes

- `plans/FEAT-*.plan.vN.md`.
- `plans/FEAT-*.plan.vN.lock`.
- Plan fingerprint and state transition to `plan_locked`.

## Process

- Use `assets/templates/feature-plan.md.template` for plan shape.
- Confirm feature status is ready and dependencies are complete or explicitly allowed.
- Identify files to inspect, create, modify, and avoid.
- For serial multi-layer application features, plan one end-to-end slice around
  the feature's behavior and layer map. Group test-first, implementation,
  validation, and evaluation steps by behavior across layers rather than by
  isolated layers. Use foundation-only steps only when they are true blockers.
- Define test-first, implementation, validation, evaluation, rollback, and stop-condition steps.
- Lock the plan.

## Idempotency

- If design and context fingerprints are unchanged, reuse the existing locked plan.
- If design or context changed, create a new plan version.
- Never modify an existing locked plan.

## Failure Handling

- If design is not implementable, route to `sdlc-create-design`.
- If context is missing, route to `sdlc-gather-context`.
- If project tooling is unknown, route to a relevant project skill.

## Must Not

- Commit the plan.
- Edit locked plans.
- Implement code or write tests.
- Expand feature scope.

## Completion Criteria

- Local plan exists and is locked.
- Plan contains test, implementation, validation, and evaluation steps.
- Plan identifies the end-to-end slice or records why no vertical slice applies.
- Plan references exact feature and requirement IDs.

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

- Use `assets/templates/feature-plan.md.template` when creating the corresponding artifact.
