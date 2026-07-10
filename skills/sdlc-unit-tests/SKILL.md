---
name: sdlc-unit-tests
description: "Use only as part of the Agentic SDLC workflow; use after validation in the Agentic SDLC loop to run behavior tests for the current feature and any planned end-to-end slice, including unit, integration, component, contract, regression, and mock-based tests when applicable."
---

# Unit Tests

## Purpose

Answer whether the code behaves correctly against the feature tests and acceptance criteria.

## When To Use

- Validation has passed and feature tests need to run.
- Regression or integration tests are required by design.
- A test failure needs classification and evidence.

## When Not To Use

- Do not use to approve real-world behavior; use `sdlc-evaluate` after tests.
- Do not use to delete or weaken tests.
- Do not use to modify requirements or design.

## Inputs

- Current feature ID.
- Test files created or selected by `sdlc-tdd`.
- Locked plan.
- Project test tooling.

## Required Reads

- Test configuration.
- Test files.
- Feature design.
- Locked plan and its End-To-End Slice when present.
- Acceptance criteria.
- Existing test conventions.

## Writes

- `evidence/FEAT-*/tests.md`.
- Coverage notes if available.
- Failure classification.

## Process

- Use `assets/templates/tests.md.template` for evidence.
- Run focused feature tests first.
- Run nearby regression tests.
- Run integration or component tests required by design.
- When the locked plan defines an end-to-end slice, run or record the tests that
  prove the slice's cross-layer validation target and layer contracts. If the
  available suite cannot prove the slice, classify the gap instead of marking
  the feature tested.
- Run broader suite only when feasible.
- Record commands and outcomes.

## Idempotency

- Rerunning tests updates evidence.
- Do not duplicate tests.
- Do not skip failed tests without classification.

## Failure Handling

- Production behavior failure maps to `IMPLEMENTATION_DEFECT`.
- Incorrect test expectation maps to `TEST_DEFECT`.
- Missing service or dependency maps to `ENVIRONMENT_DEFECT`.
- Design mismatch maps to `DESIGN_DEFECT`.

## Must Not

- Delete or weaken tests to pass.
- Treat flaky tests as pass without evidence.
- Overwrite evaluation results.

## Completion Criteria

- Required tests pass.
- Planned slice coverage passes or the blocker is classified.
- Evidence file exists.
- Failures are classified if not passing.
- State moves to `tested`.

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

- Use `assets/templates/tests.md.template` when creating the corresponding artifact.
