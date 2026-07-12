# Trigger Prompts

`task-implementer` is explicit-only because it creates private workflow state,
may edit product code after approval, invokes per-task local commits, and
requires fresh implementation sessions.

## Should Trigger

```text
$task-implementer workspace init services/nebius-cxcli
```

Create or verify the private `CODE` + `PROMPTS` workspace only. Do not prepare
or implement work.

```text
$task-implementer workspace new "Add prompt workspace support"
```

Create one editable prompt for one ask. Do not launch Codex from VS Code or
submit the prompt automatically.

```text
$task-implementer prepare ~/.codex/task-implementer/projects/example/scopes/api/prompts/2026-07-12_1430--add-retries.md
```

Validate and snapshot the exact prompt, inspect the repository, create a
reviewable multi-task queue and handoff, then stop without product edits.

```text
$task-implementer prepare --new-run <private-prompt-path>
```

Create an explicitly requested new run even when the completed run's submitted
content is unchanged. Stop after preparation.

```text
$task-implementer run run-20260712t213000z-ab12cd34
```

Read the immutable bound revision and prepared handoff, implement exactly the
first pending task through the per-task implementation and commit loop, save
the checkpoint, and stop.

```text
$task-implementer continue run-20260712t213000z-ab12cd34
```

In a fresh session, implement exactly the next pending task with
brainstorm/design/plan gates, validation, `code-review`, fixes, `$commit`, and
handoff update. Do not continue into another task.

```text
$task-implementer reconcile run-20260712t213000z-ab12cd34 <edited-private-prompt-path>
```

Snapshot the edited prompt, preserve completed work and stable IDs, propose
additive or superseding queue changes, and stop without product edits.

```text
$task-implementer Break this complex brownfield request into vertical
task-1..task-n deliverables and use the private handoff loop one task per fresh
session.
```

Preserve the explicit sequential implementation and commit loop. If there is
no prompt file yet, offer `workspace new` rather than inventing repository-local
state.

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

Use `global-context-management` for context hygiene. Do not invoke the
per-task implementation and commit loop without an explicit
`task-implementer` request.

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
