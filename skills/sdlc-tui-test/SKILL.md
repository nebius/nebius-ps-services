---
name: sdlc-tui-test
description: "Use only as part of the Agentic SDLC workflow; use when terminal, CLI wizard, or TUI behavior must be controlled, observed, transcripted, and evaluated against Agentic SDLC acceptance criteria using Pexpect-style interaction."
---

# TUI Test

## Purpose

Control terminal applications like a user and capture prompts, inputs, outputs, exit codes, and side effects.

## When To Use

- A feature or UAT evaluation requires terminal, CLI wizard, or TUI interaction.
- Transcripts and exit-code evidence are required.
- The design evaluation route says `sdlc-tui-test`.

## When Not To Use

- Do not use for browser GUI flows; use `sdlc-gui-test`.
- Do not run destructive commands.
- Do not use real credentials in transcripts.

## Inputs

- TUI or CLI acceptance criteria.
- Startup command from docs or design.
- Test data.
- Expected prompts and outputs.

## Required Reads

- Evaluation plan.
- CLI or TUI docs.
- Existing command definitions.
- Build artifacts or package config.

## Writes

- Terminal transcript.
- Exit-code summary.
- Output assertions.
- Pass/fail result.

## Process

- Launch the CLI or TUI in a controlled environment.
- Wait for expected prompts and send inputs.
- Capture output and exit code.
- Validate generated files or side effects.
- Compare transcript against acceptance criteria.

## Idempotency

- Use temp directories for generated files.
- Reset test environment where possible.
- Use unique names for created resources.
- Avoid persistent external side effects unless explicitly required.

## Failure Handling

- Prompt mismatch maps to `EVALUATION_DEFECT` or `IMPLEMENTATION_DEFECT`.
- Harness timeout maps to `ENVIRONMENT_DEFECT` unless output proves application fault.
- Incorrect expected transcript maps to `TEST_DEFECT`.

## Must Not

- Hide interactive failures.
- Treat a zero exit code as sufficient when criteria require specific behavior.
- Persist credentials in transcripts.

## Completion Criteria

- Transcript exists.
- Exit code is recorded.
- Outputs and side effects are evaluated.
- Acceptance criteria are pass/fail.

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
