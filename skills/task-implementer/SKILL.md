---
name: task-implementer
description: "Requires explicit invocation to initialize a private prompt workspace or run one durable brownfield implementation through dependency waves, internal full-repository Git worktrees, worker review/commit evidence, ordered integration, and fast-forward promotion, including safe nesting under a worktree-managed outer branch. Do not use for ordinary one-shot implementation, Agentic SDLC, standalone Git workflows, or generic parallel-agent requests."
---

# Task Implementer

## Purpose

Coordinate a complex brownfield request from one durable private Markdown
prompt. One `run` plans every task, groups compatible work into dependency
waves, dispatches isolated workers, integrates verified commits, promotes each
wave atomically, and continues until the run is done or blocked.

## Invocation Policy

Requires explicit invocation. Keep `policy.allow_implicit_invocation: false`
in `agents/openai.yaml`.

## Public Interface

Expose exactly these two actions:

```text
$task-implementer workspace init [project-folder]
$task-implementer run <prompt-path-or-unique-filename>
```

- `workspace init` defaults to the exact current directory.
- `run` accepts one managed absolute path or one unique managed filename.
- Never require a prompt ID, run ID, wave ID, task ID, branch, worktree, or
  internal transition.
- Steering means editing the same prompt and repeating `run`; do not expose a
  separate action.
- Do not add public `parallel`, `merge`, `cleanup`, `upgrade`, or compatibility
  aliases.

## When To Use

- The user explicitly invokes one of the two actions.
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
- The canonical project checkout, current named branch, repo instructions, and
  immutable prompt snapshot.
- When the checkout is owned by `worktree`, its exact managed outer identity,
  recorded scope, and current `HEAD`; that outer branch is the sole promotion
  target for the full task run.
- Complete task dependencies, exact or directory-prefix write claims, keyed
  conflict domains, validation, and done criteria.

## Required Reads

- Read `references/prompt-workspace.md` before workspace, routing, steering,
  state recovery, or sandbox decisions.
- Read `references/implementation-loop.md` before decomposition, wave planning,
  worker dispatch, integration, promotion, or cleanup.
- Read the run manifest, exact bound snapshot, steering ledger, full handoff,
  coordinator state, active wave, assignments, results, and journals.
