---
name: merge-pr
description: "Use only when explicitly asked to complete an ordinary GitHub PR via local review-pr and a protected GitHub Actions merge; verifies exact head, checks and result. Agentic SDLC is excluded."
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
result-commit CI and configured Pages publication separately.

## Use This Skill For

- Merging a specific GitHub PR by number, URL, or current branch.
- Completing a release-prep flow after `create-pr` opens or reuses a PR.
- Verifying checks, review state, mergeability, base branch, and head SHA
  immediately before merge.
- Merging with a selected protected method: `squash`, `merge`, or `rebase`.
  Merge queues are unsupported by this built-in-token path.

## Inputs Accepted

- PR number, PR URL, or current branch PR.
- Optional `--merge-method squash|merge|rebase`; default `squash`.
  A required merge queue stops for a maintainer.
- Optional `--delete-branch`; default is to keep the branch unless the user or
  calling skill explicitly asks to delete it.

Public usage: `$merge-pr [<PR-number|PR-URL|current-branch>]`
`[--merge-method squash|merge|rebase] [--delete-branch]`.
`-h, --help` shows help only. No additional public flags.

## Required Reads

For workflow execution, read [completion verification](references/completion-verification.md)
before the merge attempt. Also read [Actions protocol](references/actions-protocol.md)
for local review evidence, trusted dispatch and setup prerequisites. It owns the GitHub queries, evidence rules, bounded
polling, CI conclusions, and branch cleanup. Help requests stop before this read.

## Workflow

1. Confirm explicit merge intent or delegated ordinary create-pr/complete
   publication intent. Exclude active Agentic SDLC and propagate prepare-only,
   help and report-only restrictions. A request to review and merge authorizes
   this full sequence without routine reconfirmation. Dependabot, including
   Docker, follows the same review and repair path. Freeze host/repository/default/PR identity.
2. If already merged, skip effects and verify that exact operation's result.
   Otherwise require same-repository, non-draft, live-default target. Invoke
   local review-pr when the current head/base lacks a passing attestation.
   Delegate safe repairs to create-pr's private preparation handoff; do not
   mutate branch history here. Unsafe findings and human objections stop.
3. Wait for applicable current-head CI, synchronize/review again when default
   advances, and freeze the exact head/base and COMMENT review ID. CI alone is
   insufficient. The broker authorizes configured numeric operator IDs only.
4. Follow the Actions protocol to dispatch the trusted default-branch broker.
   A read-only job independently admits the exact review/base/CI and uploads its
   intent before effects. The write job validates that receipt, approves as
   github-actions, rechecks and requests direct protected async merge with
   `bypass_rules: false`. Required queues stop before approval. Unsupported
   workflow-file permissions stop for a maintainer; no App/PAT/bypass fallback.
5. Dispatch/HTTP acceptance is pending, not merged. Reconcile authoritative PR
   state after errors before any retry. Wait at most 3600 seconds with 30-second
   polls; retain the caller grant on timeout and return the exact resume identity.
   Externally queued PRs may be observed read-only, never enqueued by this skill.
6. After actual merge, preserve known success even if later verification fails.
   Use the trusted completion helper to verify exact-result CI artifacts and
   configured Pages publication. The completion workflow owns explicit dispatch
   and Pages effects; local observation is read-only. Observe for at most 3600
   seconds from first confirmed merge. Do not substitute the workflow revision,
   a green feature head or a newer main tip for the tested result SHA.
7. Delete branches only when explicitly requested and actual merge/destination
   verification passed. Otherwise retain them. Return exact evidence/blocker.

## Guardrails

- This skill is separate from `sdlc-merge-pr`; do not write Agentic SDLC run
  state or SDLC authorization files.
- Do not merge without explicit merge intent from the user or a calling skill
  that already has explicit publish/complete authorization.
- Never fall back to direct CLI merge, PATs, bypasses or delayed auto-merge.
- Do not merge with failing, cancelled, timed-out, missing-required, or
  unknown required checks.
- Do not dismiss or ignore unresolved requested changes.
- Do not delete the branch unless `--delete-branch` was explicitly requested.
- Do not rerun or dispatch unrelated CI, requeue a removed PR, undo a merge, or expand
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
- Head SHA guarded by the async merge API `sha`, review ID and broker run; for an already-merged observation,
  state that no guarded merge command was issued in this invocation.
- Checks/review/mergeability status verified.
- Merge outcome and timestamp, queue-entry evidence and observation time, or
  exact blocker; do not collapse these into one generic success claim.
- Resulting commit SHA; destination verified/failed/unverified/not applicable,
  observed target tip, observation time, and comparison evidence.
- Post-merge CI and configured Pages outcomes, exact checked SHA, applicable checks/run links and
  actual conclusions, skipped/neutral disclosures, outstanding checks, and
  observation start/deadline when waiting or timing out.
- Whether branch deletion was requested and performed.

For example, `merged; destination verified; CI failed` is a valid report.
`queued` is not a completed merge for release-publishing callers. Incomplete
verification is not permission to proceed as though every outcome passed.
