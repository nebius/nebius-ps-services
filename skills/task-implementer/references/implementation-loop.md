# Dependency-Wave Implementation Loop

Read this reference before task decomposition, scheduling, worker dispatch,
integration, promotion, cleanup, or recovery.
The dependency wave is the logical unit of parallelism and atomic promotion.

## Task Contract

Inspect the repository before locking IDs. Normalize the prompt into stable
`TI-REQ-nnn` requirements and stable `task-1..task-n` records. Each pending
task must contain:

- source revision and requirement/design mapping;
- priority and dependencies;
- goal, rationale, plan, implementation steps, and rollback/stop conditions;
- write claims using `exact: repo/path` or `prefix: repo/directory`;
- conflict domains using `class:stable-key`;
- focused validation, end-to-end validation, and done criteria.

Combine overlapping work before IDs lock when one worker can produce one
coherent result. Otherwise add an explicit dependency and serialize it. Never
renumber locked IDs or rewrite completed evidence.

## Conflict Analysis

Two tasks cannot share a wave when they:

- write the same path or overlapping directory prefix;
- modify the same core interface or API;
- change the same database schema or migration chain;
- touch the same dependency manifest or lockfile;
- depend on a new shared abstraction;
- modify the same Kubernetes or Terraform resource identity;
- require the same exclusive test resource;
- perform external mutations; or
- make incompatible architecture assumptions.

Unknown claims or domains force a singleton wave. Live database, Kubernetes,
Terraform, migration execution, and publication actions are singleton even
when their resource keys differ, and they retain their own explicit authority
requirements.

## Deterministic Scheduling

Use stable queue order and earliest-fit placement:

1. Validate all dependency references and reject self-dependencies or cycles.
2. A task may enter only a wave strictly after every dependency.
3. Starting at the earliest eligible wave, place it in the first wave whose
   claims and domains are pairwise disjoint.
4. If ownership is incomplete, place it alone.
5. Record logical waves before considering runtime worker capacity.
6. Split a wide logical wave into stable capacity-sized dispatch batches. Batch
   boundaries do not become dependencies and do not change the wave ID.

For the standard fixture, tasks 1-5 form wave 1, tasks 6-7 form wave 2, then
tasks 8, 9, and 10 form singleton waves.

## Preparing A Wave

Require a clean named project branch and exact `HEAD`. Preflight the private
root and Git common directory. Reject claims crossing a submodule/gitlink with
`UNSUPPORTED_SUBMODULE_SCOPE`; do not initialize submodules. Reject claims
crossing a tracked symlink with `UNSUPPORTED_SYMLINK_SCOPE` rather than treating
lexical ownership as filesystem containment.

Journal intent before each Git mutation, execute argv-based Git without a
shell, then record return status and observed `HEAD`. Create a unique sanitized
integration branch and full-repository worktree from the exact project base,
then lock it. Never include prompt text or secrets in branch names.

The coordinator preallocates managed requirement/design IDs and records. Shared
specifications, README/design documentation, and changelog are
coordinator-owned. If this changes tracked files, create one locked contract
commit in the integration worktree. Every worker branch starts at that exact
commit.

## Assigning Workers

Create one locked full-repository worktree and validated unique branch per task.
The assignment records the worker's absolute scope cwd. For a monorepo scope
`services/nebius-cxcli`, the cwd is
`<task-worktree>/services/nebius-cxcli`, not a service-only checkout.

Reserve the main thread for coordination. Prefer native worker agents up to
available capacity. If native agents are unavailable, start fresh sequential
`codex exec` workers in the same isolated worktrees. Never let the coordinator
implement worker tasks. After one worker fails, stop dispatching new batch
members but allow already-active workers to finish and report.

Each worker must:

1. Read its immutable assignment and verify its digest.
2. Verify the incoming-handoff path and digest, then verify absolute cwd, real worktree root, branch, exact base SHA, clean state,
   scope, claims, and conflict domains.
3. Stop with `REPLAN_REQUIRED` before editing if an undeclared path or domain
   is needed.
4. Implement exactly one task without touching the primary checkout, shared
   handoff/spec/docs, other refs/worktrees, Git maintenance, or external state.
5. Run focused and end-to-end validation.
6. Invoke `code-review`, fix safe scoped findings, and revalidate.
7. Invoke `$commit` exactly once. The task branch must contain exactly one
   direct-child commit from the common contract base.
