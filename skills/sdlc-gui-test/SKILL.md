---
name: sdlc-gui-test
description: "Use only as part of the Agentic SDLC workflow; use when browser-based GUI behavior must be controlled, observed, screenshotted, and evaluated against Agentic SDLC acceptance criteria, preferably through Playwright MCP when available."
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

- Use Browser or Playwright MCP when available.
- Start or access the app environment.
- Navigate to the target flow and use accessibility snapshots for interaction when available.
- Perform the user journey.
- Capture screenshots at meaningful checkpoints.
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
- Store secrets in screenshots or reports.
- Use production data without explicit permission.

## Completion Criteria

- Browser flow was executed.
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
