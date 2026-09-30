---
name: update-branch
description: "Use only when the user explicitly asks to update the current local feature branch from origin's default branch. Merge in place in a clean ordinary checkout; no branch switching, rebase, push, or feature-upstream sync."
disable-model-invocation: true
---

# Update Branch

## Help

For `$update-branch --help` or `$update-branch -h` (including native Claude forms), return concise help and stop before
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

## Usage

- `$update-branch`: merge origin's default into the checked-out feature branch.
- `$update-branch -h` or `$update-branch --help`: show help only.

No positional arguments. No additional public flags.

## Scope

Use `$update-branch` in Codex, `/update-branch` in Claude Code, or
`/skills:update-branch` in the Claude plugin. Requires Git and existing origin
access. Invocation authorizes this local merge and clear conflict resolutions,
including a merge commit when needed. Honor host mode, project rules and hooks.
Do not route through commit transactions or request another routine approval.

Stay small and fast: use Git directly; no execution helper, custom state,
GitHub API, full-repository test suite, or installation step. Read applicable
project instructions; inspect source and run focused tests only for conflicts.

## Workflow

### 1. Check eligibility

Resolve the Git root, named branch and starting HEAD. Run from that root.
Require one Git writer; stop if branch, HEAD or local changes move unexpectedly.
Use NUL-delimited output when parsing paths. Check cleanliness with:

```bash
git --no-optional-locks status --porcelain=v1 -z --untracked-files=all --ignore-submodules=none
```

Stop for any staged, unstaged or untracked changes, including dirty submodules.
Tell the user: "Review, stage, and commit your work, then rerun `$update-branch`
once the working directory is clean." For dirty submodules, resolve their local
work first. Do not stage or commit existing work automatically.
Resolve operation paths through `git rev-parse --git-path`; stop for existing
merge, rebase/am, cherry-pick, revert, sequencer or bisect state. Do not remove
locks or repair somebody else's operation. Reject detached or unborn HEAD.

Require an ordinary primary checkout: inspect `git worktree list --porcelain -z`;
reject linked checkouts or any additional registered worktree. Reject any
`branch.*.worktreeSkill*` metadata or sibling `<primary-name>-worktrees` entry,
including stale entries and dangling symlinks. Reject active coordinator work
or other ownership evidence supplied by project instructions/runtime guards;
uncertain ownership stops the update. Do not parse private owner schemas, call
publication guards under a false action, or remove records to pass this check.

Inspect effective `branch.<current>.mergeOptions` and custom merge strategies.
Stop for conflicting or unrecognized options: hook/signature bypass, squash,
no-commit, unrelated histories, autostash, ignored-file overwrite, or blanket
ours/theirs policies. Preserve normal hooks, signing and verification settings.
Do not silently edit configuration or bypass a hook rejection.

### 2. Discover and fetch

Require the named remote `origin`; never choose another remote silently.
Query `git ls-remote --symref origin HEAD`. Require one symbolic target under
`refs/heads/`, validate its branch name, and record it as `update_default`.
Do not guess `main` or trust cached `origin/HEAD`. Stop on the default branch.

Fetch only that branch, with quoted values and no configured refmap side effects:

```bash
git fetch --no-tags --no-prune --no-recurse-submodules --refmap= origin \
  "refs/heads/$update_default:refs/remotes/origin/$update_default"
```

Stop on fetch/authentication failure; never merge a stale cached ref. Resolve
`refs/remotes/origin/$update_default^{commit}` into `update_target` and freeze it.
Stop for unrelated history or insufficient shallow history; do not automatically
deepen, force history, or merge the current branch's separate upstream.

### 3. Merge in place

Recheck eligibility, branch and starting HEAD immediately before merging.
If the frozen target is already an ancestor of HEAD, verify and report no-op.
Otherwise use the frozen commit, allowing fast-forward or a normal merge commit:

```bash
git merge --ff --no-edit --no-autostash --no-overwrite-ignore \
  --no-rerere-autoupdate "$update_target"
```

Inspect exit status and Git state together. A nonzero exit may mean conflicts,
a hook/signing error, or another failure; never blindly retry or claim success.
Ignored-file collisions stop the merge rather than discard those local files.

### 4. Resolve only clear conflicts

For this task's pending merge, confirm branch, original HEAD and `MERGE_HEAD`
still match the recorded inputs. Do not adopt an unrelated pending merge.
Inspect every conflicted path, both sides' intent, and reused rerere resolutions;
no blanket ours/theirs selection. Resolve only when intent is clear. Ask about
ambiguous behavior and leave the merge pending while awaiting the answer.

Run the narrowest applicable checks on resolutions. Review the complete staged
and unstaged diff, ensure no unmerged entries remain after staging, and never
include new unrelated work. Before staging, verify every changed/untracked path
belongs to this merge; otherwise stop. Stage with `git add -A` from the root.
Inspect `git diff --cached --check` and the staged result, then finish with
`GIT_EDITOR=true git merge --continue`, retaining hooks and signing. If checks,
hooks or signing fail, report the failure and pending state; do not bypass it,
auto-abort, reset, or create an unrelated commit. Recheck state before resuming.

### 5. Verify and report

Require the original branch, both starting HEAD and frozen target as ancestors
of final HEAD (`git merge-base --is-ancestor`), no pending Git operation, and
clean status using the same explicit command. Inspect any failure before
reporting completion; a successful command alone is insufficient. Submodule
checkout mismatches or test-created files are reported, never auto-cleaned.

Report branch, source default/ref and fetched SHA, outcome (already current,
fast-forwarded, merged, or blocked), resulting HEAD, conflict/check results when
applicable, and remaining work. Do not expose remote URLs or raw logs. Updates
are local; never push, switch, rebase, stash, reset, prune or change tracking.
A repeat invocation with the same merged target is a no-op. State rechecks are
not a concurrency lock; stop on competing activity rather than promise isolation.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.
