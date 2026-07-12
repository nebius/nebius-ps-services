# Private Prompt Workspace

Use this reference for the file-first intake lifecycle owned by
`task-implementer`. The editable prompt captures intent; an immutable revision
binds a run; the handoff owns the executable queue and mutable run status.

## Storage And Identity

Store prompt workspaces outside every Git worktree:

```text
${CODEX_HOME:-$HOME/.codex}/task-implementer/
└── projects/
    └── <repo-name>-<canonical-root-hash>/
        └── scopes/
            └── <scope-slug>-<scope-hash>/
                ├── workspace.json
                ├── <scope-slug>-prompts.code-workspace
                ├── prompts/
                │   └── <created-at>--<ask-slug>.md
                └── runs/
                    └── <run-id>/
                        ├── manifest.json
                        ├── inputs/
                        │   └── <revision>/prompt.md
                        └── handoff.md
```

`workspace.json` is the workspace identity record. It contains the schema,
canonical Git root, repo-relative scope, source root, prompt root, runs root,
project ID, and scope ID. Hash the resolved Git root so different clones remain
isolated. Moving a checkout changes its identity and requires `workspace init`
again in v1.

Reject storage that resolves inside any Git worktree or Git metadata directory.
Create managed directories as `0700` and files as `0600` on POSIX systems. Reject symlinks,
path traversal, path escapes, unsafe modes, malformed state, and moved source
roots. Treat SHA-256 as drift detection, not encryption.

## VS Code Workspace

Generate one saved multi-root workspace per repository scope:

1. `CODE`: the canonical source scope. Keep it first for extensions that use
   the first root when they do not support multi-root workspaces.
2. `PROMPTS`: the private flat prompt directory.

Treat the saved workspace as editor convenience only. Skill actions must use
explicit workspace manifest, repository, scope, prompt, run, and handoff paths;
do not depend on an editor's multi-root semantics.

Generate exactly one manual workspace task named
`Task Implementer: New Prompt`. It must be a VS Code `process` task, pass
arguments as an array, and use one `promptString` input for the short ask. It
must not auto-run, change Workspace Trust, install an extension, start Codex,
or submit prompt content.

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
collisions with `--02`, `--03`, and so on. Never rename a prompt automatically
after edits. Its `prompt_id` is the durable identity and the filename date is
the creation date.

A manual rename preserves identity only when exactly one managed prompt has
the same bound bytes and prompt ID. Missing content is `PROMPT_DRIFT`; duplicate
prompt IDs are `PROMPT_CONFLICT`, even when their contents differ.

Use `assets/prompt-template.md`. Require strict scalar frontmatter with schema
`task-implementer/prompt-v1`, a unique `prompt-<uuid>` ID, a title, and a
timezone-aware creation timestamp. Before submission, require non-empty `Ask`,
`Outcome`, `Acceptance criteria`, and `Verification` sections. `Context`,
`Non-goals`, and `References` may be empty. Reject unresolved template
instructions, duplicate frontmatter keys or prompt IDs, invalid UTF-8, NUL
bytes, files larger than 256 KiB, symlinks, and paths outside the managed prompt
root.

Prompts are private local files, not encrypted secret stores. Do not put
credentials, tokens, private keys, customer data, or confidential copied
material in them. The Skill may read and snapshot a source prompt but must not
edit it.

## Mechanical Helper

The standard-library-only helper is `scripts/prompt_workspace.py`. It manages
private files but never decomposes tasks, starts Codex, edits product code, or
prints prompt bodies.

Use its public commands through absolute paths resolved from the installed
Skill directory:

```bash
python3 <skill-dir>/scripts/prompt_workspace.py init \
  --repo-root <git-root> --scope <repo-relative-scope> --json

python3 <skill-dir>/scripts/prompt_workspace.py new \
  --workspace <workspace.json> --ask '<short ask>' --json

python3 <skill-dir>/scripts/prompt_workspace.py list \
  --workspace <workspace.json> --json

python3 <skill-dir>/scripts/prompt_workspace.py snapshot \
  --workspace <workspace.json> --prompt <prompt.md> --json

python3 <skill-dir>/scripts/prompt_workspace.py verify \
  --workspace <workspace.json> --run-id <run-id> --json
```

Add `--open` only for explicit `workspace init` or `workspace new` requests.
Use `list --query <text>` or `list --date YYYY-MM-DD` for discovery. Human and
JSON output report creation, modification, last-submission, run status, prompt
identity, title, and path metadata, never prompt bodies.

For reconciliation, snapshot into an unfinished run:

```bash
python3 <skill-dir>/scripts/prompt_workspace.py snapshot \
  --workspace <workspace.json> --prompt <prompt.md> \
  --run-id <run-id> --json
```

For an explicit exact rerun after completion, use `--new-run`. Do not combine
`--run-id` and `--new-run`.

## Ownership Model

```text
editable prompt with stable prompt_id
    -> one or more historical runs
        -> one or more immutable revisions
            -> one bound revision for execution state
                -> one handoff queue containing task-1..task-n
```

There is no prompt-to-task 1:1 mapping. The manifest is append-only revision
metadata: identity, source filename, exact digest, revision path, and snapshot
time. Do not store mutable run status in it. The handoff is the execution truth
for queue order, active task, run status, checkpoints, and reconciliation.

