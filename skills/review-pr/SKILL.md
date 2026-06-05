---
name: review-pr
description: "Use for GitHub PR review by number, URL, or current branch: inspect base/head, issues, checks, reviews, conflicts, and changed files; fix safe issues or conflicts on writable branches; route to sibling skills; report merge readiness and blockers."
---

# Review PR

Use this skill for GitHub-backed pull request review work when the goal is to
inspect any provided PR, identify real issues, fix safe problems when branch
permissions allow, and leave it closer to merge-ready.

## Use This Skill For

- Reviewing an open PR by number, URL, or current branch against its base
  branch, usually `main`.
- Reviewing PRs opened from the user's branch, another same-repository branch,
  or a fork.
- Checking whether the branch is mergeable and whether CI or review blockers
  remain.
- Fixing safe code, test, workflow, or documentation issues directly on the PR
  branch when the branch can be safely updated.
- Resolving straightforward conflicts with `main` or the PR base branch when
  the correct resolution is clear and push permissions allow it.
- Reporting review findings and exact blockers when the branch cannot be
  updated safely.

## Requirements

- A Git repository with the PR branch available locally, fetchable from
  `origin`, or available through `gh pr checkout` for same-repository or fork
  PRs.
- GitHub CLI (`gh`) authenticated for the target repository.

## Sibling Skill Routing

`review-pr` should stay the coordinator skill for PR review, but it should pull
in the smallest relevant set of sibling skills based on the actual PR surface.
Do not load unrelated skills just because they exist.

Route selectively like this:

- `align`: when the PR spans multiple surfaces and needs end-to-end alignment
  across implementation, tests, docs, help output, CI, or examples.
- `github-workflows`: when the PR changes `.github/workflows/**`, reusable
  workflow behavior, release automation, or merge/publish gates.
- `helmchart`: when the PR changes Helm charts, including `Chart.yaml`,
  `values.yaml`, templates, schema, or chart publication contracts.
- `python-project`: when the PR centers on Python packaging, `pyproject.toml`,
  `src/`, CLI structure, pytest, Ruff, or general Python project hygiene.
- `shell-scripting`: when the PR changes `.sh` files, shell helpers, or Bash
  CLI flows.
- `linter`: when the PR needs shell, Markdown, or Python lint cleanup as part
  of making the branch merge-ready.
- `nebius`: when the PR depends on live Nebius IAM, VPC, quota, MK8s, or SDK
  behavior.
- `onboard-nebius-cxcli`: when the PR changes `services/nebius-cxcli` onboarding
  contracts such as `component_sources.yaml`, validation/runtime wiring, or
  bundled module/chart onboarding.
- `terraform`: when the PR is mainly about Terraform module or environment
  structure, interfaces, validation, or security posture.
- `publish-release`, `publish-image`, or `publish-helm`: when the PR changes a
  release helper plus its matching publication workflow and changelog contract.

If the PR is narrow, use only the matching domain skill. If the PR is broad,
use `align` plus the one or two domain skills that cover the specialized
surfaces.

## Workflow

1. Identify the PR, head branch, and base branch.
   Use a supplied PR number or URL when available. Otherwise resolve the PR
   from the current branch. Review against the PR base branch by default,
   usually `main`. If the user explicitly asks for `main` but the PR targets a
   different base, call out the mismatch before changing the target branch.
   Collect:
   - PR number and URL
   - title and author
   - head branch
   - head repository and owner
   - base branch
   - whether the PR is from a fork or same-repository branch
   - whether maintainers can modify the branch
   - draft state
   - merge status
   - check status
   - review decision and unresolved reviewer concerns when visible
2. Inspect the review surface.
   Read the changed files, diff, existing review comments, and failing checks.
   Compare the branch locally against `origin/<base>`, not only against the PR
   summary UI.
3. Select sibling skills for the changed surface.
   Based on the files touched and the kind of breakage in the PR, explicitly
   apply the smallest relevant set of sibling skills from this repo. Keep
   `review-pr` as the owner of readiness, branch updates, and final merge
   judgment.
4. Establish branch ownership, permissions, and update strategy.
   Decide whether the branch can be updated and whether history is safe to
   rewrite.
   - If the PR is from another contributor or a fork, assume the branch is
     externally owned unless the user says otherwise and GitHub shows the
     branch is editable by maintainers.
   - If the branch is not writable from the current credentials, perform the
     review locally and report findings, conflict details, and suggested
     changes. Do not claim conflicts were resolved upstream.
   - If the branch is writable but externally owned or shared, prefer
     non-destructive merge updates from `origin/<base>` or GitHub's update
     branch flow.
   - If the branch is clearly user-owned or automation-owned in the current
     repository workflow, rebasing can be acceptable when it applies cleanly.
   - If the branch may be shared, externally owned, or under active parallel
     collaboration, stop before rewriting history unless the user explicitly
     asks for it and branch ownership is clear.
