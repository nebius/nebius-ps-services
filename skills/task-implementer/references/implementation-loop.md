# Dependency-Wave Implementation Loop

Read this reference before task decomposition, scheduling, worker dispatch,
integration, promotion, cleanup, or recovery.
The dependency wave is the logical unit of parallelism and atomic promotion.

## Task Contract

Inspect the repository before locking IDs. Normalize the prompt into stable
`TI-REQ-nnn` requirements and stable `task-1..task-n` records. Each pending
task must contain:

- source revision and requirement/design mapping;
- priority and dependencies;
- goal, rationale, plan, implementation steps, and rollback/stop conditions;
- write claims using `exact: repo/path` or `prefix: repo/directory`;
- conflict domains using `class:stable-key`;
- focused validation, end-to-end validation, and done criteria.
- every prompt/repository constraint applicable to that worker, repeated in
  rollback/stop context rather than left only in the parent prompt.

Combine overlapping work before IDs lock when one worker can produce one
coherent result. Otherwise add an explicit dependency and serialize it. Never
renumber locked IDs or rewrite completed evidence.

## Conflict Analysis

Two tasks cannot share a wave when they:

- write the same path or overlapping directory prefix;
- modify the same core interface or API;
- change the same database schema or migration chain;
- touch the same dependency manifest or lockfile;
- depend on a new shared abstraction;
- modify the same Kubernetes or Terraform resource identity;
- require the same exclusive test resource;
- perform external mutations; or
- make incompatible architecture assumptions.

Unknown claims or domains force a singleton wave. Live database, Kubernetes,
Terraform, migration execution, and publication actions are singleton even
when their resource keys differ, and they retain their own explicit authority
requirements. Their lane generation also holds a class-wide repository domain
claim so the singleton rule remains true across separate project lanes.

## Deterministic Scheduling

Use stable queue order and earliest-fit placement:

1. Validate all dependency references and reject self-dependencies or cycles.
2. A task may enter only a wave strictly after every dependency.
3. Starting at the earliest eligible wave, place it in the first wave whose
   claims and domains are pairwise disjoint.
4. If ownership is incomplete, place it alone.
5. Record logical waves before considering runtime worker capacity.
6. Split a wide logical wave into stable capacity-sized dispatch batches. Batch
   boundaries do not become dependencies and do not change the wave ID.

For the standard fixture, tasks 1-5 form wave 1, tasks 6-7 form wave 2, then
tasks 8, 9, and 10 form singleton waves.

## Preparing A Wave

Require the clean persistent lane on its recorded branch and exact `HEAD`.
Preflight the private root and Git common directory. Reject claims crossing a submodule/gitlink with
`UNSUPPORTED_SUBMODULE_SCOPE`; do not initialize submodules. Reject claims
crossing a tracked symlink with `UNSUPPORTED_SYMLINK_SCOPE` rather than treating
lexical ownership as filesystem containment.

Journal intent before each Git mutation, execute argv-based Git without a
shell, then record return status and observed `HEAD`. Create a unique sanitized
integration branch and full-repository worktree from the exact project base,
then lock it. Never include prompt text or secrets in branch names.

The coordinator preallocates and validates managed requirement/design IDs and
records in the integration checkout, then stages both complete spec files. The
private validator emits a `project-agent-instructions.spec-validation.v3`
receipt only after both tracked managed regions validate and every
non-superseded requirement is covered by a current design record. Persist that
exact object as mode `0600`, then route to `$project-agent-instructions` with
spec owner `maintain-project-specs` and that integration selected-project root. Keep its
manifest, decision, ownership receipt, and state under
`orchestration/project-agent-instructions/`; keep the prerequisite receipt
beside that directory so its private-root marker can initialize from an empty
directory.

