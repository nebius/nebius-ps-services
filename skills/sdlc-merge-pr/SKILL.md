---
name: sdlc-merge-pr
description: "Use only as part of the Agentic SDLC workflow; use only when the user explicitly asks to merge a specific pull request in the Agentic SDLC workflow. Verifies readiness and merges only when policy, checks, reviews, branch state, and UAT allow it."
---

# Merge PR

## Purpose

Merge only after explicit human instruction and final readiness verification.

## When To Use

- The user explicitly asks to merge a specific PR.
- Review, checks, branch state, and UAT evidence must be verified before merging.
- The SDLC run needs final merge evidence after PR readiness.

## When Not To Use

- Do not use for PR review; use `review-pr`.
- Do not use for PR creation; use `create-pr`.
- Do not use without explicit merge instruction.

## Inputs

- Explicit user merge request.
- PR URL or number.
- Desired merge method if specified.

## Required Reads

- PR status, checks, reviews, branch protection, and unresolved conversations when available.
- Latest UAT and review evidence.
- Current repository state.

## Writes

- Merge result.
- Local evidence entry.
- `permissions/merge-authorization.json`, immediately before the merge, with a
  short expiry and specific PR scope.
- Final run status when this completes the SDLC run.

## Process

- Confirm the user explicitly requested merge for the specific PR.
- Read PR status and verify checks pass.
- Verify required reviews pass and unresolved conversations do not block policy.
- Verify branch is up to date when required.
- Verify UAT passed.
- Write `permissions/merge-authorization.json` in the active run directory
  immediately before merge execution. Include `allowed: true`,
  `phase: "sdlc-merge-pr"`, the specific PR URL or number,
  `explicit_user_request: true`, checks status, review status, and
  `expires_at`.
- Merge using the allowed method and record result.
- Remove or expire `permissions/merge-authorization.json` after the merge
  attempt.

## Idempotency

- If PR is already merged, report merged state.
- Do not attempt repeated merge operations.
- Do not reopen or recreate PR.

## Failure Handling

- Missing explicit approval maps to `POLICY_BLOCK`.
- Missing or expired `merge-authorization.json` maps to `POLICY_BLOCK`.
- Failing checks map to blocker.
- Required review missing maps to blocker.
- Conflict maps to blocker.

## Must Not

- Merge without explicit user request.
- Override branch protection.
- Force merge.
- Merge failed UAT.
- Merge unreviewed PRs when review is required.

## Completion Criteria

- PR is merged or blocker is reported.
- Merge evidence is stored.
- SDLC run is marked complete when applicable.

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

- No skill-local references are required by default; use project files and current official documentation as needed.
