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

Use deterministic earliest-fit dependency waves and a ready queue within each
wave. Default capacity is at most four, capped by observed native capacity,
configured limits and independent ready tasks. Unobserved capacity means one
isolated worker. Refill a slot after native termination and validated admission;
all tasks in the wave use the same frozen base. Integrate only after the full wave
is admitted, in stable task order, then run combined validation. Path overlap,
lockfiles, generated outputs, ports, test databases and shared live targets are
constraints. A shared runtime needs a declared `runtime:<resource>` conflict
domain and an exclusive assignment-bound lease.

## State And Resources

Shared schemas are `workflow/execution-coordinator-v1`, `workflow/execution-wave-v1`,
`workflow/execution-task-v1`, `workflow/worker-assignment-v1`, and
`workflow/worker-result-v1`. Each assignment binds a normalized objective and
immutable execution basis; each admitted result binds exact identity, ancestry,
changed paths, validation, review and native termination. Typed `spec_gaps` return
to the root coordinator. Workers never publish canonical specs independently.

The integration branch uses `codex/agentic-sdlc/<run>/<feature>/integration`;
Implement uses `codex/implement/<run>/<feature>/integration`. Worker identities
include wave and task IDs. Worktrees live under the private run directory.
Separate wave/task/assignment/result records and project/execution locks protect
concurrent state. Hashed native-session claims and expected-attempt recovery
prevent identity reuse. Unsupported old schemas retain resources and fail clearly.

## Lifecycle

1. Freeze the current objective/spec/plan/base/helper basis and prepare integration.
2. Run TDD there and seal its internal base commit.
3. Dispatch fresh task workers into ready slots. Require native task registration,
   bounded direct heartbeats, and read-only liveness observation. Never infer
   parallel execution from declarations or copy state from a prior native session.
4. After exact native termination, the coordinator verifies and commits each result,
   admits its immutable receipt, then refills its slot. Integrate the complete wave
   in stable order with explicit merge commits. Workers never commit themselves.
5. Run combined checks at the exact integration tip and clean only reachable,
   clean, identity-matched worker resources without force.
6. After downstream evidence and alignment, fast-forward the unchanged clean
   promotion checkout under Worktree's common-Git lock, then clean integration.

The paired specification publisher enforces registered integration ownership,
expected objective/basis digests and compare-and-swap document revisions. Delivery
returns authority to the delivered checkout; later documentation drift requires a
corrective run. See [shared contracts](../../worktree/references/workflow-contract.md).

## Recovery

An integrated wave whose combined checks fail uses classifier lifecycle
`integrated_wave`. A proven localized diagnosis and counted active dispatch
authorize private `wave-fail --classification ID --dispatch ID --evidence TEXT`
with the existing run, feature and wave selectors. It binds the exact clean
integration tip, preserves the failure and performs normal non-force worker
cleanup. Cleanup ambiguity retains ownership and blocks correction.

The resulting wave remains `failed`; coordinator state is `repair_required`.
An adjacent locked plan preserves history and appends diagnosis/oracle-bound
tasks. Only the active repair can cross an unresolved failed predecessor.
Incoming handoffs include the original combined failure alongside task history.
Passing combined checks at the corrective tip create a digest-bound resolution
with original failure, task results, classifier dispatch and integration
ancestry. Only then may normal work or sealing proceed. Complete the repair
dispatch after wave completion and rerun the classifier's invalidated gates.
Historical replay never rewinds the current integration or relabels failure.

Journal intent before Git mutation and re-observe refs, worktree registration,
cleanliness, and ancestry afterward. Repeated transitions never duplicate
resources, assignments, commits, merges, or cleanup. Dirty, divergent,
unreachable, malformed, or foreign resources stay recorded. Do not infer
success from a command error; classify observed Git state first.

`task-start`, `task-heartbeat`, and `task-recover` derive identity from
the native Codex or Claude session binding; callers cannot supply arbitrary runtime session tokens.
`task-arm` starts a 60-second prestart deadline only when capacity exists.
Workers emit direct heartbeats at least every 30 seconds; background heartbeat
loops are forbidden. Read-only `task-watch` gives scope violations precedence
over prestart mutation and enforces prestart timeout, scope, read-only, stall,
and maximum-runtime budgets. After the coordinator stops the entire process
group for a never-started worker, `task-requeue` compares the exact dispatch
timestamp and accepts only attempt zero, no session ownership, exact branch and
common-directory identity, clean base `HEAD`, and no changed paths.
`task-recover` requires coordinator-issued recovery authorization and immutable native termination evidence, the
exact current attempt, a fresh hashed session identity, and exact scope cwd. It
preserves only a clean base or claimed dirty state; worker-created commits are
rejected. `replan-future` holds the execution transition lock and replaces only
planned waves without assignments, worktrees, branches, results, commits, or
active journals.
Completed and current waves remain immutable, and every task in them must match
both its full canonical definition and recorded definition digest. An integrated
coordinator may prepare only an appended planned wave after every prior wave is
done. Preparation retains the existing integration tip and TDD lineage; it does
not reopen completed waves or reseal TDD.

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
managed child. Obsolete persistent-lane state and execution coordinator formats
are rejected without conversion, automatic deletion, or aliases.

## Safety

Native subagents inherit the parent sandbox and are cooperative, not
worktree-confined. Verify absolute cwd, real Git root/common directory, branch,
base SHA, and assignment digest at every transition. Never copy ignored local
state or widen permissions automatically. Coordinator `task-finish` screens
evidence, commit metadata, staged filenames, and staged/committed content for
obvious secrets and private endpoints before persisting a result.
The same screen covers handoff summaries, decisions, and open risks.
Generic credential assignments permit bounded, parseable unquoted lookup/call
expressions such as a CSRF cookie read. Literal values and ambiguous text still
fail closed; provider-specific patterns are screened independently.
