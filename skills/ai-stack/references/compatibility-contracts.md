# AI Stack Compatibility Contracts

Use this reference when two selected AI components must interoperate. A product
name match or compatible API shape is not proof. Record the exact edge, test it
at the narrowest risky boundary, and bind the result to immutable identities.

## Contents

- Contract rules
- Compatibility graph
- Required edge records
- Acceptance gates
- Deployment identity
- Release blockers

## Contract Rules

For every material edge, record:

- producer and consumer, including exact versions or revisions;
- artifact, API, schema, protocol, or state passed across the boundary;
- required semantic behavior, not only syntax;
- documented support and unsupported combinations;
- target test, fixture, environment, threshold, and result;
- conversion or translation owner;
- failure behavior, fallback, rollback, and migration path;
- evidence state and revalidation trigger.

Use `Measured`, `Officially documented`, or `Assumed`. An `Assumed` edge cannot
close a hard gate. A documented feature still requires target measurement when
quality, performance, safety, recovery, or cost matters.

Do not hide a failed edge behind a compatibility shim. Change the selected
combination, qualify a bounded adapter, or block the release.

## Compatibility Graph

At minimum, evaluate the edges that exist in the selected design:

```text
source data -> transformation -> tokenizer/template -> training
base model -> adapter -> merged or composed model -> quantization
training checkpoint -> serving artifact -> inference runtime -> hardware
model provider -> Pydantic AI Model/Provider/profile -> runtime facade
provider-native runtime -> runtime facade -> normalized application events
runtime facade -> typed tool adapter -> policy gateway -> domain service
embedding model -> dimensions/normalization -> index -> retrieval policy
agent state -> graph checkpoint -> durable workflow state -> side effects
MCP server -> SDK/framework -> transport -> client/host -> extensions
artifact registry -> deployment route -> telemetry/evaluation identity
```

Do not create records for layers that are absent.

## Required Edge Records

### Model, Runtime, And Hardware

Record:

- model architecture, revision or digest, license, precision, and modality;
- runtime and exact version, supported kernels, parallelism, and memory model;
- accelerator family, compute capability or equivalent, memory, topology,
  drivers, libraries, container, and operating system;
- tokenizer, special tokens, chat template, generation config, and tool parser;
- input/output limits, structured output, streaming, cancellation, and batch;
- measured quality, memory, warmup, latency, throughput, saturation, and cost.

Gate on the exact deployment tuple. Reject a runtime that silently changes
tokenization, unsupported operators, numerical behavior, tool-call formatting,
or output quality.

### Model And Quantization

Record:

- source model revision and weight format;
- quantization method, bit widths, group size, calibration data and code;
- excluded layers, activation or KV-cache precision, and kernel requirements;
- runtime loader, conversion tool, hardware, and output artifact digest;
- quality deltas by representative slice and performance/resource deltas.

Gate on quality before accepting throughput or memory gains. Do not call two
artifacts equivalent because they share a model name.

### Base Model And Adapter

Record:

- base model, tokenizer, template, and revision;
- PEFT method, adapter configuration, target modules, rank, precision, and
  training code identity;
- composition order for multiple adapters and merge algorithm when used;
- loader/runtime support, serving mode, and rollback artifact;
- evaluation of the base, composed, and merged variants.

Reject adapters trained against an incompatible base or tokenizer. Treat a
merged derivative as a new immutable artifact with independent evaluation.

### Training Checkpoint And Serving Runtime

Keep the training checkpoint and serving artifact separate. Record:

- checkpoint schema, sharding, topology, framework version, optimizer and
  resume state;
- export/conversion code and exact input/output digests;
- serving weights, config, tokenizer, template, adapters, quantization, and
  runtime expectations;
- numerical parity tolerance, task quality, tool behavior, and target serving
  benchmark.

A written checkpoint directory is not a passing edge. Resume training from it,
export the serving artifact, and validate that artifact in the actual runtime.

### Embedding Model And Index

Record:

- embedding provider/model/revision, dimensions, maximum input, batching, and
  truncation;
- text preprocessing, chunking, language handling, normalization, and version;
- distance metric, index type and parameters, vector type, metadata schema,
  filters, and tenant policy;
- corpus version, backfill, dual-read, mixed-version, deletion, restore, and
  rollback behavior;
- labeled relevance set, recall, precision, ranking, latency, and cost.

An embedding change is a data migration. Reject dimension, normalization, or
distance mismatches even when insertion succeeds.

### Retrieval And Authorization

Record:

- authoritative document identity and ownership;
- caller, tenant, document, and field-level policy owners;
- the trusted boundary that authorizes before search or context assembly;
- filter semantics, missing metadata behavior, cache partitioning, and audit;
- delete propagation and index consistency expectations.

Reject post-retrieval filtering as the only tenant control. The model and
vector similarity layer are not authorization authorities.

### Model Provider And Agent Framework

