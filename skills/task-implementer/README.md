# Task Implementer

`task-implementer` is an explicit-entry brownfield implementation coordinator.
It keeps prompts, revisions, steering, orchestration state, assignments,
results, and Git worktrees outside the repository while product changes remain
normal reviewed commits.

## Four-Action Workflow

```text
$task-implementer workspace init [project-folder]
$task-implementer run <prompt-ref-or-file>
$task-implementer integrate [project-folder]
$task-implementer workspace remove [project-folder]
```

Use `$task-implementer --help` or `$task-implementer -h` to display the
purpose, explicit invocation policy, these four workflow actions, their
arguments, and the help option itself. After the selected `SKILL.md` loads, Help
performs no workspace initialization, project inspection, additional tool
calls, or private-state changes; it is not a third workflow action and does not
authorize any lifecycle action.

Initialization creates or verifies the private `CODE` + `PROMPTS` workspace,
keeps `00-START-HERE.md` visible, and creates one starter prompt when needed,
then asks VS Code to reuse its last active
window. Loading the workspace restarts that window's extension host and may
interrupt its terminal or Codex UI; editor failure remains non-fatal. Edit the
starter prompt, then invoke `run` once. The run continues every dependency wave
until completion or a precise blocker; users never supply run, wave, task,
branch, or worktree IDs.

Only generated metadata and one meaningful `## Ask` are required; every other
heading is optional, removable, and may be added when it helps. Use the default
`New Prompt` editor task for a new objective; it allocates a fresh prompt ID,
so manual cloning is unsupported.

After an explicit initialization or run binds the current Codex session, a safe
direct prompt is staged by `prompt-session-intake`, refined losslessly by the
agent, merged with compare-and-set and one operation ID, and routed through the
same run path once. An exact interrupted retry resolves the applied operation
instead of creating or appending again. A new objective publishes its complete
marker-bearing Ask in one exclusive create, and refined user content cannot use
the reserved operation-marker namespace.
Explicit bound runs register the authoritative active prompt for unambiguous
fresh-session attachment and close it only after verified terminal completion.
Steer active work manually by editing the same prompt and repeating `run`. Steering
received after a wave starts remains queued until the next safe boundary. An
unchanged completed prompt returns `ALREADY_COMPLETE`; an edited completed
prompt starts a linked fresh-objective run against current project truth.
Running a different prompt while work is active accepts it into a private FIFO
queue. Creating or saving never queues work. Queue edits require another
explicit `run`; exact raw-byte or intent drift at the queue head blocks
activation. The next prompt activates automatically after the active run and
its resources are released, and interrupted dequeue is recovered without
creating another run.

## Mental Model: Five Git Roles Plus Private State

Task Implementer is not one "child branch." It is a local pipeline with
separate long-lived and temporary locations. The **primary checkout** is the
exact existing checkout supplied to `workspace init`; its **source branch** is
the named non-default branch recorded there. The source branch is not
necessarily the repository's default branch.

| Location | Lifetime | Purpose and ownership |
| --- | --- | --- |
| Primary checkout and source branch | Exists before Task Implementer | Operator-facing checkout. `run` leaves it unchanged; public `integrate` advances it. Use it for final manual validation and separate push or pull-request workflows. |
| Persistent lane and lane branch | Reused across runs until `workspace remove` | Task Implementer-managed full-repository linked worktree created from the committed source `HEAD`. Completed waves are promoted here. Do not manually edit, commit, reset, stash, switch, or clean it. |
| Wave integration worktree and branch | Temporary for one active wave | Coordinator-owned full-repository checkout. It prepares managed specifications and shared documents, provides the common worker base, merges verified worker commits, and runs combined validation. |
| Worker worktree and task branch | Temporary for one assigned task | Worker-owned full-repository checkout with locked write claims. The assigned worker implements, validates, reviews, and invokes `$commit` exactly once. |
| Source integration candidate worktree and branch | Temporary during public `integrate` when source and lane histories differ, or retained for recovery | Coordinator-owned exact two-parent candidate whose parents are the current source head and latest lane head. Only the validated candidate may advance the source branch. No candidate is created when the source already contains the lane head. |
| Private prompt and run workspace | Persistent; not a Git worktree | Stores prompts, revisions, queues, assignments, evidence, and recovery state under `CODEX_HOME`. Removing a lane does not remove this history. |

