# SDLC Evaluate

`sdlc-evaluate` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Evaluate observed product behavior against real-world acceptance criteria.

## Main Boundaries

- Ignore negative criteria.
- Mark pass without observable evidence.
- Overwrite validation or test evidence.

## Primary Inputs

- Feature design.
- Acceptance criteria.
- Validation evidence.
- Test evidence.
- Product type.

## Output

- Evaluation evidence exists.
- Acceptance criteria are explicitly pass or fail.
- State moves to `evaluated` only when pass.
