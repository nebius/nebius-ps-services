---
name: task-implementer
description: "Requires explicit invocation to create or reuse a persistent per-project Git worktree lane, run durable brownfield implementations through dependency waves, integrate pending generations, or remove an idle lane; after explicit session binding, also owns hook-routed accepted direct-prompt continuation. Do not use for ordinary one-shot implementation, Agentic SDLC, standalone Git workflows, or generic parallel-agent requests."
---

# Task Implementer

## Help

For `$task-implementer --help` or `$task-implementer -h`, return concise help and stop before
any workflow step. State the purpose and invocation policy. Show exact usage
for every public action. Describe each public action, positional
argument, and flag in one concise line, including `-h, --help`; say "No
additional public flags" when there are no others. Use only the documented
public interface. For internal or coordinator-only skills, state that boundary
and that no standalone public workflow action exists. After the selected
`SKILL.md` is loaded, help is report-only: do not call any additional tools,
inspect project state, or modify files, private state, Git, or external systems.
Never expose private helper actions or flags or treat help as workflow
authorization.

## Purpose

Coordinate a complex brownfield request from one durable private Markdown
prompt. One `run` plans every task, groups compatible work into dependency
waves, dispatches isolated workers, integrates verified commits, promotes each
wave atomically, and continues until the run is done or blocked.

## Invocation Policy

Public entry requires explicit invocation. Keep
`policy.allow_implicit_invocation: false` in `agents/openai.yaml`. After
`workspace init` or explicit `run` binds the current session, only a current
`prompt-session-intake` hook receipt may route a direct prompt internally.

## Public Interface

Expose exactly these four actions:

```text
$task-implementer workspace init [project-folder]
$task-implementer run <prompt-ref-or-file>
$task-implementer integrate [project-folder]
$task-implementer workspace remove [project-folder]
```

- `workspace init` defaults to the exact current directory.
- `run` accepts one exact unique prompt ref, full prompt ID, managed absolute
  path, or managed filename.
- `integrate` defaults to the exact current project and consumes every pending
  released generation for that lane.
- `workspace remove` defaults to the exact current project and removes only an
  idle, clean, fully integrated lane. It preserves private prompts and run
  history.
- Never require a prompt ID, run ID, wave ID, task ID, branch, worktree, or
  internal transition.
- Steering means editing the same prompt and repeating `run`; do not expose a
  separate action.
- Do not add public `parallel`, `merge`, `cleanup`, `upgrade`, or compatibility
  aliases.

## When To Use

- The user explicitly invokes one of the four actions.
- The prompt-session-intake hook routes the exact current accepted turn for a
  session already bound by one of those explicit actions.
- A brownfield request benefits from durable steering, dependency ordering,
  isolated worker commits, combined validation, and resumable integration.
- Independent tasks have complete, disjoint ownership and can run in parallel.
- Conflicting tasks need deterministic dependency waves.

## When Not To Use

- Do not invoke it implicitly for a complex task or generic parallel-agent
  request.
- Do not use it for one-shot implementation, Agentic SDLC, chat-only ideation,
  design-only work, standalone review/commit/PR/merge, release, or publication.
- Do not use parallel workers when ownership is incomplete or tasks overlap.

## Inputs

- One explicit public action.
- The canonical Git common directory, primary checkout, exact named
  non-default source ref, committed project scope, repo instructions, and
  immutable prompt snapshot.
- The Worktree-owned persistent lane for that exact logical identity. It is a
  full-repository linked worktree and its branch is the sole promotion target
  for internal dependency waves.
- Source-checkout dirt is permitted during initialization and runs, is excluded
  from the committed lane baseline, and is never copied or mutated. The lane
  itself must be clean before a run. Integration requires both source checkout
  and lane to be completely clean. If the primary source is dirty at
  integration time, stop with one actionable handoff: the user invokes a fresh
  explicit `$commit` in that primary checkout to review and commit the complete
  repository diff, then repeats `integrate`. Never auto-commit source dirt and
  never narrow it to the selected project.
