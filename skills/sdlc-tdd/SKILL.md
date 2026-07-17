---
name: sdlc-tdd
description: "Use only as part of the Agentic SDLC workflow; use after `sdlc-prepare-execution` in the registered feature integration worktree when an Agentic SDLC feature needs tests written before dependency-wave implementation."
---

# SDLC TDD

## Purpose

Define success before implementation by creating tests that prove the current feature behavior.

## When To Use

- A locked feature plan and verified `execution_prepared` state exist, and tests
  must be written in the registered integration worktree before implementation.
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
- Verified integration worktree, branch, base SHA, plan digest, and Git common
  directory from private execution state.

## Required Reads

- Locked plan.
- Feature and requirement blocks.
- Existing tests and test framework configuration.
- The execution-plane reference owned by `sdlc-prepare-execution` and the
  active coordinator record.

## Writes

- Tests, fixtures, or mocks required by the plan.
- Test evidence showing expected red or already-green state.
- Local state transition to `tests_red` or `tests_ready`.

## Process

- Identify acceptance criteria that can be tested.
- Change cwd to the recorded integration worktree and re-observe its Git root,
  common directory, branch, exact recorded HEAD, and cleanliness. Fail with
  `WORKTREE_CONFLICT` on drift; never write tests in the project checkout.
- Choose the smallest useful test level first.
- When the locked plan defines a planned end-to-end slice, map tests to its
  behavior, layer contracts, and cross-layer validation target. Prefer the
  smallest useful
  unit, contract, component, or integration coverage that would fail if one
  planned layer or boundary is missing.
- Write tests that fail for missing behavior when implementation is absent.
- Avoid over-mocking real behavior.
- Run focused tests and record evidence.
- Leave the integration changes uncommitted. `sdlc-implement-plan` seals the
  TDD base exactly once before it creates worker branches.

## Idempotency

- Do not duplicate tests.
- Reuse existing tests that already match criteria.
- If implementation already exists and tests pass, record `tests_already_green`.

## Failure Handling

- If test framework is missing, route to project scaffolding or design update.
- If criteria are not testable, route to `sdlc-create-requirements`.
- If design lacks test seams, route to `sdlc-create-design`.
- If the planned slice cannot be tested from the available seams, route to
  `sdlc-create-plan` or `sdlc-create-design` instead of replacing it with
  layer-isolated tests only.

## Must Not

- Implement production code except minimal test-only fixtures.
- Delete or weaken tests to pass.
- Skip test execution unless environment is blocked.

## Completion Criteria

- Tests exist and map to acceptance criteria.
- Planned end-to-end slice coverage is present or a plan/design blocker is
  classified.
- Test run evidence exists.
- Expected red or already-green state is recorded.
- Project checkout remains clean and unchanged at the prepared base.

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
