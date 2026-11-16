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

Use **deterministic where possible, agentic where necessary**. Direct calls
use the selected provider's task API or an existing qualified adapter; ordinary
code owns known workflows. For genuine agents, prefer **OpenAI Agents SDK**
unless the user explicitly overrides it or an accepted fixed project stack
already applies. A failed hard gate blocks selection and calls for an explicit
override, rather than a silent framework switch.

The preference does not mandate OpenAI models or hosted execution. Qualify the
exact SDK, provider adapter, endpoint, model and capabilities. Pydantic AI and
other frameworks remain valid explicit alternatives. Keep one primary loop;
do not nest agent kernels or duplicate SDK provider abstractions without a
real cross-runtime requirement.

Application code owns policy, tools, canonical state, budgets, recovery and
independent outcome verification. The SDK owns its supported loop. The facade
can stay in-process, and optional infrastructure requires a named trigger.
Use Codex SDK for a coding-harness requirement. Graphs and durable workflows
remain independent choices; select one qualified durability owner when needed.

The integration guidance covers approval drift, SDK state versus checkpoints,
unknown effects, tool/helper admission, bounded concurrency, streaming
completion, trace export and upgrade qualification.

## Files

- `SKILL.md`: runtime scope, workflow, guardrails, validation, and output
  contract.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.
- `evals/trigger-prompts.csv`: canonical positive and near-miss trigger cases.
- `evals/evals.json`: executable quality scenarios and observable assertions.
- `evals/quality-cases.md`: supplemental output-quality and routing scenarios.
- `references/workload-contracts.md`: workload-specific decision inputs.
- `references/selection-framework.md`: hard gates, evidence rules, and decision
  record.
- `references/current-technology-baseline.md`: dated opinionated baseline and
  switch conditions.
- `references/agent-application-stack.md`: agent frameworks, state, durability,
  tools, and UI.
- `references/provider-agnostic-agent-platform.md`: SDK-first application
  boundary, platform ownership, routing, tools, state and specialists.
- `references/openai-agents-sdk-integration.md`: preferred SDK integration and
  public source evidence, with separate application safeguards.
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
