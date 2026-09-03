# Align Specs

`sdlc-align-specs` is an Agentic SDLC skill. It is authored in this repository and is
installed into a Codex runtime only when `install-skills.sh` is run.

## What It Does

Provide an optional advisory view of whether SDLC specs, plans,
implementation, documentation, tests, end-to-end slice evidence, and other
evidence tell one consistent story. The required workflow uses general
`align`; this skill never gates phases, commit, or completion.

## Main Boundaries

- Do not free-edit requirements or design; route changes through the applicable
  Agentic SDLC adapter and the `maintain-project-specs` paired publisher.
- Do not modify locked plans.
- Do not pretend unchecked evidence passed.
- Do not replace the general `align` workflow.

## Primary Inputs

- Requirements, design, current feature plan, changed files, tests,
  documentation, steering, and evidence.
- Current feature ID or full-run scope.

## Output

- Alignment report exists.
- Each drift item has an owner skill or blocker.
- Vertical flow, layer map, locked slice, and evidence agree when applicable.
- Unresolved inconsistencies are reported with their owner and remain advisory.
