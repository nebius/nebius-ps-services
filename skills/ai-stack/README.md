# AI Stack

`ai-stack` selects or reviews the AI-specific layers of a system: model access,
training, inference, agents, durable execution, interoperability, retrieval,
evaluation, safety, and operations. It starts from workload contracts and the
current or no-new-component baseline, then adds only components that pass hard
gates and have an operational owner. For agent subsystems it consumes the
behavior, topology, authority, and policy contract frozen by
`ai-agent-design`; it does not reopen those decisions.

Every component is `Required`, `Conditional`, `Deferred`, or `Rejected`. Every
material claim is `Measured`, `Officially documented`, or `Assumed`. This keeps
vendor feature lists, target benchmarks, and unresolved assumptions visibly
separate.

## Core Model

The selection order is:

1. Freeze materially different workloads and acceptance thresholds.
2. Consume each capability's frozen classification as deterministic code, a
   direct model call, a deterministic workflow containing model calls, or an
   agent. For a direct stack-only request, mark the minimum local
   classification provisional.
3. Choose the simplest sufficient architecture before naming products.
4. Reject candidates that fail legal, data, safety, compatibility, quality,
   recovery, ownership, performance, or cost gates.
5. Verify volatile claims with official sources and target-specific claims with
   controlled measurement.
6. Record compatibility edges, failure modes, owners, switch conditions, and
   immutable release identity.

Use **deterministic where possible, agentic where necessary**. Portable Python
direct-call behavior uses either Pydantic AI's low-level direct model API for
`ModelResponse` control or a no-tools, single-request `Agent` when typed output
parsing, validation retries, or typed dependencies are required. The primitive
does not change the control-flow classification. An intentionally
provider-specific task may use its official provider API. Known sequences and
code-owned continuation conditions remain ordinary application workflows with
explicit model calls. Portable Python agents across supported providers use
Pydantic AI `Agent` by default. Pydantic AI capabilities or Harness are
conditional specialist layers; provider-native agent SDKs are separate
evaluated runtime escapes for named harness requirements.

The application owns a logical runtime facade, canonical state, model-routing
governance, tools, authorization, budgets, telemetry, and evaluation. Pydantic
AI owns portable provider translation and the model/tool interaction loop. The
facade may remain in-process, and optional databases, gateways, queues, durable
engines, and multi-agent topologies still need workload triggers.

Engineering work is a separate specialist lane: use the Codex SDK for
coding-focused threads and add Codex app-server only for a deep embedded client
that needs authentication, conversation history, approvals, or streamed agent
events. LangGraph remains conditional on explicit graph semantics, while one
selected qualified workflow engine owns durable cross-service execution;
either may wrap deterministic or agentic work. A full internal model-provider
abstraction is justified only by a proven non-Pydantic or cross-runtime
consumer.

## Files

- `SKILL.md`: runtime scope, workflow, guardrails, validation, and output
  contract.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.
- `evals/trigger-prompts.csv`: canonical positive and near-miss trigger cases.
- `evals/quality-cases.md`: supplemental output-quality and routing scenarios.
- `references/workload-contracts.md`: workload-specific decision inputs.
- `references/selection-framework.md`: hard gates, evidence rules, and decision
  record.
- `references/current-technology-baseline.md`: dated opinionated baseline and
  switch conditions.
- `references/agent-application-stack.md`: agent frameworks, state, durability,
  tools, and UI.
- `references/provider-agnostic-agent-platform.md`: Pydantic AI-first portable
  runtime, platform ownership, routing, tools, specialists, and native escapes.
- `references/model-provider-contract.md`: semantic provider portability.
- `references/mcp-core-conformance.md`: MCP core and conformance profile.
- `references/mcp-apps-tasks-gateway.md`: MCP extensions and gateway boundaries.
- Other references cover training, inference, retrieval, compatibility,
  evaluation, architecture, standards, source provenance, and handoffs.

## Boundaries

- Use `app-stack` for the surrounding product and ordinary application stack.
- Use `ai-agent-design` for agent-subsystem behavior, topology, authority,
  contracts, context, memory, failure policy, evaluation, and governance.
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
