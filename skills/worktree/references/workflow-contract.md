# Shared Direct Workflow Contract

Worktree owns the local Git transactions, locks, recovery and cleanup used by
`implement` and Agentic SDLC. Coordinators do not nest. `global-context-management`
owns context/continuity; `maintain-project-specs` owns requirements/design
interpretation and canonical publication. Global eligible root-user intent
maintenance is independent of workflow entry. Workers report spec gaps to the root.

## Accepted objectives and bindings

Canonical public entry is `$implement [request]` or `$sdlc-start [objective]`.
Bare invocation consumes the current unambiguous plan or this session's recorded
active binding. Cross-session continuation requires `continue [project-folder]`.
Help is read-only. Old workspace/file/integrate/remove commands have no aliases.
Resolve ambiguity with readable project/objective descriptions.

Private `workflow_intake.py --owner implement|agentic-sdlc` provides `accept`,
`observe`, `continue`, `fence`, `refinement-ready`, `settle-objective`, `complete`,
`retire-fenced-waves`, `resume-replanned` and `cancel-complete`. These are internal
transitions, not additional public skill commands. `accept` takes the exact project
and an owner-private normalized candidate. Material steering uses `--revise`.

The normalized objective has exactly outcome (text), scope, constraints,
acceptance_criteria, clarifications (lists of text), and provenance (kind
`direct_request`, `current_plan` or `steering`, plus `source_sha256`). No raw turns
or prompt-history archive is retained. Objective snapshots are immutable
`objectives/rNNNN.json`; `workflow/accepted-objective-v1` selects adjacent revisions
with content/semantic digests and parent ancestry. `workflow/run-v1` owns the run.

State lives under `<agent-home>/implement/` or `<agent-home>/sdlc-runs/`, outside Git.
`bindings/<native-session-sha256>.json` uses `workflow/session-binding-v1` and names
one exact run. `active-run.json` selects a project run but never grants ownership.
`active.lock` is the coordinator ownership projection. Session-start observation
may restore a recorded binding; it cannot select a sole active run or dispatch work.
A different native session takes ownership only through explicit continuation after
writer quiescence. Native session identity is never a caller-authored token.

## Immutable execution basis

Before dispatch, settle every statement's spec impact through the shared spec owner.
Contract changes map requirement/design IDs; execution-only and justified no-effect
outcomes remain distinct. No-impact work with no specs does not create documents.
Implement first calls private `prepare-specs` to allocate its sole integration
checkout from the accepted source. This pre-plan state has no execution basis,
waves or assignments. Paired publication requires the accepted objective digest
and no basis digest. After staging new canonical files and settling impact,
`prepare` verifies the paired-publisher receipt, journals and commits only the
canonical pair, then freezes the plan/basis and creates waves. Canonical specs
are implicit committed inputs; ignored or uncommitted source specs cannot be
excluded by a dependency declaration. Source HEAD and unrelated dirt are retained.
An interrupted allocation retries `prepare-specs`; an interrupted initial spec
commit retries the same `prepare` and recovers only its exact direct-child tree.
Prepared execution publishes canonical specs only at its registered integration
checkout and expected revision. After delivery, use the delivered checkout. Stale
or conflicting publication fails without blocking unrelated tools or session exit. The paired
publisher checks an explicit Git-common scope registry and the native coordinator,
accepted objective/basis digests, quiescence and expected document revisions. Use
`--expected-objective-sha256` and `--expected-basis-sha256` during prepared execution;
receipts retain that witness. Ordinary maintenance without a registered workflow
uses its existing document transaction.

Lock plans under `plans/FEAT-NNN.plan.vN.md` with a sibling `.lock`. Use heading
`# FEAT-NNN Plan vN`, stable contiguous `TASK-*` IDs, Requirements (or `none` for
no-impact work), Goal, Depends on, Write claims, Conflict domains, Validation, Done
criteria and Rollback or stop conditions. Claims are exact/prefix repo-relative
paths. Unknown ownership serializes scheduling. SDLC keeps its existing requirement
traceability and TDD responsibilities.

Task headings are exactly `### TASK-NNN`. Each top-level `- Label:` occurs
once; indent continuation text, nested bullets and fenced examples under it.
Complete field bodies survive assignment serialization. List fields use
comma/semicolon separators even when wrapped across lines. Empty or duplicate
fields and malformed task headings are rejected, and a following plan heading
ends the task's fields.

