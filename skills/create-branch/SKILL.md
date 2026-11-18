---
name: create-branch
description: "Use only when the user explicitly asks to create a fresh branch with create-branch to start a new local branch from origin's freshly fetched default. Update the local default safely, generate or accept a name, and switch; not for updating the current feature branch or publishing."
disable-model-invocation: true
---

# Create Branch

## Help

For `$create-branch --help` or `$create-branch -h` (including native Claude forms), return concise help and stop before
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

- `$create-branch`: update the local default and switch to a new generated branch.
- `$create-branch --branch <name>`: use the exact requested new branch name.
- `$create-branch -h` or `$create-branch --help`: show help only.

Use `/create-branch` in Claude Code or `/skills:create-branch` in its plugin.
Explicit invocation authorizes the described local Git effects. Preserve host
mode, permissions, project instructions and existing workflow ownership; do not
request another routine confirmation for those same effects.

## Scope

Run from any directory in an ordinary primary checkout with a named, committed
branch. Requires Python 3.10+, Git with `switch` and NUL-delimited worktree
inspection, and existing read access to `origin`. Use only that remote.

Start independent work from the current remote default, whether the current
branch is main or a feature branch and whether its commits were merged. Use
`update-branch` instead when the user wants to integrate the default into the
current feature branch. Commit, push, PR, release and managed-worktree flows
retain their own owners.

## Workflow

1. Read applicable repository instructions and honor authoritative restrictions
   supplied by an active workflow or an explicit Git-write lock. Do not invent
   a commit/publication grant, inspect private owner schemas, or infer active
   ownership from stale metadata/folder presence. If an applicable owner forbids
   switching, stop and report that owner before calling the helper.
2. Invoke `scripts/create_branch.py` using Python from the user's intended
   checkout directory. Pass `--branch` only when supplied; pass its value as one
   literal argument, never interpolated shell code. Locate the helper relative
   to this skill, not relative to the repository being operated on.
3. The helper requires a clean index/worktree, including untracked files and
   dirty submodules. Assume-unchanged and skip-worktree index flags stop the
   operation because they can hide local work; never clear them automatically.
   It rejects pending Git operations, detached/unborn HEAD,
   linked-checkout invocation and linked registrations for affected branches.
   Unrelated or detached registrations do not block, including stale ones.
4. The helper discovers origin's live default and branch names, fetches only the
   default and freezes its commit. It validates the requested name or uses the
   repository slug plus eight randomly sampled lowercase letters/digits. It
   checks local/remote ref namespaces and case-only collisions, trying at most
   ten generated candidates. Existing explicit names fail without reuse.
5. It switches safely to the local default and advances it only by fast-forward,
   or creates the missing default from the fetched commit with origin tracking.
   Ahead/diverged local defaults stop. It then creates and switches to the new
   branch at the exact frozen commit, without upstream tracking.
6. Check the helper's exit status and structured result. Success requires the
   new checkout and local default at the fetched commit, clean status, no new
   upstream and preserved previous feature history. Report the short result.
   Do not run project tests, install tools, query GitHub APIs or publish anything
   during routine skill execution.

## Failure Handling

A blocked result reports its phase and observed checkout. A failing Git command
or hook may already have switched the checkout or advanced a ref. Preserve those
observed effects and all work; do not claim an unchanged checkout, blindly retry,
remove locks, reset, auto-stash, auto-commit or delete a created branch. Diagnose
only the reported boundary and continue only after the state is understood.

For dirty-work blocks, tell the user to review and commit their work, verify the
checkout is clean, then rerun. Do not stage or commit it automatically. Ignored
files are retained; collisions abort instead of overwriting them. Normal hooks
and signing policy remain active. Network failures never select stale refs.

One Git writer is an operational assumption. Fresh checks detect observed
concurrent activity but are not an exclusion lock. Remote-name uniqueness and
freshness describe the advertised/fetched snapshot, not a remote reservation or
protection against later pushes. Each successful bare invocation creates another
branch; an explicit existing name fails.

## Output

Report the new checked-out branch, source default, fetched/result commit and
whether the local default was created, advanced or already current. Confirm
previous feature preservation and clean status. On failure, report the phase,
observed branch/commit, retained effects and concrete next step. Omit raw Git
logs and remote URLs, which can contain credentials. Local-only completion does
not establish installed discovery or native trigger quality.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.
