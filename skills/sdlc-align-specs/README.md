# Align Specs

`sdlc-align-specs` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Verify that SDLC specs, plans, implementation, tests, and evidence tell one consistent story.

## Main Boundaries

- Free-edit requirements or design.
- Modify locked plans.
- Pretend unchecked evidence passed.
- Replace the general `align` workflow.

## Primary Inputs

- Requirements, design, current feature plan, changed files, tests, and evidence.
- Current feature ID or full-run scope.

## Output

- Alignment report exists.
- Each drift item has an owner skill or blocker.
- No unresolved inconsistency remains before commit or PR readiness is claimed.