Invoke each project-instructions action as one shell-safe canonical command.
For prepared-wave validation, inspect, and render only, the hidden Task helper
exposes a lifecycle authorization adapter. The same adapter may refresh the
run-owned validation receipt at `promotion_pending` while `HEAD` remains the
sealed integrated head and the only staged or unstaged changes are tracked,
non-deleted requirements, design, README, or changelog files owned by the
coordinator. Product, untracked, deleted, symlinked, or advanced-head changes
remain blocked. Inspect and render are read-only with respect to the repository.
The adapter revalidates this workspace, run, active eligible wave, exact
integration Git identity, selected project, canonical helper, and every
run-owned path. The selected-lane lifecycle hook
rechecks that attestation and command digest during reconciliation-required.
The exact head is the original wave base during first preparation. For a
retained correction it is the recorded contract commit, and an adopted sealed
contract is eligible only while its owner journal remains authoritative. The
checkout must remain the exact registered linked worktree in the persistent
lane's common Git directory through replan, prepare, authorization, and
dispatch. Apply and verify remain on the ordinary terminal seal path. No manual
receipt copy, lifecycle-session path substitution, user-entered run ID, or
bypass is part of the workflow.
For the final wave, the first verified implementation promotion retains the
integration worktree. Complete ordinary selected-project lifecycle apply,
verify, and seal before cleanup, then invoke the hidden contract-delta owner
with that exact sealed lifecycle state. It records a terminal receipt even for
a clean no-change seal. If the lifecycle produced only tracked canonical specs
or provenance-owned `AGENTS.md`, it commits those exact bytes in the retained
integration and journals a second fast-forward before cleanup. Final cleanup
and generation release both fail closed without the exact promoted receipt.
If a historical run was released before this terminal boundary existed, do not
amend it or ignore later lifecycle dirt. The hidden owner may validate the
exact sealed canonical overlay and absent resources, then use the ordinary
reviewed whole-lane checkpoint to open and immediately release one supplemental
generation. Preserve the original receipt; public integration consumes both
generations as one contiguous pending range.
When an initial Stop requests contract reconciliation from
`implementation-open`, the shared lifecycle owner atomically enters
`reconciliation-required` before its synthetic continuation. Continue with the
canonical spec cleanup without waiting for `UserPromptSubmit`; if the installed
state did not transition, retain every run resource and report an activation
mismatch rather than changing lifecycle state or spec permissions.
Here, exact integration Git identity means the recorded branch and base head
with either no delta or only the complete selected-project requirements and
design staged. Any unstaged, untracked, deleted, symlinked, sibling, or other
staged path blocks the adapter.

The shared helper alone may create, attach, refresh, adopt, or retire its
v3-managed selected-project root `AGENTS.md` tail. Human prefix bytes remain
preserved.
If state reports `reload_required: true`, stop this execution boundary, start a
fresh coordinator session, rerun and verify the unchanged result, and read the
active project instruction file before locking the contract. Missing distinct
rules is `not-needed`, not a generic file.

Shared specifications, the selected-project `AGENTS.md`, README/design
documentation, and changelog are coordinator-owned commit paths. If this
changes tracked files, create one locked contract commit in the integration
worktree. Dispatch replays the receipt against that commit's spec blobs and
requires its active and ancestor project instruction bytes to belong to the
same clean exact commit. Every worker branch starts at that exact commit. Human-owned project
instructions remain byte-for-byte unchanged; a material gap or conflict blocks
dispatch.

## Assigning Workers

Create one locked full-repository worktree and validated unique branch per task.
The assignment records the worker's absolute scope cwd. For a monorepo scope
`services/nebius-cxcli`, the cwd is
`<task-worktree>/services/nebius-cxcli`, not a service-only checkout.

Reserve the main thread for coordination. Prefer native worker agents up to
available capacity. If native agents are unavailable, start fresh sequential
`codex exec` workers in the same isolated worktrees. Never let the coordinator
implement worker tasks. After one worker fails, stop dispatching new batch
members but allow already-active workers to finish and report.
Give each worker only its immutable assignment, incoming handoff, and exact
`start_context` returned by `task-arm` or `task-rearm`; do not inherit the coordinator transcript or
unrelated conversation history. The coordinator invokes private `task-arm`
only after a real worker slot is available, then spawns the worker immediately
with the returned lease. Queued assignments remain unarmed without consuming a
deadline. The worker reads its assignment and makes `task-start` the first
private transition after verifying immediate Git/cwd identity. It invokes the
assignment's exact `helper_path` with its
`workspace_manifest` and passes the embedded `assignment_sha256` plus the
exact returned `start_lease` unchanged. Use `start_context.scope_cwd` and
`start_context.start_argv` verbatim; never manually transcribe a private
worktree path. `task-start` performs authoritative
canonical digest and exact lease validation, so workers do not recompute the
digest with ad hoc JSON or reuse another launch. The worker reads the incoming
handoff and does deeper preflight only afterward. An armed worker must reach
`task-start` within 60 seconds.
When the host releases managed worktree visibility as soon as the arming
process exits, use the hidden atomic sequential-worker form on `task-arm` or
`task-rearm`. It launches one fresh ephemeral `codex exec` child while that
process still owns the resource lifetime, injects only the exact immutable
worker context, pins medium reasoning effort for normal work and low effort for
recovery-only continuation so a single model turn remains inside the hard
heartbeat-staleness contract, releases the completed resume
transition lock before the child starts, and waits for the child. It never reconstructs a checkout, widens
claims, or transfers implementation ownership to the coordinator.
A zero child exit is accepted only when the assignment's exact immutable result
file exists; `task-start` or `task-recover` success by itself is incomplete.