- Complete task dependencies, exact or directory-prefix write claims, keyed
  conflict domains, validation, and done criteria.

## Required Reads

- Read `references/prompt-workspace.md` before workspace, routing, steering,
  state recovery, or sandbox decisions.
- Read `references/prompt-requirements-refinement.md` before compiling a new or
  revised prompt into managed requirements or asking clarification questions.
- Read `references/implementation-loop.md` before decomposition, wave planning,
  worker dispatch, integration, promotion, or cleanup.
- Read the run manifest, exact bound snapshot, steering ledger, full handoff,
  coordinator state, active wave, assignments, results, and journals.
- Inspect relevant `AGENTS.md`, code, tests, README/design docs, changelog, Git
  state, managed requirements, and managed design records.
- After managed requirements and design are valid, explicitly route to
  `$project-agent-instructions` with the exact `spec-inspect` receipt. Verify
  its final v3 state and read any active selected-project instruction file
  before locking the coordinator contract.
- Use `brainstorm` and `design` when their routing conditions apply.
- Every worker uses `code-review` and exactly one `$commit`; the coordinator
  uses combined validation, integration `code-review`, and final `$align`.
- Verify version-sensitive Git and Codex guidance against official docs.

## Writes

Private state stays outside Git:

```text
${CODEX_HOME:-$HOME/.codex}/task-implementer/
├── projects/<project>/<scope>/
│   ├── workspace.json
│   ├── prompt-queue.json
│   ├── queued-prompts/<prompt>/<digest>.md
│   ├── prompts/00-START-HERE.md
│   ├── prompts/*.md
│   └── runs/<run>/
│       ├── manifest.json
│       ├── steering.json
│       ├── requirements-refinement.json
│       ├── inputs/<revision>/prompt.md
│       ├── handoff.md
│       └── orchestration/
│           ├── coordinator.json
│           ├── interop.json
│           ├── project-agent-spec-receipt.json
│           ├── project-agent-instructions/
│           │   ├── manifest.json
│           │   ├── decision.json
│           │   ├── ownership.json
│           │   └── state.json
│           ├── waves/wave-001.json
│           ├── tasks/wave-001/task-1.json
│           ├── assignments/wave-001/task-1.json
│           ├── incoming-handoffs/wave-001/task-1.json
│           ├── results/wave-001/task-1.json
│           └── journals/wave-001.jsonl
└── worktrees/<project>/<scope>/<run>/wave-001/
    ├── integration/
    ├── task-1/
    └── task-2/
```

Each linked worktree contains the full repository. For scope
`services/example`, a worker operates from
`<task-worktree>/services/example`. Never copy ignored/untracked files,
dotenv files, credentials, caches, or primary-checkout local state.

`handoff.md`, managed requirements/design records, the private
project-agent-instructions receipt, shared README/design docs, and changelog
evidence are coordinator-owned. The shared skill exclusively owns creation or
provenance-safe refresh of project-root `AGENTS.md`; workers never edit it.
Worker assignments are immutable; workers write only their locked product scope
and private result record.

## Process

### `workspace init [project-folder]`

1. Resolve the installed skill and canonical Git root/exact project scope.
2. Require configured `origin/HEAD`, an attached named source branch different
   from that default, and a project directory present in its committed `HEAD`.
   Invoke the private Worktree-owned lane
   ensure primitive. Create one full-repository lane from that exact commit or
   reuse the existing lane for the same common directory, primary checkout,
   source ref, and scope. Do not copy source-checkout dirt.
3. Invoke private `init`. Create, verify, or safely rebind workspace-v2 to a
   newer lane incarnation. Ensure generated `00-START-HERE.md`; exclude it from
   prompt parsing. Create exactly one starter prompt only when no actual prompt
   exists; preserve all existing prompts and run history. The default VS Code
   build task creates a fresh prompt ID. Do not clone prompt files manually.
