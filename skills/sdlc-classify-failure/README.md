# Classify Failure

The repair owner's private `capture-worker-failure` operation derives a blocked
worker event from the exact current receipt, integration commit and complete
checkpoint identity/fingerprints. New records and dispatches reject stale context
before effects. Recapture cannot bind the same receipt to a changed context;
historical event and charged-dispatch replay remain idempotent.

`sdlc-classify-failure` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Normalize failures, validate optional troubleshooting diagnoses, enforce
repair budgets and invalidations, and select the shortest evidence-backed owner
or stop route before any SDLC retry.

## Main Boundaries

- Retry without classification.
- Overwrite old failure history.
- Persist raw secrets, logs, or private data.
- Route to merge or PR creation.
- Route an ambiguous evaluation failure directly to implementation or design.
- Treat missing evidence or failure to find an implementation bug as design
  proof.

## Primary Inputs

- Current feature and phase.
- Failure evidence.
- Retry counts.
- Latest validation, test, evaluation, documentation, UAT, PR, or policy
  output.

## Output

- Immutable failure, diagnosis, design-approval, and classification records
  are stored in private run state.
- The deterministic repair-control helper records the stable blocker, route,
  invalidations, attempts, ceilings, and diagnosis pointer under one
  process-safe per-feature transition lock.
- Known mechanical owners bypass troubleshooting; ambiguous evaluation
  failures enter it once and every diagnosis returns through classification.
- The next owner or precise stop condition is explicit.

Before execution preparation, proven mechanical validation failures return to
their exact supported authoring phase through counted localized repair. This
covers requirements, context, design, steering, planning and preparation
artifacts. Unsupported phase identities stop; no plan or integration checkout
is fabricated. Candidate formatting does not authorize architectural redesign,
and canonical specifications still use their owning publisher. Prepared
validation and all repair budgets remain unchanged.

After a failed direct repair, a complete `diagnosis-v3` result
`validation_defect` returns through classification to that same validation
owner. It requires structured causal proof, a bounded repair target, affected
files and the original regression check. The next dispatch consumes the existing
budget and retains the earlier failure. Older active diagnosis records fail
closed without migration; historical reports remain intact. Active control and
referenced classifications validate diagnosis version, digest and execution
identity before dispatch or completion writes. Advancing the current event does
not make a retired diagnosis valid authority again.

A proven `plan_defect` diagnosis covers frozen validation-command mistakes.
Integrated recovery retains the exact failed producer receipt and uses counted
plan revision with an immutable equivalence review. It preserves original
acceptance, task history and all earlier failed attempts.

## Deterministic Helper

Input records use whole-second UTC `created_at` values in
`YYYY-MM-DDTHH:MM:SSZ` form, without fractional seconds or numeric offsets.
The helper's error and input help describe this existing accepted format.
For blocked-worker recovery, copy every current `fingerprint_ids` entry into
the event mapping, including context when present. Compare the full mapping
before recording the immutable event or charging dispatch; an incomplete subset
cannot authorize recovery, even when the other digests match.

Use `scripts/repair_control.py` for `record-failure`, `record-diagnosis`,
`record-approval`, `classify`, `dispatch`, `complete`, and
`record-revalidation`, and `record-environment-recovery`. A broader redesign is admitted only when
`record-approval` has stored an immutable
`design-approval-v1` bound to the exact event, normalized design gate, and
digest-verified explicit approval excerpt in a private user-input snapshot. The
helper derives `failure-log.md` from structured records and fails closed on
duplicate drift, malformed or sensitive evidence, unsupported redesign,
repeated direct repair, semantic
cycling, and exhausted blocker or feature budgets.

Classification, remediation dispatch/completion and revalidation-route refresh
select the private execution with `--execution`. The dispatcher passes that
identity unchanged to the owning operation for both feature and delivery-slice
executions; command-level tests cover the same interface used by native agents.

Use `integrated_wave` when task integration is complete but combined validation
has failed. Its proven localized diagnosis and counted dispatch authorize the
execution owner's failure retirement and corrective plan. Immutable dispatch
receipts allow later resolution checks without resetting the repair budget or
changing the original failure.

`DOCUMENTATION_DRIFT` before sealing routes to `sdlc-update-documents` and
invalidates documentation, alignment, and commit evidence. Once execution is
sealed, promoted, or completed, it routes to `sdlc-create-plan` for a new
corrective run from the promoted commit with fresh validation and downstream
gates; it never reopens the retired integration checkout.

A new failure or diagnosis archives the previous revalidation cursor and its
authorizing context before replacing the active projection. Routed
classifications carry immutable references to prior invalidations and the
canonical ordered union of earlier and newly required gates. A successful
counted repair restarts that entire sequence under its own dispatch, with no
reused gate receipts or commit binding. Earlier receipts remain historical;
exact replay preserves current progress and counters. The new blocker keeps
its own failure type, route and budget. Environment-only recovery still rejects
unrelated pending invalidations. The last allowed dispatch may finish its
revalidation; another dispatch and over-limit state remain blocked.

