# AI Agent Design

`ai-agent-design` is an implicitly invokable, provider-neutral design skill for
production AI-agent subsystems. It applies agent-specific patterns, policies,
and contracts, then uses `ai-stack` for workload-driven component and
technology selection only when a model-backed capability remains.

The skill is advisory. It produces a logical architecture and implementation
handoff but does not write code, install dependencies, provision services,
deploy, or mutate live systems.

## Core Model

The skill treats an agent as a probabilistic planner inside a deterministic,
governed software system:

```text
workload contract
  -> behavior classification
  -> smallest sufficient agent pattern when the class is Agent
  -> trusted control, execution, and durable-session architecture
  -> contracts and deterministic policy shell
  -> $ai-stack component decision, or explicit no-AI decision
  -> complete agent-subsystem architecture
  -> logical implementation handoff
```

It keeps deterministic code, direct model calls, deterministic AI workflows,
and agents distinct, and reports every class even when it is empty. It also
treats model autonomy, graph topology, durable execution, and remote deployment
as independent decisions.

## Boundaries

- `ai-agent-design` owns agent patterns, policies, contracts, and subsystem
  synthesis.
- `ai-stack` owns AI component and technology selection when a model-backed
  capability remains; deterministic-only results skip that handoff explicitly.
- An originating `ai-stack` workflow may delegate a disputed provisional
  contract here; `ai-agent-design` returns the frozen contract and skips a
  nested stack handoff so component selection occurs once.
- `design` owns cross-layer whole-solution design.
- `app-stack` owns surrounding non-AI product layers.
- `research` owns focused due diligence on one volatile technology or claim.
- `system-design-rules` owns checklist review of an existing generic design.
- Project and implementation skills own repository topology and execution.

## Files

- `SKILL.md`: trigger, workflow, routing, guardrails, validation, and output.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.
- `references/system-classification-and-patterns.md`: behavior classification
  and single-/multi-agent pattern selection.
- `references/runtime-architecture-and-sessions.md`: trusted control,
  execution, and durable-state separation; provider-neutral interfaces;
  sessions; bounded loops; and operator controls.
- `references/contracts-context-and-memory.md`: task, agent, context,
  provenance, and memory contracts.
- `references/tools-authority-and-security.md`: tools, action risk, approvals,
  MCP boundaries, and layered security.
- `references/durability-failures-and-effects.md`: state, concurrency,
  idempotency, retries, compensation, and reconciliation.
- `references/evaluation-observability-and-governance.md`: evaluation, traces,
  independent outcome verification, privacy, release identity, rollout,
  incidents, and readiness.
- `evals/trigger-prompts.csv`: canonical routing cases.
- `evals/process-cases.md`: workflow and manual runtime assertions.
- `evals/evals.json`: representative output-quality cases.

## Invocation

Codex may select the skill implicitly when a request asks for agent-specific
architecture, patterns, policies, or production design. Invoke it explicitly
with `$ai-agent-design` when deterministic selection is important.

Use `$ai-agent-design --help` or `$ai-agent-design -h` for report-only help.
There are no additional public flags.

## Evidence Boundary

Source structure and static eval definitions can pass without proving runtime
selection or design quality. Installed parity, fresh-session trigger behavior,
and output-quality comparison remain separate evidence gates.
