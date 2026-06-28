# Design

`design` is an implicit, non-SDLC skill for software design before
implementation. It reads requirements, inspects existing code or greenfield
context, researches unfamiliar technologies against current official docs,
chooses components and boundaries, compares alternatives, and produces a
Codex `/plan` handoff.

## Files

- `SKILL.md`: runtime workflow, six-phase process, boundaries, guardrails, and
  output contract.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.
- `references/design-workflow.md`: detailed phase checklist, research rubric,
  depth guidance, and `/plan` handoff template.
- `evals/trigger-prompts.md`: should-trigger and should-not-trigger examples.

## Boundaries

- Use `design` when the user wants a concrete software design and
  implementation-ready plan before coding.
- Use `brainstorm` for open-ended ideation and source-ranked discussion without
  design commitment.
- Use `system-design-rules` to review an existing proposal, ADR, or design
  against a checklist.
- Use `sdlc-create-design` and `sdlc-create-plan` inside the Agentic SDLC
  workflow for SDLC-owned `docs/design.md`, `FEAT-*` IDs, and locked plans.
- Use `/plan` or the relevant implementation skill once the design is complete
  and code should change.