The successful transition returns one transient
`task-implementer/worker-commit-context-v1`. Invoke its exact `prepare_argv`
from its `lifecycle_cwd`, then use its same `python_executable`, `helper_path`,
`repo_root`, raw `session_id`, and `claim` for execute or review. The raw
`session_id` is the current `CODEX_THREAD_ID`. Never substitute the separately
returned `worker_session_fingerprint_sha256`; that SHA-256 value is persisted
ownership evidence only. The command interpreter must be the returned exact
PATH-canonical `python3` or `python3.N`, while all helper, digest, action,
evidence, repository, and owner checks remain exact.
The returned `worker-result-context-v1` is transient. Publish the final worker
result only to `result_context.result_path`, with
`result_context.publication_cwd` as the explicit external working directory;
never publish it from the selected project cwd. Write the complete unsigned
record to `result_context.draft_path` and invoke its exact `publish_argv`; the
helper owns canonical `result_sha256` computation and atomic immutable
publication. The publisher sorts and de-duplicates validation of
`changed_paths`; coordinator acceptance compares that canonical path set rather
than worker-authored list order. If the helper exits after the immutable file
appears, verify the published result before deciding whether any replay is
needed.
Publish successful work only with exact lower-case `committed`; publish a safe
terminal correction stop only as `REPLAN_REQUIRED`. The publisher rejects all
other spellings. Retained pre-fix `COMPLETED` evidence has one bounded migration
from its already-blocked failed state after full commit, tree, claim, digest,
and evidence revalidation.

A terminal `REPLAN_REQUIRED` result remains valid immutable evidence. Resume
replays its acceptance and finish projections idempotently. Correction
replanning may supersede it only after proving all of the following together:
the task result and task plane are failed, the reported commit equals the
assignment base, `changed_paths` is empty, the assigned worktree is clean at
that base, and the failed batch is the exact blocked frontier. The superseded
task stays in history and predecessor handoffs; dispatch cleans only its exact
resource before starting the appended correction. Exact tracked dirty
`REPLAN_REQUIRED` evidence at the base may follow this path only after an
internal compare-and-set quarantine ref proves the base parent, exact tree
bytes, and complete changed-path set on replay, then exact-path restore returns
the worker to the clean base. Retain that ref through correction promotion and
delete it only during successful wave cleanup. Untracked, committed,
mismatched, or unverifiable dirt stays blocked.

When `run-resume` requires confirmed `task-recover`, give the replacement its
returned `worker_context` and invoke `recover_argv` from `scope_cwd` verbatim.
Do not reconstruct either value from a coordinator message or a visually
similar monorepo scope path.

Each worker must:

1. Read its immutable assignment; verify absolute cwd, real worktree root,
   branch, exact base SHA, and clean state; invoke `task-start` as its first
   private transition through the embedded helper/workspace paths, passing the
   embedded digest and coordinator-issued start lease unchanged for the
   helper's canonical validation.
2. Verify the incoming-handoff path and digest, then verify scope, claims, and
   conflict domains before deeper preflight.
3. Enforce the assignment's canonical worker guardrails. Stay inside the
   assigned worktree/private Task Implementer state. Installed Codex skill
   instructions/helpers and standard local executables are read/execute-only as
   required by the assignment and must never be modified. Do not intentionally
   write other filesystem paths or access network, credentials, external
   services, or live runtimes unless the immutable assignment explicitly
   authorizes that exact action.
