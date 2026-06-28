# Trigger Prompts

Use these examples when reviewing or tuning `research` trigger behavior. These
prompts are static examples; they do not prove runtime activation until they
are tried in the target Codex surface.

## Should Trigger

```text
Research Kubernetes Gateway API and explain its internals, limitations, alternatives, and whether we should use it.
```

```text
Research Cilium BGP Control Plane for a production cluster design.
```

```text
I need a technical due diligence report on how vLLM performs tensor parallelism.
```

```text
Research OpenTelemetry Collector scaling patterns and compare the alternatives.
```

```text
Investigate how Slurm preemption works, what breaks operationally, and what design tradeoffs matter.
```

```text
Use $research to study this RFC and turn it into actionable design guidance.
```

## Should Not Trigger

```text
Help me brainstorm whether we should build this product idea.
```

Use `brainstorm` for open-ended ideation before deep research.

```text
Design this new feature and produce a /plan handoff.
```

Use `design` after any needed research findings are available.

```text
Review this ADR against our system design checklist.
```

Use `system-design-rules` for checklist-style design review.

```text
Implement the Gateway API integration.
```

Use the relevant implementation workflow or project skill.

```text
Create the Agentic SDLC context pack for this feature.
```

Use the explicit Agentic SDLC workflow.

## Manual Runtime Check

When trigger precision matters, test these prompts in a fresh Codex thread
where the source skill is installed or discoverable:

- Should-trigger prompts should load `research` or produce a due diligence
  response with source ranking, internals, operations, limitations,
  alternatives, and recommendations.
- Should-not-trigger prompts should route to brainstorming, design,
  implementation, design-review, or SDLC skills as appropriate.
- If `research` steals open-ended ideation, design planning, implementation, or
  SDLC tasks, narrow the front matter `description` before changing the
  workflow body.

Report runtime activation as observed only after this check. Otherwise report
trigger readiness from metadata and static validation only.
