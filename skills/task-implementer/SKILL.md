---
name: task-implementer
description: "Use only when the user explicitly asks for sequential brownfield implementation loops: break work into ordered task-1..task-n items, inspect target code first, route architecture/contracts/missing-code/ambiguous boundaries through design when available, then implement one task per fresh Codex session with validation, code-review, fixes, $commit, and a markdown handoff checkpoint. Do not use for ordinary one-shot implementation, Agentic SDLC, chat-only brainstorming, standalone code review, standalone commits, PRs, or parallel multi-agent edits."
---

# Task Implementer

## Purpose

Coordinate small to medium brownfield implementation work by turning the user
request into an ordered task queue, then implementing that queue sequentially
with a reviewed, committed checkpoint and markdown handoff between fresh Codex
sessions.

This is a lightweight implementation loop, not the Agentic SDLC state machine.

## When To Use

- The user explicitly invokes `$task-implementer`.
- The user asks for a sequential task implementation loop.
- A brownfield request contains multiple dependent code, test, docs, config, or
  validation tasks and would benefit from fresh context between tasks.
- The user wants task ordering, per-task review, per-task local commits,
  checkpoint handoff, and no concurrent write agents.

## When Not To Use

- Do not use for a normal single-turn implementation that fits cleanly in the
  current context; implement directly with the applicable project skills.
- Do not use for the Agentic SDLC workflow; use `$sdlc-start` and `sdlc-*`
  phase skills.
- Do not use for chat-only ideation; use `brainstorm`.
- Do not use for design review without implementation; use `design` and
  `system-design-rules` when available and appropriate.
- Do not use for review-only work; use `code-review`, `review-pr`, or `align`.
- Do not use for standalone commits, pushes, PR creation, PR review, or merge.
  Use `commit`, `commit-push`, `create-pr`, `review-pr`, or `merge-pr`
  directly for those jobs.
- Do not run parallel write-capable agents or overlapping implementation
  sessions in the same workspace.

## Inputs

- The user's implementation prompt, constraints, acceptance criteria, and
  explicit non-goals.
- The current repository, working tree, visible branch state, and local
  instructions such as `AGENTS.md`.
- Optional user-provided paths, tickets, issue text, logs, screenshots,
  sketches, or existing design notes.
- Optional existing task-implementer handoff markdown from a previous session.

## Required Reads

- Existing task-implementer handoff markdown when the prompt is a continuation.
- `references/implementation-loop.md` before creating the queue or launching
  follow-on sessions.
- The `code-review` skill before reviewing the active task's code changes.
- The `commit` skill before committing the active task checkpoint.
- The relevant `AGENTS.md`, README, design docs, changelog, tests, and source
  files for the target code.
- `docs/agentic-sdlc-design.md` only when the boundary with Agentic SDLC is
  unclear.
- The `design` skill when the task needs architecture, contracts, missing code,
  or ambiguous implementation boundaries and that skill is available. Let
  `design` use `research`; do not duplicate research orchestration here.
- Current official vendor documentation when product, cloud, API, CLI, SDK,
  framework, package manager, or version-sensitive behavior affects the change.

## Writes

- Private local handoff markdown under:

  ```text
  ${CODEX_HOME:-$HOME/.codex}/task-implementer/<project-id>/<run-id>/handoff.md
  ```

- Focused source, test, docs, changelog, config, or generated-artifact edits
  required by the active task.
- Per-task validation, review, fix, and local commit evidence summarized in
  the handoff; avoid raw logs.

Do not commit the handoff file, raw logs, secrets, private endpoints, customer
data, or broad copied documentation.

## Process

1. Confirm the prompt asks for a sequential brownfield implementation loop or
   continuation from an existing handoff. If not, do not continue as
   `task-implementer`.
2. Read an existing handoff if supplied; otherwise create a new private handoff
   from `assets/handoff-template.md`.
3. Inspect the target code before ordering tasks. Use targeted `rg`, small file
   reads, and local docs instead of guessing from the prompt alone.
4. Extract concrete work items from the prompt and repo evidence. Merge
   duplicates and split only when a task has a clear independent result.
