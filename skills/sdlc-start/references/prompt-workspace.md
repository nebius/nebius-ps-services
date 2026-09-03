# Agentic SDLC Prompt Workspace

Read this reference before initializing a prompt workspace, accepting a prompt,
binding a run, or reconciling same-prompt steering.

## Public Surface

The only public actions are:

```text
$sdlc-start workspace init [project-folder]
$sdlc-start run <prompt-ref-or-file>
```

All script commands are private mechanical transitions. They do not select a
feature or SDLC phase.

## Private Layout

```text
${CODEX_HOME:-$HOME/.codex}/sdlc-runs/<project-id>/
├── workspace.json
├── activity.json
├── prompt-queue.json
├── queued-prompts/<prompt-id>/<digest>.md
├── prompt.lock
├── prompts/00-START-HERE.md
├── prompts/<prompt-ref>--<created-at>--<slug>.md
├── <project>-prompts.code-workspace
├── active-run.json
└── <run-id>/
    ├── prompt.json
    ├── requirements-refinement.json
    ├── prompt-impact-claim.json
    ├── prompt-impact/
    │   ├── attempt-0001.json
    │   ├── ledger.json
    │   └── execution/FEAT-001.json
    ├── inputs/r0001/prompt.md
    ├── run.json
    └── ... existing Agentic SDLC state ...
```

The exact canonical project folder determines workspace identity. A folder may
be initialized before Git exists; creating a Git repository later must not
change the prompt workspace identity. When Git is present, `workspace.json`
also records the Git root and repo-relative scope. Execution treats that exact
folder as an enforced boundary even though Git worktrees contain the full repo.

Private directories use mode `0700` and files use `0600` on POSIX. Reject
managed symlinks, traversal, foreign prompt paths, malformed state, invalid
UTF-8, duplicate prompt identities, and prompt files larger than 256 KiB. The
private root must not be inside a Git worktree.

The generated `00-START-HERE.md` is an always-visible workspace guide and is
never parsed as a prompt. The starter is an editable template, not an
executable prompt. Only `Ask` must contain meaningful text. Outcome, Context,
Constraints, Acceptance criteria, Verification, Non-goals, References,
Clarifications, Live Experiment Environment, Steering, and custom headings are
optional. Comments and unresolved template markers do not count. Intake
rejects obvious secret-bearing assignments before snapshotting.

## Prompt And Run Binding

Prompts use schema `agentic-sdlc/prompt-v3`. The full prompt ID is authoritative.
A collision-safe five-character reference is stored in metadata and prefixed
to the filename; an exact collision extends the new reference. Public `run`
resolves an exact ref, full prompt ID, filename, or managed path. The editable
prompt keeps a stable identity and creation timestamp. Each accepted changed digest is
copied once to immutable `inputs/rNNNN/prompt.md` and recorded in the run's
`prompt.json` manifest with schema `agentic-sdlc/prompt-binding-v2`. Each
revision records both exact raw SHA-256 and a normalized intent digest that
ignores metadata, comments, section ordering, and whitespace-only formatting.
Fenced-code bodies retain their exact indentation and content in the intent
digest; Markdown headings inside matching backtick or tilde fences remain
section content. HTML comments are non-operational; put instructions in the
Ask or another visible heading. Revision snapshot and refinement state are
staged before `prompt.json` is atomically replaced as the binding commit point;
the next intake removes only the exact uncommitted revision and retries after
an interrupted pre-commit transition.

One unfinished prompt owns one exact project scope:

- a new prompt creates and binds a new run;
- an unchanged active prompt resumes the same run without a new revision;
- an edited active prompt appends one revision and routes to
  `sdlc-auto-steering`;
- repeating the unchanged edit reuses the pending revision;
- an unchanged completed prompt returns `ALREADY_COMPLETE`;
- editing a completed prompt starts a linked fresh-full-objective run while
  preserving old history; its `r0001` kind is `completed_follow_up`, not
  steering;
- explicitly running a different prompt while a run is unfinished accepts or
  updates it in a private FIFO queue; creating or saving never queues work.

The queue stores an immutable accepted snapshot. Editing a queued prompt has no
effect until the user explicitly runs it again. If the editable FIFO head
drifts from its accepted raw bytes or normalized intent, activation blocks with
`QUEUED_PROMPT_DRIFT`. No priority or reordering path exists, and blocked
active work is not overtaken. After terminal completion and authoritative
resource release, the coordinator invokes private `queue-next` to activate the
unchanged head. If run creation commits before dequeue completes, the next
queue transition recognizes the exact accepted run and finishes the interrupted
dequeue.

The helper writes `active-run.json`, the immutable snapshot, and `prompt.json`.
`sdlc-start` remains responsible for `run.json`, checkpoints, feature state,
phase routing, and the active workflow lock.

