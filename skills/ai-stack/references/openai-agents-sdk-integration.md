# OpenAI Agents SDK Integration

Agent-framework policy reviewed: 2026-11-13. This preference is user-selected;
it does not assert universal superiority. Official documentation establishes
available interfaces, not target compatibility or production qualification.
Verify the exact Python or TypeScript release before implementing API details.

## Selection And Provider Boundary

Use the SDK for accepted agentic behavior unless the user or a fixed project
decision explicitly chooses another kernel. Keep direct model requests and
application-controlled workflows simple. A failed hard gate blocks selection;
report an alternative and obtain an override instead of quietly switching.

The SDK runs in the application's environment. It is distinct from OpenAI's
hosted Agents API and from the coding-focused Codex SDK. Framework preference
does not select a model provider, language, hosted sandbox, or persistence store.

For Python, use the public `Model` / `ModelProvider` integration points and a
run- or agent-scoped client. `OpenAIResponsesModel` and
`OpenAIChatCompletionsModel` are different protocol paths. Use a qualified
third-party adapter only where built-in integration cannot meet the requirement.
Do not change global provider defaults between concurrent tenants or runs.

Qualify tool calls, schemas, structured output, reasoning, streaming terminal
events, usage, cancellation, error classes, and data policy per exact route.
A compatible base URL does not prove Responses support, hosted-tool support,
or interchangeable model behavior. Fail closed on unsupported required features;
never silently drop settings or switch protocols/models.

## Ownership And Supported Seams

| Concern | Owner |
| --- | --- |
| Bounded model/tool loop | Standard SDK `Agent` and `Runner` |
| Domain authority, policy and effect dispatch | Deterministic application boundary |
| Admission, retry budgets, durable waits and recovery | One application/workflow owner |
| Canonical history, approvals, receipts and artifacts | Application-owned storage |
| Model selection and capability eligibility | Application route policy plus qualified adapter |
| Sandbox lifecycle, resource limits and descendant containment | Execution owner |
| Trace export, retention and evaluation identity | Application operations policy |

Use public model, tool, session, and lifecycle interfaces. Keep framework types
inside an adapter; do not create a second agent loop or a parallel scheduler.
Any required non-public seam is an explicit compatibility risk requiring pinned
source review and upgrade tests, not permission to patch incidental internals.

Agents-as-tools return bounded results to their caller; handoffs transfer
conversation control. Neither establishes distributed task admission, resource
reservations, independent reviewer authority, or cross-process recovery.
Distributed child work goes through the existing durable owner.

## State, Approval And Recovery

Choose one conversation-history strategy and document its canonical owner.
Explicit application history or an application-owned SDK Session can fit;
adding a Session database alongside an already authoritative history compiler
creates a second writer unless deliberately reconciled. Provider conversation
IDs and response chaining are optional optimizations with access, retention,
and rebuild policies.

Map SDK approval interruptions to exact application proposals and decisions.
Automatically authorized work need not prompt a human again. Recheck principal,
tenant, current rights, arguments, state revision, expiry, ownership and budgets
at resume and effect dispatch. SDK approval state is not an authorization grant.

`RunState` supports continuation; serialization alone does not authenticate its
origin or prove a safe distributed checkpoint. Select a recovery profile:

- Trusted persisted SDK state requires integrity, release/schema compatibility,
  current authorization, fencing and effect reconciliation before restoration.
- Reconstructible application checkpoints rebuild the SDK working view from
  canonical history and receipts after loss; invalidate stale proposals.

Reject client- or sandbox-supplied opaque state as authority. A missing tool
result is an unknown outcome until the effect owner reconciles it. Do not rerun
an effect merely to reconstruct conversation history.

## Execution And Security Controls

Set explicit models, finite turns, deadlines, tool concurrency, output limits
and cost budgets. Account cumulatively across retries, pauses and replacement
runtimes; a new `Runner` must not reset task allowances. Model-side parallel
call emission and actual tool execution concurrency are separate controls.
Resolve exact configuration fields from the pinned language/release.

Input guardrails can execute in parallel with agent work. For a blocking Python
input guard, use `run_in_parallel=False`; guardrails supplement deterministic
pre-dispatch policy. Function-tool guardrails do not cover every hosted tool,
handoff or other execution path. Enumerate coverage and mediate each path.

Treat lifecycle hooks as observation/integration points, not universal security
interceptors. Every tool, SDK helper and sandbox operation needs scoped
admission. Configure filesystem, shell, skills, hosted tools, nested agents and
memory explicitly; feature availability does not authorize activation.

Distinguish expected tool failure, unknown effect, cancellation and failed
observation persistence. A formatted error string must not become successful
completion evidence or an invitation to repeat an uncertain effect. Persist
bounded receipts before delivering observations. Bound intermediate capture,
decoding and parsing as well as the final model-visible preview.

Consume streaming execution through its terminal outcome. Token delivery,
iterator closure, a cancelled coroutine or a cleanup callback does not prove
remote quiescence. Fence new dispatch, stop/drain exact owned descendants,
reconcile unknown effects and retain reservations until containment is proven.
Keep separately scoped cleanup authority available after ordinary task expiry.

## Tracing And Qualification

Normal server-side tracing is enabled by default. Before the first run, select
an approved export/retention policy and explicitly disable or replace export
where required. Keep useful bounded telemetry without copying credentials,
sensitive prompts or tool payloads into an unapproved trace sink.

Pin the resolved SDK, provider client, adapters, tool schemas and runtime
configuration. Qualify upgrades against concurrent tenants, partial streams,
malformed tool batches, approval drift, retries, unknown effects, cancellation,
checkpoint reconstruction and trace export. Include hidden SDK helper calls in
resource and authority accounting. Static/source inspection, adapter conformance,
runtime behavior and live outcome qualification are separate evidence lanes.

## Current Official Sources

- [SDK overview](https://developers.openai.com/api/docs/guides/agents/sdk)
- [Models and providers](https://developers.openai.com/api/docs/guides/agents/models)
- [Python provider integration](https://openai.github.io/openai-agents-python/models/)
- [Running agents and conversation strategies](https://developers.openai.com/api/docs/guides/agents/running-agents)
- [Results and resumable state](https://developers.openai.com/api/docs/guides/agents/results)
- [Guardrails and approvals](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals)
- [Python guardrail coverage](https://openai.github.io/openai-agents-python/guardrails/)
- [Tracing and MCP integration](https://developers.openai.com/api/docs/guides/agents/integrations-observability)

The ownership, fencing, receipt, resource and recovery rules above are
application design requirements. SDK support alone does not prove them.
