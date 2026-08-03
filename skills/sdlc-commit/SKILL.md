---
name: sdlc-commit
description: "Use only as part of the Agentic SDLC workflow; use after one feature's dependency waves and downstream evidence pass to seal final integration changes, fast-forward the unchanged project branch to the exact integration tip, and non-force-clean the integration resource. It never pushes."
---

# SDLC Commit

## Purpose

Seal and promote one completed feature locally without pushing.

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
- Execution coordinator, integration branch/worktree, base SHA, recorded
  integration SHA, wave results, and cleanup inventory.

## Required Reads

- Git status and current branch.
- Feature design and requirement IDs.
- Evidence files.
- PreToolUse policy expectations.
- The execution-plane reference owned by `sdlc-prepare-execution` and current Git
  worktree/ref/ancestry state.

## Writes

- One final integration commit when validation/docs/alignment left changes.
- Fast-forward-only promotion of the named project feature branch.
- Non-force removal of the verified integration worktree and branch.
- `evidence/FEAT-*/commit.md`.
- `permissions/commit-authorization.json`, immediately before the commit, with
  a short expiry and branch scope.
- State transition to `committed`.

## Process

- Use `assets/templates/commit.md.template` for commit evidence.

1. Verify every wave is done, worker commits and ordered merge commits are
   reachable, no worker cleanup is retained, and validation, tests, evaluation,
   documentation, and spec-alignment evidence all refer to the current
   integration lineage.
2. Verify the integration worktree identity and that its HEAD equals the
   coordinator's recorded tip. Verify private run state is outside the repo and
   no secret is staged.
3. Run the private helper `seal-feature` with final evidence and a concise
   `FEAT-*`/`REQ-*` message. It creates at most one final integration commit and
   records the new exact tip. Do not squash, amend, or rewrite worker/merge
   history.
4. Verify the original project checkout still has the recorded named base
   branch, exact base HEAD, and a clean status. In unmanaged mode, re-resolve
   the symbolic remote default and require its branch and HEAD to equal the
   recorded default identity. In managed-child mode, verify only the exact
   recorded local child identity; do not fetch or consult a remote default.
5. Run the private helper `promote`. A common-Git-directory lock covers the
   exact branch/base precheck, `git merge --ff-only` to the sealed tip, and
   postcheck. It then unlocks and removes only the clean registered integration
   worktree before deleting its ref with
   `git update-ref -d <ref> <exact-promoted-tip>`.
6. Only after `promote` reports `done`, record the promoted SHA and empty
   cleanup result in structured commit evidence. Bind it to the clean project
   checkout HEAD on the recorded base branch and verified absence of the
   integration worktree and branch. Advance any repair revalidation cursor
   with that evidence, then move state to `committed`.

This phase never performs the outer managed-child merge. After final
alignment, UAT, and documentation release the outer lease, `sdlc-start` routes
that child to `$worktree integrate` and records the exact source merge proof.

`permissions/commit-authorization.json` remains the guard for an explicitly
operator-visible raw `git commit`. Normal Agentic SDLC sealing and promotion use
the private transition helper and action-scoped execution state.

## Idempotency

- If the feature is already sealed or promoted at the recorded SHA, verify and
  resume without creating another commit or promotion.
- If new fixes are required after sealing, route back to the responsible phase
  and require a new plan/execution decision; do not amend or widen the sealed tip.
- Do not amend previous commits.

## Failure Handling

- Dirty unrelated files map to a blocker.
- A default branch in unmanaged mode maps to `POLICY_BLOCK`.
- Missing evidence maps to workflow blocker.
- Hook denial maps to `POLICY_BLOCK`.
- Missing or expired `commit-authorization.json` maps to `POLICY_BLOCK`.
- Moved/dirty project or integration state maps to `PROMOTION_BLOCKED`;
  non-fast-forward ancestry maps to `PROMOTION_FAILED`; unsafe retained
  resources map to `CLEANUP_BLOCKED` without force removal.

## Must Not

- Push.
- Create PRs.
- Merge.
- Commit local run state.
- Commit secrets.
- Commit from default branch.
- Commit incomplete features.
- Rebase, squash, amend, cherry-pick, force-delete, or promote a different SHA.

## Completion Criteria

- Final integration commit exists or a clean no-op is justified.
- Commit message references feature and requirement IDs.
- Commit evidence records worker, merge, final, and promoted hashes plus cleanup.
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

- Use `assets/templates/commit.md.template` when creating the corresponding artifact.
