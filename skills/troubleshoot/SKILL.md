---
name: troubleshoot
description: "Use for causal troubleshooting and repair of difficult, persistent, intermittent, cross-layer, environment-specific, regression, performance, concurrency, corruption, CI-only, production-only, installed-software, service, container, network, storage, Kubernetes, cloud, application-code, or shell-script failures. Preserve evidence, test competing hypotheses, localize the earliest divergence, prove the mechanism, apply the smallest durable fix within the user's authority, and verify it. Do not use for routine syntax, lint, formatting, dependency installation, generic review, ordinary feature work, or an already-understood mechanical fix."
---

# Troubleshoot

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
- Use `code-review` for findings-first review, `apply-security` for a dedicated
  security assessment, and `research` for standalone technical due diligence.
- Use a matching domain skill for known Terraform, Helm, cloud, database, or
  platform implementation; retain this skill only when causal investigation is
  still required.

## Inputs

- The expected and actual behavior, error signature, request or job identifiers,
  timestamps, known-good and known-bad cases, and attempted mitigations.
- Repository, command, service, host, container, cluster, deployment, database,
  network, or software-stack target information available to the current task.
- User constraints, permitted changes, production sensitivity, success criteria,
  and required report or evidence locations.

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
- Read [technique-selection.md](references/technique-selection.md) before using
  bisection, sanitizers, tracing, profiling, fuzzing, or repeated trials.
- Read
  [verification-and-reporting.md](references/verification-and-reporting.md)
  before claiming root cause, applying a remediation, or closing the task.
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

## Process

Follow this state progression and return to an earlier state when new evidence
invalidates the model:

```text
INTAKE -> BASELINE -> MODEL -> HYPOTHESES -> EXPERIMENTS
       -> LOCALIZED -> PROVEN -> REMEDIATED -> VERIFIED -> REPORTED
```

1. **INTAKE**
   - Preserve the original state, evidence, current changes, target identity,
     permissions, data sensitivity, and operational constraints.
   - Build the failure contract. Separate an active incident's stabilization
     loop from its diagnosis loop.
2. **BASELINE**
   - Run the narrowest existing reproducer or characterize the failure from
     affected and unaffected evidence when reproduction is unsafe or impossible.
   - Record the command, working directory, exit code, duration, input, seed,
     environment identity, frequency, and stable signature.
3. **MODEL**
   - Trace only the relevant entry point, control/data flow, state, ownership,
     lifecycle, retries, caches, configuration, and process/service boundaries.
4. **HYPOTHESES**
   - Keep facts, derived inferences, hypotheses, and unknowns separate.
   - Maintain three to seven plausible hypotheses when the evidence supports
     them. Give each a prediction and falsifying observation.
5. **EXPERIMENTS**
   - Run commands or instrumentation only when the result can change the next
     decision. Change one causally relevant variable at a time.
   - Record the question, hypotheses addressed, prediction, falsifying result,
     risk, observation, and ledger update. Retain negative evidence.
6. **LOCALIZED**
   - Find the earliest divergence across temporal, spatial, input, environment,
     or state-sequence dimensions.
7. **PROVEN**
   - Establish the trigger-to-invariant-to-symptom causal chain, evidence fit,
     counterfactual, safe reintroduction when practical, alternative elimination,
     and confidence: `proven`, `high confidence`, `probable`, or `unknown`.
8. **REMEDIATED**
   - Restore the violated invariant at the narrowest correct boundary. Add a
     regression oracle before or with the fix when feasible.
9. **VERIFIED**
   - Re-run the original reproducer, counterfactual, targeted tests, affected
     integration boundaries, relevant dynamic diagnostics, and enough repeated
     trials for intermittent failures. Confirm repository hygiene.
10. **REPORTED**
    - Classify the outcome as `VERIFIED_FIXED`, `MITIGATED_NOT_PROVEN`,
      `DIAGNOSED_NOT_FIXED`, `BLOCKED_MISSING_EVIDENCE`, or `UNRESOLVED`.
    - If an attempt or time budget is exhausted, record the stop in the exact
      private task-state marker, stop all other tool use, and return the
      remediation-budget report before any user-authorized continuation.

## Authority And Safety

- Implicit selection does not grant new authority. Obey the surrounding request
  and any explicit `diagnose only`, `read only`, or `do not change` boundary.
- When the request asks to solve a problem and does not prohibit changes, repair
  code and tests once the causal mechanism is sufficiently supported.
