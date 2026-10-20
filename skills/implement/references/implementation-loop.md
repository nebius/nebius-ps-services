# Run-owned implementation execution

Native permission must cover Git metadata and run-owned private state before
execution. Default Codex `workspace-write` protects `.git` even in a writable
workspace. Use an operator-authorized scoped permission profile or report
`ENVIRONMENT_BLOCKER`; never change or bypass controls from the product workflow.

After accepting a new objective, run the shared private transition
`prepare-specs --run-dir <run> --project-root <project> --feature FEAT-001`.
It allocates the run's sole integration checkout from the accepted committed
source, with no plan, execution basis, waves or workers. Initial canonical
publication belongs to this checkout and requires the bound root's current
objective digest; omit the basis digest until one exists. Preserve source HEAD
and source dirt. Never publish initial specs into the source after acceptance.
For new canonical files, verify that only the pair changed and stage with
integration-root `git add -A` before settling impact. Ignored canonical files
must be corrected through their owning configuration, never force-added.

Lock the plan and invoke shared `prepare` after impact settlement. It verifies
the paired publication receipt, journals the exact tree, and commits only the
canonical pair before freezing the execution basis and waves. No-change work
creates no spec commit or new documents. Workers inherit the committed
integration tip. Interrupted allocation retries `prepare-specs`; interrupted
sealing retries the same `prepare` with the same locked plan. Foreign resources,
unrelated integration changes, unjournaled commits or changed bootstrap
objectives retain evidence and fail closed. Fenced/cancelled runs cannot prepare
or dispatch; retain their recorded resources for explicit reconciliation.

Use one integration checkout per run, internal FEAT-001 execution identity,
fresh task worktrees and shared Worktree Git transactions. Lock plans under the
private run plans directory with heading `# FEAT-001 Plan vN`, a sibling `.lock`,
and contiguous TASK-NNN sections. Each task declares Requirements (or none),
Goal, Depends on, Write claims (exact/prefix), Conflict domains, Validation,
Done criteria and Rollback or stop conditions. Dependencies and conflicting
resources form deterministic frozen waves. Do not modify a locked plan.

Before locking IDs, audit each task for self-contained execution context.
The coordinator translates the accepted objective and canonical specs into the
task's concrete interfaces, schema/data types, fixed values, resource constraints,
required inputs and acceptance checks. Put these in Goal, Validation and Done
criteria so they survive assignment serialization. Requirement IDs and a pointer
to the full objective do not replace those details. Check the serialized
assignment against its canonical task before arming dispatch. Missing information
requires plan correction before any worker starts; do not repair that omission
by sending unbound extra instructions to a running worker.

The immutable basis binds required workflow references, supporting owner
instructions, and the worker result schema alongside helper code. Changes make
the basis stale; missing or symlinked contract files are invalid. Reconcile
the execution basis before dispatch rather than accepting changed inputs.

The ready queue has ready_task_ids, active_task_ids and admitted_task_ids.
Native termination and independent result admission free a slot; wave-refill
may dispatch another independent task while peers continue. All assignments
retain the same wave base. Integration waits for the full wave, merges in stable
task order, runs combined validation and removes exact clean worker resources.

Record observed native capacity in host-capacity.json, schema
workflow/host-capacity-v1, with native_slots and bounded evidence. The effective
limit is at most four and at most configured/host capacity; no observation means
serial execution. Record actual native intervals to substantiate overlap.

Coordinator-owned termination receipts bind assignment_digest,
worker_session_hash, observer_session_hash, native_handle, status=terminated
and a bounded observation of the native exit. They live under the execution
terminations directory. Never write one before the native worker has exited.
Task admission verifies result, ancestry, paths and immutable basis independently.

Use an exclusive assignment-bound resource lease before shared live-runtime
operations. A resource owner must release after its operation and quiescence;
no process-name matching, guessed ownership or implicit lease stealing.

Source/default identity and final delivery are distinct. A clean unchanged
non-default source fast-forwards through the existing promotion transaction.
An unknown/default source receives a verified feature branch/checkout as the
local deliverable. Dirty source content remains untouched. A source drift
blocker retains the exact verified integration for later reconciliation.

For a managed outer checkout, invoke the shared `release-outer-lease` transition
after final alignment, acceptance validation and documentation evidence. Then
complete the run through intake `complete`; it independently checks the actual
outer lease release. Return the ordinary explicit Worktree integration handoff.
Local workflow completion never performs the outer owner's integration.