For a monorepo project such as `skills`, the selected folder is only the
operating scope. The persistent lane, wave integration checkout, worker
checkouts, and source integration candidate are all full-repository linked
worktrees with separate indexes and working directories.

### Lifecycle

```text
PRIMARY CHECKOUT / SOURCE BRANCH
        |
        | workspace init: use committed HEAD only
        v
PERSISTENT LANE / LANE BRANCH                         long-lived
        |
        | run <prompt-ref-or-file>
        v
WAVE INTEGRATION WORKTREE                            temporary
        |\
        | +--> WORKER TASK 1 WORKTREE --> $commit    temporary
        | +--> WORKER TASK 2 WORKTREE --> $commit    temporary
        |      merge verified commits in task order
        |      run combined validation and review
        v
PERSISTENT LANE fast-forwards to the verified wave result
        |
        | repeat for later dependency waves
        | finalize run: seal one pending generation
        v
PUBLIC integrate
        |
        | if needed, build and validate a source-head + lane-head candidate
        v
PRIMARY SOURCE BRANCH advances to the candidate, or stays at its current head
PERSISTENT LANE fast-forwards to that resulting source head and is rearmed
```

There are therefore two different integration operations:

1. **Internal wave integration during `run`:** worker task branches are merged
   into the temporary wave integration branch, validated, and promoted to the
   persistent lane. Task Implementer performs this automatically.
2. **Public source integration after `run`:** an explicit
   `$task-implementer integrate [project-folder]` combines the accumulated lane
   generations with the recorded source branch. When the source already
   contains the lane head, it consumes the no-change generations without
   manufacturing an empty merge. It never pushes or opens a pull request.

After successful public integration, the source branch and lane branch point
to the same merge commit but remain different branch names in different
worktree directories. Task Implementer does not change the caller's shell,
current directory, VS Code window, or checked-out worktree.

### End-To-End Operator Workflow

1. Start in the primary checkout on the source branch and run
   `$task-implementer workspace init [project-folder]`. This creates or reuses
   the persistent lane and private prompt workspace; it does not implement or
   queue work.
2. Edit the generated prompt, then invoke
   `$task-implementer run <prompt-ref-or-file>`. You do not create,
   switch, merge, commit, clean, or remove Task Implementer's branches and
   worktrees yourself. The generated `CODE` folder may show the persistent
   lane; treat that checkout as managed even when it is visible in the editor.
3. Task Implementer compiles requirements, plans dependency waves, creates the
   current wave integration worktree, and dispatches worker worktrees only for
   the active capacity batch.
4. Each worker implements one assignment, validates and reviews it, invokes
   `$commit` once inside its own worker worktree, and returns evidence. The
   coordinator verifies those commits independently.
5. The coordinator merges verified worker commits into the wave integration
   branch, runs combined validation and review, removes the verified worker
   worktrees and branches, and fast-forwards the persistent lane. It then
   removes the wave integration worktree and branch.
6. Steps 3-5 repeat for later dependency waves. When all waves and final
   `$align` pass, the run finalizer seals a pending generation. The primary
   checkout and source branch are still unchanged.
7. From outside the persistent lane, invoke
   `$task-implementer integrate [project-folder]` with the exact primary
   project path. Task Implementer validates a temporary source integration
   candidate when the histories differ, advances the source branch when
   needed, consumes all pending generations, and rearms the lane at the
   resulting source commit.
8. Open or `cd` to the primary checkout for manual acceptance. This is a
   directory or editor-window change, not `git switch`. If testing finds a
   same-objective defect, edit the same prompt and run it again; use `New
   Prompt` for a distinct objective, then integrate the resulting generation.
9. Push or open a pull request through a separate Git workflow if desired.
   Keep the lane for more managed work, or remove an idle fully integrated lane
   with `$task-implementer workspace remove [project-folder]` from outside it.

### What "Clean" Means

A clean worktree has no staged, modified, or untracked files and no merge,
rebase, cherry-pick, revert, or similar Git operation in progress.
`git status --short` prints nothing; `git status --short --branch` may print
only its `## <branch>` header.

Cleanliness is a precondition, not an instruction to run `$commit`, `git
clean`, `git reset`, or `git stash`:

- In a **worker worktree**, only the assigned worker invokes `$commit` as part
  of the managed task lifecycle.
- In the **persistent lane**, do not commit or repair dirt manually. Stop and
  follow the exact Task Implementer blocker or recovery action; manual changes
  would move the lane outside its recorded state.
