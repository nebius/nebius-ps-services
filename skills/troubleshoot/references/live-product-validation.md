# Live Product Validation

Use this contract when Codex runs a product against a live target and the
result may be used to claim that the product works. The target may be local,
test, development, staging, or production. This contract changes proof, not
mutation authority.

## Three Independent Dimensions

Do not classify a failure from the visible target state alone. Record these
dimensions separately:

| Dimension | Values | Decision it controls |
| --- | --- | --- |
| Causal owner | product, environment, test or harness, evaluator, policy or human, unknown | where the durable repair belongs |
| Target state | healthy, degraded, unsafe or stuck | whether stabilization or recovery is needed |
| Evidence lineage | clean, intervened | whether the run can prove product behavior |

An unhealthy target caused by the product is still a product defect plus a
recovery need. It is not an environment defect merely because infrastructure
is now degraded.

## Declare The Trial Contract

Before the first mutating verification run, record:

- the exact behavior or criterion under test and the scope of any success claim
- the candidate source commit, package, image, or other deployable identity
- the exact target and authoritative product-supported checkpoint or baseline
- the declared product workflow, including allowed CLI, API, UI, prompts,
  approvals, resume commands, and expected product-owned steps
- harness-owned setup and reset actions
- recovery-only actions and interfaces that are outside the tested workflow
- the independent verifier and authoritative postconditions
- current mutation authority, blast radius, rollback, and stop conditions

Freeze this declaration for the trial before mutation. Any later change starts
a new trial and a new evidence lineage; it cannot retroactively make an earlier
outside action product-owned or cleanse intervened evidence.

Product ownership is criterion-relative. A documented operator failover is a
valid product action when the criterion explicitly includes operator-driven
failover. The same action is an intervention when it bypasses a criterion that
expects automatic failover.

## Intervention And Evidence Lineage

Observation is non-intervening only when it cannot alter criterion-relevant
state or execution. Classify nominally read-only actions by their effect: queue
consumption or acknowledgment, lease refresh, lazy initialization, cache
warming, rate limiting, and timing or load changes may intervene. Any action
outside the declared product workflow intervenes when it performs, bypasses,
or pre-satisfies a product-owned step relevant to the criterion.

When intervention occurs:

- mark the affected trial segment `intervened` immediately
- record the actor or source, action, timestamp, before and after identities,
  affected state, rollback, and first contaminated boundary
- treat dependent downstream evidence as contaminated until a new clean
  lineage begins from a validated baseline
- retain earlier evidence only when it is demonstrably independent of the
  contaminated state

Keep the record public-safe. Do not store secret values, private endpoints,
customer data, or broad raw logs.

Authorized emergency stabilization takes priority over preserving a clean
experiment when delay would prolong unsafe or damaging state. Preserve evidence
first when safe, stabilize within existing authority, and mark the trial
intervened. Authorization to recover never makes the resulting evidence clean.

## Repair The Proven Owner

Classify the owner from the accepted product contract and causal evidence:

- For a product-owned implementation, reconciliation, migration, generated
  configuration, default, or workflow defect, repair authoritative product
  source or configuration. A design change counts only after it is implemented
  and deployed.
- For an environment prerequisite, external dependency, fixture, test, harness,
  or evaluator defect outside the product contract, repair that owner and rerun
  the product. Do not describe the result as a product fix.
- If the product was required to detect, report, tolerate, or recover from the
  external condition and failed that contract, the handling failure is
  product-owned even if the trigger was environmental.
- For policy, authority, or human-owned decisions, stop at the existing approval
  boundary.
- For unknown ownership, continue diagnosis or report the exact missing
  evidence. Do not choose a repair owner from the symptom alone.

Existing authority remains unchanged. Confirmed non-production may receive
bounded reversible changes. Production and unconfirmed targets remain
read-only without exact authorization. Destructive, irreversible, credential,
IAM, data, public-exposure, deletion, material-cost, and material-availability
changes require action-specific approval in every environment.

## Establish A Clean Replay

After owner-correct repair and any required recovery:

1. Select the nearest declared or independently proven known-good
   product-supported checkpoint. It must precede the earliest product
   divergence or the first contaminated boundary, whichever came first. Do not
   infer it from terminal output or task state alone. If no such checkpoint can
   be established, recreate the baseline or report
   `BLOCKED_MISSING_EVIDENCE`.
2. Verify the exact candidate and checkpoint identities and prove earlier
   writers, controllers, background jobs, and operators are quiescent or
   otherwise unable to complete the tested transition.
3. When the criterion expects a transition, restore a precondition that has not
   already been satisfied by recovery. When the criterion is idempotent
   reconciliation, prove the product evaluated and validated the state rather
   than accepting exit status alone.
4. Let Codex operate only the declared product workflow for the affected
   segment. Normal product prompts and approvals remain valid product actions.
5. Observe product-owned transition evidence and independently verify the
   authoritative postconditions in a fresh bounded window.
6. For intermittent defects, run enough clean trials to support the claimed
   confidence.

A successful exit, healthy final state, or idempotent no-op after manual
pre-satisfaction does not prove the product fixed. If a clean replay cannot be
established, report `MITIGATED_NOT_PROVEN` or `BLOCKED_MISSING_EVIDENCE`.

Checkpoint replay verifies only the affected segment and its dependent
boundaries. Claim end-to-end workflow success only after an end-to-end run.

## Outcomes And Reporting

Use the existing troubleshooting outcomes and qualify the owner and claim
scope:

- `VERIFIED_FIXED` with product ownership requires an implemented product
  repair, clean replay, product-owned transition evidence, and independent
  postconditions.
- `MITIGATED_NOT_PROVEN` covers recovery, symptom removal, an intervened run,
  a pre-satisfied or no-op replay, or incomplete causal proof.
- `DIAGNOSED_NOT_FIXED` applies when the owner is proven but its repair was not
  completed or authorized.
- `BLOCKED_MISSING_EVIDENCE` applies when baseline, identity, quiescence,
  transition, or independent verification evidence cannot be established.
- `UNRESOLVED` applies when competing owners or causal explanations remain.

For a non-product owner, state that the environment, fixture, test, harness, or
evaluator was repaired and that product behavior was revalidated. Never
attribute that repair to product source.

The final report records the trial status and scope, candidate and target
identity, checkpoint, declared workflow and recovery boundary, intervention
ledger and contamination boundary, replay range, product transition evidence,
independent verification window and postconditions, and residual uncertainty.
This is a prose evidence contract, not a new persistent schema or hook marker.
