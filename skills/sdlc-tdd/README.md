# SDLC TDD

`sdlc-tdd` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Define success before implementation by creating tests that prove the current
feature behavior and any planned end-to-end slice in the registered integration
worktree. The project checkout remains unchanged, and implementation seals the
TDD base before creating workers.

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
- Planned slice contracts and cross-layer validation targets are covered or a
  blocker is classified.
- Test run evidence exists.
- Expected red or already-green state is recorded.