`prepared-bases/FEAT-NNN.json` freezes the actual committed Git base.
`bases/FEAT-NNN/<sha256>.json` freezes objective revision, spec receipt, locked plan,
base, effective instruction/helper identities, run ownership, assignments and
validation requirements. An atomic pointer selects the current immutable basis.
Assignments/results include its digest. Dispatch, admission, integration and
promotion reject stale bases. Historical evidence remains bound to its old basis.

Execute committed history. Detached/unborn states fail before execution. On dirty
source, the root must declare complete committed input dependencies in private
`workflow/input-dependencies-v1` (`base_head`, `complete: true`, `claims`) before
excluding unrelated dirt. A dirty read dependency or write claim blocks; uncommitted
content is never copied. Preserve unrelated files and the original checkout.

## Scheduling and native worker ownership

`workflow_transition.py prepare` uses one run integration checkout for Implement
and SDLC's single active feature integration checkout. Workers receive fresh task
worktrees. The deterministic ready queue operates inside each frozen dependency
wave. Capacity is at most four, capped by native host observation, configured
limits and available independent tasks. Without an established native capacity,
use one isolated worker and report serial execution.

`wave-prepare` allocates free slots. `task-arm`, native `task-start`, heartbeat and
watch establish real task ownership. Use `workflow_dispatch.py` for the sequential
native CLI fallback. Workers return structured evidence and spec gaps; they do not
create arbitrary commits. After observed native termination, the coordinator uses
`task-finish --termination` to create one journaled direct-child commit, validates
it with `task-admit`, then calls `wave-refill`. A result file, Git worktree lock or
an agent's own “done” statement does not prove writer quiescence.

Coordinator-owned immutable termination observations use
`workflow/worker-termination-v1`: assignment digest, worker session hash, observer
session hash, native handle, terminated status and actual observation. Deterministic
tests label synthetic observations explicitly and cannot certify native execution.

All workers in a wave retain the same base. Admit results only after validating
worker/assignment identity, basis, ancestry, changed paths, evidence and quiescence.
Integrate in stable task order only after the full wave is admitted; run combined
validation and alignment before delivery. Slot refill does not remove that barrier.

Paths, lockfiles, generated outputs, ports, databases and shared runtime targets
are conflict domains. A shared live target also requires a declared `runtime:<id>`
conflict domain and `runtime-acquire` on the exact assignment. Runtime consumers
verify the exact active lease. Native workers may use `runtime-verify`; the outer
verifier uses the shared read-only validator and consumption guard. Acquire,
consume and release take the resource-use lock before short run/resource state
locks; consumers retain only the use lock during synchronous actions, preserving
heartbeat/status progress. No consumer impersonates or acquires for the worker.
The worker holds the lease through the whole live sequence and owned teardown,
then releases it before completion, or the
coordinator releases it with exact termination evidence. Independent nonconflicting
work can continue. Leases are private under `<agent-home>/workflow-resources/`.

## Steering, cancellation and recovery

Root-user steering uses the same statement-level specification extraction and
canonical reconciliation as other prompts. Status, questions and execution-only
instructions do not invent specification changes. Accepted material revisions
and cancellation additionally fence dispatch/promotion first. Stop
affected native writers and use `worker-reconcile` with their termination
observations. Reconciliation preserves work/results under the old basis and grants
no admission. Retire incomplete affected waves, preserve earlier evidence, settle
current spec impact, and replan affected work. Historical superseded tasks cannot
satisfy dependencies of new tasks. Lift steering only with `resume-replanned` after
current-basis verification. Cancellation reports retained effects/resources and
never rolls back delivered commits.

Resume journals at known supported checkpoints. Do not overwrite immutable evidence,
force-remove dirty trees or infer stopped writers. Old schemas, persistent-lane
ownership markers and obsolete state are unsupported and remain untouched.

## Delivery

Implement automatically fast-forwards the original non-default branch only when
its checkout is clean and HEAD unchanged. Drift or dirt retains the verified result
and reports the blocker. Starting on default, or with unknown default identity,
delivers a verified feature branch and checkout while leaving source unchanged;
this is complete local delivery. Use shared cleanup primitives and retain explicit
recovery artifacts when safe cleanup cannot be proven.

SDLC retains remote Git prerequisites, phase gates, remediation budgets, managed
outer-worktree leases, UAT and exact-commit remote-action authorization. Post-promotion
documentation assessment is read-only; drift starts a corrective run. Local delivery
never authorizes a push, PR, merge or publication.

Report deterministic tests, installed hooks, fresh native-agent execution and live
application evidence separately. Unavailable runtime checks remain NOT_RUN/PARTIAL;
fixture intervention cannot establish a workflow pass.
