---
name: merge-pr
description: "Use only when explicitly asked to merge a ready GitHub PR outside Agentic SDLC after verifying checks, reviews, mergeability, branch state, and head SHA; never use admin bypass."
---

# Merge PR

## Help

For `$merge-pr --help` or `$merge-pr -h`, return concise help and stop before
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

Merge a GitHub pull request only after explicit merge intent and final
readiness verification.

## Use This Skill For

- Merging a specific GitHub PR by number, URL, or current branch.
- Completing a release-prep flow after `create-pr` opens or reuses a PR.
- Verifying checks, review state, mergeability, base branch, and head SHA
  immediately before merge.
- Merging with a selected non-admin method: `squash`, `merge`, or `rebase`, or
  the no-strategy path required by GitHub merge queues.

## Inputs Accepted

- PR number, PR URL, or current branch PR.
- Optional `--merge-method squash|merge|rebase`; default `squash` when the base
  branch does not require a merge queue.
- Optional `--delete-branch`; default is to keep the branch unless the user or
  calling skill explicitly asks to delete it.

## Workflow

1. Confirm the user or calling skill explicitly requested the merge.
2. Resolve the PR with `gh pr view` and collect:
   - number, URL, title, base branch, head branch, head repository
   - `isDraft`, `mergeable`, `mergeStateStatus`, `reviewDecision`
   - `headRefOid`, `statusCheckRollup`
3. Stop before merging when the PR is draft, closed, conflicted, has failing
   required checks, has unresolved required review state, or GitHub reports it
   cannot merge.
4. Run `gh pr checks <pr> --watch --fail-fast` when checks are still pending.
   If checks finish failing, report the failing checks and do not merge.
5. Refresh PR metadata after checks finish and use the latest `headRefOid` as
   the merge guard.
6. If the base branch requires a merge queue, do not pass a merge strategy.
   After checks/reviews are ready, run
   `gh pr merge <pr> --match-head-commit <sha>` so GitHub adds the PR to the
   queue without admin bypass.
7. Otherwise, merge with one explicit method and
   `--match-head-commit <headRefOid>`:
   - `squash`: `gh pr merge <pr> --squash --match-head-commit <sha>`
   - `merge`: `gh pr merge <pr> --merge --match-head-commit <sha>`
   - `rebase`: `gh pr merge <pr> --rebase --match-head-commit <sha>`
8. Never pass `--admin`. Never force merge or bypass branch protection.
9. Verify the post-merge or queued state with
   `gh pr view <pr> --json state,mergedAt,mergeStateStatus`.

## Guardrails

- This skill is separate from `sdlc-merge-pr`; do not write Agentic SDLC run
  state or SDLC authorization files.
- Do not merge without explicit merge intent from the user or a calling skill
  that already has explicit publish/complete authorization.
- Do not use `--auto` as the default. For merge queues, prefer the no-strategy
  `gh pr merge <pr> --match-head-commit <sha>` path after checks are ready.
  Report a blocker only when GitHub still requires delayed auto-merge or human
  approval that the user did not explicitly authorize.
- Do not merge with failing, cancelled, timed-out, missing-required, or
  unknown required checks.
- Do not dismiss or ignore unresolved requested changes.
- Do not delete the branch unless `--delete-branch` was explicitly requested.
- Do not modify files, create commits, rebase branches, or push branch updates;
  use `review-pr` or `create-pr` for those jobs.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return:

- PR number and URL.
- Merge method used.
- Head SHA guarded by `--match-head-commit`.
- Checks/review/mergeability status verified.
- Merge, queue, or exact blocker result.
- Whether branch deletion was requested and performed.
