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
wave. Freeze independent W/B/P limits (defaults 4/8/16) using the shared
[wave contract](../../worktree/references/worker-waves.md). Fresh observed total
native capacity constrains launches; zero waits and unknown permits one total
worker. Reserve whole waves across the run before allocating worktrees. Refill a slot after native termination and validated admission;
all tasks in the wave use the same frozen base. Integrate only after the full wave
is admitted, in stable task order, then run combined validation. Path overlap,
lockfiles, generated outputs, ports, test databases and shared live targets are
constraints. A shared runtime needs a declared `runtime:<resource>` conflict
domain and an exclusive assignment-bound lease.

## State And Resources

Shared schemas are `workflow/execution-coordinator-v3`, `workflow/execution-wave-v3`,
`workflow/execution-task-v4`, `workflow/worker-assignment-v4`, and
`workflow/worker-result-v4`. Each assignment binds a normalized objective and
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

Blocked SDLC workers require an active counted classification bound to their
canonical receipt, current integration commit and fingerprints before recovery.
The shared `workflow/recovery-authorization-v2` binds that authority and rechecks
it at ownership transfer. See the
[shared recovery contract](../../worktree/references/workflow-contract.md) for
exact event evidence and permitted same-assignment routes. Interruption without
a worker result retains its existing native-termination recovery contract.

An active-task `DESIGN_DEFECT` with a proven diagnosis and active counted design
dispatch may correct the canonical contract before integration. Stop every
allocated native writer and release its runtime leases. Use private
`prepare-design-revision --run-dir RUN --execution FEAT --classification-id ID
--dispatch-id ID --termination PATH` with one exact termination observation per
allocated writer (repeat `--termination`). The owner validates the original
canonical snapshot, basis, plan and clean integration tip, rejects partial
merges, and preserves failed candidates and assignments with immutable retention
proof. It enters `design_revision_pending`; dispatch and replanning remain blocked.

Publish the corrected canonical pair through `maintain-project-specs`, binding
the same objective, basis, original HEAD and document digests. Then use private
`seal-design-revision --run-dir RUN --execution FEAT --revision-id ID
--publication-operation-id OP --message SUBJECT`. The revision ID is the counted
dispatch ID. Sealing reads the semantic owner's journal, admits only that exact
pair and records the exact commit tree and parent before committing. Exact
replay recovers interruption without a second commit or another repair charge.
Unrelated staged/dirty content remains a blocker and is preserved.

Refresh objective impact and lock a replacement plan. Preserve all started task
definitions and historical waves, then append new tasks without dependencies on
superseded tasks. Future tasks are scheduled after that frozen prefix, including
independent tasks with disjoint claims. Dispatch uses the new basis and sealed
tip; old assignments and results cannot resume. Preserve the original TDD seal,
rerun the classifier's invalidated gates, and complete repair only with the
required new evidence. This transition covers one admitted feature's unmerged
active-task design correction; integrated-wave repair below keeps its separate
authority. It never supplies a steering fence or broader design approval.

### Late Test Revision

An active unmerged-wave `TEST_DEFECT` uses the same revision owner with typed
localized authority. Preserve the original failure event and counted dispatch;
include exact `requirements`, `design`, `plan` and `basis` fingerprints and the
integration commit. Stop allocated native writers and release their leases.

Use private `prepare-test-revision --run-dir RUN --execution FEAT
--classification-id ID --dispatch-id ID --termination PATH --file PATH`.
Repeat termination for every allocated writer and file for each selected path.
File paths are exact repository-root-relative paths, including project scope;
one to 32 existing tracked regular text files, at most 1 MiB each, are allowed.
The TDD owner must
review test-only semantics. Mechanical checks protect canonical specs, project
instructions, frozen independent acceptance harnesses, symlinks and other scopes.
Preparation retains failed work and enters `test_revision_pending`; workers and
replanning remain blocked until sealing.

Edit only those files in the registered integration checkout. Write a private
JSON review under the run's `evidence/` directory with these exact fields:

- `schema`: `workflow/test-revision-review-v1`.
- `prepare_sha256`: the returned preparation receipt's `sha256`.
- `files`: an object keyed by every frozen path, each containing `before` copied
  from `test_files[path]` and `after`, both objects with `mode` (`100644` or
  `100755`) and unprefixed SHA-256 of exact bytes. Modes must remain unchanged.
- `criteria_unchanged`: `true`, after actual semantic review.
- `reason`: a bounded explanation of the fixture correction and preserved
  assertions/accepted behavior; no secrets or raw failure logs.

Use private `seal-test-revision --run-dir RUN --execution FEAT --revision-id ID
--review PATH --message SUBJECT`. The revision ID is the counted dispatch ID.
The owner rechecks authority, exact files and unrelated dirt before root staging,
journals the precise commit and preserves normal Git hooks. Exact replay resumes
an interrupted commit without a second commit or charge. Review hashes detect
drift; they do not replace semantic review or executed tests.

