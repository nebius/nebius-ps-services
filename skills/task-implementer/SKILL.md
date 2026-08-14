---
name: task-implementer
description: "Requires explicit invocation to initialize or reopen a persistent per-project Git worktree workspace, run durable brownfield implementations through dependency waves, integrate pending generations, or remove an idle lane. Bound-session direct prompts may be captured into its canonical prompt as a non-blocking sidecar but never invoke this workflow. Do not use for ordinary one-shot implementation, Agentic SDLC, standalone Git workflows, or generic parallel-agent requests."
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
`policy.allow_implicit_invocation: false` in `agents/openai.yaml`. A
`prompt-session-intake` receipt may authorize only a prompt-only capture merge;
it never selects or invokes a public Task Implementer action. That internal
path accepts only an agent-selected durable project-intent projection, rehashes
it against the accepted digest, and binds the digest to the operation marker.
Workflow/skill, shell/tool, delivery, agent-control, status, conversation, and
unrelated wrappers do not enter the prompt; commands remain eligible when they
define a project contract or example. Capture failures never block the direct
prompt, exact duplicates never append, and distinct prompt drift never
auto-rebases.

## Public Interface

Expose exactly these five actions:

```text
$task-implementer workspace init [project-folder]
$task-implementer workspace reuse [project-folder]
$task-implementer run <prompt-ref-or-file>
$task-implementer integrate [project-folder]
$task-implementer workspace remove [project-folder]
```

- `workspace init` defaults to the exact current directory.
- `workspace reuse` defaults to the exact current directory and reopens only
  the existing verified workspace without refreshing or repairing its lane.
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

- The user explicitly invokes one of the five actions.
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
  from the committed lane baseline, and is never copied or mutated. On the
  resource-free first open of a run, Task Implementer reviews and checkpoints
  coherent whole-repository lane dirt itself; a clean lane creates no commit.
  An active or resumed generation must remain clean. Integration requires both
  source checkout and lane to be completely clean. If the primary source is dirty at
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
  before locking the coordinator contract. Use the run-owned paths exactly as
  emitted. The hidden lifecycle adapter authenticates inspect and render
  against the active integration checkout during outer lifecycle
  reconciliation: the original wave base for first preparation, the exact
  recorded contract commit for an attested retained correction, or the sealed
  integrated head while the coordinator has only its owned spec/docs/changelog
  reconciliation diff at `promotion_pending`. The checkout must always be the
  exact registered linked worktree sharing the lane's common Git directory. It
  also authenticates an exact delegated commit against its
  active worker checkout and command-derived worker session when the hook is
  selected on the outer lane; apply and verify stay on the ordinary terminal
  seal path.
  Never copy run evidence into a lifecycle session bundle or ask the user to
  reconcile private IDs.
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
│           ├── resume-control.json
│           ├── plan-digest-recovery.json
│           ├── contract-delta-adoption.json
│           ├── pending-plans/wave-001/<tasks-sha256>.json
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

### `workspace reuse [project-folder]`

1. Resolve the exact workspace identity from the current source ref and project
   scope. Require the caller's Git root to be exactly the recorded primary
   checkout or owning lane, plus an existing safe workspace-v2 manifest and
   generated VS Code workspace; never scan other branches or create private
   state.
2. Verify the Worktree-owned live lane anchor, including its lane ID,
   incarnation, branch, worktree, scope, existing source ref, common Git
   directory, and primary checkout. Dirty, active, pending, integrating, conflicted, or
   source-promoted live lanes may reopen; creating, recovery, removing,
   removed, unsafe, or mismatched state fails closed.
3. Ask VS Code to reuse its last active window for the existing workspace.
   Editor failure is a non-fatal warning. Do not refresh, repair, checkpoint,
   migrate, queue, run, integrate, remove, or claim that the lane is ready to
   execute.
4. Return only public-safe workspace and lane paths plus observed lane state.
   `run <prompt-ref-or-file>` remains valid directly from the primary source
   project and does not require this action first.

### `run <prompt-ref-or-file>`

