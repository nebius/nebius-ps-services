---
name: commit
description: "Create a fast local Git commit on the current branch without pushing. Use when the user explicitly asks to commit current local changes, or when a fresh explicit `$worktree integrate` delegates one exact child/source commit: inspect the complete diff, stage with repo-root `git add -A`, validate, commit with normal hooks, and report exact evidence. Do not use for pushes, pull requests, branch repair, or Agentic SDLC feature commits."
---

# Commit

## Help

For `$commit --help` or `$commit -h`, return concise help and stop before
any workflow step. Include the purpose, invocation policy, public usage/actions,
and `-h, --help` plus only documented skill-level options; say "No additional
public flags" when none exist. For internal or coordinator-only skills, state
that boundary and that no standalone public workflow action exists. After the
selected `SKILL.md` is loaded, help is report-only: do not call any additional
tools, inspect project state, or modify files, private state, Git, or external
systems. Never
expose private helper actions or treat help as workflow authorization.

## Purpose

Create one local commit for the current branch as quickly as is safely
reasonable. This skill is for ordinary Git commits, not publishing or Agentic
SDLC checkpoints.

## Use This Skill For

- Committing all current local changes on the current branch without pushing.
- Staging complete repository changes with repo-root `git add -A`, regardless
  of the project, service, chart, app, or package directory where Codex starts.
- Using the user's exact commit message when provided.
- Generating a concise commit message from the staged diff when the user does
  not provide one.
- Reporting a clear no-op when there is nothing to commit.
- Executing one bounded delegated commit for a fresh explicit
  `$worktree integrate`: an eligible ordinary child first or its primary source
  second, at the exact preflight branch and head.

## Non-Goals

- Do not push; use `commit-push` when the user explicitly asks to push.
- Do not create, open, update, review, merge, or close pull requests.
- Do not replace `sdlc-commit`; that skill is only for Agentic SDLC feature
  checkpoints with SDLC evidence and local run state.
- Do not create branches, switch branches, pull, merge, rebase, cherry-pick,
  revert, stash, reset, amend, squash, or repair divergence.
- Do not stage a partial pathspec or project-scoped path. This skill always
  stages the complete repository diff with `git add -A` from the Git root.
- Do not run formatters, dependency updates, tests, or broad repair commands
  just to create the commit unless the user separately requested them.

## Workflow

1. Enter the repository root.
   - Use `git rev-parse --show-toplevel`, then run all Git commands from that
     root.
   - Treat the Git root as the commit scope. Never infer a narrower scope from
     the starting directory.
2. Run fast branch and repository safety checks.
   - Stop on detached `HEAD`.
   - Stop if a merge, rebase, cherry-pick, revert, or bisect is in progress.
   - Stop if unresolved conflicts exist.
   - If the repository default branch is known locally and the current branch
     matches it, stop unless the user explicitly asked to commit on the default
     branch. Do not perform network fetches only to discover the default branch.
3. Inspect current status.
   - Run `git status --short --branch`.
   - If the worktree is clean, report that there is nothing to commit.
   - Inspect staged and unstaged diffs plus every untracked, renamed, deleted,
     credential-like, environment, key, and generated path before staging.
   - Stop before staging obvious secrets, private endpoints, credentials,
     unclear generated artifacts, or a diff too broad or incoherent to
     summarize truthfully.
4. Stage the whole repository.
   - Run `git add -A` from the repository root with no pathspec.
   - Do not use `git add .`, a service path, or a package path.
5. Validate the staged diff.
   - Run `git diff --cached --check`.
   - If it reports whitespace errors, conflict markers, or another staged-diff
     problem, stop and report the blocker. Keep this skill fast; do not start a
     repair loop unless the user separately asks for fixes.
   - Run `git diff --cached --stat`.
   - If the staged diff is empty after `git add -A`, report that there is
     nothing to commit.
   - Inspect staged filenames and enough focused staged diff to avoid an
     obviously wrong or unsafe commit message, especially for new config,
     credential-like, environment, key, or generated files.
   - Record the exact reviewed staged tree with `git write-tree` after the
     inspection. In delegated Worktree mode, pass that tree plus the exact
     preflight head to Worktree's private integration-commit action; do not run
     a free-standing `git commit`.