The handoff's `## Run` section binds execution to one manifest revision and
digest. Later checkpoint sections may repeat revision evidence; verification
must parse the binding only from `## Run`. A newly appended reconciliation
revision is not executable until the handoff is rebound.

`run` and `continue` read only the immutable bound snapshot recorded in the
handoff. They may compare the editable source for drift, but source edits never
alter an active run implicitly.

## Lifecycle

### `workspace init <scope>`

Resolve the Git root and scope, create or verify the private workspace, and
return its explicit paths. Opening the generated VS Code workspace is optional
and only occurs when the user requests it.

### `workspace new "<short ask>"`

Resolve the workspace for the current repository scope, create one prompt
atomically from the template, return its path, and optionally open it. The
short ask is used once for its title and deterministic filename.

### `prepare <prompt-path>`

1. Resolve and verify the owning workspace and prompt.
2. Snapshot the exact prompt bytes. A normal first submission creates a new run
   and `r0001`.
3. Create `handoff.md` from the handoff asset without changing product files.
4. Inspect repository instructions, code, tests, docs, and current Git state.
5. Build a reviewable dependency-first `task-1..task-n` queue from the bound
   snapshot and repository evidence.
6. Record status `prepared`, prompt identity, source path, bound revision,
   digest, manifest path, and reconciliation state.
7. Stop. Preparing never approves or implements a task.

The mechanical snapshot exists before semantic queue preparation. Until a
valid handoff is written, list and verify report `snapshot_only`, not
`prepared`. If preparation is interrupted at that boundary, a repeated
same-prompt, same-digest `prepare` may resume the verified snapshot-only run.
It must not create another revision or resume a run for another prompt.

### `run <run-id>`

Verify the run and exact bound revision, confirm the queue is `prepared`, then
implement exactly the first pending task through the per-task context, design,
plan, implementation, validation, review, fix, commit, and checkpoint loop.
Stop after saving the handoff.

### `continue <run-id>`

In a fresh session, verify the handoff and immutable bound revision, then
implement exactly the next pending task. Never implement two tasks in one
session.

### `reconcile <run-id> <prompt-path>`

Require an unfinished run with no task currently being edited. Snapshot the
changed prompt as the next immutable revision, inspect its differences and any
relevant source drift, and propose queue changes without product edits.

- Never rewrite completed tasks or renumber existing task IDs.
- Preserve a pending task only when its identity and completion contract are
  unchanged.
- Mark changed or removed pending tasks `superseded`.
- Append replacements and new work using the next unused task IDs.
- Bind the handoff to the new revision only after recording the proposal and
  reconciliation summary.
- Return the run to `prepared` and stop before implementation.

If the revision append succeeds but handoff update is interrupted, verification
reports `reconciliation_pending`: the manifest latest revision is newer than
the handoff binding. Retrying reconciliation with the same source resumes that
existing revision instead of creating another. A different source edit or a
`running` handoff fails closed until the pending state is resolved.

## Resubmission And Drift

- Revisions are created only by submission, not on every save.
- If any run in the scope is unfinished, ordinary `prepare` returns
  `ACTIVE_RUN_EXISTS`; reconcile that run instead. The sole exception is
  idempotent recovery of the same prompt and digest from a verified
  `snapshot_only` run that has no handoff.
- An unchanged submission returns `NO_CHANGES`.
- After a run is complete, an edited prompt creates a new run linked to the
  same `prompt_id`.
- An exact rerun after completion requires explicit `prepare --new-run`.
- Source edits after preparation do not mutate the run. `verify` reports
  `PROMPT_DRIFT`; `run` and `continue` remain bound to the snapshot.
- Missing, malformed, or unresolved editable source content is also
  non-binding `PROMPT_DRIFT` after a valid run exists. It never invalidates the
  immutable bound snapshot.
- Retain run history until the user explicitly deletes it.
- Enforce one writer across the entire scope, not merely one writer per
  prompt.

## Failure Classification

Surface the helper's stable error token and stop on validation or state
failures:

- `REPO_ROOT_INVALID`, `SCOPE_INVALID`, `WORKSPACE_NOT_FOUND`,
  `WORKSPACE_PATH_INVALID`, `WORKSPACE_STATE_INVALID`, or
  `WORKSPACE_PERMISSION_INVALID`: workspace identity, path, schema, or
  permissions are unsafe.
- `PROMPT_INPUT_INVALID` or `PROMPT_CONFLICT`: the prompt contract, content,
  path, or identity is invalid.
- `ACTIVE_RUN_EXISTS`: the scope already has unfinished work; use reconcile,
  or resume only a verified same-input `snapshot_only` preparation.
- `NO_CHANGES`: no revision or new run was created.
- `PROMPT_DRIFT`: editable source differs from the latest bound revision.
- `WORKSPACE_BUSY`: another scope writer holds the workspace lock.
- `RUN_STATE_INVALID`: run, manifest, revision, digest, or handoff state is
  malformed or incompatible with the requested transition.

Do not repair malformed state by guessing or silently replacing it. Record the
failure in the handoff when one exists, then request the minimum safe user
action.

## Sandbox Access

Private state may be outside the active workspace-write root. Prefer the
opt-in `config-codex` setup contract when the user wants persistent access. Do
not weaken an existing sandbox or approval policy. When access is missing,
report this exact per-invocation remediation:

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
- [Codex Skills](https://developers.openai.com/codex/skills)
- [Codex custom prompts](https://learn.chatgpt.com/docs/custom-prompts)
