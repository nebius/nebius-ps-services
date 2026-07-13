# Private Prompt Workspace

Use this reference for the file-first intake lifecycle owned by
`task-implementer`. Users see two Skill actions; prompt IDs, run IDs, immutable
revisions, locks, and handoffs are private implementation state.

## Storage And Identity

Store prompt workspaces outside every Git worktree:

```text
${CODEX_HOME:-$HOME/.codex}/task-implementer/
└── projects/
    └── <repo-name>-<canonical-root-hash>/
        └── scopes/
            └── <scope-slug>-<scope-hash>/
                ├── workspace.json
                ├── activity.json
                ├── <scope-slug>-prompts.code-workspace
                ├── prompts/
                │   └── <created-at>--<ask-slug>.md
                └── runs/
                    └── <internal-run-id>/
                        ├── manifest.json
                        ├── steering.json
                        ├── inputs/<revision>/prompt.md
                        ├── execution/task-n.json
                        └── handoff.md
```

`workspace.json` is the workspace identity record. It contains the schema,
canonical Git root, repo-relative scope, source root, prompt root, runs root,
project ID, and scope ID. Hash the resolved Git root so different clones remain
isolated. The scope is the exact canonical project folder passed to
`workspace init`, or the exact current directory when omitted. Relative and
absolute references to the same folder must resolve to the same workspace.

Reject storage that resolves inside any Git worktree or Git metadata directory.
Create managed directories as `0700` and files as `0600` on POSIX systems.
Reject symlinks, path traversal, path escapes, unsafe modes, malformed state,
and moved source roots. Treat SHA-256 as drift detection, not encryption.

## VS Code Workspace

Generate one saved multi-root workspace per project scope:

1. `CODE`: the canonical source scope. Keep it first for extensions that use
   the first root when they do not support multi-root workspaces.
2. `PROMPTS`: the private flat prompt directory.

Initialization opens the saved workspace when VS Code is available. Editor
launch failure is non-fatal and must not roll back valid private state.

Generate exactly one manual workspace task named
`Task Implementer: New Prompt`. It is an editor convenience for additional
prompts. It must be a VS Code `process` task, pass arguments as an array, and
use one `promptString` input for the short ask. It must not auto-run, change
Workspace Trust, install an extension, start Codex, or submit prompt content.

## Prompt Files

Keep `prompts/` flat. Create one editable Markdown file per independent ask;
one prompt can produce many implementation tasks. Do not use task files,
includes, prompt bundles, a database, or one ever-growing project prompt.

Name new files with local creation time and a deterministic slug:

```text
YYYY-MM-DD_HHmm--<slug>.md
```

Normalize the short ask to lowercase ASCII, join at most eight words with
hyphens, retain at most 60 slug characters, and fall back to `prompt`. Resolve
collisions with `--02`, `--03`, and so on. The filename date is creation
metadata only. Never rename a prompt, rewrite it, or deliberately change its
mtime during initialization, submission, ordering, or retry.

Use `assets/prompt-template.md`. Require strict scalar frontmatter with schema
`task-implementer/prompt-v1`, an internally generated unique `prompt-<uuid>`
identity, a title, and a timezone-aware creation timestamp. The user never
supplies that identity to a Skill command. Before submission, require non-empty
`Ask`, `Outcome`, `Acceptance criteria`, and `Verification` sections. `Context`,
`Non-goals`, `References`, and `Steering` may be empty.

Users may append clarifications, corrections, priorities, removals, or new
requirements under `Steering` without IDs or timestamps, then repeat the same
`run` command. Edits elsewhere remain valid; the complete prompt is current
desired state. The Skill never marks steering as consumed inside the editable
file.

Reject unresolved template instructions, duplicate frontmatter keys or prompt
identities, invalid UTF-8, NUL bytes, files larger than 256 KiB, non-Markdown
inputs, symlinks, and paths outside the managed prompt root. A manually renamed
prompt preserves identity only when it is the sole managed prompt with those
bound bytes and identity.

Prompts are private local files, not encrypted secret stores. Do not put
credentials, tokens, private keys, customer data, or confidential copied
material in them. The Skill may validate and snapshot a source prompt but must
not edit it.

## Public Skill Actions

```text
$task-implementer workspace init [project-folder]
$task-implementer run <prompt-path-or-unique-filename>
```

There are no public creation, listing, preparation, continuation,
reconciliation, steering, run-ID, or new-run actions. The editor task creates
additional prompts. The same `run` command performs every safe submission,
steering, retry, and continuation transition.

## Internal Mechanical Helper

The standard-library-only helper is `scripts/prompt_workspace.py`. It manages
private files but never decomposes tasks, starts Codex, edits product code, or
prints prompt bodies. Its subcommands are Skill implementation details, not
additional public actions.

