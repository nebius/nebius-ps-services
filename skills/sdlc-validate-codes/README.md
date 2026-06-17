# Validate Codes

`sdlc-validate-codes` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Answer whether the implemented feature can be built or run correctly before deeper behavior tests.

## Main Boundaries

- Change requirements or design.
- Skip configured validation.
- Treat missing tooling as success.
- Overwrite test or evaluation evidence.

## Primary Inputs

- Current feature ID.
- Locked plan.
- Changed files.
- Project tooling.

## Output

- Validation evidence exists.
- Required checks pass or blocker is classified.
- State moves to `validated` only on pass.
