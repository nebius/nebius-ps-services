# Optional UAT and implementation admission

Both Implement and Agentic SDLC use this policy: **no UAT by default; requested
UAT runs at a meaningful, ready scope; unavailable UAT does not block
implementation or otherwise eligible PR/merge.** Core development checks,
producer/consumer correctness prerequisites, CI, review and publication authority
remain mandatory. Automated acceptance is evidence, not human business signoff.

## Accepted choice and timing

Normalize `uat` in the accepted objective. Omitted input becomes:

```json
{
  "schema": "workflow/uat-policy-v1",
  "requested": false,
  "backend": null,
  "scope": {"kind": "objective", "executions": []},
  "criteria": []
}
```

An explicit request uses `requested: true`, backend `existing`, `container` or
`nebius`, and optional selected criterion IDs. The default requested scope is the
whole accepted objective/release, after its implementation is ready. A deliberately
selected earlier coherent journey uses `scope.kind: milestone` and its execution
IDs. Do not ask a mandatory questionnaire or run UAT after every task/wave/slice.
Continuation preserves the choice. Material changes use accepted steering,
replanning and existing fences; workers cannot expand it.

The objective, execution basis and assignment carry the same policy and digest.
Program v2 also records `uat_criteria`: the optional acceptance catalog, independent
of whether the user requests its execution. Task policy v2 records each criterion's
`purpose: implementation|uat`. A dependency consumed for implementation correctness
must be `implementation`; it cannot be relabeled UAT to bypass a prerequisite.
Deferred implementation checks resolve `before_consumer` or `before_delivery`;
optional UAT uses `uat`. Preserve all debt and failed evidence.

## Environment and skill selection

| Accepted request | Behavior |
| --- | --- |
| No UAT request, with or without an environment | Record `not_requested`; finish development checks and eligible delivery. |
| UAT against an existing environment | Use its project harness and authorized access/reset procedure directly. Nebius provisioning is unnecessary. |
| Explicit `$test-software-in-container` campaign | Route to that owner at the agreed ready scope; use its isolated trial and cleanup evidence. |
| Explicit `$test-live-on-nebius` campaign | That skill owns authorized disposable provisioning, testing and cleanup; a pre-existing environment is not required. |
| Requested UAT, unavailable prerequisites | Record `pending` and a concise missing-prerequisite handoff; continue implementation and eligible publication. |
| Human testing after delivery | Retain candidate/scope/criteria and setup prerequisites for a later separately requested campaign. |

Both testing skills have `allow_implicit_invocation: false`. Providing a live
endpoint, mentioning a skill while discussing options, or requesting implementation
does not invoke either campaign. Established container-based development tests
remain development checks; this does not implicitly invoke the general testing
skill. Avoid assuming or promising a quantified time saving.

## Readiness and execution

First derive software readiness from all accepted execution outputs and required
corrections. A usable end-to-end scope needs its capabilities and dependencies;
an early unfinished task is not ready merely because a command can start.
Only then perform authorized expensive setup. A selected provisioning owner may
create its target at this point: target existence is not its invocation gate.
Execution IDs are labels, not delivery order. The promoted campaign candidate
must be clean and contain every selected promoted output or its protected,
verified merge result (including squash/rebase); separate uncombined
branches leave UAT pending. Whole-objective default coverage includes phase-only
acceptance criteria as well as slice and regression criteria. Explicit milestone
or criterion selection retains its declared scope.

Before assertions, the coordinator records candidate-bound facts for `environment`,
`access`, `test_data`, `scenarios` and `smoke` using private `uat-readiness`.
Missing facts remain pending. The declaration is not PASS evidence. Bind the
actual delivered checkout, including Implement's retained feature checkout.
For automatic aggregation, use the final aggregate candidate; the testing owner
may act in `aggregation-ready` while worker dispatch stays fenced.

