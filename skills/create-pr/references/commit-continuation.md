# Commit continuation for one PR task

An explicit unmanaged PR task covers necessary reviewed commits, base merges,
checks and pushes for its selected targets. Do not demand another standalone
commit invocation after a safe failed attempt or a branch-owned repair.
Discussion, help and requests to edit skill source do not authorize Git effects.

Read [the shared private task lifecycle](../../commit/references/task-lifecycle.md)
before preparing effects. Active SDLC and delegated local workflows retain their
own authority and do not use this root-task protocol.

1. Run the installed Worktree publication guard and resolve the default/base,
   all selected local or remote-only branches, any planned feature, dependencies
   and the temporary validation ref. Fetching refs may precede intake; switching
   branches or moving HEAD must not.
2. Call private `begin` with the original root receipt and complete frozen scope,
   even for an initially clean branch. From the default branch, create the
   selected feature at the receipt HEAD before synchronization with the base.
3. Validate and review the complete repository candidate. Repeat
   `prepare`/candidate review/`execute` with the same task key for needed repairs.
   Each attempt uses a fresh token; staging remains repository-root `git add -A`
   and commits run normal hooks. Standalone one-commit limits do not restrict
   the selected PR task's necessary repair commits.
4. Use private `sync` for base or declared dependency merges. Review each actual
   result and run affected checks before acknowledging its exact tree. Use
   `validate-order` for multiple targets; retain conflict/interruption evidence
   until exact cleanup is proved. Never publish the scratch ref.
5. Push only the verified target with `origin HEAD:<branch>`, inspect current-head
   checks, and continue safe repairs under the same task until complete.
6. Call private `finish --outcome completed` after verification, including tasks
   with zero local commits. Use `finish --outcome cancelled` when abandoning the
   task. Actual uncertain effects remain available for exact metadata-only
   review; closure never authorizes new effects.

The immutable grant binds action, original receipt, native session, worktree,
common directory, target refs, effective origin destinations, base and declared
dependencies. Mutable checkpoints record only proved commits and reviewed
synchronization. A scope change or unexplained history is a reconciliation
problem, not permission to mint authority from a later unrelated prompt.

Disposable repository tests cover clean starts, fast-forwards, multiple targets,
retries, cancellation, interruption and scratch cleanup. This is deterministic
source evidence; installed parity and native hook dispatch are separate claims.
Passing checks and GitHub `MERGEABLE` do not bypass required human approval.
