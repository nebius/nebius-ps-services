# System Design Rules

`system-design-rules` is an implicit design-phase skill for evaluating system
designs, architecture proposals, ADRs, platform choices, API contracts, data
models, reliability plans, security posture, observability, cost, and ownership
before implementation.

It applies a refined 100-rule checklist selectively, based on risk and context,
so design review stays practical rather than ceremonial.

## Files

- `SKILL.md`: runtime contract, workflow, guardrails, and output shape.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.
- `references/design-principles.md`: refined 100-rule system design checklist
  and decision worksheet.
- `evals/trigger-prompts.md`: should-trigger and should-not-trigger examples.

## Boundaries

- Use this skill for design decisions and architecture review before
  implementation.
- Use `brainstorm` for early open-ended ideation when the user wants discussion
  before a design review.
- Use `align` for project-wide changed-scope implementation alignment.
- Use `sdlc-create-design` only inside the explicit Agentic SDLC workflow.
