---
name: create-pr
description: Create or reuse one or more feature branches, make each branch conflict-free against the default branch when possible, push it, open or reuse GitHub pull requests, and return the PR numbers, URLs, and merge order. Use when a user wants local work or named branches turned into reviewable PRs without hand-driving Git and GitHub steps, including when they provide the exact PR title or body to use.
---

# Create PR

Use this skill to turn local repository work or named branches into GitHub pull
requests with a safe default-branch workflow. It can prepare a single PR or one
PR per branch, resolve straightforward merge conflicts against the default
branch, and report the order the user should merge the PRs manually.

## Use This Skill For

- Opening a PR from local changes or local commits.
- Opening or reusing PRs for one or more named branches.
- Moving in-progress work off the default branch before publishing it.
- Staging complete monorepo work with `git add -A` when the user wants the
  current local changes submitted as one PR.
- Reusing the current feature branch instead of creating extra branches.
- Making each target branch conflict-free against the default branch, usually
  `main`, before returning the PR.
- Planning an ordered multi-branch merge path when several branches may overlap.
- Treating the current non-default branch as the target when the user invokes
  the skill without naming a branch.
- Honoring an explicit user-provided PR title or body instead of inventing one.
- Returning PR numbers, URLs, readiness state, and merge order so the user can
  review or merge manually.

## Requirements

- A Git repository with an `origin` remote.
- GitHub CLI (`gh`) authenticated for the target repository.
- A clean worktree before switching between existing branches or updating
  remote PR branches. If local work is dirty on the active branch, move or
  commit it before updating other branches.

## Branch Selection

- If the user provides one or more branch names, process exactly those
  branches. Preserve the user-provided order unless current Git evidence shows
  a safer dependency order.
- If the user provides no branch name and the current branch is non-default,
  treat the current branch as the only target branch and make it conflict-free
  against the default branch.
- If the user provides no branch name and the current branch is the default
  branch, use the local-work PR flow: create a feature branch only when there
  is work to submit.
- For multiple target branches, create or reuse one PR per branch. Do not
  combine unrelated branches into a single PR.
- Use the repository default branch as the PR base unless the user explicitly
  provides another base.

## Workflow

1. Inspect repository state first.
   Determine:
   - current branch
   - whether `HEAD` is detached
   - repository default branch
   - whether the worktree has changes
   - whether the current branch already has an upstream
   - whether the current branch is ahead of or behind `origin/<base>`
   - whether the user named target branches or expects current-branch fallback
2. Refresh base-branch context.
   Fetch `origin/<base>` and the target branch refs before deciding how to
   branch, resolve conflicts, or open PRs. If the local default branch is clean
   and has no local-only commits, fast-forward it first so new work starts from
   the latest reviewed base.
3. Resolve the target branches.
   - If `HEAD` is detached, stop and explain the problem.
   - If the user named branches, check whether each exists locally or on
     `origin`. Stop for unknown branches instead of guessing.
   - If no branch is named and the current branch is the default branch, create
     a new branch from it. Prefer a short user-provided slug such as
     `prep/<project-tag>` or `fix/<topic>`. Ensure the branch name does not
     collide with an unrelated existing local or remote branch.
   - If no branch is named and the current branch is already non-default,
     reuse it. Do not create another branch.
4. Make each branch reviewable.
   A PR must come from committed changes, not only a dirty worktree.
   - If the worktree is dirty on the default branch, create the feature branch
     first so the in-progress work moves off the default branch safely.
   - If the worktree is still dirty after branch selection and the user clearly
     wants to submit the current local work, stage the complete repository diff
     from the repository root with `git add -A`, including modified, deleted,
     and untracked files across monorepo projects. Then inspect the staged diff
     and commit it with a concise message.
   - Use path-limited staging only when the user explicitly requests a narrower
     PR scope. Otherwise, do not leave related monorepo edits unstaged.
   - If the user did not clearly ask to submit the dirty work, stop and explain
     that a PR cannot be created until the branch has reviewable commits.
5. Confirm there is something to review for every target.
   Compare each target branch with `origin/<base>`. If a branch has no diff and
   no unpublished commits, do not open an empty PR for that branch.
6. Make target branches conflict-free when possible.
   - First test each target branch against `origin/<base>` without changing it,
     for example with
     `git merge-tree --write-tree origin/<base> <branch-or-origin/branch>`.
   - For a branch with conflicts against the current base, update that branch
     non-destructively by merging `origin/<base>` into it. Resolve only
     straightforward conflicts where both sides are clear and preserving
     current logic is possible.
   - Do not rebase or force-push shared PR branches by default. Use rebase or
     force-with-lease only when the user explicitly asks for it and the branch
     ownership is clear.
   - Never use blanket `ours` or `theirs` conflict resolution. Keep both sides
     when they are additive, preserve the branch behavior when the base only
     moved nearby code, and stop when the conflict needs product or business
     judgment.
   - When multiple branches are requested, also validate the proposed merge
     order with a throwaway local branch or worktree starting at `origin/<base>`
     and merging the target branches in order. Do not push the throwaway
     branch.
   - If a later branch depends on an earlier branch, either merge the earlier
     branch into the later branch so the ordered path is conflict-free, or
     report that the later PR should be refreshed after the earlier PR lands.
     Choose the non-destructive update only when the dependency is evident from
     current branch history or the user asked for an ordered multi-branch PR
     flow.
