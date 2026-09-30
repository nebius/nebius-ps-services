<!-- Staging and commits in this cookbook are effects owned by the canonical
shared commit helper; never run them as raw agent Git commands. Read
commit-continuation.md before preparing local commits. -->

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
  - private `prepare`, complete candidate review, then `execute`
  - the helper owns `git add -A`, staged checks and normal-hook commit

## Conflict And Base Merge

- Conflict checks:
  - `git merge-tree --write-tree origin/<base> <branch-or-origin/branch>`
  - `git diff --name-only --diff-filter=U`
  - `rg -n '^(<{7}|={7}|>{7})'`
- Base branch merge before PR creation:
  - `git fetch origin`
  - `git merge-tree --write-tree origin/<base> HEAD`
  - private `sync`, review/check its actual tree, then `sync --reviewed-tree <tree>`
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
  - private `prepare`, complete candidate review, then `execute`
  - the helper owns `git add -A`, staged checks and normal-hook commit
  - `git fetch origin`
  - `git merge-tree --write-tree origin/<base> HEAD`
  - private `sync`, review/check its actual tree, then `sync --reviewed-tree <tree>`
  - rerun focused validation after merge
  - new remote branch: `git push -u origin HEAD:<branch>`
  - existing remote branch: `git push origin HEAD:<branch>`
  - `gh pr create --base <base> --head <branch> --title <title> --body <body>`

## Ordered Merge Simulation

Use the canonical installed transaction helper's private `validate-order` with
its existing task key and ordered `--branch` arguments. Intake must already
have frozen `--validation-branch`. It records source SHAs, performs normal-hook
merges on that ref and restores/removes only its exact recorded state. Do not
run ad hoc switches, merge aborts or scratch-ref deletion around the helper.

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