5. Identify dependencies and order tasks as `task-1`, `task-2`, ..., `task-n`.
   Order by prerequisite relationships first, then user priority, user-visible
   risk, shared contracts, and validation value.
6. For each task, record: goal, rationale, dependencies, likely files, design
   need, implementation steps, validation commands, done criteria, and rollback
   notes.
7. If design is needed, invoke or explicitly route to `design` before editing
   the task. If `design` is unavailable, do a compact local design pass and mark
   the handoff assumption as `design_skill_unavailable`.
8. Implement only the active task. Keep edits within its boundary; update docs,
   README, and changelog only when they are in scope for that task.
9. Run the narrowest relevant validation for the active task. Broaden only when
   the changed surface requires it.
10. Inspect the diff after each task. Do not hide failures, weaken tests, or
    leave unrelated cleanup mixed into the task.
11. Invoke `code-review` on the active task's code changes. Treat review
    findings as a required gate before handoff.
12. Fix the review findings that are safe, scoped to the active task, and
    supported by evidence. Re-run focused validation and, when code changed as
    part of the fix, re-run or refresh the review until no blocking finding
    remains or a blocker is recorded.
13. Invoke `$commit` to commit the completed active task only after validation
    and review/fix gates pass. Let the `commit` skill own repo-root
    `git add -A`, staged validation, commit hooks, message generation, and
    no-push behavior. If unrelated or unsafe dirty changes would make the
    whole-repo commit unsafe, classify `WORKTREE_CONFLICT`, update the
    handoff, and stop before committing.
14. Update the handoff with status, changed files, validation, code-review
    result, fixes applied, commit hash/message or commit blocker, residual
    risks, and the exact next-session prompt.
15. End the current task session after the handoff is saved. Start the next
    task only in a fresh agent session that receives the handoff markdown as
    context material.
16. Repeat until every task is `done` or a task is `blocked`.
17. After all tasks are done, run changed-surface alignment with `$align` when
    available, or perform the equivalent local checklist across code, tests,
    docs, changelog, CLI/help, config, and generated artifacts.

## Fresh Session Contract

The skill cannot reset, close, or reopen its own current context. It must hand
off through a markdown file and one of these operator/session mechanisms:

- Strict interactive close/open: update the handoff, run `/archive` or
  `/quit` to close the current session, start a new `codex` session from the
  repo root, and paste or send the next-session prompt from the handoff.
- Interactive same-terminal reset: when the operator accepts a fresh
  conversation inside the same CLI process as the session boundary, update the
  handoff, run `/new`, and paste or send the next-session prompt from the
  handoff.
- Noninteractive automation: run a new `codex exec` process for each task.
  Let the current process exit after saving the handoff; the supervising
  script starts the next process and passes the handoff path in the prompt.
  Grant access to the handoff directory when sandboxing requires it, for
  example:

  ```bash
  codex --ask-for-approval never exec \
    --cd <repo-root> \
    --sandbox workspace-write \
    --add-dir "${CODEX_HOME:-$HOME/.codex}/task-implementer" \
    'Use $task-implementer to continue <handoff-path>. Implement only task-N, run validation, use code-review, fix scoped findings, commit through $commit, update the handoff, and stop.'
  ```

Do not write `codex exec /new`; `/new` is an interactive slash command, while a
new `codex exec` process is already a fresh noninteractive session. Do not use
`codex exec resume` for normal next-task handoff because the handoff file is
the intended context boundary.

## Ordering Rules

- Preserve explicit user priority unless code dependencies make it impossible.
- Put prerequisite schema, API, data-model, config, or contract changes before
  consumers.
- Put tests with the task they verify unless the task is explicitly a test-only
  preparation step.
- Put high-blast-radius shared code before leaf UI/docs cleanup only when the
  shared change is a prerequisite.
- Prefer smaller tasks that can be validated and checkpointed independently.
- Do not create artificial tasks for ceremony. If the request is truly one
  coherent change, record one task and implement it.

## Idempotency

- On every session, read the handoff first and verify it against current
  `git status` and relevant source files before editing.
