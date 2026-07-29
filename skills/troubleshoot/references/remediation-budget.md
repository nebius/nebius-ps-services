# Remediation Budget

Use this contract whenever an agent begins a second remediation after the first
one failed against the same blocker. It bounds autonomous repair work per
causally independent blocker while preserving diagnosis quality and gives the
user a complete handoff before more time is spent.

## Defaults

- One tranche allows at most three total remediation attempts against one
  blocker. Three is a hard maximum, not only a default.
- One tranche allows at most 60 active minutes recorded in `active_seconds`.
- The first reached limit stops the tranche.
- A current-task user instruction may lower the attempt limit or change the
  time limit. The attempt limit must remain an integer from 1 to 3 and cannot
  be disabled.
- After an exhaustion report, a bare user `continue` starts a fresh tranche for
  the same blocker with three attempts and 60 minutes.

The agent must not extend, reset, or disable a tranche for the same blocker on
its own. Even an explicit user continuation creates another bounded tranche; it
does not raise the three-attempt maximum inside a tranche. A user-defined lower
attempt limit or non-default time limit in the initial task is an override, and
a continuation after exhaustion must come from a new user message. Record a
public-safe `override_summary` for every non-default limit and every continuation
tranche; the hook rejects either one without that evidence. A causally
independent blocker is not a continuation and starts its own fresh default
budget without requiring another user instruction.

## Blocker Identity

Create one public-safe `blocker_key` from stable semantic fields:

```text
component | operation | error class or code | source boundary
```

Exclude timestamps, request IDs, temporary paths, line numbers that drift, and
other volatile text. A different message does not create a new blocker when the
affected operation and causal boundary are unchanged. Start a fresh blocker
only when evidence establishes a causally independent failure. Replace the
marker for that transition with the new `blocker_key`, `tranche: 1`, a fresh
`started_at`, `active_seconds: 0`, `attempts: []`, `status: active`,
`stop_trigger: null`, and the default limits unless the user's current
instruction sets limits that apply to the new blocker. Set `override_summary`
to null when the defaults apply. Retain one concise prior outcome in the prose
task summary instead of carrying the old attempt ledger into the new blocker.

A hook denial or marker-schema failure is not the original operation's blocker.
Correcting coordination state does not consume an attempt or exhaust that
operation's budget. If the hook or marker develops a substantive failure that
itself needs multiple remediation cycles, give that causally independent
failure its own `blocker_key` and fresh budget.

## Counted Attempt

Count an attempt only when all of these are true:

1. The agent has newly acquired evidence that was not used to admit an earlier
   attempt. Use a public-safe summary or bounded reference to logs, stack
   traces, code inspection, runtime state, or an equivalent observation.
2. The new evidence updates the model and supports a genuinely new hypothesis
   with a falsifiable causal prediction. Rewording an earlier hypothesis does
   not qualify.
3. It changes one materially different variable at a bounded target.
4. It performs an authorized remediation, not only a diagnostic observation.
5. It reruns the original reproducer or an equivalent verification oracle.
6. Verification records whether the same blocker remains or the remediation
   succeeded, and the attempt records the exact marker `blocker_key` that was
   verified.

Before each retry, update the evidence and hypothesis ledgers with steps 1 and
2. If either gate cannot be satisfied, do not remediate again; transition to
`REPORTED` with `BLOCKED_MISSING_EVIDENCE` or `UNRESOLVED` and identify the
highest-information next action.

The attempt's `distinct_key` combines its hypothesis, changed variable, and
target. Attempt labels are derived from list order as `attempt-1`, `attempt-2`,
and `attempt-3`; do not author a separate ID field. Every recorded
`distinct_key`, normalized hypothesis, and normalized `new_evidence` summary
must be unique inside the tranche. The durable marker records the admitted
evidence and hypothesis after verification; the workflow contract owns their
pre-remediation timing and semantic novelty. Diagnostics, baseline collection,
unchanged retries, repeated measurements, report generation, permission
denials, and marker validation or repair are not attempts. A successful
remediation is an attempt but does not trigger failure exhaustion; it must be
the final ledger entry and set the marker to `resolved`.

After attempts 1 and 2 fail, acquire the next evidence, rebuild the hypothesis,
and send the user a concise progress update containing the count, attempted
remediation, result, new evidence, and next hypothesis before another repair.
After attempt 3 fails, update only the exact advertised `current.md` marker to
record exhaustion, then do not call another tool.

## Durable Marker

When durable task state is available, keep exactly one complete marker inside
the first 12 KiB of the current session's advertised `current.md`:

