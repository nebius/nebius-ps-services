---
name: sdlc-implement-plan
description: "Use only as part of the Agentic SDLC workflow; use after `sdlc-tdd` to coordinate one feature's dependency waves, with one fresh agent, branch, and private worktree per implementation task, ordered integration, combined validation, and non-force worker cleanup."
---

# Implement Plan

## Purpose

Coordinate isolated task agents to implement one locked feature safely.

## When To Use

- A feature has a locked plan and tests are red, ready, or already green.
- The SDLC loop reaches implementation for the current feature.
- A classified implementation defect needs repair within the same plan.

## When Not To Use

- Do not use to edit requirements, design, or locked plans.
- Do not use to broaden scope.
- Do not use to remove tests to pass.

## Inputs

- Locked plan.
- Red or ready tests.
- Feature design.
- Context pack.
- Existing codebase.
- Prepared execution coordinator, logical waves, immutable assignments, and
  integration worktree.

## Required Reads

- Locked plan.
- Feature design and context pack.
- Relevant source files, tests, and project conventions.
- The execution-plane reference owned by `sdlc-prepare-execution` and every private wave,
  task, assignment, and result record used in the current transition.

## Writes

- Worker-owned production/configuration/test-fixture changes inside declared
  write claims.
- One worker result and one direct-child commit per task.
- Ordered no-fast-forward merge commits, combined evidence, and cleanup state.

## Process

1. Reload the checkpoint and coordinator. Re-observe the integration Git root,
   common directory, branch, recorded HEAD, and cleanliness.
2. Run the private helper `seal-tdd` once. This creates the internal TDD-base
   commit when needed; do not stage or commit it by hand.
3. For the next logical wave, run `wave-prepare`. Dispatch only the current
   capacity batch. Every `TASK-*` must use its own fresh agent operating from
   its immutable worker cwd; never reuse one agent for multiple parallel tasks.
   Prefer native isolated agents. When they are unavailable, use the private
   sequential `codex exec` fallback: one fresh `--ephemeral` process per task,
   exact `--cd <scope_cwd>`, `--sandbox workspace-write`, schema-bound output,
   and no session resume or extra writable directories.
   After the batch commits, invoke private `batch-advance`; it creates the next
   batch's assignments and worktrees. Never start a task outside the active
   batch or reuse a session hash from a completed or recovered task.
4. Give each worker only its task record, assignment digest, digest-bound
   incoming handoff from all earlier completed waves and batches, requirements,
   design/context reminders, test target, write claims, and stop conditions.
   The worker calls `task-start`, implements the smallest coherent change,
   including its part of the vertical end-to-end slice without widening feature scope,
   validates it, runs a task-scoped `code-review`, fixes blocking findings, and
   returns an explicit summary, decisions, open risks, validation, and review
   evidence; a commit subject is never substituted for handoff evidence. The coordinator calls
   `task-finish` with that structured evidence to scan staged paths/content, create exactly one direct-child
   commit with normal Git hooks, and persist the result.
   Recovery passes the recorded current attempt with explicit stopped-worker
   confirmation so only one fresh session can win an ownership transfer.
5. The coordinator does not edit product files while workers run. It verifies
   every result, then runs `wave-integrate`; merges occur in stable task order
   with retained worker commits and explicit merge commits.
6. Run combined validation/tests at the exact integration tip. Pass evidence to
   `wave-complete`, which removes only clean, reachable, registered worker
   resources using non-force Git operations.
7. Repeat for dependent waves. Route to downstream validation only after every
   wave is integrated, validated, and cleaned.

## Idempotency

- Reruns converge instead of duplicating resources, commits, or merges.
- If implementation already exists, verify it against the plan instead of rewriting.
- If source drift invalidates the plan, or if the vertical slice cannot be
  implemented inside the locked boundaries, stop and route to
  `sdlc-create-plan` or `sdlc-create-design` instead of broadening scope.
- Resume from recorded task/wave/coordinator state. Do not infer a successful
  Git mutation from an error; re-observe refs, worktree registration, ancestry,
  and result digests first.
- Transfer an interrupted running task only through `task-recover` after the
  previous worker is explicitly confirmed stopped. Preserve its assignment,
  branch, worktree, and claimed changes; never use a time-based or forced
  takeover.

## Failure Handling

- If tests cannot pass due to code, fix implementation.
- If tests are wrong, route to `sdlc-tdd`.
- If design is wrong, route to `sdlc-create-design`.
- If environment is blocked, mark `ENVIRONMENT_DEFECT`.
- Undeclared worker paths, incomplete ownership, or invalidated assignments map
  to `REPLAN_REQUIRED`; merge conflicts map to `INTEGRATION_CONFLICT`; unsafe
  cleanup maps to `CLEANUP_BLOCKED` with resources retained.

## Must Not

- Change unrelated features.
- Modify locked plans.
- Hide failures.
- Remove tests to pass.
- Expand scope without design update.
- Let two active tasks share a worker agent, branch, worktree, write claim, or
  conflict domain.
- Let workers mutate coordinator/wave shared state except through the private
  transition helper, or use force, reset, rebase, squash, cherry-pick, or prune.

## Completion Criteria

- Planned code changes are implemented.
- Planned vertical slice is implemented or a plan/design defect is recorded.
- Focused tests are runnable or blocker is recorded.
- Changed files match implementation boundaries.
- Each task has validation and review evidence plus one verified worker commit.
- Every logical wave has ordered merge evidence and no unsafe retained worker
  resource.
- State moves to `implemented`.

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

- Read the execution-plane reference owned by `sdlc-prepare-execution` before
  worker dispatch, integration, cleanup, or recovery.
