---
name: sdlc-tui-test
description: "Use only as part of the Agentic SDLC workflow; control, observe, transcribe, and evaluate terminal, CLI-wizard, or TUI behavior against acceptance criteria with Pexpect-style interaction."
---

# TUI Test

## Help

For `$sdlc-tui-test --help` or `$sdlc-tui-test -h`, return concise help and stop before
any workflow step. State the purpose and invocation policy. Show exact usage
for every public action. Describe each public action, positional
argument, and flag in one concise line, including `-h, --help`; say "No
additional public flags" when there are no others. Use only the documented
public interface. For internal or coordinator-only skills, state that boundary
and that no standalone public workflow action exists. After the selected
`SKILL.md` is loaded, help is report-only: do not call any additional tools,
inspect project state, or modify files, private state, Git, or external systems.
Never expose private helper actions or flags or treat help as workflow
authorization.

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
- `maintain-project-specs` is the sole semantic, schema, and validation owner
  of both canonical specs. Inside Agentic SDLC, only its routed
  `sdlc-create-requirements` and `sdlc-create-design` authoring adapters may
  write their respective managed records; all other phase skills route changes
  through those adapters and return validation to the shared owner.
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
