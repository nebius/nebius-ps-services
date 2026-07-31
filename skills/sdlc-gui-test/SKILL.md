---
name: sdlc-gui-test
description: "Use only as part of the Agentic SDLC workflow; use when browser-based GUI behavior must be controlled, observed, screenshotted, and evaluated against acceptance criteria through the declared harness, including required human-like computer-use UAT or Browser/Playwright when allowed."
---

# GUI Test

## Purpose

Control, observe, and evaluate browser UI behavior with durable local evidence.

## When To Use

- A feature or UAT evaluation requires browser UI interaction.
- Screenshots, accessibility snapshots, console notes, or network notes are needed.
- The design evaluation route says `sdlc-gui-test`.

## When Not To Use

- Do not use for non-browser TUI or CLI flows; use `sdlc-tui-test`.
- Do not use production data unless explicitly allowed.
- Do not mark pass without observable evidence.

## Inputs

- GUI acceptance criteria.
- App URL or startup method.
- Test data or account instructions.
- Feature design.
- Required harness and browser identity when the evaluation plan constrains
  them. `computer-use` is not interchangeable with Browser or Playwright when
  the acceptance contract explicitly requires human-like application control.

## Required Reads

- Evaluation plan.
- Acceptance criteria.
- Existing Playwright config if any.
- UI routes and startup docs.

## Writes

- GUI evaluation report.
- Screenshots or accessibility snapshots.
- Console or network notes when available.
- Pass/fail result.

## Process

- Honor the evaluation plan's required harness. Use `computer-use` when it is
  explicitly required; otherwise use Browser or Playwright MCP when available.
- Start or access the app environment.
- Navigate to the target flow and use accessibility state for interaction when
  available. After every navigation, click, type, submit, refresh, filter,
  restart, or other state-changing action, obtain fresh accessibility state
  before selecting the next target; never reuse stale coordinates or element
  references.
- Perform the user journey.
- Capture screenshots at meaningful checkpoints.
- For data-backed GUI flows, correlate the GUI observation with an independent
  API, database, or service oracle when the acceptance plan requires it.
- Compare result to acceptance criteria and record reproduction steps for failures.

## Idempotency

- Use disposable test data where possible.
- Clean up created records when safe.
- Use unique test names when cleanup is not possible.
- Avoid relying on previous browser state.

## Failure Handling

- Missing app server maps to `ENVIRONMENT_DEFECT`.
- UI mismatch maps to `EVALUATION_DEFECT`.
- Broken selector with correct UI behavior maps to `TEST_DEFECT`.
- Missing acceptance criteria maps to `SPEC_GAP`.

## Must Not

- Use screenshots as the only interaction source when DOM or accessibility snapshots are available.
- Substitute Browser/Playwright evidence for a required `computer-use` harness.
- Mark a data-backed GUI flow PASS from screenshots alone when the acceptance
  plan requires an API, database, or service correlation.
- Store secrets in screenshots or reports.
- Use production data without explicit permission.

## Completion Criteria

- Browser flow was executed.
- The recorded harness and browser match the evaluation plan.
- Evidence exists.
- Acceptance criteria are pass/fail.
- Screenshots or snapshots are stored locally.

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
