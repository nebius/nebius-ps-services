# Design

`design` is an implicit, non-SDLC skill for software design before
implementation. It reads requirements, inspects existing code or greenfield
context, routes topic, requirement, and technology due diligence through
`research` when available, routes undecided application-stack and layer
technology choices through `app-stack`, routes undecided AI subsystem behavior,
topology, policy, and contracts through `ai-agent-design`, consumes its scoped
`ai-stack` component decision, and applies `system-design-rules` to
non-trivial solution decisions, chooses components and boundaries, compares
alternatives, designs vertical end-to-end slices for serial multi-layer
applications, and produces a Codex `/plan` handoff.

When the approved design needs a complete or multi-component repository
skeleton, the handoff may include the component/materialization/runtime graph
for a later explicit `$scaffold-project` invocation. `design` does not create
that scaffold and the scaffold workflow does not call back into design.

## Files

- `SKILL.md`: runtime workflow, seven-phase process, boundaries, guardrails, and
  output contract.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.
- `references/design-workflow.md`: detailed phase checklist, `research`,
  `app-stack`, `ai-agent-design`, and `ai-stack` handoff guidance,
  `system-design-rules` decision-review guidance, depth guidance,
  vertical-slice strategy, and `/plan` handoff template.
- `evals/trigger-prompts.csv`: canonical should-trigger and should-not-trigger examples.
- `evals/process-cases.md`: supplemental workflow and runtime-check cases.

## Boundaries

- Use `design` when the user wants a concrete software design and
  implementation-ready plan before coding.
- Accept an evidence-backed handoff from `troubleshoot` when the causal
  mechanism is already proven and the durable remediation changes architecture
  topology, component or service responsibilities or boundaries, a public
  interface, data ownership or lifecycle, a migration, or a cross-component
  workflow. Preserve the causal chain and return only if design work finds
  concrete contradictory evidence.
- Leave a complex or large repair inside one existing private boundary with
  `troubleshoot`; implementation difficulty alone does not create design work.
- Use `troubleshoot` instead when the failure mechanism is unknown or disputed.
- Use `research` for substantial topic, feature-requirement, product,
  standard, architecture-pattern, or technology due diligence needed by the
  design.
- Use `app-stack` when the application stack or technology for a frontend,
  client, API, backend, data, asynchronous-work, deployment, or observability
  layer is undecided or being reconsidered. Skip it when the stack is fixed.
- Use `app-stack` directly when the user wants only a stack decision rather
  than a complete design and `/plan` handoff.
- Use `ai-agent-design` once for undecided AI subsystem behavior, topology,
  policy, authority, contracts, context, memory, failure handling, evaluation,
  or governance. It freezes the four-class capability map and delegates only
  component selection to `ai-stack`.
- Use `ai-stack` directly for an undecided AI-specific technology layer when
  no agent-subsystem design or complete `/plan` handoff is needed.
- For AI applications, keep known logic and transitions deterministic, use a
  direct model call when one request is sufficient, and introduce an agent only
  when the model must choose actions or observation-driven next steps. One
  application may contain deterministic code, direct calls, deterministic AI
  workflows, and agents. Treat graph and durable execution as an independent
  orchestration choice that may wrap any class.
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
- Use explicit `$scaffold-project` after design and stack approval when the
  implementation needs repository topology composed from several specialist
  owners. This boundary remains outside Agentic SDLC.
