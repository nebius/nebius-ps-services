# Supplemental Process Cases

`trigger-prompts.csv` is the canonical trigger authority. These process cases
exercise the existing agent's owner handoffs; executable helper tests cannot prove
native invocation, commit-owner delegation or real GitHub publication.

## One invocation

- Explicit bare invocation selects complete and wait. Version is supplied,
  derived from unambiguous prepared metadata, or asked once. Never silently bump.
- Dirty feature entry reviews every current repository change, including sibling
  projects, prepares release metadata, and uses one canonical create-pr grant.
  It stages at repository root and commits the full reviewed candidate once.
- Dirty default entry begins owner intake before selecting `release/<tag>`;
  changes are carried onto that branch without committing to default.
- Existing feature branch/PR is reused. The content-only helper never switches
  branches, changes the index, commits or pushes. No extra commit-push invocation.
- create-pr performs its normal non-rewriting synchronization and validated
  repairs before the final head is bound. A ref-like branch uses the heads namespace.
- Missing setup assets are prepared inside the same authorized branch workflow.
  Managed children and active SDLC retain their owner restrictions.

## Approval and continuation

- Required review prints its GitHub link, polls every 15 seconds, and emits
  progress at least every minute. Approval at 590 seconds continues after refresh;
  lack of approval at 600 seconds returns a timeout and exact resume command.
- Changes requested, failed checks, conflicts, closed PRs and API errors are not
  reported as waiting for approval. No self-approval or admin bypass is attempted.
- Partial environment approval and reordered pending-environment responses do
  not renew the release run's fixed approval deadline.
- Pending checks, queue and workflow each have bounded one-hour phases. A queued
  PR never permits tagging. Removed queue entries are not silently re-enqueued.
- Approval after timeout followed by explicit resume reuses the pushed PR/head,
  refreshes all gates, and continues without a duplicate commit or PR.
- Fresh-session resume never replays a native-session-bound commit grant.
  Additional local commit work uses fresh owner intake after effect reconciliation.

## Exact publication and recovery

- Squash/rebase results differ from the feature head; use the verified resulting
  commit. Default advancement afterward does not change the release commit.
- Tag in a clean isolated clone after default-history and exact-result CI proof.
  Preserve source checkout, local tags and current user work.
- Runtime version must match after annotated-tag creation and before tag push.
  On mismatch delete only the exact task-created unpushed local tag.
- Checkpoint the exact annotated object before pushing. Clone loss before push
  restores the same object bytes; never mint a timestamp-different replacement.
- Interrupted push rechecks remote object and peeled commit. Matching publication
  continues; collision blocks. Multiple or changed push destinations fail closed.
- Match the frozen workflow, push event, tag, commit and run. A successful run with
  missing assets or a draft release does not count as published.
- Download expected assets and verify versions, sizes and available digests.
  Repeating completed invocation verifies current evidence without new publication.
- Explicit no-wait or partial modes report their checkpoint, not full success.

## Evidence lanes

Run deterministic Python/shell tests with disposable local origins and mocked
GitHub responses. In a separately authorized disposable GitHub target, validate
one dirty feature/default start and an approval timeout/resume under actual branch
protections. Source tests and copied-skill parity do not establish those live or
native-agent outcomes. Never publish to a real project to satisfy local checks.
