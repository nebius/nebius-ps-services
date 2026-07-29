---
name: sdlc-classify-failure
description: "Use only as part of the Agentic SDLC workflow; use when a phase or evaluation fails and deterministic evidence, diagnosis validation, repair budgets, invalidation, and the earliest responsible retry or stop route must be recorded before any loop retry."
---

# Classify Failure

## Purpose

Classify failures and select the correct retry or stop route before any SDLC loop retry.

## When To Use

- Any SDLC phase fails.
- A retry budget might be consumed.
- The loop needs to choose between repair, reroute, blocker, or human input.

## When Not To Use

- Do not use to hide or ignore failures.
- Do not use to change specs or code directly.
- Do not use as a generic issue triage skill outside SDLC state.

## Inputs

- Current feature, phase, exact integration commit, and execution lifecycle.
- A normalized `failure-event-v1` and optional `diagnosis-v1`.
- Phase retry counts and `repair-control-v1`.
- Latest validation, test, evaluation, documentation, UAT, PR, or policy
  output.
- Execution coordinator, wave/task/result records, Git identity, ancestry, and
  cleanup inventory when the failure is after plan lock.

## Required Reads

- `references/failure-taxonomy.md`.
- `scripts/repair_control.py` and the active structured repair records.
- Current state and retry counts.
- Relevant evidence file.
- Requirement, design, and plan context.

## Writes

- Immutable `failure-event-v1`, `diagnosis-v1`, and classification records.
- Atomic `repair-control-v1`, append-only repair journal, and derived
  `failure-log.md`.
- Updated failure class, diagnosis pointer, invalidation set, and next
  recommended skill in state.
- Blocker summary when needed.

## Process

- Read `references/failure-taxonomy.md` and invoke
  `scripts/repair_control.py`; the helper is authoritative for event identity,
  diagnosis validation, taxonomy routing, deduplication, repair budgets,
  invalidation, and transition state.
- Record one immutable `failure-event-v1`. Its `event_id` binds the exact
  criterion, commit, fingerprints, and evidence; its `blocker_key` uses only
  stable component, operation, error class, and source boundary.
- If the event already proves a mechanical owner, route directly to that
  owner. A proven implementation defect bypasses troubleshooting.
- If an evaluation failure is ambiguous, route exactly once to `troubleshoot`.
  Require its `diagnosis-v1` to return here before selecting any repair,
  specification, test, evaluator, environment, or design owner.
- Treat `probable`, incomplete, stale, malformed, tampered, or secret-bearing
  diagnoses as non-authorizing. `UNKNOWN_DEFECT`, missing decisive evidence,
  or competing hypotheses stop; lack of an implementation bug never implies a
  design defect.
- Admit `DESIGN_DEFECT` only when positive evidence passes the design gate:
  stable requirements, valid evaluator and environment, reproducible failure,
  a named violated system contract, evidence that localized repair is
  insufficient, affected `FEAT-*` closure, invalidation scope, work estimate,
  rollback, and `proven` or `high_confidence` causality.
- For a localized implementation defect, route an active task through its
  existing attempt contract. After completed waves, require immutable
  corrective plan vN+1. After promotion, require a new corrective run from the
  promoted commit; never reopen sealed execution.
- Increment remediation counts only through a helper-authorized dispatch.
  Enforce two localized repairs, one design repair, three total blocker
  attempts, 60 active minutes, and four repair dispatches per feature.
- Before every retry, require new evidence and a genuinely new falsifiable
  hypothesis. One failed direct repair makes troubleshooting mandatory before
  attempt two. A failed design repair, evidence-free semantic phase cycling, or
  a reached ceiling stops immediately.
- Record invalidations from the responsible owner. Do not weaken acceptance
  criteria: requirements changes route to `sdlc-create-requirements`.
- A successful remediation enters `revalidation_required`, not terminal
  `resolved`, whenever evidence was invalidated. Advance the deterministic
  cursor only with digest-verified, commit-bound evidence for its exact next
  gate. Bind pre-commit gates to the live integration HEAD and the final commit
  gate to completed promotion, the clean promoted project HEAD on the recorded
  base branch, and verified integration cleanup; mark the repair resolved only
  after that final gate.
- Stop when human input, policy block, environment block, or retry budget requires it.
- Preserve every dirty, divergent, unreachable, malformed, or foreign Git
  resource. Classification never authorizes reset, history rewrite, or force
  cleanup.

## Idempotency

- Reclassifying unchanged evidence, replaying a completed transition, or
  recording the same event must be an exact no-op.
- If new evidence changes the root cause, append a new classification with reason.
- Do not reset retry counts without explicit state repair.

## Failure Handling

- If evidence is insufficient, classify as `UNKNOWN_DEFECT`, record
  `BLOCKED_MISSING_EVIDENCE` or `UNRESOLVED`, and stop with the minimum missing
  evidence.
- If the failure is human-owned, set status to blocked and summarize the question.

## Must Not

- Retry without classification.
- Let an agent-proposed class override deterministic helper validation.
- Route an ambiguous failure directly to implementation or design.
- Treat "no implementation bug found" as design evidence.
- Overwrite old failure history.
- Persist raw secrets, logs, or private data.
- Route to merge or PR creation.

## Completion Criteria

- Failure class is recorded.
- Next skill or blocker is explicit.
- Retry budget state is updated.
- Failure, diagnosis, repair-control, journal, failure-log, and checkpoint
  pointers agree.

## SDLC Invariants

- Treat `docs/requirements.md` and `docs/design.md` as committed product truth.
- Only `sdlc-create-requirements` writes `docs/requirements.md`; only `sdlc-create-design`
  writes `docs/design.md`. Other skills route spec changes to those owners.
- Keep run state, plans, evidence, steering, screenshots, and transcripts under `~/.codex/sdlc-runs/<project-id>/<run-id>/`.
- When an active run exists, reload `current-state.json` and the latest
  checkpoint before changing phase or writing evidence.
- Work on one feature at a time unless the user explicitly asks for a different SDLC shape.
- Classify every failure before retrying or routing backward.
- Use MCP servers for browser, GitHub, internal docs, Slack, Confluence, Jira, and other external systems when they are available and appropriate.
- Treat hooks as invariant guardrails only; do not make hooks orchestrate the workflow.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return a concise result with:

- Scope handled and current `REQ-*` or `FEAT-*` IDs.
- Files or local state written.
- Evidence created or checked.
- Failure classification and next recommended skill when blocked.
- Confirmation that private SDLC state was kept out of committed project files.

## References

- Read `references/failure-taxonomy.md` when this skill needs that local policy or schema.
- Use `scripts/repair_control.py` for every failure, diagnosis, broader-design
  approval, classification, diagnostic-experiment, remediation-dispatch, and
  completion and invalidated-gate revalidation transition.
