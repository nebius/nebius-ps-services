# Shared Direct Workflow Contract

Worktree owns the local Git transactions, locks, recovery and cleanup used by
`implement` and Agentic SDLC. Coordinators do not nest. `global-context-management`
owns context/continuity; `maintain-project-specs` owns requirements/design
interpretation and canonical publication. Global eligible root-user intent
maintenance is independent of workflow entry. Workers report spec gaps to the root.

Prepared execution can admit a proven active-task design correction through
private `prepare-design-revision` and `seal-design-revision`. The existing
classifier and counted dispatch supply authority; exact native terminations,
released leases and immutable retained-wave proof precede paired publication.
The semantic owner retains publication authority, while Worktree commits only
the validated pair with exact parent/tree crash recovery. Pending revisions
block new dispatch/replanning. Fresh impact and appended tasks replace the old
basis without rewriting failed work, original TDD or started task definitions.
See [the private recovery sequence](../../sdlc-prepare-execution/references/execution-plane.md).
Both typed revision routes release proven-stopped worker slots at reconciliation.
After sealing and reviewed replanning, fresh wave preparation automatically
[archives and retires superseded candidates](revision-retirement.md) before
reserving replacement capacity. Original failure evidence remains immutable.

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
`objectives/rNNNN.json`; `workflow/accepted-objective-v3` selects adjacent revisions
with content/semantic digests and parent ancestry. `workflow/run-v4` owns the run.

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

After every wave is satisfied, private `checkpoint-authoring --run-dir PATH
--execution ID --operation-id ID --message SUBJECT` commits a paired canonical
publication with current no-effect/retain-plan review and execution basis. It
requires quiescent workers, validators and runtime leases, rejects unrelated dirt,
and binds exact publication, objective, Git parent/tree and bytes. The journal
recovers only its exact clean direct child. Receipts remain immutable; the
coordinator stays integrated. During counted repair, complete remediation before
checkpointing and run it before the first revalidation gate; no allowance resets.
Private `validate-candidate --run-dir PATH --execution ID` independently runs the
frozen combined checks on the current candidate. It preserves original wave
receipts and requires fresh passing evidence before final sealing after a
checkpoint. Final phase gates, sealing and promotion retain their existing owners.

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
Automatic outer intake applies this check before allocation as described in
[automatic projects](automatic-projects.md). Private accept/continue admit the
source-bound declaration; there is no public allow-dirty option.

Declarations are archived immutably at `input-declarations/<sha256>.json`.
`input-dependencies.json` selects the current declaration; each frozen execution
basis records its archive digest in `input_dependencies_sha256`. Verification
reads that archive, never the mutable current selector. A missing archive fails
explicitly and is not reconstructed from a later declaration. Replacing inputs
after basis freeze requires accepted fenced steering and quiescent writers;
new shared reads and writes cannot overlap original-source dirt.

## Scheduling and native worker ownership

The shared execution and native dispatch owners use the canonical
[execution health and timing contract](execution-health.md). Assignment v4 and
launch v3 bind progress-aware observations with no healthy-work duration expiry.
Unknown health preserves ownership; cancellation, confirmed failure, scope and
resource safety still enforce. Direct heartbeats target 30 seconds without
background impersonation. Timings describe observed work and never authorize
termination or acceptance.

`workflow_transition.py prepare` uses one run integration checkout for Implement
and SDLC's single active feature integration checkout. Workers receive fresh task
worktrees. The deterministic ready queue operates inside each frozen dependency
wave. Freeze independent worker, wave-task and retained-worker-worktree limits
before allocating resources (defaults 4, 8 and 16). Larger hosts may configure
higher limits. Fresh native observations constrain launches; zero waits, while
unknown capacity permits at most one worker including existing active workers.
See [wave policy and acceptance](worker-waves.md) for the canonical contract.

`wave-prepare` allocates free slots. `task-arm` returns a canonical `launch`
envelope from `workflow_worker_contract.py`: assignment/basis, exact cwd,
fresh assignment-only context, prompt, heartbeat phases, liveness limits, raw
runtime-resource command arguments and output-schema path/digest. Both native
parallel adapters and the sequential `workflow_dispatch.py` consume this owner.
Prepare the bounded handoff and verify native capacity before arming. After
arming, spawn immediately; defer serializing launch observations and other
inspection until after dispatch. Repeating `task-arm` never resets its clock.
Configure the native host for a fresh context; a prompt saying “fresh” does not
establish it. Adapters launch, monitor, interrupt and observe native termination;
they do not recreate prompts, result policy or scheduling. Native `task-start`,
heartbeat and watch establish task ownership.

