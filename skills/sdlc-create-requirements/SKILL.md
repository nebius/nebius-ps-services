---
name: sdlc-create-requirements
description: "Use only as part of the Agentic SDLC workflow; use when the user provides a product idea, ticket, rough prompt, user story, change request, feature request, or optional live experiment environment details and needs `docs/requirements.md` created or updated. This Agentic SDLC skill owns requirements.md and preserves stable REQ IDs."
---

# Create Requirements

## Purpose

Convert user intent into durable, testable product requirements in `docs/requirements.md`.

## When To Use

- A project idea, ticket, issue, Slack thread, Confluence page, or rough prompt needs requirements.
- An existing requirement needs an approved update from user feedback.
- An active SDLC run discovers a spec gap that must be reflected in requirements.

## When Not To Use

- Do not use for design details; use `sdlc-create-design`.
- Do not use for local execution plans; use `sdlc-create-plan`.
- Do not use for implementation, validation, tests, UAT, PR, or merge work.

## Inputs

- User prompt or approved change request.
- Existing `docs/requirements.md` when present.
- Existing `docs/design.md` for impact awareness only.
- Optional live experiment environment details, including safe connection and
  usage instructions.
- Optional Jira, Slack, Confluence, GitHub, or pasted context.

## Required Reads

- Existing requirements file.
- Existing design file if present.
- Project README or docs if available.
- Active SDLC run state if the change happens during a run.

## Writes

- `docs/requirements.md`.
- A local run-history change summary when an SDLC run is active.

## Process

- Use `assets/templates/requirements.md.template` when creating the file.
- Extract product goal, users, constraints, non-goals, assumptions, and external systems.
- Ask for or record the optional Live Experiment Environment when the user can
  provide one: status, environment type, non-production confirmation, safe
  access reference, connection steps, allowed and prohibited agent actions, test
  data, reset process, approvals, and evidence rules.
- If a user provides raw credentials, private endpoints, customer data, or
  sensitive logs, replace them with placeholders or safe references and ask for
  an approved credential delivery mechanism instead of committing the values.
- Break intent into stable `REQ-*` blocks with acceptance and negative criteria.
- Add inputs, outputs, validation method, test method, evaluation method, priority, and risk to each requirement.
- Preserve existing requirement IDs and append new IDs instead of renumbering.
- Update the requirements decision log and change log.
- Mark unclear items as open questions instead of guessing.

## Idempotency

- Reapplying the same prompt must not duplicate requirements.
- If an existing requirement changes, update that `REQ-*` block and append a change-log entry.
- If design must change, mark the affected requirements so `sdlc-create-design` can update related features.

## Failure Handling

- If ambiguity is non-blocking, create draft requirements and list open questions.
- If ambiguity blocks meaningful progress, stop with the required questions.
- If the existing requirements file is malformed, repair structure without changing intent.

## Must Not

- Edit `docs/design.md`.
- Create execution plans.
- Implement code or tests.
- Rename existing requirement IDs.
- Delete accepted requirements without explicit user instruction.
- Store secrets, private endpoints, customer data, or raw logs in
  `docs/requirements.md`.
- Mark a live experiment environment safe unless non-production or disposable
  scope and allowed operations are explicit.

## Completion Criteria

- `docs/requirements.md` exists.
- Every requirement has acceptance criteria, validation method, test method, and evaluation method.
- The Live Experiment Environment section has a status; when provided, it
  records safe access references, allowed operations, reset instructions, and
  evidence limits.
- Open questions and change log are explicit.

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

- Scope handled and current `REQ-*` or `FEAT-*` IDs.
- Files or local state written.
- Evidence created or checked.
- Failure classification and next recommended skill when blocked.
- Confirmation that private SDLC state was kept out of committed project files.

## References

- Use `assets/templates/requirements.md.template` when creating the corresponding artifact.
