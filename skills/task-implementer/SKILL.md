---
name: task-implementer
description: "Use for sequential brownfield implementation loops when a request asks to break work into ordered task-1..task-n items, inspect target code first, route architecture/contracts/missing-code/ambiguous boundaries through the design skill when available, then implement one task at a time with a markdown handoff checkpoint and a fresh Codex session per task. Do not use for ordinary one-shot implementation, Agentic SDLC, chat-only brainstorming, code review, commits, PRs, or parallel multi-agent edits."
---

# Task Implementer

## Purpose

Coordinate small to medium brownfield implementation work by turning the user
request into an ordered task queue, then implementing that queue sequentially
with a markdown handoff between fresh Codex sessions.

This is a lightweight implementation loop, not the Agentic SDLC state machine.

## When To Use

- The user invokes `$task-implementer`.
- The user asks for a sequential task implementation loop.
- A brownfield request contains multiple dependent code, test, docs, config, or
  validation tasks and would benefit from fresh context between tasks.
- The user wants task ordering, checkpoint handoff, and no concurrent write
  agents.

## When Not To Use

- Do not use for a normal single-turn implementation that fits cleanly in the
  current context; implement directly with the applicable project skills.
- Do not use for the Agentic SDLC workflow; use `$sdlc-start` and `sdlc-*`
  phase skills.
- Do not use for chat-only ideation; use `brainstorm`.
- Do not use for design review without implementation; use `design` or
  `system-design-rules` when available and appropriate.
- Do not use for review-only work; use `code-review`, `review-pr`, or `align`.
- Do not use for commits, pushes, PR creation, PR review, or merge.
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
- Validation evidence summarized in the handoff; avoid raw logs.

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
11. Update the handoff with status, changed files, validation, blockers, and
    the exact next-session prompt.
12. Start the next task in a fresh context. In the interactive CLI, use `/new`
    after the handoff is updated. In automation, start a new `codex exec`
    invocation; do not use `codex exec resume` for normal next-task handoff.
13. Repeat until every task is `done` or a task is `blocked`.
14. After all tasks are done, run changed-surface alignment with `$align` when
    available, or perform the equivalent local checklist across code, tests,
    docs, changelog, CLI/help, config, and generated artifacts.

## Fresh Session Contract

The skill cannot reset its own current context. It must hand off through a
markdown file and one of these operator/session mechanisms:

- Interactive CLI: update the handoff, then run `/new` and paste or send the
  next-session prompt from the handoff.
- Noninteractive automation: run a new `codex exec` process for each task.
  Pass the handoff path in the prompt and grant access to the handoff directory
  when sandboxing requires it, for example:

  ```bash
  codex --ask-for-approval never exec \
    --cd <repo-root> \
    --sandbox workspace-write \
    --add-dir "${CODEX_HOME:-$HOME/.codex}/task-implementer" \
    'Use $task-implementer to continue <handoff-path> and implement only task-N.'
  ```

Do not write `codex exec /new`; `/new` is an interactive slash command, while a
new `codex exec` process is already a fresh noninteractive session.

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
- Never renumber existing task IDs after implementation starts. Add new tasks
  as `task-n+1` and mark superseded tasks explicitly.
- If a task is already done, verify its evidence instead of redoing it.
- If source drift invalidates a task, update that task to `blocked` or
  `superseded`, add a new task with the revised plan, and explain why.
- Keep one active task at a time. Do not launch the next fresh session until
  the current one has finished and the handoff is updated.

## Failure Handling

Classify failures before retrying:

- `DESIGN_GAP`: architecture, contracts, missing code, or boundary decisions
  are unresolved. Route to `design`.
- `CONTEXT_GAP`: required repo, ticket, internal, or vendor context is missing.
- `IMPLEMENTATION_DEFECT`: code does not satisfy the task.
- `TEST_DEFECT`: tests are wrong, incomplete, or not aligned with the task.
- `VALIDATION_DEFECT`: lint, type, build, import, generated artifact, or config
  validation fails.
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
- Do not commit, push, open PRs, merge, publish, or run live external writes
  unless a separate explicitly invoked skill owns that action.
- Do not persist secrets, raw logs, private URLs, or customer data in the
  handoff or reusable skill sources.

## Completion Criteria

- The handoff contains an ordered `task-1` through `task-n` queue.
- Exactly one task is active at a time, and each completed task records files
  changed, validation, and a short checkpoint summary.
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
- Failure classification and next action when blocked.
- Exact next-session prompt when work remains.

## References

- Read `references/implementation-loop.md` before creating a queue or launching
  fresh sessions.
- Use `assets/handoff-template.md` when creating a new handoff.
- Use `evals/trigger-prompts.md` when tuning or reviewing invocation behavior.
