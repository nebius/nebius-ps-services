---
name: task-implementer
description: "Requires explicit invocation to initialize a private prompt workspace and run one durable brownfield implementation through dependency waves, internal full-repository Git worktrees, worker review/commit evidence, ordered integration, and fast-forward promotion, including safe nesting under a worktree-managed outer branch. Do not use for ordinary one-shot implementation, Agentic SDLC, standalone Git workflows, or generic parallel-agent requests."
---

# Task Implementer

## Help

For `$task-implementer --help` or `$task-implementer -h`, return concise help and stop before
any workflow step. Include the purpose, invocation policy, public usage/actions,
and `-h, --help` plus only documented skill-level options; say "No additional
public flags" when none exist. For internal or coordinator-only skills, state
that boundary and that no standalone public workflow action exists. After the
selected `SKILL.md` is loaded, help is report-only: do not call any additional
tools, inspect project state, or modify files, private state, Git, or external
systems. Never
expose private helper actions or treat help as workflow authorization.

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
- The canonical project checkout, repo instructions, and immutable prompt
  snapshot. In an unmanaged checkout, `origin` must expose an unambiguous
  symbolic default branch; a clean checkout on that default is switched to a
  deterministic `feature/task-<run-hash>` promotion branch. A managed child
  instead uses its exact local branch and current `HEAD` without fetching or
  consulting the remote default.
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
- After managed requirements and design are valid, explicitly route to
  `$project-agent-instructions` and read its final decision plus any active
  selected-project instruction file before locking the coordinator contract.
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

`handoff.md`, managed requirements/design records, the private
project-agent-instructions receipt, shared README/design docs, and changelog
evidence are coordinator-owned. The shared skill exclusively owns creation or
provenance-safe refresh of project-root `AGENTS.md`; workers never edit it.
Worker assignments are immutable; workers write only their locked product scope
and private result record.

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
   validation, done criteria, and prompt/repository constraints in stable task
   order. Repeat every applicable constraint in each task's stop context. Do
   not decide project instructions until the managed requirements and design
   records have both been rendered and validated.
3. Combine overlapping work before IDs lock when it is one coherent result.
   Otherwise add an explicit dependency. Invoke private `wave-plan` and fail on
   cycles or malformed ownership. If the project checkout is a `worktree`
   managed linked worktree, acquire its private v4 lease with owner kind
   `task-implementer` at the exact current outer `HEAD` before creating task
   resources. On every resume, reconcile the exact lease token, owner, run,
   promoted head, release state, clean outer checkout, and live Git identity;
   never trust local interop flags alone. The active lease blocks outer
   integration and removal. Reject any worker or coordinator write claim that
   escapes the managed task scope; never clip it.
4. Coordinate every recorded wave through the lifecycle below. A logical wave
   may dispatch in capacity-sized batches, but batching never changes its wave.
5. Reconcile queued steering only at a safe wave boundary. Contradictory
   steering preserves work and stops before promotion. Before preparation,
   private `wave-replan` may replace the resource-free planned tail. After the
   final promoted wave is cleaned, it may also append a newly discovered
   isolated correction tail before finalization. The active coordinator index
   keeps only completed waves plus the replacement schedule;
   superseded planned wave files remain blocked history outside that index.
6. Continue with the next wave from the newly promoted project `HEAD`. After
   the last cleanup, run final changed-surface `$align`, then invoke private
   `run-finalize` with its concise evidence. Only that transition marks the
   handoff done and releases a managed outer lease. For a managed child, report
   the recorded primary path/source branch plus the exact
   `$worktree integrate <generated-name>` handoff, and stop for a fresh explicit
   user invocation from that primary checkout; never invoke it internally,
   push the child, or open a PR from it. An unchanged completed prompt returns
   `ALREADY_COMPLETE`; an interrupted lease release returns a private
   finalization-pending outcome and repeats the same final transition. Edited
   completed content starts a new internal run only after release.

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

1. Require a clean named project branch. In an unmanaged checkout, resolve and
   verify the actual remote default; when the current branch is that default,
   create and switch to the deterministic run promotion branch from the
   verified default SHA. In a managed child, retain the exact local child
   branch and current `HEAD` without fetching or resolving a remote default.
   Record the selected mode and exact identity in coordinator v6.
2. Register resource intent in the managed outer lease when present, journal
   intent, create and lock an integration worktree/branch from that exact
   commit, then re-observe Git state.
