# Implement Plan

`sdlc-implement-plan` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Implement production code for one feature while staying inside the locked plan.

## Main Boundaries

- Change unrelated features.
- Modify locked plans.
- Hide failures.
- Remove tests to pass.

## Primary Inputs

- Locked plan.
- Red or ready tests.
- Feature design.
- Context pack.
- Existing codebase.

## Output

- Planned code changes are implemented.
- Focused tests are runnable or blocker is recorded.
- Changed files match implementation boundaries.
- State moves to `implemented`.
