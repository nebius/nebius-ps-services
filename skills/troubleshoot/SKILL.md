---
name: troubleshoot
description: "Diagnose and repair difficult, persistent, intermittent, or cross-layer failures by proving the causal divergence and smallest durable fix. Not for routine lint, setup, review, feature work, or known mechanical fixes."
---

# Troubleshoot

## Help

For `$troubleshoot --help` or `$troubleshoot -h`, return concise help and stop before
any workflow step. State the purpose and invocation policy. Show exact usage
for every public action. Describe each public action, positional
argument, and flag in one concise line, including `-h, --help`; say "No
additional public flags" when there are no others. Use only the documented
public interface. For internal or coordinator-only skills, state that boundary
and that no standalone public workflow action exists. After the selected
`SKILL.md` is loaded, help is report-only: do not call any additional tools,
inspect project state, or modify files, private state, Git, or external systems.
Never expose private helper actions or flags or treat help as workflow
authorization.

## Purpose

Investigate ambiguous software and infrastructure failures as controlled causal
inquiries, repair the violated invariant at the narrowest correct boundary, and
report proof or residual uncertainty honestly. Stay language-agnostic: use the
target repository's toolchain and current official language or runtime guidance.

## When To Use

- A failure is persistent, flaky, production-only, cross-service,
  environment-specific, stateful, performance-sensitive, or concurrency-related.
- Code, shell automation, an installed service, or an infrastructure boundary
  behaves differently from its observable contract.
- Earlier retries, restarts, timeout increases, rollbacks, or speculative fixes
  changed the symptom without establishing a cause.
- A regression must be localized between known-good and known-bad states.

## When Not To Use

- Use normal implementation for a known mechanical fix, routine feature work,
  syntax errors, formatting, lint, or straightforward dependency installation.
- If the causal mechanism is already proven and the user asks only for the
  design and `/plan` handoff for a boundary-changing remediation, use `design`
  directly rather than restarting troubleshooting.
- Use `code-review` for findings-first review, `apply-security` for a dedicated
  security assessment, and `research` for standalone technical due diligence.
- Use a matching domain skill for known Terraform, Helm, cloud, database, or
  platform implementation; retain this skill only when causal investigation is
  still required.

## Inputs

- Optional session budget flags immediately after the skill name:
  `$troubleshoot --attempt-limit=N --time-limit-minutes=N <problem>`. Do not add
  a `-- <problem>` separator. Omitted flags keep the saved session value; initial
  defaults are 5 attempts and 120 active minutes, with maxima of 10 and 180.
- The expected and actual behavior, error signature, request or job identifiers,
  timestamps, known-good and known-bad cases, and attempted mitigations.
- Repository, command, service, host, container, cluster, deployment, database,
  network, or software-stack target information available to the current task.
- User constraints, permitted changes, production sensitivity, success criteria,
  and required report or evidence locations.
- In active Agentic SDLC, the immutable `failure-event-v1`, exact integration
  commit and worktree identity, accepted criteria, fingerprints,
  `repair-control-v1`, and classifier-selected diagnostic route.

## Required Reads

- Read applicable `AGENTS.md`, repository instructions, current Git status and
  diff, nearby tests, architecture docs, runbooks, and the narrow execution path.
- Read [investigation-protocol.md](references/investigation-protocol.md) for the
  failure contract, ledgers, experiments, proof standard, and state transitions.
- Read [remediation-budget.md](references/remediation-budget.md) before a second
  remediation after the first one failed against the same blocker.
- Read [software-failure-playbooks.md](references/software-failure-playbooks.md)
  for code, shell, CI, build, concurrency, memory, and performance failures.
- Read
  [infrastructure-failure-playbooks.md](references/infrastructure-failure-playbooks.md)
  for installed stacks, services, containers, networks, storage, databases,
  orchestrators, and distributed systems.
