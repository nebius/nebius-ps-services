# Agentic SDLC Prompt Workspace

Read this reference before initializing a prompt workspace, accepting a prompt,
binding a run, or reconciling same-prompt steering.

## Public Surface

The only public actions are:

```text
$sdlc-start workspace init [project-folder]
$sdlc-start run <prompt-path-or-unique-filename>
```

All script commands are private mechanical transitions. They do not select a
feature or SDLC phase.

## Private Layout

```text
${CODEX_HOME:-$HOME/.codex}/sdlc-runs/<project-id>/
├── workspace.json
├── activity.json
├── prompt.lock
├── prompts/<created-at>--<slug>.md
├── <project>-prompts.code-workspace
├── active-run.json
└── <run-id>/
    ├── prompt.json
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

The starter is an editable template, not an executable prompt. `Ask`, `Outcome`,
`Acceptance criteria`, and `Verification` must contain meaningful text;
comments, empty checklist markers, and unresolved template markers do not
count. Intake rejects obvious secret-bearing assignments before snapshotting.

## Prompt And Run Binding

Prompts use schema `agentic-sdlc/prompt-v1`. The editable prompt keeps a stable
filename, prompt ID, and creation timestamp. Each accepted changed digest is
copied once to immutable `inputs/rNNNN/prompt.md` and recorded in the run's
`prompt.json` manifest with schema `agentic-sdlc/prompt-binding-v1`.

One unfinished prompt owns one exact project scope:

- a new prompt creates and binds a new run;
- an unchanged active prompt resumes the same run without a new revision;
- an edited active prompt appends one revision and routes to
  `sdlc-auto-steering`;
- repeating the unchanged edit reuses the pending revision;
- an unchanged completed prompt returns `ALREADY_COMPLETE`;
- editing a completed prompt starts a new run while preserving old history;
- a different prompt while a run is unfinished returns `ACTIVE_RUN_CONFLICT`.

The helper writes `active-run.json`, the immutable snapshot, and `prompt.json`.
`sdlc-start` remains responsible for `run.json`, checkpoints, feature state,
phase routing, and the active workflow lock.

The editor workspace exposes private `new` and `list` tasks. `new` creates a
`0600` timestamp/slug prompt with collision suffixes. `list` returns only title,
path, status, accepted activity time, revision count, and completed-run count;
it never returns prompt bodies, IDs, digests, snapshots, or private run paths.
Accepted activity is monotonic and independent of file mtime. Private `verify`
checks workspace, prompt identity, immutable snapshots, and active binding.

An exact manual rename preserves prompt identity only when the old filename is
absent, exactly one editable prompt has the bound ID, and its bytes equal the
bound revision. Intake updates `prompt.json`, returns the new filename for the
coordinator mirror, and Stop continuation uses it. Rename-plus-edit and
stale/duplicate copies fail closed; perform rename and edit as separate runs.

## Steering Linkage

Every prompt revision after `r0001` starts with steering status `pending`.
`sdlc-auto-steering` records exactly one corresponding inbox entry containing
the prompt ID, revision, digest, and snapshot pointer, then calls the private
`steering-resolve` transition with `applied`, `blocked`, or `no_effect`.

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

Validate every immutable snapshot and manifest digest before resuming. Partial
new-run creation is safe to retry: the active pointer selects the same bound
run, and unchanged intake creates no duplicate revision.
