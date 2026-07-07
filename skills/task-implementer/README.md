# Task Implementer

`task-implementer` is a narrow brownfield implementation coordinator for
sequential task loops.
It turns a user request into `task-1` through `task-n`, orders the tasks by
dependencies and priority, and implements one task at a time with a markdown
handoff between fresh Codex sessions.

It is intentionally smaller than Agentic SDLC. It does not create committed
requirements/design docs, private SDLC JSON state, hooks, PRs, or merge
actions. Each completed task is reviewed with `code-review`, fixed as needed,
and locally committed through `$commit` before the next fresh session starts.

## Files

- `SKILL.md`: runtime workflow, state boundary, task ordering, fresh-session
  contract, failure handling, and output shape.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.
- `references/implementation-loop.md`: queue construction, design routing,
  handoff discipline, fresh-session patterns, and final alignment guidance.
- `assets/handoff-template.md`: markdown checkpoint template copied into
  `$CODEX_HOME/task-implementer/<project-id>/<run-id>/handoff.md`.
- `evals/trigger-prompts.md`: should-trigger and should-not-trigger examples.

## Boundaries

- Use this skill when the user asks for a sequential task implementation loop
  or continuation from an existing handoff. It is explicit-only because it
  mutates code, invokes commit checkpoints, and may launch follow-on sessions.
- Use `design` as a supporting skill for unresolved architecture, component,
  contract, or technology choices before implementation.
- Use `code-review` and `$commit` as required per-task gates after validation
  and before session handoff.
- Use `$sdlc-start` for Agentic SDLC.
- Use `align` for changed-surface consistency after implementation.
- Do not run parallel write-capable agents in the same workspace.