- In the **primary checkout**, changes belong to the operator's ordinary Git
  workflow. Resolve them intentionally before public `integrate`; Task
  Implementer never copies, commits, discards, or hides them.
- Temporary wave, worker, and candidate resources are coordinator-owned. If a
  failure retains one for recovery, do not remove or modify it manually.

To avoid ambiguity, use **source branch**, **persistent lane branch**, **wave
integration branch**, or **worker task branch** instead of the broad terms
"base branch" and "child branch."

## Managed Requirements And Design Documents

`workspace init` does not create or modify project documentation. During
`run`, before worker dispatch, the coordinator renders and validates two
tracked files under the selected project scope:

- `docs/requirements.md`, with shared-owner `TI-REQ-*` records authored by the
  Task Implementer adapter.
- `docs/design.md`, with shared-owner `TI-DES-*` records that map to
  the managed requirements.

The coordinator compiles these records from the full Ask, optional prompt
headings, current repository facts, chat clarifications, and existing product
truth. It asks only when ambiguity would materially change behavior, safety,
interfaces, architecture, or acceptance evidence. Stable clarification IDs and
answers stay in private `requirements-refinement.json`; managed specs never
contain private prompt/run identifiers or raw chat. Wave planning fails closed
unless the latest ready ledger matches the exact current managed requirements
region.

If either file is missing, Task Implementer creates it. If a generic document
already exists, Task Implementer preserves the human-owned content and appends
the canonical `maintain-project-specs` managed region. Later runs replace only
that validated region. The shared owner controls these files, and the
coordinator may include their changes in the contract or integration commit;
workers never edit them. They are
project-tracked implementation records, not private prompt or orchestration
state.

These records and Agentic SDLC rich records now share one canonical owner.
Former `task-implementer/*-v1` and `agentic-sdlc.*.v1` pairs require the
journaled `maintain-project-specs` migration before managed work continues.
Use `sdlc-create-requirements` and `sdlc-create-design` as shared-owner
adapters when the richer Agentic SDLC record shape is required. Do not mix rich
and compact record shapes inside one managed region.

## Persistent Project Lanes

Run initialization from the exact monorepo project folder while the primary
checkout has configured `origin/HEAD` and is attached to a named non-default
source branch. Task Implementer
creates one persistent full-repository linked worktree, branch, and private VS
Code workspace for the exact common Git directory, primary checkout, source
ref, and repo-relative scope.

Initialization and runs may start while the source checkout is dirty. Only its
committed `HEAD` becomes the lane baseline; dirty and untracked source files are
never copied or mutated. The lane must remain clean at run boundaries. Separate
project scopes get separate lanes and may run concurrently when their
repository-wide exact/prefix claims and conflict domains do not overlap.
When `integrate` finds a dirty primary source, it does not auto-commit or stage
a project subset. It tells the user to run one fresh explicit `$commit` in the
primary checkout, which reviews and commits the complete repository diff, and
then to repeat `integrate`.

Before resources exist, replanning replaces the resource-free planned tail.
The coordinator index keeps completed waves plus the replacement schedule;
superseded planned wave files remain blocked history outside final completion.
The active lane generation reserves every newly introduced repository claim
before replacement state is written and retains earlier claims conservatively.
After a final promoted wave is cleaned, replanning can append an isolated
correction tail found by integration review before finalization.

Every `run` acquires the next monotonic generation at the lane's exact clean
`HEAD`. Finalization creates an immutable released-generation receipt and keeps
its repository claims pending. Back-to-back runs therefore accumulate a
contiguous pending range without touching the source checkout. `integrate`
validates and promotes the whole range, then fast-forwards and rearms the same
lane. `workspace remove` is explicit and proof-gated; it preserves private
prompts and run history, and a later initialization creates the next lane
incarnation and rebinds that history.

Each worker's successful `task-start` also mints its private one-direct-child commit
authorization. `$commit` revalidates the immutable assignment, running task
plane, worker session, branch, and base commit before it may prepare or execute
the worker's one direct-child commit. Root-user prompt intent is neither needed
nor accepted as a substitute for this delegated evidence.

## Dependency Waves

Before implementation, the coordinator inspects source and locks stable tasks
with dependencies, exact or directory-prefix write claims, keyed conflict
domains, validation, and done criteria. It builds deterministic earliest-fit
waves in stable task order.