When a new independent failure interrupts a counted pending repair,
`repair-control-v3` retains its full context in immutable suspended evidence.
After the intervening worker has performed and verified the actual repair,
record its successful completion before the original corrective wave completes.
The owner returns `resumed_dispatch_id` and restores the pending parent only
when its ordered gates cover every remaining invalidation. Refresh checkpoint
repair pointers from the returned control. Both budgets, the child completion
and deferred cursor, and all failure evidence remain. No historical receipt
becomes active by itself; no new dispatch is charged. Failed, changed, exhausted
or broader obligations stop restoration. The original wave still needs fresh
combined validation. Reaching the child's time limit persists its consumed time
and exhausted status before rejecting completion; even a zero-time retry cannot
restore the parent or reset the budget. The original repair's later completion
reruns all invalidated gates. Completion replay cannot rewind a later owner.
Completion journal entries retain their canonical shape; continuation links live
in immutable resumption records. Read-only recovery verification consumes those
owner-validated records without restoring authority or advancing revalidation.
Classifier observation of a failed integrated wave uses a separate read-only
repair-owner query. The exact pending parent may remain suspended if a bounded
immutable chain connects it to the current counted child at the same integration
commit. Validate carried obligations, increasing feature counts and active attempt
limits; reject missing, reordered, tampered, exhausted or terminal lineage.
This query does not resume repairs or grant the parent's mutation authority.
The verifier separately requires clean integration and writer/lease quiescence.
Obsolete v2 control state
is rejected without conversion; install the source repair before a fresh run.

Without a suspended parent, a successful repair with invalidations enters
`revalidation_required`.
`record-revalidation` advances one ordered gate at a time using immutable
`revalidation-evidence-v2` whose source must be a structured phase-owned
`passed` result. The helper verifies its file digest, current workflow
fingerprints, immutable classification, successful dispatch, and cursor
identity. Pre-commit evidence must match the live integration HEAD. Final
commit evidence must match completed promotion, the clean promoted checkout
on its recorded base branch, and verified integration cleanup. The helper
records `resolved` only after the cursor has cleared every invalidated surface.

A proven `ENVIRONMENT_DEFECT` during `active_task` at exactly
`sdlc-implement-plan` now routes through `existing_task_attempt`. Establish an
in-scope remedy and new evidence/hypothesis, then consume a counted localized
repair dispatch before authorizing the shared fresh-worker recovery. Preserve
the blocked receipt and observe termination and runtime release. Rerun the
original failed operation through an authorized mechanism, complete the
assignment's validation and normal task completion, then record the actual
remediation result before completing an interrupted parent's corrective wave. Success requires the existing downstream revalidation
cursor; it does not erase failure history or grant permission. Other active
phases, policy/human blockers and later lifecycle boundaries gain no authority.

Every blocked SDLC worker now needs active counted repair authority bound to
its exact receipt and current target before same-assignment recovery. Set
`evidence_digests.worker_result` to `sha256:` plus the canonical `receipt_sha256`
from `task-result`. Authorization and ownership transfer independently recheck
the current classification, dispatch, integration commit and fingerprints.
Implementation/environment `existing_task_attempt` and validation repair through
`sdlc-validate-codes` are permitted; other classifications retain their revision
routes. Keep the dispatch active until the worker performs and verifies the
repair. A changed blocked attempt cannot reuse earlier authority. See the
[shared recovery contract](../worktree/references/workflow-contract.md).

For an externally recovered environment block after completed waves, the
private `record-environment-recovery` transition requires the original gate to
pass on the same clean integrated commit with unchanged fingerprints. Its
digest-bound phase evidence resolves that block without consuming or resetting
repair attempts. The sole resolved invalidation is archived in immutable
recovery evidence and removed from the pending projection; other pending
invalidations block recovery. Failure history remains, and no earlier failed
test lifecycle is relabeled as passing. Other blocker classes and later
promotion states cannot use this transition.

Environment records preserve the exact failed phase. Pending invalidations use
the canonical gate surface, resolved from an existing surface name or its exact
owner in `REVALIDATION_ROUTES`. Unsupported phases remain blocked without a
recovery mode and retain earlier immutable obligations; they do not gain a new
gate route.

The alignment gate is owned by `align`. After a source repair changes gate
owner routes, the private `refresh-revalidation-routes` action requires the
exact pending cursor and an evidence reference. It verifies the original repair
authority and unchanged invalidated surfaces, archives the old cursor and
progress, and resets all gates to pending under the current policy. Immutable
evidence and repair budgets remain unchanged. A retry preserves any new gate
progress; this is not a general reset or an alias for retired skill names.

Canonical owner-issued pre-effect formatting feedback has a separate two-submission,
300-active-second allowance. See [input correction](references/input-correction.md).
Missing criteria, semantic changes and effectful failures retain normal repair budgets.
