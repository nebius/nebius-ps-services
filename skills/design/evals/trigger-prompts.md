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
use research for the auth-library due diligence, apply system-design-rules to
the auth boundary decision, compare options, and give me a /plan handoff.
```

```text
There is no code yet. Use research for the stack and requirement due diligence,
use app-stack to select the frontend, API, backend, database, and deployment
technologies, then use system-design-rules to check the API, data, reliability,
security, observability, and rollout choices before the /plan handoff.
```

```text
Use $design to understand this repository and propose the implementation design
for a new background job workflow.
```

```text
Before coding, inspect README.md, design.md, and the relevant modules, then
design the components and implementation plan for this feature.
```

```text
Design this three-layer app feature before implementation. Show the vertical
slice from frontend through API to database, then create the /plan handoff.
```

```text
Design a new multi-tier application. Use app-stack for the technology choices
across the web frontend, API, service runtime, and database, then return to the
complete design and /plan handoff.
```

```text
Design a feature for this established application using its approved React,
FastAPI, and PostgreSQL stack. Do not reconsider the stack.
```

This should use `design` but skip `app-stack` because no stack decision remains.

```text
Design this modernization. Keep the approved frontend and API stack, but use
app-stack to decide whether the data layer should remain on the current database
or move to a different database technology.
```

```text
Troubleshooting already proved the causal chain and identified the violated
invariant. Design the required cross-service ownership change from that handoff
and produce the /plan without reopening the diagnosis.
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
Choose the smallest technology stack for this application and classify which
components are required, conditional, deferred, or rejected.
```

Use `app-stack` directly because the user requested a stack decision, not a
complete solution design and `/plan` handoff.

```text
Run the Agentic SDLC workflow and update docs/design.md for FEAT-123.
```

Use the explicit Agentic SDLC workflow through `$sdlc-start run <managed-prompt>`.

```text
Implement the selected design now.
```

Use `/plan` and the relevant implementation or project skill.

```text
This service fails intermittently and we do not know why. Find the root cause
and repair it.
```

Use `troubleshoot`; route to `design` only if the proven durable remediation
later changes a system contract such as a component boundary, public interface,
data owner, migration, or cross-component workflow.

```text
The cause is proven and the fix is a difficult concurrency rewrite, but it
stays inside one existing private component boundary and preserves every
external contract. Design a new architecture before fixing it.
```

Keep this repair in `troubleshoot`; implementation difficulty without a
system-contract change must not trigger `design`.

```text
Create a PR for the design changes.
```

Use the explicit GitHub PR workflow after files are intentionally changed.

## Manual Runtime Check

When trigger precision matters, test these prompts in a fresh Codex thread
where the source skill is installed or discoverable:

- Should-trigger prompts should load `design` or produce a response that
  follows its design workflow, routes substantial due diligence through
  `research`, routes undecided application-stack or layer technology choices
  through `app-stack`, applies `system-design-rules` for non-trivial solution
  decisions, and ends with a `/plan` handoff.
- Fixed-stack design prompts should remain in `design` without reopening the
  technology decision through `app-stack`.
- Should-not-trigger prompts should route to brainstorming, checklist review,
  stack selection, Agentic SDLC, implementation, or PR workflows.
- If `design` steals open-ended ideation, SDLC-owned design artifacts,
  stack-selection-only prompts, implementation, or checklist-only reviews,
  narrow the front matter `description` before changing the workflow body.

Report runtime activation as observed only after this check. Otherwise report
trigger readiness from metadata and static validation only.
