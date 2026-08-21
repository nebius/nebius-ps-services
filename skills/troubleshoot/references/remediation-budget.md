# Remediation Budget

Use this contract whenever an agent begins a second remediation after the first
one failed against the same blocker. It bounds autonomous repair work per
causally independent blocker while preserving diagnosis quality and gives the
user a complete handoff before more time is spent.

## Defaults

- One tranche starts with five total remediation attempts and 120 active
  minutes recorded in `active_seconds`.
- The hard maxima are 10 attempts and 180 active minutes. Limits must be
  positive integers; they cannot be disabled.
- The first reached limit stops the tranche.
- Invoke optional flags directly after the skill name, in either order:
  `$troubleshoot --attempt-limit=10 --time-limit-minutes=180 <problem>`. No
  separate `-- <problem>` form is used. Duplicate, malformed, non-positive, or
  above-maximum flags are rejected without changing the saved profile.
- The profile persists for the session. A bare `$troubleshoot` keeps it. One
  flag changes only that field. Explicit `--attempt-limit=5` and
  `--time-limit-minutes=120` reset the defaults.
- A current-task user instruction may require an earlier workflow stop. Honor
  it in prose without changing marker limits or using `override_summary` to
  encode it.
- A profile change applies immediately to active state only when its resulting
  attempt limit is strictly greater than the completed-attempt count and its
  resulting time limit in seconds is strictly greater than `active_seconds`.
  Reject a smaller or equal limit without changing or exhausting the tranche.
- After exhaustion, the next user instruction authorizes fresh state using the
  saved or newly selected profile. It never reopens or clears the exhausted
  tranche in place.

The agent must not extend, reset, or disable a tranche on its own. A
task-specific earlier stop remains workflow-owned and does not change the
mechanical marker.
A lower workflow stop leaves `status: active` and `stop_trigger: null`; return
the structured investigation report without `REMEDIATION_BUDGET_EXHAUSTED` and
wait for another user message.
A continuation after exhaustion must come from a new user message. Record a
public-safe `override_summary` only for a same-blocker continuation tranche; the
hook rejects an initial v4 tranche containing one and rejects a continuation
without one.
Before exhaustion, a causally independent blocker is not a continuation and
starts its own fresh canonical budget without requiring another user
instruction. After exhaustion, the next user instruction is required whether
the fresh marker continues the same blocker or starts an independent one.

## Blocker Identity

Create one public-safe `blocker_key` from stable semantic fields:

```text
component | operation | error class or code | source boundary
```

Exclude timestamps, request IDs, temporary paths, line numbers that drift, and
other volatile text. A different message does not create a new blocker when the
affected operation and causal boundary are unchanged. Start a fresh blocker
only when evidence establishes a causally independent failure. Replace the
marker for that transition with one complete canonical marker: the new
`blocker_key`, a concise public-safe `blocker_summary`, `tranche: 1`, a fresh
`started_at`, `active_seconds: 0`, `attempts: []`, `status: active`,
`stop_trigger: null`, the saved session profile and its authorization binding,
and `override_summary: null`.
Retain one concise prior outcome in the prose task summary instead of carrying
the old attempt ledger into the new blocker. If the user requested an earlier
stop, keep it in prose and stop voluntarily before the hook ceiling.

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

Do not write a planned or in-progress attempt object. Until remediation has
executed and its verification result is known, keep the plan and evidence in
the prose task summary and leave the marker ledger unchanged. After
verification, append the complete canonical attempt object atomically before
another tool call.

Before each retry, update the evidence and hypothesis ledgers with steps 1 and
2. If either gate cannot be satisfied, do not remediate again; transition to
`REPORTED` with `BLOCKED_MISSING_EVIDENCE` or `UNRESOLVED` and identify the
highest-information next action.

The attempt's `distinct_key` combines its hypothesis, changed variable, and
target. Attempt labels are derived from list order as `attempt-1` through the
configured `attempt_limit`; do not author a separate ID field. Every recorded
`distinct_key`, normalized hypothesis, and normalized `new_evidence` summary
must be unique inside the tranche. The durable marker records the admitted
evidence and hypothesis after verification; the workflow contract owns their
pre-remediation timing and semantic novelty. Diagnostics, baseline collection,
unchanged retries, repeated measurements, report generation, permission
denials, and marker validation or repair are not attempts. A successful
remediation is an attempt but does not trigger failure exhaustion; it must be
the final ledger entry and set the marker to `resolved`.

