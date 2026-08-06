# Agentic SDLC Execution Plane

Read this reference before preparation, TDD-base sealing, worker dispatch,
integration, promotion, cleanup, or recovery.

## Ownership

- `sdlc-start` owns phase selection and checkpoint routing.
- `sdlc-create-plan` owns the locked task graph.
- `sdlc-prepare-execution` owns the persistent feature integration resource.
- `sdlc-implement-plan` owns worker dispatch, ordered integration, and worker
  cleanup.
- `sdlc-commit` owns final integration sealing, ff-only project promotion, and
  integration-resource cleanup.
- The coordinator owns Git resources and shared state. Each worker owns exactly
  one immutable assignment and one result.

## Locked Task Shape

Each plan task uses a stable heading and required fields:

```markdown
### TASK-001

- Requirements: REQ-001
- Goal: Implement one coherent result.
- Depends on: none
- Write claims: exact: src/example.py; prefix: tests/example
- Conflict domains: api:example
- Validation: run focused tests
- Done criteria: focused tests pass
- Rollback or stop conditions: stop before undeclared scope
```

Dependencies must name earlier or known tasks. Claims are repository-relative
`exact:` or `prefix:` paths and must remain under the exact initialized project
scope. Unknown claims/domains force a singleton wave.
Core APIs, schemas, migration chains, dependency manifests, shared
abstractions, infrastructure identities, exclusive test resources, and
external mutations serialize.

## Scheduling

Build waves in stable task order using earliest-fit placement. A task enters a
wave only after every dependency's wave and only when claims/domains are
pairwise disjoint. Split wide waves into stable capacity-sized dispatch
batches; batches do not change logical dependencies or wave IDs.

## State And Resources

Private execution state uses `agentic-sdlc/execution-coordinator-v7`,
`execution-wave-v2`, `execution-task-v4`, `worker-assignment-v3`, and
`worker-result-v4`. Wave, task, assignment, incoming-handoff, and result files are
separate so parallel workers never write shared mutable JSON. Assignments and
results carry SHA-256 digests. A corrective result must include
`regression-oracle-evidence-v1` with the assignment's exact diagnosis and
oracle, a `passed` outcome, evidence reference, coordinator-created task
commit, and content
digest; missing or mismatched proof fails before integration. Task records
retain append-only hashes of every
worker session that has owned the task. A process-safe execution transition
lock serializes task ownership, atomic private session claims prevent one
identity from owning multiple tasks, and recovery requires the exact current
attempt. Task records also own dispatch, start, heartbeat, phase, and bounded
liveness timing. Every coordinator v1 through v6 record fails
with `WORKFLOW_UPGRADE_REQUIRED` and retains resources, including completed
records.

The persistent integration branch is
`codex/sdlc/<run-id>/<feature-id>/integration`. Worker branches append
`/<wave-id>/<task-id>`. Worktrees live only under the private run directory.
Branch/path components are sanitized IDs; never include prompt text or secrets.

## Lifecycle

1. Prepare and lock the integration worktree from the exact clean project base.
2. Run TDD there and seal one internal TDD-base commit.
3. Create one branch/worktree/assignment only for the active capacity batch
   from the current integration tip. Dispatch one fresh worker agent per task.
   Each assignment binds an immutable incoming handoff with accepted
   result evidence from all earlier completed waves and capacity batches; only
   the first batch of the first wave has an empty predecessor list. Prefer
   native agents; when unavailable use the sequential `codex exec` fallback with exact scope cwd,
   `workspace-write`, `--ephemeral`, stdin assignment, and output schema.
   Arm a task only when a worker slot exists, require `task-start` before any
   edit, accept direct bounded heartbeats, and have the coordinator invoke
   read-only `task-watch` every 30 seconds. This preserves one fresh worker
   agent per task across every batch and wave.
4. Advance to the next capacity batch only after every task in the active batch
   is committed. Workers do not commit. The coordinator invokes locked
   `task-finish` to check liveness, claims, special Git entries, and sensitive
   content before creating one direct-child task commit, then merges it in
   stable task-ID order with `git merge --no-ff --no-edit`.