4. Invoke private `task-heartbeat` at least every 30 seconds with the current
   phase. Dependency-free `standard` tasks warn at 240 seconds and stop at 300
   seconds without claimed progress; dependent `integration` tasks warn at 360
   seconds and stop at 420 seconds. A heartbeat becomes hard-stale at 240
   seconds. Invoke each heartbeat directly; never
   create a background process or autonomous heartbeat loop. Treat the
   assignment and incoming handoff as complete task context; do not reread the
   full prompt or coordinator-only state.
   Invoke `task-start` exactly once. Only mutations inside immutable write
   claims count as progress; any other mutation stops with
   `WORKER_SCOPE_VIOLATION`.
5. Stop with `REPLAN_REQUIRED` before editing if an undeclared path or domain
   is needed.
6. Implement exactly one task without touching the source checkout, shared
   handoff/spec/docs, other refs/worktrees, Git maintenance, or external state.
7. Run focused and end-to-end validation.
8. Invoke `code-review`, fix safe scoped findings, and revalidate.
9. Invoke `$commit` exactly once. The task branch must contain exactly one
   direct-child commit from the common contract base.
10. Write one private result with assignment digest, status, commit, exact paths,
   summary, decisions, open risks, validation, end-to-end evidence, and review
   evidence. Stop. Never reuse this worker session for another task.

Task `committed` means ready for integration, not done.

From dispatch onward, the coordinator invokes private `task-watch` every 30
seconds. `WORKER_PRESTART_TIMEOUT`, `WORKER_PRESTART_MUTATION`,
`WORKER_STALLED`, `WORKER_READ_ONLY_TIMEOUT`, or `WORKER_TIMEOUT` requires
immediate interruption, stopped-status confirmation, and explicit recovery or
blocking. Treat `WORKER_SCOPE_VIOLATION` the same way. Never silently wait or
blind-retry no-progress work. Resume routes a hard guard result to confirmed
recovery immediately; a fresh final heartbeat never overrides that stop.
When the returned recovery cwd is scoped to the observing helper's process
lifetime, use the hidden atomic recovery-worker form on `run-resume`. It starts
one fresh ephemeral child before returning and uses the exact `recover_argv` as
that child's first transition; the coordinator never impersonates recovery.
Out-of-claim recovered dirt is reporting-only. Return `replan_required`, the
exact violating path names, and no commit authorization; the fresh worker then
publishes terminal `REPLAN_REQUIRED` evidence without another edit. Preserve
the dirty resource until the ordinary correction dependency owns its outcome.
At the profile-specific `READ_ONLY_DEADLINE_NEAR`, require an immediate
claimed-file edit or blocker before the hard cutoff.

## Verifying And Integrating

The coordinator independently verifies every result:

- assignment/result identities and immutable digests match;
- worktree and branch identities match the assignment;
- branch is clean and its `HEAD` is the reported commit;
- exactly one commit descends directly from the common base;
- actual changed paths exactly match the result and fit locked claims;
- validation and review evidence are present.

After all tasks are committed, merge branches into the integration branch in
stable task-ID order:

```text
git merge --no-ff --no-edit <task-branch>
```

Never cherry-pick, rebase, squash, push, or merge directly into the project
branch. An unexpected conflict aborts the merge, marks the wave blocked, leaves
the project branch unchanged, and retains all worktrees/branches.

After worker merges, the coordinator may update only shared managed specs,
provenance-owned project instructions, README/design docs, and changelog
evidence. Requirements/design, validation-receipt, effective-config, evidence,
ancestor-instruction, ownership, or target drift invalidates the
project-agent-instructions state. Dispatch revalidates the exact clean
integration contract checkout, not the persistent lane. Rerun and verify it at
the safe boundary before future dispatch. Invoke private `coordinator-commit`
for a non-empty shared-file diff. It accepts only the sealed integrated head and
tracked, non-deleted requirements, design, README, and changelog changes, then
journals one exact direct-child commit; interrupted replay may only reuse that
clean commit. Any product-code correction becomes a new isolated task.

## Validation And Promotion

