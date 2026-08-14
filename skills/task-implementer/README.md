# Task Implementer

`task-implementer` is an explicit-entry brownfield implementation coordinator.
It keeps prompts, revisions, steering, orchestration state, assignments,
results, and Git worktrees outside the repository while product changes remain
normal reviewed commits.

## Five-Action Workflow

```text
$task-implementer workspace init [project-folder]
$task-implementer workspace reuse [project-folder]
$task-implementer run <prompt-ref-or-file>
$task-implementer integrate [project-folder]
$task-implementer workspace remove [project-folder]
```

Use `$task-implementer --help` or `$task-implementer -h` to display the
purpose, explicit invocation policy, these five workflow actions, their
arguments, and the help option itself. After the selected `SKILL.md` loads, Help
performs no workspace initialization, project inspection, additional tool
calls, or private-state changes; it is not a sixth workflow action and does not
authorize any lifecycle action.

Initialization creates or verifies the private `CODE` + `PROMPTS` workspace,
keeps `00-START-HERE.md` visible, and creates one starter prompt when needed,
then asks VS Code to reuse its last active
window. Loading the workspace restarts that window's extension host and may
interrupt its terminal or Codex UI; editor failure remains non-fatal. Edit the
starter prompt, then invoke `run` once. The run continues every dependency wave
until completion or a precise blocker; users never supply run, wave, task,
branch, or worktree IDs.

`workspace reuse` is the non-mutating reopen path for an already initialized
project. Invoke it from the primary source project or the owning managed lane;
both resolve the same generated workspace. It validates the existing workspace
and live lane identity, including the still-existing source ref, rejects an
unrelated worktree even if its metadata aliases that ref, tolerates dirty or
active live state, and opens the workspace without lane refresh, prompt
migration, checkpointing, or execution.
`run` also resolves from the primary source project, so reopening is optional.
If a package-manager upgrade removed only the exact resolved Python executable
recorded in the generated VS Code launcher, explicit `run` refreshes that
generated file through canonical workspace initialization and repeats full
verification before intake. It does not repair any other workspace mismatch or
change lane, prompt, run, orchestration, or Git state; `workspace reuse`
remains non-mutating and reports the stale launcher instead.

Only generated metadata and one meaningful `## Ask` are required; every other
heading is optional, removable, and may be added when it helps. Use the default
`New Prompt` editor task for a new objective; it allocates a fresh prompt ID,
so manual cloning is unsupported.

After an explicit initialization or run binds the current Codex session, every
direct prompt still runs normally in the current agent. When safe, the separate
`prompt-session-intake` sidecar stages metadata only; it never stores the
submitted body. The agent records merge/no-op/sensitive and, for merge, writes
only a durable project-intent projection. Workflow/skill, shell/tool, delivery,
agent-control, status, conversation, unrelated, and duplicate-only clauses do
not mutate the prompt; mixed turns retain only project intent, while commands
remain eligible when they define a project contract or example. The private
adapter rehashes the accepted projection and compare-and-set merges it with one
operation-and-projection marker. Capture never starts, resumes, or selects Task
Implementer. Exact retries and byte-identical projections do not append again;
distinct concurrent same-base updates never auto-rebase. A new objective
publishes its complete marker-bearing Ask in one exclusive create, and
projection content cannot use the reserved operation-marker namespace.
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

![Task Implementer lifecycle from the primary checkout through dependency-wave
workers, verified lane promotion, run finalization, and public source
integration](../docs/images/task-implementer-lifecycle.png)

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
2. If that editor window was closed later, run
   `$task-implementer workspace reuse [project-folder]` from the same source
   project to reopen it without changing the managed lane.
3. Edit the generated prompt, then invoke
   `$task-implementer run <prompt-ref-or-file>`. You do not create,
   switch, merge, commit, clean, or remove Task Implementer's branches and
   worktrees yourself. The generated `CODE` folder may show the persistent
   lane; treat that checkout as managed even when it is visible in the editor.
