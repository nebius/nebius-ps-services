# Remediation Budget

Use this contract whenever an agent begins a second remediation after the first
one failed against the same blocker. It bounds autonomous repair work while
preserving diagnosis quality and gives the user a complete handoff before more
time is spent.

## Defaults

- One tranche allows at most three distinct failed remediation attempts.
- One tranche allows at most 60 active minutes recorded in `active_seconds`.
- The first reached limit stops the tranche.
- A current-task user instruction may set a lower or higher attempt or time
  limit. Only an explicit instruction may set either limit to unlimited.
- After an exhaustion report, a bare user `continue` starts a fresh tranche for
  the same blocker with three attempts and 60 minutes.

The agent must not extend, reset, or disable a budget on its own. A user-defined
limit in the initial task is an override; a continuation after exhaustion must
come from a new user message. Record a public-safe `override_summary` for every
non-default limit and every continuation tranche; the hook rejects either one
without that evidence.

## Blocker Identity

Create one public-safe `blocker_key` from stable semantic fields:

```text
component | operation | error class or code | source boundary
```

Exclude timestamps, request IDs, temporary paths, line numbers that drift, and
other volatile text. A different message does not create a new blocker when the
affected operation and causal boundary are unchanged. Start a fresh blocker
only when evidence establishes a causally independent failure; retain one
concise prior outcome instead of the full old ledger.

## Counted Attempt

Count an attempt only when all of these are true:

1. The agent states a hypothesis or causal prediction.
2. It changes one materially different variable at a bounded target.
3. It performs an authorized remediation, not only a diagnostic observation.
4. It reruns the original reproducer or an equivalent verification oracle.
5. Verification shows the same blocker remains.

The attempt's `distinct_key` combines its hypothesis, changed variable, and
target. Count each unique `distinct_key` with `result: failed_same_blocker` at
most once. Diagnostics, baseline collection, unchanged retries, repeated
measurements, report generation, permission denials, and a successful repair do
not consume an attempt.

After attempts 1 and 2 fail, send the user a concise progress update containing
the count, attempted remediation, result, and next hypothesis. After attempt 3
fails, update only the exact advertised `current.md` marker to record
exhaustion, then do not call another tool.

## Durable Marker

When durable task state is available, keep exactly one complete marker inside
the first 12 KiB of the current session's advertised `current.md`:

```markdown
<!-- codex-remediation-budget:v1
{
  "schema": "codex/remediation-budget-v1",
  "blocker_key": "component|operation|error-class|boundary",
  "blocker_summary": "Concise public-safe description.",
  "tranche": 1,
  "started_at": "2026-01-01T00:00:00Z",
  "active_seconds": 0,
  "attempt_limit": 3,
  "time_limit_minutes": 60,
  "attempts": [],
  "status": "active",
  "stop_trigger": null,
  "override_summary": null
}
-->
```

Use RFC3339 UTC for `started_at`. Track whole seconds spent actively diagnosing,
remediating, verifying, and reporting in `active_seconds`; exclude time waiting
for the user, model capacity, or an external event. Update it at each durable
checkpoint and before another remediation. Each attempt object contains bounded
`id`, `distinct_key`, `hypothesis`, `remediation`, `verification`, and `result`
fields. Supported results are `failed_same_blocker` and `succeeded`. Supported
statuses are `active`, `exhausted`, and `resolved`; supported stop triggers are
`attempt_limit` and `time_limit`.

Keep raw errors, logs, secrets, private endpoints, customer data, and copied
transcripts outside the marker. The marker is parent-authored coordination
state, not a security token. Hooks validate it but cannot prove that two
attempts are semantically distinct.

Initialize the marker before the second remediation. Record the already failed
first remediation as attempt 1, use its start time when known, and include the
active time already spent on the blocker. After each verification, update the
marker before another tool call. On exhaustion, set `status: exhausted` and its
stop trigger. On success, set `status: resolved`.

For a user-authorized continuation, increment `tranche`, reset `attempts`, set a
fresh `started_at`, reset `active_seconds` to zero, restore the default limits
unless the user provided new ones, and record a concise `override_summary`.
Preserve only a concise outcome for earlier tranches.

## Stop Report

When either limit is exhausted, transition to `REPORTED`, perform no further
diagnostics or remediation after the exact exhaustion-state update, and return
the existing Troubleshooting Report with these additional requirements:

- include `REMEDIATION_BUDGET_EXHAUSTED`;
- identify whether `attempt_limit` or `time_limit` stopped the tranche;
- use the exact sections `## Outcome`, `## Blocking Error`, `## Source`,
  `## Attempts`, `## Evidence`, `## Current State`, and `## Next Action`;
- under `## Blocking Error`, give the exact current error class, code, and
  message excerpt when available, redacted as needed, plus the failing operation;
- list each counted attempt and why it failed;
- state the error source as a component, command, test, service, or bounded log
  location without copying raw sensitive material;
- state repository/runtime state, rollback state, residual uncertainty, and the
  highest-information next action for the user or next tranche.

Use `UNRESOLVED` when competing hypotheses remain,
`BLOCKED_MISSING_EVIDENCE` when access or safe evidence is unavailable, or
`DIAGNOSED_NOT_FIXED` when the cause is known but a repair was not safe,
authorized, or feasible.

## Hook Boundary

The optional hook bundle in `assets/hooks/` reads the marker at supported local
tool boundaries. It blocks supported tools after exhaustion except an exact
update to the advertised `current.md`, and its Stop handler requires the report.
It cannot interrupt a tool that is already running or intercept hosted tools
outside Codex's local hook path. Codex starts matching command hooks
concurrently, so this guard also cannot prevent a peer hook from starting; peer
hooks must remain independently safe. The skill contract remains authoritative
for those paths.
