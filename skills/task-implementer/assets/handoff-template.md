# Task Implementer Handoff

## Run

- Project:
- Repo root:
- Branch:
- Run ID:
- Created:
- Last updated:
- Current task:
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

## Task Queue

### task-1

- Status: pending | in_progress | done | blocked | superseded
- Priority:
- Depends on:
- Goal:
- Rationale:
- Design needed: yes | no
- Design notes:
- Likely files:
- Implementation steps:
- Validation:
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
- Design needed: yes | no
- Design notes:
- Likely files:
- Implementation steps:
- Validation:
- Done criteria:
- Rollback notes:
- Changed files:
- Evidence:
- Blocker:

## Checkpoints

### checkpoint-1

- Completed task:
- Summary:
- Files changed:
- Validation:
- Open risks:
- Next task:

## Failure Log

- Task:
- Classification:
- Evidence:
- Decision:
- Next action:

## Next Session Prompt

```text
Use $task-implementer to continue from this handoff file:
<handoff-path>

Read the handoff first, verify the current git status and relevant source files,
then implement only <task-id>. Do not run parallel write agents. Update the
handoff with changed files, validation, blockers, and the next-session prompt
before stopping.
```
