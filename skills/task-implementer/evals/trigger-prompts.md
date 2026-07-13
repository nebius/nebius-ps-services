# Trigger Prompts

`task-implementer` is explicit-only because it creates private workflow state,
may edit product code, invokes per-task local commits, and requires fresh
implementation sessions.

## Should Trigger

```text
$task-implementer workspace init
```

Initialize the exact current project folder, create one starter only when no
prompt exists, open the generated workspace when possible, and show prompts by
private submission activity. Do not implement work.

```text
$task-implementer workspace init services/nebius-cxcli
```

Resolve the explicit folder to the same canonical workspace as running from
inside it. Repeated initialization preserves prompts and run history.

```text
$task-implementer run ~/.codex/task-implementer/projects/example/scopes/api/prompts/2026-07-12_1430--add-retries.md
```

Validate and snapshot the managed prompt, build the internal queue, claim the
first task in planning, authorize its completed locked plan before product
edits, then validate, `code-review`, fix, `$commit`, checkpoint, and stop.

```text
$task-implementer run 2026-07-12_1430--add-retries.md
```

Resolve the unique filename in the current project's prompt workspace. Resume
or reconcile its internal state, claim exactly one dependency-ready task, plan
before implementation, update private activity and checkpoint handoff, and
stop. A same-session attempt to claim another task must fail with
`FRESH_SESSION_REQUIRED`.

```text
$task-implementer run <same-edited-prompt-file>
```

Append an immutable revision once, preserve completed tasks and stable IDs,
reconcile stable requirements before pending work, add the task's just-in-time
design, and implement the next safe task. Keep all IDs private. Stop with
`HUMAN_INPUT_REQUIRED` before product edits for ambiguous or destructive
reversals.

```text
$task-implementer run <same-prompt-after-appending-a-Steering-note>
```

Do not require a separate steering file or command. If the same session owns a
clean planning plane, rebind and replan that task before authorization. If
another session owns planning or any plane is implementing, append one pending
revision, preserve the plane exactly, return
`STEERING_QUEUED_AFTER_TASK`, and reconcile it after the task stops.

```text
$task-implementer run <same-prompt-that-adds-a-new-requirement>
```

Incrementally update only Task Implementer-managed regions in project
`docs/requirements.md` and `docs/design.md` after plan authorization. Preserve
user-owned text and commit the specifications with the affected task. Return
`SPEC_OWNER_CONFLICT` when Agentic SDLC owns either exact file.

```text
$task-implementer run <same-completed-prompt-file>
```

For unchanged content, record activity and return `ALREADY_COMPLETE` without
product changes. For edited content, start a new internal run and implement its
first task.

```text
$task-implementer run <same-prompt-file-after-an-interrupted-task>
```

Recover only the already claimed task after verifying that the old writer is
gone and explicitly reviewing the exact recovery worktree digest and changed
paths against the locked task allowlist. Preserve its plan and evidence; never
advance to another task during recovery.

## Retired Public Actions

These explicit forms must fail as unsupported public actions; do not translate
them to hidden compatibility aliases:

```text
$task-implementer workspace new "Add retries"
$task-implementer workspace list
$task-implementer prepare <prompt-path>
$task-implementer continue <internal-run-id>
$task-implementer reconcile <internal-run-id> <prompt-path>
$task-implementer run <internal-run-id>
$task-implementer run --new-run <prompt-path>
$task-implementer steer <steering-file>
```

Explain the two-command interface and ask the user to initialize or run a
managed prompt path/unique filename as appropriate. Never require an internal
ID.

## Should Not Trigger

```text
I opened a Markdown prompt from two days ago. Help me improve the wording.
```

Treat this as ordinary editing or brainstorming. Do not invoke
`task-implementer` without an explicit action.

```text
How should I organize prompt files for this project?
```

Answer or brainstorm. Do not initialize private state implicitly.

```text
Implement this small bug fix and run the focused test.
```

Use the normal implementation flow.

```text
This looks complex. Use global-context-management while you fix it.
```

Use `global-context-management` for context hygiene. Do not invoke the per-task
implementation and commit loop without an explicit `task-implementer` request.

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

Use `$commit` directly for a standalone commit.
