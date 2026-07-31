# Create Plan

`sdlc-create-plan` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Create a local, locked execution plan for exactly one feature. When the feature
spans serial application layers, the plan preserves one end-to-end slice and
expresses work as stable `TASK-*` records with dependencies, exact/prefix write
claims, conflict domains, focused validation, done criteria, and stop conditions.

After completed waves, a proven implementation defect creates immutable
corrective plan vN+1. The correction preserves every existing task definition
and completed digest, appends diagnosis-bound corrective tasks and waves, and
keeps the original evaluator oracle as the first regression target.

## Main Boundaries

- Do not commit the plan.
- Do not edit locked plans.
- Do not implement code or write tests.
- Do not expand feature scope.
- Do not split a multi-layer feature into broad layer-only work when one
  vertical slice can be planned safely.
- Do not reinterpret completed work, reuse completed tasks under a new digest,
  or reopen sealed or promoted execution.

## Primary Inputs

- One `FEAT-*`.
- Corresponding `REQ-*` blocks.
- Context pack.
- Current repo and feature state.

## Output

- Local plan exists and is locked.
- Plan contains test, implementation, validation, and evaluation steps.
- Plan identifies the end-to-end slice or records why no vertical slice applies.
- Task dependencies are acyclic; parallel candidates have disjoint ownership.
- Corrective versions pass `scripts/corrective_plan.py`; append-only history,
  diagnosis, regression oracle, and completed task manifest bindings are exact.
- Plan references exact feature and requirement IDs.
