# Implementation Loop

Use this reference for the task-implementer queue, handoff, and fresh-session
loop. Keep the workflow small; do not recreate the Agentic SDLC state machine.

## Source Order

1. User prompt, explicit constraints, non-goals, and acceptance criteria.
2. Current repository instructions such as `AGENTS.md`.
3. Current code, tests, docs, changelog, generated artifacts, and local
   command output.
4. Related skills. Use `design` for architecture, contracts, missing code, or
   ambiguous boundaries when available. Let `design` decide when it needs
   `research`.
5. Current official vendor docs for version-sensitive products, SDKs, CLIs,
   APIs, frameworks, package managers, cloud behavior, or standards.

## Queue Construction

Build a dependency-first task queue:

- `task-1`: first prerequisite or highest-priority independent task.
- `task-2`: next task that depends only on completed tasks.
- `task-n`: final validation, cleanup, or integration task when it is a real
  work item.

Each task should have one coherent output. A task may include code, tests, docs,
and validation when those changes belong to the same behavioral surface.

Avoid splitting into ceremony-only tasks such as "inspect code" or "run tests"
unless the user explicitly wants planning-only output. Inspection and
validation are part of every implementation task.

## Design Routing

Route to `design` before editing a task when any of these are true:

- Public API, CLI, data model, storage schema, or protocol contract changes.
- Cross-module or cross-service ownership boundaries.
- Missing codebase surface where no clear local pattern exists.
- Multiple viable implementation approaches with different tradeoffs.
- Rollout, migration, compatibility, security, or reliability decisions.
- User prompt is underspecified and guessing could create the wrong behavior.

If `design` is unavailable, write a compact local design note in the handoff:
problem, chosen approach, alternatives rejected, open risks, and validation.
Mark the note with `design_skill_unavailable` so the next session can revisit it
when the skill becomes available.

## Handoff Discipline

The handoff is the durable source of truth between fresh sessions. Update it
after planning and after every task. It should stay short enough for a new
agent to read fully before editing.

Record summaries, not transcripts:

- changed files and why
- validation commands and outcome
- blocker classification and minimum missing input
- current assumptions and risks
- next task ID and exact next-session prompt

Do not store raw logs, copied docs, credentials, private endpoints, customer
data, or unrelated exploration output.

## Fresh Session Patterns

Interactive CLI:

1. Finish the active task.
2. Update the handoff.
3. Run `/new`.
4. Send the next-session prompt from the handoff.

Noninteractive automation:

1. Finish the active `codex exec` process.
2. Verify it updated the handoff.
3. Start a new `codex exec` process for the next task.
4. Do not use `codex exec resume` unless the user explicitly wants to preserve
   the previous session context for debugging.

Example:

```bash
codex --ask-for-approval never exec \
  --cd <repo-root> \
  --sandbox workspace-write \
  --add-dir "${CODEX_HOME:-$HOME/.codex}/task-implementer" \
  'Use $task-implementer to read <handoff-path>, verify the current worktree, implement only task-2, update the handoff, and stop.'
```

Use one implementation process at a time. Do not start `task-3` until `task-2`
has stopped and its handoff update is present.

## Final Alignment

After the last task, run changed-surface alignment:

- code and module wiring
- tests and fixtures
- CLI/help or API behavior
- docs, README, design docs, and changelog
- config, generated artifacts, and CI/workflow surfaces

Use `$align` when available. If it is unavailable, perform the equivalent local
checklist and say so in the final response.
