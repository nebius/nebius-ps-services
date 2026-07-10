# SDLC Evaluate

`sdlc-evaluate` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Evaluate observed product behavior against real-world acceptance criteria and
any planned end-to-end slice, using the requirements Live Experiment
Environment only when it is confirmed safe and allowed.

## Main Boundaries

- Ignore negative criteria.
- Mark pass without observable evidence.
- Overwrite validation or test evidence.
- Use production or unconfirmed environments for live experiments.

## Primary Inputs

- Feature design.
- Acceptance criteria.
- Validation evidence.
- Test evidence.
- Product type.
- Live Experiment Environment section from `docs/requirements.md`, when present.

## Output

- Evaluation evidence exists.
- Acceptance criteria are explicitly pass or fail.
- Planned end-to-end slice observation is recorded or a blocker is classified.
- State moves to `evaluated` only when pass.
