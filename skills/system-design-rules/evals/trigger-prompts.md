# Trigger Prompts

Use these examples when reviewing or tuning `system-design-rules` trigger
behavior. Static examples do not prove runtime activation until tried in the
target Codex surface.

## Should Trigger

```text
Review this architecture proposal against system design principles before we implement it.
```

```text
Use $system-design-rules to compare these service boundary options and identify the trade-offs.
```

```text
Help me decide whether this workflow should be synchronous, asynchronous, or event-driven.
```

```text
Check this ADR for missing reliability, data ownership, observability, security, and cost considerations.
```

```text
Before coding, apply a system design checklist to this API and data model proposal.
```

## Should Not Trigger

```text
Implement the selected API design.
```

Use an implementation or project-specific skill.

```text
Run the full Agentic SDLC workflow for this feature.
```

Use the explicit SDLC workflow through `$sdlc-start run <managed-prompt>`.

```text
Review this pull request and fix safe issues.
```

Use `review-pr` or `align`, depending on the request.

```text
Create Terraform for this architecture.
```

Use `terraform` after the design decision is clear.

```text
Brainstorm a few wild product ideas without checking a formal checklist yet.
```

Use `brainstorm` for open-ended ideation.

## Manual Runtime Check

When trigger precision matters, test these prompts in a fresh Codex thread
where the source skill is installed or discoverable:

- Should-trigger prompts should load `system-design-rules` or produce a
  response that follows its design-decision checklist workflow.
- Should-not-trigger prompts should route to implementation, SDLC, PR review,
  Terraform, or brainstorming workflows.
- If the skill steals direct implementation or SDLC tasks, narrow the front
  matter `description` before changing the workflow body.

Report runtime activation as observed only after this check. Otherwise report
trigger readiness from metadata and static validation only.