Before arming, verify the available native tools can create fresh context,
establish the assigned command directory and honor required support permissions.
After admission, verify every returned support path is covered before spawning;
the capability precheck never substitutes for that admission.
A spawn-level cwd parameter is not required when the supported execution tool
sets each command's actual working directory explicitly. Use `scope_cwd` for
startup, recovery, heartbeats and ordinary work; frozen validation commands use
their separately declared Git-root cwd. Startup and heartbeat owners verify the
actual command cwd. Use absolute scoped paths for product edits and read the effective
instructions for the assigned project before editing. Do not rely on the
parent agent's default directory or instruction discovery.

Existing inherited permissions may already cover the assigned scope and every
`support_write_roots` entry. Verify effective coverage and preserve sandbox and
approval policy; no additional grant or spawn parameter is needed in that case.
Codex documents [inherited subagent permissions](https://learn.chatgpt.com/docs/agent-configuration/subagents#approvals-and-sandbox-controls).
Missing spawn setters alone do not make native tools unavailable. Use the
sequential CLI only when the native path cannot meet these requirements and the
host supports that fallback. Preserve any executed launch failure and follow
the existing failure and requeue/recovery owners before another attempt.

Both coordinators use the shared stop-before-repair worker boundary. An
unexpected failed check or blocking review finding returns preserved evidence
and a blocked result before any repair or rerun. Only negative outcomes declared
in immutable assignment validation before execution are expected feedback.
Required safe teardown and lease release remain allowed. The owning coordinator
classifies and authorizes recovery or replanning; workers cannot relabel failures
or erase them with a later successful check.

Before actions with known access prerequisites, both worker envelopes direct
agents to supported tools and native approval mechanisms. An approval request
does not grant access, a denial remains a policy/authority stop, and an
unexpected executed failure still requires the blocked handoff. Recovery uses
the existing shared engine with a stopped predecessor, released runtime,
unchanged assignment and fresh identity; it never grants permission itself.

Workers return complete structured evidence and spec gaps without committing.
After independently observing native termination, the coordinator stores that
observation and the complete result in bounded owner-private JSON files. Invoke
`task-result --result <payload> --termination <observation>` with the exact
run/execution/wave/task. The shared owner validates every field, identity, basis
and attempt before writing an immutable `workflow/worker-result-receipt-v1`.
Its response supplies `receipt_path`, `receipt_sha256` and `worker_status`.
An implemented receipt enters `task-finish --receipt <receipt_path>
--termination <observation> --message <message>`. A `verified_no_change` proposal
first requires independent `validate-target --task <task>` acceptance at its exact
clean base; finish creates no commit and needs no message. Projected summary,
validation and review flags are unsupported. Finish binds the native receipt and
Git proof into the tagged `workflow/worker-result-v4`.
Then use `task-admit` and `wave-refill`. A result file, Git worktree lock or an
agent's own “done” statement does not prove writer quiescence or test success.

Receipts are immutable per task attempt. Exact ingestion replays work after
finish; changed replays conflict. Current operations authorize the current
coordinator while historical receipts retain their original issuer and evidence.
Before prepare/refill, arm and first start, current blocked or replan receipts
stop new dispatch. Running peers may heartbeat, replay start, finish and admit;
the full-wave integration barrier remains. Recovery and replanning stay with
their existing owners. Completed and specification-gap results cannot renew a
worker attempt through recovery.

Blocked SDLC recovery requires an active counted localized dispatch for that
exact worker-result receipt, integration commit and current fingerprints. The
failure event's `evidence_digests.worker_result` is `sha256:` followed by the
canonical `receipt_sha256` returned by `task-result`, not the raw file hash.
Allowed same-assignment routes are `IMPLEMENTATION_DEFECT` or
`ENVIRONMENT_DEFECT` with `existing_task_attempt`, and `VALIDATION_DEFECT` routed
to `sdlc-validate-codes`. Other classifications use their owning revision route.
`workflow/recovery-authorization-v2` binds this repair authority and blocked
receipt. Both authorization and ownership transfer verify the binding; a changed
failure, completed dispatch or stale target rejects recovery before task mutation.
Redeeming an already counted dispatch does not count another repair attempt.
An interrupted worker with no result and ordinary Implement recovery retain
their existing contracts. Old authorization schemas fail without migration.

Authorized recovery returns a launch envelope from the same builder. If a host
allocates native identity only when creating an agent, first create a fresh idle
agent that performs no task work, obtain its identity, authorize recovery after
observing the prior worker's termination, and send the returned envelope. It
contains the exact recovery command and expected attempt. Recovery authorizations
are immutable per task, attempt and issuing coordinator; a successor needs its
own authorization and fresh termination observation. Preserve prior receipts
and reject an obsolete issuer's authorization without overwriting it.

All native adapters consume the existing `task-arm` or `authorize-recovery` CLI
admission, which validates the selected home, owner-root layout, current
coordinator binding, project context/manifest and canonical assignment before
native launch. Existing arm/recovery checks still enforce basis, worktree and
attempt identity. Do not substitute a pure internal launch-builder result for
this admission. The `worker-launch-v3` envelope declares `support_write_roots`:
the registered project-state directory, plus the selected home's
`workflow-resources` directory for runtime tasks or recovery. Recovery needs
the lease-store lock even without a runtime conflict domain.

The coordinator prepares missing lease storage after owner checks, under the
existing project lock. Both sequential host adapters use documented `--add-dir`
directories; no native config, approval, sandbox or launch-mode override is
introduced. Complete admission before spawning. Native parallel adapters must
verify the same admitted roots are covered, including by inherited grants,
or report unavailable capability.
The directory grants include sibling runs and active pointers within one
project and other leases in the shared resource store. Helper ownership,
assignment and resource checks enforce finer authority; unrelated edits remain
forbidden. Keep the project lock and its ordering unchanged. Atomic lease
replacement requires directory access, not only access to the final JSON file.
See the [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
and [Claude CLI reference](https://code.claude.com/docs/en/cli-reference).

The worker result schema uses the native provider's supported output subset.
Local validation remains authoritative: implemented, verified_no_change and blocked results require
no spec gaps, replan results require at least one, and each requirement/design ID list must be
typed and unique. Unsupported conditional and uniqueness schema keywords are
not sent to the provider. A failed native process preserves its nonzero exit;
known schema rejection produces a fixed diagnostic without reflecting stderr.

Workers return `implemented` only after assigned work, validation and review
complete. A non-specification failure that leaves work or validation incomplete
returns `blocked`, with honest evidence and no invented specification gap. After
validating every result field, the sequential dispatcher stops later assignments
and returns exit 2, `WORKER_BLOCKED`, and the validated `worker_result` in its error
response. This evidence never enters the normal success result list. The
coordinator observes termination and owns diagnosis and authorized routing;
blocked work must not enter `task-finish`, admission or integration. The result
does not authorize an automatic retry or change existing budgets.
The sequential adapter similarly stops on `replan_required`, returning
`WORKER_REPLAN_REQUIRED` with validated evidence. Both native paths still ingest
the full result through `task-result`; neither adapter can bypass the receipt.

Before `task-start`, a zero-exit worker with exact `PENDING_START` state may
preserve validated blocked/replan output as diagnostic-only `prestart_result`.
The dispatcher still returns `WORKER_FAILED` and stops later tasks. Both input
and canonical result are capped at 64 KiB; identity, schema and sensitive-content
guards apply. Invalid, oversized or implemented output returns a fixed reason
without a payload. Terminal liveness and nonzero-exit failures take precedence.
This field is never `worker_result` and grants no receipt, completion, recovery
or replanning authority; the coordinator diagnoses the observed failure.

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

On an eligible non-default primary branch, both coordinators automatically create
an outer per objective. Inner execution fast-forwards that stable outer; combined
acceptance then validates the latest descendant aggregate plus the child before
source promotion and cleanup. Ordinary aggregate work may continue between those
short reservations. Dirty or switched source waits; rewritten source blocks.
See [automatic projects](automatic-projects.md) for shared claims, capacity,
correction and original-folder status/continuation.

Direct Implement execution on default or an unknown default retains its verified
feature checkout as local delivery. An explicitly supplied manual outer remains
externally owned and requires its separate integration handoff. Retain recovery
artifacts whenever safe cleanup cannot be proven.

SDLC retains remote Git prerequisites, phase gates, remediation budgets, managed
outer-worktree leases, UAT and exact-commit remote-action authorization. Post-promotion
documentation assessment is read-only; drift starts a corrective run. Local delivery
never authorizes a push, PR, merge or publication.

Report deterministic tests, installed hooks, fresh native-agent execution and live
application evidence separately. Unavailable runtime checks remain NOT_RUN/PARTIAL;
fixture intervention cannot establish a workflow pass.

The read-only `observe_pending_revision` helper validates an existing pending
design revision for independent classifier evidence. It checks current objective,
plan/basis, active counted design authority, immutable preparation and retained
termination/reconciliation proofs, exact integration registration and quiescence.
It never adopts a coordinator session or writes workflow state. Mutating prepare,
seal and reconciliation owners continue to require the bound coordinator.

## Document programs

Use [document delivery](document-delivery.md) for portable source coverage,
phase barriers, slice identity, bounded retention and conditional protected merge.
The same execution primitives serve ordinary singleton Implement and SDLC slices.

Combined-wave success requires the producer receipt from `validate-target`,
passed to `wave-complete --receipt`. Free-form success evidence is rejected.
Changed and no-change results share the full-wave admission, validation and
non-forced cleanup barrier. Historical completion verifies its original receipt.

## SDLC Statistics

`statistics.md` defines source-bound architectural inventory, optional task
reporting metadata and owner-observed attempt history. The existing SDLC status
branch uses the shared projector; Implement status is unchanged. Statistics are
private observations and never scheduling, acceptance or repair authority.
