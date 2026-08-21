---
name: sdlc-implement-plan
description: "Use only as part of the Agentic SDLC workflow; after sdlc-tdd, coordinate one feature's dependency waves with isolated agents, branches, and worktrees, ordered integration, combined validation, and non-force cleanup."
---

# Implement Plan

## Help

For `$sdlc-implement-plan --help` or `$sdlc-implement-plan -h`, return concise help and stop before
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

## Purpose

Coordinate isolated task agents to implement one locked feature safely.

## When To Use

- A feature has a locked plan and tests are red, ready, or already green.
- The SDLC loop reaches implementation for the current feature.
- A proven implementation defect is still inside an active task's existing
  attempt contract.
- A post-wave implementation defect has an immutable corrective plan vN+1 with
  diagnosis-bound tasks and appended waves.

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
- For corrective work, the exact `diagnosis-v1`, stable blocker key, original
  regression oracle, affected-boundary check, and invalidation set.

## Required Reads

- Locked plan.
- Feature design and context pack.
- Relevant source files, tests, and project conventions.
- The execution-plane reference owned by `sdlc-prepare-execution` and every private wave,
  task, assignment, and result record used in the current transition.

## Writes

- Worker-owned production/configuration/test-fixture changes inside declared
  write claims, without worker-created commits.
- One worker result and one coordinator-created direct-child commit per task.
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
   Invoke private `task-arm` only after a real worker slot exists. From dispatch
   onward, invoke read-only `task-watch` every 30 seconds. Interrupt immediately
   on `WORKER_PRESTART_TIMEOUT`, `WORKER_PRESTART_MUTATION`, `WORKER_STALLED`,
   `WORKER_READ_ONLY_TIMEOUT`, `WORKER_SCOPE_VIOLATION`, or `WORKER_TIMEOUT`.
   `READ_ONLY_DEADLINE_NEAR` requires an immediate productive change or blocker
   report. After the batch commits, invoke private `batch-advance`; it creates
   the next batch's assignments and worktrees. Never start a task outside the
   active batch or reuse a session hash from a completed or recovered task.
   The sequential fallback starts each worker in a dedicated process session;
   on spawn, arm, watch, timeout, or helper failure it terminates the full
   process group, including descendants. If a worker never reached `task-start`,
   confirm that group is stopped and invoke private `task-requeue` with the
   exact recorded dispatch timestamp before allocating a fresh slot.
4. Give each worker only its task record, assignment digest, digest-bound
   incoming handoff from all earlier completed waves and batches, requirements,
   design/context reminders, test target, write claims, and stop conditions.
   The worker calls `task-start` before editing, emits direct bounded
   `task-heartbeat` calls at least every 30 seconds without a background loop,
   implements the smallest coherent change, including its part of the vertical
   end-to-end slice without widening feature scope,
   validates it, runs a task-scoped `code-review`, fixes blocking findings, and
   returns an explicit summary, decisions, open risks, validation, and review
   evidence; a commit subject is never substituted for handoff evidence. The
   coordinator calls `task-finish` with that structured evidence to recheck
   liveness, scan staged paths/content, create exactly one direct-child commit
   with normal Git hooks, and persist the result. The helper journals an exact
   tree/message/evidence finish intent before the commit so an interrupted retry
   can adopt only that commit and exact result.
   Recovery passes the recorded current attempt with explicit stopped-worker
   confirmation so only one fresh session can win an ownership transfer.
   A corrective assignment also carries its exact diagnosis ID and original
   regression oracle. The worker must implement only the diagnosis-bounded
   repair, run that oracle first, then run the counterfactual or
   affected-boundary check. `task-finish` must receive structured oracle,
   `passed` outcome, and evidence-reference fields; it binds their digest to
   the coordinator-created task commit and rejects missing or mismatched proof.
   The worker must
   not reinterpret any completed task.
5. The coordinator does not edit product files while workers run. It verifies
   every result, then runs `wave-integrate`; merges occur in stable task order
   with retained coordinator-created task commits and explicit merge commits.
6. Run combined validation/tests at the exact integration tip. For corrective
   work, run the original failed oracle, the counterfactual or affected-boundary
   check, and then validation plus unit/integration tests. Pass evidence to
   `wave-complete`, which removes only clean, reachable, registered worker
   resources using non-force Git operations.
7. Repeat for dependent waves. Route to downstream validation only after every
   wave is integrated, validated, and cleaned.
8. Rerun every failed or invalidated evaluation criterion at the new
   integration commit. Continue to documentation, alignment, and commit only
   when required evidence is bound to that commit and the current
   requirements, design, and plan fingerprints.

## Idempotency

- Reruns converge instead of duplicating resources, commits, or merges.
- If implementation already exists, verify it against the plan instead of rewriting.
- If source drift invalidates the plan, or if the vertical end-to-end slice
  cannot be implemented inside the locked boundaries, stop and route to
  `sdlc-create-plan` or `sdlc-create-design` instead of broadening scope.
- Resume from recorded task/wave/coordinator state. Do not infer a successful
  Git mutation from an error; re-observe refs, worktree registration, ancestry,
  and result digests first.
- Transfer an interrupted running task only through `task-recover` after the
  previous worker is explicitly confirmed stopped. Preserve its assignment,
  branch, worktree, and claimed changes; never use a time-based or forced
  takeover.
- A failure while its task is active retries only through that task's recorded
  attempt contract. A failure after completed waves requires an adjacent,
  append-only corrective plan and `replan-future`; it never reopens or
  reinterprets completed work.

## Failure Handling

- Return every new failure through `sdlc-classify-failure`. Do not infer a code,
  test, design, or environment owner from the symptom alone.
- If a first direct implementation repair fails with the same blocker, return
  to the classifier; `troubleshoot` diagnosis is mandatory before another
  repair dispatch.
- A probable, incomplete, unresolved, or missing-evidence diagnosis cannot
  authorize implementation.
- Undeclared worker paths, incomplete ownership, or invalidated assignments map
  to `REPLAN_REQUIRED`; merge conflicts map to `INTEGRATION_CONFLICT`; unsafe
  cleanup maps to `CLEANUP_BLOCKED` with resources retained.

## Must Not

- Change unrelated features.
- Modify locked plans.
- Hide failures.
- Remove tests to pass.
- Expand scope without design update.
- Reopen sealed, promoted, or completed execution. A post-promotion defect
  starts a corrective run from the exact promoted commit.
- Weaken acceptance criteria or change the regression oracle to make a repair
  pass.
- Let two active tasks share a worker agent, branch, worktree, write claim, or
  conflict domain.
- Let workers mutate coordinator/wave shared state except through the private
  transition helper, or use force, reset, rebase, squash, cherry-pick, or prune.

## Completion Criteria

- Planned code changes are implemented.
- Planned vertical slice is implemented or a plan/design defect is recorded.
- Focused tests are runnable or blocker is recorded.
- Changed files match implementation boundaries.
- Each task has validation and review evidence plus one verified
  coordinator-created task commit.
- Every logical wave has ordered merge evidence and no unsafe retained worker
  resource.
- Corrective workers are bound to a complete localized implementation handoff,
  and the original oracle plus every invalidated downstream gate passes at the
  new integration commit.
- State moves to `implemented`.

## SDLC Invariants

- Treat `docs/requirements.md` and `docs/design.md` as committed product truth.
- `maintain-project-specs` is the sole semantic, schema, and validation owner
  of both canonical specs. Inside Agentic SDLC, only its routed
  `sdlc-create-requirements` and `sdlc-create-design` authoring adapters may
  write their respective managed records; all other phase skills route changes
  through those adapters and return validation to the shared owner.
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