Run combined validation and integration `code-review` in the integration
worktree. Reconcile all queued steering. If steering contradicts the active
wave, preserve the integration branch and stop before promotion.

After combined evidence is bound to the integration tip, remove each verified
clean worker worktree and delete its ref with an exact expected-old SHA. If a
worker is dirty, advanced, or unverifiable, retain it and block promotion.

Recheck that the persistent lane is clean, on its recorded named branch, at the
recorded base. Hold the common-Git-directory promotion lock
through precheck, merge, and postcheck:

```text
git merge --ff-only <verified-integration-SHA>
```

Verify the integration branch still identifies that SHA. If Git reports
failure, re-observe project `HEAD` and classify it as unchanged,
successfully promoted, or unexpectedly moved. Never reset, rebase, force, or
repeat blindly.

Only after the project `HEAD` equals the verified integration tip may the
coordinator mark wave tasks `done` in `handoff.md`. Dependency satisfaction is
promotion-based, not worker-result-based.

## Cleanup

After verified promotion, prove the integration branch is reachable from the
promoted project `HEAD`. For its clean managed worktree:

1. unlock the exact path;
2. run non-force `git worktree remove <path>`;
3. run `git update-ref -d refs/heads/<branch> <exact-expected-tip>`.

Task resources were removed before promotion; remove the integration resource
after promotion. Never use `--force`,
`rm -rf`, broad `git worktree prune`, or `git gc`. Dirty, unreachable, or
failed resources remain recorded. Cleanup failure does not roll back promotion.

## Resume And Failure Classification

A repeated `run` first invokes the pure resume planner. It validates the run
manifest and settled revision; every indexed coordinator-v7, wave-v4,
task-plane-v5, assignment-v7, incoming-handoff-v1, and result-v3 artifact;
interop-v4 and the exact external lease; and live Git refs, registrations,
heads, worktrees, and cleanliness. It returns exactly one of `execute`, `wait`,
`requires_confirmation`, `blocked`, or `complete`. Only `execute` carries an
opaque digest-bound token and one exact next private transition.

Before `task-start`, assignment-v7 guardrails must equal current source. After
successful start records that exact assignment digest, timestamp, and hashed
worker identity in task-plane-v5, later source-only guardrail wording growth
does not invalidate the accepted immutable assignment. Active assignments keep
an exact current-helper requirement. Once committed, merged, or superseded,
the assignment's recorded helper path is digest-bound historical evidence;
terminal observation neither rebinds nor executes it. Every task, handoff, Git,
Worktree, and lease check remains current, and recovery returns current
transient commit and result-publication contexts without rewriting assignment
bytes or admitting older schemas.

The transition executor reacquires the scope and owning Git/Worktree locks and
recomputes the observation. A private execution lock spans that verification,
the selected transition, and state/projection reconciliation while the owning
reentrant scope lock excludes another helper state writer. `RESUME_STALE` means
another writer advanced and
the coordinator replans without mutation. `resume-control-v1` persists one
intent before an effect and records effect observation, machine-state commit,
projection commit, and terminal digest. A nonterminal intent replays the same
idempotent transition; it never starts a second transition. Existing JSONL
journals remain audit evidence rather than the only recovery authority.
Before the first controlled mutation of a new run, the executor creates the
private orchestration directory and adopts resume control while holding both
locks. Each successful controlled transition then returns the next
authoritative `resume` outcome, canonical arguments, and token when executable;
the coordinator consumes that object unchanged rather than inferring a next
command.

A journal-less coordinator-v7 run can be adopted at a fully validated stable
boundary. Missing or malformed planes, unresolved journal intents, dirty or
divergent Git state, unknown writers, lease ambiguity, or immutable-artifact
drift block adoption and retain all evidence. This current-schema adoption does
not read, migrate, or execute coordinator-v1 through v6.

After coordinator creation, machine state owns effective status. Handoff is a
CAS-protected projection; reconciliation updates only machine-owned status and
current-action fields while preserving pending corrections, failure history,
and narrative bytes. Final human completion is published only after external
generation release and local interop reconciliation; queue activation follows
with the same exact-once transition receipt.
Project planned tasks as `pending`; assigned, running, committed, or merged
tasks as `in_progress`; failed tasks as `blocked`; and tasks only as `done`
after their wave reaches promoted, cleanup, or done. If an affected current-v7
run contains the historical unsupported `committed` projection, use only the
hidden owner recovery with its exact handoff preimage. It requires idle resume
control and consistent indexed machine planes, journals the canonical
postimage, preserves unindexed corrections and narrative, and then returns the
run to ordinary repeated resume.

