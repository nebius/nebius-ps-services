# Prompt Workspace And V2 State Contract

Read this reference before initialization, intake routing, steering, recovery,
or private-state validation.

## Public Surface

The only public commands are:

```text
$task-implementer workspace init [project-folder]
$task-implementer run <prompt-path-or-unique-filename>
$task-implementer integrate [project-folder]
$task-implementer workspace remove [project-folder]
```

All helper commands are private mechanical transitions. Never ask the user for
prompt, run, wave, task, branch, worktree, assignment, or result IDs.

## Workspace Identity And Storage

Canonical Git common directory, primary checkout, exact named source ref, and
repo-relative scope determine one logical workspace and persistent lane:

```text
${CODEX_HOME:-$HOME/.codex}/task-implementer/
├── projects/<project-id>/scopes/<scope-id>/
│   ├── workspace.json
│   ├── activity.json
│   ├── <scope>-prompts.code-workspace
│   ├── prompts/<created-at>--<slug>.md
│   └── runs/<run-id>/
│       ├── manifest.json
│       ├── steering.json
│       ├── inputs/<revision>/prompt.md
│       ├── handoff.md
│       └── orchestration/
│           ├── coordinator.json
│           ├── interop.json
│           ├── waves/wave-001.json
│           ├── tasks/wave-001/task-1.json
│           ├── assignments/wave-001/task-1.json
│           ├── incoming-handoffs/wave-001/task-1.json
│           ├── results/wave-001/task-1.json
│           └── journals/wave-001.jsonl
└── worktrees/<project-id>/<scope-id>/<run-id>/wave-001/
    ├── integration/
    ├── task-1/
    └── task-2/
```

The private root must be outside Git storage. On POSIX, managed directories are
`0700` and files are `0600`. Reject symlinks, traversal, foreign paths, unsafe
permissions, malformed UTF-8/JSON/Markdown, duplicate identities, and state
whose canonical paths no longer match its manifest.

Prompts remain `task-implementer/prompt-v1`, with a maximum of 256 KiB. Prompt
filenames and workflow-managed mtimes never change. The filename date is
creation metadata; accepted invocation ordering comes from private
`last_invoked_at` activity.

## Initialization

Private lane ensure requires configured `origin/HEAD`, an attached named source
branch different from that default, and an exact scope present at committed
`HEAD`. It creates one full-repository linked
worktree from that commit or reuses the existing lane for the logical identity.
Dirty source-checkout state is permitted, excluded, and never copied.

Private `init` canonicalizes the lane and exact source scope, creates or
verifies workspace-v2 metadata, and creates exactly one starter prompt only
when no Markdown prompt exists. Repeated initialization preserves every prompt,
revision, run, result, branch, and worktree record. After explicit removal, it
may rebind only the same logical workspace to a strictly newer lane
incarnation. It never starts a run.

The generated workspace exposes `CODE` and `PROMPTS`. The editor task creates a
new prompt only; it does not invoke Codex.

After successful initialization, the helper asks VS Code to reuse its last
active window for the generated workspace. The CLI does not bind this request
to an exact invoking window when several windows are open. Loading the workspace
restarts that window's extension host and may interrupt its terminal or Codex
UI. An unavailable, timed-out, or unsuccessful editor launch warns without
invalidating initialization.

## Intake And Steering

Private `intake` resolves a managed absolute prompt path or unique filename and
returns `new`, `continue`, `reconcile`, `steering_queued_after_wave`,
`finalize`, or `done`.

- Acquire the scope lock before mutable private-state changes.
- One unfinished prompt owns a scope.
- Snapshot each adjacent changed digest as one immutable revision.
- Record steering dispositions as `pending`, `applied`, `blocked`, or
  `no_effect`.
- A planned wave may be recomputed before resources exist. Once a wave is
  preparing, running, integrating, or promotion-pending, its assignments remain
  immutable and new steering queues for the next safe join boundary.
- Contradictory steering preserves work and stops before promotion with
  `STEERING_QUEUED_AFTER_WAVE` or `HUMAN_INPUT_REQUIRED`.
- An unchanged completed prompt returns `ALREADY_COMPLETE`; an edited completed
  prompt starts a new run.
- A terminal handoff whose lane generation was not released returns private
  `finalize`/`TASK_LEASE_RELEASE_REQUIRED` and resumes the same run.

Every validated, lock-acquired invocation updates private activity and the
handoff `Last invoked at`, including no-op and blocked outcomes. Rejected and
lock-busy calls do not reorder prompts.

## V4 Execution State Ownership

