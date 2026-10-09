---
name: publish-release
description: "Use only when explicitly asked to publish a GitHub Release: commit current work, push and merge its PR, locally review/fix, use protected Actions merge, tag, verify assets, and resume interruptions. Also supports setup-only guidance."
disable-model-invocation: true
---

# Publish Release

## Help

For `$publish-release --help` or `$publish-release -h` (including native Claude forms), return concise help and stop before
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

Use `$publish-release` in Codex, `/publish-release` in Claude Code, or
`/skills:publish-release` in the Claude plugin. This skill is explicit-only.
Use the selected host's native tools and private home; missing required tools or
owners are blockers, never permission to bypass their controls.

## Purpose

Publish a verified GitHub Release from the current project in one invocation.
Own the entire continuation through commit, PR, approval, merge, tag, workflow
and asset verification. Do not return a routine instruction for the user to merge
or invoke a second publication phase when the task can continue automatically.

## When To Use

Use for an explicit request to publish or resume a GitHub Release, or to prepare
its release setup. A complete request delegates the necessary owner handoffs.

## When Not To Use

Do not publish from a design discussion, skill-source edit, help request or
implicit trigger. Use the image or Helm publication owners for those artifacts.

## Public Usage

- `$publish-release [--tag X.Y.Z|<prefix>-vX.Y.Z]`: complete publication and wait.
- `$publish-release --resume [--tag X.Y.Z|<prefix>-vX.Y.Z]`: reconcile and continue
  one unfinished release for the current project, including in a fresh session.
- `--mode setup|prep|publish|complete`: default `complete`; setup prepares assets,
  prep ends at the pushed PR, publish continues an existing merged release, and
  complete follows the whole workflow.
- `--project-dir <path>`: selected project, default current directory.
- `--main-branch <branch>`: expected default branch; verify against live origin.
- `--tag-prefix <prefix>`: derive only from unambiguous project/workflow metadata.
- `--merge-method squash|merge|rebase`: default squash; a required merge queue
  stops for a maintainer because the built-in token cannot enqueue.
- `--wait|--no-wait`: default wait. No-wait checkpoints pending work and reports
  pending, never published without final verification.
- `--project-name`, `--package-import-name`, `--asset-glob`, `--python-version`:
  derive release setup/build inputs from the project when possible.
- `-h, --help`: help only. No additional public flags.

Use an explicit tag or unambiguous prepared release version. If neither exists,
ask once; do not invent a patch bump. Resolve resume before choosing a new version.
A tag disambiguates multiple unfinished releases. Repeating the same release
invocation reuses its checkpoint; only an explicit resume renews wait deadlines.

## Inputs

Use the selected project, explicit or prepared version, Git/GitHub identity,
release workflow and expected artifacts. Resolve missing version information
once and reject ambiguous project or publication targets.

## Required Reads

Read applicable instructions, project README/changelog/version/build metadata,
Git status and complete diff, live default and origin destinations, release
workflow and expected assets. Read `references/orchestration.md` before execution.
Load `create-pr` and its commit-continuation reference for commit/PR work, and
`merge-pr` plus completion-verification for merge and exact-result CI evidence.

## Writes

An explicit complete release request authorizes its necessary preparation,
reviewed repository-wide commits, pushes, PR creation/reuse, protected merge,
annotated tag and release publication. Do not require a second commit/PR/merge
invocation or repeated approval for those actions. This is delegation to the
existing owners, not a new commit authorization protocol.

Review and validate all current repository changes plus release metadata. Only
create-pr's canonical transaction stages with repository-root `git add -A` and
commits. No raw Git commit fallback, separate commit-push task, cherry-pick,
force push or direct default-branch commit. Preserve new unrelated work appearing
on resume; do not silently add it to the frozen release.

Setup may create missing release assets and workflow. Private checkpoints belong
under the selected agent home outside Git, contain no credentials, and never
serve as commit/merge authorization. The tag workflow owns artifact publication.
Managed children and active SDLC publication remain with their workflow owners.

## Process

Setup-only requests generate/validate setup assets and stop without release
version selection, checkpoints or Git publication. Prep stops after binding the
pushed PR; publish starts from the reconciled merged checkpoint. No-wait stops
at the first pending gate and reports its checkpoint. Resume does not apply to
setup. Complete follows every step below.

1. Inspect and freeze project, repository, origin, default branch, version,
   release workflow and expected asset families. Reject ambiguous/mismatched
   destinations, unfinished Git operations and missing required capabilities.
2. Reconcile any existing release checkpoint before doing preparation. A
   completed record still requires fresh remote release/asset verification.
3. For a new release, begin the canonical create-pr grant before branch changes.
   Reuse the feature branch; from default, create `release/<tag>` at the captured
   head. Create missing setup assets and prepare release notes/metadata.