4. Task Implementer compiles requirements, plans dependency waves, creates the
   current wave integration worktree, and dispatches worker worktrees only for
   the active capacity batch.
5. Each worker implements one assignment, validates and reviews it, invokes
   `$commit` once inside its own worker worktree, and returns evidence. The
   coordinator verifies those commits independently.
6. The coordinator merges verified worker commits into the wave integration
   branch, runs combined validation and review, removes the verified worker
   worktrees and branches, and fast-forwards the persistent lane. Non-final
   waves then remove their integration worktrees and branches.
7. Steps 4-6 repeat for later dependency waves. After the final implementation
   promotion, Task Implementer retains that integration checkout until the
   selected-project lifecycle is applied, verified, and sealed. The owner
   records a clean no-change seal or promotes only its exact canonical
   spec/provenance overlay, then cleans the final integration. When final
   `$align` passes, the run finalizer releases the pending generation. The
   primary checkout and source branch are still unchanged.
8. From outside the persistent lane, invoke
   `$task-implementer integrate [project-folder]` with the exact primary
   project path. Task Implementer validates a temporary source integration
   candidate when the histories differ, advances the source branch when
   needed, consumes all pending generations, and rearms the lane at the
   resulting source commit.
9. Open or `cd` to the primary checkout for manual acceptance. This is a
   directory or editor-window change, not `git switch`. If testing finds a
   same-objective defect, edit the same prompt and run it again; use `New
   Prompt` for a distinct objective, then integrate the resulting generation.
10. Push or open a pull request through a separate Git workflow if desired.
   Keep the lane for more managed work, or remove an idle fully integrated lane
   with `$task-implementer workspace remove [project-folder]` from outside it.

### What "Clean" Means

A clean worktree has no staged, modified, or untracked files and no merge,
rebase, cherry-pick, revert, or similar Git operation in progress.
`git status --short` prints nothing; `git status --short --branch` may print
only its `## <branch>` header.

Cleanliness is still required for active-generation recovery, promotion, and
public integration. A resource-free fresh `run` is the one exception: Task
Implementer first reserves an exact candidate tree and path digest without
mutating the real index or history, pauses dirty candidates for complete
review, then consumes that review token while creating one built-in
whole-repository checkpoint with repo-root `git add -A`, normal hooks, and the fixed message
`chore(task-implementer): checkpoint managed lane`. A clean candidate creates
no commit. Before opening the transaction, the coordinator inspects every
changed path, loads applicable instructions for every affected first-class
project, and rejects sensitive, unrelated, unsupported, or unresolved
cross-project content. Hook-modified commits rotate the token and require a
fresh preparation plus review, so a blind retry cannot adopt them. This is
never an instruction to run `$commit`, `git clean`, `git reset`, or `git stash`
manually:

- In a **worker worktree**, only the assigned worker invokes `$commit` as part
  of the managed task lifecycle.
- In the **persistent lane**, related pre-run changes may span the repository
  root and sibling project folders; the next explicit `run` checkpoints them
  together and claims every changed path before workers exist. Do not commit or
  repair lane dirt manually. Dirt discovered after the generation opens still
  blocks and follows exact recovery evidence.
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
unless the latest ready ledger and a complete private impact claim validate
against the exact current requirements and design. The shared owner requires
one disposition for every extracted statement occurrence and derives whether
the current coordinator plan may be retained or must be rebuilt. Matching
document bytes or an unsubstantiated `no_effect` label cannot unlock work.

