# Trigger Prompts

`task-implementer` is explicit-only because it creates private state, isolated
Git worktrees/branches, worker commits, and coordinator integration commits.
Generic parallel requests do not trigger it.

## Should Trigger

```text
$task-implementer --help
$task-implementer -h
```

Return concise, report-only help with purpose, explicit invocation policy,
exactly two workflow actions (`workspace init` and `run`), their arguments, and
`-h, --help`, then stop. After the selected `SKILL.md` loads, do not initialize
a workspace, inspect the project, call additional tools, change private state,
or start either workflow action.

```text
$task-implementer workspace init
```

Initialize the exact current project scope, create one starter only when none
exists, and do not implement work.

```text
$task-implementer workspace init services/nebius-cxcli
```

Resolve the monorepo scope while keeping every future worker worktree a full
repository checkout.

```text
$task-implementer run 2026-07-12_1430--add-retries.md
```

Resolve the unique managed prompt, plan all tasks and deterministic dependency
waves, then coordinate every wave until done or blocked. Keep all internal IDs,
branches, paths, and transitions private.

```text
$task-implementer run <prompt-with-five-disjoint-tasks-then-one-dependent-task>
```

Put the five completely disjoint tasks in one logical wave, dispatch them in
capacity-sized batches to isolated full-repository worktrees, and place the
dependent task in the next wave. Capacity must not change logical waves.

```text
$task-implementer run <prompt-with-two-tasks-that-touch-the-same-lockfile>
```

Combine the tasks before IDs lock when they form one coherent result;
otherwise add a dependency and serialize them. Never dispatch overlapping
dependency-file writers.

```text
$task-implementer run <prompt-with-distinct-kubernetes-resources-and-live-apply>
```

The code/config edits may be analyzed by resource identity, but live external
Kubernetes mutation remains a singleton conflict domain and needs separate
explicit authority.

```text
$task-implementer run <same-prompt-after-appending-a-Steering-note>
```

Recompute a merely planned wave safely. If assignments or integration already
started, preserve the immutable active wave and return
`STEERING_QUEUED_AFTER_WAVE`; reconcile before promotion or at the next wave
boundary.

```text
$task-implementer run <same-prompt-after-a-worker-crash>
```

Re-observe coordinator/wave state, journals, refs, and worktrees. Retain exact
resources, do not dispatch new batch members after failure, and resume without
duplicating branches, worktrees, assignments, commits, or merges.

```text
$task-implementer run <managed-prompt-from-inside-a-$worktree-checkout>
```

Bind the run to the exact outer worktree branch and current `HEAD`; never create
a replacement outer worktree merely because one already exists. Keep every
worker/integration branch private and temporary, promote only back to the outer
branch, and block outer integration and removal until final alignment and
lease release. Then return the recorded primary path and exact
`$worktree integrate` command and stop for a fresh explicit user invocation
from the primary checkout; never invoke it internally, push it, or use it as a
PR head. Never use the remote default as the nested worker base.

```text
$task-implementer run <same-completed-prompt-after-an-interrupted-final-release>
```

Resume the private finalizer and release the existing outer lease only after
re-observing the final promoted head and absent internal resources. Do not
start a new run, expire the lease, or force-clear state.

```text
$task-implementer run <same-prompt-after-an-integration-conflict>
```

Confirm the primary branch remains at the recorded base, retain integration
and worker resources, and return `INTEGRATION_CONFLICT`. Do not partially merge
the wave into the project branch.

```text
$task-implementer run <same-completed-prompt-file>
```

For unchanged content, record activity and return `ALREADY_COMPLETE`. For
edited content, start a new internal run.

## Unsupported Public Actions

These must fail; do not translate them to hidden aliases:

```text
$task-implementer parallel <prompt-path>
$task-implementer merge <run-id>
$task-implementer cleanup <run-id>
$task-implementer upgrade <run-id>
$task-implementer workspace new "Add retries"
$task-implementer workspace list
$task-implementer prepare <prompt-path>
$task-implementer continue <run-id>
$task-implementer steer <steering-file>
```

Explain the two-command interface without exposing internal IDs.

## Should Not Trigger

```text
Run parallel agents to implement these independent fixes.
```

Use ordinary authorized delegation. Do not invoke `task-implementer` without
an explicit `$task-implementer` action.

```text
I opened an old Markdown prompt. Improve its wording.
```

Treat this as ordinary editing or brainstorming.

```text
Implement this small bug fix and run the focused test.
```

Use the normal implementation flow.

```text
This is complex; use global-context-management.
```

Use `global-context-management` only. It must not trigger this workflow.

```text
Review this diff, commit it, open a PR, or start Agentic SDLC.
```

Use the matching `code-review`, `$commit`, `create-pr`, or prompt-bound
`$sdlc-start run` workflow.