1. Invoke private `intake`; validate prompt-v3, acquire the scope lock, and
   snapshot the accepted intent once. For an existing coordinator-v7 run,
   intake performs a read-only authoritative resume audit, adopts a validated
   journal-less v7 stable boundary when needed, reconciles human handoff
   projection, and returns exactly one `execute`, `wait`,
   `requires_confirmation`, `blocked`, or `complete` outcome. `execute`
   carries one digest-bound private token, canonical argument object, and exact
   next transition; pass both unchanged to the returned coordinator transition.
   Every successful controlled transition returns the next authoritative
   `resume` plan; consume that object directly instead of reconstructing or
   guessing the following command. Before the first controlled mutation of a
   new run, the helper adopts resume control atomically under both execution
   locks, so the same response pipeline applies from the first checkpoint.
   Terminal finalization first requires fresh `$align` evidence, then private
   `run-resume --alignment <summary>` mints the token bound to that exact
   bounded single-line evidence. Confirmed worker/resource recovery records its stop authority and
   mints the private token atomically before changing state. If another prompt owns active work,
   persist this explicit run request in the private FIFO queue and stop this
   invocation with its queue position. Saving or creating a prompt never
   queues it. Re-running an edited queued prompt updates its accepted snapshot
   in place; an unaccepted queue-head edit blocks activation. If the only
   workspace validation failure is a removed resolved Python executable in the
   generated VS Code launcher, `run` may invoke canonical workspace
   initialization for the same exact scope to refresh that generated file,
   then must repeat full verification before intake. Do not apply this repair
   to any other mismatch, and do not add it to non-mutating `workspace reuse`.
2. For a new or safely reconcilable run, apply
   `references/prompt-requirements-refinement.md`: inspect discoverable facts,
   extract the Ask and optional headings into outcome, context, constraints,
   acceptance, verification, non-goals, and related managed requirement
   fields. Persist stable clarification IDs privately. Ask only for material
   ambiguity and block contract lock while such a question is open or
   reopened. Render and validate the managed requirements/design regions,
   write a complete private prompt-impact claim, and require private
   refinement verification to obtain the shared owner's immutable impact
   receipt. Only then may `wave-plan` bind the coordinator plan basis to the
   latest settled revision and exact current canonical specs
   before acquiring resources. Impact publication is per-run locked and
   compare-checks the ledger; a conflicting crash orphan is preserved and
   skipped. Material impact requires a distinct replanned plan identity rather
   than rebinding unchanged plan bytes. Normalize task IDs, dependencies, write claims, conflict domains,
   validation, done criteria, and prompt/repository constraints in stable task
   order. Repeat every applicable constraint in each task's stop context. Do
   not decide project instructions until the managed requirements and design
   records have both been rendered and validated.
3. Combine overlapping work before IDs lock when it is one coherent result.
   Otherwise add an explicit dependency. Invoke private `checkpoint-prepare`
   to reserve the exact candidate tree, path digest, and initial claims without
   mutating the real index or history. Fail on cycles or malformed ownership.
   For a resource-free dirty lane, inspect the complete status and diff for
   that reserved candidate. Resolve every changed path to
   its applicable repository instruction chain and first-class project owner;
   read every affected project's instructions, verify the changes are related
   to the accepted run, and block secrets, private endpoints, unsupported
   special paths, or unresolved cross-project ownership. The private helper is
   transactional enforcement, not a substitute for that review. Only after
   review, invoke private `wave-plan`; it consumes the exact preparation token,
   candidate tree, path digest, and claims while atomically opening the next
   monotonic generation. Let the Worktree-owned transaction run repo-root
   `git add -A`
   with normal hooks and the fixed checkpoint message
   `chore(task-implementer): checkpoint managed lane`. A dirty candidate must
   become one exact clean direct-child commit; a clean candidate retains
   `HEAD`. Record the private lane-checkpoint receipt and use its resulting
   post-checkpoint `HEAD` as the lane, lease, interop, coordinator, and
   first-wave baseline.
   Hook-modified commits rotate the preparation token, block for review, and
   require a fresh private `checkpoint-prepare` followed by `wave-plan` after
   that review; a blind retry cannot adopt them. Never reset, stash, clean,
   amend, unstage, or manually commit the lane. Reconcile the generation's
   exact lane ID, incarnation, generation, lease token, run, promoted head,
   release state, clean checkout, and live Git identity on every resume; never
   trust local interop flags alone. Register repository-wide exact/prefix and
   conflict-domain claims before worker creation. Claims overlapping any other
   live lane block the run and remain held through integration. Replanning
   extends the active generation's claims before replacement state is written;
   earlier claims remain as a conservative superset. The active generation
   blocks lane integration and removal, and any dirt after open remains a
   conflict rather than another checkpoint.
