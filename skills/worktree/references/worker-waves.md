# Worker waves and acceptance

One coordinator owns one objective on one execution host. Workers use fresh
native contexts and exact-base worktrees. Independent coordinators may share a
repository through admitted sibling project scopes and the common capacity and
write-claim owner in `automatic-projects.md`. Equal/prefix scopes conflict.
Cross-host workers remain outside this contract. Worktrees share Git objects and refs and are not security sandboxes.
Native permissions, runtime leases and owner-specific cleanup remain required.

## Frozen policy

Before `prepare-specs` or `prepare`, the coordinator may invoke the private
`configure-policy` transition with `--worker-limit W`, `--wave-task-limit B`
and `--worker-worktree-limit P`. Defaults are W=4, B=2W and P=2B. Require positive
integers and W <= B <= P. The owner freezes `execution-policy.json`; its digest
joins the execution basis. Repeated configuration must match exactly. Missing or
changed policy after ownership starts requires recovery, never reconstruction.

W bounds active native workers. B bounds tasks in one immutable dependency wave.
P bounds physical worker worktrees plus reserved/uncertain allocations across
all executions in the run. The integration checkout has its own single slot;
prior owners must complete cleanup before replacement. Reserve the entire wave
before allocating its first worker. Queued reservations, active workers, completed
workers awaiting validation, superseded and interrupted resources consume budget.
Exact path, registration and ref absence is required to release physical capacity.
Reconcile retained resources through their owners; never delete to manufacture room.

The private `observe-capacity --native-slots N --evidence <bounded-reference>`
transition binds host, run, current coordinator and observation time. N means the
**total concurrent workflow workers currently supported**, including active
workflow workers, not additional free slots. Derive it from the actual native
host/session observation. It expires after 60 seconds. Zero waits; missing,
stale, foreign-session or unknown observations permit at most one total worker.
Refresh before arm/refill; reduced capacity drains existing workers. CPU count
is not a native concurrency limit. Keep the independent host-memory floor active.
Never infer observed parallel execution merely from configured capacity.

Verify fresh-context, explicit command-cwd and effective support-path permissions
before arming. Native tools can satisfy cwd through their execution tool and
paths through inherited grants; missing spawn setters alone do not require a
CLI fallback. Follow the shared [launch contract](workflow-contract.md).

## Frozen executable acceptance

SDLC also requires the separate [task acceptance policy](task-acceptance.md),
with checks before worker success and explicit deferred obligations. Implement
keeps the validation contract below. Requested integrated UAT follows the shared
[optional UAT policy](optional-uat.md); it is not a default worker obligation.

Alongside every locked Markdown plan, create `<plan>.validation.json` before
preparation. Its `workflow/validation-plan-v2` object has `wave_checks` and
`task_checks`, with exactly every task ID represented. Each nonempty check list
contains unique `id`, explicit `argv`, `execution_policy` (observation interval
and null deadline), and
`environment_keys` (only PATH, LANG, LC_ALL and TZ). `{python}` selects the current
Python executable. Commands execute at the target Git checkout root, including
for nested selected projects. Select the project directory explicitly inside
the command when required; relative executable paths also resolve at Git root.
No shell interpolation or secret values belong in this plan. For example:

```json
{
  "schema": "workflow/validation-plan-v2",
  "wave_checks": [{
    "id": "combined-tests",
    "argv": ["{python}", "-m", "unittest", "tests.test_feature"],
    "execution_policy": {"observation_interval_seconds": 30, "deadline": null},
    "environment_keys": ["PATH"]
  }],
  "task_checks": {
    "TASK-001": [{
      "id": "task-acceptance",
      "argv": ["{python}", "-m", "unittest", "tests.test_feature"],
      "execution_policy": {"observation_interval_seconds": 30, "deadline": null},
      "environment_keys": ["PATH"]
    }]
  }
}
```

The shared launch owner reads the worker's immutable execution basis and checks
the original validation-plan digest before embedding only that task's check
definitions and Git-root working directory in the prompt. Keep explicit argv,
timeouts and environment keys; `{python}` names the launch interpreter. Missing,
changed, foreign or symlinked evidence blocks launch. Do not borrow the current
coordinator's plan for an older assignment or ask workers to guess sidecar paths.
These definitions grant no early-check or retry authority and do not replace
independent acceptance after native termination.

Choose checks that actually prove task and combined acceptance. A zero-exit
placeholder, the worker's narrative or a file-existence check is insufficient
product evidence. The plan digest is frozen in the basis; changing it requires
the existing replan owner. Runner output is bounded and only digests enter receipts.

For a proven integrated `PLAN_DEFECT`, the existing failed-wave owner retains
the exact failed producer receipt and archived validation plan. After counted
repair admission, create an adjacent locked plan and a private
`<plan>.validation-review.json` with exactly these keys:

