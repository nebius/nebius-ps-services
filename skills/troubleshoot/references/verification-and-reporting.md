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

For live product verification, also follow `live-product-validation.md`. Bind
the proof to one declared candidate, target, checkpoint, and evidence lineage.
A successful exit or healthy target after an out-of-band mutation does not
prove the product fixed; require a clean replay from a declared or independently
proven known-good checkpoint before the earliest product divergence or
contamination, with product-owned transition evidence and independent
authoritative postconditions. Treat nominally read-only observation as
intervening when it can alter criterion-relevant state or execution.

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

`VERIFIED_FIXED` additionally requires all seven completion criteria below to
be `PASS`. Other classifications may contain honest `FAIL` or `UNKNOWN` rows.
Passing tests alone cannot establish Design, Infrastructure, Logs, or Relevant
code paths as `PASS`.

## Report Template

Every explicit `$troubleshoot` invocation uses this report, including success,
blocking, tool or coordination error, ordinary early stop, and unresolved work.
The optional hook records the duty in
`troubleshoot-report-obligation.json`, retains an undelivered duty across a
resumed turn in the same session, and validates
`Current workflow state: REPORTED`. It requests one correction and then emits
an honest bounded UI fallback rather than looping. If the host terminates
before Stop, no local hook can create an assistant response; the next resumed
same-session turn must report the interruption.

```markdown
# Troubleshooting Report

## Outcome
- Classification:
- Current workflow state: REPORTED
- Confidence:
- Current impact:
- Stabilization status:

## Failure Contract
- Expected:
- Actual:
- Scope and signature:
- Reproduction or characterization:
- Success criteria and constraints:
- Target, environment, blast radius, and allowed mutations:
- Included system boundary:
- Excluded system boundary:
- Exercised control and data paths:
- Incident-window start:
- Incident-window end:

## Architecture Verdict
- Observed technologies, versions, and deployment model:
- Configuration authorities:
- Components, dependencies, ports, protocols, and authentication:
- Control and data flows:
- Official vendor architecture comparison and verdict:

## Component Verification Matrix
| Component | Version and existence | Active configuration | Runtime health | Dependencies, authentication, and DNS | Resources and time sync | Restart history and recent changes | Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |

## Incident Timeline
| Time | Source and clock basis | Correlation identifier | Event | Evidence or inference |
| --- | --- | --- | --- | --- |

## Logs Examined
| Layer | Source | Window and filters | Finding | Coverage status |
| --- | --- | --- | --- | --- |
| Component | | | | examined / unavailable / unsafe / not applicable |
| Application or job | | | | examined / unavailable / unsafe / not applicable |
| Container or orchestrator | | | | examined / unavailable / unsafe / not applicable |
| Service manager | | | | examined / unavailable / unsafe / not applicable |
| OS and kernel | | | | examined / unavailable / unsafe / not applicable |
| Network and firewall | | | | examined / unavailable / unsafe / not applicable |
| Storage | | | | examined / unavailable / unsafe / not applicable |
| GPU or hardware | | | | examined / unavailable / unsafe / not applicable |

## Hypotheses And Experiments
| Hypothesis | Prediction and falsifier | Bounded experiment | Observation | Decision |
| --- | --- | --- | --- | --- |

## Code Debugging
- Reproduction and execution or data path:
- Stack trace, core dump, or equivalent runtime evidence:
- Configuration, environment, and data inputs:
- Recent changes and affected or unaffected comparison:
- Focused tests, static or dynamic analysis, and instrumentation:
- Instrumentation cleanup and limitations:

## Root Cause
- Earliest divergence:
- Causal chain:
- Counterfactual and reintroduction:
- Alternatives eliminated:
- Confidence:

## Remediation
- Design classification and handoff:
- Changes made:
- Authority and safety basis:
- Rollback or recovery state:

## Post-Fix Validation
- Original reproducer:
- Regression oracle:
- Targeted and boundary checks:
- Repeated or dynamic diagnostics:
- Live trial status and claim scope:
- Candidate, target, checkpoint, and replay range:
- Intervention ledger and first contaminated boundary:
- Product-owned transitions and independent postconditions:

## Completion Gate
| Criterion | Verdict | Evidence | Gap or next action |
| --- | --- | --- | --- |
| Design | PASS / FAIL / UNKNOWN | | |
| Infrastructure | PASS / FAIL / UNKNOWN | | |
| Connectivity | PASS / FAIL / UNKNOWN | | |
| Configuration | PASS / FAIL / UNKNOWN | | |
| Runtime health | PASS / FAIL / UNKNOWN | | |
| Logs | PASS / FAIL / UNKNOWN | | |
| Relevant code paths | PASS / FAIL / UNKNOWN | | |

## Remaining Unknowns And Residual Risks
- Unknowns and coverage gaps:
- Residual risks:
- Exact next action:
```

