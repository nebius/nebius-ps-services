# Report Delivery

## Output Contract

End every explicit `$troubleshoot` invocation with one concise, user-visible
report. Lead with the result; do not make the user read the investigation
ledger to discover whether the code was fixed.

```markdown
# Troubleshooting Report

## Outcome
- Classification: DIAGNOSED-FIXED
- Confidence: High
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

Use exactly one supported classification:

- `VERIFIED_FIXED`: complete end-to-end failure-contract proof.
- `DIAGNOSED-FIXED`: proven cause, applied owner-correct repair, and passing
  reproducer, regression, and source or affected-boundary checks for the
  named fixed scope; activation or live proof may remain under `Not verified`.
- `MITIGATED_NOT_PROVEN`: impact reduced without complete causal repair proof.
- `DIAGNOSED_NOT_FIXED`: cause diagnosed but no owner-correct repair applied.
- `BLOCKED_MISSING_EVIDENCE`: decisive evidence unavailable with no safe
  alternative.
- `UNRESOLVED`: competing hypotheses remain.

Use `High`, `Medium`, `Low`, or `Unknown` for confidence. For every next
action, name the owner, exact action, and observable done condition. If
nothing remains within a `VERIFIED_FIXED` scope, write `None within the
declared scope.` under `Not verified`.

Add `## Evidence Appendix` only when the user requests detail, the evidence
changes a decision, a live or production boundary needs explicit accounting,
or the remediation budget is exhausted. Keep it bounded and public-safe.
Architecture, component, timeline, layered-log, hypothesis, code-debugging,
and completion ledgers remain internal investigation evidence by default.

The optional hook records every explicit invocation in session-private
`troubleshoot-report-obligation.json` and finalizes delivery transactionally.
If the host terminates before Stop, only a resumed turn in the same session can
report that interruption. An ordinary missing,
malformed, partial, `FAIL`, or `UNKNOWN` report is advisory: Stop continues,
no tool is denied, and no generated fallback replaces the assistant response.
Prefer plain inline-code repository-relative local labels such as
`references/verification-and-reporting.md`, optionally followed by a colon and
positive line number. Inline or Markdown destinations may use relative,
platform-native absolute, home-relative, or constrained local `file:` syntax
only when their decoded canonical targets remain inside the Git repository
root derived from the event working directory; without that root, absolute,
home-relative, and local `file:` forms are unsafe. A harmless contained format
defect is advisory. Sensitive content, ambiguous or renderer-active link
syntax, an outside-root target, traversal or symlink escape, or an unsafe URI
closes as `sensitive_detected` and stops with one generic warning. The same
resolver normalizes contained targets and atomically replaces unsafe or
over-limit markup in strict exhausted-report fallbacks. Read
`references/verification-and-reporting.md` before authoring local links or
interpreting a reference warning. The hook requests no automatic replacement
report and cannot retract output already rendered by the host. Invalid trusted
coordination state, missing authority, peer Stop policy, and exact
remediation-budget exhaustion remain fail-closed.

At budget exhaustion, use the same concise report, add
`REMEDIATION_BUDGET_EXHAUSTED` and the exact stop trigger under `Outcome`,
include the bounded marker-derived blocker, blocker key, attempts, and
evidence, and use only `UNRESOLVED`, `BLOCKED_MISSING_EVIDENCE`, or
`DIAGNOSED_NOT_FIXED`. Return a hook-supplied redacted exhaustion report
verbatim. A new user instruction is required before another tranche.

In Agentic SDLC diagnostic mode, return the required `diagnosis-v1` to
`sdlc-classify-failure`, including the result, confidence, owner handoff or
exact missing evidence, instrumentation cleanup, and return route.