Impact attempts are append-only private evidence. A separate plan-basis receipt
lets a later proven no-effect revision retain an older coordinator plan, while
contract or execution effects freeze new dispatch, integration, promotion, and
finalization until a safe replan with a distinct plan identity. Per-run locked
publication compare-checks the ledger and preserves then skips crash-orphaned
attempts. Status reports only the revision, edit state,
impact class, public record IDs or bounded reason class, and action. Editable
prompts keep their existing raw and normalized-intent change detection; no hash
header is added, and edits still execute only through explicit `run`.

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
never copied or mutated. A fresh resource-free run may start with coherent lane
dirt and checkpoints the whole lane as its generation baseline. Active and
resumed runs stay clean except for an exact sealed requirements/design overlay,
with an optional provenance-only `AGENTS.md`, that the private contract owner
has journaled and committed into the retained integration. Separate project
scopes get separate lanes and may run concurrently when their
repository-wide exact/prefix claims and conflict domains do not overlap.
When `integrate` finds a dirty primary source, it does not auto-commit or stage
a project subset. It tells the user to run one fresh explicit `$commit` in the
primary checkout, which reviews and commits the complete repository diff, and
then to repeat `integrate`.

Before resources exist, replanning replaces the resource-free planned tail.
Resume compares the live pending task contract with the immutable plan and
forces that replan before preparation whenever either one has drifted.
The coordinator index keeps completed waves plus the replacement schedule;
superseded planned wave files remain blocked history outside final completion.
The active lane generation reserves every newly introduced repository claim
before replacement state is written and retains earlier claims conservatively.
When combined review blocks an exact `promotion_pending` integration,
replanning appends only the currently dependency-ready correction frontier to
that same wave. New workers branch from the sealed integrated head while the
lane remains at the wave's original base; existing merged assignments retain
their original bases and evidence. Dependent frontiers repeat after the prior
frontier merges, and a resource-free future task may update only its dependency
list once the referenced correction is indexed. The whole corrected wave
repeats integration validation and review before one fast-forward promotion. A
rendered project-instruction
decision may carry to that descendant head only when the authoritative receipt
differs solely by Git head and requirements, design, traceability, project
identity, ancestry, and instruction bytes remain exact. A sealed canonical spec
overlay is admitted only through the owner-only contract-delta journal, which
binds its lifecycle and file digests to one coordinator integration commit.
The single claim-bound final coordinator commit may reconcile those adopted
spec bytes after implementation. Promotion temporarily clears the original
lane overlay and restores its exact bytes from the immutable adoption commit
after an interrupted or failed fast-forward; unrelated dirt remains blocked.
Before deleting the promoted integration, cleanup settles the advanced spec
receipt only when the exact coordinator commit explains it and impact analysis
retains the remaining plan. A retained completed adoption journal is ignored by
a later wave's recovery and completion only after proving the recorded wave is
done, its promoted head is exact, and that head precedes the active wave base.
For a post-fast-forward interruption, replay defers stale spec-impact rejection
only when the clean lane exactly equals the sealed integration target; cleanup
still proves and settles the exact coordinator reconciliation.
The final promoted integration has an additional terminal lifecycle boundary:
cleanup is blocked until a private receipt binds the exact sealed lifecycle,
instruction state, final-wave base, promoted head, and complete canonical
requirements/design/instructions bytes. A clean seal creates no Git commit. If
the lifecycle changed only tracked canonical specs or provenance-owned
`AGENTS.md`, the owner commits that exact overlay in the retained integration,
journals temporary lane cleanup, fast-forwards it as a second promotion, and
only then removes the integration resource. Missing, stale, partial, staged,
untracked, deleted, or unrelated state remains blocked, and generation release
rechecks the promoted terminal receipt.
For a historical run that was already released before this boundary existed,
the same hidden owner may recover only an exact sealed canonical overlay by
opening and immediately releasing the next Worktree-owned generation. That
supplemental generation checkpoints the reviewed bytes, preserves the original
immutable generation and task evidence, and remains part of the contiguous
pending range consumed by public `integrate`. It never amends generation
history, ignores lane dirt, or recreates cleaned workers or integrations.
After a final promoted wave is cleaned, replanning can append an isolated
correction tail found by integration review before finalization.

