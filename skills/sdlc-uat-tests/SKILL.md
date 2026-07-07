---
name: sdlc-uat-tests
description: "Use only as part of the Agentic SDLC workflow; use after all Agentic SDLC feature commits are complete to run product-level user acceptance testing across the whole system, using any confirmed safe live experiment environment, before pull request creation."
---

# UAT Tests

## Purpose

Validate the full product, not just individual features, before PR creation.

## When To Use

- All feature commits are complete.
- Cross-feature user journeys need acceptance evidence.
- The SDLC loop reaches UAT before PR creation.

## When Not To Use

- Do not use for a single feature evaluation; use `sdlc-evaluate`.
- Do not create PRs or merge.
- Do not approve UAT without evidence.

## Inputs

- All requirements.
- All feature designs.
- All feature evidence.
- Current branch.
- Product startup instructions.
- Live Experiment Environment section from `docs/requirements.md`, when present.

## Required Reads

- `docs/requirements.md`.
- Live Experiment Environment section in `docs/requirements.md`.
- `docs/design.md`.
- Feature evidence.
- Current source tree.
- Existing UAT or E2E tests.
- Environment instructions.

## Writes

- `evidence/uat/uat-report.md`.
- GUI screenshots, TUI transcripts, or API/service logs.
- Final pass/fail result.

## Process

- Use `assets/templates/uat-report.md.template` for the report.
- Build a UAT matrix from acceptance criteria.
- Select GUI, TUI, API, service, or mixed harness.
- Use the Live Experiment Environment only when it is marked provided, has
  explicit non-production or disposable confirmation, and the UAT actions fit
  the recorded allowed operations and reset process. If unavailable or unsafe,
  classify the blocker instead of improvising against production.
- Run end-to-end user journeys and negative criteria.
- Validate cross-feature interactions.
- Record evidence and classify failures.

## Idempotency

- Rerunning UAT refreshes the report.
- Use clean or isolated test data.
- Do not duplicate external resources.
- Preserve previous UAT evidence in history when useful.

## Failure Handling

- Product behavior failure maps to `EVALUATION_DEFECT`.
- Cross-feature design issue maps to `DESIGN_DEFECT`.
- Missing acceptance coverage maps to `SPEC_GAP`.
- Environment problem maps to `ENVIRONMENT_DEFECT`.
- Unsafe or unconfirmed live environment use maps to `POLICY_BLOCK`.
- Human-owned access or approval gaps map to `HUMAN_INPUT_REQUIRED`.

## Must Not

- Create PRs.
- Merge.
- Ignore failed negative criteria.
- Modify requirements or design directly.
- Use a production or unconfirmed environment for live experiments.
- Exceed the allowed actions recorded in `docs/requirements.md`.
- Store credentials, private endpoints, customer data, or raw logs in UAT evidence.

## Completion Criteria

- Full UAT report exists.
- All P0 acceptance criteria pass or blockers are classified.
- Cross-feature flows pass.
- Product is ready for `create-pr` only on pass.

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

- Use `assets/templates/uat-report.md.template` when creating the corresponding artifact.
