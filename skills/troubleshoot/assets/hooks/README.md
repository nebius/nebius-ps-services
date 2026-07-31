# Remediation Budget Hook Bundle

This optional hook bundle enforces the remediation budget recorded by the
`troubleshoot` skill in the current session's private `current.md` file. The
skill owns semantic classification; the hook only validates the bounded marker,
blocks supported tool calls after exhaustion, and requires the final report.

## Files

- `remediation_attempt_guard.py`: shared `PreToolUse` and `Stop` handler.
- `tests/test_remediation_attempt_guard.py`: disposable unit tests.

## Runtime Contract

- Missing task state or a missing marker fails open.
- A present malformed marker fails closed until the parent repairs the exact
  advertised `current.md` file. The denial includes a bounded public-safe
  validation reason without reflecting marker content or filesystem exception
  details; repair itself consumes no attempt and does not require an exhaustion
  report.
- An unsupported attempt result names the two canonical values and directs
  unverified progress to prose or a causally independent blocker to a fresh
  empty attempt ledger. The guard does not add aliases or reflect the invalid
  value.
- A marker moved beyond the first 12 KiB or a task-state file above the 1 MiB
  safety limit fails closed instead of being treated as absent.
- Contradictory lifecycle state fails closed: active or resolved markers cannot
  retain a stop trigger, and an exhausted trigger must match a reached limit.
- Attempt limits above five or disabled attempt limits fail closed. The marker
  may lower the limit, and the tranche ledger cannot contain more entries than
  that configured limit.
- Attempt labels are derived from list order as `attempt-1` through
  `attempt-5`; an authored ID is ignored. Every active recorded retry must have
  a `blocker_key` exactly matching the marker's one blocker, a unique
  `distinct_key`, normalized hypothesis, and normalized `new_evidence`
  summary. Missing or mismatched binding makes the marker invalid, so a ledger
  copied onto a causally independent blocker enters marker repair rather than
  exhaustion. The guard catches structural or textual inconsistency; the skill
  remains responsible for blocker classification, semantic novelty, and
  pre-remediation timing.
- The canonical data schema is `codex/remediation-budget-v3`. The surrounding
  `codex-remediation-budget:v1` HTML marker remains the stable locator.
  Historical v1 data is accepted only when already exhausted and only for
  report delivery; it keeps its original three-attempt ceiling, may omit
  `new_evidence`, never authorizes a retry, and requires the missing
  evidence-record limitation in the report. Previous v2 state fails closed and
  requires exact marker repair before more work; the hook does not maintain a
  dual-limits compatibility path. Newly written state must use v3 with the
  five-attempt, 120-minute defaults.
- A causally independent blocker starts with a fresh marker and budget. The
  parent owns that classification and must write an empty ledger. The hook
  requires consistent attempt bindings but does not infer semantic blocker
  identity or detect deliberately false relabeling.
- The time limit uses parent-accounted active seconds, so time spent waiting for
  the user, model capacity, or an external event does not consume the tranche.
- Once the attempt or time limit is exhausted, every supported tool call is
  denied except an `apply_patch` that updates only that `current.md` file.
- For exhausted state, the Stop hook requests one corrected report with the
  exact validation issue and includes a bounded, redacted report as the minimum
  assistant response. Validation requires substantive `Remediation`,
  `Verification`, `Result`, and `Evidence` fields for every positional attempt,
  bound to the guard's bounded, redacted marker-derived summaries. A report
  containing a detected sensitive value is rejected for correction. The
  marker-derived report fields are capped at 70 characters so all five attempts
  remain inside the existing fallback preview bound. The
  fallback normalizes pipe characters in attempt remediation and verification
  summaries so marker text cannot collide with the report field delimiters.
  If the continued response remains incomplete, the hook terminates and emits
  the same fallback as a UI/event-stream `systemMessage` warning; that warning
  is not an assistant-authored conversation response. Secret-, URL-, private
  IPv4/IPv6-, internal-hostname/localhost-, cloud-access-key-, and Unix/Windows
  personal-path-shaped values are replaced with generic summaries even though
  the marker contract already requires public-safe content. For invalid state,
  it requests exact marker repair instead of an exhaustion report; one failed
  repair request then stops with an explicit warning.
- Hosted tools outside Codex's local hook path remain governed by the skill
  contract rather than this mechanical guardrail.
- Matching command hooks start concurrently. This guard can deny the underlying
  tool call, but peer hooks must be independently safe because they may already
  have started.

## Validate

From the `skills/` directory:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 \
  troubleshoot/assets/hooks/tests/test_remediation_attempt_guard.py
python3 -m py_compile \
  troubleshoot/assets/hooks/remediation_attempt_guard.py
```

## Install To A Local Runtime

Installation is explicit because hooks change local Codex behavior:

```bash
./install-skills.sh \
  --install-hooks troubleshoot/assets/hooks \
  --register-hooks
```

Set `CODEX_HOME` first to target a disposable or non-default Codex home. After
installing, restart Codex and review and trust both new hook registrations in
`/hooks`. The normal skill installer does not install or trust this bundle.
