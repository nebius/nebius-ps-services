---
name: sdlc-auto-steering
description: "Use only as part of the Agentic SDLC workflow; use when an active prompt-bound SDLC run needs private `STEERING.md` refreshed from an accepted same-prompt revision, requirements, design, context, locked plans, fingerprints, or recent evidence before selecting the next phase."
---

# SDLC Auto Steering

## Help

For `$sdlc-auto-steering --help` or `$sdlc-auto-steering -h`, return concise help and stop before
any workflow step. Include the purpose, invocation policy, public usage/actions,
and `-h, --help` plus only documented skill-level options; say "No additional
public flags" when none exist. For internal or coordinator-only skills, state
that boundary and that no standalone public workflow action exists. After the
selected `SKILL.md` is loaded, help is report-only: do not call any additional
tools, inspect project state, or modify files, private state, Git, or external
systems. Never
expose private helper actions or treat help as workflow authorization.

## Purpose

Refresh private runtime steering for an active Agentic SDLC run without
changing committed product-truth documents directly.

## When To Use

- `sdlc-start` accepts a changed revision of the run's bound managed prompt.
- `STEERING.md` has pending, stale, or conflicting entries.
- Requirements, design, project instructions, context, locked plan,
  fingerprints, or evidence changed and the active feature needs compact
  reminders before the next phase.
- The feature loop is about to enter planning, implementation, validation,
  tests, evaluation, document update, UAT, PR creation, or review.

## When Not To Use

- Do not use outside an active Agentic SDLC run.
- Do not use to author initial requirements or design.
- Do not use to decide the next SDLC phase directly; `sdlc-start` owns routing.
- Do not use as a cron job, daemon, workflow CLI, or hook orchestrator.
- Do not use to update committed README, changelog, or project docs; use
  `sdlc-update-documents`.

## Inputs

- Accepted prompt ID, revision, digest, and immutable snapshot from
  `sdlc-start` intake.
- `docs/requirements.md`.
- `docs/design.md` when present.
- The active selected-project instruction file and latest verified
  `project-agent-instructions` state when present.
- Active run state under `~/.codex/sdlc-runs/<project-id>/<run-id>/`.
- Existing `STEERING.md`.
- Current feature context pack, locked plan, fingerprints, checkpoint, and
  recent evidence.

## Required Reads

- `docs/requirements.md`.
- `docs/design.md` when present.
- The active selected-project instruction file and latest verified
  `project-agent-instructions` state when present.
- `run.json`, `current-state.json`, `feature-queue.json`, `fingerprints.json`,
  `checkpoints/latest.json`, and the latest checkpoint.
- `prompt.json` and the exact accepted immutable prompt snapshot.
- `STEERING.md`.
- Current feature context pack and locked plan when present.
- Recent validation, test, evaluation, document-update, UAT, PR, review, or
  failure evidence relevant to the current feature.
- `assets/templates/STEERING.md.template` when creating or repairing steering.
- `assets/templates/auto-steering.json.template` when creating or repairing
  machine-readable steering state.

## Writes

- `STEERING.md` in the active private run directory.
- `steering/auto-steering.json` in the active private run directory.
- Steering fingerprint or summary fields in `fingerprints.json` when needed.
- A checkpoint or history note only through the coordinator handoff expected by
  `sdlc-start`; this skill does not bypass checkpoint ownership.

## Process

- Reload active state and existing steering before classifying anything.
- Append each pending accepted prompt revision to the steering inbox exactly
  once. Link its prompt ID, revision, digest, and snapshot pointer; store only a
  compact redacted summary, never raw prompt text, and redact secrets,
  credentials, private endpoints, customer data, raw logs, or other unsafe
  material.
- Classify every unresolved steering entry as one of:
  `runtime-only`, `requirements-change`, `design-change`,
  `project-agent-instructions-change`, `docs-update`, `resolved`,
  `superseded`, `rejected`, or `needs-human`.
- Treat `requirements-change` and `design-change` as routing requests for
  `sdlc-start`; do not let them become implementation truth until the owning
  product-truth document has been updated by `sdlc-create-requirements` or
  `sdlc-create-design`.
