# Task Implementer Handoff

## Run

- Project ID:
- Scope ID:
- Repo root:
- Repo-relative scope:
- Branch:
- Run ID:
- Workspace manifest:
- Run manifest:
- Prompt ID:
- Editable source path:
- Bound revision:
- Bound SHA-256:
- Bound snapshot path:
- Created:
- Last invoked at:
- Last updated:
- Active wave: none
- Last promoted wave: none
- Last promoted commit: none
- Overall status: prepared | running | blocked | done | superseded | abandoned

## Reconciliation

- State: none | proposed | queued_after_wave | applied | blocked
- Previous bound revision: none
- Current bound revision:
- Reconciled at: none
- Summary: none
- Preserved completed task IDs: none
- Preserved pending task IDs: none
- Superseded pending task IDs: none
- Appended task IDs: none
- Next-wave overrides: none

## Specification State

- Requirements document: docs/requirements.md
- Design document: docs/design.md
- Requirements managed SHA-256: none
- Design managed SHA-256: none
- Next requirement ID: TI-REQ-001
- Next design ID: TI-DES-001
- Open requirement IDs: none
- Pending steering revisions: none
- Applied steering revisions: none

## Project Agent Instructions

- Spec receipt schema: project-agent-instructions.spec-validation.v2
- Decision schema: project-agent-instructions.decision.v2
- State schema: project-agent-instructions.state.v2
- Outcome: pending | created | refreshed | adopted | retired | existing-sufficient | not-needed | blocked
- Active instruction path: none
- Decision SHA-256: none
- Project instruction SHA-256: none
- Spec receipt SHA-256: none
- Ownership receipt SHA-256: none
- Effective config SHA-256: none
- Reload required: no
- Contract commit: none
- Blocker: none

## Request Summary

Summarize the bound prompt revision and constraints without copying the prompt
body, secrets, raw logs, or private material.

## Code Context

- Relevant files:
- Relevant tests:
- Relevant docs/config:
- Important repo instructions:
- Primary checkout state:
- Current assumptions:
- Source context:
- Brainstorm/context result:
- Vertical slice or layers:
- End-to-end validation target:

## Dependency Waves

### wave-001

- Status: planned | preparing | running | integrating | promotion_pending | promoted | cleanup | done | blocked
- Base commit:
- Contract commit:
- Logical task order: task-1, task-2
- Dispatch batches: task-1, task-2
- Integration validation:
- Integration code-review:
- Steering reconciled: no
- Promoted commit: none
- Cleanup retained: none

### wave-002

- Status: planned
- Base commit: pending prior promotion
- Contract commit: none
- Logical task order: task-3
- Dispatch batches: task-3
- Integration validation:
- Integration code-review:
- Steering reconciled: no
- Promoted commit: none
- Cleanup retained: none

## Task Queue

Worker liveness is derived deterministically: tasks with no dependencies use
the `standard` profile; tasks with dependencies use the `integration` profile.
Every task must carry non-empty implementation steps, validation,
end-to-end validation, and done criteria into its immutable assignment.

### task-1

- Status: pending | in_progress | committed | done | blocked | superseded
- Wave: wave-001
- Source revision:
- Source prompt sections:
- Requirement IDs:
- Design ID:
- Requirements proposal:
- Design record:
- Requirements envelope SHA-256:
- Design envelope SHA-256:
- Priority:
- Depends on: none
- Goal:
- Rationale:
- Brainstorm/context needed: yes | no
- Brainstorm/context result:
- Design needed: yes | no
- Design notes:
- Vertical slice or layers:
- Plan:
- Write claims: <!-- `exact: repo/path` or `prefix: repo/directory`, one per line. -->
- Conflict domains: <!-- `class:stable-key`, one per line; use `unknown` to force singleton. -->
- Implementation steps:
- Validation:
- End-to-end validation:
- Done criteria:
- Rollback notes:
- Stop conditions:
- Worker assignment: private immutable record
- Worker result: private immutable record
- Commit:
- Changed files:
- Evidence:
- Blocker:

### task-2

- Status: pending | in_progress | committed | done | blocked | superseded
- Wave: wave-001
- Source revision:
- Source prompt sections:
- Requirement IDs:
- Design ID:
- Priority:
- Depends on: none
- Goal:
- Rationale:
- Plan:
- Write claims:
- Conflict domains:
- Implementation steps:
- Validation:
- End-to-end validation:
- Done criteria:
- Rollback notes:
- Stop conditions:
- Worker assignment: private immutable record
- Worker result: private immutable record
- Commit:
- Changed files:
- Evidence:
- Blocker:

### task-3

- Status: pending | in_progress | committed | done | blocked | superseded
- Wave: wave-002
- Source revision:
- Source prompt sections:
- Requirement IDs:
- Design ID:
- Priority:
- Depends on: task-1, task-2
- Goal:
- Rationale:
- Plan:
- Write claims:
- Conflict domains:
- Implementation steps:
- Validation:
- End-to-end validation:
- Done criteria:
- Rollback notes:
- Stop conditions:
- Worker assignment: private immutable record
- Worker result: private immutable record
- Commit:
- Changed files:
- Evidence:
- Blocker:

## Wave Checkpoints

Tasks become done only after verified fast-forward promotion.

### checkpoint-wave-001

- Completed wave:
- Bound revision:
- Task commits:
- Ordered merge commits:
- Shared-file integration commit: none
- Combined validation:
- Integration code-review:
- Review fixes:
- Promoted commit:
- Requirements SHA-256:
- Design SHA-256:
- Spec validation:
- Open risks:
- Next wave:

## Failure Log

- Wave/task:
- Classification:
- Evidence:
- Project branch unchanged: yes | no | not_applicable
- Retained worktrees/branches:
- Decision:
- Next action:

## Coordinator Handoff

- Current action: resume recorded v3 transition
- Coordinator state path:
- Active wave:
- Dispatch batch:
- Retained inventory: none
- Worker mechanism: native subagents | fresh sequential codex exec

## Final Alignment

- Completed at: none
- Promoted commit: none
- Evidence: none

## Next Run Prompt

```text
Use $task-implementer run <same-prompt-path-or-unique-filename>.

Read the run manifest, exact bound snapshot, complete coordinator-owned handoff,
v2 coordinator/wave state, immutable assignments/results, and wave journal.
Re-observe the primary checkout and every managed worktree/ref before choosing a
transition. Resume the active dependency wave idempotently. Keep shared specs
and documentation coordinator-owned; require each worker to use its assigned
absolute scope cwd, locked write claims, code-review, exactly one $commit, and a
private result. Integrate in stable task order, run combined validation and
integration review, reconcile steering, promote only by verified ff-only merge,
then clean up without force. Do not expose internal IDs to the user.
```