After each non-terminal failure, acquire the next evidence, rebuild the
hypothesis, and send the user a concise progress update containing the count,
attempted remediation, result, new evidence, and next hypothesis before another
repair. When the configured limit is reached, update only the exact advertised
`current.md` marker to record exhaustion, then do not call another tool.

## Durable Marker

When durable task state is available, keep exactly one complete marker inside
the first 12 KiB of the current session's advertised `current.md`:

```markdown
<!-- codex-remediation-budget:v1
{
  "schema": "codex/remediation-budget-v4",
  "blocker_key": "component|operation|error-class|boundary",
  "blocker_summary": "Concise public-safe description.",
  "tranche": 1,
  "started_at": "2026-01-01T00:00:00Z",
  "active_seconds": 0,
  "attempt_limit": 5,
  "time_limit_minutes": 120,
  "budget_authorization_id": null,
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
contains no more entries than the configured `attempt_limit` or the hard
10-attempt maximum.
Supported results are `failed_same_blocker` and `succeeded`. Supported statuses
are `active`, `exhausted`, and `resolved`; supported stop triggers are
`attempt_limit` and `time_limit`.

Use this canonical attempt shape:

```json
{
  "blocker_key": "component|operation|error-class|boundary",
  "distinct_key": "hypothesis|changed-variable|bounded-target",
  "hypothesis": "Concise causal prediction.",
  "new_evidence": "Public-safe summary of a new observation.",
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

Default v4 state may use `budget_authorization_id: null` only when no saved
session authorization exists and its limits are exactly 5/120. An explicit
profile selection, including an explicit reset to 5/120, creates a private
session authorization and requires the marker to match its ID and exact values.
The ID is a mechanical binding, not a credential; do not invent or copy it from
prompt prose. The hook supplies it as bounded developer context after verifying
the user turn.

Historical v1 markers that predate required `new_evidence` may omit that field
only when their data schema is `codex/remediation-budget-v1` and status is
already `exhausted`. The hook accepts that shape only to deny further tools and
deliver an honest final report; it labels the missing evidence record
explicitly and never admits another remediation. Previous v2 and v3 markers
fail closed and require exact marker repair; v3 is not reinterpreted as v4 and
there is no dual-limits compatibility path. Newly written markers must use the
canonical v4 data schema above. The v1
suffix on the enclosing HTML comment is the stable marker locator and is
independent of the JSON data-schema version. Historical exhausted v1 records do
not change the 5/120 defaults for newly authored v4 state.

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

For a user-authorized same-blocker continuation, increment `tranche`, reset
`attempts`, set a fresh `started_at`, reset `active_seconds` to zero, use the
saved or newly selected profile and authorization ID, and record a concise
continuation-only `override_summary`. Preserve only a concise outcome for
earlier tranches.

For a causally independent blocker, do not increment the old tranche. Replace
the old marker with the fresh blocker state described under Blocker Identity.
The new blocker begins at attempt 1 even when an earlier blocker exhausted its
budget.

## Stop Report

When either limit is exhausted, transition to `REPORTED`, perform no further
diagnostics or remediation after the exact exhaustion-state update, and return
the concise Troubleshooting Report with these additional requirements:

- include `REMEDIATION_BUDGET_EXHAUSTED`;
- identify whether `attempt_limit` or `time_limit` stopped the tranche;
- use the single concise heading order in `verification-and-reporting.md`
  rather than a separate exhaustion format;
- under `## Root Cause And Fix`, use one substantive `- Root cause: ...` line
  with the exact bounded marker-derived blocker summary;
- under `## Root Cause And Fix`, use one substantive `- Blocker key: ...` line
  identifying the bounded causal owner without copying raw sensitive material;
- under `## Root Cause And Fix`, list each counted attempt as
  `- attempt-N | Remediation: ... | Verification: ... | Result: ...`;
- under `## Verification`, list its evidence as
  `- attempt-N | Evidence: ...`;
- use the hook's bounded, redacted marker-derived blocker, source, remediation,
  verification, result, and evidence summaries when the optional guard is
  active; generic prose or sensitive values do not satisfy delivery;
- state repository/runtime state, rollback state, residual uncertainty, and the
  highest-information next action for the user or next tranche.

When the Stop hook provides its bounded marker-derived report, return it
verbatim as the whole assistant response. Do not add an introduction, rewrite
its fields, or substitute a richer narrative; even a semantically equivalent
root-cause value does not satisfy the exact marker binding.

If the evidence or hypothesis gate blocks a retry before either numeric limit,
transition to `REPORTED` without `REMEDIATION_BUDGET_EXHAUSTED`. Use the same
structured investigation report, identify the unsatisfied retry gate, preserve
the current attempt count, and name the exact evidence needed to proceed.

Use `UNRESOLVED` when competing hypotheses remain,
`BLOCKED_MISSING_EVIDENCE` when access or safe evidence is unavailable, or
`DIAGNOSED_NOT_FIXED` when the cause is known but a repair was not safe,
authorized, or feasible.

## Hook Boundary

The optional hook bundle in `assets/hooks/` handles `UserPromptSubmit`,
`PreToolUse`, and `Stop`. Its prompt handler parses only an exact leading
`$troubleshoot`, persists no raw prompt, and writes a 0600 session-bound
authorization sidecar under the private 0700 task-state directory. Non-default
marker values must match that sidecar; `override_summary` and prompt text are
not authorization. It blocks supported tools after exhaustion except an exact
update to the advertised `current.md`. At exhaustion, its Stop handler requests
one corrected marker-bound concise report and supplies a bounded, redacted
report for the assistant to return verbatim. If that continuation is still
incomplete, it stops with the same concise UI fallback. The fallback includes
an explicit limitation when a historical exhausted v1 data marker did not
record retry-admission evidence. New v4 state defaults to 5/120 and permits
authorized values only through 10/180. Free-text
`override_summary` cannot authorize numbers; it records same-blocker
continuation only.

Separately, every explicit `$troubleshoot` invocation creates
`troubleshoot-report-obligation.json` without changing the budget authorization
schema. The sidecar is an invocation and transactional-delivery record, not a
general workflow lock. A valid concise report is finalized only when the shared
Stop arbiter has no peer lifecycle continuation. An ordinary incomplete or
malformed report records `advisory_incomplete` and returns `continue: true`;
it requests no correction, denies no later tool, and emits no fallback. A
sensitive or unsafe ordinary report records `sensitive_detected`, emits one
generic terminal warning, and requests no automatic replacement. A contained
local-reference format defect remains advisory. This does not change the
exhausted-budget branch, which retains its bounded report correction and
terminal fallback. Marker-derived exhausted fallback fields use the same full
Git-root containment policy as ordinary reports, normalize contained local
targets to repository-relative form, and replace outside-root, unsafe, or
symlink-escaping references with generic summaries. Process termination
before Stop remains reportable only after a same-session resume.
For an active resize, the hook records a pending authorization and admits only
the exact `current.md` patch until the marker matches it while preserving the
blocker, tranche, attempt ledger, counters, lifecycle, and timestamps. It then
promotes the pending profile atomically. If that marker is invalid, feedback
requires atomic restoration of every non-profile field plus the authorized
profile fields. If it is missing, the hook states that bounded authorization
metadata cannot reconstruct it and requires the exact pre-resize marker or a
fresh user-authorized troubleshoot session; it never suggests a reset. At
exhaustion it records a private terminal lock, so clearing or relabeling the
ledger cannot reopen the tranche; deleting the marker also remains fail-closed,
with only an exact marker restore admitted before another tool. The next user
turn must authorize fresh same-blocker or causally independent state.
For invalid state, it permits only exact marker repair, reports the validation
reason, and re-evaluates the repaired marker; invalidity alone does not require
an exhaustion report. A pending authorization never masks a missing or invalid
marker or an invalid pending transition: UserPromptSubmit, PreToolUse, and Stop
all report the precise bounded reason followed by complete pending-repair
guidance. Fresh-state guidance refers to the prior terminal marker because the
handoff may follow either resolved or exhausted state. For an incomplete
attempt, it reports all missing canonical fields together and directs the parent
to remove unverified progress or complete the verified record atomically instead
of repairing one field at a time. It verifies that every canonical attempt is
textually
bound to the marker's one `blocker_key`, but cannot infer whether two failures
are causally independent or detect a deliberately false relabeling. It also
cannot infer whether evidence is semantically new or acquired before the retry,
whether a local tool call is diagnostic or remedial, interrupt a tool that is
already running, or intercept hosted tools outside Codex's local hook path.
Codex starts matching command hooks concurrently, so this guard also cannot
prevent a peer hook from starting; peer hooks must remain independently safe.
The skill contract remains authoritative for those paths.
