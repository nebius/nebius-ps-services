# Prompt Workspace And V2 State Contract

Read this reference before initialization, intake routing, steering, recovery,
or private-state validation.

## Public Surface

The only public commands are:

```text
$task-implementer workspace init [project-folder]
$task-implementer run <prompt-path-or-unique-filename>
```

All helper commands are private mechanical transitions. Never ask the user for
prompt, run, wave, task, branch, worktree, assignment, or result IDs.

## Workspace Identity And Storage

Canonical Git root plus exact repo-relative scope determine one workspace:

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

Private `init` canonicalizes the repository and exact source scope, creates or
verifies workspace metadata, and creates exactly one starter prompt only when
no Markdown prompt exists. Repeated initialization preserves every prompt,
revision, run, result, branch, and worktree record. It never starts a run.

The generated workspace exposes `CODE` and `PROMPTS`. The editor task creates a
new prompt only; it does not invoke Codex.

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
- A terminal handoff whose managed outer lease was not released returns private
  `finalize`/`TASK_LEASE_RELEASE_REQUIRED` and resumes the same run.

Every validated, lock-acquired invocation updates private activity and the
handoff `Last invoked at`, including no-op and blocked outcomes. Rejected and
lock-busy calls do not reorder prompts.

## V3 Execution State Ownership

`handoff.md` is coordinator-owned. It contains the stable task queue,
requirements/design mappings, wave schedule, checkpoints, failure log, and
human-readable recovery status. Workers never edit it.

`coordinator.json` owns the run branch, initial `HEAD`, plan digest, ordered wave
index, active wave, and overall v3 execution status. Each wave file owns its base,
contract commit, integration identity, stable task order, task states,
promotion proof, and cleanup inventory.

Each mutable task plane records `planned -> assigned -> running -> committed -> merged|failed`,
the current worker session fingerprint, append-only session-fingerprint
history, assignment digest, result digest, and commit.

`interop.json` is strict private run state. For a `worktree`-managed outer
checkout it binds the run to the exact outer name, branch, path, outer/task
scope, v2 `task-implementer` owner lease identity, promoted head, and release
status. For an unmanaged
checkout it records only the unmanaged mode. Any malformed or mismatched state
fails closed.

Assignments are immutable and bind:

Their schema is `task-implementer/worker-assignment-v3`.

- run, wave, and task;
- exact base commit and plan digest;
- unique branch and full-repository worktree;
- absolute scope cwd inside that worktree;
- exact/prefix write claims and conflict domains;
- requirements, design, goal, plan, dependencies, rollback, and stop context;
- incoming-handoff path and digest;
- validation and done criteria;
- assignment digest and creation time.

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
prompt_workspace.py wave-plan --workspace <manifest> --run-id <id> --capacity <n>
prompt_workspace.py wave-replan --workspace <manifest> --run-id <id> --capacity <n>
prompt_workspace.py wave-prepare --workspace <manifest> --run-id <id>
prompt_workspace.py wave-dispatch --workspace <manifest> --run-id <id> --contract-commit <sha>
prompt_workspace.py batch-advance --workspace <manifest> --run-id <id>
prompt_workspace.py task-start --workspace <manifest> --run-id <id> --task-id <id> --assignment-sha256 <digest>
prompt_workspace.py task-recover --workspace <manifest> --run-id <id> --task-id <id> --confirmed-stopped
prompt_workspace.py task-finish --workspace <manifest> --run-id <id> --task-id <id>
prompt_workspace.py wave-integrate --workspace <manifest> --run-id <id>
prompt_workspace.py wave-promote --workspace <manifest> --run-id <id> --evidence <private-json>
prompt_workspace.py wave-cleanup --workspace <manifest> --run-id <id>
prompt_workspace.py run-finalize --workspace <manifest> --run-id <id> --alignment <summary>
```

These names and arguments are not public workflow commands. Human output stays
redacted; internal JSON may carry assignment paths to the coordinator.
`wave-replan` replaces only a clean planned wave that owns no Git resources.
`task-recover` requires confirmation that the previous worker stopped and
transfers only declared dirty state or one direct-child commit to a fresh
session. Blocked resources remain retained for operator-directed recovery;
they are never silently discarded.

`wave-plan` acquires the managed outer task lease before task resources exist.
Each integration/worker identity is recorded before creation and marked absent
only after real Git and filesystem removal. `run-finalize` requires all waves
done, no retained cleanup resources, a clean project branch at the final
promoted head, and non-empty final `$align` evidence before lease release.
An interrupted release is retried idempotently. Unfinished pre-interop runs in
a managed outer checkout return `WORKFLOW_UPGRADE_REQUIRED`; there is no
migration, stale timeout, PID recovery, or force-clear path.

Completed private prompt/run history may be archived only after the outer
worktree lifecycle has also been removed. Archive is history retention, not a
workspace identity migration; never rebind it to a primary or different linked
checkout.

## Legacy Boundary

Workspace, prompt, and run-manifest schemas are unchanged. The execution state
machine has no compatibility path: every
`task-implementer/execution-plane-v1` or coordinator-v1/v2 run returns
`WORKFLOW_UPGRADE_REQUIRED` without changing bytes or creating v3 resources,
including completed records. Do not add a legacy read path, migration, or
upgrade command.

## Sandbox And Bootstrap

Preflight write access to the private root and shared Git common directory.
Native subagents inherit the parent sandbox and are cooperative, not
worktree-confined. Verify absolute cwd, real worktree root, branch, base, and
assignment digest at every worker transition.

Do not copy ignored/untracked files, credentials, dotenv files, caches, local
tool state, or hooks from the primary checkout. Linked worktrees already share
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
