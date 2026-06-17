# Create Plan

`sdlc-create-plan` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Create a local, locked execution plan for exactly one feature.

## Main Boundaries

- Commit the plan.
- Edit locked plans.
- Implement code or write tests.
- Expand feature scope.

## Primary Inputs

- One `FEAT-*`.
- Corresponding `REQ-*` blocks.
- Context pack.
- Current repo and feature state.

## Output

- Local plan exists and is locked.
- Plan contains test, implementation, validation, and evaluation steps.
- Plan references exact feature and requirement IDs.
