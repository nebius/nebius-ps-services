# Brainstorm

`brainstorm` is an implicit, chat-only skill for exploring ideas before
implementation. It gathers source-ranked context, challenges assumptions, and
keeps facts, hypotheses, tradeoffs, and open questions separated.

The skill is intentionally read-only during the brainstorming conversation. If
the user decides to implement, document, ticket, message, commit, or publish
something, the agent should first summarize the brainstorm and then switch to
the appropriate workflow skill.

## Files

- `SKILL.md`: runtime contract, source priority, guardrails, and output shape.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.
- `references/source-priority.md`: detailed source-gathering rubric.
- `evals/trigger-prompts.md`: should-trigger and should-not-trigger examples.

## Boundaries

- `brainstorm` does not write context packs or SDLC state.
- `sdlc-gather-context` owns Agentic SDLC context-pack creation.
- `global-context-management` owns complex implementation context hygiene after
  the user switches from discussion to execution.
