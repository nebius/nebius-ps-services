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
   `research`. Use `code-review` as the per-task review gate after
   implementation, and use `$commit` as the per-task local checkpoint gate
   after review findings are fixed.
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

## Per-Task Completion Gate

Each task session must finish the active task completely before handing off:

1. Implement only the active task.
2. Run focused validation for that task.
3. Inspect the diff and remove unrelated cleanup.
4. Use `code-review` on the active task's code changes.
5. Fix safe, scoped review findings and re-run focused validation.
6. Re-run or refresh the review when review fixes changed code materially.
7. Use `$commit` to create the local task checkpoint.
8. Update the handoff with validation, review, fixes, commit hash/message or
   commit blocker, residual risks, and the exact next-session prompt.
9. Stop the current session so the next task starts from the handoff in fresh
   context.

If `code-review` finds a blocking issue that cannot be fixed inside the active
task boundary, mark the task `blocked` with `REVIEW_BLOCKER`. If `$commit`
stops because staged validation, hooks, branch state, or commit scope is unsafe,
mark the task `blocked` with `COMMIT_BLOCKER`.

Because `$commit` stages the complete repository diff from the Git root, do not
invoke it when `git status` includes unrelated user changes that are not part of
the active task or prior task checkpoints recorded in the handoff. Mark that as
`WORKTREE_CONFLICT` and stop rather than producing a mixed commit.

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
- code-review decision and scoped fixes applied
- commit hash/message, or the exact blocker that prevented commit
- blocker classification and minimum missing input
- current assumptions and risks
- next task ID and exact next-session prompt

Do not store raw logs, copied docs, credentials, private endpoints, customer
data, or unrelated exploration output.

## Fresh Session Patterns

Interactive CLI:

1. Finish the active task.
2. Run validation, `code-review`, scoped fixes, and `$commit`.
3. Update the handoff with the commit evidence.
4. For strict close/open, run `/archive` or `/quit`, start a new `codex`
   session from the repo root, and send the next-session prompt from the
   handoff.
5. If the operator accepts a fresh conversation in the same CLI process as the
   boundary, use `/new` instead.

Noninteractive automation:

1. Finish the active task inside one `codex exec` process.
2. Run validation, `code-review`, scoped fixes, and `$commit`.
3. Verify it updated the handoff with commit evidence.
4. Let that process exit.
5. Start a new `codex exec` process for the next task and pass the handoff path
   in the prompt.
6. Do not use `codex exec resume` unless the user explicitly wants to preserve
   the previous session context for debugging.

Example:

```bash
codex --ask-for-approval never exec \
  --cd <repo-root> \
  --sandbox workspace-write \
  --add-dir "${CODEX_HOME:-$HOME/.codex}/task-implementer" \
  'Use $task-implementer to read <handoff-path>, verify the current worktree, implement only task-2, validate it, use code-review, fix scoped findings, commit through $commit, update the handoff, and stop.'
```

Use one implementation process at a time. Do not start `task-3` until `task-2`
has stopped and its reviewed, committed handoff update is present.

## Final Alignment

After the last task, run changed-surface alignment:

- code and module wiring
- tests and fixtures
- CLI/help or API behavior
- docs, README, design docs, and changelog
- config, generated artifacts, and CI/workflow surfaces

Use `$align` when available. If it is unavailable, perform the equivalent local
checklist and say so in the final response.