Every `run` acquires the next monotonic generation at the lane's exact
post-checkpoint clean `HEAD`. The checkpoint is either a single direct child or
a clean no-op, and its compact receipt records the before/head/tree/path
evidence without file contents. Finalization creates an immutable
released-generation receipt and keeps
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
`task-start` and `task-recover` return a transient canonical `commit_context`
with the exact PATH-canonical Python executable, installed helper, outer
lifecycle cwd, worker Git root, raw `CODEX_THREAD_ID`, evidence paths, and
prepare argv. Workers use that context unchanged. The separately returned
`worker_session_fingerprint_sha256` is non-command ownership evidence and must
never be supplied as `--session-id`; only its raw source value can satisfy the
owner binding. Raw session identity is not persisted in run, assignment, task,
or authorization state.
Worker result publication similarly uses the returned transient
`result_context`: write only the exact `result_path` from its private
`publication_cwd`, not from the selected project cwd. Write the unsigned final
record to `draft_path` and invoke the exact `publish_argv`; the helper computes
`result_sha256` from the final bytes and publishes atomically. Workers do not
hand-compute or patch the checksum. Publication canonicalizes `changed_paths`
as a sorted unique set, while acceptance treats their order as non-semantic and
can safely revalidate the exact result/commit pair after an older ordering-only
false rejection.
The publisher accepts only exact lower-case `committed` for success or
`REPLAN_REQUIRED` for a terminal safe stop. A retained pre-fix `COMPLETED`
result can migrate only from its exact blocked failed state after its digest,
direct-child commit, clean tree, claims, and evidence are revalidated; new
publication never accepts that spelling.

A worker may also publish a truthful terminal `REPLAN_REQUIRED` result. Resume
accepts that immutable result idempotently instead of waiting for another
publication. A later correction plan may mark the failed task `superseded` only
when its result and Git state prove an exact clean no-op: the reported commit is
the assignment base, the changed-path set is empty, and the assigned worktree
is clean at that base. The coordinator then cleans only that exact worker
resource and appends the correction task. Exact tracked dirty
`REPLAN_REQUIRED` evidence at the base may also proceed after Task archives its
tree behind a compare-and-set internal ref, verifies the parent, exact tree
bytes, and complete path set on replay, and restores only those tracked paths.
The archive remains until the correction promotes and successful cleanup
deletes it. Untracked, committed, mismatched, or unverifiable dirt remains
blocked.

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
The lifecycle integration is automatic: a hidden read-only adapter authenticates
validation, inspect, and render for the active run's exact prepared integration
checkout. It also authenticates validation of the exact tracked
coordinator-owned reconciliation diff at the sealed `promotion_pending`
integration head. All actions remain bound to run-owned paths during
reconciliation-required. The first preparation remains
bound to the original wave base; a retained correction is bound to its exact
recorded contract commit, with an adopted contract head admitted only while its
owner journal remains authoritative. Every correction boundary also requires
the recorded checkout to remain a registered linked worktree in the lane's
common Git directory. Apply and verify retain the
ordinary terminal seal path. The selected-lane hook records the first successful
`wave-plan` checkpoint as reconciliation-required. Users never move receipts
between private bundles or provide lifecycle session identifiers.
The final implementation promotion deliberately retains its integration
worktree. After ordinary lifecycle apply, verify, and seal, the hidden
contract-delta owner records the terminal receipt even when no repository bytes
changed. Exact canonical lifecycle dirt is adopted into that retained
integration and promoted before cleanup; the coordinator never ignores or
manually repairs a dirty released lane.

After worker integration, private `coordinator-commit` is the only staging and
commit path for the final coordinator-owned requirements, design, README, and
changelog delta. It creates at most one direct-child integration commit and
rejects product, untracked, deleted, symlinked, or advanced-head changes.
If a later Stop requests contract cleanup from `implementation-open`, the
shared lifecycle owner enters `reconciliation-required` before its generated
continuation. The workflow does not require an extra user message or assume
that a synthetic continuation fires `UserPromptSubmit`; an installed-runtime
mismatch preserves the run and fails closed instead of widening spec access.
The adapter also proves an exact active worker assignment, session, checkout,
and commit evidence when a delegated commit transaction reaches a hook selected
on the persistent outer lane; the hook then validates only that attested worker
root and the command-derived worker session matched to the running plane. It
does not substitute the outer hook payload session for the nested worker.
Raw Git, coordinator-root commits, future batches, and arbitrary sibling
worktrees remain denied.
The hook accepts either its own interpreter or an exact executable whose
`python3` or `python3.N` basename resolves to the same file through the hook's
PATH. This permits a canonical worker Python version to differ from the hook
runtime without admitting arbitrary same-name paths, wrappers, helpers, or
commands.
The adapter accepts the recorded integration branch/base either clean or with
only its complete selected-project requirements and design staged; every other
worktree delta remains invalid.