- In confirmed non-production, allow bounded reversible service, configuration,
  rollout, scaling, or package changes when they are necessary to resolve the
  issue; preview or dry-run first when available and observe after every change.
- Treat production or an unconfirmed environment as read-only until the user
  explicitly authorizes the exact live action.
- Require action-specific approval everywhere before destructive or irreversible
  actions, credential or IAM changes, data mutation, public exposure, resource
  deletion, or changes with material availability or cost impact.
- Use existing authorized credentials without printing, copying, or persisting
  them. Redact secrets and private endpoints from evidence and reports.
- Treat restarts, retries, rollbacks, failovers, cache clearing, timeouts, sleeps,
  concurrency reduction, and downgrades as mitigations or experimental evidence,
  not proof of root cause.

## Idempotency

- Reuse the same failure signature and evidence ledger across reruns; do not
  silently redefine success after the symptom changes.
- Do not repeat an unchanged command unless repetition itself is the measurement.
- Do not chain speculative patches. Revert temporary instrumentation after its
  observation unless it becomes justified production observability.
- After three low-information experiments, stop and rebuild the model and
  hypothesis set before running another experiment.
- After one remediation fails against a stable blocker, initialize the
  remediation budget before a second repair. Count only distinct failed
  remediation-plus-verification cycles; default to three attempts or 60 active
  minutes, whichever is reached first.
- Report failed attempts 1 and 2 to the user. After the third failed attempt or
  time exhaustion, set the marker to `exhausted`, call no other tools, and
  transition directly to `REPORTED`.
- Never extend or reset a tranche without an explicit current-task user
  instruction. A bare `continue` after the report starts a fresh default tranche.
- Helper scripts must be safe to rerun and must replace only the exact output
  path selected by the user.

## Failure Handling

- If reproduction is unavailable, characterize the failure and state the exact
  evidence required to advance; do not fabricate proof.
- If access, observability, or a safe environment is missing, return
  `BLOCKED_MISSING_EVIDENCE` with the highest-information next experiment.
- If stabilization removes the symptom, preserve `MITIGATED_NOT_PROVEN` until
  the causal chain and counterfactual are established.
- If a proposed experiment is unsafe, reduce its scope, use a dry run or
  disposable fixture, or stop for explicit approval.
- If new evidence contradicts the current cause, lower confidence and return to
  `MODEL` or `HYPOTHESES` rather than defending the earlier explanation.
- If a remediation tranche is exhausted, return `UNRESOLVED`,
  `BLOCKED_MISSING_EVIDENCE`, or `DIAGNOSED_NOT_FIXED` as supported by the
  evidence; do not attempt a fourth remediation before a new user instruction.

## Must Not

- Do not clean, restart, rebuild, clear caches, or alter the target before
  preserving relevant evidence unless emergency stabilization is explicitly
  required.
- Do not patch before establishing a baseline except for a separately authorized
  incident mitigation.
- Do not use broad logging, repository-wide searches, or full rebuilds without a
  localization question they can answer.
- Do not mask symptoms with sleeps, unbounded retries, exception suppression,
  disabled tests, arbitrary timeout increases, or global serialization.
- Do not claim root cause from correlation, a hot stack frame, one passing run,
  or symptom disappearance alone.
- Do not expose secrets, private URLs, customer data, internal hostnames, raw
  production logs, or proprietary infrastructure details.

## Completion Criteria

- The failure contract and baseline or characterization are explicit.
- Evidence identifies the earliest divergence and supports the reported
  confidence without unresolved contradictions being hidden.
- Any change follows directly from the causal chain and respects the authority
  and live-safety boundary.
- The original failure and adjacent affected scopes are verified, or the exact
  missing evidence and next experiment are reported.
- No unrelated changes, diagnostic artifacts, credentials, or private data
  remain in the repository.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return:

- Current workflow state using the exact state-machine name.
- Failure contract and scope.
- Stabilization status, if applicable.
- Observed facts, derived inferences, remaining hypotheses, and negative evidence.
- Earliest divergence, causal chain, confidence, and alternatives eliminated.
- Remediation or mitigation performed, with authority and safety basis.
- Regression oracle and verification evidence.
- Final outcome classification, residual uncertainty, and exact next action.
- On budget exhaustion, the exact stop trigger, `REMEDIATION_BUDGET_EXHAUSTED`,
  the blocking error and source, every counted attempt, current state, and the
  user action required before another tranche.

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