8. Write one private result with assignment digest, status, commit, exact paths,
   summary, decisions, open risks, validation, end-to-end evidence, and review
   evidence. Stop. Never reuse this worker session for another task.

Task `committed` means ready for integration, not done.

## Verifying And Integrating

The coordinator independently verifies every result:

- assignment/result identities and immutable digests match;
- worktree and branch identities match the assignment;
- branch is clean and its `HEAD` is the reported commit;
- exactly one commit descends directly from the common base;
- actual changed paths exactly match the result and fit locked claims;
- validation and review evidence are present.

After all tasks are committed, merge branches into the integration branch in
stable task-ID order:

```text
git merge --no-ff --no-edit <task-branch>
```

Never cherry-pick, rebase, squash, push, or merge directly into the project
branch. An unexpected conflict aborts the merge, marks the wave blocked, leaves
the project branch unchanged, and retains all worktrees/branches.

After worker merges, the coordinator may update only shared managed specs,
README/design docs, and changelog evidence. Make a final integration commit
only for a non-empty shared-file diff. Any product-code correction becomes a
new isolated task.

## Validation And Promotion

Run combined validation and integration `code-review` in the integration
worktree. Reconcile all queued steering. If steering contradicts the active
wave, preserve the integration branch and stop before promotion.

Recheck that the primary checkout is clean, on the recorded named branch, at
the recorded base. Promote atomically:

```text
git merge --ff-only <verified-integration-SHA>
```

Verify the integration branch still identifies that SHA. If Git reports
failure, re-observe project `HEAD` and classify it as unchanged,
successfully promoted, or unexpectedly moved. Never reset, rebase, force, or
repeat blindly.

Only after the project `HEAD` equals the verified integration tip may the
coordinator mark wave tasks `done` in `handoff.md`. Dependency satisfaction is
promotion-based, not worker-result-based.

## Cleanup

After verified promotion, prove every temporary branch is reachable from the
promoted project `HEAD`. For each clean managed worktree:

1. unlock the exact path;
2. run non-force `git worktree remove <path>`;
3. run `git branch -d <branch>`.

Remove task resources before the integration resource. Never use `--force`,
`rm -rf`, broad `git worktree prune`, or `git gc`. Dirty, unreachable, or
failed resources remain recorded. Cleanup failure does not roll back promotion.

## Resume And Failure Classification

A repeated `run` loads coordinator, wave, assignment, result, journal, and Git
truth before choosing a transition. It must converge after interruptions such
as:

- intent journal written but worktree command outcome unknown;
- worktree created but state update interrupted;
- worker commit exists but result acceptance interrupted;
- worker stopped with declared dirty state or one direct-child commit;
- some ordered merges completed;
- fast-forward succeeded but private promotion state did not update;
- one worktree or branch cleaned before interruption.

Idempotent retries re-observe exact refs and worktrees. They never recreate a
foreign collision, duplicate a commit/merge, overwrite a divergent immutable
assignment/result, or force cleanup.

For an interrupted running task, require explicit confirmation that the old
worker stopped before transferring the locked assignment to a fresh session.
Recovery accepts only the locked base, one direct-child commit, and dirty paths
inside the assignment claims. A blocked task or undeclared path stays retained
for operator-directed recovery; do not delete or replace unmerged evidence.

Failure rules:

- worker failure: retain its exact branch/worktree; stop new dispatches;
- scope expansion: `REPLAN_REQUIRED` before edit/commit;
- merge conflict: abort integration merge and retain the wave;
- validation/review failure: retain integration state before promotion;
- primary branch drift: `PROMOTION_BLOCKED` with no mutation;
- promotion uncertainty: classify observed `HEAD` before retry;
- cleanup failure: report retained inventory after successful promotion;
- missing safe local bootstrap: `ENVIRONMENT_BLOCKER` without copying primary
  checkout state;
- unfinished v1 state: `WORKFLOW_UPGRADE_REQUIRED`, no migration shim.

## Final Run Completion

Start the next planned wave from the newly promoted project `HEAD`. After the
last wave is promoted and safely cleaned, run changed-surface `$align`, verify
managed specification state, record final evidence, and invoke the private run
finalizer. The finalizer sets the handoff to `done` and releases a managed outer
worktree lease only when the outer branch is clean at the final promoted head
and all internal resources are absent. If release is interrupted, repeat the
same finalizer; do not start a new run or clear state. Static validation and
observed live/runtime proof must be reported separately.