4. Ask VS Code to reuse its last active window for the generated workspace when
   available. Loading the workspace restarts that window's extension host and
   may interrupt its terminal or Codex UI; editor failure does not invalidate
   initialization.
5. Return workspace and lane paths plus prompt metadata. Stop without queuing
   work.

### `run <prompt-ref-or-file>`

1. Invoke private `intake`; validate prompt-v3, acquire the scope lock, and
   snapshot the accepted intent once. If another prompt owns active work,
   persist this explicit run request in the private FIFO queue and stop this
   invocation with its queue position. Saving or creating a prompt never
   queues it. Re-running an edited queued prompt updates its accepted snapshot
   in place; an unaccepted queue-head edit blocks activation.
2. For a new or safely reconcilable run, apply
   `references/prompt-requirements-refinement.md`: inspect discoverable facts,
   extract the Ask and optional headings into outcome, context, constraints,
   acceptance, verification, non-goals, and related managed requirement
   fields. Persist stable clarification IDs privately. Ask only for material
   ambiguity and block contract lock while such a question is open or
   reopened. Render and validate the managed requirements/design regions,
   then require private `wave-plan` to bind the latest accepted intent and
   `ready` refinement digest to the exact current managed requirements region
   before acquiring resources. Normalize task IDs, dependencies, write claims, conflict domains,
   validation, done criteria, and prompt/repository constraints in stable task
   order. Repeat every applicable constraint in each task's stop context. Do
   not decide project instructions until the managed requirements and design
   records have both been rendered and validated.
3. Combine overlapping work before IDs lock when it is one coherent result.
   Otherwise add an explicit dependency. Invoke private `wave-plan` and fail on
   cycles or malformed ownership. Acquire the next monotonic lane generation
   at the exact clean lane `HEAD` before creating task resources. Reconcile its
   exact lane ID, incarnation, generation, lease token, run, promoted head,
   release state, clean checkout, and live Git identity on every resume; never
   trust local interop flags alone. Register repository-wide exact/prefix and
   conflict-domain claims before worker creation. Claims overlapping any other
   live lane block the run and remain held through integration. Replanning
   extends the active generation's claims before replacement state is written;
   earlier claims remain as a conservative superset. The active generation
   blocks lane integration and removal.
4. Coordinate every recorded wave through the lifecycle below. A logical wave
   may dispatch in capacity-sized batches, but batching never changes its wave.
5. Reconcile queued steering only at a safe wave boundary. Contradictory
   steering preserves work and stops before promotion. Before preparation,
   private `wave-replan` may replace the resource-free planned tail. After the
   final promoted wave is cleaned, it may also append a newly discovered
   isolated correction tail before finalization. The active coordinator index
   keeps only completed waves plus the replacement schedule;
   superseded planned wave files remain blocked history outside that index.
6. Continue with the next wave from the newly promoted lane `HEAD`. After
   the last cleanup, run final changed-surface `$align`, then invoke private
   `run-finalize` with its concise evidence. Only that transition marks the
   handoff done, seals an immutable generation receipt, and releases the
   generation for later lane integration. It does not integrate the source
   branch, push, or open a PR. An unchanged completed prompt returns
   `ALREADY_COMPLETE`. Editing it creates a linked fresh-full-objective run
   against current project truth; `r0001` is not steering and omission does not
   delete prior accepted product truth. An interrupted generation release returns a private
   finalization-pending outcome and repeats the same final transition. A fresh
   explicit `run` may immediately acquire the next generation, so multiple
   pending generations can accumulate before `integrate`. After finalization
   releases the active generation, activate the unchanged FIFO queue head
   automatically; never overtake blocked active work or reorder the queue.
7. When the current explicit invocation carried a prompt-session binding
   receipt and owns an active objective transition, register the authoritative
   prompt identity/digest through the internal prompt-session objective
   transition. Mark it terminal only after `ALREADY_COMPLETE` or successful
   finalization. Do not register the newly queued prompt as active while
   another prompt still owns the scope. A hook-routed material turn passes the
   same terminal fact when consuming its exact event.