If a running-wave worker or integration directory, or a promotion-pending
integration directory, disappears while its Worktree lease remains `present`,
preserve the stopped run, lease, branches, and Git administrative records.
After confirming prior workers stopped, the coordinator may invoke internal
`wave-resource-recover` from the exact owning scope. It rehydrates only unique
locked registrations with exact lease, branch, HEAD, administrative HEAD,
clean-index, and ancestry proof, journals the operation, and keeps each lease
row `present`. Running integration is bound to the contract commit;
promotion-pending integration is bound to the recorded integrated head. The
promotion-pending recovery gate accepts immutable task history only when every
task is already `merged` or safely `superseded`; a retained failed, running,
assigned, committed, or planned task still blocks recovery. The
result reports filesystem-only state as lost and changes no task, commit,
integration, or promotion state. A fresh replacement invokes the exact
`recover_argv` from the `worker_context.scope_cwd` returned by `run-resume`;
staged indexes, locks, symlink collisions, drift, or ambiguity
remain retained and blocked.

Tasks may share a wave only when dependencies are already satisfied and their
ownership is completely disjoint. Shared interfaces, schemas, migration
chains, dependency files, abstractions, Kubernetes/Terraform identities,
exclusive test resources, external mutations, and architecture decisions
serialize. Disjoint paths are insufficient when tasks establish, recover, or
validate the same invariant; those tasks require a shared conflict domain or
an explicit dependency. Unknown semantic ownership forces a singleton wave.
External database, Kubernetes, Terraform, migration execution, and publication domains also
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
transcript: the immutable assignment and incoming handoff plus the exact
`start_context` returned by `task-arm` or `task-rearm`. Queued assignments do not consume a start
budget; the coordinator arms one only when a real worker slot is available.
That worker reads its assignment and makes `task-start` the first private
transition after immediate Git/cwd verification. It passes the embedded digest
unchanged through the embedded helper/workspace paths together with that exact
`start_lease`. The worker invokes `start_context.start_argv` from
`start_context.scope_cwd` verbatim instead of transcribing a visually similar
private path. Internal `dispatched_at` task-plane state is not a worker command
input. `task-start` performs authoritative canonical digest and exact lease
validation, so workers never guess JSON serialization or reuse another launch.
Incoming-handoff reading and deeper preflight follow. The start budget is 60
seconds.
If the execution environment releases the managed worktree when the arming
process exits, Task Implementer can use its hidden atomic sequential-worker
form. That form starts one fresh ephemeral `codex exec` child before
`task-arm` or `task-rearm` returns, passes only the exact immutable worker
context, releases the completed coordinator transition lock so `task-start`
can acquire the task scope normally, pins medium reasoning effort for normal
work and low effort for recovery-only continuation so one model turn stays
within the heartbeat lease, and waits for completion. It does not
recreate the worktree, widen claims, or let the coordinator implement the task.
A zero child exit is accepted only after the assignment's exact immutable
result file exists; successful start or recovery alone never completes a task.
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
The watch result reports the bounded changed-path set and exact violating paths
so the coordinator can distinguish a missing claim from an unrelated mutation
without reading or copying file contents.
Resume routes any hard worker-guard result directly to confirmed recovery even
when the final heartbeat is fresh; heartbeat freshness cannot override a
read-only, scope, stall, or total-time stop condition.
If the exact recovery cwd is visible only while `run-resume` is still active,
Task Implementer uses its hidden atomic recovery-worker form. It starts a fresh
ephemeral child in that cwd before returning and makes the returned exact
`recover_argv` the child's first transition, preserving fresh-session ownership.
If recovered dirt exceeds the immutable claims, recovery becomes reporting-only:
it returns `replan_required` and the exact violating paths without commit
authorization. The fresh worker publishes terminal `REPLAN_REQUIRED` evidence
without another edit; Task neither discards nor adopts the dirty bytes.

