---
name: sdlc-classify-failure
description: "Use only as part of the Agentic SDLC workflow; classify a failed phase or evaluation with deterministic evidence, diagnosis, repair budget, invalidation, and the earliest responsible retry or stop route."
user-invocable: false
---

# Classify Failure

## Help

For `$sdlc-classify-failure --help` or `$sdlc-classify-failure -h` (including native Claude forms), return concise help and stop before
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

## Agent Compatibility

Before workflow reads, load `../global-context-management/references/agent-hosts.md`
for the selected agent home, native identity and required runtime setup.

Use `$sdlc-classify-failure` in Codex, `/sdlc-classify-failure` in Claude Code, or
`/skills:sdlc-classify-failure` in the Claude plugin. Dollar-prefixed skill examples
refer to the same named skill on either host; use the native invocation syntax.
Preserve the declared invocation policy, approvals and workflow ownership.
Use available native tools; an unavailable required capability is a blocker,
never permission to bypass a guard or claim unobserved behavior.

## Coordinator Invocation Boundary

This skill requires verified workflow context from the active Agentic SDLC
coordinator. Before acting, verify the selected project, active run, phase and
coordinator ownership through the workflow's existing state helpers. Without
that evidence, return without mutation; a skill mention or automatic selection
is not workflow authorization. In Claude Code this skill is hidden from the
public command menu but remains available to the verified coordinator.

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
- A normalized `failure-event-v2` and optional `diagnosis-v3`.
- Phase retry counts and `repair-control-v2`.
- Latest validation, test, evaluation, documentation, UAT, PR, or policy
  output.
- Execution coordinator, wave/task/result records, Git identity, ancestry, and
  cleanup inventory when the failure is after plan lock.

## Required Reads

- `../worktree/references/document-delivery.md` for source coverage, phase/slice
  identities, shared-skill caller contracts and protected completion policy.

- `../worktree/references/workflow-contract.md`; bind this phase's inputs,
  assignment/ownership and evidence to the current immutable execution basis.
  Before preparation, bind evidence to the accepted objective and applicable
  spec receipts. Stale evidence cannot authorize dispatch or promotion.

- `references/failure-taxonomy.md`.
- `scripts/repair_control.py` and the active structured repair records.
- Current state and retry counts.
- Relevant evidence file.
- Requirement, design, and plan context.

## Writes

- Immutable `failure-event-v2`, `diagnosis-v3`, and classification records.
- Atomic `repair-control-v2`, append-only repair journal, and derived
  `failure-log.md`.
- Updated failure class, diagnosis pointer, invalidation set, and next
  recommended skill in state.
- Blocker summary when needed.

## Process

- Read `references/failure-taxonomy.md` and invoke
  `scripts/repair_control.py`; the helper is authoritative for event identity,
  diagnosis validation, taxonomy routing, deduplication, repair budgets,
  invalidation, and transition state.
- Record one immutable `failure-event-v2`. Its `event_id` binds the exact
  criterion, commit, fingerprints, and evidence; its `blocker_key` uses only
  stable component, operation, error class, and source boundary.
  Record `created_at` as whole-second UTC `YYYY-MM-DDTHH:MM:SSZ`; fractional
  seconds and numeric offsets are not accepted.
- If the event already proves a mechanical owner, route directly to that
  owner. A proven implementation defect bypasses troubleshooting.
- For proven `VALIDATION_DEFECT` before execution preparation, the helper routes
  to the event's exact requirements, context, design, steering, planning or
  preparation phase. Unsupported phase identities stop. Use the ordinary counted
  localized dispatch to repair the phase artifact and rerun its original check;
  preserve the failed candidate and event. This does not authorize architectural
  redesign or bypass the canonical specification publisher. Do not fabricate a
  plan or integration checkout to enter code validation. Prepared validation
  retains its existing route.
- After a failed counted validation repair, accept only a complete
  `diagnosis-v3` with result `validation_defect` and proven or high-confidence
  causality. Require the bounded repair target, affected files and original
  regression check as well as structured causal proof. It returns to the same
  existing validation route through normal classification and dispatch; the
  previous failure and all counters remain. Older active diagnoses are rejected
  without conversion; historical reports do not become current authority.
