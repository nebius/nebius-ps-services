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

## Implementation Coordination

When the user asks only for advice, the skill remains read-only. When the user
asks to build or modernize an application, `app-stack` owns the cross-layer
sequence and routes concrete work to the narrow installed skills that match the
selection. This keeps Python, website, Terraform, Helm, workflow, security, and
alignment guidance with their specialist owners.

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
- `evals/trigger-prompts.md`: trigger and output-quality examples.

## Boundaries

- Use `brainstorm` for open-ended ideation.
- Use `research` for deep due diligence on a technology or architecture
  pattern.
- Use `design` for a complete solution design before implementation.
- Use a stack-specific skill directly when the stack is already fixed and no
  selection decision remains.
- Use `align` after implementation to reconcile the changed project surfaces.