After the coordinator renders, stages, and validates Task Implementer-managed
requirements and design records in the integration checkout, it routes to the
explicit-only `project-agent-instructions` skill with a private v3 receipt that
binds both complete tracked files and total status-aware cross-document
coverage. That shared owner
conditionally renders a selected-project `AGENTS.md` from structured,
tracked-evidence-backed rules. Personal global instructions are never copied
into repository content. Existing human bytes remain an exact prefix outside
the v3 managed tail; managed-region edits fail closed. Unreceipted v3 regions
require exact-digest adoption approval. Retirement likewise requires
exact-digest approval and a guarded transition that restores the human prefix.

Manifest, decision, ownership, and final state remain private. Exact rendered
rules are injected before worker dispatch, while repository `AGENTS.md`
creation, attachment, refresh, or retirement is deferred until final spec
reconciliation as the terminal seal mutation. A changed file still requires a
fresh Codex session before later managed work treats it as loaded authority.
Dispatch revalidates the receipt against the exact clean integration commit,
proves its active and ancestor project instructions belong to that commit, and
rejects private state bound only to the persistent lane or another worktree.
`workspace init` never creates project documentation or instructions.

Tasks may share a wave only when dependencies are already satisfied and their
ownership is completely disjoint. Shared interfaces, schemas, migration
chains, dependency files, abstractions, Kubernetes/Terraform identities,
exclusive test resources, external mutations, and architecture decisions
serialize. Unknown ownership forces a singleton wave. External database,
Kubernetes, Terraform, migration execution, and publication domains also
reserve class-wide repository claims, so separate lanes cannot run those
live-action classes concurrently merely by using different keys.

Logical waves may exceed runtime agent capacity. They dispatch in stable
capacity-sized batches without changing the dependency wave.

## Worktrees And Workers

Every parallel-capable task receives a unique branch and full-repository linked
worktree under:

```text
${CODEX_HOME:-$HOME/.codex}/task-implementer/worktrees/
<project>/<scope>/<run>/wave-001/{integration,task-1,task-2}
```

For a monorepo scope such as `services/nebius-cxcli`, the worker cwd is
`<task-worktree>/services/nebius-cxcli`. Worktrees share Git objects, refs,
configuration, and hooks but have separate indexes and working files.

The main thread is coordinator-only. It dispatches native workers up to
capacity, or fresh sequential `codex exec` workers when native subagents are
unavailable. Each worker verifies its immutable assignment, implements exactly
one task inside locked claims, validates, runs `code-review`, fixes scoped
findings, and creates exactly one direct-child commit through `$commit`.
Every task starts in a distinct worker session. The coordinator creates only
the active capacity batch; later batches open after the current batch commits.
Each assignment references an immutable private incoming-handoff record with
the accepted commits, paths, summaries, decisions, risks, validation, and
review evidence from all earlier completed waves and batches. Only the first
batch of the first wave has an empty predecessor list.

Immutable worker-assignment v7 records also carry canonical guardrails and the
exact helper/workspace-manifest paths for the first transition. Unless
the exact assignment explicitly authorizes an action, workers stay inside the
assigned worktree/private state; installed skill instructions/helpers and
standard local executables are read/execute-only when required. Workers do not
modify installed files, intentionally write other paths, or access network,
credentials, external services, or live runtimes. Applicable prompt and
repository constraints, implementation steps, and end-to-end validation are
repeated in the assignment so it and the incoming handoff are the worker's
complete task context.
Workers record a private heartbeat at least every 30 seconds. The coordinator
checks liveness on the same cadence. Dependency-free `standard` tasks warn at
240 seconds and stop at 300 seconds without a claimed-path edit or blocker.
Dependent `integration` tasks warn at 360 seconds and stop at 420 seconds.
A heartbeat becomes hard-stale at 240 seconds; that stop gate and the immutable
total budget remain independent of the recommended 30-second cadence.
Workers receive assignment-scoped fresh context with no inherited coordinator
transcript: the immutable assignment and incoming handoff plus one opaque
coordinator-issued start lease. Queued assignments do not consume a start
budget; the coordinator arms one only when a real worker slot is available.
That worker reads its assignment and makes `task-start` the first private
transition after immediate Git/cwd verification. It passes the embedded digest
unchanged through the embedded helper/workspace paths together with the start
lease; `task-start` performs authoritative canonical digest and exact lease
validation, so workers never guess JSON serialization or reuse another launch.
Incoming-handoff reading and deeper preflight follow. The start budget is 60
seconds.
If an armed worker misses that deadline without changing its exact assigned
base, the coordinator confirms that the old worker stopped and invokes private
`task-rearm --confirmed-stopped` with the exact observed start lease. The
compare-and-swap transition preserves the assignment, branch, worktree, wave,
and generation resources while issuing one fresh lease to the replacement.
The replacement still invokes normal `task-start` first; the old lease is
rejected as `WORKER_START_LEASE_INVALID`. Rearm with a stale lease stops as
`WORKER_START_LEASE_CONFLICT`; rearm before expiry or after any prestart
mutation also fails closed and retains the resources for operator review.
At the profile warning, `task-watch` tells the coordinator to demand an
immediate edit or blocker. Heartbeats are direct bounded calls;
background or autonomous heartbeat loops are forbidden. Workers do not reread the full
managed prompt or coordinator-only state after validating their assignment.
`task-start` is single-use, and mutations outside immutable write claims stop as
`WORKER_SCOPE_VIOLATION` instead of extending any liveness budget.

