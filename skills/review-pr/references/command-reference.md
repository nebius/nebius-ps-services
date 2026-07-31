# Review PR Command Reference

Read this file when `review-pr` needs exact Git or GitHub CLI commands for PR
metadata, checkout, local base sync, conflict detection, or branch updates.

## PR Inspection

- PR metadata:
  - `gh pr view <pr-or-url> --json number,url,title,author,headRefName,headRepository,headRepositoryOwner,baseRefName,isCrossRepository,isDraft,maintainerCanModify,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup`
- Changed files:
  - `gh pr diff <pr-or-url> --name-only`
  - `gh pr view <pr-or-url> --json files`
- Checkout:
  - `gh pr checkout <pr-or-url>`

## Base Sync And Conflicts

- Local base sync:
  - `git fetch origin <base>`
  - `git merge-tree --write-tree origin/<base> HEAD`
  - `git merge --no-edit origin/<base>`
  - rebase only when safe: `git rebase origin/<base>`
  - non-destructive alternative when appropriate:
    `gh pr update-branch <pr-or-url>`
- Conflict checks:
  - `git diff --name-only --diff-filter=U`
  - `rg -n '^(<{7}|={7}|>{7})'`

## Push

- Push:
  - same-repository branch: `git push origin HEAD:<head-branch>`
  - fork branch when GitHub created a writable remote:
    `git push <head-remote> HEAD:<head-branch>`
  - after an intentional safe rebase:
    `git push --force-with-lease <head-remote> HEAD:<head-branch>`