Workers never edit the shared handoff, managed specs, common docs, other refs
or worktrees, or the source checkout. An undeclared path requirement stops
with `REPLAN_REQUIRED` before edit or commit.

## Integration And Promotion

The coordinator verifies worker Git evidence independently, then merges task
branches into a temporary integration branch in stable task-ID order with
`git merge --no-ff --no-edit`. Shared managed specs, README/design docs, and
changelog remain coordinator-owned.

At planning, the coordinator verifies the exact Worktree-owned persistent lane,
reserves the candidate through private `checkpoint-prepare`, reviews every
changed path, then lets `wave-plan` atomically checkpoint that exact token while
acquiring the generation lease and initial claims. The resulting clean head is the
coordinator and first-wave baseline. It never creates another promotion branch,
fetches, resolves a remote default, or mutates the source checkout.

After combined validation, integration `code-review`, and steering
and project-agent-instructions reconciliation, the unchanged clean lane branch
advances under a shared promotion lock with
`git merge --ff-only <verified-integration-SHA>` after the integration branch
is verified at that SHA. Before that promotion, clean verified worker
worktrees are removed and worker refs are deleted with exact expected-old
SHAs. This fast-forward promotion is the only point where tasks become done.

For non-final waves, the integration worktree is removed after promotion. The
final integration remains until terminal lifecycle evidence is recorded and
any exact sealed overlay is promoted, then cleanup performs exact-SHA
integration-ref deletion. Failures preserve exact resources for recovery. The
workflow never runs broad prune/gc, cherry-picks, rebases, squashes, pushes, or
force-removes.

After the terminal-sealed last-wave cleanup, final changed-surface `$align`
evidence is sealed before the lane generation is released. Finalization
revalidates the exact promoted lifecycle receipt, clean lane head, and absent
resources. An interruption after the handoff is marked done remains
recoverable: repeating `run` finishes the same private release instead of
starting a new generation.

`integrate [project-folder]` requires both the recorded source checkout and
lane to be clean and free of Git operations, with no active generation. It
serializes by source ref, builds one exact two-parent candidate from the current
source head and latest lane head, runs combined validation and review on that
candidate, and promotes only the validated SHA through an expected-old
compare-and-set. A `REQUEST CHANGES` review is not treated as a Git conflict:
Task Implementer binds the findings digest to the exact candidate, archives
that immutable commit privately, removes only the exact clean candidate
resources, releases the reservation, and reopens the pending lane for a
correction generation. The same explicit integration workflow owns that
correction and repeats validation and review before promotion. Merge conflicts
and uncertain state are retained. Review rejection may preserve unrelated
primary dirt because it neither stages nor advances the source; candidate
creation and promotion still require a completely clean primary. Success consumes all pending generations,
releases their claims, and rearms the same lane at the merge head.

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

Coordinator v7, wave v4, mutable task-plane v5, immutable assignment v7/result
v3, incoming-handoff v1, interop v4, resume-control v1, and digest-addressed
pending-plan v1 records live with the run under the private prompt workspace.
Every Git mutation is journaled
before execution and re-observed afterward. A repeated `run` resumes the
artifact-specific current truth without recreating branches, worktrees,
assignments, commits, merges, promotions, generations, or queue activations.
An unstarted assignment must match the current canonical worker guardrails. A
successfully started current-v7 assignment remains bound to its exact accepted
digest even if later source wording adds guardrails; recovery supplies the
current transient commit and result-publication contexts without rewriting the
immutable assignment or admitting an older schema. Active assignments still
require the exact currently executing helper. After an assignment becomes
committed, merged, or superseded, its recorded helper path is digest-bound
historical evidence and is not rebound to a relocated source or installed copy.

