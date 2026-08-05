---
name: sdlc-prepare-execution
description: "Use only as part of the Agentic SDLC workflow; use after one feature plan is locked and before `sdlc-tdd` to create or resume the feature's private integration branch/worktree, deterministic task waves, and recoverable execution state."
---

# Prepare SDLC Execution

## Help

For `$sdlc-prepare-execution --help` or `$sdlc-prepare-execution -h`, return concise help and stop before
any workflow step. Include the purpose, invocation policy, public usage/actions,
and `-h, --help` plus only documented skill-level options; say "No additional
public flags" when none exist. For internal or coordinator-only skills, state
that boundary and that no standalone public workflow action exists. After the
selected `SKILL.md` is loaded, help is report-only: do not call any additional
tools, inspect project state, or modify files, private state, Git, or external
systems. Never
expose private helper actions or treat help as workflow authorization.

## Purpose

Prepare one locked feature for isolated TDD and dependency-wave implementation.

## When To Use

- The active feature has a locked plan with stable `TASK-*` records.
- `sdlc-start` selects the execution-preparation phase before TDD.
- An interrupted preparation needs exact-state recovery.
- A validated corrective plan vN+1 must append resource-free future waves
  without changing active or completed task history.

## When Not To Use

- Do not use before the feature plan is locked.
- Do not implement production code, write tests, validate, commit the final
  feature, push, open a PR, or merge a PR.
- Do not use the general `worktree` or `task-implementer` public workflows for
  internal SDLC task resources.

## Inputs

- Active run directory, current `FEAT-*`, and locked plan.
- Canonical project checkout, named promotion branch, exact `HEAD`, and Git
  common directory, plus the exact initialized project folder and repo-relative
  scope. Unmanaged mode also requires a verified `origin` default identity;
  managed-child mode binds the exact local worktree identity instead.
- Current requirements, design, project-instruction decision, context,
  steering, and checkpoint state.

## Required Reads

- Reload `current-state.json`, the latest checkpoint, and active feature state.
- Read the locked plan and the state-schema reference owned by `sdlc-start`.
- Read `references/execution-plane.md` before preparing or recovering resources.
- Inspect Git branch, status, `HEAD`, worktree registry, and plan fingerprint.

## Writes

- Private execution state under
  `~/.codex/sdlc-runs/<project-id>/<run-id>/execution/<FEAT-*>/`.
- A persistent feature integration branch/worktree under the private run root.
- A scoped contract commit on the named project branch only when committed
  requirements, design, and a provenance-owned generated project-root
  `AGENTS.md` are the complete repo-root staged diff.
- State transition to `execution_prepared` and checkpoint evidence.

## Process

1. Require a clean named checkout. In unmanaged mode, resolve and verify the
   actual `origin` default; if on it, create and switch to the deterministic run
   promotion branch, otherwise reuse the current non-default branch. In a
   managed child, keep the exact local branch and `HEAD` without fetching or
   resolving a remote default. Reject unrelated dirt or private state inside
   the repository.
2. If current requirements/design changes and an optional
   `project-agent-instructions`-owned project-root `AGENTS.md` are the only
   tracked changes, stage from the repository root with `git add -A`, create
   one authorized contract commit, then require a clean checkout. Reject an
   unverified or human-owned `AGENTS.md`; never make a partial mixed commit.
3. Invoke the private execution helper `prepare`. It parses stable `TASK-*`
   records, validates dependencies/claims/domains, builds deterministic logical
   waves, records intent, and creates the integration branch/worktree from the
   exact contract `HEAD`. Git operations use the repository root, but every
   claim and worker `scope_cwd` must remain inside the initialized folder.
   Acquire the Agentic SDLC owner lease first when the checkout is managed by
   `worktree`; require lease schema v4 and register integration and worker
   resource intent before creation. Persist a canonical full-definition digest
   with every task record.
4. Re-observe branch, `HEAD`, Git common directory, worktree registration,
   cleanliness, plan digest, and private state before recording completion.
5. Return the integration cwd and route the next phase to `sdlc-tdd` there.

## Idempotency

- Repeated preparation returns the same verified integration identity.
- Never recreate a recorded branch, worktree, wave, task, or contract commit.
- Foreign collisions, moved refs, malformed state, or changed locked plans fail
  closed. Every execution coordinator schema v1 through v5 record returns
  `WORKFLOW_UPGRADE_REQUIRED`, including completed records.
- `replan-future` holds the execution transition lock, compares every active or
  completed task's full canonical definition and recorded definition digest,
  and changes only resource-free planned future waves. Exact repeat replans are
  no-ops.

## Failure Handling

- Use `PLAN_INVALID` for malformed tasks, unknown dependencies, or cycles.
- Use `WORKTREE_CONFLICT` for dirty, moved, foreign, or colliding Git resources.
- Use `REPLAN_REQUIRED` when the locked plan changes after preparation.
- Use private `task-recover --confirmed-stopped --expected-attempt <n>` only
  with a fresh worker session and an exactly re-observed clean, claimed-dirty,
  or one-direct-child worktree. The expected attempt makes ownership transfer
  compare-and-swap safe. Use `replan-future` only for resource-free planned waves.
- Reject corrective replan when any active/completed task definition or digest
  changes, any future wave owns a branch/worktree/assignment/result/journal, or
  execution is sealed, promoted, or done.
- Treat capacity batches as authoritative: `wave-prepare` creates only the
  active batch, and private `batch-advance` opens the next batch only after all
  current tasks are committed. `task-start` derives worker identity from
  `CODEX_THREAD_ID`; never accept a caller-invented session token.
- Retain every observed partial resource in recovery state; never force-delete it.
- For managed-outer promotion, persist the Git fast-forward, exact lease CAS,
  local interop, and coordinator state in that order. Resume must reconcile the
  lease and clean live outer head; stale or missing proof fails closed.

## Must Not

- Do not create workers, edit code/tests, promote, push, publish, or clean
  unverified resources.
- Do not use force flags, reset, rebase, squash, cherry-pick, prune, or gc.
- Do not copy ignored files, credentials, dotenv files, caches, hooks, or local
  tool state into worktrees.
- Do not treat agent/worktree separation as an operating-system security boundary.

## Completion Criteria

- The integration worktree is clean, locked, registered, and bound to the exact
  plan digest and project base.
- Every task belongs to one deterministic logical wave.
- Every task record binds its full canonical definition digest; corrective
  replanning preserves those bindings for all active and completed waves.
- Every corrective worker result binds a passed, digest-protected copy of the
  assignment's exact regression oracle to its diagnosis and worker commit.
- Current state and checkpoint point to the integration cwd and
  `sdlc-tdd` as the next skill.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return the feature, plan digest, project base, integration branch/cwd, logical
wave summary, private state written, recovery inventory, and next skill. Do not
print private prompt content, secrets, or raw journals.

## References

- Read `references/execution-plane.md` for task schemas, resource ownership,
  state transitions, and recovery rules.
