# Trigger Prompts

Use these examples when reviewing or tuning `design` trigger behavior. Static
examples do not prove runtime activation until tried in the target Codex
surface.

## Should Trigger

```text
Design a new quota-aware preflight for this CLI before we implement it.
```

```text
I need a software design for adding SSO to this existing app. Read the code,
research the auth libraries, compare options, and give me a /plan handoff.
```

```text
There is no code yet. Research the stack choices and design a small API service
with persistence, tests, observability, and rollout guidance.
```

```text
Use $design to understand this repository and propose the implementation design
for a new background job workflow.
```

```text
Before coding, inspect README.md, design.md, and the relevant modules, then
design the components and implementation plan for this feature.
```

## Should Not Trigger

```text
Brainstorm whether this product idea is worth building.
```

Use `brainstorm` for open-ended ideation without a concrete design commitment.

```text
Review this ADR against our architecture checklist.
```

Use `system-design-rules` for checklist-style design review.

```text
Run the Agentic SDLC workflow and update docs/design.md for FEAT-123.
```

Use the explicit Agentic SDLC workflow, normally starting with `$sdlc-start`.

```text
Implement the selected design now.
```

Use `/plan` and the relevant implementation or project skill.

```text
Create a PR for the design changes.
```

Use the explicit GitHub PR workflow after files are intentionally changed.

## Manual Runtime Check

When trigger precision matters, test these prompts in a fresh Codex thread
where the source skill is installed or discoverable:

- Should-trigger prompts should load `design` or produce a response that
  follows its six-phase workflow and ends with a `/plan` handoff.
- Should-not-trigger prompts should route to brainstorming, checklist review,
  Agentic SDLC, implementation, or PR workflows.
- If `design` steals open-ended ideation, SDLC-owned design artifacts,
  implementation, or checklist-only reviews, narrow the front matter
  `description` before changing the workflow body.

Report runtime activation as observed only after this check. Otherwise report
trigger readiness from metadata and static validation only.