### `integrate [project-folder]`

1. Resolve workspace-v2 and its exact lane identity. Reject an active
   generation, missing or non-contiguous pending receipts, a Git operation in
   progress, source-branch drift, or any dirt in the source checkout or lane.
2. Serialize integration for the recorded source ref. Build one two-parent
   candidate from the exact source head and latest lane head. Retain conflicts
   and recovery state; never resolve, reset, rebase, or force automatically.
3. Run nonmutating combined validation, integration `code-review`, and the
   required changed-surface checks in the exact candidate worktree. Promote
   only that validated candidate with expected-old source-head compare-and-set.
4. Fast-forward the same persistent lane to the promoted merge head, mark the
   entire pending generation range integrated, release its repository claims,
   and rearm the lane for the next `run`.

### `workspace remove [project-folder]`

1. Resolve workspace-v2 and its exact lane identity. Require no active or
   pending generations, no claims or integration recovery, a clean idle lane,
   and proof that the source ref contains the lane head.
2. Invoke the private non-forced lane removal primitive. Delete only the exact
   linked worktree and branch behind expected-head proof. Never remove prompt,
   run, or generation history.
3. Repeated removal is idempotent. A later `workspace init` creates the next
   lane incarnation and safely rebinds the same private workspace/history.

## Dependency-Wave Contract

Build deterministic earliest-fit waves in stable task order:

- dependencies must be in earlier completed waves;
- write claims must be pairwise disjoint;
- conflict-domain keys must be pairwise disjoint;
- unknown or incomplete ownership forces a singleton wave;
- external database, Kubernetes, Terraform, migration execution, and
  publication actions are singleton domains and still need explicit authority.
  Each also reserves one class-wide repository domain claim so distinct keys
  cannot run concurrently in separate lanes;
- cycles fail closed.

Conflict domains cover core APIs, schemas, migration chains, dependency
manifests/lockfiles, shared abstractions, Kubernetes/Terraform resource
identities, exclusive test resources, external mutations, and architecture
decisions. A worker discovering an undeclared path or domain stops with
`REPLAN_REQUIRED` before editing or committing.

## Wave Lifecycle

Wave states are
`planned -> preparing -> running -> integrating -> promotion_pending -> promoted -> cleanup -> done|blocked`.
Task states are
`planned -> assigned -> running -> committed -> merged|failed`.

For each wave:

1. Require the clean persistent lane branch at its recorded exact `HEAD`.
   Never create a promotion branch, fetch, consult a remote default, or touch
   the source checkout. Record the lane mode and exact identity in coordinator
   v6.
2. Register resource intent in the active lane generation, journal
   intent, create and lock an integration worktree/branch from that exact
   commit, then re-observe Git state.
3. In the integration checkout, preallocate and validate coordinator-owned
   requirement/design records, then stage both complete spec files so the owner
   validator can issue a Git-bound receipt. Every non-superseded requirement
   must be covered by a current design record.
   Explicitly invoke `$project-agent-instructions` with spec owner
   `maintain-project-specs`. Persist the exact receipt emitted for that integration
   selected-project root by the private spec validator as mode `0600`, pass it
   to `inspect`, and keep manifest, decision, ownership, and state under
   private orchestration. Render the exact decision bytes and inject them for
   worker guidance, but defer repository `AGENTS.md` mutation.
   Commit-mode `spec-inspect` is historical blob metadata only and returns no
   authoritative project-agent receipt; only the shared current-checkout
   validator may issue that receipt.
4. Require the shared v3 spec receipt plus the exact rendered-rule digest
   before dispatch. After final promoted implementation reconciliation, apply
   and verify the project-instruction decision once as the terminal seal
   mutation. If it reports `reload_required: true`, require a fresh session
   before later managed work.