5. Review with code-review priorities first.
   Focus on:
   - incorrect behavior
   - regressions
   - missing tests
   - stale docs or help text
   - broken workflows or release paths
6. Fix safe issues on the branch.
   When the required fix is clear and the branch is writable, implement it
   instead of stopping at a report. Keep tests, docs, and automation aligned
   with the code changes. If the branch is not writable, report the exact fix
   or patch shape without creating a misleading local-only resolution.
7. Run focused validation.
   Choose validation based on the files changed:
   - Python: `ruff`, focused `pytest`
   - shell: `bash -n`, `shellcheck`
   - workflows: `actionlint`
   - Helm charts: `helm lint`, `helm template`
   - docs-only: focused markdown validation when available
8. Resolve branch drift or conflicts when safe.
   If the PR branch is behind, conflicted, or GitHub reports it cannot merge
   cleanly:
   - fetch the base branch
   - check out the PR branch with `gh pr checkout <pr>` or the existing local
     branch
   - test the merge locally before committing conflict-resolution work
   - prefer non-destructive update paths first when branch ownership is unclear,
     including `gh pr update-branch <pr>` when GitHub allows it
   - merge `origin/<base>` into the PR branch when a same-branch merge commit is
     the safest writable update path
   - use local rebase only when the branch history is safe to rewrite
   - resolve only straightforward conflicts automatically
   - rerun focused validation after conflict resolution
   - push branch updates back, using `--force-with-lease` only when a rebase
     made it necessary
   - if the branch cannot be pushed, keep the local conflict analysis and report
     the unresolved files plus the safest next action
9. Report readiness.
   Return findings first, then summarize:
   - what was fixed
   - which sibling skills were applied and why
   - what validation ran
   - whether the PR is ready to merge
   - any remaining blockers

## Recommended Commands

- PR metadata:
  - `gh pr view <pr-or-url> --json number,url,title,author,headRefName,headRepository,headRepositoryOwner,baseRefName,isCrossRepository,isDraft,maintainerCanModify,mergeable,mergeStateStatus,reviewDecision,statusCheckRollup`
- Changed files:
  - `gh pr diff <pr-or-url> --name-only`
  - `gh pr view <pr-or-url> --json files`
- Checkout:
  - `gh pr checkout <pr-or-url>`
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
- Push:
  - same-repository branch: `git push origin HEAD:<head-branch>`
  - fork branch when GitHub created a writable remote:
    `git push <head-remote> HEAD:<head-branch>`
  - after an intentional safe rebase:
    `git push --force-with-lease <head-remote> HEAD:<head-branch>`

## Guardrails

- Default behavior is review-and-fix, not report-only, unless the user asks for
  audit-only output.
- A PR link or number is authoritative. Do not ignore it and review the current
  branch instead.
- Use sibling skills selectively by changed surface; do not turn every PR review
  into a full-repo multi-skill pass.
- Do not claim a PR is ready if checks are failing, conflicts remain, or review
  blockers are unresolved.
- Do not claim conflicts are resolved unless the conflict-resolution commit was
  pushed to the PR branch or GitHub confirms the branch is mergeable after the
  update.
- Do not clear or ignore unresolved reviewer concerns without evidence that the
  branch now addresses them.
- Resolve conflicts automatically only when the correct merge is obvious from
  local context. Stop and explain when conflicts are semantic or risky.
- Never rewrite the default branch.
- Do not push to another contributor's branch or fork unless GitHub permits it,
  the user asked for branch updates, and the update is non-destructive or the
  user explicitly approved the rewrite.
- Do not create a replacement PR from someone else's branch unless the user
  explicitly asks for that follow-up path.
- Do not rewrite shared or externally owned branch history unless the user
  explicitly wants that and the ownership is clear.
- When a rebase is required on a branch you control, use `--force-with-lease`
  rather than a blind force push.
- Do not approve or merge the PR unless the user explicitly asks for that
  separate step.
- Do not let a sibling skill override `review-pr`'s ownership of readiness,
  branch safety, or final review judgment.
- Keep the PR branch aligned across code, tests, docs, and workflows before
  calling it ready.

## Output Contract

When using this skill:

1. Review the PR against the base branch.
2. Identify whether the PR branch is same-repository, forked, writable, and
   safe to update.
3. Apply the smallest relevant set of sibling skills for the changed surface.
4. Fix safe issues directly on the PR branch when appropriate and writable.
5. Resolve straightforward conflicts when safe and push permissions allow it.
6. Run focused validation and report what actually ran.
7. State clearly whether the PR is ready to merge and what, if anything, still
   blocks it.