Run private `qualify --execution OBJECTIVE` only for requested ready scope. Its
frozen `workflow/qualification-plan-v3` identifies the selected `backend`, reviewed
Python harness, criterion inventory, confirmed nonproduction target and bounded
operations. The harness implements `candidate`, `observe`, `criterion --criterion
ID`, and `cleanup`. Only the selected resource owner may perform cleanup; borrowed
environments remain intact. Cleanup returns the exact head/backend, a digest of
the owner's receipt, `writers_quiescent: true` and `owned_resources_absent: true`
for resources owned by this trial. Local process exit alone proves neither cloud
nor container cleanup. Preserve cleanup uncertainty and owner recovery paths.
Private `uat-recover-cleanup --candidate <file>` retries only cleanup for the
original retained campaign. Its candidate names the original `receipt`, `sha256`
and clean `checkout`; the frozen owner harness must independently prove writer
quiescence and resource absence. Recovery appends evidence, retains failures,
and never invents PASS or reruns assertions. Fencing new tests does not prohibit
reconciling already owned resources.

PASS requires all assertions, independent before/after observations, exact
candidate identity and observed cleanup. A later verifier validates the immutable
receipt and current source identity without requiring a cleaned environment to
remain online. A changed aggregate candidate requires new evidence.

## Delivery and retained outcomes

UAT states are `not_requested`, `pending`, `running`, `passed`, `failed` and
`incomplete`. Never fabricate PASS, N/A, runtime artifact hashes or environment
identities to unblock delivery. Missing optional prerequisites do not consume a
repair budget or keep Stop in a retry loop.

The separate `workflow_delivery_admission` owner verifies actual required task
and wave evidence, then executes frozen core checks on the exact final candidate.
Each program slice freezes an `implementation_coverage` map from non-UAT source
criteria to required check IDs. Selecting additional UAT never removes core
coverage, and task-local IDs cannot reclassify another slice. Private `admit-delivery` produces a
source/tree/objective/basis-bound receipt with honest UAT disposition. Phase-only
implementation checks use `admit-phase` with explicit frozen checks; optional
phase criteria do not require executed UAT. `phase-exit`, protected delivery and
program completion consume implementation admissions, not synthetic qualification.
Remote `workflow/sdlc-delivery-v2` attestations retain CI, review, exact SHA/base and
merge authority. Keep merge-before-next-slice scheduling.

A structured failed assertion blocks affected publication until repaired and retested
under existing budgets. Independent implementation may continue. A later narrower
PASS cannot erase larger unresolved failures, and a new trial cannot erase old
cleanup debt. Campaign events (`workflow/uat-event-v2`) retain the frozen identity
of each criterion. A covering PASS must match those identities: reusing criterion
IDs for changed requirements does not resolve the original defect. Cleanup
recovery preserves the same identities, including source-document and coverage
hash pairs as well as direct-objective digests. Unsupported earlier histories remain
intact and need their original owner for recovery; do not silently migrate them.
Already delivered slices receive forward repairs. An interrupted
but demonstrably cleaned campaign is `incomplete`, not PASS; it does not by itself
block eligible delivery. A nonzero harness exit without valid assertion results
is an execution failure, not a proven product defect. Active trials and unproven cleanup remain owner obligations.

Before terminal completion, retain `uat/handoff.json` with policy, exact source
commit/tree and disposition outside temporary checkouts. Later testing starts a
new campaign; it never reopens a completed run. PR/merge eligibility does not
establish issue acceptance or authorize closing unresolved criteria.
The handoff and campaign share the same scope-aware criterion resolver, including
phase-only and regression criteria. Saving the accepted testing inventory needs
no runtime setup or readiness; verify the frozen program digest before using it.

## Cutover and evidence boundary

Accepted-objective v3, run v4, execution-basis v3, assignment v3, task-policy v2,
program v2, qualification-plan v2/receipt v3, UAT-event v2, gate-review v2 and SDLC-delivery v2
form one coordinated source contract. Preserve unsupported frozen runs and their
cleanup evidence; provide fresh-run guidance without silent migration or shims.
Installing the coherent catalog and broker policy is a separate requested action.
Local deterministic checks do not prove installed activation or live qualification.