- If an evaluation failure is ambiguous, route exactly once to `troubleshoot`.
  Require its `diagnosis-v3` to return here before selecting any repair,
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
- For an active-task design revision, bind the failure's `fingerprints` to the
  exact `requirements`, `design`, `plan` and immutable execution `basis` digests
  (each `sha256:` prefixed), plus its integration commit. The existing counted
  design dispatch authorizes the Worktree revision sequence only for that
  snapshot; unknown causes and broader changes without approval remain blocked.
- For a localized implementation defect, route an active task through its
  existing attempt contract. After completed waves, require immutable
  corrective plan vN+1. After promotion, require a new corrective run from the
  promoted commit; never reopen sealed execution.
- A proven or high-confidence `ENVIRONMENT_DEFECT` at lifecycle `active_task`
  and exact phase `sdlc-implement-plan` uses `existing_task_attempt`. Establish
  an in-scope environment remedy with new evidence and hypothesis, then use a
  counted localized dispatch. Preserve the blocked receipt, independently
  observe termination, confirm runtime release, and authorize the existing
  shared fresh-worker recovery with unchanged assignment and validation.
  Require the original failed operation and assigned checks to pass through
  authorized mechanisms, followed by normal task/wave completion. Record the
  actual remediation result; success enters downstream revalidation for
  validation, tests, evaluation, documentation, alignment and commit. Neither
  classification nor dispatch grants host permissions or revives a failed
  verifier trial. Other active phases, policy/human blocks and completed work
  gain no recovery authority.
- Apply the same terminal-execution boundary to `DOCUMENTATION_DRIFT`:
  sealed, promoted or completed execution requires a new corrective run.
  Before sealing, route to documentation, alignment and commit revalidation.
- Use lifecycle `integrated_wave` when task integration succeeded but combined
  checks failed before wave completion. Record its exact failed commit and a
  complete localized diagnosis. A counted localized dispatch authorizes the
  execution owner's `wave-fail` retirement and adjacent corrective plan.
  Immutable dispatch receipts preserve historical authority after repair
  control advances. Do not call the failed wave completed or reopen its tasks.
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
- After completed waves and before promotion, if an environment block is
  externally recovered, rerun its
  exact gate on the unchanged clean integrated commit. Use private
  `record-environment-recovery --run-dir <run> --input <receipt>` only with
  schema `agentic-sdlc/environment-recovery-v2`, the active feature/event/
  classification IDs, original surface/commit/fingerprints, and a digest-bound
  passing `gate-evidence-v2` source. The helper verifies current Git and
  fingerprints, then resolves only that environment block. It preserves the
  original failure, history and budgets. It archives the sole resolved gate's
  invalidation in immutable recovery evidence and clears that pending
  projection; other pending invalidations reject recovery. It does not certify an
  earlier failed trial or accept implementation, policy or human blockers.
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
- `maintain-project-specs` is the sole semantic, schema, and validation owner
  of both canonical specs. Inside Agentic SDLC, only its routed
  `sdlc-create-requirements` and `sdlc-create-design` authoring adapters may
  write their respective managed records; all other phase skills route changes
  through those adapters and return validation to the shared owner.
- Keep run state, plans, evidence, steering, screenshots, and transcripts under `<agent-home>/sdlc-runs/<project-id>/<run-id>/`.
- When an active run exists, reload `current-state.json` and the latest
  checkpoint before changing phase or writing evidence.
- Keep one delivery candidate active; eligible tasks may span features within its frozen slice.
- Classify every failure before retrying or routing backward.
- Use MCP servers for browser, GitHub, internal docs, Slack, Confluence, Jira, and other external systems when they are available and appropriate.
- Treat hooks as invariant guardrails only; do not make hooks orchestrate the workflow.

## Successive Failure Obligations

A new failure or diagnosis archives the previous revalidation cursor and its
authorizing context before replacing the active projection. Routed
classifications carry immutable references to prior invalidations and the
canonical ordered union of earlier and newly required gates. A successful
counted repair restarts that entire sequence under its own dispatch, with no
reused gate receipts or commit binding. Earlier receipts remain historical;
exact replay preserves current progress and counters. The new blocker keeps
its own failure type, route and budget. Environment-only recovery still rejects
unrelated pending invalidations. The last allowed dispatch may finish its
revalidation; another dispatch and over-limit state remain blocked.

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
- After a proven gate-owner route repair, use the private
  `refresh-revalidation-routes` action with the exact pending cursor and evidence
  reference. Archive old progress and rerun all gates; never edit the cursor,
  transfer old passes, or reset repair budgets. Alignment is owned by `align`.
