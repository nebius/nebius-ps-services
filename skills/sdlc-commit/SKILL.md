---
name: sdlc-commit
description: "Use only as part of the Agentic SDLC workflow; use after a single Agentic SDLC feature has passed validation, tests, and evaluation to create a local feature-scoped Git commit. This skill does not push and does not replace the general `commit` or `commit-push` skills."
---

# SDLC Commit

## Purpose

Create a local commit for one completed feature as a durable checkpoint without pushing.

## When To Use

- A feature passed validation, tests, and evaluation.
- The SDLC loop needs a local commit checkpoint before UAT or PR creation.
- A follow-up feature fix has fresh evidence and needs its own local commit.

## When Not To Use

- Do not use for ordinary local commits outside Agentic SDLC; use `commit`.
- Do not use to push; use `commit-push` only when explicitly requested outside this SDLC local-commit path.
- Do not use to create or merge PRs.
- Do not use before evidence passes.

## Inputs

- Current feature ID.
- Validation, test, and evaluation evidence.
- Changed files.
- Current branch.

## Required Reads

- Git status and current branch.
- Feature design and requirement IDs.
- Evidence files.
- PreToolUse policy expectations.

## Writes

- Local Git commit.
- `evidence/FEAT-*/commit.md`.
- `permissions/commit-authorization.json`, immediately before the commit, with
  a short expiry and branch scope.
- State transition to `committed`.

## Process

- Use `assets/templates/commit.md.template` for commit evidence.
- Verify current branch is not the default branch.
- Verify validation, tests, and evaluation passed.
- Verify changed files match feature scope.
- Verify private state under `~/.codex/sdlc-runs/` is not staged.
- Stage appropriate project files only.
- Write `permissions/commit-authorization.json` in the active run directory
  only after evidence passes. Include `allowed: true`, feature ID, requirement
  IDs, branch, expiry, and validation/test/evaluation pass status.
- Create a concise commit message referencing `FEAT-*` and `REQ-*`.
- Remove or expire `permissions/commit-authorization.json` after the commit
  attempt.
- Record commit hash locally.

## Idempotency

- If the feature already has a matching commit and no new changes, do not create another commit.
- If additional fixes exist, create a follow-up commit only after evidence reruns.
- Do not amend previous commits by default.

## Failure Handling

- Dirty unrelated files map to a blocker.
- Default branch maps to `POLICY_BLOCK`.
- Missing evidence maps to workflow blocker.
- Hook denial maps to `POLICY_BLOCK`.
- Missing or expired `commit-authorization.json` maps to `POLICY_BLOCK`.

## Must Not

- Push.
- Create PRs.
- Merge.
- Commit local run state.
- Commit secrets.
- Commit from default branch.
- Commit incomplete features.

## Completion Criteria

- Local commit exists or no-op is justified.
- Commit message references feature and requirement IDs.
- Commit evidence records commit hash.
- Feature state is `committed`.

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

- Use `assets/templates/commit.md.template` when creating the corresponding artifact.
