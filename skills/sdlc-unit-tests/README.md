# Unit Tests

`sdlc-unit-tests` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Answer whether the code behaves correctly against the feature tests,
acceptance criteria, and any planned end-to-end slice.

## Main Boundaries

- Delete or weaken tests to pass.
- Treat flaky tests as pass without evidence.
- Overwrite evaluation results.

## Primary Inputs

- Current feature ID.
- Test files created or selected by `sdlc-tdd`.
- Locked plan.
- Project test tooling.

## Output

- Required tests pass.
- Planned slice coverage passes or the blocker is classified.
- Evidence file exists.
- Failures are classified if not passing.
- State moves to `tested`.