4. Coordinate every recorded wave through the lifecycle below. A logical wave
   may dispatch in capacity-sized batches, but batching never changes its wave.
5. Reconcile queued steering only at a safe wave boundary. Contradictory
   steering preserves work and stops before promotion. Before preparation,
   private `wave-replan` may replace the resource-free planned tail. Resume
   compares that immutable tail with the live pending task contract and routes
   any pre-resource drift through the replan before preparation. After the
   final promoted wave is cleaned, it may also append a newly discovered
   isolated correction tail before finalization. When combined review blocks a
   `promotion_pending` wave, the same action appends only the currently ready
   correction frontier to that retained integration. Dependent frontiers repeat
   the transition after the prior frontier merges. The lane stays at the wave's
   original promotion base, prior worker assignments and commits remain
   immutable, and the coordinator first stages the ready correction tasks as
   one immutable `pending-plan-v1` artifact and consumes only those revalidated
   bytes. A resource-free future task may change only its dependencies once the
   correction target is indexed. The corrected integration must repeat combined
   validation and review before the single promotion. The rendered project
   contract may carry forward only to a descendant receipt that is otherwise
   byte-identical and whose specification, traceability, project identity, and
   instruction bytes revalidate exactly. The active coordinator
   index keeps only completed waves plus the replacement schedule;
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
   another prompt still owns the scope. Direct-prompt capture never supplies a
   run transition or terminal workflow fact.

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
   If review returns `REQUEST CHANGES`, canonicalize the blocking findings,
   bind their SHA-256 to the exact candidate, and invoke the private review-
   rejection transition. That transition archives the candidate behind an
   expected-old internal ref, removes only its clean temporary worktree and
   branch, releases the integration reservation, leaves the source unchanged,
   and returns the lane to `pending`. The same explicit `integrate` invocation
   then appends a correction generation and repeats this workflow; do not make
   the user manually repair private state or invoke another public action.
   Because review rejection never advances or stages the source checkout, it
   may preserve unrelated primary dirt; candidate creation and promotion still
   require a completely clean source.
   Correction tasks are serial whenever they touch one shared invariant or
   their semantic independence is uncertain.
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
- file-path disjointness alone never proves semantic independence: tasks that
  read, establish, recover, or validate one invariant must share a conflict
  domain or an explicit dependency;
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
   v7.
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
   exact `start_context` returned by `task-arm` or `task-rearm`, without inherited coordinator
   transcript or unrelated conversation context.
   Invoke private `task-arm` only when a real worker slot is available, then
   spawn that worker immediately. Queued assignments remain unarmed and do not
   consume a start budget. The worker reads the assignment and makes
   `task-start` its first private transition after verifying immediate Git/cwd
   identity. It invokes the assignment's exact embedded helper/workspace paths
   and passes the embedded digest plus the exact returned `start_lease` unchanged.
   Use `start_context.scope_cwd` and `start_context.start_argv` verbatim; never
   transcribe or reconstruct visually similar private worktree paths.
   When a separately spawned worker cannot retain the managed worktree across
   the launcher process boundary, use the hidden atomic sequential-worker form
   on `task-arm` or `task-rearm`. It starts one fresh ephemeral `codex exec`
   child before the helper releases its resource lifetime, supplies only the
   immutable start context, assignment, and incoming handoff, and waits for
   that child. The fallback pins medium reasoning effort, and a recovery-only
   child pins low effort, so one model turn remains comfortably inside the hard
   heartbeat-staleness contract. Release
   the completed resume transition lock before the child
   starts so its first `task-start` can acquire the task scope normally. Never
   recreate the worktree or transfer the task to the coordinator. A zero child
   exit is accepted only when the assignment's exact immutable result file now
   exists; start or recovery success alone is incomplete.
   `task-start` performs authoritative canonical digest and exact lease checks,
   so the worker never invents JSON serialization or reuses another launch. It
   reads the incoming handoff and performs deeper preflight only after that
   transition. An armed worker must reach `task-start` within 60 seconds.
   After one failure, stop dispatching new batch members while
   active workers finish.
