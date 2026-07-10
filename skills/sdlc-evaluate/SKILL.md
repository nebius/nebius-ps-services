---
name: sdlc-evaluate
description: "Use only as part of the Agentic SDLC workflow; use after validation and tests in the Agentic SDLC loop to determine whether the current feature and any planned end-to-end slice solve the real-world requirement using acceptance criteria, the correct evaluation harness, and any confirmed safe live experiment environment."
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
- Live Experiment Environment section from `docs/requirements.md`, when present.

## Required Reads

- Requirement block.
- Live Experiment Environment section in `docs/requirements.md`.
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
- Use the Live Experiment Environment only when it is marked provided, has
  explicit non-production or disposable confirmation, and the intended checks
  fit the recorded allowed actions. Otherwise fall back to local, mocked, dry
  run, or manual evaluation as appropriate.
- Compare observed behavior against acceptance and negative criteria.
- When the locked plan defines an end-to-end slice, observe the feature through
  that slice's user-visible or system-visible flow. Layer-isolated checks alone
  are not enough to mark the feature evaluated unless the plan says no vertical
  slice applies.
- Record control, observation, and evaluation evidence.

## Idempotency

- Rerunning evaluation refreshes evidence.
- If product behavior is unchanged, outcome should be stable.
- Failed evaluation routes back to the correct phase.

## Failure Handling

- Real behavior mismatch maps to `EVALUATION_DEFECT`.
- Missing end-to-end slice observation maps to `EVALUATION_DEFECT` or
  `ENVIRONMENT_DEFECT` based on whether product behavior or access prevented
  the observation.
- Automation failure maps to `ENVIRONMENT_DEFECT` or `EVALUATION_DEFECT` based on cause.
- Missing required live environment access maps to `ENVIRONMENT_DEFECT` or
  `HUMAN_INPUT_REQUIRED` based on whether setup or a human-owned decision is
  missing.
- Unsafe or unconfirmed live environment use maps to `POLICY_BLOCK`.
- Acceptance issue maps to `SPEC_GAP`.
- Design mismatch maps to `DESIGN_DEFECT`.

## Must Not

- Ignore negative criteria.
- Mark pass without observable evidence.
- Overwrite validation or test evidence.
- Use a production or unconfirmed environment for live experiments.
- Exceed the allowed actions recorded in `docs/requirements.md`.
- Store credentials, private endpoints, customer data, or raw logs in evidence.

## Completion Criteria

- Evaluation evidence exists.
- Acceptance criteria are explicitly pass or fail.
- Planned end-to-end slice observation is recorded or a blocker is classified.
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

- Use `assets/templates/evaluate.md.template` when creating the corresponding artifact.
