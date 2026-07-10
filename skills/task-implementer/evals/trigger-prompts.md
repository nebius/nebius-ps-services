# Trigger Prompts

`task-implementer` is explicit-only because it mutates code, invokes
per-task local commits, and may launch fresh implementation sessions.

## Should Trigger

```text
$task-implementer Break this complex brownfield request into ordered tasks and implement them one by one with a handoff between fresh sessions.
```

```text
Use $task-implementer for this repo change. Create task-1..task-n, run each task sequentially, gather per-task context, design and plan before editing, review and commit each completed task, and start a fresh Codex context for each task.
```

```text
$task-implementer Break this brownfield change into task-1..task-n, implement
one task at a time after brainstorm/design/plan gates, review and commit each
task, and keep a handoff between fresh Codex sessions.
```

```text
$task-implementer This is bigger than one task: split it into numbered
dependent work items, then for each item gather context, use design for the
implementation decision, write the plan, implement, validate, code-review, fix,
commit, and hand off to the next fresh session.
```

```text
$task-implementer Split this multi-layer app change into vertical tasks that
carry one behavior through frontend, API, and database, then implement each
task through the normal per-task implementation and commit loop.
```

```text
$task-implementer Continue from ~/.codex/task-implementer/my-repo/run-2026-06-28/handoff.md and implement the next pending task.
```

## Should Not Trigger

```text
Implement this small bug fix and run the focused test.
```

Use the normal implementation flow. Do not invoke `task-implementer` unless the
user explicitly asks for `$task-implementer`.

```text
This looks complex, please use global-context-management while you fix it.
```

Use `global-context-management` for context hygiene. Do not invoke
`task-implementer` unless the user also asks for the explicit sequential
per-task implementation and commit loop.

```text
Review this diff and tell me if the design is good.
```

Use `code-review`, `align`, `design`, or `system-design-rules` as appropriate.

```text
Start Agentic SDLC for this project.
```

Use `$sdlc-start`.

```text
Brainstorm how we could split this migration.
```

Use `brainstorm` for chat-only ideation.

```text
Commit the current changes.
```

Use `$commit` directly for standalone commits.