- After stack discovery, read only the matching technology playbooks:
  [slurm.md](references/slurm.md), [soperator.md](references/soperator.md),
  [kubernetes.md](references/kubernetes.md), [nebius.md](references/nebius.md),
  [linux.md](references/linux.md), [network.md](references/network.md),
  [storage.md](references/storage.md), [gpu.md](references/gpu.md), and
  [code-debugging.md](references/code-debugging.md). Treat their official
  documentation links as version-sensitive starting points; pin the observed
  version and verify the matching vendor documentation before acting.
- Read [live-product-validation.md](references/live-product-validation.md)
  whenever a live target is used to verify product behavior or a product test
  failure may require target stabilization or recovery.
- Read [technique-selection.md](references/technique-selection.md) before using
  bisection, sanitizers, tracing, profiling, fuzzing, or repeated trials.
- Read
  [verification-and-reporting.md](references/verification-and-reporting.md)
  before claiming root cause, applying a remediation, or closing the task.
- Read [observability-evidence.md](references/observability-evidence.md) only
  when a deployed-runtime hypothesis may require metrics, logs, traces,
  platform state, or observed deployment/configuration changes.
- After the cause is proven and outside Agentic SDLC, use the installed
  `design` skill before implementation only when the durable remediation
  changes architecture topology, component or service responsibilities or
  boundaries, a public interface, data ownership or lifecycle, a migration, or
  a cross-component workflow. Keep repairs inside an existing private boundary
  in `troubleshoot`, regardless of their implementation size or difficulty.
- Verify version-sensitive product or tool behavior against current official
  vendor documentation before relying on it.

## Writes

- Modify code, tests, configuration, or documentation when the active request
  authorizes solving the problem and the evidence supports a specific repair.
- Make bounded, reversible live changes only in a confirmed non-production
  environment and only after identifying the exact target and rollback path.
- Write evidence artifacts only to a user-selected path. Do not create hidden
  repository-local troubleshooting state.
- Use the current durable task-state surface when available for concise
  continuation facts and the bounded remediation marker, never for raw logs,
  secrets, or customer data.

## Agentic SDLC Diagnostic Mode

When `sdlc-classify-failure` routes an active Agentic SDLC failure here:

- Operate as a conditional diagnostic branch, not a mandatory workflow phase.
- Preserve the failed evaluation, exact integration commit, environment
  identity, accepted criteria, fingerprints, stable blocker key, and repair
  budget.
- Use the registered private integration worktree only. Temporary diagnostic
  instrumentation must be explicitly scoped, reversible, uncommitted, removed
  before handoff, and absent from the final diff.
- Do not commit product fixes, change tests or specifications, weaken
  acceptance, invoke general `design`, or call an SDLC design, plan, or
  implementation phase directly.
- Return one `diagnosis-v1` to `sdlc-classify-failure`. The classifier remains
  authoritative for diagnosis validation, taxonomy, budget accounting,
  invalidation, and the next owner.
- The result must be exactly one of: localized implementation defect, test
  defect, evaluator defect, environment defect, specification gap, proven
  system-contract defect, missing decisive evidence, unresolved competing
  hypotheses, policy block, or human input.
- "No implementation bug found" is missing or unresolved evidence. It never
  becomes a design defect without positive causal proof.

A localized implementation handoff must record expected and observed behavior,
the stable blocker and exact regression oracle, earliest divergent component,
operation, and source boundary, violated invariant and causal chain, affected
files and bounded repair target, counterfactual, alternatives eliminated,
confidence, required regression test, evidence references, and constraints to
preserve. It defines the bug and repair boundary, not a speculative patch.

## Process

Follow this state progression and return to an earlier state when new evidence
invalidates the model:

```text
INTAKE -> DISCOVERY -> BASELINE -> MODEL -> HYPOTHESES -> EXPERIMENTS
       -> LOCALIZED -> PROVEN -> REMEDIATED -> VERIFIED -> REPORTED
```

