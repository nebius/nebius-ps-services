# Implement Plan

`sdlc-implement-plan` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Coordinate dependency waves for one feature. Every safe task uses one fresh
agent with its own branch and private worktree. The coordinator verifies one
task commit, merges workers in stable order, runs combined evidence, and
non-force-cleans worker resources before advancing.
Capacity batches are enforced: only the active batch owns assignments and
worktrees, and the next batch opens only after the current batch commits.
Every assignment binds a private dependency handoff, and every task uses a
worker session identity that has never owned another task in the run.

Native isolated agents are preferred. When unavailable, the private launcher
uses one sequential ephemeral `codex exec` process per assignment with exact
scope cwd, `workspace-write`, stdin instructions, and schema-bound output. The
coordinator—not the worker—performs the sensitive-content gate and task commit.
Workers must start through the bound private helper, emit direct bounded
heartbeats at least every 30 seconds, and never run a background heartbeat
process. The coordinator arms only available slots and watches each active
worker for prestart, scope, read-only, stall, and maximum-runtime violations.
The sequential launcher places each worker and its descendants in one dedicated
process group and terminates that group on every post-spawn failure. A worker
that never starts is requeued only after confirmed termination and an exact
dispatch-timestamp compare-and-swap proves the assignment is untouched.

For diagnosis-bound correction, immutable plan vN+1 preserves every prior task
definition and digest, then appends corrective tasks and waves. Each corrective
assignment carries the exact diagnosis and original regression oracle. The
worker runs that oracle first after the bounded repair, followed by the
affected-boundary check and normal validation. `task-finish` stores a passed,
digest-protected oracle proof bound to the diagnosis and the
coordinator-created task commit; missing
or mismatched proof cannot enter integration. Completed, sealed, promoted, or
completed-run execution is never reopened.
Before creating that commit, the coordinator journals the exact staged tree,
message, assignment, and evidence intent. Retry accepts only the corresponding
clean direct-child commit and exact persisted result.

## Main Boundaries

- Do not change unrelated features.
- Do not modify locked plans.
- Do not hide failures.
- Do not remove tests to pass.
- Do not broaden implementation beyond the locked vertical slice; route plan or
  design defects backward instead.
- Do not let workers share agents, branches, worktrees, write claims, or
  conflict domains.
- Do not rewrite history or force-clean resources.
- Do not speculate from an evaluation symptom, weaken acceptance criteria, or
  dispatch a probable/incomplete diagnosis.

## Primary Inputs

- Locked plan.
- Red or ready tests.
- Feature design.
- Context pack.
- Existing codebase.
- Prepared execution coordinator and task-wave assignments.
- Exact diagnosis, original oracle, and invalidation set for corrective work.

## Output

- Planned task waves are implemented and integrated.
- Planned vertical slice is implemented or a plan/design defect is recorded.
- Focused tests are runnable or blocker is recorded.
- Changed files match implementation boundaries.
- Every task has validation, review, one direct-child commit, and cleanup proof.
- Corrective work reruns the original oracle and invalidated downstream gates
  at the new integration commit.
- State moves to `implemented`.
