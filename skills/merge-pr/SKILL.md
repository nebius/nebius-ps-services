---
name: merge-pr
description: "Use only when explicitly asked to merge a ready GitHub PR outside Agentic SDLC after verifying checks, reviews, mergeability, branch state, and head SHA; never use admin bypass."
disable-model-invocation: true
---

# Merge PR

## Help

For `$merge-pr --help` or `$merge-pr -h` (including native Claude forms), return concise help and stop before
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

## Agent Compatibility

Use `$merge-pr` in Codex, `/merge-pr` in Claude Code, or
`/skills:merge-pr` in the Claude plugin. Dollar-prefixed skill examples
refer to the same named skill on either host; use the native invocation syntax.
Preserve the declared invocation policy, approvals and workflow ownership.
Use available native tools; an unavailable required capability is a blocker,
never permission to bypass a guard or claim unobserved behavior.

## Purpose

Merge a GitHub pull request only after explicit merge intent and final
readiness verification. Report the merge outcome, remote destination proof, and
result-commit CI separately, or return after proving current queue membership.

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

Public usage: `$merge-pr [<PR-number|PR-URL|current-branch>]`
`[--merge-method squash|merge|rebase] [--delete-branch]`.
`-h, --help` shows help only. No additional public flags.

## Required Reads

For workflow execution, read [completion verification](references/completion-verification.md)
before the merge attempt. It owns the GitHub queries, evidence rules, bounded
polling, CI conclusions, and branch cleanup. Help requests stop before this read.

## Workflow

1. Confirm the user or calling skill explicitly requested the merge.
2. Resolve the PR with `gh pr view` and API reads. Freeze its host, base
   repository, target branch, number, and URL; collect:
   - title, head branch, head repository, `state`, `mergedAt`
   - `isDraft`, `mergeable`, `mergeStateStatus`, `reviewDecision`
   - `headRefOid`, `statusCheckRollup`
   Use this exact host/repository/PR for every subsequent query and command.
3. If already merged, skip mutation and verify that merge. Otherwise stop when
   draft, closed, conflicted, missing required checks/reviews, or unmergeable.
4. Run `gh pr checks <pr-url> --watch --fail-fast` when checks are still pending.
   If checks finish failing, report the failing checks and do not merge.
5. Refresh all readiness evidence after checks finish. Stop if the base target
   changed; if the head changed, repeat readiness checks for that head before
   freezing `headRefOid` as the merge guard. Never reuse stale review evidence.
6. If the base branch requires a merge queue, do not pass a merge strategy.
   After checks/reviews are ready, run
   `gh pr merge <pr-url> --match-head-commit <sha>`. An existing verified queue
   entry needs no repeated enqueue attempt.
7. Otherwise, merge with one explicit method and
   `--match-head-commit <headRefOid>`:
   - `squash`: `gh pr merge <pr-url> --squash --match-head-commit <sha>`
   - `merge`: `gh pr merge <pr-url> --merge --match-head-commit <sha>`
   - `rebase`: `gh pr merge <pr-url> --rebase --match-head-commit <sha>`
8. Never pass `--admin` or attach branch deletion to the initial command.
   After a command error or ambiguous response, read authoritative state before
   reporting; do not blindly repeat a merge or enqueue operation.
9. Follow the reference to distinguish merged, currently queued, removed from
   queue, closed unmerged, and unverified outcomes. Read-retry unsettled evidence
   for at most 60 seconds. An open PR or auto-merge request never proves queued.
   Refresh a queue entry immediately before reporting; return when confirmed.
10. For a merged PR, obtain the method-specific resulting SHA and prove it is
    in the remote target's history. Accept an advanced target; do not require
    tip equality or original PR-head ancestry. Preserve known merge success
    when destination evidence is failed or unverified.
11. Observe applicable CI on that exact result for up to 3600 seconds from the
    first confirmed merged observation in this invocation, polling every 30
    seconds. Settlement and read retries consume this same budget. Stop on a
    conclusive result; CI reruns never reset the deadline. Report CI separately
    as passed, failed, pending, not configured, or unverified; while queued,
    destination and post-merge CI are not applicable.
12. Perform explicitly requested branch cleanup only after actual merge and
    destination verification. While queued, defer it and report that fact.

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
- Do not rerun or dispatch CI, requeue a removed PR, undo a merge, or expand
  credentials/permissions to repair verification. Report the exact limitation.
- Missing checks, empty API responses, or access errors never establish that
  post-merge CI is not configured. Unknown applicability remains unverified.
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

- PR number, URL, host, base repository, and target branch.
- Merge method used, or queue-configured/unknown when not independently known.
- Head SHA guarded by `--match-head-commit`; for an already-merged observation,
  state that no guarded merge command was issued in this invocation.
- Checks/review/mergeability status verified.
- Merge outcome and timestamp, queue-entry evidence and observation time, or
  exact blocker; do not collapse these into one generic success claim.
- Resulting commit SHA; destination verified/failed/unverified/not applicable,
  observed target tip, observation time, and comparison evidence.
- Post-merge CI outcome, exact checked SHA, applicable checks/run links and
  actual conclusions, skipped/neutral disclosures, outstanding checks, and
  observation start/deadline when waiting or timing out.
- Whether branch deletion was requested and performed.

For example, `merged; destination verified; CI failed` is a valid report.
`queued` is not a completed merge for release-publishing callers. Incomplete
verification is not permission to proceed as though every outcome passed.