7. Every worker first verifies its real worktree root, branch, base SHA, and
   absolute cwd, then invokes `task-start` with the embedded helper/workspace
   paths, embedded digest, and exact returned `start_lease` unchanged. The
   helper verifies the canonical assignment digest and exact current lease and
   returns private authorization and claim paths for one direct-child `$commit`,
   plus one transient canonical `commit_context` bound to that assignment,
   running task plane, worker session, branch, and base SHA. Use its exact
   `prepare_argv` from its `lifecycle_cwd`; use the returned raw `session_id`
   for every later execute or review transition. Never pass the
   `worker_session_fingerprint_sha256` as `--session-id`: that value is
   persisted ownership evidence, not a command input. The context's exact
   PATH-canonical `python3` or `python3.N` remains subject to helper digest,
   path, action, evidence, repository, and session validation. The helper also
   returns an exact transient `result_context`; publish only to its
   `result_path` while using its private `publication_cwd` as the explicit
   external working directory, never the selected project cwd. Write the
   unsigned final JSON to its exact `draft_path`, then invoke its exact
   `publish_argv`; the helper computes the canonical digest and atomically
   publishes the immutable result, with `changed_paths` canonicalized as a
   sorted unique set. The only result statuses are exact lower-case
   `committed` for successful one-commit work and `REPLAN_REQUIRED` for a
   terminal safe stop. Never hand-compute or patch `result_sha256`, and never
   replay publication merely because post-publication output rendering failed;
   inspect the immutable result first.
   A truthful `REPLAN_REQUIRED` result is terminal evidence, not a missing
   result. Resume replays and accepts it idempotently. If that result proves an
   exact clean no-op (`commit == base_commit`, no changed paths, clean worker
   at the same base), a correction plan may retain the immutable task as
   `superseded`, clean only its exact resource, and append a replacement task.
   An exact tracked dirty `REPLAN_REQUIRED` result at the base may use the same
   path only after Task archives its tree behind a compare-and-set internal ref,
   proves the archive parent, exact tree bytes, and changed paths on replay,
   restores only those tracked paths, and retains the ref through correction
   promotion. Untracked, committed, mismatched, or unverifiable dirt remains
   blocked. Delete the archive ref only during successful wave cleanup.
   It then
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
   the exact `worker_context.scope_cwd` returned by `run-resume`, using its
   `recover_argv` verbatim; that transition transfers only declared dirty state
   or one direct-child commit. The coordinator must not invoke running-worker
   recovery on the worker's behalf because session ownership is bound to the
   caller. If that recovery cwd exists only during the observing helper's
   resource lifetime, use the hidden atomic recovery-worker form on
   `run-resume`; it launches a fresh ephemeral child before returning and makes
   the exact `recover_argv` its first transition. Session hashes are append-only history: a
   Recovery of dirty paths outside immutable claims is reporting-only: return
   `replan_required` with the exact violating names, no commit authorization,
   and let the fresh worker publish terminal `REPLAN_REQUIRED` evidence without
   another edit. Never silently discard or adopt those bytes.
   recovered-away or completed identity can never be reused.
   If a running-wave integration or worker directory, or a promotion-pending
   integration directory, is missing while its lease remains `present`, keep
   the stopped run and retained Git administration intact. From the owning
   scope, invoke private `wave-resource-recover --confirmed-stopped` before
   replacement-worker transfer or promotion validation. It may rehydrate only
   an exact locked registration with matching lease, branch, head,
   administrative HEAD, and clean index. Running integration is bound to the
   contract commit; promotion-pending integration is bound to the recorded
   integrated head and requires every retained task to be `merged` or safely
   `superseded`. Treat filesystem-only worker edits as lost and reported,
   never reconstructed. Staged state, symlink collisions, drift, or ambiguous
   registration stays blocked and retained.
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
   Resume must route a hard worker-guard outcome directly to confirmed
   `task-recover` even when the last heartbeat is still fresh; a heartbeat
   never overrides a read-only, scope, stall, or total-time stop condition.
   On the profile-specific `READ_ONLY_DEADLINE_NEAR`, require an immediate
   claimed-file edit or blocker instead of waiting for the hard cutoff.
   Watch output includes the bounded changed-path set and exact paths outside
   claims; use those names to decide replan versus retained unrelated mutation,
   never copy file contents through the coordinator.
9. Invoke private `wave-integrate`. Merge task branches into the integration
   branch in stable task-ID order with `git merge --no-ff --no-edit`. Never
   cherry-pick, rebase, squash, push, or merge workers directly into the
   project branch.