For initialization, the Skill invokes:

```bash
python3 <skill-dir>/scripts/prompt_workspace.py init \
  [<project-folder>] --json
```

Omit the project folder to use the helper process's exact current directory.
Initialization returns the canonical workspace and editor paths, starter
creation result, and submission-ordered prompt metadata. Opening is enabled by
default; internal `--no-open` suppresses it only for validation or automation.

For every user `run`, the Skill invokes the private intake router:

```bash
python3 <skill-dir>/scripts/prompt_workspace.py intake \
  <prompt-path-or-unique-filename> --project-path <current-project> \
  --internal-json
```

The router validates prompt ownership and content before mutation, acquires the
scope lock, verifies internal state, records accepted activity, and returns one
private action: `new`, `continue`, `reconcile`, `reconcile_planning`,
`steering_queued`, or `done`. `steering_queued` also returns user outcome
`STEERING_QUEUED_AFTER_TASK`. Explicit internal machine output may contain IDs
and private paths needed by the agent; never reproduce that payload in
user-facing output. Ordinary `--json` and human output omit it.

After queue creation or reconciliation, the Skill owns these private execution
transitions:

```bash
python3 <skill-dir>/scripts/prompt_workspace.py plane-claim \
  --workspace <workspace.json> --run-id <internal-run-id> --json

python3 <skill-dir>/scripts/prompt_workspace.py plane-authorize \
  --workspace <workspace.json> --run-id <internal-run-id> --json

python3 <skill-dir>/scripts/prompt_workspace.py plane-replan \
  --workspace <workspace.json> --run-id <internal-run-id> --json

python3 <skill-dir>/scripts/prompt_workspace.py plane-checkpoint \
  --workspace <workspace.json> --run-id <internal-run-id> --json

python3 <skill-dir>/scripts/prompt_workspace.py steering-resolve \
  --workspace <workspace.json> --run-id <internal-run-id> \
  --revision <internal-revision> \
  --disposition applied|blocked|no_effect --json

python3 <skill-dir>/scripts/prompt_workspace.py spec-inspect \
  --workspace <workspace.json> --json
```

`plane-replan` is valid only for the same session's clean, un-authorized
planning plane. It rebinds the same task to the latest immutable revision,
clears required plan fields, and requires reconciliation before authorization.
It never changes an implementation or stopped plane. `steering-resolve`
changes only mutable disposition state and cannot mark a revision applied or
no-effect until the handoff binds it.

`spec-inspect` validates both exact project-relative specification paths,
managed markers, schemas, stable IDs, mappings, Agentic SDLC ownership,
private-state redaction, and user-owned envelopes. It returns only
repo-relative paths, managed-region digests, IDs, mappings, and next IDs for
the locked plan and checkpoint.

These commands use runtime-provided `CODEX_THREAD_ID` automatically and persist
only SHA-256 fingerprints. This is a cooperative correlation guard, not a
cryptographic identity. Internal tests may inject a session identifier; users
never do. When a previous session was interrupted, the Skill may add private
`--recover --confirmed-recovery-worktree-sha256 <digest>` to `plane-claim` only
after explicit confirmation that the prior writer is gone and after reviewing
the exact worktree digest and changed paths against the locked task allowlist.
Without digest-bound confirmation, recovery fails with `HUMAN_INPUT_REQUIRED`
and cannot transfer ownership.

The filesystem lock serializes each transition. The execution plane then keeps
the selected task exclusively owned while the filesystem lock is released for
planning and product implementation. Another session receives `WORKSPACE_BUSY`;
after checkpointing, every session fingerprint that participated in a
different completed task in the scope receives `FRESH_SESSION_REQUIRED`,
including after recovery or an intervening fresh session.

Internal `new`, `list`, `snapshot`, `verify`, and execution-plane helper
operations may remain for editor integration, state construction, recovery,
validation, and tests. Users must not be asked to call them. They must preserve
the same path, permission, symlink, digest, lock, and redaction checks.

## Initialization Contract

`workspace init` is safe to repeat:

1. Resolve and validate the exact project folder, Git root, and repo-relative
   scope.
2. Create or verify the private directory tree and `workspace.json`.
3. Regenerate only derived VS Code workspace/task metadata when repair is
   needed.
4. Under the scope lock, create one starter prompt only when no managed prompt
   exists.
5. Never delete, rename, rewrite, duplicate, or touch prompts, revisions, run
   history, or handoffs.
6. Return workspace paths and the newest-first prompt table.

Initialization must leave the Git worktree unchanged. Paths containing spaces
and equivalent relative/absolute project paths are supported.

