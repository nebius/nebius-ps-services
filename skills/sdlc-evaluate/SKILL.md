---
name: sdlc-evaluate
description: "Use only as part of the Agentic SDLC workflow; use after validation and tests to determine whether the current feature and any planned end-to-end slice solve the real-world requirement using acceptance criteria, the correct evaluation harness, a confirmed safe live experiment environment when needed, and narrowly gated observability evidence for predefined runtime operational criteria."
---

# SDLC Evaluate

## Help

For `$sdlc-evaluate --help` or `$sdlc-evaluate -h`, return concise help and stop before
any workflow step. Include the purpose, invocation policy, public usage/actions,
and `-h, --help` plus only documented skill-level options; say "No additional
public flags" when none exist. For internal or coordinator-only skills, state
that boundary and that no standalone public workflow action exists. After the
selected `SKILL.md` is loaded, help is report-only: do not call any additional
tools, inspect project state, or modify files, private state, Git, or external
systems. Never
expose private helper actions or treat help as workflow authorization.

## Purpose

Evaluate observed product behavior against real-world acceptance criteria.

## When To Use

- Validation and tests pass and feature acceptance must be observed.
- The product is GUI, TUI, CLI, API, library, service, or infrastructure and needs evidence.
- A prior evaluation defect needs rerun evidence.

## When Not To Use

- Do not treat unit tests as evaluation.
- Do not approve behavior without observation.
- Do not change code directly unless routed back to implementation.

## Inputs

- Feature design.
- Acceptance criteria.
- Validation evidence.
- Test evidence.
- Product type.
- Live Experiment Environment section from `docs/requirements.md`, when present.
- Predefined operational acceptance criteria and explicit deployed scope when
  runtime observability may be relevant.

## Required Reads

- Requirement block.
- Live Experiment Environment section in `docs/requirements.md`.
- Feature design.
- Locked plan.
- Validation and test evidence.
- App startup instructions.

## Writes

- `evidence/FEAT-*/evaluate.md`.
- Screenshots, transcripts, or API/service summaries as appropriate.
- Per-criterion `pass`, `fail`, or `inconclusive` result.
- For every failed gate, one immutable `failure-event-v1` recorded through
  `scripts/failure_contract.py`.

## Process

- Run from the registered integration worktree and verify its branch, Git common
  directory, exact recorded HEAD, and cleanliness before evaluation. Bind
  evidence to that SHA; do not evaluate a worker or the stale project checkout.
- Use `assets/templates/evaluate.md.template` for evidence.
- Select the evaluation route: `sdlc-gui-test`, `sdlc-tui-test`, API/service
  checks, observability-backed operational checks, or manual review.
- Use the Live Experiment Environment only when it is marked provided, has
  explicit non-production or disposable confirmation, and the intended checks
  fit the recorded allowed actions. Otherwise fall back to local, mocked, dry
  run, or manual evaluation as appropriate.
- Run functional and semantic evaluation first. Observability does not replace
  functional or semantic checks, product behavior observation, or agent trace
  evaluation.
- Consider observability only for a predefined runtime operational criterion:
  release, canary, performance, reliability, resilience, capacity, or
  operational efficiency. Before any Grafana readiness or data call, create one
  query-admission record for one criterion. Require its criterion ID, exact
  measurement and unit, comparator and threshold, authoritative scope,
  candidate version or workload identity, candidate and baseline/control
  attribution, absolute candidate and baseline/control windows, required
  coverage, explicit pass and fail conditions, explicit inconclusive
  conditions, and how the result can change the criterion grade.
- Identify one matching metric, log fingerprint, trace selector, platform-state
  signal, or observed-change feed before readiness. Record
  non-Grafana provenance that the signal is expected to be Grafana-backed for
  the deployed target: explicit user or requirements evidence,
  instrumentation/exporter configuration, a repository-owned dashboard or
  rule, a catalog or runbook mapping, or a known Grafana-backed feed. A
  signal-family guess, datasource listing, or readiness call is not valid
  signal discovery.
- Resolve authority from the user or committed requirements. Existing service
  catalogs, deployment manifests, Helm, Terraform, or CI metadata may narrow
  deployed selectors but do not grant or broaden authority.
- Prefer already captured, frozen, or mocked telemetry when it satisfies the
  criterion and attribution requirements. Skip Grafana with zero calls when
  the criterion is optional, already decidable from available evidence, cannot
  change the evaluation result, lacks a complete query-admission record, or has
  no evidenced matching signal. A required but underspecified operational
  criterion is `inconclusive` with `SPEC_GAP`; it is never silently passed.
- When the observability gate passes, invoke `$nebius-grafana-query` in
  evidence-provider mode with exactly one signal family, its structured
  `signal_fit`, and the structured `criterion_fit` from the query-admission
  record. Reuse its caller-owned
  `unknown | available | unavailable` connectivity state for this evaluation
  run and pass its returned total, fast, and deep remaining query budgets into
  every later provider request. After `unavailable`, skip later observability
  without another check, installer handoff, setup, repair, or credential
  switch.
- Admit at most one data query per provider invocation, then update the
  criterion evidence ledger before considering another. A later query requires
  one new exact grade-changing question and a refreshed query-admission record.
  Remaining query budget is a ceiling, not a target; never batch independent
  criteria or signal families.