1. **INTAKE**
   - Preserve the original state, evidence, current changes, target identity,
     permissions, data sensitivity, and operational constraints.
   - Build the failure contract. Separate an active incident's stabilization
     loop from its diagnosis loop.
2. **DISCOVERY**
   - Identify technologies, exact versions, deployment model, configuration
     sources, components, dependencies, ports, protocols, authentication, and
     control and data flows before selecting diagnostic commands.
   - Compare the observed topology and active configuration with matching
     official vendor architecture and configuration documentation. Record
     drift, inaccessible evidence, and version ambiguity instead of assuming
     that the design is correct.
   - Create the component verification matrix, incident timeline, and layered
     log-coverage ledger defined in `references/investigation-protocol.md`.
     Verify clock synchronization before correlating timestamps across hosts.
   - Freeze the included and excluded system boundary, exercised control and
     data paths, and incident-window start and end. Limit every later health
     claim to that declared scope and observation period.
3. **BASELINE**
   - Run the narrowest existing reproducer or characterize the failure from
     affected and unaffected evidence when reproduction is unsafe or impossible.
   - Record the command, working directory, exit code, duration, input, seed,
     environment identity, frequency, and stable signature.
4. **MODEL**
   - Trace only the relevant entry point, control/data flow, state, ownership,
     lifecycle, retries, caches, configuration, and process/service boundaries.
5. **HYPOTHESES**
   - Keep facts, derived inferences, hypotheses, and unknowns separate.
   - Maintain three to seven plausible hypotheses when the evidence supports
     them. Give each a prediction and falsifying observation.
   - Before requesting runtime telemetry, prove that one scoped observability
     result can distinguish a named hypothesis. Identify one matching signal
     family and record non-Grafana evidence that it is expected to exist for
     the deployed target, such as user-provided Grafana-backed telemetry,
     instrumentation plus its exporter/collector route, a repository-owned
     dashboard or rule, a service catalog or runbook telemetry mapping, or a
     known Grafana-backed platform/change feed. A symptom, guess, deployment
     manifest without telemetry wiring, or datasource discovery is not signal
     provenance. Resolve explicit authority, the deployed selector, and an
     absolute bounded window before any Grafana call. Skip observability when
     local/static evidence is conclusive, signal fit is unproven, or the query
     cannot change the next decision.
6. **EXPERIMENTS**
   - Run a command, query, or instrumentation only when its result can change
     the next decision. Before execution, state the hypothesis, expected
     supporting and falsifying evidence, timeout or output bound, and next
     branch for each possible result. Change one causally relevant variable at
     a time.
   - Record the question, hypotheses addressed, prediction, falsifying result,
     risk, observation, timeout, and ledger update. Retain negative evidence.
   - Investigate the eight canonical log layers: component, application or job,
     container or orchestrator, service manager, OS and kernel, network and
     firewall, storage, and GPU or hardware. Filter by the incident window and
     correlation identifiers. Record every layer exactly once as `examined`,
     `unavailable`, `unsafe`, or `not applicable`; an unfiltered error search is
     not proof of health.
   - Treat configured component and application logs, container logs, service
     journals, OS/kernel logs, and other primary local sources as baseline
     evidence. Their bounded inspection is not subject to Grafana admission;
     only remote observability-provider queries use the gates below.
   - Only after the decision-value, signal-fit, authority, selector, and window
     gates pass, invoke `$nebius-grafana-query` in evidence-provider mode. Its
     first bounded datasource discovery is the one connectivity/readiness check
     for this investigation; it must not be used to fish for a relevant signal.
     Reuse the resulting `unknown | available | unavailable` state and returned
     total, fast, and deep remaining query budgets; after `unavailable`, skip
     all later observability without retry, setup, authentication repair, or
     credential switching.
   - Start with one cheapest query for the single matching signal family, not a
     fixed bundle of signals. Treat the six-query fast allowance as a cumulative
     ceiling, not a target. Admit another fast query only after the prior result
     or data gap updates the hypothesis ledger and a new exact question can
     change the decision. Do not fan out to other telemetry families merely
     because the expected signal is absent.
   - Use the deep path only when fast evidence leaves at least two named
     hypotheses indistinguishable and the next bounded hypothesis-specific
     query can change the decision. Stop when another query is unlikely to
     change the decision.
   - Before every remediation retry, acquire new evidence from logs, stack
     traces, code inspection, runtime state, or an equivalent observation,
     update the model, and state a genuinely new falsifiable hypothesis. If
     either is unavailable, do not retry or patch speculatively. Return to
     `DISCOVERY`, `MODEL`, or `HYPOTHESES` and seek the next safe bounded result
     that can change a decision. Report early only when decisive evidence is
     unavailable with no safe alternative, authority or safety requires user
     action, the user asks to stop, or the remediation budget is exhausted.
