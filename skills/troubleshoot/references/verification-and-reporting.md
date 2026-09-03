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

- `VERIFIED_FIXED`: complete end-to-end failure-contract proof. The causal
  mechanism is proven or high confidence, the owner-correct repair is applied,
  the original reproducer and adjacent checks pass, and no material item
  remains unverified inside the declared scope.
- `DIAGNOSED-FIXED`: the causal owner and earliest divergence are proven, the
  owner-correct repair is applied, and the original reproducer, focused
  regression, and source or affected-boundary checks pass for the named fixed
  scope. Installation, deployment, restart, or live replay may remain
  explicitly unverified.
- `MITIGATED_NOT_PROVEN`: impact is reduced or the symptom is absent, but the
  cause, counterfactual, or durable repair proof is incomplete.
- `DIAGNOSED_NOT_FIXED`: the cause is sufficiently established but no
  owner-correct repair was authorized, safe, feasible, or completed.
- `BLOCKED_MISSING_EVIDENCE`: decisive access, reproduction, or safe experiment
  capability is missing and no safe alternative remains.
- `UNRESOLVED`: competing hypotheses remain without a decisive next result.

`FAIL` and `UNKNOWN` are evidence states, not automatic stop reasons. Continue
through discovery, modeling, or bounded experiments while another safe result
can change the decision. Do not apply another remediation without new evidence
and a genuinely new falsifiable hypothesis. Report early only for a real
authority or safety boundary, unavailable decisive evidence with no safe
alternative, explicit user stop, or exhausted remediation budget.

## Internal Evidence And User Projection

Maintain the detailed architecture verdict, component matrix, incident
timeline, eight-layer log ledger, hypothesis history, code-debugging record,
post-fix validation, and completion verdicts as working evidence. Each
canonical log layer remains exactly once as `examined`, `unavailable`,
`unsafe`, or `not applicable`. Missing evidence stays `UNKNOWN`; a neighboring
status or aggregate health result cannot turn it into a pass.

Keep one internal completion verdict for each of: Design, Infrastructure,
Connectivity, Configuration, Runtime health, Logs, and Relevant code paths.
Record each as `PASS`, `FAIL`, or `UNKNOWN` with evidence and a gap or next
action. These verdicts constrain classification and guide continued work, but
they are not mandatory rows in the ordinary user-visible report.

Project only decision-relevant facts into the ordinary user-visible report:
classification, confidence, fixed scope, current state, root cause, changes
made, verified and unverified scope, and the exact next action. Do not make
internal ledger formatting a Stop prerequisite.

## Concise Report Template

```markdown
# Troubleshooting Report

## Outcome
- Classification:
- Confidence:
- Fixed scope:
- Current state:

## Root Cause And Fix
- Root cause:
- Changes made:

## Verification
- Verified:
- Not verified:

## Next Action
- Owner:
- Action:
- Done when:
```

Use `High`, `Medium`, `Low`, or `Unknown` for confidence. Every narrative
field must be substantive and public-safe. `DIAGNOSED-FIXED` requires an
affirmative fixed scope, causal statement, applied change, and verification.
`VERIFIED_FIXED` additionally requires exactly `None within the declared
scope.` under `Not verified`.

Add `## Evidence Appendix` only when requested, decision-relevant, needed for
a live or production claim, or required at budget exhaustion. The appendix
may summarize `PASS`, `FAIL`, and `UNKNOWN` evidence without changing the
outcome solely because an item is unknown.

## Hook Behavior

Every explicit `$troubleshoot` invocation creates session-private
`troubleshoot-report-obligation.json`. A host interruption before Stop can be
reported only after a resumed turn in the same session. On an ordinary
non-exhausted Stop:

- a valid concise report is finalized transactionally after peer Stop policy;
- an incomplete, malformed, partial, `FAIL`, or `UNKNOWN` report records
  `advisory_incomplete` and returns `continue: true`;
- the hook does not request another agent turn, deny later tools, or emit a
  generated fallback for ordinary report quality.

Prefer local evidence labels as plain inline-code repository-relative slash
paths with an optional positive line suffix. Inline code and Markdown targets
may use repository-relative, platform-native absolute, home-relative, or local
`file:` syntax only when their decoded canonical destination remains inside the
Git repository root derived from the event working directory. This root, not a
selected subproject, is the boundary, so sibling-project evidence is valid.
If no Git root can be proven, absolute, home-relative, and local `file:` forms
are unsafe because their containment boundary is indeterminate.

For `file:`, require an empty authority and an absolute local path. Reject
credentials, query or fragment components, control bytes, malformed or
residual encoded traversal and separators, UNC or remote targets, non-native
separators or path syntax, parent traversal, and symlink escape. Parse balanced
or escaped Markdown destinations and optional titles; treat ambiguous link
syntax and renderer-active schemes as unsafe. Resolve existing symlinks; use
lexical containment beneath the deepest existing ancestor for a nonexistent
target. A proven-contained target span may be excluded from the generic
home-path check, but its raw and decoded target, label, surrounding prose, and
all other content remain subject to secret, private-host, private-IP, and
credential checks. Containment validates the address only and never certifies
referenced file content as safe.

A safe target may be delivered. A contained wrapper or authoring defect is
`advisory_incomplete`; ordinary dotted words, slash commands, and route-like
prose are not references. An outside-root target, escape, unsafe URI, or
independently sensitive value is terminal. Strict exhausted-report fallback
uses the same resolver but normalizes contained absolute, home-relative, and
URI targets to repository-relative form and redacts unsafe or indeterminate
values. It replaces over-limit Markdown, inline-code, or URL values atomically
instead of truncating through their syntax. Public HTTPS links remain subject
to private-host checks.

A report containing a secret, private endpoint, internal hostname, customer
data, uncontained local user path, or unsafe reference atomically records
`sensitive_detected` and stops with one generic non-report warning. The hook
requests no automatic replacement report and emits no second warning on
repeated evaluation. This is terminal detection, not sanitization or
suppression of output already rendered by the host. Invalid trusted
coordination state, missing authority, exact remediation-budget exhaustion,
and independent peer Stop policy remain fail-closed.
A valid report finalization failure after a peer terminal preserves that first
terminal result and adds one generic trusted-state warning without reflecting
the finalizer result.

## Exhaustion Additions

At exact attempt or active-time exhaustion, use the same concise core and add:

- `REMEDIATION_BUDGET_EXHAUSTED` and the exact stop trigger under `Outcome`;
- the exact bounded marker-derived blocker and blocker key under
  `Root Cause And Fix`;
- each marker-derived remediation and verification result under
  `Root Cause And Fix`;
- each marker-derived evidence summary under `Verification`.

Exhaustion permits only `UNRESOLVED`, `BLOCKED_MISSING_EVIDENCE`, or
`DIAGNOSED_NOT_FIXED`; it never permits `DIAGNOSED-FIXED`. Return the
hook-supplied redacted report verbatim. A historical exhausted marker that
did not record retry-admission evidence must state that limitation instead of
inventing evidence.

Keep committed reports public-safe. Do not include secrets, private URLs,
internal hostnames, customer data, raw production logs, or environment-specific
credential paths.