5. Invoke private `wave-dispatch`. After proving the exact clean integration
   `contract_commit`, it revalidates that checkout's current managed-spec
   receipt against the committed blobs, proves the active and ancestor project
   instructions belong to that commit, and invokes the shared
   project-agent state verifier; stale, missing, or reload-pending v3 state
   blocks dispatch. It then creates a unique validated branch and
   locked full-repository worktree only for the active capacity batch, plus an
   immutable v7 assignment with absolute scope cwd, exact helper/workspace
   paths, base, digest, claims, domains, validation, criteria, canonical worker guardrails, and a
   digest-bound private incoming handoff. The
   handoff contains accepted evidence from all earlier completed waves and
   batches; only the first batch of the first wave has no predecessors.
6. Reserve the main thread for coordination. Dispatch native worker agents up
   to available capacity. If unavailable, use fresh sequential `codex exec`
   workers in the same isolated worktrees; the coordinator never implements a worker task.
   Start every worker with only its immutable assignment, incoming handoff, and
   opaque coordinator-issued start lease, without inherited coordinator
   transcript or unrelated conversation context.
   Invoke private `task-arm` only when a real worker slot is available, then
   spawn that worker immediately. Queued assignments remain unarmed and do not
   consume a start budget. The worker reads the assignment and makes
   `task-start` its first private transition after verifying immediate Git/cwd
   identity. It invokes the assignment's exact embedded helper/workspace paths
   and passes the embedded digest plus coordinator-issued start lease unchanged;
   `task-start` performs authoritative canonical digest and exact lease checks,
   so the worker never invents JSON serialization or reuses another launch. It
   reads the incoming handoff and performs deeper preflight only after that
   transition. An armed worker must reach `task-start` within 60 seconds.
   After one failure, stop dispatching new batch members while
   active workers finish.
7. Every worker first verifies its real worktree root, branch, base SHA, and
   absolute cwd, then invokes `task-start` with the embedded helper/workspace
   paths, embedded digest, and coordinator-issued start lease unchanged. The
   helper verifies the canonical assignment digest and exact current lease and
   returns private authorization and claim paths for one direct-child `$commit`,
   bound to that assignment, running task plane, worker session, branch, and
   base SHA. It next
   verifies the incoming-handoff digest and claims; starts from a
   worker session never used by another task in the run; implements one task; validates; runs
   `code-review`; fixes scoped findings; creates exactly one direct-child
   commit through `$commit`; and writes one private result. It never edits the
   shared handoff or manages worktrees/refs. It obeys the immutable assignment's
   canonical guardrails: stay inside the assigned worktree/private state and
   use installed Codex skill instructions/helpers and standard local
   executables read/execute-only as required by the assignment. It never
   modifies installed files, intentionally writes other paths, or accesses
   network, credentials, external services, or live runtimes unless that exact
   action is explicitly authorized there.
   It records `task-heartbeat` progress at least every 30 seconds. A
   dependency-free `standard` assignment warns at 240 seconds and allows at
   most 300 seconds of read-only preflight; a dependent `integration`
   assignment warns at 360 seconds and allows at most 420 seconds. Before the
   assigned cutoff the worker edits a claimed path or reports a blocker. Every
   heartbeat is one direct bounded command; the hard stale cutoff is 240
   seconds so bounded high-reasoning turns do not become false failures. Never
   create a background process or autonomous heartbeat loop. Treat the immutable assignment and incoming
   handoff as self-contained task context; do not reread the full prompt or
   coordinator-only state.
   `task-start` is single-use. Only mutations inside the immutable write claims
   count as progress; any other mutation is `WORKER_SCOPE_VIOLATION`.
   If an armed worker misses `task-start`, the coordinator confirms that it
   stopped, then invokes private `task-rearm --confirmed-stopped` with the exact
   observed start lease. That compare-and-swap accepts only expired assigned
   state at the exact clean locked base and returns a fresh lease. Any stale or
   conflicting lease fails closed; after an interrupted response, the
   coordinator re-observes `task-watch` and continues with its current active
   lease. An active deadline or prestart mutation also fails closed. The
   replacement uses that fresh lease with normal `task-start` as its first
   transition. A rearm mismatch is `WORKER_START_LEASE_CONFLICT`; the stopped
   worker's old lease is rejected by `task-start` as
   `WORKER_START_LEASE_INVALID` and cannot start the task. If a running worker
   stops,
   a fresh replacement invokes private `task-recover --confirmed-stopped` from
   the assigned scope cwd; that transition transfers only declared dirty state
   or one direct-child commit. The coordinator must not invoke running-worker
   recovery on the worker's behalf because session ownership is bound to the
   caller. Session hashes are append-only history: a
   recovered-away or completed identity can never be reused.