The completion table must contain exactly one row for each named criterion and
only `PASS`, `FAIL`, or `UNKNOWN`. Every row needs evidence and a substantive
gap or next action. A `PASS` row uses the exact evidence cell below and exactly
`None after scoped verification.` in the final cell:

| Criterion | Exact `PASS` evidence cell | Cross-validated proof state |
| --- | --- | --- |
| Design | `Verified: Architecture Verdict.` | Every Architecture Verdict value starts `PASS:` |
| Infrastructure | `Verified: Component Verification Matrix.` | Version/existence, resources/time, and evidence cells start `PASS:` for every row |
| Connectivity | `Verified: Component Verification Matrix.` | Dependencies/authentication/DNS and evidence cells start `PASS:` for every row |
| Configuration | `Verified: Component Verification Matrix.` | Active-configuration and evidence cells start `PASS:` for every row |
| Runtime health | `Verified: Component Verification Matrix.` | Runtime-health, restart/recent-change, and evidence cells start `PASS:` for every row |
| Logs | `Verified: Logs Examined.` | Sources and findings are affirmative; coverage is `examined` or `not applicable` |
| Relevant code paths | `Verified: Code Debugging and Post-Fix Validation.` | Every Code Debugging and Post-Fix Validation value starts `PASS:` |

A `FAIL` or `UNKNOWN` row must name a real gap or next action instead of using
the no-gap sentinel. Free-text evidence cannot override the referenced
structured state, and a structured token cannot override referenced detail that
explicitly reports absent, missing, unavailable, unexamined, unverified,
unproven, unknown, incomplete, insufficient, or uncollected evidence.
`VERIFIED_FIXED` is invalid unless all seven verdicts are `PASS`. An unavailable
source is `UNKNOWN`, not `PASS`. A "no issue found" result remains `UNRESOLVED`
or `BLOCKED_MISSING_EVIDENCE` unless every required criterion is supported;
name all coverage gaps.

The report validator requires each of the eight canonical log layers exactly
once, in the order shown, with one lower-case canonical coverage status. It
rejects missing, duplicate, unknown, or reordered layers even when the
completion verdict is not `PASS`. Scope every conclusion to the declared
included components and dependencies, exercised paths, and incident window;
do not generalize beyond that boundary or observed period.
Every evidence-bearing component and log cell must be substantive on its own;
other cells in the row cannot make a placeholder count as evidence. Text after
`PASS:` must also be substantive before the structured state can pass.

For remediation-budget exhaustion, keep this same canonical envelope. Add
`REMEDIATION_BUDGET_EXHAUSTED` and `- Stop trigger: attempt_limit` or
`- Stop trigger: time_limit` under `## Outcome`; add `- Blocker: ...` and
`- Blocker key: ...` under `## Root Cause`; list every marker-derived
`- attempt-N | Remediation: ... | Verification: ... | Result: ...` under
`## Remediation`; and list every `- attempt-N | Evidence: ...` under
`## Post-Fix Validation`. Include the highest-information next action and mark
completion criteria `FAIL` or `UNKNOWN` as the evidence supports. The optional
guard uses bounded, redacted marker-derived summaries; filler or sensitive
values do not satisfy report delivery.

If a retry stops earlier because no new evidence or genuinely new hypothesis is
available, use the same canonical report without
`REMEDIATION_BUDGET_EXHAUSTED`. Classify the outcome as
`BLOCKED_MISSING_EVIDENCE` or `UNRESOLVED`, state which retry-admission gate
failed, and identify the highest-information evidence needed next.

Do not use placeholders merely to satisfy the section headings. Every included
section must summarize the evidence-backed investigation state available at the
stop boundary, including failed attempts, residual uncertainty, and rollback or
runtime state where applicable.

The optional Stop hook requests one correction when the report is missing a
required section, completion row, or marker-bound attempt and includes a bounded, redacted
minimum report for the assistant to return verbatim as the whole response. Do
not prefix, enrich, or paraphrase its exact marker-derived fields. If the
continued response is still
incomplete, it stops instead of recursing indefinitely and emits that fallback
as a UI/event-stream warning, not an assistant-authored response. For a
historical exhausted v1 data marker that predates recorded `new_evidence`, the
fallback states that limitation rather than inventing evidence.

Keep reports public-safe when committed. Do not include secrets, private URLs,
internal hostnames, customer data, raw production logs, or environment-specific
credential paths.