7. Validate after conflict resolution.
   Run focused checks based on touched files. At minimum, scan for conflict
   markers and whitespace errors before pushing. If relevant sibling skills
   apply to the touched surfaces, use them after the branch edits and keep the
   scope limited to the PR branches.
8. Publish each branch.
   If a branch has no upstream yet, push it with upstream tracking. If conflict
   resolution created new commits, push those commits to the same branch.
9. Avoid duplicate PRs.
   Check for an existing open PR for each head branch. If one already exists,
   return that PR instead of creating another.
10. Open each PR with the right readiness state.
   Treat any explicit user-provided PR title as authoritative. Use it verbatim
   unless the user explicitly asks for refinement. Do not substitute a generic
   title such as "Preparation" and do not derive the PR title from a branch
   prefix such as `prep/<topic>`.

    - If the user provides both title and body, use both.
    - If the user provides only a title, use that title and generate or fill the
      body as needed.
    - If the user provides neither, prefer a concise generated title/body or
      `gh pr create --fill` when the commit history is clean enough to support
      it.

   Prefer a draft PR when the branch is intentionally incomplete or validation
   has not run yet.
11. Return the result.
   Report:

    - head branch name for each PR
    - base branch name
    - PR number and URL for each PR
    - whether conflicts were found and how they were resolved
    - validation performed
    - recommended manual merge order
    - any blockers that remain

## Recommended Commands

- Default branch detection:
  - `gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name'`
  - fallback: `git symbolic-ref --short refs/remotes/origin/HEAD | sed 's#^origin/##'`
- Refresh refs:
  - `git fetch origin --prune`
- Conflict checks:
  - `git merge-tree --write-tree origin/<base> <branch-or-origin/branch>`
  - `git diff --name-only --diff-filter=U`
  - `rg -n '^(<{7}|={7}|>{7})'`
- Non-destructive branch update:
  - `git switch <branch>`
  - `git merge --no-edit origin/<base>`
  - `git push origin <branch>`
- Complete local-work staging:
  - `git status --short`
  - `git add -A`
  - `git diff --cached --stat`
  - `git commit -m "<concise message>"`
- Ordered merge simulation:
  - `git switch --detach origin/<base>`
  - `git switch -c tmp/pr-order-check-<short-id>`
  - `git merge --no-edit <first-branch-or-origin/first-branch>`
  - `git merge --no-edit <next-branch-or-origin/next-branch>`
  - if a simulation merge conflicts: `git merge --abort`
  - `git switch <original-branch>`
  - `git branch -D tmp/pr-order-check-<short-id>`
- Existing PR lookup:
  - `gh pr list --head <branch> --state open --json number,url,headRefName,baseRefName`
- PR readiness:
  - `gh pr view <number> --json number,url,headRefName,baseRefName,mergeable,mergeStateStatus`
  - `gh pr checks <number>`
- PR creation:
  - `gh pr create --base <base> --head <branch> --title <title> --body <body>`
  - draft variant: `gh pr create --draft ...`

## Guardrails

- Never keep new work on the default branch once the user asks to open a PR.
- Do not create a second feature branch if the current branch is already a
  feature branch.
- Reuse an existing open PR for the same branch instead of creating duplicates.
- Do not push directly to the default branch.
- Do not merge the PRs into the default branch unless the user explicitly asks.
  This skill prepares PRs so the user can merge them manually.
- Do not open a PR from uncommitted changes alone. Commit first or stop.
- For local-work PRs, do not stage only a subset of the worktree unless the
  user explicitly asks for a narrower PR. The default staging command is
  `git add -A` from the repository root so monorepo-wide related changes stay
  together.
- Do not open an empty PR with no branch diff against the base branch.
- Do not combine multiple requested branches into one PR.
- Do not rewrite published branch history unless the user explicitly asks and
  the branch is safe to rewrite.
- Do not treat a conflict-free current-base PR as enough when the user asked
  for multiple branches. Also check the proposed manual merge order.
- Do not resolve semantic conflicts by guessing. Prefer a small merge commit
  that preserves both branch and base behavior, or stop and report the blocker.
- Do not normalize or reformat generated, vendored, or exact upstream-imported
  files just because a conflict was nearby.
- When the default branch is clean, do not branch from a stale local copy if it
  can be safely fast-forwarded to `origin/<base>` first.
- If local uncommitted work is present on the default branch, create the new
  branch first so those changes move off the default branch safely.
- Do not let a suggested branch slug such as `prep/<topic>` determine the PR
  title when the user supplied a title explicitly.
- Prefer a draft PR over a misleading ready-for-review PR when the work is
  intentionally still in progress.

## Output Contract

When using this skill:

1. Create or reuse the correct working branch or target branches.
2. Make each target branch conflict-free against the base branch when safe.
3. Validate the ordered multi-branch merge path when more than one branch is
   requested.
4. Push each branch if needed.
5. Create or reuse the GitHub PR for each branch.
6. Return the PR numbers, URLs, merge order, validation performed, and any
   conflict-resolution commits.
7. Call out any blockers, such as detached `HEAD`, missing `gh` auth, unknown
   branch names, unresolved conflicts, failing checks, or no diff against the
   base branch.