- Inspect relevant `AGENTS.md`, code, tests, README/design docs, changelog, Git
  state, managed requirements, and managed design records.
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
│   ├── prompts/*.md
│   └── runs/<run>/
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
└── worktrees/<project>/<scope>/<run>/wave-001/
    ├── integration/
    ├── task-1/
    └── task-2/
```

Each linked worktree contains the full repository. For scope
`services/example`, a worker operates from
`<task-worktree>/services/example`. Never copy ignored/untracked files,
dotenv files, credentials, caches, or primary-checkout local state.

`handoff.md`, managed requirements/design records, shared README/design docs,
and changelog evidence are coordinator-owned. Worker assignments are immutable;
workers write only their locked product scope and private result record.

## Process

### `workspace init [project-folder]`

1. Resolve the installed skill and canonical Git root/exact project scope.
2. Invoke private `init`. Create or verify the private workspace and exactly
   one starter prompt only when the prompt directory is empty.
3. Open the generated VS Code workspace when available; editor failure does not
   invalidate initialization.
4. Return workspace paths and prompt metadata. Stop without queuing work.

### `run <prompt-path-or-unique-filename>`

1. Invoke private `intake`; validate the managed prompt, acquire the scope
   lock, select one run, and snapshot accepted steering once.
2. For a new or safely reconcilable run, inspect source and normalize stable
   requirements, task IDs, dependencies, write claims, conflict domains,
   validation, and done criteria in stable task order.
3. Combine overlapping work before IDs lock when it is one coherent result.
   Otherwise add an explicit dependency. Invoke private `wave-plan` and fail on
   cycles or malformed ownership. If the project checkout is a `worktree`
   managed linked worktree, acquire its private v2 lease with owner kind
   `task-implementer` at the exact current
   outer `HEAD` before creating task resources. Reject any worker or
   coordinator write claim that escapes the managed task scope; never clip it.
4. Coordinate every recorded wave through the lifecycle below. A logical wave
   may dispatch in capacity-sized batches, but batching never changes its wave.
5. Reconcile queued steering only at a safe wave boundary. Contradictory
   steering preserves work and stops before promotion. Before preparation,
   private `wave-replan` may replace a resource-free planned wave; it never
   replaces a blocked wave.
6. Continue with the next wave from the newly promoted project `HEAD`. After
   the last cleanup, run final changed-surface `$align`, then invoke private
   `run-finalize` with its concise evidence. Only that transition marks the
   handoff done and releases a managed outer lease. An unchanged completed
   prompt returns `ALREADY_COMPLETE`; an interrupted lease release returns a
   private finalization-pending outcome and repeats the same final transition.
   Edited completed content starts a new internal run only after release.

## Dependency-Wave Contract

Build deterministic earliest-fit waves in stable task order:

- dependencies must be in earlier completed waves;
- write claims must be pairwise disjoint;
- conflict-domain keys must be pairwise disjoint;
- unknown or incomplete ownership forces a singleton wave;
- external database, Kubernetes, Terraform, migration execution, and
  publication actions are singleton domains and still need explicit authority;
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

1. Require a clean named project branch and record its exact `HEAD`.
2. Register resource intent in the managed outer lease when present, journal
   intent, create and lock an integration worktree/branch from that exact
   commit, then re-observe Git state.
3. Preallocate coordinator-owned requirement/design records. Commit the locked
   contract only if tracked coordinator files changed; that commit is every
   worker base.
4. Invoke private `wave-dispatch`. It creates a unique validated branch and
   locked full-repository worktree only for the active capacity batch, plus an
   immutable assignment with absolute scope cwd, base, digest, claims, domains,
   validation, criteria, and a digest-bound private incoming handoff. The
   handoff contains accepted evidence from all earlier completed waves and
   batches; only the first batch of the first wave has no predecessors.
5. Reserve the main thread for coordination. Dispatch native worker agents up
   to available capacity. If unavailable, use fresh sequential `codex exec`
   workers in the same isolated worktrees; the coordinator never implements a worker task.
   After one failure, stop dispatching new batch members while
   active workers finish.
6. Every worker verifies its real worktree root, branch, base SHA, assignment
   digest, incoming-handoff digest, absolute cwd, and claims; starts from a
   worker session never used by another task in the run; implements one task; validates; runs
   `code-review`; fixes scoped findings; creates exactly one direct-child
   commit through `$commit`; and writes one private result. It never edits the
   shared handoff or manages worktrees/refs.
   If a running worker stops, private `task-recover` requires explicit stopped
   confirmation and transfers only declared dirty state or one direct-child
   commit to a fresh session. Session hashes are append-only history: a
   recovered-away or completed identity can never be reused.
7. The coordinator independently verifies a clean branch, exactly one
   direct-child task commit, exact changed paths within claims, and complete
   validation/review evidence. Invoke private `batch-advance` after every task
   in the active batch commits; it alone creates the next batch's assignments.
8. Invoke private `wave-integrate`. Merge task branches into the integration
   branch in stable task-ID order with `git merge --no-ff --no-edit`. Never
   cherry-pick, rebase, squash, push, or merge workers directly into the
   project branch.
9. The coordinator updates only shared managed specs/docs/changelog and makes
   one final integration commit only for a non-empty diff. Product-code fixes
   become a new isolated correction task.
10. Run combined validation and integration `code-review`; reconcile queued
    steering. Invoke private `wave-promote` only when evidence is complete and
    the primary checkout remains clean, on the recorded branch, at the recorded
    base. Promotion is `git merge --ff-only <verified-integration-SHA>` after
    verifying the integration branch still identifies that exact commit.
11. Mark tasks done only after promotion. Invoke private `wave-cleanup`; unlock
    and non-force-remove clean reachable worktrees, then ancestry-proven
    branches with `git branch -d`. Cleanup uses `git worktree remove` without
    force and records each absent resource in the outer lease. Never run broad
    prune or gc. All worker and integration resources stay internal: they are
    never pushed, published, or merged anywhere except the outer project branch.

## Idempotency

- Every Git mutation is journaled before execution and re-observed afterward.
- Repeated `run` resumes recorded v3 execution state; it does not recreate assignments,
  branches, worktrees, commits, merges, or revisions.
- Immutable assignment retries must be byte-equivalent. Coordinator state owns
  mutable task/wave transitions.
- If promotion reports failure, classify observed project `HEAD` as unchanged,
  promoted, or unexpectedly moved before any retry.
- Cleanup failure retains an exact inventory and never rolls back promotion.
- A managed outer lease spans every wave and final `$align`. While held it
  blocks outer inspect, push, PR creation, and removal. It releases only from a
  clean outer branch at the final promoted head with every internal resource
  absent. Missing or malformed coordination state fails closed.
- Execution-plane-v1 and coordinator-v1/v2 runs are unsupported and return
  `WORKFLOW_UPGRADE_REQUIRED`, including completed records. Do not add a legacy
  read path, execution shim, or migration path.

## Failure Handling

- Worker, merge, validation, review, steering, sandbox, or promotion failure
  leaves the project branch unchanged and retains exact recovery resources.
- Use `REPLAN_REQUIRED` for undeclared paths/domains;
  `UNSUPPORTED_SUBMODULE_SCOPE` for claims crossing gitlinks;
  `UNSUPPORTED_SYMLINK_SCOPE` for claims crossing tracked symlinks;
  `WORKTREE_COLLISION` or `WORKTREE_CONFLICT` for identity/drift;
  `INTEGRATION_CONFLICT` or `INTEGRATION_VALIDATION_FAILED` before promotion;
  `PROMOTION_BLOCKED` or `PROMOTION_FAILED` for publication failure;
  `CLEANUP_BLOCKED` for retained unsafe resources; and
  `ENVIRONMENT_BLOCKER` for missing safe bootstrap state or access.
- Native subagents inherit the parent sandbox but are not intrinsically bound
  to a worktree. Verify absolute cwd and Git identity at every transition;
  never widen permissions to make a worker pass.
- Preflight private-root and shared Git-common-directory access. Linked
  worktrees share objects, refs, config, and hooks despite separate indexes.
- An unfinished pre-interop run detected inside a managed outer worktree
  returns `WORKFLOW_UPGRADE_REQUIRED`; do not infer, migrate, or adopt its
  resources. There is no TTL, PID-based recovery, force-clear, or compatibility
  path for task leases.

## Must Not

- Do not expose internal IDs or transitions in user commands.
- Do not let the coordinator implement worker tasks.
- Do not run parallel tasks with overlapping claims, domains, shared specs,
  dependency files, migrations, infrastructure identities, or architecture
  assumptions.
- Do not let workers touch the primary checkout, other refs/worktrees, shared
  handoff/spec/docs, Git maintenance, external systems, or undeclared paths.
- Do not push or publish internal task branches, target `origin/main`, or let
  outer `worktree` push/create-pr/remove run while the task lease is active.
- Do not force-remove worktrees, force-delete branches, copy local state,
  initialize submodules, cherry-pick, rebase, squash, push, open a PR, publish,
  or perform live external writes without separate authorization.

## Completion Criteria

- The public interface remains exactly two explicit-only commands.
- Every task belongs to a deterministic wave and has locked ownership.
- Every promoted task has one verified worker commit, ordered merge evidence,
  combined validation/review evidence, and handoff status updated after
  promotion.
- The project branch advances only by verified fast-forward promotion.
- Successful cleanup leaves no managed temporary refs or worktrees; retained
  resources are reported exactly.
- Final changed-surface `$align` passes and the private finalizer releases any
  outer task lease, or the run stops with a precise blocker.

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
