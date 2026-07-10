# Design

`design` is an implicit, non-SDLC skill for software design before
implementation. It reads requirements, inspects existing code or greenfield
context, routes topic, requirement, and technology due diligence through
`research` when available, applies `system-design-rules` to non-trivial
solution decisions, chooses components and boundaries, compares alternatives,
designs vertical end-to-end slices for serial multi-layer applications, and
produces a Codex `/plan` handoff.

## Files

- `SKILL.md`: runtime workflow, six-phase process, boundaries, guardrails, and
  output contract.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.
- `references/design-workflow.md`: detailed phase checklist, `research`
  handoff guidance, `system-design-rules` decision-review guidance, depth
  guidance, vertical-slice strategy, and `/plan` handoff template.
- `evals/trigger-prompts.md`: should-trigger and should-not-trigger examples.

## Boundaries

- Use `design` when the user wants a concrete software design and
  implementation-ready plan before coding.
- Use `research` for substantial topic, feature-requirement, product,
  standard, architecture-pattern, or technology due diligence needed by the
  design.
- Use `system-design-rules` inside `design` for standard, deep,
  architecture-heavy, ADR-like, or hard-to-reverse solution decisions before
  finalizing the `/plan`.
- Use `brainstorm` for open-ended ideation and source-ranked discussion without
  design commitment.
- Use `system-design-rules` to review an existing proposal, ADR, or design
  against a checklist.
- Use `sdlc-create-design` and `sdlc-create-plan` inside the Agentic SDLC
  workflow for SDLC-owned `docs/design.md`, `FEAT-*` IDs, and locked plans.
- Use `/plan` or the relevant implementation skill once the design is complete
  and code should change.
