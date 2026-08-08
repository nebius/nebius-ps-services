# App Stack

`app-stack` selects the smallest justified technology stack for an application
and coordinates matching specialist skills when implementation is requested.
It is implicitly invokable because advisory selection is read-only and its
runtime contract keeps mutations behind the user's requested mode.

## Core Model

The skill follows four rules:

1. Classify the application and its quality attributes before selecting
   products.
2. Choose architecture boundaries before vendors or frameworks.
3. Mark every component as required, conditional, deferred, or rejected.
4. Add operational complexity only for a named requirement, owner, and revisit
   trigger.

The bundled Python, FastAPI, PostgreSQL, SQLAlchemy, and Alembic guidance is an
opinionated general-web profile, not a universal answer. Frontend, background
work, scheduling, workflows, streaming, deployment, and observability remain
conditional decisions.

For an AI-enabled product, `app-stack` owns the surrounding UI, API, backend,
ordinary data, deployment, and product layers. It delegates undecided model
access, training, inference, agent, MCP/A2A, retrieval, and AI evaluation
choices to `ai-stack`, then consumes that scoped decision without duplicating
its workload and compatibility analysis.

## Implementation Coordination

When the user asks only for advice, the skill remains read-only. When the user
asks to build or modernize an application, `app-stack` owns the cross-layer
sequence and routes concrete work to the narrow installed skills that match the
selection. This keeps Python, website, Terraform, Helm, workflow, security, and
alignment guidance with their specialist owners.

For an explicitly requested new or multi-component repository,
`app-stack` emits a closed logical component and technology handoff to the
explicit-only `scaffold-project` skill. It never assigns repository paths or
file owners. `scaffold-project` owns topology, candidate routing, approval, and
apply and does not route back into stack selection.

The current handoff is schema version 2. Every component declares one closed
component class and a canonical technology name so downstream composition can
prove exact technology and external-service bindings without moving physical
topology ownership upstream.

## Files

- `SKILL.md`: trigger, selection workflow, safety boundaries, and output
  contract.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.
- `references/selection-framework.md`: archetypes, decision axes, admission
  test, and decision record.
- `references/technology-profiles.md`: opinionated profiles, alternatives, and
  official vendor sources.
- `references/implementation-routing.md`: specialist ownership, sequencing,
  mutation boundaries, and completion evidence.
- `references/scaffold-handoff.schema.json`: closed logical handoff consumed by
  `scaffold-project`.
- `evals/trigger-prompts.md`: trigger and output-quality examples.

## Boundaries

- Use `brainstorm` for open-ended ideation.
- Use `research` for deep due diligence on a technology or architecture
  pattern.
- Use `ai-stack` for an undecided AI-specific stack or layer; use `app-stack`
  for the surrounding application.
- Use `design` for a complete solution design before implementation.
- When `design` requests a scoped stack decision, return the decision to that
  active workflow instead of starting a recursive design handoff.
- Use a stack-specific skill directly when the stack is already fixed and no
  selection decision remains.
- Use explicit `$scaffold-project` after the stack is approved when repository
  topology and several specialist-owned components must be composed safely.
- Use `align` after implementation to reconcile the changed project surfaces.