Before a promotion-review replan consumes unindexed task sections, stage the
currently dependency-ready frontier's exact normalized records in one digest-
addressed immutable `pending-plan-v1`
artifact. Publish coordinator, task-plane, and wave changes only from that
artifact. After a crash, reuse the same bytes; if live Markdown changed, stop
for replan instead of combining old and new correction intent.

The workflow must converge after interruptions such as:

- intent journal written but worktree command outcome unknown;
- worktree created but state update interrupted;
- worker commit exists but result acceptance interrupted;
- worker stopped with declared dirty state or one direct-child commit;
- some ordered merges completed;
- fast-forward succeeded but private promotion state did not update;
- one worktree or branch cleaned before interruption.

Idempotent retries re-observe exact refs and worktrees. They never recreate a
foreign collision, duplicate a commit/merge, overwrite a divergent immutable
assignment/result, or force cleanup.

Fresh heartbeats and unexpired start leases return `wait`. Expired prestart or
stale running workers return `requires_confirmation`; only explicit proof that
the prior worker stopped permits rearm or recovery. Missing active resource
paths follow the same confirmation boundary before exact registered-resource
rehydration. Ambiguous state returns `blocked` with one minimum next action.

Before resources exist, replanning replaces the active planned tail in the
coordinator schedule. Completed waves remain indexed; superseded planned wave
files are retained as blocked history but are not part of final completion or
semantic validation. Each coordinator plan records the exact accepted prompt
revision and intent digest. Preparation fails with `REPLAN_REQUIRED` when
pending steering, reconciliation, refinement, or a newer bound intent makes
that plan stale. After the final wave is promoted and cleaned, integration
review may append a newly discovered isolated correction tail before
finalization. A blocking review at `promotion_pending` may append one
dependency-ready correction frontier directly to the retained wave. Every
prior task must already be merged, the lane must remain on the original wave
base, and the integration tip must equal the sealed integrated head or contain
the owner-journaled sealed contract commit. Correction workers use that head
as their immutable base; prior assignments keep their own task-plane bases.
Dependent frontiers repeat after the previous frontier merges. A resource-free
future task may change only its dependencies after the referenced correction
is indexed. Re-integration replaces the sealed integrated head and requires
fresh combined evidence before promotion. Before any replacement
wave or coordinator record is written,
replanning atomically extends the active generation's Worktree-owned repository
claims. Earlier claims remain held as a conservative superset until generation
integration.

A selected-lane lifecycle may leave the canonical requirements/design pair and
an optional provenance-only `AGENTS.md` dirty after sealing. The private
contract-delta owner accepts only that exact unstaged set, binds lifecycle,
instruction-state, prompt-impact, and file digests, and creates one coordinator
commit in the retained integration. Ordinary cleanliness is waived only while
the journal, lane bytes, integration ancestry, and external lease remain exact.
The one claim-bound final coordinator reconciliation may replace adopted spec
bytes at the sealed promotion tip. Promotion journals temporary restoration of
those paths to lane `HEAD`; an interrupted or failed fast-forward restores the
original adopted bytes from their immutable commit before returning.
Cleanup refreshes the ready requirements refinement and prompt-impact plan
before deleting that integration, but only when the exact final coordinator
commit explains the spec advance and impact analysis retains the remaining
plan. Promotion recovery and completion treat an older adoption journal as
historical evidence only when its exact done wave, promoted head, and ancestry
into the active wave base are all proven. Any unrelated, ambiguous, or material
drift remains blocked.
If promotion stops after the selected lane fast-forwards but before wave state
is published, replay admits stale pre-promotion spec impact only when the clean
lane exactly equals the sealed integration target. Cleanup still proves the
single final coordinator commit and settles its impact before resource removal.