After an explicit init or run binds a Codex session, the separate
`prompt-session-intake` hook may stage a later safe direct turn while the
current agent handles it normally. Staging is event-v2 metadata only and never
persists the submitted body. The agent records merge/no-op/sensitive and writes
only a durable project-intent projection for merge. Private `session-merge`
rehashes it and binds its digest to the operation marker before compare-and-set
create or update. Exact retries and byte-identical projections do not append
twice; distinct concurrent same-base updates never auto-rebase. Workflow/skill,
shell/tool, delivery, agent-control, status, conversation, and unrelated turns
do not mutate or run a prompt, while commands used as project contracts remain
eligible. New-objective publication is one exclusive marker-bearing file
creation; projection content that uses the reserved operation-marker namespace
is rejected. Editing or saving a prompt file
never triggers execution; captured updates and manual changes require explicit
`run`. Secrets and capture failures do not persist or block the direct request.
Explicit bound runs register and close the authoritative active prompt for
objective identity and validation; they never bind another session. Every
fresh session requires its own exact init or run command before later capture,
and queued prompts remain inactive until activation.

The editor workspace exposes private `new`, `list`, `queue-list`, and
`queue-cancel` tasks. `new` is the default build task and creates a
`0600` timestamp/slug prompt with collision suffixes. `list` returns only title,
path, status, accepted activity time, revision count, and completed-run count;
it never returns prompt bodies, IDs, digests, snapshots, or private run paths.
Accepted activity is monotonic and independent of file mtime. Private `verify`
checks workspace, prompt identity, immutable snapshots, and active binding.

An exact manual rename preserves prompt identity only when the old filename is
absent, exactly one editable prompt has the bound ID, and its normalized intent
equals the bound revision. Intake updates `prompt.json`, returns the new
filename for the coordinator mirror, and Stop continuation uses it. Metadata,
comments, and formatting may differ without creating work. Rename plus an
intent edit and stale/duplicate copies fail closed; perform rename and intent
editing as separate runs.

## Requirements Refinement And Steering

Before design or planning, apply `prompt-requirements-refinement.md`. Compile
the full Ask and optional headings into `docs/requirements.md`; inspect
discoverable facts before asking, allocate stable private `Q-*` IDs only for
material ambiguity, and keep the refinement ledger outside Git. Omission does
not delete existing product truth. The private `refinement-verify` helper must
bind the latest accepted revision and intent digest to the exact current
canonical specs and publish the shared owner's complete impact receipt before
the workflow can leave requirements.

Only an `active_steering` revision after `r0001` starts with steering status `pending`.
`sdlc-auto-steering` records exactly one corresponding inbox entry containing
the prompt ID, revision, digest, and snapshot pointer, then calls the private
`steering-resolve` transition with `applied`, `blocked`, or `no_effect`.
`applied` and `no_effect` are accepted only when the current revision's
owner-validated impact receipt has the corresponding derived effect.

The steering ledger stores only a safe summary. The immutable private snapshot
is the historical input and must never be copied into committed project files
or injected wholesale into the parent context. Requirements, design, and docs
changes continue to route to their owning skills. Prepared or running execution
resources are preserved until the existing safe replan boundary is reached.

## Legacy And Recovery

An unfinished active run without `prompt.json` returns
`WORKFLOW_UPGRADE_REQUIRED`; do not adopt it, synthesize a prompt, or add a
compatibility alias. Completed unbound history remains readable and a managed
prompt may start a new run.

An active bound run without prompt-impact evidence freezes new progression as
`PROMPT_IMPACT_REQUIRED` until refinement reconstructs the current basis or a
safe replan settles it. Terminal history remains readable as
`historical_no_receipt`; no migration invents semantic coverage.

Prompt-v1 files and unfinished prompt-binding-v1 runs are read-only and return
`WORKFLOW_UPGRADE_REQUIRED`. Preserve their bytes as history; there is no
migration or execution compatibility path.

Initialization performs the only prompt-v2 transition: editable prompt-v2
files gain their deterministic prompt reference and v3 filename, then mutable
queue and run pointers are repaired under the prompt lock. A private migration
journal finishes each interrupted file transition from its validated old
source or already-installed v3 target before retrying pointer repair. Immutable
v2 revision bytes remain unchanged and readable; prompt-v2 is never a parallel
write path.
Pointer repair accepts either the validated old filename or the already-repaired
v3 filename, recomputes queue digests and snapshots, and can resume after a
partial queue or run-manifest commit.

Validate every immutable snapshot and manifest digest before resuming. Partial
new-run creation is safe to retry: the active pointer selects the same bound
run, and unchanged intake creates no duplicate revision.
