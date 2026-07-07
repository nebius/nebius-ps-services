---
name: sdlc-start
description: "Use only as part of the Agentic SDLC workflow; use when the user asks to start, resume, continue, or run the Agentic SDLC workflow for a project using `docs/requirements.md`, `docs/design.md`, private steering input, and local run state. This is the main SDLC coordinator and state-machine skill."
---

# Start SDLC

## Purpose

Coordinate the SDLC loop by reading specs, checkpoints, steering input, and
local state, selecting the next feature, refreshing auto-steering when needed,
and choosing exactly one next skill.

## When To Use

- The user asks to start, resume, continue, or run the Agentic SDLC workflow.
- A Stop-hook continuation prompt says to continue an active SDLC run.
- An active run needs the next phase selected after a skill completes.

## When Not To Use

- Do not use to implement code directly.
- Do not use to free-edit requirements or design.
- Do not use to commit, push, create PRs, review PRs, or merge directly.

## Inputs

- `docs/requirements.md`.
- `docs/design.md` when present.
- Existing local run state when present.
- User instruction or continuation prompt.
- Active `STEERING.md` and `steering/auto-steering.json` when present.
- Optional user-provided live experiment environment information for
  requirements capture.

## Required Reads

- `docs/requirements.md`.
- `docs/design.md`.
- `~/.codex/sdlc-runs/<project-id>/active-run.json` when present.
- Active run state under `~/.codex/sdlc-runs/<project-id>/<run-id>/`.
- `current-state.json`, `feature-queue.json`, `fingerprints.json`,
  `checkpoints/latest.json`, and the latest checkpoint file.
- `STEERING.md`.
- `steering/auto-steering.json` when present.
- Latest feature evidence and failure logs.
- `references/state-schema.md` for state fields and transitions.
- `assets/hooks/README.md` when maintaining optional SDLC PreToolUse or Stop
  hooks.

## Writes

- `active-run.json` only when starting or switching the active run.
- `run.json`, `current-state.json`, `feature-queue.json`, and
  `fingerprints.json`.
- `checkpoints/checkpoint-*.json` and `checkpoints/latest.json`.
- `history/iteration-*.md` for state transitions.
- Local blocker summaries.

## Process

- Read `references/state-schema.md` before initializing or repairing local state.
- At the beginning of a new run, or when `docs/requirements.md` is missing or
  lacks a Live Experiment Environment section, encourage the user to provide a
  non-production or disposable environment with safe connection steps, allowed
  actions, reset instructions, and evidence limits. Do not block the workflow
  only because the section is not provided.
- If the user provides live experiment environment details, route that update to
  `sdlc-create-requirements`; `sdlc-start` must not write `docs/requirements.md`
  directly or store raw environment details in local run state.
- Resolve the project ID from the repository identity and read
  `active-run.json` before choosing a run.
- Acquire or honor the active run lock. If the lock appears stale, report the
  lock owner and timestamp and recover only when the local evidence clearly
  shows no active writer.
- Resume from `checkpoints/latest.json` first. Do not rely on conversation
  memory for current feature, phase, blocker, retry counts, or next skill.
- Reconcile `current-state.json`, the latest checkpoint, feature queue,
  fingerprints, and evidence. If they disagree, prefer the newest complete
  checkpoint that matches existing evidence, then repair `current-state.json`.
- Initialize local run state only when no active run or checkpoint exists.
- If requirements are missing, route to `sdlc-create-requirements`.
- If design is missing or stale and required context is missing, route only to
  `sdlc-gather-context`.
- If design is missing or stale and required context is present, route only to
  `sdlc-create-design`.
- Build or refresh the feature queue from `docs/design.md`.
- Check `STEERING.md` and `steering/auto-steering.json` before every
  iteration.
- If there are new or unresolved steering entries, stale steering fingerprints,
  conflicting reminders, or changed requirements, design, context, plan, or
  evidence reminders, route to `sdlc-auto-steering` before selecting the next
  implementation phase.
- Honor `sdlc-auto-steering` dispositions: route `requirements-change` to
  `sdlc-create-requirements`, `design-change` to `sdlc-create-design`,
  `docs-update` to `sdlc-update-documents`, and `needs-human` to a blocked
  human-input state.
- Select the highest-priority incomplete feature whose dependencies are complete.
- Set one `next_recommended_skill` based on the current phase.
- After evaluation passes, route to `sdlc-update-documents` before
  `sdlc-align-specs` when feature-facing docs, changelog, examples, or
  documentation steering need updates.
- After UAT, route back to `sdlc-update-documents` before PR creation when UAT
  or final steering requires run-level documentation updates.
- Before returning a next skill, write a checkpoint containing current feature,
  phase, status, blocker, retry counts, last successful phase, fingerprints,
  evidence pointers, and next recommended skill.
- Update `checkpoints/latest.json`, then `current-state.json`, after the
  checkpoint is written.
- Write a history entry only when state changes. A repeated resume with no
  state change returns the existing checkpoint and does not duplicate history.
- Stop when complete, blocked, retry budget is exceeded, or human input is
  required.

## Idempotency

- Every invocation starts by reading the latest checkpoint and current state.
- Repeating the same invocation with unchanged specs, fingerprints, evidence,
  and steering returns the same current feature and `next_recommended_skill`.
- Repeating with unchanged auto-steering state must not re-route to
  `sdlc-auto-steering` or duplicate steering history.
- Completed features with unchanged fingerprints are skipped.
- Changed requirements or design invalidate only affected features and
  supersede stale plans; locked plan files are never edited in place.
- History entries, checkpoints, and blocker summaries are append-only except
  for `checkpoints/latest.json` and `current-state.json` pointers.
- Partial writes are repaired by selecting the newest complete checkpoint and
  regenerating derived pointers.

## Failure Handling

- If state is corrupt, reconstruct from the newest complete checkpoint, specs,
  commits, and evidence when possible.
- If plan lock conflicts exist, stop and report.
- If a feature loops repeatedly, classify the blocker and stop after retry budget.
- If no checkpoint can be trusted, stop with `HUMAN_INPUT_REQUIRED` instead of
  guessing the phase.

## Must Not

- Free-edit requirements or design.
- Implement code directly.
- Commit, push, create PRs, review PRs, or merge.
- Bypass validation, tests, or evaluation.
- Record live environment credentials, private endpoints, customer data, or raw
  logs in run state.
- Create a workflow CLI.

## Completion Criteria

- Active run state is accurate and backed by a checkpoint.
- Current feature and next skill are explicit.
- Steering has been consumed, refreshed, or routed to `sdlc-auto-steering`.
- Each state transition writes a checkpoint and history entry.
- Repeated resumes without state changes do not duplicate history.
- The loop can resume after context loss.

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
- Keep optional SDLC hook source under `assets/hooks/`. Patch that source first,
  validate it with its local tests, then intentionally sync it to `$CODEX_HOME/hooks`
  with `install-skills.sh --install-all-hooks`, or with
  `install-skills.sh --install-hooks sdlc-start/assets/hooks` for an SDLC-only
  hook sync. Add `--register-hooks` only when the operator explicitly wants the
  installer to merge the SDLC `PreToolUse` and `Stop` registrations into
  `$CODEX_HOME/hooks.json`.

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

- Read `references/state-schema.md` when this skill needs that local policy or schema.
- Read `assets/hooks/README.md` when maintaining or installing optional SDLC hooks.