- Treat `project-agent-instructions-change` as a request for `sdlc-start` to
  rerun the conditional decision. Never edit a project instruction file here.
- If execution is prepared or running, include its plan digest, integration
  identity, active wave, started assignments, and cleanup blockers in routing
  reminders. A product-truth or plan change must request `REPLAN_REQUIRED` and
  preserve existing resources; steering must never reset them.
- Derive compact active reminders from requirements, design, current feature,
  locked plan, context, evidence, and unresolved steering entries.
- Keep `STEERING.md` human-readable and keep `steering/auto-steering.json`
  machine-readable with the same pending disposition state.
- After both ledgers are durably updated, invoke the private prompt helper's
  `steering-resolve` transition with `applied`, `blocked`, or `no_effect`.
- Return the requested routing signal for `sdlc-start` instead of invoking the
  next SDLC phase directly.

## Idempotency

- Rerunning with an unchanged accepted revision, specs, fingerprints, evidence, and steering
  must not duplicate inbox entries or reminders.
- Preserve append-only revision history unless an entry is redacted, superseded,
  or explicitly marked resolved.
- Replace compact active reminders with the newest derived set for the current
  feature instead of accumulating stale reminders.
- Keep resolved or superseded entries available as history, but do not inject
  them into the active reminder set.

## Failure Handling

- Missing active run state maps to `HUMAN_INPUT_REQUIRED` and routes to
  `sdlc-start`.
- Conflicting steering that cannot be resolved from product-truth docs maps to
  `needs-human`.
- Unsafe prompt content is redacted and classified with a safe summary; raw
  secrets or customer data must not be persisted.
- Product-truth drift routes to `sdlc-create-requirements`,
  `sdlc-create-design`, or `project-agent-instructions`.
- Documentation-only drift routes to `sdlc-update-documents`.

## Must Not

- Commit `STEERING.md` or private steering state.
- Edit `docs/requirements.md` or `docs/design.md`.
- Edit `AGENTS.md`, `AGENTS.override.md`, or a configured instruction fallback.
- Edit implementation code, tests, project README, changelog, or PR text.
- Use OS cron, background daemons, or hooks as the authoritative steering
  engine.
- Store secrets, tokens, private endpoints, customer data, or raw noisy logs in
  steering state.
- Inject the entire steering inbox into the parent context; inject only compact
  active reminders and unresolved routing decisions.

## Completion Criteria

- New accepted prompt revisions are linked or safely summarized in `STEERING.md`.
- Every unresolved steering entry has a disposition.
- Active reminders are compact, current, and tied to the active feature.
- Machine-readable steering state agrees with `STEERING.md`.
- `sdlc-start` has enough information to choose exactly one next skill.

## SDLC Invariants

- Treat `docs/requirements.md`, `docs/design.md`, and any
  provenance-owned generated project-root `AGENTS.md` as committed project
  truth.
- Only `sdlc-create-requirements` writes `docs/requirements.md`; only
  `sdlc-create-design` writes `docs/design.md`; only
  `project-agent-instructions` creates or refreshes its generated project-root
  `AGENTS.md`. Other skills route changes to those owners.
- Keep run state, plans, evidence, steering, screenshots, and transcripts under
  `~/.codex/sdlc-runs/<project-id>/<run-id>/`.
- When an active run exists, reload `current-state.json` and the latest
  checkpoint before changing phase or writing evidence.
- Work on one feature at a time unless the user explicitly asks for a different
  SDLC shape.
- Classify every failure before retrying or routing backward.
- Treat hooks as invariant guardrails only; do not make hooks orchestrate the
  workflow.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return a concise result with:

- Active run and feature handled.
- Steering entries appended, redacted, resolved, superseded, or left pending.
- Active reminders created or refreshed.
- Routing request for `sdlc-start`, if any.
- Failure classification and next action when blocked.
- Confirmation that private steering state was kept out of committed project
  files.

## References

- Use `assets/templates/STEERING.md.template` when creating or repairing the
  human-readable steering ledger.
- Use `assets/templates/auto-steering.json.template` when creating or repairing
  machine-readable steering state.
