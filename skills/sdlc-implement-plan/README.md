# Implement Plan

`sdlc-implement-plan` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Implement production code for one feature while staying inside the locked plan.
When the plan defines a vertical end-to-end slice, implement that slice through
the planned layers without widening scope.

## Main Boundaries

- Do not change unrelated features.
- Do not modify locked plans.
- Do not hide failures.
- Do not remove tests to pass.
- Do not broaden implementation beyond the locked vertical slice; route plan or
  design defects backward instead.

## Primary Inputs

- Locked plan.
- Red or ready tests.
- Feature design.
- Context pack.
- Existing codebase.

## Output

- Planned code changes are implemented.
- Planned vertical slice is implemented or a plan/design defect is recorded.
- Focused tests are runnable or blocker is recorded.
- Changed files match implementation boundaries.
- State moves to `implemented`.
