---
name: sdlc-auto-steering
description: "Use only as part of the Agentic SDLC workflow; use when an active SDLC run needs private `STEERING.md` refreshed from mid-run user prompts, requirements, design, context, locked plans, fingerprints, or recent evidence before selecting the next phase."
---

# SDLC Auto Steering

## Purpose

Refresh private runtime steering for an active Agentic SDLC run without
changing committed product-truth documents directly.

## When To Use

- `sdlc-start` detects new user instructions submitted during an active run.
- `STEERING.md` has pending, stale, or conflicting entries.
- Requirements, design, context, locked plan, fingerprints, or evidence changed
  and the active feature needs compact reminders before the next phase.
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

- Active user prompt or continuation prompt.
- `docs/requirements.md`.
- `docs/design.md` when present.
- Active run state under `~/.codex/sdlc-runs/<project-id>/<run-id>/`.
- Existing `STEERING.md`.
- Current feature context pack, locked plan, fingerprints, checkpoint, and
  recent evidence.

## Required Reads

- `docs/requirements.md`.
- `docs/design.md` when present.
- `run.json`, `current-state.json`, `feature-queue.json`, `fingerprints.json`,
  `checkpoints/latest.json`, and the latest checkpoint.
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
- Append each new mid-run user prompt to the steering inbox, using a redacted
  summary when the prompt contains secrets, credentials, private endpoints,
  customer data, raw logs, or other unsafe material.
- Classify every unresolved steering entry as one of:
  `runtime-only`, `requirements-change`, `design-change`, `docs-update`,
  `resolved`, `superseded`, `rejected`, or `needs-human`.
- Treat `requirements-change` and `design-change` as routing requests for
  `sdlc-start`; do not let them become implementation truth until the owning
  product-truth document has been updated by `sdlc-create-requirements` or
  `sdlc-create-design`.
- Derive compact active reminders from requirements, design, current feature,
  locked plan, context, evidence, and unresolved steering entries.
- Keep `STEERING.md` human-readable and keep `steering/auto-steering.json`
  machine-readable with the same pending disposition state.
- Return the requested routing signal for `sdlc-start` instead of invoking the
  next SDLC phase directly.

## Idempotency

- Rerunning with unchanged prompts, specs, fingerprints, evidence, and steering
  must not duplicate inbox entries or reminders.
- Preserve append-only prompt history unless an entry is redacted, superseded,
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
- Product-truth drift routes to `sdlc-create-requirements` or
  `sdlc-create-design`.
- Documentation-only drift routes to `sdlc-update-documents`.

## Must Not

- Commit `STEERING.md` or private steering state.
- Edit `docs/requirements.md` or `docs/design.md`.
- Edit implementation code, tests, project README, changelog, or PR text.
- Use OS cron, background daemons, or hooks as the authoritative steering
  engine.
- Store secrets, tokens, private endpoints, customer data, or raw noisy logs in
  steering state.
- Inject the entire steering inbox into the parent context; inject only compact
  active reminders and unresolved routing decisions.

## Completion Criteria

- New user prompts are recorded or safely redacted in `STEERING.md`.
- Every unresolved steering entry has a disposition.
- Active reminders are compact, current, and tied to the active feature.
- Machine-readable steering state agrees with `STEERING.md`.
- `sdlc-start` has enough information to choose exactly one next skill.

## SDLC Invariants

- Treat `docs/requirements.md` and `docs/design.md` as committed product truth.
- Only `sdlc-create-requirements` writes `docs/requirements.md`; only
  `sdlc-create-design` writes `docs/design.md`. Other skills route spec
  changes to those owners.
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

When using this skill, capture durable, reusable, public-safe learnings back
into this skill's local source materials before completion when the current task
contract allows source edits. Update the narrowest appropriate surface:
`SKILL.md` for runtime rules, `references/` for detailed guidance, `assets/`
for reusable templates, `scripts/` for deterministic helpers, and README or
changelog entries for human-facing or release-note updates.

If the current task is explicitly read-only/report-only, or source writes are
outside this skill's task contract, do not edit skill sources; report the
skipped source update instead.

Do not capture secrets, private URLs, customer data, raw logs, one-off local
state, or unverified/vendor-specific claims. If a useful learning is not safe,
not evidence-backed, or outside this skill's scope, report that it was skipped.

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