`handoff.md` is coordinator-owned. It contains the stable task queue,
requirements/design mappings, wave schedule, checkpoints, failure log, and
human-readable recovery status. Workers never edit it.

`coordinator.json` owns the run branch, initial `HEAD`, plan digest, ordered wave
index, active wave, and overall v4 execution status. Each wave file owns its base,
contract commit, integration identity, stable task order, task states,
promotion proof, and cleanup inventory.

Each mutable task plane records `planned -> assigned -> running -> committed -> merged|failed`,
the current worker session fingerprint, append-only session-fingerprint
history, assignment digest, result digest, and commit.

`interop.json` is strict schema-v4 private run state. It binds the run to the
exact lane ID, incarnation, monotonic generation, lane name/branch/path,
outer/task scope, Worktree-owned lease token, promoted head, and release status.
There is no unmanaged execution mode. Any malformed or mismatched state fails
closed. Existing local state is never authoritative by itself: resume,
promotion, and release inspect the exact durable generation/lease and live
clean lane head, repair only exact external-first crash windows, and reject
stale local promoted/released values.

Assignments are immutable and bind:

Their schema is `task-implementer/worker-assignment-v7`; v6 and older
assignments are a hard-cut unsupported execution surface.

- run, wave, and task;
- exact base commit and plan digest;
- unique branch and full-repository worktree;
- absolute scope cwd inside that worktree;
- exact helper and workspace-manifest paths for the first transition;
- exact/prefix write claims and conflict domains;
- requirements, design, goal, plan, implementation steps, end-to-end
  validation, dependencies, rollback, and stop context;
- canonical worktree/network/credential/external-service guardrails that may be
  relaxed only by exact immutable-assignment authorization, plus read/execute-
  only use of required installed skill instructions/helpers and standard local
  executables;
- immutable `standard` 240/300-second or dependent `integration`
  360/420-second read-only warning/timeout profile, plus 60-second
  dispatch-to-start, 30-second heartbeat, 240-second stale-heartbeat, and total
  worker budgets;
- incoming-handoff path and digest;
- validation and done criteria;
- assignment digest and creation time.

The worker passes the embedded assignment digest unchanged to `task-start`
through those exact paths. That helper validates the canonical unsigned
assignment digest and Git/cwd identity; workers must not guess or recompute the
JSON serialization before starting.

The referenced `incoming-handoff-v1` record is private, immutable, and
digest-bound. It contains an ordered list of accepted results from all earlier
completed waves and capacity batches. Only the first batch of the first wave
has an empty list. Workers write one
`worker-result-v3` record with summary, decisions, and open risks. The coordinator distrusts it and
re-observes the branch, commit ancestry, cleanliness, and changed paths before
acceptance.

## Hidden Mechanical Commands

The skill may invoke:

```text
prompt_workspace.py init [project-folder]
prompt_workspace.py integrate [project-folder]
prompt_workspace.py remove [project-folder]
prompt_workspace.py wave-plan --workspace <manifest> --run-id <id> --capacity <n>
prompt_workspace.py wave-replan --workspace <manifest> --run-id <id> --capacity <n>
prompt_workspace.py wave-prepare --workspace <manifest> --run-id <id>
prompt_workspace.py wave-dispatch --workspace <manifest> --run-id <id> --contract-commit <sha>
prompt_workspace.py batch-advance --workspace <manifest> --run-id <id>
prompt_workspace.py task-arm --workspace <manifest> --run-id <id> --task-id <id>
prompt_workspace.py task-start --workspace <manifest> --run-id <id> --task-id <id> --assignment-sha256 <digest>
prompt_workspace.py task-heartbeat --workspace <manifest> --run-id <id> --task-id <id> --assignment-sha256 <digest> --phase <phase>
prompt_workspace.py task-watch --workspace <manifest> --run-id <id> --task-id <id>
prompt_workspace.py task-recover --workspace <manifest> --run-id <id> --task-id <id> --confirmed-stopped
prompt_workspace.py task-finish --workspace <manifest> --run-id <id> --task-id <id>
prompt_workspace.py wave-integrate --workspace <manifest> --run-id <id>
prompt_workspace.py wave-promote --workspace <manifest> --run-id <id> --evidence <private-json>
prompt_workspace.py wave-cleanup --workspace <manifest> --run-id <id>
prompt_workspace.py run-finalize --workspace <manifest> --run-id <id> --alignment <summary>
```

