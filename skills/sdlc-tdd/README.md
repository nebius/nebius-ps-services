# SDLC TDD

`sdlc-tdd` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Define success before implementation by creating tests that prove the current feature behavior.

## Main Boundaries

- Implement production code except minimal test-only fixtures.
- Delete or weaken tests to pass.
- Skip test execution unless environment is blocked.

## Primary Inputs

- Locked feature plan.
- Feature design.
- Requirement acceptance criteria.
- Existing test conventions.

## Output

- Tests exist and map to acceptance criteria.
- Test run evidence exists.
- Expected red or already-green state is recorded.