10. The coordinator updates only shared managed specs/docs/changelog and invokes
   private `coordinator-commit` once for a non-empty diff. The helper journals,
   stages, and creates exactly one claim-bound direct-child integration commit;
   replay only reuses that exact clean commit. Product-code fixes become a new
   isolated correction task.
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
- Repeated `run` resumes authoritative coordinator-v7 execution state; it does
  not recreate assignments, branches, worktrees, commits, merges, promotions,
  generations, queue activations, or revisions.
- Private `resume-control-v1` records one monotonic transition intent, its
  observed-state digest, canonical arguments plus their digest, phase, and
  terminal digest. An interrupted intent replays only those stored arguments;
  an effect-observed or state/projection-committed phase finishes validation
  and projection without rerunning the mutation. A
  second coordinator transition cannot begin until the first is reconciled.
  One private execution lock and the owning reentrant scope lock span token
  verification, the selected transition, and state/projection reconciliation;
  each mutation still acquires its narrower Git/Worktree locks. A stale token
  returns `RESUME_STALE` before any effect.
- A journal-less coordinator-v7 run is adopted only at a completely validated
  stable boundary. Unresolved journals, malformed planes, dirty or divergent
  Git state, missing ownership proof, or ambiguous leases stay retained and
  blocked. Coordinator-v1 through v6 remain unsupported.
- The coordinator plan digest covers the complete ordered indexed wave plan,
  including retained completed waves and every replacement correction wave.
  A private current-v7 plan-digest recovery is permitted only for the proven
  replacement-tail writer signature, exact old and combined digests, one done
  retained prefix, deterministic replacement IDs, matching wave/task planes,
  and no assigned or running replacement worker. When canonical spec receipt
  bytes advanced, the owner must first refresh the ready refinement's compiled
  managed-requirements digest. Recovery then republishes the validated impact,
  accepts only `retain_plan`, and binds its digest in the fixed recovery
  identity and prompt-impact plan basis before the coordinator write. It never
  repairs a stale refinement or admits material impact. Either interrupted
  write remains safely repeatable without changing task, worker, Git,
  Worktree, lease, promotion, or public workflow state.
- A sealed selected-lane requirements/design reconciliation may be adopted only
  through the private current-v7 contract-delta owner. It accepts exactly the
  canonical spec pair plus an optional provenance-owned `AGENTS.md`, binds the
  sealed lifecycle and instruction-state digests, and creates one coordinator-
  owned integration commit. Lane dirt is admissible only while those exact lane
  bytes remain unchanged and the adoption commit remains in integration
  ancestry. The one claim-bound final coordinator reconciliation may replace
  adopted requirements or design bytes at the sealed promotion tip. Promotion
  journals temporary lane cleanup and restores the original adopted overlay
  from its immutable commit if fast-forward does not complete; arbitrary,
  staged, untracked, partial, or unsealed dirt remains blocked.
  Before deleting the promoted integration, cleanup refreshes the ready
  refinement and prompt-impact plan only when that exact coordinator commit
  explains the canonical spec advance and the remaining plan is retained. A
  retained `promoted` journal cannot block later-wave recovery or completion:
  it is historical only when its recorded wave is done, its promoted head is
  exact, and that head is an ancestor of the later wave base. Any mismatch
  remains fail-closed. If interruption occurs after the fast-forward but before
  wave publication, replay accepts stale pre-promotion spec impact only when
  the clean lane exactly equals the sealed integration target; cleanup retains
  the strict final coordinator-commit reconciliation gate.
- The final implementation promotion retains its exact integration resource
  until the selected-project lifecycle is applied, verified, and sealed. A
  private terminal-lifecycle receipt binds the sealed lifecycle and instruction
  state, final-wave base, first promoted head, and complete canonical
  requirements/design/instructions bytes. A clean seal creates no commit. An
  exact tracked canonical spec or provenance-owned `AGENTS.md` overlay is
  committed in the retained integration, temporarily cleared from the lane
  under a journal, and fast-forwarded as a second promotion before cleanup.
  Missing, stale, staged, untracked, deleted, partial, unrelated, or divergent
  state blocks final cleanup. Finalization revalidates the promoted receipt; it
  never releases first and relies on a later Stop mutation to dirty the
  immutable generation.
