# Create Design

`sdlc-create-design` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Convert requirements and gathered context into evidence-backed architecture
and feature designs in `docs/design.md`. The skill maps stable `REQ-*` blocks
to stable `FEAT-*` blocks, records selected and rejected design options, and
defines implementation, validation, test, evaluation, rollout, and rollback
boundaries before planning starts.

## Design Method

- Understand requirements, priorities, constraints, non-goals, and open
  questions before designing.
- Inspect the existing system, including README files, architecture docs,
  source files, tests, configs, interfaces, and nearby patterns.
- Use gathered context as the technology and vendor evidence source; route
  missing or unverifiable facts back to `sdlc-gather-context`.
- Compare the baseline/current approach, selected design, and a simpler or
  more conservative alternative when the decision is non-trivial or
  hard to reverse.

## Main Boundaries

- Do not create local execution plans.
- Do not implement code.
- Do not modify tests.
- Do not rewrite requirements.
- Do not use for non-SDLC design docs, ADRs, or `/plan` handoffs.
- Do not delete feature blocks without explicit requirement removal.

## Primary Inputs

- `docs/requirements.md`.
- Feature context packs.
- Existing `docs/design.md` when present.
- Current codebase shape.

## Output

- `docs/design.md` exists.
- Every P0 requirement maps to at least one feature.
- Every ready feature has selected and rejected options, implementation
  boundaries, validation, test, evaluation, rollout, rollback, and done
  criteria.
- Open design questions are explicit.
