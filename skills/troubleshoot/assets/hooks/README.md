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
- A causally independent blocker starts with a fresh marker and budget. The
  parent owns that classification; the hook validates the replacement but does
  not infer semantic blocker identity.
- The time limit uses parent-accounted active seconds, so time spent waiting for
  the user, model capacity, or an external event does not consume the tranche.
- Once the attempt or time limit is exhausted, every supported tool call is
  denied except an `apply_patch` that updates only that `current.md` file.
- For exhausted state, the Stop hook accepts the turn only after the assistant
  returns the required troubleshooting report. For invalid state, it requests
  exact marker repair instead of an exhaustion report. Either request is made
  once before an explicit warning stops an infinite continuation loop.
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