These names and arguments are not public workflow commands. Human output stays
redacted; internal JSON may carry assignment paths to the coordinator.
`wave-replan` replaces only a clean planned tail that owns no Git resources.
The coordinator index keeps completed history plus the replacement schedule;
superseded planned wave files remain blocked, non-indexed history. It extends
the active lane generation's repository claims before writing replacement
state and retains earlier claims until generation integration.
After the last promoted wave is cleaned, `wave-replan` may append a new
isolated correction tail discovered by integration review before finalization.
`task-recover` requires confirmation that the previous worker stopped and must
be invoked by the fresh replacement worker from the assigned scope cwd. The
coordinator communicates the stop confirmation but never invokes recovery for
the replacement because heartbeat and finish ownership bind to the caller.
Recovery transfers only declared dirty state or one direct-child commit.
Blocked resources remain retained for operator-directed recovery;
they are never silently discarded.

`wave-plan` acquires the next lane-generation lease and its repository-wide
claims before task resources exist. Exclusive external database, Kubernetes,
Terraform, migration execution, and publication classes add class-wide domain
claims so differently keyed tasks in separate lanes still conflict.
Each integration/worker identity is recorded before creation and marked absent
only after real Git and filesystem removal. `run-finalize` requires all waves
done, no retained cleanup resources, a clean lane branch at the final promoted
head, and non-empty final `$align` evidence before generation release. An
interrupted release is retried idempotently. Unfinished pre-interop runs return
`WORKFLOW_UPGRADE_REQUIRED`; there is no
migration, stale timeout, PID recovery, or force-clear path.

Generation release persists an immutable receipt before retiring the terminal
schema-v4 coordinator lease. Exact lane/incarnation/generation/token/promoted-
head replay is accepted; missing or different state is a conflict. Successive
wave promotions append to the lease's ordered head history through expected-
head compare-and-set. Pending generation receipts and claims remain until
`$task-implementer integrate` consumes their contiguous range.

`workspace remove` deletes only an idle, clean, fully integrated lane through
non-forced exact-head proof. It preserves completed private prompt/run and
generation history. A later initialization may rebind that same logical
workspace only to the next recorded lane incarnation.

## Legacy Boundary

Workspace-v1 is unsupported and must be backed up/removed before lane
initialization. Prompt and run-manifest schemas are unchanged. The execution
state machine has no compatibility path: every
`task-implementer/execution-plane-v1` or coordinator-v1/v2/v3/v4/v5 run returns
`WORKFLOW_UPGRADE_REQUIRED` without changing bytes or creating v6 resources,
including completed records. Do not add a legacy read path, migration, or
upgrade command.

## Sandbox And Bootstrap

Preflight write access to the private root and shared Git common directory.
Native subagents inherit the parent sandbox and are cooperative, not
worktree-confined. Verify absolute cwd, real worktree root, branch, base, and
assignment digest at every worker transition.

Do not copy ignored/untracked files, credentials, dotenv files, caches, local
tool state, or hooks from the source checkout. Linked worktrees already share
repository objects, refs, config, and hooks. If a safe required bootstrap input
is unavailable, stop with `ENVIRONMENT_BLOCKER`. If persistent private-root
access is missing, report the narrow launch option:

```bash
codex --add-dir "${CODEX_HOME:-$HOME/.codex}/task-implementer"
```

Never widen permissions automatically.

## Stable Errors

- `WORKSPACE_NOT_FOUND`, `WORKSPACE_MISMATCH`, `WORKSPACE_PATH_INVALID`,
  `WORKSPACE_PERMISSION_INVALID`, `WORKSPACE_BUSY`
- `PROMPT_NOT_FOUND`, `PROMPT_AMBIGUOUS`, `PROMPT_CONFLICT`, `PROMPT_DRIFT`,
  `PROMPT_TOO_LARGE`
- `RUN_STATE_INVALID`, `EXECUTION_STATE_INVALID`,
  `WORKFLOW_UPGRADE_REQUIRED`
- `DEPENDENCY_CYCLE`, `REPLAN_REQUIRED`, `UNSUPPORTED_SUBMODULE_SCOPE`,
  `UNSUPPORTED_SYMLINK_SCOPE`
- `WORKTREE_COLLISION`, `WORKTREE_CONFLICT`, `GIT_OPERATION_FAILED`
- `INTEGRATION_CONFLICT`, `INTEGRATION_VALIDATION_FAILED`
- `PROMOTION_BLOCKED`, `PROMOTION_FAILED`, `CLEANUP_BLOCKED`
- `ENVIRONMENT_BLOCKER`, `HUMAN_INPUT_REQUIRED`,
  `STEERING_QUEUED_AFTER_WAVE`

Retry only after re-observing durable state. Never silently repair malformed
state or delete recovery resources.