Append replacement tasks using a new locked plan and existing `replan-future`.
Preserve all started definitions and acceptance policies; never depend on the
superseded tasks or copy their unadmitted code. Reacquire preparation/TDD evidence
against the fresh basis, then rerun all classifier-invalidated gates. Original
TDD sealing remains immutable. This path covers active unmerged waves only;
it grants no authority over integrated or promoted candidates.

For both late design and test revision, reconciliation releases exact stopped
shared worker slots while retaining physical checkout claims. After sealing and
reviewed replanning, `wave-prepare` automatically archives superseded candidate
bytes, index/staging evidence and complete branch history, verifies the archive,
then retires only the owned checkout/ref and releases physical capacity. It
rechecks archive integrity at promotion. Follow the
[retirement contract](../../worktree/references/revision-retirement.md) on a
refusal; do not clear reservations, delete a candidate or fabricate a commit.

### Integrated Wave Repair

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

For `PLAN_DEFECT`, require a proven `plan_defect` diagnosis, the exact failed
validation producer receipt and the private validation-plan review described
in `worktree/references/worker-waves.md`. The plan owner may correct only
combined command argv while preserving acceptance and historical task checks.
Schedule appended corrective tasks before untouched future tasks; no-change
corrections still require independent task and wave acceptance. If another
counted correction is needed, resolution verifies the complete immutable chain
of reviewed plans and failed receipts to the final successful producer plan.

After a terminal cancelled fence, quiescent unresolved integration may be
retained as cancellation evidence without changing its wave status or basis.
This grants neither resource deletion nor nonterminal replan authority.

Journal intent before Git mutation and re-observe refs, worktree registration,
cleanliness, and ancestry afterward. Repeated transitions never duplicate
resources, assignments, commits, merges, or cleanup. Dirty, divergent,
unreachable, malformed, or foreign resources stay recorded. Do not infer
success from a command error; classify observed Git state first.

`task-start`, `task-heartbeat`, and `task-recover` derive identity from
the native Codex or Claude session binding; callers cannot supply arbitrary runtime session tokens.
`task-arm` records dispatch ownership when capacity exists. Prepare handoffs
first and spawn immediately afterward. Workers emit direct heartbeats targeting
30 seconds; background heartbeat loops are forbidden. Read-only `task-watch`
gives scope violations precedence over prestart mutation. Startup, stale health,
read-only work and periodic progress thresholds request diagnosis; elapsed time
alone never terminates healthy work. Unknown health retains ownership and blocks
successor admission until reconciled. Confirmed failures retain their repair
budgets. See [execution health](../../worktree/references/execution-health.md).
For a never-started worker, `task-requeue` compares the exact dispatch
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

Use the shared [worker contract](../../worktree/references/workflow-contract.md)
for native launch, strict result ingestion, dispatch fences and recovery.
`task-result` validates the complete result and independent termination; only
its current successful receipt can enter `task-finish --receipt`. A
`verified_no_change` result additionally requires independent `validate-target
--task` proof and creates no commit; combined waves require `wave-complete --receipt`.
Coordinator `task-finish` writes a digest-protected
`workflow/task-finish-intent-v3` before invoking Git. The intent binds the
assignment digest, base, staged tree, canonical message, structured
evidence and immutable worker-result receipt digest. A retry may adopt only an exact clean direct-child commit matching
that intent, and may reuse a result file only when every result field matches
the same finish request. A moved `HEAD` without an exact intent, or any intent,
commit, result, or evidence drift, fails closed.

SDLC intake acquires the shared v5 lease in a clean managed outer worktree
before canonical authoring. Preparation checks the existing ownership while
validating the complete admitted contract diff. Its private contract intent binds
parent, branch, common directory, objective, plan, publication receipt, files and
staged tree. Recovery adopts only its exact clean direct-child commit, records
that head in the outer lease before ordinary reconciliation, and creates the
integration from that contract head. Unrelated dirt and unjournaled commits fail
closed. Register integration/worker resources and record
each promoted feature head. Promotion ordering is Git fast-forward, exact lease
compare-and-set, local interop write, then coordinator `promoted` persistence.
Resume reconciles `promoted`, `cleanup`, and `done` coordinators against the
durable lease and clean outer Git head before continuing. Release only after all
resources are absent and final alignment, an eligible optional UAT disposition,
and documentation evidence exist. Follow the shared
[optional UAT policy](../../worktree/references/optional-uat.md); missing optional
prerequisites do not delay otherwise eligible release. An automatically allocated objective keeps its lease in
`aggregation-ready`, validates the latest combined aggregate, promotes it through
the private owner, and only then releases and cleans the outer. Failed aggregate
acceptance retains evidence and starts a fresh corrective execution; it cannot
reopen a completed execution. See [automatic projects](../../worktree/references/automatic-projects.md).

For an explicitly supplied manual outer, persist a schema-v5 `released` receipt
and enter outer integration pending. The coordinator returns the recorded primary path plus the exact
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
