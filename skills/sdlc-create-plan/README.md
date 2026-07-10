# Create Plan

`sdlc-create-plan` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Create a local, locked execution plan for exactly one feature. When the feature
spans serial application layers, the plan preserves one end-to-end slice and
groups work by behavior across layers instead of by isolated layer.

## Main Boundaries

- Do not commit the plan.
- Do not edit locked plans.
- Do not implement code or write tests.
- Do not expand feature scope.
- Do not split a multi-layer feature into broad layer-only work when one
  vertical slice can be planned safely.

## Primary Inputs

- One `FEAT-*`.
- Corresponding `REQ-*` blocks.
- Context pack.
- Current repo and feature state.

## Output

- Local plan exists and is locked.
- Plan contains test, implementation, validation, and evaluation steps.
- Plan identifies the end-to-end slice or records why no vertical slice applies.
- Plan references exact feature and requirement IDs.
