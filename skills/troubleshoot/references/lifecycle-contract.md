# Lifecycle Contract

Read when resuming an interrupted investigation or before changing a remediation marker.

## Idempotency

- Reuse the same failure signature and evidence ledger across reruns; do not
  silently redefine success after the symptom changes.
- Do not repeat an unchanged command unless repetition itself is the measurement.
- Reuse the observability connectivity state, datasource discovery, identical
  query results, and returned total, fast, and deep remaining budgets for one
  investigation. A new investigation starts with `unknown`; do not persist raw
  telemetry or credentials in task state.
- Do not chain speculative patches. Revert temporary instrumentation after its
  observation unless it becomes justified production observability.
- In Agentic SDLC diagnostic mode, temporary instrumentation never becomes a
  committed change; product remediation belongs to the classified owner.
- After three consecutive completed observations that neither distinguish hypotheses
  nor narrow the causal boundary, rebuild the model before another experiment.
  This never resets or extends remediation budgets.
- After one remediation fails against a stable blocker, initialize the
  remediation budget before a second repair. A blocker tranche starts at the
  saved session profile: five total remediation attempts and 120 active minutes
  by default, with hard maxima of 10 attempts and 180 minutes. The first reached
  limit stops the tranche. Only the UserPromptSubmit authorization hook may
  establish non-default marker values; prompt prose and `override_summary`
  cannot. A current-task user may require an earlier workflow stop in prose. At
  that earlier stop, leave `status: active` and `stop_trigger: null`, return a
  normal report without `REMEDIATION_BUDGET_EXHAUSTED`, and wait.
- Admit a retry only after new evidence obtained since the preceding failed
  attempt changes the model and supports a genuinely new hypothesis with a
  falsifiable prediction. Record them in the working ledgers before the retry
  and persist their public-safe summaries in the completed attempt object after
  verification. Rewording the same hypothesis or reusing the same evidence
  does not qualify.
- When evidence establishes a causally independent blocker, replace the marker
  with one complete canonical fresh blocker budget, including its new
  `blocker_key` and a public-safe `blocker_summary`: tranche 1, zero active
  time, an empty attempt ledger, active status, no stop trigger, and a null
  `override_summary`. Keep the next remediation plan in prose; only after that
  remediation executes and verification completes does it become attempt 1.
  This is not a continuation of the earlier blocker and does not require a new
  user instruction.
- Bind every completed attempt to the exact top-level marker `blocker_key`.
  Missing, mixed, or carried attempt bindings are invalid coordination state,
  not evidence that the new blocker exhausted its budget.
- Treat permission denials and remediation-marker validation or repair as
  coordination events, not counted remediation attempts or budget exhaustion.
- Report each failed attempt before another repair. At the configured attempt or
  time limit, set the marker to `exhausted`, call no other tools, and transition
  directly to `REPORTED`.
- If the new-evidence or new-hypothesis gate cannot be satisfied before the
  maximum, stop without another remediation and return the structured
  investigation report with the exact missing evidence and next action.
- Never extend or reset a tranche for the same blocker without a new current-task
  user instruction. Optional flags update the saved session profile. An active
  or resolved-state profile change is valid only when both resulting limits
  remain strictly above the completed-attempt and consumed-active-time
  counters. If a pending resize
  marker becomes invalid, restore every non-profile field to its exact
  pre-resize value and apply the authorized profile fields atomically. A
  deleted marker cannot be reconstructed from bounded authorization metadata;
  restore the exact prior marker or end the session and request a fresh
  user-authorized troubleshoot session without inventing or resetting blocker
  state. A resolved marker is completed evidence, not a terminal tool lock: a
  bare later `$troubleshoot` keeps the saved profile and starts discovery
  without pending authorization or marker replacement. Explicit profile flags
  use the profile-only handshake while preserving the resolved marker core.
  Only a post-exhaustion fresh-state handoff calls its source the prior terminal
  marker. An exhausted tranche is never reopened; the next user instruction
  starts fresh state using the saved profile.
- Helper scripts must be safe to rerun and must replace only the exact output
  path selected by the user.

Hooks validate structural coordination state and selected boundaries. They do not
mechanically prove causality or semantic novelty of a hypothesis. The shared Stop
arbiter and trusted runtime loader retain their existing owners.
