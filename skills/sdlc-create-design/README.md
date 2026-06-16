# Create Design

`sdlc-create-design` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Convert requirements and gathered context into implementable architecture and feature designs in `docs/design.md`.

## Main Boundaries

- Create local execution plans.
- Implement code.
- Modify tests.
- Delete feature blocks without explicit requirement removal.

## Primary Inputs

- `docs/requirements.md`.
- Feature context packs.
- Existing `docs/design.md` when present.
- Current codebase shape.

## Output

- `docs/design.md` exists.
- Every P0 requirement maps to at least one feature.
- Every ready feature has validation, test, evaluation, and implementation boundaries.
- Open design questions are explicit.