7. **LOCALIZED**
   - Find the earliest divergence across temporal, spatial, input, environment,
     or state-sequence dimensions.
8. **PROVEN**
   - Establish the trigger-to-invariant-to-symptom causal chain, evidence fit,
     counterfactual, safe reintroduction when practical, alternative elimination,
     and confidence: `proven`, `high confidence`, `probable`, or `unknown`.
   - Classify the proposed remedy before editing as either a localized
     invariant restoration or a design-scale change.
   - A design-scale remedy changes at least one system contract: architecture
     topology; component or service responsibilities or boundaries; a public
     interface; data ownership or lifecycle; a migration; or a cross-component
     workflow. Implementation size, algorithmic complexity, concurrency
     difficulty, or a large rewrite inside one existing private boundary does
     not make a repair design-scale.
   - Outside an active Agentic SDLC workflow, use `design` after causal proof
     and before implementation only for a design-scale remedy. Give `design`
     the proven causal chain, violated invariant, requirements, constraints,
     non-goals, fixed technologies, and regression oracle. `design` owns
     solution design and the `/plan` handoff; it must not reopen diagnosis or
     implement the change. Return the completed handoff to the appropriate
     implementation workflow, while `troubleshoot` retains verification and
     final causal reporting.
   - Inside an active Agentic SDLC workflow, send the proven causal handoff to
     `sdlc-classify-failure` instead of calling general `design` or a design
     phase directly. The classifier must reload current state, record the
     failure class and retry accounting, and set `next_recommended_skill`;
     the SDLC coordinator then routes to the recorded design, plan, or other
     owning phase.
   - In Agentic SDLC diagnostic mode, stop at the causal handoff. Emit
     `diagnosis-v1`, remove temporary instrumentation, and return to
     classification without entering `REMEDIATED`.
9. **REMEDIATED**
   - Restore the violated invariant at the narrowest correct boundary,
     following the completed design and `/plan` handoff when the remedy was
     design-scale. Add a regression oracle before or with the fix when feasible.
10. **VERIFIED**
    - Re-run the original reproducer, counterfactual, targeted tests, affected
      integration boundaries, relevant dynamic diagnostics, and enough repeated
      trials for intermittent failures. Confirm repository hygiene.
    - For live product verification, require a clean replay under
      `references/live-product-validation.md`; recovery or a healthy final state
      alone cannot prove the product fixed.
11. **REPORTED**
    - Classify the outcome as `VERIFIED_FIXED`, `MITIGATED_NOT_PROVEN`,
      `DIAGNOSED-FIXED`, `DIAGNOSED_NOT_FIXED`,
      `BLOCKED_MISSING_EVIDENCE`, or `UNRESOLVED`.
    - Use `DIAGNOSED-FIXED` when the causal owner and earliest divergence are
      proven, the owner-correct repair is applied, and the original reproducer,
      focused regression, and source or affected-boundary checks pass for the
      named fixed scope. Put installation, deployment, restart, or live replay
      that was not performed under `Not verified` with one exact next action.
      Reserve `VERIFIED_FIXED` for complete end-to-end proof. Use
      `DIAGNOSED_NOT_FIXED` only when no owner-correct repair was applied.
    - If an attempt or time budget is exhausted, record the stop in the exact
      private task-state marker, stop all other tool use, and return the
      remediation-budget report before any user-authorized continuation.