## Prompt Resolution

`run` accepts:

- an absolute path to one prompt in the current workspace; or
- a basename that matches exactly one prompt in the current workspace.

Resolve the current workspace from the exact current project folder. Fail
before state changes when initialization is missing, a basename is ambiguous,
the prompt belongs to another workspace, a path traverses or escapes, a file is
a symlink, permissions are unsafe, the extension is not `.md`, or content is
invalid. Do not accept internal IDs as prompt references.

## Internal Ownership Model

```text
editable prompt with stable internal identity
    -> one or more historical internal runs
        -> one or more immutable revisions
            -> ordered private steering dispositions
            -> one bound execution revision
                -> one handoff queue containing task-1..task-n
                    -> one execution plane per claimed task
```

There is no prompt-to-task 1:1 mapping. The manifest is append-only revision
metadata: internal identity, source filename, exact digest, revision path, and
snapshot time. Do not store mutable run status in it. `steering.json` records
each accepted revision at most once with `pending`, `applied`, `blocked`, or
`no_effect` disposition; it never stores prompt content. The handoff is the
execution truth for queue order, active task, run status, checkpoints,
requirement/design mappings, reconciliation overrides, and `Last invoked at`.
Each `execution/task-n.json` is the
durable ownership and phase record for exactly one task. It binds the task,
revision, runtime session participant history, claim-time worktree digest,
locked plan and queue digests, immutable stopped-checkpoint digest, timestamps,
recovery evidence, and mandatory stop state.

Before claiming any next task, validate every stopped plane against its
completed task, globally unique checkpoint index, exact commit evidence, and
checkpoint digest. Use the handoff's unique `Run.Last completed task` to select
the immediate predecessor; second-resolution timestamps are activity metadata,
not execution order.

For plane-backed runs, require a one-to-one index between `done` tasks,
populated checkpoints, and stopped planes. Reject orphan checkpoints and
fabricated completed tasks. Legacy completed runs without execution planes
remain readable and non-destructive but do not gain fabricated plane history.

The handoff's `## Run` section binds execution to one manifest revision and
digest. Verification parses the binding only from `## Run`. An appended
revision is not executable until reconciliation atomically rebinds the handoff.

Implementation reads only the immutable bound snapshot. It may compare the
editable source for drift, but source edits never rewrite an existing snapshot
or completed task.

## Managed Specification Documents

The only committed specification paths are relative to the initialized source
scope:

```text
docs/requirements.md
docs/design.md
```

When absent, create compact documents from the managed-region assets. When a
generic document exists, append exactly one managed region and preserve every
existing byte as its user-owned envelope. On later updates, replace only the
managed body and preserve the prefix, suffix, Unicode, and newline style.

Use schemas `task-implementer/requirements-v1` and
`task-implementer/design-v1`, IDs `TI-REQ-nnn` and `TI-DES-nnn`, and monotonic
scope-wide counters recovered from committed managed regions. Never renumber or
delete IDs. Mark removed requirements `superseded`; append corrective designs
instead of rewriting implemented history.

Fail before repository edits when a file or `docs` ancestor is a symlink, a
path escapes the source scope, a document is invalid UTF-8, markers are absent
on only one side, duplicated, or reversed, IDs or mappings are invalid, or a
managed body contains private prompt/run/revision paths or identities. Exact
Agentic SDLC frontmatter schemas produce `SPEC_OWNER_CONFLICT`; do not share
ownership or silently select alternate paths.

## Single Run Transition

After validation and lock acquisition, route the prompt as follows:

- No historical run: create an immutable revision and new internal run, build
  the queue, and claim `task-1` in phase `planning`.
- Unchanged unfinished prompt: verify current evidence and claim the next
  dependency-ready task, or resume the already claimed task.
- Edited unfinished prompt: append an immutable revision exactly once,
  record it as pending steering, reconcile requirements and pending tasks while
  preserving completed work and stable IDs, resolve processed revisions, and
  claim the next safe task in `planning`.
- Edited same-owner planning task with an unchanged clean baseline: append the
  revision, privately rebind the same task, clear its unfinished plan,
  reconcile it, and authorize only after resolving the disposition.
- Edited planning task owned by another session, or any edited implementation
  task: append the revision and disposition without rebinding the handoff,
  plane, plan, queue, or checkpoint. Return
  `STEERING_QUEUED_AFTER_TASK` and apply it after the task stops.
- Multiple pending edits: append each digest that differs from the immediately
  preceding revision. Preserve A-B-A as three historical states; an unchanged
  retry appends nothing. Reconcile pending events in order toward the latest
  desired state.
- Edited prompt contradicts completed work or leaves an ambiguous contract:
  update accepted activity, then stop with `HUMAN_INPUT_REQUIRED` before
  product edits.
