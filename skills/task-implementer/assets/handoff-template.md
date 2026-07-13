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
- Current task: none
- Last completed task: none
- Last commit: none
- Overall status: prepared | running | blocked | done | superseded | abandoned

## Execution Plane

- Task: none
- Phase: unclaimed | planning | implementation | stopped
- Bound revision: none
- Plan SHA-256: none
- Queue SHA-256: none
- Checkpoint SHA-256: none
- Worktree baseline SHA-256: none
- Claimed at: none
- Authorized at: none
- Completed at: none
- Recovery count: 0
- Stop required: yes
- Next session required: no

## Reconciliation

- State: none | proposed | applied
- Previous bound revision: none
- Current bound revision:
- Reconciled at: none
- Summary: none
- Preserved completed task IDs: none
- Preserved pending task IDs: none
- Superseded pending task IDs: none
- Appended task IDs: none
- Next-task overrides: none

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

## Request Summary

Summarize the bound prompt revision and constraints without copying the prompt
body, secrets, raw logs, or private material.

## Code Context

- Relevant files:
- Relevant tests:
- Relevant docs/config:
- Important repo instructions:
- Worktree state:
- Current assumptions:
- Source context:
- Brainstorm/context result:
- Vertical slice or layers:
- End-to-end validation target:

## Task Queue

### task-1

- Status: pending | in_progress | done | blocked | superseded
- Source revision:
- Source prompt sections:
- Requirement IDs:
- Design ID:
- Requirements proposal:
- Design record:
- Requirements envelope SHA-256:
- Design envelope SHA-256:
- Priority:
- Depends on:
- Goal:
- Rationale:
- Brainstorm/context needed: yes | no
- Brainstorm/context result:
- Design needed: yes | no
- Design notes:
- Vertical slice or layers:
- Plan:
- Likely files: <!-- Exact repo-relative paths, one per line. -->
- Implementation steps:
- Validation:
- End-to-end validation:
- Code-review:
- Review fixes:
- Commit:
- Done criteria:
- Rollback notes:
- Stop conditions:
- Changed files:
- Evidence:
- Blocker:

### task-2

- Status: pending | in_progress | done | blocked | superseded
- Source revision:
- Source prompt sections:
- Requirement IDs:
- Design ID:
- Requirements proposal:
- Design record:
- Requirements envelope SHA-256:
- Design envelope SHA-256:
- Priority:
- Depends on:
- Goal:
- Rationale:
- Brainstorm/context needed: yes | no
- Brainstorm/context result:
- Design needed: yes | no
- Design notes:
- Vertical slice or layers:
- Plan:
- Likely files: <!-- Exact repo-relative paths, one per line. -->
- Implementation steps:
- Validation:
- End-to-end validation:
- Code-review:
- Review fixes:
- Commit:
- Done criteria:
- Rollback notes:
- Stop conditions:
- Changed files:
- Evidence:
- Blocker:

## Checkpoints

### checkpoint-1

- Completed task:
- Bound revision:
- Summary:
- Brainstorm/context result:
- Design result:
- Plan followed:
- Vertical slice or layers:
- Files changed: <!-- Exact repo-relative paths from the commit, one per line. -->
- Validation:
- End-to-end validation:
- Code-review:
- Review fixes:
- Commit hash:
- Commit message:
- Requirements SHA-256:
- Design SHA-256:
- Spec validation:
- Open risks:
- Next task:

## Failure Log

- Task:
- Classification:
- Evidence:
- Decision:
- Next action:

## Session Handoff

- Current session action: stop after saving this handoff
- Next session mechanism: new Codex session | `/new` | new `codex exec` process
- Handoff context path:
- Next task:
- Do not continue in current session: yes

## Next Session Prompt

```text
Use $task-implementer run <same-prompt-path-or-unique-filename>.

Read the run manifest, exact bound snapshot, and complete handoff first. Verify
the repository, scope, digest, handoff status, current git state, and relevant
source files. Claim exactly the dependency-ready task in a private execution
plane, complete its plan, and authorize that locked plan before product edits.
Do not use the editable prompt as execution input and do not run parallel write
agents. Gather task-specific context, use brainstorm when source-ranked context
or assumption checks are useful, route non-trivial design or contract choices
through design, run focused and end-to-end validation, use code-review, fix
scoped findings, commit through $commit, checkpoint the task and next-session
handoff, and stop this session.
```