5. Run combined evidence at the exact integration tip and non-force-clean
   reachable worker resources.
6. After all downstream evidence passes, hold the common-Git-directory lock
   while fast-forwarding the clean project promotion branch to the verified
   integration tip with `git merge --ff-only`, then non-force-remove the
   integration worktree and delete its ref with the exact promoted SHA.

The project branch remains at the recorded contract base throughout TDD,
implementation, validation, testing, evaluation, documentation, and alignment.

## Recovery

Journal intent before Git mutation and re-observe refs, worktree registration,
cleanliness, and ancestry afterward. Repeated transitions never duplicate
resources, assignments, commits, merges, or cleanup. Dirty, divergent,
unreachable, malformed, or foreign resources stay recorded. Do not infer
success from a command error; classify observed Git state first.

`task-start`, `task-heartbeat`, and `task-recover` derive identity from
`CODEX_THREAD_ID`; callers cannot supply arbitrary runtime session tokens.
`task-arm` starts a 60-second prestart deadline only when capacity exists.
Workers emit direct heartbeats at least every 30 seconds; background heartbeat
loops are forbidden. Read-only `task-watch` gives scope violations precedence
over prestart mutation and enforces prestart timeout, scope, read-only, stall,
and maximum-runtime budgets. After the coordinator stops the entire process
group for a never-started worker, `task-requeue` compares the exact dispatch
timestamp and accepts only attempt zero, no session ownership, exact branch and
common-directory identity, clean base `HEAD`, and no changed paths.
`task-recover` requires explicit previous-worker-stopped confirmation, the
exact current attempt, a fresh hashed session identity, and exact scope cwd. It
preserves only a clean base or claimed dirty state; worker-created commits are
rejected. `replan-future` holds the execution transition lock and replaces only
planned waves without assignments, worktrees, branches, results, commits, or
active journals.
Completed and current waves remain immutable, and every task in them must match
both its full canonical definition and recorded definition digest.

Coordinator `task-finish` writes a digest-protected
`agentic-sdlc/task-finish-intent-v1` before invoking Git. The intent binds the
assignment digest, base, staged tree, canonical message, and structured
evidence. A retry may adopt only an exact clean direct-child commit matching
that intent, and may reuse a result file only when every result field matches
the same finish request. A moved `HEAD` without an exact intent, or any intent,
commit, result, or evidence drift, fails closed.

When execution starts inside an ordinary managed outer worktree, acquire the shared v4
lease as owner `agentic-sdlc`, register integration/worker resources, and record
each promoted feature head. Promotion ordering is Git fast-forward, exact lease
compare-and-set, local interop write, then coordinator `promoted` persistence.
Resume reconciles `promoted`, `cleanup`, and `done` coordinators against the
durable lease and clean outer Git head before continuing. Release only after all
resources are absent and final alignment, UAT, and documentation evidence
exists. It persists a schema-v4 `released` receipt and enters outer integration
pending. The coordinator returns the recorded primary path plus the exact
`$worktree integrate <generated-name>` command and stops for a fresh explicit
user invocation from that primary checkout; it never invokes that public
lifecycle internally. After the separate local merge,
`complete-outer-integration` must record exact source proof. Never publish the
managed child. A Task Implementer persistent lane is a separate workflow domain
and fails with `WORKTREE_CONFLICT` before Agentic state, lease, or resource
mutation.

Execution coordinator schema v1 through v6 is unsupported. Return
`WORKFLOW_UPGRADE_REQUIRED` without changing its resources, including for
completed records. There is no legacy read path or migration.

## Safety

Native subagents inherit the parent sandbox and are cooperative, not
worktree-confined. Verify absolute cwd, real Git root/common directory, branch,
base SHA, and assignment digest at every transition. Never copy ignored local
state or widen permissions automatically. Coordinator `task-finish` screens
evidence, commit metadata, staged filenames, and staged/committed content for
obvious secrets and private endpoints before persisting a result.
The same screen covers handoff summaries, decisions, and open risks.
