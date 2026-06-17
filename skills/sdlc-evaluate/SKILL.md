---
name: sdlc-evaluate
description: "Use only as part of the Agentic SDLC workflow; use after validation and tests in the Agentic SDLC loop to determine whether the current feature solves the real-world requirement using acceptance criteria and the correct evaluation harness."
---

# SDLC Evaluate

## Purpose

Evaluate observed product behavior against real-world acceptance criteria.

## When To Use

- Validation and tests pass and feature acceptance must be observed.
- The product is GUI, TUI, CLI, API, library, service, or infrastructure and needs evidence.
- A prior evaluation defect needs rerun evidence.

## When Not To Use

- Do not treat unit tests as evaluation.
- Do not approve behavior without observation.
- Do not change code directly unless routed back to implementation.

## Inputs

- Feature design.
- Acceptance criteria.
- Validation evidence.
- Test evidence.
- Product type.

## Required Reads

- Requirement block.
- Feature design.
- Locked plan.
- Validation and test evidence.
- App startup instructions.

## Writes

- `evidence/FEAT-*/evaluate.md`.
- Screenshots, transcripts, or API/service summaries as appropriate.
- Evaluation pass/fail result.

## Process

- Use `assets/templates/evaluate.md.template` for evidence.
- Select the evaluation route: `sdlc-gui-test`, `sdlc-tui-test`, API/service checks, or manual review.
- Compare observed behavior against acceptance and negative criteria.
- Record control, observation, and evaluation evidence.

## Idempotency

- Rerunning evaluation refreshes evidence.
- If product behavior is unchanged, outcome should be stable.
- Failed evaluation routes back to the correct phase.

## Failure Handling

- Real behavior mismatch maps to `EVALUATION_DEFECT`.
- Automation failure maps to `ENVIRONMENT_DEFECT` or `EVALUATION_DEFECT` based on cause.
- Acceptance issue maps to `SPEC_GAP`.
- Design mismatch maps to `DESIGN_DEFECT`.

## Must Not

- Ignore negative criteria.
- Mark pass without observable evidence.
- Overwrite validation or test evidence.

## Completion Criteria

- Evaluation evidence exists.
- Acceptance criteria are explicitly pass or fail.
- State moves to `evaluated` only when pass.

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

- Use `assets/templates/evaluate.md.template` when creating the corresponding artifact.
