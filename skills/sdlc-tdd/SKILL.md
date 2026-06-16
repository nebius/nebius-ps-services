---
name: sdlc-tdd
description: "Use only as part of the Agentic SDLC workflow; use after `sdlc-create-plan` when an Agentic SDLC feature needs tests written before implementation. Converts acceptance criteria and design success criteria into failing or already-green tests."
---

# SDLC TDD

## Purpose

Define success before implementation by creating tests that prove the current feature behavior.

## When To Use

- A locked feature plan exists and tests must be written before implementation.
- Acceptance criteria need unit, integration, component, contract, or regression coverage.
- Existing tests must be mapped to SDLC acceptance criteria.

## When Not To Use

- Do not use to implement production behavior.
- Do not use to weaken acceptance criteria.
- Do not use when no locked plan exists.

## Inputs

- Locked feature plan.
- Feature design.
- Requirement acceptance criteria.
- Existing test conventions.

## Required Reads

- Locked plan.
- Feature and requirement blocks.
- Existing tests and test framework configuration.

## Writes

- Tests, fixtures, or mocks required by the plan.
- Test evidence showing expected red or already-green state.
- Local state transition to `tests_red` or `tests_ready`.

## Process

- Identify acceptance criteria that can be tested.
- Choose the smallest useful test level first.
- Write tests that fail for missing behavior when implementation is absent.
- Avoid over-mocking real behavior.
- Run focused tests and record evidence.

## Idempotency

- Do not duplicate tests.
- Reuse existing tests that already match criteria.
- If implementation already exists and tests pass, record `tests_already_green`.

## Failure Handling

- If test framework is missing, route to project scaffolding or design update.
- If criteria are not testable, route to `sdlc-create-requirements`.
- If design lacks test seams, route to `sdlc-create-design`.

## Must Not

- Implement production code except minimal test-only fixtures.
- Delete or weaken tests to pass.
- Skip test execution unless environment is blocked.

## Completion Criteria

- Tests exist and map to acceptance criteria.
- Test run evidence exists.
- Expected red or already-green state is recorded.

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

- No skill-local references are required by default; use project files and current official documentation as needed.