- Interrupted task: resume that task or reconstruct the missing checkpoint
  from verified commit evidence. Never duplicate a revision, edit, or commit.
- Completed unchanged prompt: update activity and return `ALREADY_COMPLETE`
  without product changes.
- Completed edited prompt: create a new internal run and implement its first
  task.
- Another prompt has unfinished work in the same scope: fail closed with the
  active prompt path. Do not expose its run ID.

If an interrupted reconciliation already appended the same latest digest,
reuse its revision and disposition. A later different edit may append another
pending revision; never discard or reorder earlier events. Formatting-only
changes may resolve as `no_effect`, rebind execution truth, and make no task,
document, product, or commit change.

## Activity Ordering And Output

Every validated, lock-acquired `run` invocation records a timezone-aware
`last_invoked_at` in mutable handoff/activity state. Accepted continuation,
queued steering, reconciliation, blocked, and completed-no-op invocations all
move the prompt to the top. Validation failures and lock-busy calls do not reorder prompts.

Sort output by:

1. `last_invoked_at`, newest first;
2. prompt creation time, newest first;
3. canonical prompt path for deterministic ties.

Draft prompts without runs use creation time as their activity fallback. Both
initialization and run output include only:

- last invocation
- status
- title
- prompt path

Never output prompt bodies, internal prompt IDs, run IDs, revision IDs, digests,
snapshot paths, manifests, handoffs, or lock paths as list metadata.

## Stable Failure Classification

Surface the helper's stable token and stop on unsafe input or state:

- `REPO_ROOT_INVALID`, `SCOPE_INVALID`, `WORKSPACE_NOT_FOUND`,
  `WORKSPACE_PATH_INVALID`, `WORKSPACE_STATE_INVALID`, or
  `WORKSPACE_PERMISSION_INVALID`: workspace identity, path, schema, or
  permissions are unsafe.
- `PROMPT_INPUT_INVALID` or `PROMPT_CONFLICT`: prompt path, type, content,
  uniqueness, or identity is invalid.
- `ACTIVE_RUN_EXISTS`: another prompt owns unfinished scope work; show only its
  prompt path.
- `PROMPT_DRIFT`: immutable binding and editable source differ where the
  requested internal transition cannot safely proceed.
- `WORKSPACE_BUSY`: another process holds the scope lock; do not reorder.
- `RUN_STATE_INVALID`: manifest, revision, digest, or handoff is malformed or
  incompatible with the transition.
- `EXECUTION_STATE_INVALID`: task queue or execution-plane state is malformed,
  unsafe, or inconsistent.
- `SESSION_ID_UNAVAILABLE`: no runtime session identifier is available for the
  cooperative fresh-session guard.
- `PLAN_REQUIRED`: required task planning fields are incomplete.
- `PLAN_LOCKED`: the authorized plan changed before checkpointing.
- `CHECKPOINT_REQUIRED`: validation, review, commit, next-task, or session-stop
  evidence is incomplete or inconsistent.
- `FRESH_SESSION_REQUIRED`: the completed task's session attempted to claim
  another task.
- `HUMAN_INPUT_REQUIRED`: reconciliation would contradict completed work or
  requires a consequential decision.
- `STEERING_QUEUED_AFTER_TASK`: accepted steering is durably pending while an
  active execution plane remains unchanged.
- `SPEC_OWNER_CONFLICT`: Agentic SDLC owns an exact specification path.
- `SPEC_CONFLICT`: a specification path, marker, ID, mapping, managed body, or
  user-owned envelope is unsafe or inconsistent.

Do not repair malformed state by guessing or silently replacing it. Record a
failure in the handoff only after the invocation has passed validation and lock
acquisition.

## Sandbox Access

Private state may be outside the active workspace-write root. Prefer the
opt-in `config-codex` setup contract for persistent access. Do not weaken an
existing sandbox or approval policy. When access is missing, report:

```bash
codex --add-dir "${CODEX_HOME:-$HOME/.codex}/task-implementer"
```

Do not create repository-local prompt storage as a fallback.

## References

- [VS Code multi-root workspaces](https://code.visualstudio.com/docs/editing/workspaces/multi-root-workspaces)
- [VS Code tasks](https://code.visualstudio.com/docs/debugtest/tasks)
- [VS Code input variables](https://code.visualstudio.com/docs/reference/variables-reference)
- [VS Code Workspace Trust](https://code.visualstudio.com/docs/editing/workspaces/workspace-trust)
- [Codex IDE extension](https://developers.openai.com/codex/ide)
- [OpenAI Build Skills](https://learn.chatgpt.com/docs/build-skills#optional-metadata)
