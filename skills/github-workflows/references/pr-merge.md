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

- Require explicit trusted operator dispatch and local exact-head/base COMMENT
  review for every merge, including Dependabot and Docker updates.
- PR events, CI events and schedules may report Required CI, never initiate merges.
- Review the complete diff, breaking changes, security/dependency information and
  meaningful validation; safely repaired heads and changed bases need fresh review.
- `pull_request_target` is acceptable only for metadata observation without PR checkout.
- Pass dynamic expressions through step-level environment variables before shell use.
- Keep write permissions limited to workflows that approve, label, comment, or merge.
- Route approval/merge through the protected merge templates and merge-pr owner.
- The built-in token cannot enqueue queues; report a maintainer blocker.
- Explicitly dispatch exact-result CI and configured Pages builds after token merges; push events are suppressed.

## Repo examples

- `.github/workflows/nebius-cxcli-ci.yml`: compact PR + main merge CI with build verification.
- `.github/workflows/vpngw-ci.yml`: split lint/unit/manual integration pipeline.
- `.github/workflows/skills-merge-pr.yml`: CI observation and explicitly dispatched reviewed merges.
