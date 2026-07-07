---
name: sdlc-validate-codes
description: "Use only as part of the Agentic SDLC workflow; use after implementation or before `sdlc-commit` to run build, parse, lint, type, import, dependency, and configuration validation, then use `code-review` as a review-only implementation-quality gate before marking the feature validated."
---

# Validate Codes

## Purpose

Answer whether the implemented feature can be built or run correctly and has
passed a review-only implementation-quality gate before deeper behavior tests.

## When To Use

- Implementation completed and syntax, lint, type, import, config, or build checks must run.
- Mechanical validation passed and changed implementation needs `code-review`
  before the feature can move to behavior tests.
- A classified validation defect needs repair evidence.
- The SDLC loop reaches validation for the current feature.

## When Not To Use

- Do not use as a substitute for behavior tests or evaluation.
- Do not use to fix code; classify blockers and route repairs to the
  responsible SDLC phase.
- Do not treat missing configured tooling as success.
- Do not use `code-review` for GitHub PR readiness; use `review-pr` after PR
  creation.
- Do not let this phase edit implementation for review findings; route repair
  to `sdlc-implement-plan`, `sdlc-tdd`, `sdlc-create-design`, or the
  responsible owner.

## Inputs

- Current feature ID.
- Locked plan.
- Changed files.
- Project tooling.
- Existing tests and design context needed for implementation review.

## Required Reads

- Project config, package manager files, build files, linter and type checker config.
- Changed source files.
- Locked plan, relevant feature design, and test files for the current feature.
- `code-review/SKILL.md` when available, and its quality rubric for meaningful
  implementation review.

## Writes

- `evidence/FEAT-*/validate.md`.
- Code review summary and decision in validation evidence.
- Validation status.
- Failure classification if needed.

## Process

- Use `assets/templates/validate.md.template` for evidence.
- Detect the project stack and reuse configured project commands.
- Run the smallest reliable validation set first.
- Include syntax, linting, type checking when configured, Python import checks when relevant, config validation, and feasible build checks.
- Record exact commands and outcomes.
- After mechanical checks pass, use `code-review` in review-only mode on the
  current feature diff, changed files, nearby tests, locked plan, and design
  context. Do not let `code-review` edit files from this phase.
- If `code-review` cannot be loaded or its review cannot run, classify the
  blocker instead of silently waiving the review gate.
- Record the `code-review` decision, findings, residual risks, and owner-review
  needs in validation evidence.
- Mark validation as passing only when mechanical checks pass and `code-review`
  finds no blocking issue in the reviewed scope.

## Idempotency

- Rerunning validation updates evidence.
- Passing validation with unchanged inputs should remain passing.
- New failures replace stale evidence.

## Failure Handling

- Syntax, lint, type, import, build, or config failure maps to `VALIDATION_DEFECT`.
- Missing tooling maps to `ENVIRONMENT_DEFECT` unless intentionally absent.
- Missing or unavailable `code-review` maps to `ENVIRONMENT_DEFECT` unless
  local SDLC policy explicitly waives the review gate.
- Design flaw discovered by validation routes to `sdlc-create-design`.
- Blocking `code-review` findings route to the earliest responsible phase:
  implementation defects to `sdlc-implement-plan`, missing or wrong tests to
  `sdlc-tdd`, design defects to `sdlc-create-design`, and owner-only decisions
  to human input.

## Must Not

- Change requirements or design.
- Skip configured validation.
- Treat missing tooling as success.
- Mark validation passed when `code-review` requested changes, owner review, or
  more context.
- Use `code-review` to mutate source code during this phase.
- Overwrite test or evaluation evidence.

## Completion Criteria

- Validation evidence exists.
- Required checks pass or blocker is classified.
- `code-review` decision is recorded.
- State moves to `validated` only when mechanical validation passes and
  `code-review` does not request changes or owner review.

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

- Use `assets/templates/validate.md.template` when creating the corresponding artifact.
