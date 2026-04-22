---
name: review-pr
description: Review a GitHub pull request against its base branch, fix safe issues on the PR branch, resolve safe conflicts when possible, and report whether the PR is ready to merge. Use when a user wants more than an audit and expects the branch to be brought into shape.
---

# Review PR

Use this skill for GitHub-backed pull request review work when the goal is to
inspect the PR, fix real issues, and leave it closer to merge-ready.

## Use This Skill For

- Reviewing an open PR against its base branch, usually `main`.
- Checking whether the branch is mergeable and whether CI or review blockers
  remain.
- Fixing safe code, test, workflow, or documentation issues directly on the PR
  branch.
- Resolving straightforward conflicts with the base branch when the correct
  resolution is clear.

## Requirements

- A Git repository with the PR branch available locally or fetchable from
  `origin`.
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
- `onboard-nbs-cxcli`: when the PR changes `services/nebius-cxcli` onboarding
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

1. Identify the PR and base branch.
   Use a supplied PR number or URL when available. Otherwise resolve the PR
   from the current branch. Collect:
   - PR number and URL
   - head branch
   - base branch
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
4. Establish branch ownership and update strategy.
   Decide whether the branch history is safe to rewrite.
   - If the branch is clearly user-owned or automation-owned in the current
     repository workflow, rebasing can be acceptable.
   - If the branch may be shared, externally owned, or under active parallel
     collaboration, prefer non-destructive update paths or stop before
     rewriting history.
5. Review with code-review priorities first.
   Focus on:
   - incorrect behavior
   - regressions
   - missing tests
   - stale docs or help text
   - broken workflows or release paths
6. Fix safe issues on the branch.
   When the required fix is clear, implement it instead of stopping at a report.
   Keep tests, docs, and automation aligned with the code changes.
7. Run focused validation.
   Choose validation based on the files changed:
   - Python: `ruff`, focused `pytest`
   - shell: `bash -n`, `shellcheck`
   - workflows: `actionlint`
   - Helm charts: `helm lint`, `helm template`
   - docs-only: focused markdown validation when available
8. Resolve branch drift when safe.
   If the PR branch is behind or conflicted:
   - fetch the base branch
   - prefer non-destructive update paths first when branch ownership is unclear
   - use local rebase only when the branch history is safe to rewrite
   - resolve only straightforward conflicts automatically
   - rerun focused validation after conflict resolution
   - push branch updates back, using `--force-with-lease` only when a rebase
     made it necessary
9. Report readiness.
   Return findings first, then summarize:
   - what was fixed
   - which sibling skills were applied and why
   - what validation ran
   - whether the PR is ready to merge
   - any remaining blockers

## Recommended Commands

- PR metadata:
  - `gh pr view <pr> --json number,url,title,headRefName,baseRefName,isDraft,mergeStateStatus,reviewDecision,statusCheckRollup`
- Changed files:
  - `gh pr diff <pr> --name-only`
  - `gh pr view <pr> --json files`
- Local base sync:
  - `git fetch origin <base>`
  - `git rebase origin/<base>`
  - non-destructive alternative when appropriate: `gh pr update-branch <pr>`

## Guardrails

- Default behavior is review-and-fix, not report-only, unless the user asks for
  audit-only output.
- Use sibling skills selectively by changed surface; do not turn every PR review
  into a full-repo multi-skill pass.
- Do not claim a PR is ready if checks are failing, conflicts remain, or review
  blockers are unresolved.
- Do not clear or ignore unresolved reviewer concerns without evidence that the
  branch now addresses them.
- Resolve conflicts automatically only when the correct merge is obvious from
  local context. Stop and explain when conflicts are semantic or risky.
- Never rewrite the default branch.
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
2. Apply the smallest relevant set of sibling skills for the changed surface.
3. Fix safe issues directly on the PR branch when appropriate.
4. Resolve straightforward conflicts when safe.
5. Run focused validation and report what actually ran.
6. State clearly whether the PR is ready to merge and what, if anything, still
   blocks it.
