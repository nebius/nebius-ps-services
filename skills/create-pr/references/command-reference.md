# Create PR Command Reference

Read this file when `create-pr` needs exact Git or GitHub CLI commands for
branch detection, validation, base merges, ordered merge simulation, PR lookup,
checks, or PR creation.

## Branch And Base

- Default branch detection:
  - `gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name'`
  - fallback: `git symbolic-ref --short refs/remotes/origin/HEAD | sed 's#^origin/##'`
- Refresh refs:
  - `git fetch origin`

## Local Validation

- Pre-test hygiene and local validation before committing:
  - `git status --short`
  - `rg -n '^(<{7}|={7}|>{7})'`
  - `git diff --check`
  - run existing formatter/lint commands for touched files when available
  - run focused local tests and wait for completion
- Complete local-work staging:
  - `git status --short`
  - `git add -A`
  - `git diff --cached --check`
  - `git diff --cached --stat`
  - `git commit -m "<concise message>"`

## Conflict And Base Merge

- Conflict checks:
  - `git merge-tree --write-tree origin/<base> <branch-or-origin/branch>`
  - `git diff --name-only --diff-filter=U`
  - `rg -n '^(<{7}|={7}|>{7})'`
- Base branch merge before PR creation:
  - `git fetch origin`
  - `git merge-tree --write-tree origin/<base> HEAD`
  - `git merge --no-edit origin/<base>`
  - rerun focused validation after the merge
  - new remote branch: `git push -u origin HEAD:<branch>`
  - existing remote branch: `git push origin HEAD:<branch>`

## Current Feature Branch Path

- Current feature-branch PR path:
  - `git branch --show-current`
  - `git status --short`
  - `git diff --check`
  - run existing formatter/lint commands for touched files when available
  - run focused local tests and wait for completion
  - `git add -A`
  - `git diff --cached --check`
  - `git diff --cached --stat`
  - `git commit -m "<concise message>"`
  - `git fetch origin`
  - `git merge-tree --write-tree origin/<base> HEAD`
  - `git merge --no-edit origin/<base>`
  - rerun focused validation after merge
  - new remote branch: `git push -u origin HEAD:<branch>`
  - existing remote branch: `git push origin HEAD:<branch>`
  - `gh pr create --base <base> --head <branch> --title <title> --body <body>`

## Ordered Merge Simulation

- Ordered merge simulation:
  - `git switch --detach origin/<base>`
  - `git switch -c tmp/pr-order-check-<short-id>`
  - `git merge --no-edit <first-branch-or-origin/first-branch>`
  - `git merge --no-edit <next-branch-or-origin/next-branch>`
  - if a simulation merge conflicts: `git merge --abort`
  - record the exact simulation tip with `git rev-parse HEAD`
  - `git switch <original-branch>`
  - `git update-ref -d refs/heads/tmp/pr-order-check-<short-id> <observed-simulation-tip>`

## Pull Request

- Existing PR lookup:
  - `gh pr list --head <branch> --state open --json number,url,headRefName,baseRefName`
- PR readiness:
  - `gh pr view <number> --json number,url,headRefName,baseRefName,mergeable,mergeStateStatus`
  - `gh pr checks <number>`
  - `gh pr checks <number> --watch`
- PR creation:
  - `gh pr create --base <base> --head <branch> --title <title> --body <body>`
  - draft variant: `gh pr create --draft ...`