Workers never edit the shared handoff, managed specs, common docs, other refs
or worktrees, or the source checkout. An undeclared path requirement stops
with `REPLAN_REQUIRED` before edit or commit.

## Integration And Promotion

The coordinator verifies worker Git evidence independently, then merges task
branches into a temporary integration branch in stable task-ID order with
`git merge --no-ff --no-edit`. Shared managed specs, README/design docs, and
changelog remain coordinator-owned.

At planning, the coordinator verifies the exact Worktree-owned persistent lane
and acquires a generation lease. It never creates another promotion branch,
fetches, resolves a remote default, or mutates the source checkout.

After combined validation, integration `code-review`, and steering
and project-agent-instructions reconciliation, the unchanged clean lane branch
advances under a shared promotion lock with
`git merge --ff-only <verified-integration-SHA>` after the integration branch
is verified at that SHA. Before that promotion, clean verified worker
worktrees are removed and worker refs are deleted with exact expected-old
SHAs. This fast-forward promotion is the only point where tasks become done.

The integration worktree is removed after promotion, followed by exact-SHA
integration-ref deletion. Failures preserve exact resources for recovery. The
workflow never runs broad prune/gc, cherry-picks, rebases, squashes, pushes, or
force-removes.

After the last wave cleanup, final changed-surface `$align` evidence is sealed
before the lane generation is released. An interruption after the handoff is
marked done remains recoverable: repeating `run` finishes the same private
release instead of starting a new generation.

`integrate [project-folder]` requires both the recorded source checkout and
lane to be clean and free of Git operations, with no active generation. It
serializes by source ref, builds one exact two-parent candidate from the current
source head and latest lane head, runs combined validation and review on that
candidate, and promotes only the validated SHA through an expected-old
compare-and-set. Conflicts and uncertain state are retained. Success consumes
all pending generations, releases their claims, and rearms the same lane at the
merge head.

## Recommended Post-Run Workflow

Use this operator flow after one or more `run` invocations have finalized and
left released generations pending:

1. From a directory outside the persistent lane, invoke
   `$task-implementer integrate [project-folder]` with the exact primary
   project path. This avoids cwd ambiguity. Integration requires no active
   generation and completely clean primary and lane worktrees. Success means
   the validated local candidate advanced the recorded source branch, all
   pending generations were consumed, and the same persistent lane was
   rearmed. It does not publish the branch or prove external/manual acceptance.
2. Open or `cd` to the primary checkout reported by the workflow. This is a
   directory or editor-window change, not `git switch`: linked worktrees have
   separate working directories and indexes.
3. Run any acceptance activity that could not be completed by the workers,
   such as a real-desktop application or gameplay playthrough. Treat this as a
   gate before publication. If it finds a defect in the same objective, edit
   the same prompt and repeat `run`; the completed-prompt edit creates a linked
   fresh-objective follow-up. Use the `New Prompt` editor task for a distinct
   objective. After either run finalizes, invoke `integrate` again.
4. After the relevant automated and manual checks pass, push the source branch
   or open a pull request through the repository's separate Git workflow if
   desired. Task Implementer never pushes, opens pull requests, or publishes.
