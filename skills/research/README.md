# Research

`research` is an implicit, read-only skill for senior-engineer technical due
diligence. It investigates technologies, protocols, RFCs, APIs, frameworks,
products, architecture patterns, and feature requirements by searching internal
Slack and Confluence context first when organization context matters, using MCP
or app-tool access when connectors are unavailable, verifying technical claims
against vendor and authoritative external sources, comparing alternatives, and
producing actionable recommendations for design decisions.

The skill is intentionally not a generic Q&A helper. A useful research pass
continues from "what is this?" to "why does it exist?", "how does it work?",
"when should we use it?", "when should we avoid it?", and "what tradeoffs
matter?".

## Files

- `SKILL.md`: runtime contract, source priority, workflow, guardrails, and
  output contract.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.
- `references/research-methodology.md`: detailed source-ranking and research
  workflow guidance.
- `evals/trigger-prompts.md`: should-trigger and should-not-trigger examples.

## Boundaries

- `research` does not implement code, create plans, write docs, open tickets,
  commit, publish, or mutate external systems.
- Internal Slack, Confluence, MCP, and app-tool access is read-only source
  retrieval for research. Missing internal access must be reported as a source
  gap, not filled with assumptions.
- Internal sources own organization-specific guidance; vendor docs and primary
  sources own upstream technical behavior unless there is a documented local
  fork, wrapper, configuration override, or tested environment-specific result.
- `brainstorm` owns open-ended chat-first ideation before deep research.
- `design` owns solution design and `/plan` handoff after research findings are
  available.
- `system-design-rules` owns checklist-style review of an existing proposal.
- `$sdlc-start run <prompt>` and the relevant `sdlc-*` skills own Agentic SDLC context packs
  and committed SDLC artifacts.
