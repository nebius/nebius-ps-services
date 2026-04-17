---
name: create-pr
description: Create or reuse a feature branch, push it, open or reuse a GitHub pull request, and return the PR number and URL. Use when a user wants local work turned into a reviewable PR without hand-driving the branch and GitHub steps.
---

# Create PR

Use this skill to turn local repository work into a GitHub pull request with a
safe default-branch workflow.

## Use This Skill For

- Opening a PR from local changes or local commits.
- Moving in-progress work off the default branch before publishing it.
- Reusing the current feature branch instead of creating extra branches.
- Returning the PR number and URL so the user can review or merge it.

## Requirements

- A Git repository with an `origin` remote.
- GitHub CLI (`gh`) authenticated for the target repository.

## Workflow

1. Inspect repository state first.
   Determine:
   - current branch
   - whether `HEAD` is detached
   - repository default branch
   - whether the worktree has changes
   - whether the current branch already has an upstream
   - whether the current branch is ahead of or behind `origin/<base>`
2. Refresh base-branch context.
   Fetch `origin/<base>` before deciding how to branch or open the PR. If the
   local default branch is clean and has no local-only commits, fast-forward it
   first so the new branch starts from the latest reviewed base.
3. Resolve the working branch.
   - If `HEAD` is detached, stop and explain the problem.
   - If the current branch is the default branch, create a new branch from it.
     Prefer a short user-provided slug such as `prep/<project-tag>` or
     `fix/<topic>`. Ensure the branch name does not collide with an unrelated
     existing local or remote branch.
   - If the current branch is already a non-default branch, reuse it. Do not
     create another branch.
4. Make the branch reviewable.
   A PR must come from committed changes, not only a dirty worktree.
   - If the worktree is dirty on the default branch, create the feature branch
     first so the in-progress work moves off the default branch safely.
   - If the worktree is still dirty after branch selection, either help commit
     the current diff when the user clearly wants to submit it, or stop and
     explain that a PR cannot be created until the branch has reviewable
     commits.
5. Confirm there is something to review.
   Compare the working branch with `origin/<base>`. If there is no diff and no
   unpublished commits, stop instead of creating an empty PR.
6. Publish the branch.
   If the branch has no upstream yet, push it with upstream tracking.
7. Avoid duplicate PRs.
   Check for an existing open PR for the current head branch. If one already
   exists, return that PR instead of creating another.
8. Open the PR with the right readiness state.
   Use explicit user-provided title/body when available. Otherwise prefer a
   concise generated title/body or `gh pr create --fill` when the commit
   history is clean enough to support it. Prefer a draft PR when the branch is
   intentionally incomplete or validation has not run yet.
9. Return the result.
   Report:
   - head branch name
   - base branch name
   - PR number
   - PR URL

## Recommended Commands

- Default branch detection:
  - `gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name'`
  - fallback: `git symbolic-ref --short refs/remotes/origin/HEAD | sed 's#^origin/##'`
- Existing PR lookup:
  - `gh pr list --head <branch> --state open --json number,url,headRefName,baseRefName`
- PR creation:
  - `gh pr create --base <base> --head <branch> --title <title> --body <body>`
  - draft variant: `gh pr create --draft ...`

## Guardrails

- Never keep new work on the default branch once the user asks to open a PR.
- Do not create a second feature branch if the current branch is already a
  feature branch.
- Reuse an existing open PR for the same branch instead of creating duplicates.
- Do not push directly to the default branch.
- Do not open a PR from uncommitted changes alone. Commit first or stop.
- Do not open an empty PR with no branch diff against the base branch.
- When the default branch is clean, do not branch from a stale local copy if it
  can be safely fast-forwarded to `origin/<base>` first.
- If local uncommitted work is present on the default branch, create the new
  branch first so those changes move off the default branch safely.
- Prefer a draft PR over a misleading ready-for-review PR when the work is
  intentionally still in progress.

## Output Contract

When using this skill:

1. Create or reuse the correct working branch.
2. Push the branch if needed.
3. Create or reuse the GitHub PR.
4. Return the PR number and URL.
5. Call out any blockers, such as detached `HEAD`, missing `gh` auth, or no
   diff against the base branch.
