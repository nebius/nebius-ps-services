---
name: commit-push
description: "Use only when explicitly asked to commit all repo changes and push an unmanaged non-default branch with repo-root staging and validation. Reject managed worktrees; do not open a PR."
---

# Commit Push

## Help

For `$commit-push --help` or `$commit-push -h`, return concise help and stop before
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

Use this skill to publish the current feature branch by committing all local
work across the whole Git repository and pushing that branch to GitHub. Keep
the workflow narrow: commit, push, verify status, and report blockers.

## Use This Skill For

- Committing all current local changes on the active feature branch across the
  whole Git repository.
- Staging complete repository changes through the claim-bound commit
  transaction, whose helper runs `git add -A` from the repository root.
- Generating a concise commit message when the user does not provide one.
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
- Do not publish through an active pre-push or reference-transaction hook.
  Stop and route that hidden project effect to its owner rather than bypassing
  the hook.
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
     `origin/HEAD`; fall back to one direct
     `git ls-remote --symref origin HEAD` query and parse its output without
     shell composition. Normalize either result to the plain branch name, for
     example `main`, before comparing it with the current local branch. Stop
     if the default branch cannot be determined.
   - Stop if the current branch is the default branch.
   - Stop if a merge, rebase, cherry-pick, revert, or bisect is in progress.
   - Stop if unresolved conflicts exist.
4. Refresh the current branch's remote tracking context.
   - Check whether `origin/<branch>` exists with the exact `--branches` query,
     then fetch the current branch ref into `refs/remotes/origin/<branch>` when
     it exists. Use the full remote source ref
     `refs/heads/<branch>:refs/remotes/origin/<branch>` and the exact
     `--no-write-fetch-head --no-auto-maintenance --no-write-commit-graph
     --no-tags` controls. This makes the tracking-ref and object-database
     effects explicit while suppressing unrelated Git metadata updates.
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
   - Inspect `git status --short` before preparing the commit.
   - Use the canonical installed `commit_transaction.py prepare` helper with
     this turn's hook-provided authorization and claim paths, exact repository
     root, and current session. `$commit-push` authorizes this local transaction
     only as the commit phase of the same publication workflow. Never run raw
     `git add` or `git commit`.
   - Review the returned temporary-index candidate tree with read-only Git
     tree and diff commands. The helper alone runs repository-root `git add -A`
     with no pathspec and `git diff --cached --check` before committing.
   - Stop on whitespace errors, conflict markers, unresolved conflicts,
     semantic failures, generated-artifact uncertainty, or an unsafe or
     incoherent candidate. Do not mutate a failed candidate inside this
     publication workflow.
   - If the candidate tree equals `HEAD^{tree}`, report that there is nothing
     to commit.
   - Use the user's exact commit message if provided. Otherwise generate a
     concise imperative message from the reviewed candidate.
   - Run the helper's exact uncomposed `execute` action with the same root,
     session, claim, token, reviewed candidate tree, and commit message. It
     revalidates all Git state, stages the real index, and runs normal commit
     hooks under the shared repository lock.
7. Push the current branch.
   - Resolve the effective pre-push and reference-transaction hook paths. If
     either executable hook exists or a path cannot be inspected safely, stop;
     do not use `--no-verify`.
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
  `git ls-remote --symref origin HEAD`, parsed without shell composition
- Status: `git status --short --branch`
- Conflict check: `git diff --name-only --diff-filter=U`
- Remote branch check:
  `git ls-remote --exit-code --branches origin refs/heads/<branch>`
- Remote branch refresh:
  `git fetch --no-write-fetch-head --no-auto-maintenance --no-write-commit-graph --no-tags origin refs/heads/<branch>:refs/remotes/origin/<branch>`
- Pre-push hook path:
  `git rev-parse --path-format=absolute --git-path hooks/pre-push`
- Reference-transaction hook path:
  `git rev-parse --path-format=absolute --git-path hooks/reference-transaction`
- Candidate preparation and commit: canonical installed commit transaction
  helper with the hook-provided authorization and claim paths
- Upstream: `git rev-parse --abbrev-ref --symbolic-full-name @{upstream}`
- Ahead/behind: `git rev-list --left-right --count HEAD...<remote-ref>`
- Default comparison: `git diff --quiet <default-ref>...HEAD`
- Commit inside the helper: `git commit -m "<message>"`
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

- Treat `$commit-push` plus an action request as permission for the exact
  claim-bound local transaction and bounded current-branch push only.
- Never run raw `git add` or `git commit`; the transaction helper is the sole
  local mutation path.
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
  candidate validation.
- Never use `git commit --allow-empty`; an empty staged diff is a no-op.
- Never use `--no-verify`; commit hooks run normally, while an active pre-push
  or reference-transaction hook blocks remote effects until it has an explicit
  owner.
- Never make the final answer sound clean if `git status --short --branch`
  still shows dirty files, unresolved conflicts, or ahead/behind divergence.

## Output Contract

Return:

- Whether the run committed, pushed, both, or no-op'd.
- The current branch and push target.
- The commit hash and commit message when a commit was created.
- The lightweight validation performed.
- Any candidate validation blocker that stopped the transaction.
- Final `git status --short --branch` interpretation.
- Any blocker that stopped the workflow.