8. The coordinator independently verifies a clean branch, exactly one
   direct-child task commit, exact changed paths within claims, and complete
   validation/review evidence. Invoke private `batch-advance` after every task
   in the active batch commits; it alone creates the next batch's assignments.
   From dispatch onward, invoke private `task-watch` every 30 seconds. Interrupt
   a worker immediately on `WORKER_PRESTART_TIMEOUT`,
   `WORKER_PRESTART_MUTATION`, `WORKER_STALLED`,
   `WORKER_READ_ONLY_TIMEOUT`, `WORKER_SCOPE_VIOLATION`, or `WORKER_TIMEOUT`;
   confirm it stopped before recovery. Never wait silently or blind-retry a
   no-progress worker.
   On the profile-specific `READ_ONLY_DEADLINE_NEAR`, require an immediate
   claimed-file edit or blocker instead of waiting for the hard cutoff.
9. Invoke private `wave-integrate`. Merge task branches into the integration
   branch in stable task-ID order with `git merge --no-ff --no-edit`. Never
   cherry-pick, rebase, squash, push, or merge workers directly into the
   project branch.
10. The coordinator updates only shared managed specs/docs/changelog and makes
   one final integration commit only for a non-empty diff. Product-code fixes
   become a new isolated correction task.
11. Run combined validation and integration `code-review`; reconcile queued
   steering. Once that evidence is bound to the unchanged integration tip,
    unlock and remove every clean worker with
    `git worktree remove <exact-worker-path>`, then delete each worker ref with
    `git update-ref -d <ref> <exact-worker-tip>`. Dirty,
    advanced, or unverifiable workers block promotion and remain intact.
12. Invoke private `wave-promote` only after worker cleanup succeeds and the
    persistent lane remains clean on its recorded branch at its recorded base.
    A common-Git-directory lock covers identity precheck,
    `git merge --ff-only <verified-integration-SHA>`, and postcheck. Mark tasks
    done only after promotion. Then `wave-cleanup` removes the integration
    worktree first with `git worktree remove <exact-integration-path>` and its
    branch second with an exact expected-old SHA. Never
    run broad prune or gc. Internal branches are never pushed or published.

## Idempotency

- Every Git mutation is journaled before execution and re-observed afterward.
- Repeated `run` resumes recorded v6 execution state; it does not recreate assignments,
  branches, worktrees, commits, merges, or revisions.
- Immutable assignment retries must be byte-equivalent. Coordinator state owns
  mutable task/wave transitions.
- Reuse only a verified v3 project-agent-instructions state whose owner receipt,
  full-file spec receipt, effective config, evidence, and target still match.
  Any drift requires a fresh decision at a safe wave boundary before dispatch.
- If promotion reports failure, classify observed project `HEAD` as unchanged,
  promoted, or unexpectedly moved before any retry.
- Cleanup failure retains an exact inventory and never rolls back promotion.
- A lane generation spans every wave and final `$align`. While active it blocks
  lane integration and removal. It releases only from the clean lane at the
  final promoted head with every internal resource absent. Released immutable
  receipts and repository claims remain pending until `$task-implementer
  integrate` consumes their contiguous range. Missing or malformed
  coordination state fails closed.
