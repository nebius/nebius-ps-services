---
name: commit-push
description: Commit all current local changes across the whole Git repository on an unmanaged active non-default feature branch with repo-root git add -A, repair simple staged whitespace validation blockers when safe, generate or use a commit message, push the branch to origin, and report final worktree cleanliness. Reject worktree-managed children and route them to local integration. Use when the user explicitly asks to commit and push the current branch without opening a pull request.
---

# Commit Push

Use this skill to publish the current feature branch by committing all local
work across the whole Git repository and pushing that branch to GitHub. Keep
the workflow narrow: commit, push, verify status, and report blockers.

## Use This Skill For

- Committing all current local changes on the active feature branch across the
  whole Git repository.
- Staging complete repository changes with `git add -A` from the repository
  root, regardless of the project or subdirectory where the agent started.
- Generating a concise commit message when the user does not provide one.
- Repairing simple mechanical whitespace blockers found by
  `git diff --cached --check`, such as trailing whitespace or a final extra
  blank line, when the fix is unambiguous and safe.
- Pushing the current branch to `origin`.
- Re-running safely when there is nothing new to commit or push.
- Leaving the worktree clean after a successful commit and push.

## Non-Goals

- Do not create, open, update, or merge pull requests; use `create-pr` for PR
  workflows.
- Do not create branches, switch branches, merge, rebase, pull, cherry-pick, or
  resolve divergence.
- Do not force-push, use `--force-with-lease`, or bypass hooks with
  `--no-verify`.
- Do not push the repository default branch.
- Do not stage a partial pathspec or project-scoped path. This skill always
  stages the complete repository diff with `git add -A` from the Git root; use
  another explicit Git workflow for narrowed commits.
- Do not repair semantic code, test, dependency, merge, or conflict-marker
  failures. Only mechanical whitespace validation blockers are in scope.

## Workflow

1. Reject direct managed-child publication.
   - Resolve the installed `worktree` skill and invoke its Python manager's
     `publication-guard --publication-action push` action from the current
     checkout before staging, committing, fetching, or pushing.
   - If it reports any managed child, integration candidate, nested worker, or
     inconsistent ownership claim, stop and route to the owning local workflow.
     Publish only from the accumulated source branch. Only a genuinely
     unmanaged manual worktree may pass as `unmanaged`.
2. Enter the repository root.
   - Use `git rev-parse --show-toplevel`, then run all Git commands from that
     root.
   - Treat that Git root as the commit scope even when the current working
     directory is a nested service, chart, app, package, or project folder.
     Never infer a narrower staging scope from the starting directory.
3. Inspect branch and repository safety.
   - Stop on detached `HEAD`.
   - Stop if there is no `origin` remote.
   - Determine the remote default branch before staging. Prefer
     `origin/HEAD`; fall back to querying the remote `HEAD` symref. Normalize
     either result to the plain branch name, for example `main`, before
     comparing it with the current local branch. Stop if the default branch
     cannot be determined.
   - Stop if the current branch is the default branch.
   - Stop if a merge, rebase, cherry-pick, revert, or bisect is in progress.
   - Stop if unresolved conflicts exist.
4. Refresh the current branch's remote tracking context.
   - Check whether `origin/<branch>` exists, then fetch the current branch ref
     into `refs/remotes/origin/<branch>` when it exists. Use the full remote
     source ref `refs/heads/<branch>:refs/remotes/origin/<branch>` instead of
     the abbreviated `<branch>:refs/remotes/origin/<branch>` form so branch
     names are resolved from the remote heads namespace explicitly.
   - Determine the branch upstream before staging. If an upstream exists and is
     not exactly `origin/<branch>`, stop and report the exact upstream instead
     of committing work that this skill will refuse to push.
   - Compare the local branch with its upstream, or with `origin/<branch>` if
     the upstream is missing but a same-named remote branch exists.
   - Stop if the local branch is behind or diverged. Do not pull, merge,
     rebase, or force-push without a separate explicit request.
5. Handle idempotent no-op cases.
   - If the worktree is clean and the branch has no unpublished commits, report
     that nothing needed to be committed or pushed.
   - If the worktree is clean but the branch is ahead, skip committing and push
     the existing local commits.
   - If the worktree is clean, no upstream exists, no same-named remote branch
     exists, and the branch has commits or a diff against the default branch,
     push it with upstream tracking.
   - If the worktree is clean, no upstream exists, no same-named remote branch
     exists, and the branch has no commits or diff against the default branch,
     report that there is nothing to push.
