# Implement Plan

Execution supports Codex and Claude through the selected native host context.
See `SKILL.md` for invocation, required setup and evidence boundaries.

`sdlc-implement-plan` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

Both coordinators use the shared stop-before-repair worker boundary. An
unexpected failed check or blocking review finding returns preserved evidence
and a blocked result before any repair or rerun. Only negative outcomes declared
in immutable assignment validation before execution are expected feedback.
Required safe teardown and lease release remain allowed. The owning coordinator
classifies and authorizes recovery or replanning; workers cannot relabel failures
or erase them with a later successful check.

Before actions with known access prerequisites, both worker envelopes direct
agents to supported tools and native approval mechanisms. An approval request
does not grant access, a denial remains a policy/authority stop, and an
unexpected executed failure still requires the blocked handoff. Recovery uses
the existing shared engine with a stopped predecessor, released runtime,
unchanged assignment and fresh identity; it never grants permission itself.

A proven `ENVIRONMENT_DEFECT` during `active_task` at exactly
`sdlc-implement-plan` now routes through `existing_task_attempt`. Establish an
in-scope remedy and new evidence/hypothesis, then consume a counted localized
repair dispatch before authorizing the shared fresh-worker recovery. Preserve
the blocked receipt and observe termination and runtime release. Rerun the
original failed operation through an authorized mechanism, complete the
assignment's validation and normal task/wave completion, then record the actual
remediation result. Success requires the existing downstream revalidation
cursor; it does not erase failure history or grant permission. Other active
phases, policy/human blockers and later lifecycle boundaries gain no authority.

## What It Does

Coordinate bounded dependency waves for one delivery slice across its eligible features. Every safe task uses one fresh
agent with its own branch and private worktree. The coordinator verifies one
task commit, merges workers in stable order, runs combined evidence, and
non-force-cleans worker resources before advancing.
A deterministic ready queue refills each native slot after termination and
validated result admission. Every task in a wave uses the same frozen base;
integration waits for the full wave.
Every assignment binds a private dependency handoff, and every task uses a
worker session identity that has never owned another task in the run.
`workflow/worker-assignment-v2` also binds the accepted root-intent digest and exact canonical
project-spec receipt. Workers never reclassify user intent or edit specs; they
return typed `spec_gaps` to the root coordinator, and any non-empty list stops
before task commit for reconciliation and replanning. The sequential launcher
rejects sensitive gap summaries/evidence before returning coordinator-visible
JSON; execution path inventories keep rename folding disabled so protected
source paths remain visible.

Implement and SDLC share one launch/result owner. `task-arm` returns the
canonical launch envelope, and `task-result` validates complete native output
and independently observed termination into an immutable per-attempt receipt.
`task-finish --receipt` derives completion evidence from that receipt. Blocked or
replan results stop new work while active peers may drain; recovery preserves
old evidence and uses the current coordinator's immutable authorization.
Native arm/recovery admission validates the selected home, project/run and
coordinator before declaring required project-state and runtime-lease write
directories. Adapters preserve sandbox and approval controls; directory grants
do not authorize unrelated state edits. The sequential adapter admits before
spawning. Missing host capability blocks execution.
An early exit before `task-start` remains `WORKER_FAILED`; a validated bounded
`prestart_result` supplies diagnosis only, never result or recovery authority.

Native isolated agents are preferred. When unavailable, the private launcher
uses one sequential ephemeral `codex exec` process per assignment with exact
scope cwd, `workspace-write`, stdin instructions, and schema-bound output. The
coordinator—not the worker—performs the sensitive-content gate and task commit.
Workers must start through the bound private helper, emit direct bounded
heartbeats targeting 30 seconds, and never run a background heartbeat
process. The coordinator arms only available slots and watches each active
worker for prestart, scope, read-only, stall, and maximum-runtime violations.
The shared startup window is five minutes from arming to `task-start`, followed
by the one-hour work limit. Prepare handoff details before arming, spawn
immediately, and persist observations afterward. Repeated arming never renews time.
The sequential launcher places each worker and its descendants in one dedicated
process group and terminates that group on every post-spawn failure. A worker
that never starts is requeued only after confirmed termination and an exact
dispatch-timestamp compare-and-swap proves the assignment is untouched.

For diagnosis-bound correction, immutable plan vN+1 preserves every prior task
definition and digest, then appends corrective tasks and waves. It retains the
existing TDD base and integration tip; prepare the appended wave directly after
`replan-future`, without resealing TDD. Each corrective
assignment carries the exact diagnosis and original regression oracle. The
worker runs that oracle first after the bounded repair, followed by the
affected-boundary check and normal validation. `task-finish` stores a passed,
digest-protected oracle proof bound to the diagnosis and the
coordinator-created task commit; missing
or mismatched proof cannot enter integration. Completed, sealed, promoted, or
completed-run execution is never reopened.
When combined validation fails after integration, classifier lifecycle
`integrated_wave` and a counted repair dispatch authorize `wave-fail`. The wave
remains failed while clean worker resources are retired. Only matching
diagnosis/oracle-bound tasks may cross that failure. Successful corrective
combined validation creates a separate resolution receipt before the dispatch
is completed and downstream gates are rerun.
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
