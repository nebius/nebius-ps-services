# AI Stack

`ai-stack` selects or reviews the AI-specific layers of a system: model access,
training, inference, agents, durable execution, interoperability, retrieval,
evaluation, safety, and operations. It starts from workload contracts and the
current or no-new-component baseline, then adds only components that pass hard
gates and have an operational owner.

Every component is `Required`, `Conditional`, `Deferred`, or `Rejected`. Every
material claim is `Measured`, `Officially documented`, or `Assumed`. This keeps
vendor feature lists, target benchmarks, and unresolved assumptions visibly
separate.

## Core Model

The selection order is:

1. Freeze materially different workloads and acceptance thresholds.
2. Classify each AI capability as a direct model call, deterministic workflow
   containing model calls, or agent.
3. Choose the simplest sufficient architecture before naming products.
4. Reject candidates that fail legal, data, safety, compatibility, quality,
   recovery, ownership, performance, or cost gates.
5. Verify volatile claims with official sources and target-specific claims with
   controlled measurement.
6. Record compatibility edges, failure modes, owners, switch conditions, and
   immutable release identity.

Use **deterministic where possible, agentic where necessary**. Direct OpenAI
text or reasoning generation supported by Responses uses an official OpenAI
SDK plus the Responses API; embeddings, transcription, realtime speech, and
other direct workloads use their official task-specific APIs. Known sequences
and code-owned continuation conditions remain ordinary application workflows
with explicit model calls. Intentionally OpenAI-native agents use the OpenAI
Agents SDK; Python agents using Anthropic, Gemini, another non-OpenAI provider,
or a provider-neutral typed boundary use Pydantic AI by default.

Engineering work is a separate specialist lane: use the Codex SDK for
coding-focused threads and add Codex app-server only for a deep embedded client
that needs authentication, conversation history, approvals, or streamed agent
events. LangGraph remains conditional on explicit graph semantics and Temporal
owns durable cross-service execution, but either may wrap deterministic or
agentic work. A full internal model-provider abstraction is justified only
when semantic portability is real.

## Files

- `SKILL.md`: runtime scope, workflow, guardrails, validation, and output
  contract.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.
- `evals/trigger-prompts.md`: trigger and output-quality scenarios.
- `references/workload-contracts.md`: workload-specific decision inputs.
- `references/selection-framework.md`: hard gates, evidence rules, and decision
  record.
- `references/current-technology-baseline.md`: dated opinionated baseline and
  switch conditions.
- `references/agent-application-stack.md`: agent frameworks, state, durability,
  tools, and UI.
- `references/model-provider-contract.md`: semantic provider portability.
- `references/mcp-core-conformance.md`: MCP core and conformance profile.
- `references/mcp-apps-tasks-gateway.md`: MCP extensions and gateway boundaries.
- Other references cover training, inference, retrieval, compatibility,
  evaluation, architecture, standards, source provenance, and handoffs.

## Boundaries

- Use `app-stack` for the surrounding product and ordinary application stack.
- Use `design` for complete cross-layer design and `/plan` handoff.
- Use `research` for deep due diligence on one technology or claim.
- Use `system-design-rules` for checklist review of an existing design.
- Use specialist implementation skills when the stack is already fixed.
- Use Codex for coding-focused engineering tasks; do not turn deterministic
  application logic, APIs, data, RBAC, or approvals into agent decisions.
- Keep scaffold handoffs logical-only. Repository topology, paths, runtime
  units, candidate manifests, file ownership, and apply authority belong to the
  scaffold or implementation workflow.

The source skill is not proof of active installation or runtime triggering.
Static validation establishes structural and metadata readiness only.
