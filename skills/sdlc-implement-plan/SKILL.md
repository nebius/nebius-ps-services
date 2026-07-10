---
name: sdlc-implement-plan
description: "Use only as part of the Agentic SDLC workflow; use after `sdlc-tdd` when a locked Agentic SDLC feature plan needs production implementation. Implements only the current feature plan, including its vertical end-to-end slice when present, and stays inside its boundaries."
---

# Implement Plan

## Purpose

Implement production code for one feature while staying inside the locked plan.

## When To Use

- A feature has a locked plan and tests are red, ready, or already green.
- The SDLC loop reaches implementation for the current feature.
- A classified implementation defect needs repair within the same plan.

## When Not To Use

- Do not use to edit requirements, design, or locked plans.
- Do not use to broaden scope.
- Do not use to remove tests to pass.

## Inputs

- Locked plan.
- Red or ready tests.
- Feature design.
- Context pack.
- Existing codebase.

## Required Reads

- Locked plan.
- Feature design and context pack.
- Relevant source files, tests, and project conventions.

## Writes

- Production code.
- Required configuration changes from the plan.
- Test fixture updates only when required.
- Local implementation evidence.

## Process

- Re-read the locked plan and inspect current code before editing.
- Make the smallest coherent implementation. When the locked plan defines a
  vertical end-to-end slice, implement that slice through the planned layers
  without widening feature scope.
- Preserve public behavior unrelated to the feature.
- Keep style consistent with nearby code.
- Run focused tests when useful and record changed files plus rationale.

## Idempotency

- Reruns converge instead of duplicating code.
- If implementation already exists, verify it against the plan instead of rewriting.
- If source drift invalidates the plan, or if the vertical slice cannot be
  implemented inside the locked boundaries, stop and route to
  `sdlc-create-plan` or `sdlc-create-design` instead of broadening scope.

## Failure Handling

- If tests cannot pass due to code, fix implementation.
- If tests are wrong, route to `sdlc-tdd`.
- If design is wrong, route to `sdlc-create-design`.
- If environment is blocked, mark `ENVIRONMENT_DEFECT`.

## Must Not

- Change unrelated features.
- Modify locked plans.
- Hide failures.
- Remove tests to pass.
- Expand scope without design update.

## Completion Criteria

- Planned code changes are implemented.
- Planned vertical slice is implemented or a plan/design defect is recorded.
- Focused tests are runnable or blocker is recorded.
- Changed files match implementation boundaries.
- State moves to `implemented`.

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