```markdown
<!-- codex-remediation-budget:v1
{
  "schema": "codex/remediation-budget-v2",
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
`blocker_key`, `distinct_key`, `hypothesis`, `new_evidence`, `remediation`,
`verification`, and `result` fields. Every canonical attempt's `blocker_key`
must exactly match the marker's top-level `blocker_key`; a mixed or carried
ledger is invalid. Its list position is its canonical attempt label. The list
contains no more entries than the configured `attempt_limit`, which itself
cannot exceed three.
Supported results are `failed_same_blocker` and `succeeded`. Supported statuses
are `active`, `exhausted`, and `resolved`; supported stop triggers are
`attempt_limit` and `time_limit`.

Use this canonical attempt shape:

```json
{
  "blocker_key": "component|operation|error-class|boundary",
  "distinct_key": "hypothesis|changed-variable|bounded-target",
  "hypothesis": "Concise causal prediction.",
  "new_evidence": "Public-safe summary of a new log, stack trace, code inspection, or equivalent observation.",
  "remediation": "One materially different bounded change.",
  "verification": "Original reproducer still showed the same blocker.",
  "result": "failed_same_blocker"
}
```

Keep raw errors, logs, secrets, private endpoints, customer data, and copied
transcripts outside the marker. The marker is parent-authored coordination
state, not a security token. Hooks reject missing or textually repeated
hypotheses and evidence summaries, but cannot prove semantic novelty, when the
evidence was acquired, or whether a tool call is diagnostic or remedial.

Historical v1 markers that predate required `new_evidence` may omit that field
only when their data schema is `codex/remediation-budget-v1` and status is
already `exhausted`. The hook accepts that shape only to deny further tools and
deliver an honest final report; it labels the missing evidence record
explicitly and never admits another remediation. Active, resolved, and newly
written markers must use the canonical v2 data schema above. The v1 suffix on
the enclosing HTML comment is the stable marker locator and is independent of
the JSON data-schema version.

Initialize the marker before the second remediation. Record the already failed
first remediation as attempt 1, use its start time when known, and include the
active time already spent on the blocker. After each verification, update the
marker before another tool call. On exhaustion, set `status: exhausted` and its
stop trigger. On success, set `status: resolved`.

Keep lifecycle fields consistent. `active` and `resolved` require
`stop_trigger: null`. `exhausted` with `attempt_limit` requires the configured
number of unique `failed_same_blocker` keys; `exhausted` with `time_limit`
requires `active_seconds` to reach the configured time limit. A reached numeric
limit still exhausts the budget when the parent has not yet changed `status`.
`resolved` requires one successful final attempt and cannot bypass an already
reached attempt or time limit. A successful attempt in any other state is
invalid. Contradictory lifecycle fields are invalid and must be repaired, not
interpreted as exhaustion. A canonical attempt with a missing or mismatched
`blocker_key` is also invalid and must enter marker repair, not exhaustion.

For a user-authorized continuation, increment `tranche`, reset `attempts`, set a
fresh `started_at`, reset `active_seconds` to zero, restore the default limits
unless the user provided new ones, and record a concise `override_summary`.
Preserve only a concise outcome for earlier tranches.

For a causally independent blocker, do not increment the old tranche. Replace
the old marker with the fresh blocker state described under Blocker Identity.
The new blocker begins at attempt 1 even when an earlier blocker exhausted its
budget.

## Stop Report

When either limit is exhausted, transition to `REPORTED`, perform no further
diagnostics or remediation after the exact exhaustion-state update, and return
the existing Troubleshooting Report with these additional requirements:

- include `REMEDIATION_BUDGET_EXHAUSTED`;
- identify whether `attempt_limit` or `time_limit` stopped the tranche;
- use the exact sections `## Outcome`, `## Blocking Error`, `## Source`,
  `## Attempts`, `## Evidence`, `## Current State`, and `## Next Action`;
- under `## Blocking Error`, use one substantive `Blocker: ...` line with the
  exact current error class, code, and message excerpt when available, redacted
  as needed, plus the failing operation;
- under `## Source`, use one substantive `Blocker key: ...` line identifying a
  component, command, test, service, or bounded log location without copying
  raw sensitive material;
- list each counted attempt as
  `- attempt-N | Remediation: ... | Verification: ... | Result: ...`;
- list its evidence as `- attempt-N | Evidence: ...`;
- use the hook's bounded, redacted marker-derived blocker, source, remediation,
  verification, result, and evidence summaries when the optional guard is
  active; generic prose or sensitive values do not satisfy delivery;
- state repository/runtime state, rollback state, residual uncertainty, and the
  highest-information next action for the user or next tranche.

If the evidence or hypothesis gate blocks a retry before either numeric limit,
transition to `REPORTED` without `REMEDIATION_BUDGET_EXHAUSTED`. Use the same
structured investigation report, identify the unsatisfied retry gate, preserve
the current attempt count, and name the exact evidence needed to proceed.

Use `UNRESOLVED` when competing hypotheses remain,
`BLOCKED_MISSING_EVIDENCE` when access or safe evidence is unavailable, or
`DIAGNOSED_NOT_FIXED` when the cause is known but a repair was not safe,
authorized, or feasible.

## Hook Boundary

The optional hook bundle in `assets/hooks/` reads the marker at supported local
tool boundaries. It blocks supported tools after exhaustion except an exact
update to the advertised `current.md`. Its Stop handler requests one corrected
report with the exact missing field or section and supplies a bounded, redacted
minimum report for the assistant to return. If that continuation is still
incomplete, it stops and emits that fallback as a UI/event-stream
`systemMessage` warning rather than an assistant-authored response. The
fallback includes an explicit limitation when a historical exhausted v1 data
marker did not record retry-admission evidence.
For invalid state, it permits only exact marker repair, reports the validation
reason, and re-evaluates the repaired marker; invalidity alone does not require
an exhaustion report. It verifies that every canonical attempt is textually
bound to the marker's one `blocker_key`, but cannot infer whether two failures
are causally independent or detect a deliberately false relabeling. It also
cannot infer whether evidence is semantically new or acquired before the retry,
whether a local tool call is diagnostic or remedial, interrupt a tool that is
already running, or intercept hosted tools outside Codex's local hook path.
Codex starts matching command hooks concurrently, so this guard also cannot
prevent a peer hook from starting; peer hooks must remain independently safe.
The skill contract remains authoritative for those paths.
