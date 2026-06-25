# Agentic SDLC Verification Checklist

Use this checklist as the durable test plan for `agentic-sdlc-test`.

## Required SDLC Skills

- `sdlc-align-specs`
- `sdlc-classify-failure`
- `sdlc-commit`
- `sdlc-create-design`
- `sdlc-create-plan`
- `sdlc-create-requirements`
- `sdlc-evaluate`
- `sdlc-gather-context`
- `sdlc-gui-test`
- `sdlc-implement-plan`
- `sdlc-merge-pr`
- `sdlc-start`
- `sdlc-tdd`
- `sdlc-tui-test`
- `sdlc-uat-tests`
- `sdlc-unit-tests`
- `sdlc-validate-codes`

## Report Sections

The report at `~/.codex/sdlc-verification/report.md` must include:

- Summary
- Environment checked
- Skill discovery results
- Hook configuration results
- PreToolUse safety test results
- Stop continuation test results
- Disposable SDLC golden-path run results
- Idempotency results
- Failure-loop results
- Steering behavior results
- Gaps found
- Recommended fixes
- Final readiness status: PASS, PARTIAL, or FAIL

## Design Contract

Verify `docs/agentic-sdlc-design.md` includes:

- core workflow terms: no workflow CLI, `sdlc-start`, PreToolUse, Stop, private
  local run state, and resume/idempotency
- `Workflow Verification`
- `Quick preflight test`
- `Full workflow test`
- `$agentic-sdlc-test`
- `$sdlc-start`
- `allow_implicit_invocation: false`
- `~/.codex/sdlc-verification/report.md`
- path-agnostic filesystem target handling and ordinary outbound network
  command allowance, with only unsafe content or guarded action checks

## Static Discovery

Verify:

- Global skill folders exist under `~/.agents/skills` for all required
  `sdlc-*` skills.
- Each skill has `SKILL.md`.
- Each `SKILL.md` has valid `name` and `description` front matter.
- Skill names match folder names.
- No duplicate SDLC skill names exist.
- Each SDLC description starts with
  `Use only as part of the Agentic SDLC workflow;`.
- Each required `sdlc-*` skill has `agents/openai.yaml` with
  `policy.allow_implicit_invocation: false`.
- No project-local `.agents/skills` directory is required by the disposable
  project.

## Hook Configuration

Verify read-only:

- `~/.codex/hooks.json` or inline hook configuration exists.
- PreToolUse safety hook is configured.
- Stop continuation hook is configured.
- Existing non-SDLC `SessionStart` and `UserPromptSubmit` hooks are preserved.
- `UserPromptSubmit` does not perform SDLC routing.
- Stop continuation routes through explicit `$sdlc-start` invocation.

Do not install, trust, edit, delete, or rewrite hooks during verification.

## PreToolUse Safety

Allow cases:

- Read-only Git commands: `git status`, `git diff`, `git log`.
- Normal source and test edits inside the disposable repo.
- General filesystem reads, writes, updates, deletes, and moves regardless of
  target path, including outside-repo files, credential directories, Codex
  runtime files, global `AGENTS.md`, locked SDLC plans, and private SDLC state.
- Ordinary outbound network commands such as `curl`, `ssh`, and `scp`.
- Local SDLC state writes in the disposable verification state.
- Read-only MCP-like operations where fixture support exists.
- Project validation commands.

Deny cases:

- Commit without valid `sdlc-commit` authorization.
- Commit on protected branches.
- Staged secrets.
- Push without PR authorization.
- Force push.
- Merge or PR merge without merge authorization.
- Broad destructive shell commands.
- Patches containing obvious secret material.

Authorization handoff:

- Commit, PR, and merge authorization files allow only the matching guarded
  action while valid.
- Expired or removed authorization files must deny again.

## Stop Continuation

Stop cases:

- No active run.
- Complete, paused, blocked, or human-input state.
- Max iteration or retry budget exceeded.
- No-progress guard triggers.
- Merge-ready state without explicit merge request.

Continuation cases:

- Incomplete current feature.
- `next_recommended_skill` points to another phase.
- All features committed but UAT has not passed.
- Critical or pause/no-PR steering is present.
- UAT failed with an addressable classification.

Continuation prompts must say to use `$sdlc-start`, include project root,
project ID, run ID, current feature, current phase, next recommended skill, and
instructions to read local state first, avoid locked-plan edits, and persist
evidence before stopping.

## Golden Path

Use a disposable Python project that validates a Nebius-style resource name:

- lowercase letters, numbers, and hyphens only
- starts with a letter
- 3 to 32 characters
- structured validation errors
- tests and evaluation evidence

Run the SDLC skills in order through local disposable state. Passing evidence
requires committed requirements/design, locked local plan, tests before
implementation, validation evidence, test evidence, evaluation evidence, UAT
evidence, one local feature-scoped commit after evidence passes, and no private
state committed.

## Idempotency And Change Request

Rerun with no product changes and verify no duplicate specs, plans, tests,
commits, or evidence.

Then apply this change request:

```text
Allow underscores when explicitly configured.
```

Verify stable `REQ-*` and `FEAT-*` IDs, preserved old locked plan, a new plan
version only when needed, scoped test/code changes, refreshed evidence, and a
new local feature-scoped commit after evidence passes.

## Failure Loop

Inject controlled validation, test, bad-test, design, spec-gap, and environment
failures one at a time. Each failure must be classified by
`sdlc-classify-failure`, routed to the earliest responsible phase, repaired,
and rerun without blind retry.

## Steering And Continuation

Use this steering instruction:

```text
Pause after the current feature. Do not create a PR.
```

Verify `sdlc-start` reads `STEERING.md`, Stop continuation respects the
instruction, no PR is created, and clearing steering allows resume.

For long-running continuation, verify local state survives context loss,
feature isolation is preserved, and max-iteration/no-progress guards prevent
runaway execution.

## GUI And TUI Smoke

GUI and TUI checks are smoke tests only:

- GUI: detect Browser or Playwright availability and use a harmless local page
  when available. Missing GUI harness is non-blocking.
- TUI: use a harmless toy prompt flow and store transcript evidence locally.

## Final Status Rules

- PASS: all required checks pass; optional GUI/TUI may be NOT APPLICABLE.
- PARTIAL: core flow works but non-critical gaps remain.
- FAIL: safety hook, Stop continuation, state persistence, or golden-path SDLC
  run fails.
