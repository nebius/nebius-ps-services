# AI Stack Implementation Routing

Use this reference only when the user asks how an approved AI stack should hand
off to implementation. `ai-stack` remains the decision owner; it does not
materialize repositories, provision infrastructure, deploy, or replace the
specialist workflow that owns each component.

## Contents

- Authorization boundary
- Logical component handoff
- Specialist routing
- Suggested sequence
- Validation handoff
- Stop conditions

## Authorization Boundary

An approved stack does not authorize implementation or live actions. Keep these
separate:

- Advisory selection or review: read-only decision and validation plan.
- Logical implementation handoff: component capabilities, technologies,
  statuses, contracts, owners, and gates only.
- Repository implementation: a separately requested project or specialist
  workflow owns files and local validation.
- Live execution: training, paid provider calls, indexing, infrastructure,
  deployment, database, registry, or production mutations require exact target
  and action authorization.

Implicit invocation authorizes no writes, external cost, credentials, or
production access.

## Logical Component Handoff

Emit only the approved logical AI decision. For each component include:

- stable logical capability ID;
- capability class such as model access, agent kernel, durability, capability
  protocol, inference, training, retrieval, evaluation, or telemetry;
- canonical technology or external-service choice;
- `Required`, `Conditional`, `Deferred`, or `Rejected` status;
- requirement served and simplest-sufficient rationale;
- selected features and excluded features;
- interface, schema, protocol, artifact, state, and identity contracts;
- compatibility dependencies and evidence state;
- acceptance, security, recovery, rollback, and cost gates;
- operational owner or unresolved ownership requirement;
- revisit trigger.

Do not include:

- repository paths or target roots;
- materialization or runtime units;
- candidate sets or candidate manifests;
- per-file ownership or generated payloads;
- apply plans, digests, or execution authorization;
- unsupported scaffold schema fields.

If a later `scaffold-project` or project workflow is explicitly invoked, that
workflow owns topology, path assignment, candidate routing, approval, and safe
materialization. It must not silently change the approved technology or add a
Conditional or Deferred component.

## Specialist Routing

Discover available skills from current metadata at implementation time. Route
each approved component to the narrowest owner, for example:

- Python application or package setup;
- model training or fine-tuning;
- inference server and model artifact qualification;
- agent application or MCP implementation;
- retrieval, database, or data pipeline;
- API, backend, frontend, or application integration;
- container, Kubernetes, Helm, Terraform, cloud, or scheduler;
- security, identity, secrets, and policy;
- evaluation, tests, telemetry, documentation, and release.

Do not invent an installed skill or duplicate its workflow. Keep one content
owner per eventual file and one action owner per external mutation, but leave
those physical assignments to the implementation coordinator.

When no specialist exists, return an unsupported handoff or allow the owning
project workflow to implement ordinary glue using established repository
patterns. Do not reopen a settled stack without new blocking evidence.

## Suggested Sequence

Recommend a thin vertical slice:

1. Freeze the approved workload and acceptance gates.
2. Establish reproducible fixtures, evaluation data, and baseline.
3. Define the application runtime facade, Pydantic AI model/provider boundary,
   typed tools, canonical state, routing policy, and telemetry required by the
   slice.
4. Implement one minimum path with the fewest new components.
5. Enforce trusted identity, authorization, validation, and secret boundaries.
6. Add contract and end-to-end tests with immutable release identity.
7. Exercise timeout, cancellation, retry, partial failure, recovery, and
   rollback.
8. Measure quality, safety, latency, throughput, saturation, resource, and cost.
9. Add only an optimization whose switch condition is now observed.
10. Complete operations, rollout, rollback, and documentation.

Examples:

- RAG: one authoritative source, authorization before retrieval, labeled
  retrieval evaluation, and one answer path before hybrid or agentic retrieval.
- Agent: one in-process Pydantic AI loop, application-owned state, and read-only
  domain tools before a service facade, graph, durability, mutating tools,
  memory, native runtime escape, or multi-agent topology.
- Inference: one exact model and engine defaults before quantization, custom
  routing, distributed caches, or prefill/decode disaggregation.
- Training: smallest representative run and checkpoint-resume proof before full
  data, full scale, or a new scheduler/control plane.

## Validation Handoff

Pass these gates to the implementation owner:

- schema, format, static, type, configuration, and secret checks;
- unit and contract tests for model, tool, data, retrieval, state, and API
  boundaries;
- authorization and negative security tests;
- component and end-to-end quality and safety evaluation;
- exact provider, MCP, model/runtime, embedding/index, and artifact
  compatibility suites;
- checkpoint, resume, retry, cancellation, failure, recovery, migration, and
  rollback tests;
- representative latency, throughput, saturation, resource, and cost benchmark;
- artifact integrity, deployment dry run, canary, observability, alert, and
  runbook review.

Costly training, paid calls, large indexing, live clusters, production data, and
external services remain separately authorized. Skipped gates must name the
missing evidence and release consequence.

## Stop Conditions

Return a blocking handoff when:

- a workload contract or required acceptance gate is missing;
- implementation would change an unapproved choice;
- model, license, data right, hardware, region, API, schema, or protocol is
  incompatible or unverified;
- authorization depends on model behavior;
- an owner, live target, identity, data boundary, cost limit, migration,
  recovery, or rollback path is missing;
- current repository or external state would need to be guessed;
- an action exceeds current authorization.

Do not conceal a blocker with a placeholder, compatibility shim, unqualified
fallback, arbitrary retry, or second parallel stack.
