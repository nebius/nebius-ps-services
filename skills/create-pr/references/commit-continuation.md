# Commit continuation for one PR task

An explicit unmanaged PR task covers necessary reviewed commits, base merges,
checks and pushes for its selected targets. Do not demand another standalone
commit invocation after a safe failed attempt or a branch-owned repair.
Discussion, help and requests to edit skill source do not authorize Git effects.
Plain create-pr ends at the reviewed PR. An explicit combined create/review-and-merge
request keeps the original root receipt, targets, grant and readiness deadline
through merge-pr and its parent-owned review-pr call. Only the completion owner
closes at the selected boundary; no handoff mints a new authorization.

An explicit complete `publish-release`, `publish-helm` or `publish-image` request delegates its necessary PR,
validated repository-wide commits, synchronization and pushes to this owner.
Use the original release prompt receipt with the existing `create-pr` transaction;
no separate PR/commit invocation or release-specific grant is required. Let the
caller prepare release metadata before reviewing the complete candidate. Return
the final locally reviewed PR/head to the caller before checkpoint binding and
merge-pr. Authorized review-pr/merge-pr repairs use the same private preparation
handoff and active grant; this path cannot recursively review or merge. A checkpoint never replaces authorization; fresh-session commit work
requires fresh canonical intake after reconciliation of uncertain prior effects.

Read [PR publication](pr-publication.md) for existing PR bindings, fork intake
and every ordinary target push.

Read [the shared private task lifecycle](../../commit/references/task-lifecycle.md)
before preparing effects. Active SDLC and delegated local workflows retain their
own authority and do not use this root-task protocol.

1. Run the installed Worktree publication guard and resolve the default/base,
   all selected local or remote-only branches, any planned feature, dependencies
   and the temporary validation ref. Fetching refs may precede intake; switching
   branches or moving HEAD must not.
2. Read [checkout completion](checkout-completion.md), then call private `begin` with the original root receipt and complete frozen scope,
   even for an initially clean branch. From the default branch, create the
   selected feature through `checkout-target` at the receipt HEAD before sync.
3. Validate and review the complete repository candidate. Repeat
   `prepare`/candidate review/`execute` with the same task key for needed repairs.
   Each attempt uses a fresh token; staging remains repository-root `git add -A`
   and commits run normal hooks. Standalone one-commit limits do not restrict
   the selected PR task's necessary repair commits.
4. Use private `sync` for base or declared dependency merges. Review each actual
   result and run affected checks before acknowledging its exact tree. Use
   `validate-order` for multiple targets; retain conflict/interruption evidence
   until exact cleanup is proved. Never publish the scratch ref.
5. After final base synchronization, delegate nested read-only code-review of
   the complete final candidate and affected callers. Renew validation/review
   after changes. Publish only the reviewed target through private `publish-target`, inspect current-head
   checks, and continue safe repairs under the same task until complete.
6. Run outer-owner `complete-checkout --mode finalize` under the inherited policy,
   then call private `finish --outcome completed` only at the selected public
   completion boundary, including zero-commit tasks. Never close it during a
   private preparation handoff or delegated review repairs. Use `finish --outcome cancelled` when abandoning the
   task. Actual uncertain effects remain available for exact metadata-only
   review; closure never authorizes new effects.

The immutable grant binds action, original receipt, native session, worktree,
common directory, target refs, effective origin destinations, base and declared
dependencies and checkout policy. Mutable publication checkpoints record proved
commits and reviewed synchronization; completion effects have a separate journal. A scope change or unexplained history is a reconciliation
problem, not permission to mint authority from a later unrelated prompt.

Disposable repository tests cover clean starts, fast-forwards, multiple targets,
retries, cancellation, interruption and scratch cleanup. This is deterministic
source evidence; installed parity and native hook dispatch are separate claims.
Passing checks alone never replace local review or protected Actions approval.
Existing human requested changes remain blockers. Agentic SDLC is excluded.