- An already-released historical run without that receipt may recover only
  through a supplemental Worktree-owned generation. The private owner validates
  the exact sealed lifecycle, canonical tracked overlay, original released
  generation/head, absent resources, and ancestry; then the reviewed built-in
  checkpoint opens and immediately releases the next generation. The original
  receipt and task evidence remain immutable, and public `integrate` consumes
  the contiguous original-plus-supplemental range. Never relax cleanliness,
  amend the released generation, or reconstruct removed task resources.
- Resume planning never repairs local interop while reading external lease
  truth. Handoff is planning input only before coordinator creation; afterward
  it is a compare-and-swap human projection of machine state.
- Project planned tasks as `pending`; assigned, running, committed, or merged
  tasks as `in_progress`; failed tasks as `blocked`; and tasks in promoted,
  cleanup, or done waves as `done`. The hidden current-v7 projection recovery
  accepts only caller-digest-bound historical `committed` fields that match an
  idle resume controller and exact machine planes, journals the postimage, and
  never changes machine state or admits `committed` as a handoff status.
- Unindexed correction tasks are copied once into a digest-addressed immutable
  `pending-plan-v1` artifact bound to the active resume epoch and token before
  coordinator, wave, or task-plane mutation. Interrupted publication reuses
  those exact staged bytes even if the editable handoff later changes; it never
  mixes live Markdown with a partially published correction plan.
- Immutable assignment retries must be byte-equivalent. Coordinator state owns
  mutable task/wave transitions.
- Canonical worker guardrails must match current source through the first
  successful `task-start`. After that transition binds the exact current-v7
  assignment digest and worker identity in the task plane, source-only
  guardrail wording growth does not invalidate the accepted immutable bytes;
  normal resume still revalidates every other identity and returns current
  transient recovery, commit, and result-publication contexts. Active
  assignments require the exact current helper. For committed, merged, or
  superseded assignments, the recorded helper path remains digest-bound
  historical evidence across source/install relocation and is never executed
  by terminal observation. Never rewrite the assignment, admit unstarted drift,
  or accept an older assignment schema.
- Reuse only a verified v3 project-agent-instructions state whose owner receipt,
  full-file spec receipt, effective config, evidence, and target still match.
  Any drift requires a fresh decision at a safe wave boundary before dispatch.
- If promotion reports failure, classify observed project `HEAD` as unchanged,
  promoted, or unexpectedly moved before any retry.
- Cleanup failure retains an exact inventory and never rolls back promotion.
- A lane generation spans every wave, the terminal selected-project lifecycle
  seal, and final `$align`. While active it blocks lane integration and removal.
  It releases only from the clean lane at the terminal-sealed final promoted
  head with every internal resource absent. Released immutable receipts and
  repository claims remain pending until `$task-implementer integrate`
  consumes their contiguous range. Missing or malformed coordination state
  fails closed.
- Execution-plane-v1 and coordinator-v1/v2/v3/v4/v5/v6 runs are unsupported and return
  `WORKFLOW_UPGRADE_REQUIRED`, including completed records. Do not add a legacy
  read path, execution shim, or migration path.

## Failure Handling

- Worker, merge, validation, review, steering, sandbox, or promotion failure
  leaves the persistent lane at its last proven head and retains exact recovery
  resources. Lane integration failure leaves the source ref unchanged.
- A Stop-generated project-contract reconciliation does not require a new user
  prompt: the shared lifecycle owner must atomically enter
  `reconciliation-required` before the continuation. If installed runtime state
  remains `implementation-open`, preserve the run and report a source/install/
  runtime mismatch; never loosen spec gates or forge the transition.
- A fresh worker heartbeat returns `wait`. An expired prestart or stale running
  worker returns `requires_confirmation`; timestamps, PIDs, missing paths, or
  Markdown alone never prove that the prior owner stopped. Unknown writer,
  unexpected head, malformed state, unresolved journal, or dirty ambiguity
  returns `blocked` with retained resources.
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
- Do not checkpoint a path whose applicable project instructions, ownership,
  relationship to the accepted run, or sensitive-content safety has not been
  reviewed. Never persist diff bodies or file contents in checkpoint state.

## Completion Criteria

- The public interface remains exactly five explicit-only actions.
- Hook-captured direct turns may merge into the canonical prompt but are not an
  additional public action, implicit skill selection, or workflow execution.
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