- `schema`: `workflow/validation-plan-review-v1`.
- `classification_id`, `dispatch_id`: the canonical active repair authority.
- `failed_receipt_sha256`, `prior_validation_plan_sha256`,
  `replacement_validation_plan_sha256`: compact canonical JSON digests using
  `workflow_validation.digest`.
- `replacement_plan_sha256`: the replacement Markdown bytes digest.
- `objective_sha256`: the current accepted objective revision digest.
- `requirements_sha256`, `design_sha256`: unchanged canonical spec digests.
- `criteria_unchanged`, `source_read_only`: both literal `true`, established by
  the coordinator's actual review of original and corrected commands.
- `summary`: a bounded explanation of the command defect, unchanged acceptance
  and equivalence review; no test-success assertion or sensitive material.

This path changes only combined-check `argv`. Preserve every wave check ID,
order, timeout and environment key, and all previously frozen task checks.
The execution owner archives the review and rechecks it at dispatch and
resolution. Other plan changes require their own supported route; a review
cannot waive a check. Append diagnosis-bound corrective tasks; the scheduler
places them before untouched future work without rewriting existing task
definitions. A command-only correction may return `verified_no_change`, with
independent task and combined acceptance, without a cosmetic commit. Failed
wave and original receipts remain immutable; successful correction adds a
separate resolution, then all invalidated gates run again.
Further counted command corrections retain each failed plan and review;
resolution checks their complete chain to the successful producer plan.

## Changed and unchanged results

Workers return `implemented`, `verified_no_change`, `replan_required` or `blocked`.
Both success proposals require empty spec gaps and complete evidence. A no-change
proposal says the exact frozen base already meets acceptance; it never authorizes
an empty or cosmetic commit. Normal worker start/heartbeat/deadline limits apply.
After independently observing termination and released runtime leases, ingest the
native result. For no-change, invoke `validate-target` with the exact run, execution,
wave and task before `task-finish`. Independent checks run after the worker stops.
They cannot renew or retroactively excuse a late worker attempt.

For blocked SDLC results, fresh-worker recovery requires the exact counted
classification and current target binding in
[`workflow/recovery-authorization-v2`](workflow-contract.md). Authorization and
transfer both recheck this authority. The classifier's private
`capture-worker-failure` operation derives the complete current binding from the
canonical blocked receipt. New failure records and repair dispatches recheck
that binding before writes or budget charges; historical replay stays idempotent.
Preserve original failure receipts; a
second blocked attempt requires its own classification. Interrupted workers
without a result and ordinary Implement use their existing recovery contracts.

An intervening repair can suspend the counted repair that owns a corrective
wave. After fresh worker verification and termination, complete the intervening
dispatch first. The classifier owner restores the exact parent and returns
`resumed_dispatch_id` only when all obligations are covered. Refresh checkpoint
pointers, produce fresh combined validation and complete the wave normally.
Active authority checks, failure history, budgets and later revalidation remain.

The internal result records status, verified head/tree, native receipt and optional
commit. An implemented task has one direct-child commit; no-change has no commit,
no changed paths and an independently admitted acceptance receipt. Both become
`finished`, release native slots only through admission, and participate in stable
`integrated_task_ids` order. No-change integration records disposition without a
merge. Dependencies become ready only after the full wave is validated and cleaned.

After `wave-integrate`, run `validate-target` without a task and pass its receipt
to `wave-complete --receipt`. This applies to mixed and entirely no-change waves.
Receipts bind run/execution/wave/basis, exact head/tree, complete task-result set,
frozen check definitions, evaluator and environment identities, and outcomes.
Producer admission is immutable; replay reconciles exact sealed artifacts without
rerunning checks or replacing historical proof. Long checks hold a dedicated
operation lock, leaving peer heartbeat and completion transitions available.
Evaluator lookup resolves relative and empty `PATH` entries against the target
checkout, so the executable identity records the program actually launched.

Every validation operation records intent before effects. The first failed check
stops the check sequence; failed or interrupted evidence remains. Use existing
failure classification and corrective replan ownership before another trial;
never erase an operation to rerun it. Final product qualification remains a
separate gate with its own target, environment and intervention semantics.
Running validators and unproven evaluator termination retain ownership: they
block coordinator transfer, replanning, retirement and target cleanup. Seal a
receipt only under the coordinator identity admitted at start. A stopped producer
alone does not prove evaluator termination; retain unknown ownership for recovery.

## Recovery and rollout

Native subprocess cleanup pins the process-group identity until final signaling,
checks descendant quiescence on success and failure, and retains resources when
termination is unproven. Escaped sessions, containers and remote jobs require their
own owner stop receipts. A direct child exit alone is insufficient.

Install both coordinators, shared Worktree helpers and verifier consumers together
after active writers are quiescent. Old coordinator/result/reservation schemas
fail with upgrade-required or invalid-state errors and retain their artifacts;
there is no migration shim. Deterministic Git and subprocess tests prove local
contracts. Disposable installation checks prove packaging only. Native overlap,
laptop resource behavior and larger-host operation require separate runtime trials.