6. Commit.
   - Use the user's exact commit message if provided.
   - Otherwise generate one concise imperative subject line from the staged
     diff. If the diff is too broad or unclear to summarize truthfully, stop and
     ask for a commit message.
   - For an ordinary direct `$commit`, run `git commit -m "<message>"` with
     normal hooks enabled.
   - For a delegated Worktree commit, let the private Worktree helper create a
     durable source-scoped preparation claim, rerun `git add -A`, compare the
     staged tree to the reviewed tree, and then run the normal-hook commit.
     The claim blocks nested leases, competing integrations, removal, and
     source publication until candidate reservation or explicit abort.
7. Verify and report.
   - Run `git status --short --branch`.
   - In delegated worktree mode, require the same branch, exactly one
     direct-descendant commit from the preflight head, and a completely clean
     checkout. Return that exact commit SHA to the worktree workflow.
   - Compare `HEAD^{tree}` with the reviewed staged tree. If a hook changed the
     committed tree, return review-required and stop integration until the
     complete actual commit is inspected and bound through the private review
     action.
   - Report the branch name, commit hash and message when a commit was created,
     the validation performed, and whether the worktree is clean.

## Recommended Commands

- Repository root: `git rev-parse --show-toplevel`
- Current branch: `git symbolic-ref -q --short HEAD`
- Status: `git status --short --branch`
- In-progress operation checks: inspect Git state paths such as `MERGE_HEAD`,
  `rebase-merge`, `rebase-apply`, `CHERRY_PICK_HEAD`, `REVERT_HEAD`, and
  `BISECT_LOG` under the Git directory from `git rev-parse --git-path <name>`.
- Conflict check: `git diff --name-only --diff-filter=U`
- Default branch, local only:
  `git symbolic-ref -q --short refs/remotes/origin/HEAD | sed 's#^origin/##'`
- Staging: `git add -A` from the repository root, with no pathspec
- Staged validation: `git diff --cached --check`
- Staged summary: `git diff --cached --stat`
- Staged filenames: `git diff --cached --name-status`
- Reviewed staged tree: `git write-tree`
- Commit: `git commit -m "<message>"`
- Committed tree: `git rev-parse HEAD^{tree}`
- Commit hash: `git rev-parse --short HEAD`

## Guardrails

- Treat `$commit` plus an action request as permission to run mutating local
  `git add -A` and `git commit` commands for the current branch only.
- Treat a fresh explicit `$worktree integrate <name>` as permission for only
  the eligible child/source commits that its read-only preflight orders. Do not
  infer delegated permission from a coordinator handoff, active reservation,
  restart, candidate, or ordinary implicit skill selection.
- Never push, create a PR, dispatch CI, publish artifacts, or call external
  write APIs from this skill.
- Never run `git add -A` outside the repository root.
- Never pass a pathspec to `git add -A`; this skill is intentionally whole-repo
  because multi-folder repositories often have related changes outside the
  current directory.
- Never use `--no-verify`; commit hooks should run normally.
- Never use `--allow-empty`; an empty staged diff is a no-op.
- Stop before committing obvious secrets, private endpoints, credential files,
  generated files with unclear ownership, unresolved conflicts, or staged
  validation failures.
- Stop on known default branch unless the user explicitly authorized committing
  there.
- Never write Agentic SDLC evidence, permissions files, run state, or
  checkpoints. Use `sdlc-commit` inside the SDLC workflow instead.
- In delegated worktree mode, stop if the observed branch or head differs from
  preflight. Never commit a nested/coordinated child or any dirty checkout after
  an integration reservation exists.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return:

- Whether the run committed or no-op'd.
- The current branch.
- The commit hash and commit message when a commit was created.
- Confirmation that staging used repo-root `git add -A`.
- The lightweight validation performed.
- Final `git status --short --branch` interpretation.
- Any blocker that stopped the workflow.
