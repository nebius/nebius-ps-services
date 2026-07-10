# Task Implementer

`task-implementer` is a narrow brownfield implementation coordinator for
explicit sequential task loops.
It turns a complex user request that is bigger than one coherent task into
`task-1` through `task-n`, orders the tasks by dependencies and priority, and
implements one task at a time with a markdown handoff between fresh Codex
sessions. For serial multi-layer application work, it prefers vertical tasks
that deliver one behavior through the connected layers over broad layer-by-layer
tasks, unless a shared foundation must be built first.

It is intentionally smaller than Agentic SDLC. It does not create committed
requirements/design docs, private SDLC JSON state, hooks, PRs, or merge
actions. Each task gathers the context it needs, uses `brainstorm` when
source-ranked context or assumption checks are useful, routes non-trivial
design or contract choices through `design`, records a short plan, validates,
is reviewed with `code-review`, is fixed as needed, and is locally committed
through `$commit` before the next fresh session starts.

## Files

- `SKILL.md`: runtime workflow, state boundary, task ordering, fresh-session
  contract, failure handling, and output shape.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.
- `references/implementation-loop.md`: queue construction, design routing,
  per-task context/design/plan gates, handoff discipline, fresh-session
  patterns, and final alignment guidance.
- `assets/handoff-template.md`: markdown checkpoint template copied into
  `$CODEX_HOME/task-implementer/<project-id>/<run-id>/handoff.md`.
- `evals/trigger-prompts.md`: should-trigger and should-not-trigger examples.
- `scripts/test-task-implementer-contract.py`: local static smoke test for the
  workflow, handoff, metadata, and trigger-example contract.

## Boundaries

- Use this skill when the user asks for a sequential task implementation loop
  or continuation from an existing handoff. It is explicit-only because it
  mutates code, invokes commit checkpoints, and may launch follow-on sessions.
- Do not make `global-context-management` always call this skill. Use
  `global-context-management` for context hygiene around complex work; use
  `$task-implementer` only when the explicit sequential implementation,
  per-task commit, and fresh-session handoff loop is the requested workflow.
- Use `brainstorm` as a read-only context source before a task's design pass
  when useful; do not let it own implementation.
- Use `design` as a supporting skill for unresolved architecture, component,
  contract, or technology choices before implementation.
- Prefer vertical end-to-end tasks for multi-layer application features; use
  foundation-only tasks only for true blockers such as schema, auth,
  migrations, test harnesses, or safety preflights.
- Use `code-review` and `$commit` as required per-task gates after validation
  and before session handoff.
- Use `$sdlc-start` for Agentic SDLC.
- Use `align` for changed-surface consistency after implementation.
- Do not run parallel write-capable agents in the same workspace.
