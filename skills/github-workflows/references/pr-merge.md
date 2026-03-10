# PR And Merge Workflows

Use this reference for service CI and merge automation.

## Service CI pattern

Prefer this trigger set for service-scoped CI:

- `pull_request` for contributor feedback
- `push` to the main branch when merged commits must be revalidated
- `workflow_dispatch` for manual reruns or smoke execution

In monorepos:

- Scope triggers with `paths`.
- Include the workflow file itself in `paths`.
- Include sibling release/image workflows in `paths` when CI should validate their command path.

## Job shape

- Start with one clear verification job unless there is a real reason to split jobs.
- Split jobs only when the workflow benefits from independent status checks, gated stages, or expensive optional jobs.
- If building release artifacts later depends on CI, add a build step in PR CI so that path is exercised before tag time.

## Merge automation

Use merge automation narrowly.

- Bot-only workflows are preferred.
- `pull_request_target` is acceptable only when the workflow does not check out or execute untrusted PR code.
- For Dependabot auto-merge, require both actor scoping and changed-file scoping.
- Prefer auto-merging GitHub Actions ecosystem PRs only when every changed file is under `.github/workflows/` or automation-only action metadata.
- GitHub Actions major bumps can be auto-merged when the file scope is limited to workflow automation and the repository accepts bot approvals for branch protection.
- Keep write permissions limited to workflows that approve, label, comment, or merge.
- If the repository uses queued or squash merges by default, the automation should request that exact merge mode.

## Repo examples

- `.github/workflows/nebius-cxcli-ci.yml`: compact PR + main merge CI with build verification.
- `.github/workflows/vpngw-ci.yml`: split lint/unit/manual integration pipeline.
- `.github/workflows/dependabot-auto-merge.yml`: safe bot-scoped approval and auto-merge.
