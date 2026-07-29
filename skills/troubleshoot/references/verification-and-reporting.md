# Verification And Reporting

## Repair Standard

A durable repair restores the violated invariant at the narrowest correct
boundary. The explanation of the change should follow directly from the causal
chain.

Reject symptom masking such as:

- retries or timeout increases without a proven transient or blocked path
- sleeps or global serialization around a race
- disabled tests or swallowed exceptions
- permanent cache clearing
- broad refactoring during diagnosis
- dependency downgrades without identifying the incompatible contract

## Layered Verification

1. **Original reproducer:** passes with the original signature absent.
2. **Counterfactual:** the proposed causal condition is neutralized while
   intended behavior remains.
3. **Regression oracle:** fails against the faulty state and passes against the
   repaired state when feasible.
4. **Targeted checks:** cover the changed component and realistic edge cases.
5. **Boundary checks:** cover affected integration or system contracts.
6. **Dynamic diagnostics:** repeat relevant races, sanitizers, profilers,
   traces, or fuzzing.
7. **Repeated trials:** compare failure rate, signature distribution, timing,
   and variance for intermittent problems.
8. **Repository and target hygiene:** remove diagnostics and confirm no
   unrelated changes or residual unsafe state.

Never claim an unrun check passed. Record command, target, result, and any
coverage limitation.

## Outcome Classification

- `VERIFIED_FIXED`: causal mechanism is proven or high confidence, repair is
  applied, original reproducer and required adjacent checks pass.
- `MITIGATED_NOT_PROVEN`: impact is reduced or symptom is absent, but the cause
  or counterfactual is incomplete.
- `DIAGNOSED_NOT_FIXED`: cause is sufficiently established but repair was not
  authorized, safe, feasible, or completed.
- `BLOCKED_MISSING_EVIDENCE`: access, observability, reproduction, or safe
  experiment capability is missing; exact next evidence is named.
- `UNRESOLVED`: competing hypotheses remain without a decisive next result.

## Report Template

```markdown
# Troubleshooting Report

## Outcome
- Classification:
- Confidence:
- Current impact:

## Failure Contract
- Expected:
- Actual:
- Scope and signature:
- Reproduction or characterization:
- Timeline:
- Success criteria and constraints:
- Target, environment, blast radius, and allowed mutations:

## Evidence And Model
- Observed facts:
- Derived inferences:
- Unknowns:
- Affected and unaffected comparisons:
- Relevant execution and boundary path:

## Hypotheses And Experiments
| Hypothesis | Prediction | Experiment | Observation | Status |
| --- | --- | --- | --- | --- |

## Cause
- Earliest divergence:
- Causal chain:
- Counterfactual and reintroduction:
- Alternatives eliminated:

## Mitigation Or Repair
- Changes made:
- Authority and safety basis:
- Rollback or recovery state:

## Verification
- Original reproducer:
- Regression oracle:
- Targeted and boundary checks:
- Repeated or dynamic diagnostics:

## Residual Uncertainty And Next Action
```

For remediation-budget exhaustion, add `REMEDIATION_BUDGET_EXHAUSTED`, the
`attempt_limit` or `time_limit` stop trigger, and use these exact report section
names so the optional Stop hook can validate delivery: `## Outcome`,
`## Blocking Error`, `## Source`, `## Attempts`, `## Evidence`,
`## Current State`, and `## Next Action`. List all counted attempts and the
highest-information action available to the user or a later authorized tranche.
Use `Blocker: ...` under `## Blocking Error` and `Blocker key: ...` under
`## Source`. For every positional attempt, use
`- attempt-N | Remediation: ... | Verification: ... | Result: ...` and
`- attempt-N | Evidence: ...`. Include the exact redacted error class, code,
message excerpt, and failing operation when known; identify its component,
command, test, service, or bounded log location under `## Source`.
When the optional guard is active, use its bounded, redacted marker-derived
summaries in those fields; generic filler or detected sensitive values do not
satisfy report delivery.

If a retry stops earlier because no new evidence or genuinely new hypothesis is
available, use the same structured investigation content without
`REMEDIATION_BUDGET_EXHAUSTED`. Classify the outcome as
`BLOCKED_MISSING_EVIDENCE` or `UNRESOLVED`, state which retry-admission gate
failed, and identify the highest-information evidence needed next.

Do not use placeholders merely to satisfy the section headings. Every included
section must summarize the evidence-backed investigation state available at the
stop boundary, including failed attempts, residual uncertainty, and rollback or
runtime state where applicable.

The optional Stop hook requests one correction when the report is missing a
required section or marker-bound attempt and includes a bounded, redacted
minimum report for the assistant to return. If the continued response is still
incomplete, it stops instead of recursing indefinitely and emits that fallback
as a UI/event-stream warning, not an assistant-authored response. For a
historical exhausted v1 data marker that predates recorded `new_evidence`, the
fallback states that limitation rather than inventing evidence.

Keep reports public-safe when committed. Do not include secrets, private URLs,
internal hostnames, customer data, raw production logs, or environment-specific
credential paths.
