# Research

`research` is an implicit, read-only skill for senior-engineer technical due
diligence. It investigates technologies, protocols, RFCs, APIs, frameworks,
products, architecture patterns, and feature requirements by ranking sources,
studying internals and operations, comparing alternatives, and producing
actionable recommendations for design decisions.

The skill is intentionally not a generic Q&A helper. A useful research pass
continues from "what is this?" to "why does it exist?", "how does it work?",
"when should we use it?", "when should we avoid it?", and "what tradeoffs
matter?".

## Files

- `SKILL.md`: runtime contract, source tiers, workflow, guardrails, and output
  contract.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.
- `references/research-methodology.md`: detailed source-ranking and research
  workflow guidance.
- `evals/trigger-prompts.md`: should-trigger and should-not-trigger examples.

## Boundaries

- `research` does not implement code, create plans, write docs, open tickets,
  commit, publish, or mutate external systems.
- `brainstorm` owns open-ended chat-first ideation before deep research.
- `design` owns solution design and `/plan` handoff after research findings are
  available.
- `system-design-rules` owns checklist-style review of an existing proposal.
- `$sdlc-start` and the relevant `sdlc-*` skills own Agentic SDLC context packs
  and committed SDLC artifacts.