6. Commit dirty work.
   - Inspect `git status --short` before staging.
   - Run `git add -A` from the repository root. Do not pass a pathspec, current
     project directory, or service directory.
   - Run `git diff --cached --check`.
   - If staged validation fails only because of simple mechanical whitespace
     issues, inspect the exact files and lines, repair the smallest safe
     whitespace-only issue, run `git add -A`, and rerun
     `git diff --cached --check`. Keep this repair loop bounded to at most two
     passes before stopping and reporting the remaining blocker.
   - Stop on conflict markers, unresolved conflicts, semantic failures, broad
     formatter churn, generated-artifact uncertainty, or any staged validation
     failure whose repair is not plainly mechanical and local to the reported
     lines.
   - Inspect `git diff --cached --stat` and, when needed, a focused staged
     diff before writing the commit message.
   - If the staged diff is empty after `git add -A`, report that there is
     nothing to commit.
   - Use the user's exact commit message if provided. Otherwise generate a
     concise imperative message from the staged diff.
   - Run `git commit -m "<message>"` with normal hooks enabled.
7. Push the current branch.
   - If the branch has no upstream, use `git push -u origin HEAD:<branch>`.
   - If the branch already tracks `origin/<branch>`, use
     `git push origin HEAD:<branch>`.
8. Verify and report.
   - Run `git status --short --branch`.
   - Report the branch name, commit hash if a new commit was created, push
     target, final ahead/behind state, and whether the worktree is clean.

## Commit Message Guidance

- Prefer one concise imperative subject line.
- Use a scope when the diff has a clear primary area, for example
  `skills: add commit-push workflow`.
- Do not include internal paths, customer names, secrets, or private endpoint
  details in the message.
- If the staged diff is broad and no clear message can be generated safely,
  stop and ask the user for an exact commit message.

## Recommended Commands

- Repository root: `git rev-parse --show-toplevel`
- Current branch: `git symbolic-ref -q --short HEAD`
- Origin check: `git remote get-url origin`
- Default branch: `git symbolic-ref --short refs/remotes/origin/HEAD | sed 's#^origin/##'`
- Default branch fallback:
  `git ls-remote --symref origin HEAD | awk '/^ref:/ { sub("refs/heads/", "", $2); print $2; exit }'`
- Status: `git status --short --branch`
- Conflict check: `git diff --name-only --diff-filter=U`
- Remote branch check: `git ls-remote --exit-code --heads origin <branch>`
- Remote branch refresh:
  `git fetch origin refs/heads/<branch>:refs/remotes/origin/<branch>`
- Staging: `git add -A` from the repository root, with no pathspec
- Staged validation: `git diff --cached --check`
- Staged summary: `git diff --cached --stat`
- Upstream: `git rev-parse --abbrev-ref --symbolic-full-name @{upstream}`
- Ahead/behind: `git rev-list --left-right --count HEAD...<remote-ref>`
- Default comparison: `git diff --quiet <default-ref>...HEAD`
- Commit: `git commit -m "<message>"`
- Push with upstream: `git push -u origin HEAD:<branch>`
- Push existing upstream: `git push origin HEAD:<branch>`

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Guardrails

- Treat `$commit-push` plus an action request as permission to run mutating Git
  add, commit, and push commands for the current branch only.
- Never run `git add -A` until branch safety, repository state, and conflict
  checks pass.
- Never use project-folder, package-folder, or current-directory staging for
  this skill. The only staging command in scope is repo-root `git add -A`.
- Never push from the default branch, detached `HEAD`, or a branch whose
  default-branch status cannot be determined.
- Never recover branch divergence inside this skill. Report the blocker and
  wait for a separate explicit request.
- Never treat conflict markers as auto-repairable whitespace. If
  `git diff --cached --check` reports conflict markers or unresolved conflicts,
  stop and report the blocker.
- Never run broad formatters or dependency update commands just to satisfy
  staged validation. Auto-repair is limited to small, obvious whitespace edits
  in files already included in the repo-wide staged diff.
- Never continue after a staged-validation repair unless a fresh
  `git add -A` and `git diff --cached --check` pass.
- Never use `git commit --allow-empty`; an empty staged diff is a no-op.
- Never use `--no-verify`; commit and push hooks should run normally.
- Never make the final answer sound clean if `git status --short --branch`
  still shows dirty files, unresolved conflicts, or ahead/behind divergence.

## Output Contract

Return:

- Whether the run committed, pushed, both, or no-op'd.
- The current branch and push target.
- The commit hash and commit message when a commit was created.
- The lightweight validation performed.
- Any whitespace validation repairs performed before commit.
- Final `git status --short --branch` interpretation.
- Any blocker that stopped the workflow.
