---
name: sdlc-align-specs
description: "Use only as part of the Agentic SDLC workflow; use when Agentic SDLC requirements, design, plans, tests, implementation, documentation, end-to-end slice evidence, and other evidence must be checked for consistency. This is SDLC-specific and does not replace the general `align` skill."
---

# Align Specs

## Purpose

Verify that SDLC specs, plans, implementation, documentation, tests, and
evidence tell one consistent story.

## When To Use

- Before committing or PR creation, specs and evidence need consistency review.
- A failure suggests drift between requirements, design, tests, implementation,
  documentation, steering, or evidence.
- The user asks for SDLC spec alignment.

## When Not To Use

- Do not use for general project-wide alignment outside SDLC artifacts; use `align`.
- Do not rewrite requirements or design directly.
- Do not implement broad fixes without routing to the responsible skill.

## Inputs

- Requirements, design, current feature plan, changed files, tests,
  documentation, steering, and evidence.
- Current feature ID or full-run scope.

## Required Reads

- `docs/requirements.md`.
- `docs/design.md`.
- Locked plans.
- Validation, test, evaluation, documentation, UAT, and commit evidence.
- Changed implementation and tests.
- Project-facing docs touched by the active feature or run.

## Writes

- Alignment report.
- Failure classification or next recommended skill when drift is found.

## Process

- Map `REQ-*` to `FEAT-*`, plans, tests, implementation, documentation, and
  evidence.
- For features with a vertical flow or layer map, check that `docs/design.md`,
  the locked plan's End-To-End Slice, TDD/tests, implementation boundaries,
  validation evidence, evaluation evidence, documentation updates, and UAT
  evidence all describe the same slice or explicitly record why no slice
  applies.
- Check stable IDs and status fields.
- Check that no manual spec edits bypassed owner skills.
- Check that evidence supports claimed state transitions.
- Check that project-facing docs do not describe behavior that implementation
  and evaluation evidence have not proven.
- Classify drift and route to the responsible SDLC skill.

## Idempotency

- Rerunning alignment updates the report without duplicating findings.
- Unchanged specs and evidence should produce stable conclusions.
- Resolved drift should be marked resolved instead of erased from history.

## Failure Handling

- Missing requirements or design routes to their owner skills.
- Missing evidence routes to the responsible phase.
- Spec contradiction maps to `SPEC_GAP`.
- Plan/design mismatch maps to `DESIGN_DEFECT` or `PLAN_DEFECT`.
- Slice mismatch maps to the earliest owner: design for wrong layer map, plan
  for wrong slice, TDD/tests for missing coverage, implementation for
  out-of-scope files, evaluation for missing observation, or documentation for
  unsupported wording.
- Documentation mismatch maps to `DOCUMENTATION_DRIFT` and routes to
  `sdlc-update-documents`.

## Must Not

- Free-edit requirements or design.
- Modify locked plans.
- Pretend unchecked evidence passed.
- Replace the general `align` workflow.

## Completion Criteria

- Alignment report exists.
- Each drift item has an owner skill or blocker.
- Vertical flow, layer map, locked slice, and evidence agree when applicable.
- No unresolved inconsistency remains before commit or PR readiness is claimed.

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

- No skill-local references are required by default; use project files and current official documentation as needed.
