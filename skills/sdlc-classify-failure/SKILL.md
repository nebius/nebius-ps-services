---
name: sdlc-classify-failure
description: "Use only as part of the Agentic SDLC workflow; use when an Agentic SDLC phase fails and the loop must classify the failure before retrying. Routes failures to requirements, context, design, plan, TDD, implementation, validation, tests, evaluation, UAT, environment, policy, or human input."
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

- Current feature and phase.
- Failure evidence.
- Retry counts.
- Latest validation, test, evaluation, UAT, or policy output.

## Required Reads

- `references/failure-taxonomy.md`.
- Current state and retry counts.
- Relevant evidence file.
- Requirement, design, and plan context.

## Writes

- Updated `failure-log.md`.
- Updated failure class and next recommended skill in state.
- Blocker summary when needed.

## Process

- Read `references/failure-taxonomy.md`.
- Classify the primary failure cause, not just the last command that failed.
- Map the failure to the earliest responsible SDLC phase.
- Increment retry count for the responsible phase.
- Stop when human input, policy block, environment block, or retry budget requires it.

## Idempotency

- Reclassifying unchanged evidence should not add duplicate failure entries.
- If new evidence changes the root cause, append a new classification with reason.
- Do not reset retry counts without explicit state repair.

## Failure Handling

- If evidence is insufficient, classify as `UNKNOWN_DEFECT` and request the minimum missing evidence.
- If the failure is human-owned, set status to blocked and summarize the question.

## Must Not

- Retry without classification.
- Overwrite old failure history.
- Persist raw secrets, logs, or private data.
- Route to merge or PR creation.

## Completion Criteria

- Failure class is recorded.
- Next skill or blocker is explicit.
- Retry budget state is updated.

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
