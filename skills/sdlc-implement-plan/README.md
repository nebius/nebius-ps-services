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

Every blocked SDLC recovery additionally requires active counted classification
bound to its canonical worker-result receipt, integration commit and current
fingerprints. Both authorization and transfer enforce the
[shared v2 contract](../worktree/references/workflow-contract.md). Include
`evidence_digests.worker_result` as `sha256:` plus the receipt's `receipt_sha256`
when classifying. Do not complete the repair before its authorized worker runs.
Discover existing project instruction files before reading them; absent optional
`AGENTS.md` or `AGENTS.override.md` files are not required artifacts.

A proven `ENVIRONMENT_DEFECT` during `active_task` at exactly
`sdlc-implement-plan` now routes through `existing_task_attempt`. Establish an
in-scope remedy and new evidence/hypothesis, then consume a counted localized
repair dispatch before authorizing the shared fresh-worker recovery. Preserve
the blocked receipt and observe termination and runtime release. Rerun the
original failed operation through an authorized mechanism, complete the
assignment's validation and normal task completion, then record the actual
remediation result before completing an interrupted parent's corrective wave. Success requires the existing downstream revalidation
cursor; it does not erase failure history or grant permission. Other active
phases, policy/human blockers and later lifecycle boundaries gain no authority.

When a new independent failure interrupts a counted pending repair,
`repair-control-v3` retains its full context in immutable suspended evidence.
After the intervening worker has performed and verified the actual repair,
record its successful completion before the original corrective wave completes.
The owner returns `resumed_dispatch_id` and restores the pending parent only
when its ordered gates cover every remaining invalidation. Refresh checkpoint
repair pointers from the returned control. Both budgets, the child completion
and deferred cursor, and all failure evidence remain. No historical receipt
becomes active by itself; no new dispatch is charged. Failed, changed, exhausted
or broader obligations stop restoration. The original wave still needs fresh
combined validation, and its later repair completion reruns all invalidated
gates. Completion replay cannot rewind a later owner. Obsolete v2 control state
is rejected without conversion; install the source repair before a fresh run.

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
`workflow/worker-assignment-v4` also binds the accepted root-intent digest and exact canonical
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
spawning. Verify capabilities before arming: supported explicit command cwd and
existing inherited directory grants can satisfy the envelope without spawn
setters. Worker prompts name the startup/heartbeat cwd separately from frozen
validation cwd. Missing host capability blocks execution.
An early exit before `task-start` remains `WORKER_FAILED`; a validated bounded
`prestart_result` supplies diagnosis only, never result or recovery authority.

Native isolated agents are preferred. When unavailable, the private launcher
uses one sequential ephemeral `codex exec` process per assignment with exact
scope cwd, `workspace-write`, stdin instructions, and schema-bound output. The
coordinator—not the worker—performs the sensitive-content gate and task commit.
Workers must start through the bound private helper, emit direct bounded
heartbeats targeting 30 seconds, and never run a background heartbeat
process. The coordinator arms only available slots and watches each active
worker for identity/scope violations and health observations. Delayed startup,
read-only work and stale observations prompt diagnosis without healthy-work
expiry. Prepare handoffs before arming, spawn immediately and persist observations
afterward. See [execution health](../worktree/references/execution-health.md).
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