For each exact provider, endpoint, model, region, adapter, protocol mode, and
framework version, record:

- message roles and typed content blocks;
- tool definition, tool choice, parallel calls, call/result correlation, and
  schema subset;
- structured output, refusals, safety metadata, reasoning controls, citations,
  caching, and batch;
- streaming lifecycle and text, reasoning, tool-call, usage, refusal, safety,
  terminal, cancellation, and error events;
- context/output limits, token accounting, rate limits, retry classes, request
  IDs, retention, residency, and provider extensions.

Changing API key, base URL, and model name proves connectivity only. Run the
same conformance suite for native and OpenAI-compatible adapters.

### Tool Schema And Execution

Record:

- canonical internal tool name, purpose, JSON Schema dialect and subset;
- translated provider or protocol schema and version;
- semantic validation beyond JSON Schema;
- caller identity, tenant, risk class, authorization, approval, and deadline;
- idempotency key, side effects, compensation, output schema, and audit event;
- model/framework/MCP mapping and exact error contract.

Reject tool execution when schema translation widens authority, drops required
validation, or allows model-controlled identity or policy fields.

### MCP Server, Client, Host, And Extensions

Record independently:

- MCP core revision;
- client SDK and exact version;
- server SDK and exact version;
- framework and exact version;
- local `stdio` or remote Streamable HTTP transport;
- authorization profile and resource/audience binding;
- extension identifiers and exact revisions;
- host, gateway, server, and compatibility-matrix versions;
- discovery, cache, routing-header, error, cancellation, trace, and migration
  behavior.

Never infer MCP core from a FastMCP package major. Require Tier 1 official SDK
interoperability and per-host extension tests.

### Graph State, Durable Workflow, And Retry Semantics

Record the owner of:

- request and run context;
- session history;
- agent or graph checkpoint state;
- durable business-process state;
- side-effect idempotency and compensation;
- queue/job state, long-term memory, knowledge, and audit history;
- timeouts, retries, backoff, cancellation, and terminal failures.

Assign each responsibility to one layer. Reject designs where the agent kernel,
graph, Temporal workflow, queue, and provider client independently retry the
same non-idempotent action.

Treat provider sessions, response IDs, caches, and native compaction as
optimizations. Reject a design that cannot reconstruct required conversation,
approval, workflow, or side-effect state from application-owned records.

### Registry, Deployment, And Evaluation

Record:

- source, dependency, build, container, driver, and runtime identity;
- model, adapter, tokenizer, template, prompt, tool schema, Skill, MCP server,
  policy, data, index, evaluator, and configuration identity;
- artifact digests, lineage, license, approvals, and retention;
- deployment route, region, capacity, fallback, canary, and rollback target;
- trace fields and the evaluation report that qualifies the exact tuple.

The registry is an approval and lineage authority, not only a file catalog.
Mutable aliases may point to immutable versions but cannot replace them in the
release record.

## Acceptance Gates

Select only the gates relevant to the edge:

| Gate | Required proof |
| --- | --- |
| Functional | Exact required modality, API, schema, protocol, or artifact behavior succeeds. |
| Quality | Representative evaluation meets slice and regression thresholds. |
| Safety | Unsafe-attempt, refusal, policy, isolation, and authorization tests pass. |
| Performance | Percentile latency, throughput, saturation, resource, and cost targets pass on the target setup. |
| Reliability | Retry, cancellation, restart, resume, fault, upgrade, and rollback behavior is bounded. |
| Data | Residency, retention, deletion, provenance, license, and tenant boundaries pass. |
| Operations | Telemetry, alerting, capacity, runbook, owner, and recovery objectives are present. |
| Portability | Export, conversion, downgrade, migration, or replacement path is exercised where required. |

Use identical inputs and environments when comparing candidates. Record
variance, tail behavior, and failure modes; do not accept a single average.

## Compatibility Record Template

```markdown
### <producer> -> <consumer>

Purpose: <requirement>
Producer identity: <exact tuple>
Consumer identity: <exact tuple>
Boundary artifact or protocol: <exact schema/version/digest>
Required semantics: <behavior>
Evidence: Measured | Officially documented | Assumed
Target proof: <fixture, environment, command, metric, threshold>
Failure behavior: <error, retry owner, safe terminal state>
Migration and rollback: <path>
Owner: <team or role>
Revalidate when: <observable trigger>
```

## Release Blockers

Block release when:

- a required edge remains `Assumed` and no bounded validation exists;
- an exact model, endpoint, adapter, SDK, framework, extension, host, runtime,
  artifact, or policy version is missing;
- the compatibility test does not represent the target workload;
- schema or protocol translation changes authority or semantics;
- checkpoint resume, serving export, deletion, migration, recovery, or rollback
  is required but untested;
- authorization occurs after retrieval or tool invocation;
- the release cannot be tied to an immutable deployment and evaluation
  identity;
- no owner accepts the new failure modes and operating burden.