- Use deep mode only when fast evidence leaves at least two named attribution,
  coverage, or dependency interpretations of the predefined operational
  criterion indistinguishable, deep-stage budget remains, and one
  criterion-specific query can change the grade. Do not use deep mode to
  discover a missing gate or investigate root cause.
- Passive read-only production telemetry may pass or fail only the predefined
  operational criterion when candidate attribution, baseline comparability,
  coverage, and scope are complete. Otherwise record `inconclusive`.
- Execute a controlled workload only through the existing evaluation route and
  only in a confirmed non-production or disposable Live Experiment Environment
  whose allowed actions include it. The observability provider must not execute
  a production workload.
- Treat no data as a data gap unless signal semantics and complete coverage
  prove that absence means zero or healthy.
- Compare observed behavior against acceptance and negative criteria.
- When the locked plan defines an end-to-end slice, observe the feature through
  that slice's user-visible or system-visible flow. Layer-isolated checks alone
  are not enough to mark the feature evaluated unless the plan says no vertical
  slice applies.
- Record control, observation, and evaluation evidence.
- For each `fail`, run `scripts/failure_contract.py` with the criterion ID,
  expected and observed behavior, evidence digests, exact reproduction oracle,
  integration commit, requirements/design/plan fingerprints, execution
  lifecycle, and stable component, operation, error class, and source boundary.
- Mark `cause_status` as `proven` or `high_confidence` only when current
  evidence already establishes the responsible mechanical owner. Otherwise
  emit `ambiguous`; do not guess implementation or design.
- Pass the resulting `failure-event-v1` to `sdlc-classify-failure`. Proven test,
  implementation, specification, evaluator, environment, policy, human, or
  design causes take their direct owner route. An ambiguous evaluation failure
  enters conditional troubleshooting.

## Idempotency

- Rerunning evaluation refreshes evidence while exact duplicate failure events
  remain no-ops.
- If product behavior is unchanged, outcome should be stable.
- Failed evaluation routes back to the correct phase.

## Failure Handling

- A behavior mismatch with an unproven owner emits an ambiguous
  `EVALUATION_DEFECT` event for conditional troubleshooting; it does not route
  directly to implementation or design.
- A proven local implementation, test, specification, evaluator, environment,
  policy, human, or design cause records that exact proposed class.
- Missing end-to-end slice observation maps to `EVALUATION_DEFECT` or
  `ENVIRONMENT_DEFECT` based on whether product behavior or access prevented
  the observation.
- Automation failure maps to `ENVIRONMENT_DEFECT` or `EVALUATION_DEFECT` based on cause.
- Missing required live environment access maps to `ENVIRONMENT_DEFECT` or
  `HUMAN_INPUT_REQUIRED` based on whether setup or a human-owned decision is
  missing.
- Required operational telemetry blocked by a tool, authentication, network,
  endpoint, or service failure maps to `ENVIRONMENT_DEFECT`.
- Provider rejection routing is deterministic:
  - `missing_authority` maps to `HUMAN_INPUT_REQUIRED`;
  - `unresolved_selector`, `invalid_window`, and `irrelevant_evidence` map to
    `SPEC_GAP`; and
  - `invalid_budget` maps to `POLICY_BLOCK`.
- Missing signal semantics, threshold, attribution, baseline, or coverage
  needed to grade the operational criterion, missing matching-signal
  provenance, or an incomplete criterion-fit record maps to `SPEC_GAP` unless
  an environment defect caused the gap.
- A provider `rejected` result is never `ENVIRONMENT_DEFECT`.
- Unsafe or unconfirmed live environment use maps to `POLICY_BLOCK`.
- Acceptance issue maps to `SPEC_GAP`.
- Design mismatch maps to `DESIGN_DEFECT`.
- Failure to find an implementation bug is `UNKNOWN_DEFECT` or unresolved, not
  design evidence.

## Must Not

- Ignore negative criteria.
- Mark pass without observable evidence.
- Overwrite validation or test evidence.
- Use a production or unconfirmed environment for live experiments.
- Treat passive production telemetry as permission to execute a workload,
  mutate production, broaden scope, or grade functional acceptance.
- Invoke Grafana when runtime evidence cannot change a predefined criterion or
  when the criterion-fit record, matching-signal provenance, scope, selectors,
  attribution, or time windows are unproven.
- Use Grafana readiness or datasource discovery to search for a useful signal
  or grading rule.
- Batch queries across criteria or signal families, or issue another query
  before recording why its exact result can change the grade.
- Invoke the Grafana installer, repair authentication, or retry a failed
  readiness check from evaluation.
- Exceed the allowed actions recorded in `docs/requirements.md`.
- Store credentials, private endpoints, customer data, or raw logs in evidence.

## Completion Criteria

- Evaluation evidence exists.
- Acceptance criteria are explicitly `pass`, `fail`, or `inconclusive`.
- Planned end-to-end slice observation is recorded or a blocker is classified.
- State moves to `evaluated` only when every required criterion passes.

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
- Observability used, skipped, partial, unavailable, or inconclusive; authority
  and selector provenance; admitted criterion, signal, measurement, grading
  rule, and signal provenance; candidate/control attribution and windows;
  connectivity and query cost; coverage; and data gaps when runtime evidence
  was considered.
- Failure classification and next recommended skill when blocked.
- Confirmation that private SDLC state was kept out of committed project files.

## References

- Use `assets/templates/evaluate.md.template` when creating the corresponding artifact.
- Use `scripts/failure_contract.py` to normalize and record every failed
  criterion before classification.