3. Preallocate and validate coordinator-owned requirement/design records.
   Explicitly invoke `$project-agent-instructions` with spec owner
   `task-implementer`, store its receipt under private orchestration state, and
   stop on any structured blocker. Read any active selected-project instruction
   file explicitly in the current coordinator session.
4. Commit the locked contract only if tracked coordinator files changed. A
   created or refreshed selected-project `AGENTS.md` is permitted in that one
   coordinator-owned commit; that commit is every worker base. A `not-needed`
   or `existing-sufficient` outcome changes only private state.
5. Invoke private `wave-dispatch`. It creates a unique validated branch and
   locked full-repository worktree only for the active capacity batch, plus an
   immutable v7 assignment with absolute scope cwd, exact helper/workspace
   paths, base, digest, claims, domains, validation, criteria, canonical worker guardrails, and a
   digest-bound private incoming handoff. The
   handoff contains accepted evidence from all earlier completed waves and
   batches; only the first batch of the first wave has no predecessors.
6. Reserve the main thread for coordination. Dispatch native worker agents up
   to available capacity. If unavailable, use fresh sequential `codex exec`
   workers in the same isolated worktrees; the coordinator never implements a worker task.
   Start every worker with only its immutable assignment and incoming handoff,
   without inherited coordinator transcript or unrelated conversation context.
   Invoke private `task-arm` only when a real worker slot is available, then
   spawn that worker immediately. Queued assignments remain unarmed and do not
   consume a start budget. The worker reads the assignment and makes
   `task-start` its first private transition after verifying immediate Git/cwd
   identity. It invokes the assignment's exact embedded helper/workspace paths
   and passes the embedded digest unchanged; `task-start` performs the
   authoritative canonical digest check, so the worker never invents JSON
   serialization. It reads the incoming handoff and performs deeper preflight
   only after that transition. An armed worker must reach `task-start` within
   60 seconds.
   After one failure, stop dispatching new batch members while
   active workers finish.
7. Every worker first verifies its real worktree root, branch, base SHA, and
   absolute cwd, then invokes `task-start` with the embedded helper/workspace
   paths and the embedded digest unchanged. The helper verifies the canonical
   assignment digest. It next
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
   If a running worker stops, the coordinator confirms that it stopped, then
   the fresh replacement worker invokes private `task-recover
   --confirmed-stopped` from the assigned scope cwd as its first transition.
   Recovery transfers only declared dirty state or one direct-child commit to
   that replacement session; the coordinator must not invoke recovery on the
   worker's behalf because session ownership is bound to the caller. Session
   hashes are append-only history: a
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
12. Invoke private `wave-promote` only after worker cleanup succeeds, the
    primary checkout remains clean on the recorded promotion branch at its
    recorded base, and the actual remote-default branch and HEAD still equal
    their recorded identity. A common-Git-directory lock covers identity precheck,
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
- Reuse an unchanged project-agent-instructions decision. Changed managed specs
  or inherited guidance require a fresh decision at a safe wave boundary before
  future workers dispatch.
- If promotion reports failure, classify observed project `HEAD` as unchanged,
  promoted, or unexpectedly moved before any retry.
- Cleanup failure retains an exact inventory and never rolls back promotion.
- A managed outer lease spans every wave and final `$align`. While held it
  blocks outer integration and removal. It releases only from a clean outer
  branch at the final promoted head with every internal resource absent. The
  released child then returns the primary path plus the exact
  `$worktree integrate` handoff and stops for a fresh explicit user invocation
  from the primary checkout; publication remains a source-branch action.
  Missing or malformed coordination state fails closed.
- Execution-plane-v1 and coordinator-v1/v2/v3/v4/v5 runs are unsupported and return
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
  `PROMOTION_BLOCKED` or `PROMOTION_FAILED` for local promotion failure;
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
- Treat the schema-v4 `released` lease record as the terminal receipt. Repair a
  missed local interop write only when its owner, token, promoted SHA, clean
  outer checkout, and absent resources match exactly; otherwise stop.

## Must Not

- Do not expose internal IDs or transitions in user commands.
- Do not let the coordinator implement worker tasks.
- Do not run parallel tasks with overlapping claims, domains, shared specs,
  dependency files, migrations, infrastructure identities, or architecture
  assumptions.
- Do not let workers touch the primary checkout, other refs/worktrees, shared
  handoff/spec/docs, Git maintenance, external systems, or undeclared paths.
- Do not push or publish internal task branches or managed child branches,
  target the remote default from managed mode, or let outer `worktree`
  integrate/remove run while the task lease is active.
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