The private resume planner validates every indexed artifact plus Git,
Worktree, lease, revision, immutable-input, and lifecycle identity before it
returns one outcome: execute one exact transition, wait for a proven live
worker, request stopped-owner confirmation, block with retained evidence, or
report exact completion. `resume-control-v1` binds execution to the observed
state digest and stores the exact canonical capacity, task, commit, evidence,
stop-authority, or final-alignment arguments. An interrupted intent replays
those arguments; a transition whose state is already committed finishes
projection reconciliation without repeating its mutation. An execution lock
spans token verification through transition and projection commit while the
owning reentrant scope lock prevents another state writer, rejecting concurrent
or stale tokens. The first controlled transition bootstraps this state under
both locks, and every successful controlled response carries the next
authoritative resume plan and token when executable. Existing journal-less
coordinator-v7 runs can be adopted only at an exact stable boundary; this is
not a compatibility path for coordinator-v1 through v6.

`coordinator.json` hashes the complete ordered indexed wave plan. Replanning
may retain completed waves and replace only the correction tail, but the plan
identity still includes both parts. A private owner recovery exists only for a
current-v7 run with the proven historical replacement-tail signature. It
requires caller-supplied old and combined digests, exact deterministic
replacement wave IDs, a fully done retained prefix, matching wave/task planes,
and no assigned or running replacement worker. Its small intent journal makes
the plan-basis-first and coordinator-second repair repeatable without changing
task, worker, Git, Worktree, lease, promotion, or public workflow state. If
canonical spec receipt bytes advanced, the owner first refreshes the ready
refinement's compiled managed-requirements digest. Recovery republishes the
validated impact, accepts only `retain_plan`, and binds that exact impact into
both the journal and repaired basis. It never repairs stale refinement or
accepts material impact.

After coordinator creation, machine-readable state owns effective run status.
`handoff.md` remains the human recovery view and pending correction input, but
its status cannot route execution. Resume reconciles only coordinator-owned
projection fields with compare-and-swap protection and preserves task input,
failure history, and narrative content. Missing machine-owned projection fields
or sections are reconstructed from validated coordinator state; duplicate or
conflicting projection fields fail closed.
Task projection has one canonical mapping: planned is `pending`; assigned,
running, committed, and merged are `in_progress`; failed is `blocked`; a clean
no-op failed task retained by a correction plan is `superseded`; and only a
promoted or cleaned wave is `done`. A hidden current-v7 recovery accepts only
the historical unsupported `committed` preimage, an idle adopted controller,
matching coordinator/wave/task planes, and the caller-supplied handoff digest.
It journals the exact postimage and can replay an interrupted write without
changing machine state, pending correction input, or narrative bytes.

Before a promotion-review correction changes coordinator or wave state, its
unindexed task records are staged as one immutable `pending-plan-v1` artifact
bound to the active resume epoch and token. Retries consume those exact staged
bytes even if live Markdown changes after staging, closing both sides of the
partial-publication window without adding a legacy state path.

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

- `SKILL.md`: explicit five-action coordinator contract.
- `agents/openai.yaml`: UI metadata and explicit-only policy.
- `references/prompt-workspace.md`: private storage, routing, current state, errors,
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
- `scripts/prompt_workspace_resume.py`: pure resume planner, v7 adoption,
  transition fencing, and handoff projection.
- `scripts/prompt_workspace_recovery.py`: exact owner recovery for proven
  current-v7 plan-digest and handoff-projection writer defects.
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
- Public surface remains exactly `workspace init`, `workspace reuse`, `run`,
  `integrate`, and `workspace remove`.
- External database, Kubernetes, Terraform, migration, and publication actions
  remain singleton and need separate explicit authority.
- Use `$align` after the final promoted wave; use `$sdlc-start run <prompt-ref-or-file>` for Agentic
  SDLC.