## Authority And Safety

- Implicit selection does not grant new authority. Obey the surrounding request
  and any explicit `diagnose only`, `read only`, or `do not change` boundary.
- When the request asks to solve a problem and does not prohibit changes, repair
  code and tests once the causal mechanism is sufficiently supported.
- For live product verification, freeze the declared product workflow for each
  trial and keep it separate from environment intervention. Changing the
  declaration starts a new evidence lineage. Authorized stabilization or
  recovery may be necessary, but it marks affected evidence as intervened and
  never substitutes for owner-correct repair and a later clean replay.
- In confirmed non-production, allow bounded reversible service, configuration,
  rollout, scaling, or package changes when they are necessary to resolve the
  issue; preview or dry-run first when available and observe after every change.
- Treat production or an unconfirmed environment as read-only until the user
  explicitly authorizes the exact live action.
- Passive production telemetry remains read-only evidence. It does not
  authorize remediation, workload execution, or broader scope than the user
  explicitly provided.
- Require action-specific approval everywhere before destructive or irreversible
  actions, credential or IAM changes, data mutation, public exposure, resource
  deletion, or changes with material availability or cost impact.
- Use existing authorized credentials without printing, copying, or persisting
  them. Redact secrets and private endpoints from evidence and reports.
- Treat restarts, retries, rollbacks, failovers, cache clearing, timeouts, sleeps,
  concurrency reduction, and downgrades as mitigations or experimental evidence,
  not proof of root cause.
- A temporary verbosity or subsystem-debug increase must have a narrow scope,
  bounded duration, stated performance and availability impact, secret-redaction
  plan, original-value capture, and verified rollback. Production and
  unconfirmed targets still require exact authorization for the live change.

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
- After three low-information experiments, stop and rebuild the model and
  hypothesis set before running another experiment.
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

## Failure Handling

- If reproduction is unavailable, characterize the failure and state the exact
  evidence required to advance; do not fabricate proof.
- If access, observability, or a safe environment is missing, return
  to non-observability evidence, a smaller safe reproducer, code inspection,
  or another decision-changing path first. Return `BLOCKED_MISSING_EVIDENCE`
  only when the missing evidence is decisive and no safe alternative remains;
  name the highest-information next experiment.
- If the evidence provider is `unavailable` or `partial`, continue with
  non-observability evidence when telemetry was optional. When the missing
  runtime signal is decisive and no safe alternative can answer the
  hypothesis, return `BLOCKED_MISSING_EVIDENCE` with the exact missing signal,
  scope, window, and next action.
- If the evidence provider returns `rejected`, preserve connectivity and budget
  and resolve its canonical `rejection_reason` for relevance, authority,
  selector, window, or budget before reconsidering observability.
- If stabilization removes the symptom, preserve `MITIGATED_NOT_PROVEN` until
  the causal chain and counterfactual are established.
- If a proposed experiment is unsafe, reduce its scope, use a dry run or
  disposable fixture, or stop for explicit approval.
- If a design-scale remediation is required but `design` is unavailable, do
  not improvise the redesign inside `troubleshoot`; return
  `DIAGNOSED_NOT_FIXED` with the proven causal chain and exact handoff needed.
- If new evidence contradicts the current cause, lower confidence and return to
  `MODEL` or `HYPOTHESES` rather than defending the earlier explanation.
- If a remediation tranche is exhausted, return `UNRESOLVED`,
  `BLOCKED_MISSING_EVIDENCE`, or `DIAGNOSED_NOT_FIXED` as supported by the
  evidence; do not attempt another remediation before a new user instruction.
- If a retry lacks newly acquired evidence or a genuinely new hypothesis,
  do not repeat the prior remediation path. Rebuild the model and continue
  safe evidence collection while a decision-changing experiment remains;
  otherwise return `BLOCKED_MISSING_EVIDENCE` or `UNRESOLVED` as supported.