4. Use the serialized content-only preparation helper, then create-pr's review,
   validation, complete-tree commit, base synchronization, push and PR reuse.
   An initially dirty worktree is normal. Preparation itself never commits.
5. Run local review-pr and safe repairs through create-pr's private preparation
   handoff. Bind the final reviewed pushed PR/head to the checkpoint, then invoke
   merge-pr for Actions approval and guarded merge. Do not wait for human approval
   before dispatching the Actions broker. Refresh all gates for the exact head. A queued PR is pending, never a completed merge.
   If base advancement requires a canonical repair after binding, use the guarded
   `reconcile-pr` transition in the orchestration reference with fresh review
   evidence before any new broker dispatch. Arbitrary head drift still blocks.
6. Observe actual merge, freeze its method-specific resulting SHA, and require
   merge-pr's remote-default ancestry and exact-result CI verification. Failed,
   pending or unverified applicable CI blocks tagging. No configured CI is
   acceptable only when the owner independently establishes that fact.
7. Create a private isolated clone at that exact merged SHA with full history.
   Keep the user's working checkout and local tags unchanged. Recheck remote
   ancestry, release notes and runtime inputs. Preserve required signing/auth
   configuration by references; never copy secret values into files or output.
8. Create and runtime-verify the annotated tag locally, checkpoint its exact
   object and public tag metadata, then push the exact object. On resume, inspect
   remote state first and restore only that same object if the clone was lost.
9. Wait for the exact tag/commit/workflow, including environment approvals.
   Download expected release artifacts; verify versions, sizes and available
   SHA-256 digests before marking the release complete.
10. Report the release URL, tag, merged SHA, PR/workflow results and asset proof.
    Retain resumable checkpoint evidence; clean only exact task-owned scratch.

## Waiting And Resume

Routine PR approval is supplied by Actions after local review. Show
`Waiting for approval on GitHub: <link>` only for an actual additional protection
or release-environment approval that the built-in token cannot satisfy.
Explain that an eligible reviewer must approve and that continuation is automatic.
Poll every 15 seconds and update progress at least once per minute. Additional
approvals consume the enclosing phase's remaining budget; there is no separate
ten-minute cutoff. Partial approval or reordered API responses never restart it.

PR readiness, merge settlement, post-merge verification and release execution each
have independent fixed 10,800-second phase budgets. Gate changes, repairs and
reruns never restart a phase. The checkpoint owns the verification deadline;
merge-pr consumes it while verifying the exact result. An open PR
without queue membership can be awaiting direct async merge; observe until actual
merge or the phase deadline. Previously observed queue removal remains a blocker.
Failed checks, rejected reviews, closed PRs, conflicts and identity
drift are blockers; do not mislabel them as pending approval.

At timeout, preserve progress and show `$publish-release --resume --tag <tag>`.
Do not cancel GitHub work or leave a local background publisher. A later explicit
resume renews waiting budgets and rechecks actual state; it does not recreate
commits, PRs, merges, tags or releases already completed. New-session commit work
requires fresh owner intake, never replay of an old grant or manufactured receipt.
Existing stored deadlines remain unchanged until explicit resume. These are local
observation limits, independent of GitHub job execution limits.

## Idempotency

- Reuse matching branch/PR/release identities. Refuse collisions and remote
  divergence; let create-pr own safe base synchronization and validated repairs.
- Observe ambiguous effects before retrying any mutation.

## Failure Handling

- A version mismatch deletes only the exact task-created unpushed local tag.
  A push error keeps identity evidence until the remote outcome is established.
- Missing assets, draft releases and mismatched tag/workflow commits never count
  as success. Repair failing publication through its owner, not duplicate uploads.

## Must Not

- No remote tag rewrite/deletion, branch-protection bypass, admin merge, implicit
  requeue or CI rerun.
- A release checkpoint is progress evidence. Never use it to bypass managed
  workflow guards, current user intent, normal Git hooks or credential controls.

## Completion Criteria

Complete means confirmed protected merge, verified result in default history,
exact annotated tag, successful matching workflow, published release and verified
expected assets. Partial modes, no-wait and timeouts must report their actual
checkpoint and exact next action. Distinguish source tests, installed/native
behavior and live publication evidence.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return mode, project, version/tag, PR and merge result, frozen merged SHA,
workflow result, release URL and asset evidence, or the blocker/approval link,
timeout and exact resume command. Report skipped or unverified lanes explicitly.

## Resources

- `references/orchestration.md`: owner handoffs, helper calls and recovery.
- `scripts/release_session.py`: private checkpoints, observation and deadlines.
- `scripts/publish-release-doer.sh`: content, local tag, exact push and wheel checks.
- `assets/`: optional changelog, runnable helper and tag-workflow templates.
