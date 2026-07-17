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

Private execution state uses `agentic-sdlc/execution-coordinator-v3`; mutable
task and result records use v2. Wave, task, assignment, and result files are
separate so parallel workers never write shared mutable JSON. Assignments and
results carry SHA-256 digests. Unfinished coordinator v1/v2 state fails with
`WORKFLOW_UPGRADE_REQUIRED` and retains resources.

The persistent integration branch is
`codex/sdlc/<run-id>/<feature-id>/integration`. Worker branches append
`/<wave-id>/<task-id>`. Worktrees live only under the private run directory.
Branch/path components are sanitized IDs; never include prompt text or secrets.

## Lifecycle

1. Prepare and lock the integration worktree from the exact clean project base.
2. Run TDD there and seal one internal TDD-base commit.
3. Create one branch/worktree/assignment per task from the current integration
   tip and dispatch one fresh worker agent per task. Prefer native agents; when
   unavailable use the sequential `codex exec` fallback with exact scope cwd,
   `workspace-write`, `--ephemeral`, stdin assignment, and output schema.
4. Verify each worker's one direct-child commit and changed paths, then merge in
   stable task-ID order with `git merge --no-ff --no-edit`.
5. Run combined evidence at the exact integration tip and non-force-clean
   reachable worker resources.
6. After all downstream evidence passes, fast-forward the clean project branch
   to the verified integration tip with `git merge --ff-only` and
   non-force-clean the integration resource.

The project branch remains at the recorded contract base throughout TDD,
implementation, validation, testing, evaluation, documentation, and alignment.

## Recovery

Journal intent before Git mutation and re-observe refs, worktree registration,
cleanliness, and ancestry afterward. Repeated transitions never duplicate
resources, assignments, commits, merges, or cleanup. Dirty, divergent,
unreachable, malformed, or foreign resources stay recorded. Do not infer
success from a command error; classify observed Git state first.

`task-recover` requires explicit previous-worker-stopped confirmation, a fresh
hashed session identity, and exact scope cwd. It preserves only a clean base,
claimed dirty state, or one clean direct-child commit. `replan-future` replaces
only planned waves without assignments, worktrees, branches, results, commits,
or active journals; completed and current waves remain immutable.

When execution starts inside a managed outer worktree, acquire the shared v2
lease as owner `agentic-sdlc`, register integration/worker resources, and record
each promoted feature head. Release only after all resources are absent and
final alignment, UAT, and documentation evidence exists; PR publication begins
after release.

Unfinished execution coordinator schema v1/v2 is not migrated. Return
`WORKFLOW_UPGRADE_REQUIRED` without changing its resources. Completed history
remains readable.

## Safety

Native subagents inherit the parent sandbox and are cooperative, not
worktree-confined. Verify absolute cwd, real Git root/common directory, branch,
base SHA, and assignment digest at every transition. Never copy ignored local
state or widen permissions automatically. Coordinator `task-finish` screens
evidence, commit metadata, staged filenames, and staged/committed content for
obvious secrets and private endpoints before persisting a result.