For an expired prestart task, require explicit confirmation that the old worker
stopped, then have the coordinator invoke `task-rearm` with the exact observed
start lease. The compare-and-swap accepts only the exact clean locked base,
preserves the immutable assignment and all existing wave and generation
resources, and returns a fresh lease. Any stale or conflicting lease fails
closed. After an interrupted response, re-observe `task-watch` and continue
with its current active lease. An active deadline or prestart mutation also
fails closed. The replacement invokes normal `task-start` from its assigned
scope cwd with the fresh lease as its first transition. A rearm mismatch is
`WORKER_START_LEASE_CONFLICT`; `task-start` rejects the old worker's lease as
`WORKER_START_LEASE_INVALID`.

For an interrupted running task, require the same stop confirmation, then have
the fresh replacement invoke the `recover_argv` returned by `run-resume` from
its exact `worker_context.scope_cwd` as its
first transition. The coordinator must not invoke running-worker recovery
because session ownership binds to the caller. Recovery accepts only the locked
base or one direct-child commit with dirty paths inside the assignment claims.
A base-state recovery returns the same transient commit-context contract for
the replacement's raw session and persists only its session fingerprint.
A blocked task or undeclared path stays retained for operator-directed
recovery; do not delete or replace unmerged evidence.

If an assigned worker, running-wave integration, or promotion-pending
integration directory disappeared but its lease row remains `present`, do not
call `task-recover`, ordinary worktree creation, prune, remove, or branch
deletion yet. After all prior workers are confirmed stopped, the coordinator
invokes private `wave-resource-recover --confirmed-stopped` from the exact
owning scope. The command requires the active generation's exact resource tuple
and a unique locked Git registration, then checks branch and registered HEAD,
administrative HEAD, clean index, and the running integration contract,
recorded integrated head, or worker base/direct-child rule before using Git's
locked-worktree rehydration form. A promotion-pending wave may contain only
immutable `merged` and safely `superseded` task history; every other task state
blocks recovery. It journals and revalidates the checkout,
keeps the lease `present`, changes no task or promotion state, and reports that
filesystem-only edits cannot be recovered. Only then may the fresh replacement
invoke normal `task-recover`, or promotion validation continue. Any staged
index, lock, symlink, registration, lease, branch, head, or ancestry ambiguity
remains retained and blocked.

Failure rules:

- worker failure: retain its exact branch/worktree; stop new dispatches;
- scope expansion: `REPLAN_REQUIRED` before edit/commit;
- merge conflict: abort integration merge and retain the wave;
- validation/review failure: retain integration state before promotion;
- lane branch drift: `PROMOTION_BLOCKED` with no mutation;
- promotion uncertainty: classify observed `HEAD` before retry;
- cleanup failure: report retained inventory after successful promotion;
- missing safe local bootstrap: `ENVIRONMENT_BLOCKER` without copying primary
  checkout state;
- unfinished v1 state: `WORKFLOW_UPGRADE_REQUIRED`, no migration shim.

## Final Run Completion

Start the next planned wave from the newly promoted project `HEAD`. After the
last wave is promoted and safely cleaned, run changed-surface `$align`, verify
managed specification state, record final evidence, and invoke the private run
finalizer. The finalizer sets the handoff to `done` and releases the active lane
generation only when the lane is clean at the final promoted head and all
internal resources are absent. It seals an immutable generation receipt and
leaves it pending for `$task-implementer integrate`; it never invokes source
integration or publishes the lane. If release is interrupted, repeat the same
finalizer; do not start a new run or clear state. A later explicit `run` may
acquire the next generation immediately. Static validation and observed
live/runtime proof must be reported separately.

## Persistent Lane Integration

`$task-implementer integrate [project-folder]` consumes every contiguous
pending generation for the exact lane. Require no active generation, no Git
operation in progress, and complete cleanliness in both the recorded source
checkout and lane. Serialize by source ref, then construct one two-parent candidate
whose first parent is the exact source head and second parent is the
latest lane head. Conflicts remain retained recovery state.

Run nonmutating combined validation, integration `code-review`, and changed-
surface checks in the exact candidate worktree. Promote only that candidate
through expected-old compare-and-set. After promotion, fast-forward the same
lane to the merge head, consume the pending range, release its repository
claims, and rearm the lane. Never cherry-pick, rebase, squash, force, or call a
public `$worktree` lifecycle action for a Task Implementer lane.
