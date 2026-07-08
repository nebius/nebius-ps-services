# Task Implementer Handoff

## Run

- Project:
- Repo root:
- Branch:
- Run ID:
- Created:
- Last updated:
- Current task:
- Last completed task:
- Last commit:
- Overall status: planning | running | blocked | done

## User Request

Summarize the user's request and constraints. Do not paste secrets, raw logs, or
private material.

## Code Context

- Relevant files:
- Relevant tests:
- Relevant docs/config:
- Important repo instructions:
- Current assumptions:
- Source context:
- Brainstorm/context result:

## Task Queue

### task-1

- Status: pending | in_progress | done | blocked | superseded
- Priority:
- Depends on:
- Goal:
- Rationale:
- Brainstorm/context needed: yes | no
- Brainstorm/context result:
- Design needed: yes | no
- Design notes:
- Plan:
- Likely files:
- Implementation steps:
- Validation:
- Code-review:
- Review fixes:
- Commit:
- Done criteria:
- Rollback notes:
- Changed files:
- Evidence:
- Blocker:

### task-2

- Status: pending | in_progress | done | blocked | superseded
- Priority:
- Depends on:
- Goal:
- Rationale:
- Brainstorm/context needed: yes | no
- Brainstorm/context result:
- Design needed: yes | no
- Design notes:
- Plan:
- Likely files:
- Implementation steps:
- Validation:
- Code-review:
- Review fixes:
- Commit:
- Done criteria:
- Rollback notes:
- Changed files:
- Evidence:
- Blocker:

## Checkpoints

### checkpoint-1

- Completed task:
- Summary:
- Brainstorm/context result:
- Design result:
- Plan followed:
- Files changed:
- Validation:
- Code-review:
- Review fixes:
- Commit hash:
- Commit message:
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
- Next session mechanism: new `codex` session | `/new` | new `codex exec`
  process
- Handoff context path:
- Next task:
- Do not continue in current session: yes

## Next Session Prompt

```text
Use $task-implementer to continue from this handoff file:
<handoff-path>

Read the handoff first, verify the current git status and relevant source files,
then implement only <task-id>. Do not run parallel write agents. Gather the
task-specific context, use brainstorm when source-ranked context or assumption
checks are useful, route non-trivial design or contract choices through design,
write the per-task plan, run focused validation, use code-review, fix scoped
findings, commit through $commit, and update the handoff with context, design,
plan, changed files, validation, review result, fixes, commit hash/message or
blocker, residual risks, and the next-session prompt before stopping.
```