5. Keep the persistent lane when more managed work is likely. Otherwise, after
   confirming that it is idle, clean, fully integrated, and free of claims or
   recovery state, run
   `$task-implementer workspace remove [project-folder]` from the primary
   checkout or another directory outside the lane. Removal deletes only the
   exact lane worktree and branch; private prompt, run, generation, and history
   records remain available for a later `workspace init`.

For example, suppose a game project uses Task Implementer to add controller
support. After the managed run finalizes, integrate it into the game's source
branch, move to the primary checkout, launch the normal desktop build, and test
controller connection, gameplay input, pause/resume, and save/load behavior.
If the playthrough exposes an input defect, update the controller-support
prompt, run it again, reintegrate, and repeat the playthrough. Once it passes,
publish through the project's normal Git process. Retain the lane for the next
managed gameplay objective, or remove the idle lane while keeping its prompt
and run history.

## Private State And Recovery

Coordinator v7, wave v4, mutable task-plane v5, immutable assignment v7/result v3,
incoming-handoff, and journal records live with the run
under the private prompt workspace. Every Git mutation is journaled before
execution and re-observed afterward. A repeated `run` resumes durable v5 truth
without recreating branches, worktrees, assignments, commits, or merges.

`orchestration/interop.json` uses schema v4 and binds one run to its exact lane,
incarnation, generation, and Worktree-owned lease. Worktree schema-v4 remains
the general linked-worktree ownership format; separate lane schema-v1 state and
immutable generation receipts add the persistent Task lifecycle without
changing that public Worktree schema.

Every execution-plane-v1 or coordinator-v1/v2/v3/v4/v5/v6 run returns
`WORKFLOW_UPGRADE_REQUIRED`, including completed records. There is no legacy
read path, compatibility execution path, or migration command.

Prompt-v1 files and unfinished prompt-v1 runs are read-only and return
`WORKFLOW_UPGRADE_REQUIRED`; initialize once to migrate editable prompt-v2
files to prompt-v3 through its retryable private migration journal, then create
only prompt-v3 files. Completed history
bytes are preserved and there is no migration command. Prompt filenames stay stable. Submission order comes from private
`last_invoked_at`, not filenames or mtimes. Output never prints prompt bodies,
secrets, or internal IDs.

## Files

- `SKILL.md`: explicit four-action coordinator contract.
- `agents/openai.yaml`: UI metadata and explicit-only policy.
- `references/prompt-workspace.md`: private storage, routing, v2 state, errors,
  and sandbox behavior.
- `references/implementation-loop.md`: task analysis, wave lifecycle, worker,
  integration, promotion, cleanup, and recovery rules.
- `references/prompt-requirements-refinement.md`: Ask compilation, selective
  clarification, stable questions, and managed-spec ownership.
- `assets/handoff-template.md`: coordinator-owned queue and wave evidence.
- `scripts/prompt_workspace_execution.py`: parsing and deterministic scheduler.
- `scripts/prompt_workspace_waves.py`: journaled worktree lifecycle.
- `scripts/prompt_workspace_lanes.py`: private Worktree-owned lane adapter.
- `scripts/prompt_workspace_interop.py`: generation lease bridge.
- `scripts/prompt_workspace_intake.py`: prompt routing and steering.
- `scripts/prompt_workspace_specs.py`: managed specification validation.
- `scripts/validate_project_specs.py`: selected-project receipt replay used by
  the shared project-instruction owner.
- `project-agent-instructions`: shared conditional selected-project
  `AGENTS.md` owner and deterministic provenance helper.
- `scripts/test-task-execution.py`: scheduler and v1-boundary tests.
- `scripts/test-task-waves.py`: disposable real-Git lifecycle tests.
- `scripts/test-worktree-interoperability.py`: composed lane-generation,
  promotion, cleanup, interruption, and source-isolation tests.
- `scripts/test-prompt-workspace.py`, `test-task-specs.py`, and
  `test-task-implementer-contract.py`: storage, spec, and contract tests.

## Boundaries

- Explicit invocation only; generic parallel requests do not trigger it.
- Public surface remains exactly `workspace init`, `run`, `integrate`, and
  `workspace remove`.
- External database, Kubernetes, Terraform, migration, and publication actions
  remain singleton and need separate explicit authority.
- Use `$align` after the final promoted wave; use `$sdlc-start run <prompt-ref-or-file>` for Agentic
  SDLC.