## Must Not

- Do not clean, restart, rebuild, clear caches, or alter the target before
  preserving relevant evidence unless emergency stabilization is explicitly
  required.
- Do not patch before establishing a baseline except for a separately authorized
  incident mitigation.
- Do not use broad logging, repository-wide searches, or full rebuilds without a
  localization question they can answer.
- Do not use indefinite `tail -f`, arbitrary sleeps, passive terminal waiting,
  or large unfiltered log dumps. Bound commands by time and output and make the
  next branch explicit before execution.
- Do not mask symptoms with sleeps, unbounded retries, exception suppression,
  disabled tests, arbitrary timeout increases, or global serialization.
- Do not claim root cause from correlation, a hot stack frame, one passing run,
  or symptom disappearance alone.
- Do not query observability before decision relevance, matching-signal
  provenance, authority, selector, and time gates pass. Do not use Grafana
  readiness or datasource discovery to determine whether any relevant telemetry
  exists. Do not invoke the Grafana installer or repair path from embedded
  troubleshooting evidence collection.
- Do not expose secrets, private URLs, customer data, internal hostnames, raw
  production logs, or proprietary infrastructure details.
- Do not commit a product fix or route directly to general design, an SDLC
  design phase, planning, or implementation while in Agentic SDLC diagnostic
  mode.

## Completion Criteria

- The failure contract and baseline or characterization are explicit.
- Evidence identifies the earliest divergence and supports the reported
  confidence without unresolved contradictions being hidden.
- Any change follows directly from the causal chain and respects the authority
  and live-safety boundary.
- A design-scale remediation has a completed design and implementation-plan
  handoff before editing; in Agentic SDLC it also has a recorded failure
  classification and coordinator-selected next skill. A localized repair does
  not invoke `design` unnecessarily.
- The original failure and adjacent affected scopes are verified, or the exact
  missing evidence and next experiment are reported.
- Observability is recorded as used, skipped, partial, or unavailable with its
  decision and signal-fit basis, scope/window provenance, data gaps, and query
  cost when the path was considered.
- No unrelated changes, diagnostic artifacts, credentials, or private data
  remain in the repository.
- The internal component matrix covers dependency reachability,
  authentication, DNS or service-name resolution, resource and clock state,
  restart history, and recent changes. The internal log ledger contains all
  eight canonical records exactly once and in order. Missing evidence stays
  `UNKNOWN`; it does not stop the workflow while another safe bounded path can
  change the decision.
- The concise report accurately projects the causal result, fixed scope,
  verified scope, material unverified scope, and exact next action. Detailed
  matrices, timelines, log rows, and criterion verdicts stay in the working
  evidence unless the user requests them or a hard boundary needs a bounded
  appendix.
- `VERIFIED_FIXED` requires the original failure contract and every applicable
  end-to-end criterion to be proven. `DIAGNOSED-FIXED` requires proven cause,
  applied owner repair, and passing source or affected-boundary verification,
  but may name later activation or live proof explicitly as unverified.
  Passing tests alone do not prove unrelated code paths or a live target.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

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

## References

- Use `scripts/collect_evidence.py` for bounded local repository and environment
  identity. It reads recognized root manifest bytes only to hash them, never
  emits their contents, and does not read remotes or environment values.
- Use `scripts/repeat_command.py` when repeated execution is itself the
  measurement for intermittency, timing, or signature clustering. It executes
  the exact supplied argv and inherits that command's effects; use it only for
  an authorized, safe, idempotent reproducer and do not pass secrets in argv.
- Use `scripts/compare_evidence.py` to compare known-good and known-bad evidence
  snapshots with explicit volatile-field exclusions.
- Use `references/observability-evidence.md` for the runtime-evidence
  eligibility gate, scope resolution, provider invocation, interpretation, and
  unavailable/partial handling.