- Before invoking `$commit`, verify that the dirty diff belongs to the active
  task or to intentionally preserved prior task changes recorded in the
  handoff. If not, stop with `WORKTREE_CONFLICT`.
- Never renumber existing task IDs after implementation starts. Add new tasks
  as `task-n+1` and mark superseded tasks explicitly.
- If a task is already done, verify its evidence instead of redoing it.
- If a task already has a commit hash recorded, verify the commit still exists
  and the task evidence still matches before moving on.
- If source drift invalidates a task, update that task to `blocked` or
  `superseded`, add a new task with the revised plan, and explain why.
- Keep one active task at a time. Do not launch the next fresh session until
  the current one has finished, reviewed, committed, and updated the handoff.

## Failure Handling

Classify failures before retrying:

- `DESIGN_GAP`: architecture, contracts, missing code, or boundary decisions
  are unresolved. Route to `design`.
- `CONTEXT_GAP`: required repo, ticket, internal, or vendor context is missing.
- `IMPLEMENTATION_DEFECT`: code does not satisfy the task.
- `TEST_DEFECT`: tests are wrong, incomplete, or not aligned with the task.
- `VALIDATION_DEFECT`: lint, type, build, import, generated artifact, or config
  validation fails.
- `REVIEW_BLOCKER`: `code-review` found a blocking issue that cannot be fixed
  safely inside the active task boundary.
- `COMMIT_BLOCKER`: `$commit` stopped because staged validation failed,
  commit hooks failed, the branch state is unsafe, or the commit message/scope
  would be misleading.
- `WORKTREE_CONFLICT`: unrelated user changes or concurrent edits block safe
  progress.
- `ENVIRONMENT_BLOCKER`: local tools, credentials, services, network, or
  permissions are unavailable.
- `HUMAN_INPUT_REQUIRED`: the next decision would be risky to guess.

Retry only when the next change is clear and scoped. Stop, update the handoff,
and report the blocker when the failure needs user input, unsafe permissions,
or repeated attempts without new evidence.

## Must Not

- Do not run multiple write-capable agents or implementation sessions at the
  same time in the same workspace.
- Do not treat the user prompt alone as the task order when code evidence says
  the dependencies differ.
- Do not create Agentic SDLC `docs/requirements.md`, `docs/design.md`, or
  `~/.codex/sdlc-runs` state.
- Do not use compatibility shims, deprecated aliases, or dual old/new paths
  unless the user explicitly asks for them.
- Do not bypass `code-review` for completed code tasks unless the handoff
  records that no code changed and review was not applicable.
- Do not commit by hand. Use `$commit` for the per-task local checkpoint, and
  never push, open PRs, merge, publish, or run live external writes unless a
  separate explicitly invoked skill owns that action.
- Do not continue into the next task in the same session after a task
  checkpoint is saved; stop so the next task starts from the handoff in a fresh
  session.
- Do not persist secrets, raw logs, private URLs, or customer data in the
  handoff or reusable skill sources.

## Completion Criteria

- The handoff contains an ordered `task-1` through `task-n` queue.
- Exactly one task is active at a time, and each completed task records files
  changed, validation, `code-review` outcome, fixes, commit hash/message, and a
  short checkpoint summary.
- Each task session stops after saving the reviewed and committed handoff
  checkpoint; the next task starts in a fresh session using the handoff as
  context.
- Every task is `done`, `blocked`, or `superseded`.
- The final session inspected the aggregate diff and ran changed-surface
  alignment or recorded why it could not.
- The final answer reports completed tasks, validation, remaining blockers, and
  the handoff path.

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

Return:

- Handoff path.
- Ordered task queue with statuses.
- Current or completed task summary.
- Files changed and validation performed.
- Code-review result, fixes applied, and per-task commit hash/message when a
  task completed.
- Failure classification and next action when blocked.
- Exact next-session prompt when work remains.

## References

- Read `references/implementation-loop.md` before creating a queue or launching
  fresh sessions.
- Use `assets/handoff-template.md` when creating a new handoff.
- Use `evals/trigger-prompts.md` when tuning or reviewing invocation behavior.
