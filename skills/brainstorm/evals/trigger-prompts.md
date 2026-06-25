# Trigger Prompts

Use these examples when reviewing or tuning `brainstorm` trigger behavior.
These prompts are static examples; they do not prove runtime activation until
they are tried in the target Codex surface.

## Should Trigger

```text
Help me brainstorm whether we should add a quota-aware preflight to this service.
```

```text
I want to think through the design options for Nebius MK8s GPU readiness before we change code.
```

```text
Can we discuss this product idea and gather the local docs, related skills, Confluence, Slack, and vendor docs that matter?
```

```text
Challenge my assumption that we need a new workflow here. Check the repo context first.
```

```text
Use $brainstorm to explore whether this should become an SDLC feature or just a small docs update.
```

## Should Not Trigger

```text
Implement the quota-aware preflight.
```

Use the implementation or domain-specific skill instead.

```text
Run $sdlc-start for this feature.
```

Use the explicit Agentic SDLC workflow.

```text
Review this PR and fix safe issues.
```

Use `review-pr` or `align`, depending on the request.

```text
Create the Confluence page and send the Slack update.
```

Use the appropriate outgoing or publishing workflow after explicit user intent.

```text
Commit these changes.
```

Use the explicit Git commit skill.

## Manual Runtime Check

When trigger precision matters, test these prompts in a fresh Codex thread
where the source skill is installed or discoverable:

- Should-trigger prompts should load `brainstorm` or produce a response that
  follows its chat-only, source-ranked context workflow.
- Should-not-trigger prompts should route to the implementation, Agentic SDLC,
  PR review, communication, or commit skill named by the request.
- If `brainstorm` steals implementation, SDLC, or review tasks, narrow the
  front matter `description` before changing the workflow body.

Report runtime activation as observed only after this check. Otherwise report
trigger readiness from metadata and static validation only.