- Execution-plane-v1 and coordinator-v1/v2/v3/v4/v5/v6 runs are unsupported and return
  `WORKFLOW_UPGRADE_REQUIRED`, including completed records. Do not add a legacy
  read path, execution shim, or migration path.

## Failure Handling

- Worker, merge, validation, review, steering, sandbox, or promotion failure
  leaves the persistent lane at its last proven head and retains exact recovery
  resources. Lane integration failure leaves the source ref unchanged.
- Use `REPLAN_REQUIRED` for undeclared paths/domains;
  `UNSUPPORTED_SUBMODULE_SCOPE` for claims crossing gitlinks;
  `UNSUPPORTED_SYMLINK_SCOPE` for claims crossing tracked symlinks;
  `WORKTREE_COLLISION` or `WORKTREE_CONFLICT` for identity/drift;
  `INTEGRATION_CONFLICT` or `INTEGRATION_VALIDATION_FAILED` before promotion;
  `PROMOTION_BLOCKED` or `PROMOTION_FAILED` for local promotion failure;
  `CLEANUP_BLOCKED` for retained unsafe resources; and
  `ENVIRONMENT_BLOCKER` for missing safe bootstrap state or access.
- Native subagents inherit the parent sandbox but are not intrinsically bound
  to a worktree. Verify absolute cwd and Git identity at every transition;
  never widen permissions to make a worker pass.
- Preflight private-root and shared Git-common-directory access. Linked
  worktrees share objects, refs, config, and hooks despite separate indexes.
- An unfinished pre-interop run in a persistent lane returns
  `WORKFLOW_UPGRADE_REQUIRED`; do not infer, migrate, or adopt its resources.
  There is no TTL, PID-based recovery, force-clear, or compatibility path for
  generation leases.
- Treat the immutable generation record as the terminal run receipt. Repair a
  missed local interop write only when its lane, incarnation, generation,
  token, promoted SHA, clean checkout, and absent resources match exactly;
  otherwise stop.

## Must Not

- Do not expose internal IDs or transitions in user commands.
- Do not let the coordinator implement worker tasks.
- Do not run parallel tasks with overlapping claims, domains, shared specs,
  dependency files, migrations, infrastructure identities, or architecture
  assumptions.
- Do not let workers touch the primary checkout, other refs/worktrees, shared
  handoff/spec/docs, Git maintenance, external systems, or undeclared paths.
- Do not push or publish internal task branches or the lane branch, target a
  remote default from lane mode, call public `$worktree` lifecycle actions for
  a lane, or integrate/remove while a generation is active.
- Do not force-remove worktrees, force-delete branches, copy local state,
  initialize submodules, cherry-pick, rebase, squash, push, open a PR, publish,
  or perform live external writes without separate authorization.

## Completion Criteria

- The public interface remains exactly four explicit-only actions.
- Hook-routed direct turns are an internal continuation of an explicit session
  binding, not an additional public action or implicit skill selection.
- Every task belongs to a deterministic wave and has locked ownership.
- Every promoted task has one verified worker commit, ordered merge evidence,
  combined validation/review evidence, and handoff status updated after
  promotion.
- Internal waves advance only the persistent lane by verified fast-forward
  promotion. Source integration advances only the recorded source ref through
  its exact validated two-parent candidate.
- Successful cleanup leaves no managed temporary refs or worktrees; retained
  resources are reported exactly.
- Final changed-surface `$align` passes and the private finalizer seals the lane
  generation, or the run stops with a precise blocker. A successful
  `integrate` consumes every pending generation and rearms the same lane.

## Output Contract

Return the project scope, prompt status, current wave/task outcome, validation
and review status, promotion result, retained recovery inventory, and minimum
next action. Never print prompt bodies, secrets, private internal IDs, or raw
logs.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.
