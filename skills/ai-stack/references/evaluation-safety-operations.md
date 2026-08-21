# Evaluation, Safety, And Operations

Use this reference for every production or productionization decision and every
benchmark. Evaluation, safety, reliability, interoperability, observability,
and cost are stack requirements, not cleanup tasks added after product
selection.

Baseline date: 2026-08-07

## Contents

- Acceptance-gate model
- Evaluation layers
- Agentic interoperability evaluation
- Reproducible benchmark contract
- Safety and threat model
- Observability and evaluation identity
- Reliability and recovery
- Release, rollout, and rollback
- Cost and capacity
- Production learning loop

Start with the existing telemetry, evaluation, experiment, artifact, and model
lineage authorities. Add OpenTelemetry when runtimes or services need one trace
identity. Add MLflow only when the existing platform cannot satisfy required
evaluation, experiment, artifact, or promotion lineage. Read
`compatibility-contracts.md` for the immutable evaluation identity and
registry-to-deployment contract. Read `model-provider-contract.md` for provider
conformance. Read `mcp-core-conformance.md` for MCP core and interoperability,
and `mcp-apps-tasks-gateway.md` for Apps, Tasks, identity, and gateway tests.

## Acceptance-Gate Model

Define gates before implementation or benchmarking:

- Functional gate: The intended task and interfaces work.
- Quality gate: Task-specific quality meets approved thresholds and slices.
- Retrieval gate: Relevance, ACL, freshness, and provenance meet thresholds.
- Agent gate: Planning, tools, side effects, and recovery are correct.
- Provider portability gate: Every required provider profile passes the same
  capability, stream, tool, error, usage, and policy contract.
- Protocol conformance gate: Every required MCP, A2A, AG-UI, ACP, or extension
  path passes exact-version conformance and interoperability tests.
- Safety gate: Disallowed behavior and data-boundary violations remain below
  approved limits, with deterministic controls for hard policy.
- Performance gate: Latency, throughput, saturation, and resource targets are
  met at the representative workload.
- Reliability gate: Failure, cancellation, retry, resume, degradation, and
  rollback behave as designed.
- Economic gate: Cost per accepted outcome and capacity buffer meet budget.
- Operational gate: Owners can deploy, observe, diagnose, upgrade, recover, and
  retire the stack.

Mark hard gates explicitly. Do not trade a privacy, authorization, license,
quality, protocol, or recovery failure for better throughput.

## Evaluation Layers

### Component Evaluation

Evaluate the narrow component before the whole system:

- Model: Task quality, structured output, tool-call format, safety, and slices.
- Pydantic AI model/provider path: Profile resolution, settings, message
  mapping, stream reconstruction, tool calls, structured output, errors,
  usage, cancellation, and data policy.
- Agent kernel: State isolation, dependency injection, tool lifecycle, output
  validation, bounded execution, and model-provider substitution.
- Provider-native runtime escape: Named harness requirement, facade mapping,
  canonical-state independence, tool mediation, normalized events, lifecycle,
  data policy, evaluation parity, and rollback to the portable path.
- Workflow engine: Durable timers, retries, idempotency, cancellation,
  compensation, resume, replay, and version migration.
- Skill or repository instruction: Triggering, scope, precedence, provenance,
  conflict handling, least privilege, and supply-chain integrity.
- Embedding and retrieval: Relevance, filters, ACL, freshness, and latency.
- Reranker: Ranking lift, latency, and failure behavior.
- Tool: Schema, authorization, idempotency, side effects, and errors.
- MCP client: Discovery, negotiation, transport, auth, timeout, cancellation,
  schema handling, and identity isolation.
- MCP server: Protocol conformance, tool and resource contracts, authorization,
  stateless scaling, error behavior, and release compatibility.
- MCP App: Host support, resource integrity, sandbox, CSP, bridge permissions,
  accessibility, fallback content, and tenant isolation.
- MCP Task adapter: Handle ownership, durable executor mapping, idempotency,
  progress, cancellation, expiry, retention, and reconciliation.
- A2A adapter: Agent-card trust, delegation authority, message and artifact
  handling, task ownership, identity, and failure semantics.
- Guardrail: Detection, false positives, false negatives, bypasses, and latency.
- Runtime: Model compatibility, quality equivalence, latency, throughput,
  memory, cancellation, and recovery.
- Gateway: Routing, policy, identity, tenant isolation, streaming, protocol
  fidelity, observability, failover, and performance overhead.

### Workflow Evaluation

Evaluate model and tool steps together:

- Routing and plan correctness: Select the correct model, provider, tool, skill,
  MCP server, agent, or human path.
- Tool choice and arguments: Produce valid, authorized, semantically correct
  requests.
- State transitions: Reach valid terminal states without hidden or unbounded
  loops.
- Approval and policy: Enforce deterministic approval, identity, and policy at
  the execution boundary.
- Side effects: Prevent duplicates, verify postconditions, and reconcile partial
  completion.
- Failure behavior: Exercise retries, cancellation, disconnect, process loss,
  provider failover, and resume.
- Resource behavior: Measure latency, tokens, calls, queue time, and cost by
  stage.
- Product outcome: Measure task success, user correction, and accepted result.

### End-To-End Evaluation

Evaluate the observable product outcome with the exact application, agent
framework, provider adapter, model profile, prompt, skills, tools, MCP or A2A
endpoints, data, index, runtime, policies, gateway, and configuration that will
ship. Include realistic dependency failures and external-service behavior.

### Human Evaluation

Use calibrated human review for subjective quality, high-impact safety, nuanced
preference, or low-frequency critical failures. Define rubric, reviewer
qualification, blinding, disagreement handling, sample size, and escalation.
Do not use unstructured thumbs-up review as the only release gate.

### Model-Based Evaluation

Use model graders only with a clear rubric, reference or evidence where
possible, calibration against human or deterministic labels, variance checks,
and a plan for grader drift. Do not use the same model family as generator and
sole judge for material claims without independent validation.

## Agentic Interoperability Evaluation

### Provider Portability Suite

Run the same tests through each supported Pydantic AI model/provider profile,
including OpenAI, Anthropic, Google, Nebius Token Factory, self-hosted, and any
gateway path selected for the product. Do not call an adapter portable merely
because it accepts an API key and base URL.

Test:

- system, developer, user, assistant, and tool message mapping;
- text, image, audio, file, and other required input parts;
- streaming text and structured event reconstruction;
- tool schema acceptance, tool selection, parallel calls, and tool results;
- strict structured output and validation failure behavior;
- finish reasons, refusals, safety metadata, and content filtering;
- token or unit accounting and pricing metadata;
- context, output, timeout, and payload limits;
- retries, rate limits, overload, invalid request, auth, and policy errors;
- cancellation and disconnect behavior;
- model alias, revision, retirement, and fallback policy;
- provider retention, training use, residency, and logging policy;
- trace propagation and redaction;
- quality, safety, latency, and cost on the same representative evaluation set.

Block an automatic provider fallback when the destination does not meet the
same data, safety, capability, quality, or residency policy. Record the
fallback as a separate evaluated route.

### MCP Conformance Suite

Test the exact combination of:

```text
MCP core revision
client SDK and version
server SDK and version
framework and version
transport
host and version
gateway and version
auth mode
extension names and revisions
```

Require:

- official conformance tests for the selected core revision;
- golden fixtures for discovery, lists, reads, calls, errors, caching, tracing,
  authorization, and multi round-trip input;
- official Tier 1 client against the server;
- official Tier 1 server against the client or host;
- FastMCP Client tests where FastMCP is selected;
- host-specific tests for MCP Apps and Tasks;
- cross-version tests for supported compatibility windows;
- negative tests for unadvertised methods or extensions;
- load and chaos tests for stateless horizontal scaling;
- cross-tenant authorization tests for tools, resources, tasks, and UI data.

Treat the MCP core revision separately from the SDK major, FastMCP major, Apps
revision, Tasks revision, and host feature support.

### MCP Apps Suite

Test:

- negotiated Apps extension revision;
- `ui://` resource resolution and integrity;
- iframe sandbox and content security policy;
- JSON-RPC bridge method allowlist;
- tool calls initiated from the UI;
- identity, consent, and approval propagation;
- conversation and tenant switching;
- untrusted tool result rendering and output encoding;
- network and storage restrictions;
- accessibility, keyboard navigation, and responsive layout;
- text or non-App fallback;
- host upgrade and unsupported-host behavior;
- App resource cache invalidation and rollback.

Treat model-generated executable UI code as untrusted code. Keep generative UI
Conditional and require stronger sandbox, dependency, egress, integrity, and
review controls than declarative or prebuilt Apps.

### MCP Tasks Suite

The core MCP revision and the Tasks extension have independent lifecycle and
maturity. At the 2026-08-07 baseline, keep Tasks Conditional and pin the exact
published extension revision and SDK implementation.

Test:

- `tools/call` returning a task handle plus `tasks/get`, `tasks/update`, and
  `tasks/cancel` for the exact selected Draft revision;
- completion, failure, cancellation, expiry, retention cleanup, and result
  publication in the backing durable owner;
- duplicate calls and idempotent attachment to an existing operation;
- opaque handle ownership and unguessability;
- authorization after role, tenant, or policy changes;
- client disconnect, host restart, server restart, and replica change;
- gateway rollout and protocol-version changes;
- workflow or job-system outage and reconciliation;
- progress, pending input, and approval state;
- cancellation request versus actual executor termination;
- large result references and object-store authorization;
- retention, deletion, audit, and trace continuity;
- migration from any experimental Tasks implementation.

Do not treat a protocol Task as the durable executor. Test the mapping to
Temporal, a job system, a provider batch API, or another named owner.

### A2A And Frontend Protocol Suite

Where selected, test:

- A2A v1.0: Requested and negotiated `A2A-Version`, Agent Card authenticity,
  declared capabilities, discovery policy, remote identity, delegation scope,
  task and artifact ownership, cancellation, timeout, replay, untrusted-agent
  output, and official compatibility-kit coverage.
- AG-UI: Event ordering, reconnection, state synchronization, approval events,
  tool and artifact rendering, and runtime substitution.
- ACP: Editor permissions, workspace roots, terminal or file actions, session
  lifecycle, cancellation, and coding-agent isolation.

Do not introduce these protocols without an independent ownership boundary or
multiple implementations that justify interoperability cost.

### Skill And Instruction Suite

For Agent Skills, repository `AGENTS.md`, and similar instruction bundles,
test:

- discovery and trigger accuracy;
- instruction precedence and conflict handling;
- path or repository scoping;
- version and provenance;
- unsafe instruction, dependency, or executable-content handling;
- least-privilege tool access;
- reproducibility across supported hosts;
- removal, rollback, and cache invalidation.

Treat instructions as supply-chain inputs. Do not grant new permissions merely
because a Skill or repository file requests them.

## Reproducible Benchmark Contract

Record immutable or reconstructible identities for:

- source revision and environment;
- agent framework, workflow engine, and framework configuration;
- provider adapter, provider profile, gateway route, and credential class;
- model, tokenizer, templates, adapters, quantization, and runtime;
- skills, repository instructions, prompts, tools, policies, and schemas;
- MCP core, SDK, framework, transport, host, gateway, and extension revisions;
- A2A, AG-UI, or ACP revision where selected;
- data snapshot, index, retrieval, memory, and state-store configuration;
- hardware, topology, drivers, kernels, container, and infrastructure limits;
- traffic or dataset, slices, seeds, warmup, duration, repetitions, and cache
  state;
- metric implementations, evaluator versions, thresholds, and raw result
  locations;
- timestamps and current pricing assumptions for economic results.

Benchmark rules:

- Define the question and acceptance threshold first.
- Change one material variable per comparison.
- Use identical workload and environment when claiming comparative performance.
- Report distributions, variance, tail behavior, saturation, and failures.
- Run enough repetitions to distinguish noise from meaningful change.
- Preserve raw measurements and configuration, not only summary charts.
- Re-run quality and safety when provider, runtime, quantization, model, prompt,
  skill, data, retrieval, tool, protocol, gateway, or policy changes can affect
  behavior.
- Label extrapolation and vendor-published results as unmeasured assumptions for
  the target environment.

## Safety And Threat Model

Map assets, actors, trust boundaries, allowed actions, and maximum impact.
Include at least the following categories.

### Prompt And Content Threats

- Direct and indirect prompt injection: Hidden instructions can arrive through
  documents, websites, tool output, MCP resources, agent messages, images, or
  metadata.
- Policy extraction: Attackers can seek system prompts, hidden schemas, secrets,
  or approval logic.
- Parser abuse: Malicious formatting, oversized content, recursive structures,
  or ambiguous encodings can exhaust or confuse the stack.
- Poisoned knowledge: Memory, retrieval corpora, examples, skills, and synthetic
  data can carry malicious or misleading content.

Controls: Treat external content as data, isolate instructions, preserve source
provenance, constrain context, validate outputs, and test adversarial cases.

### Provider And Routing Threats

- Credential confusion: A provider key, tenant credential, workload identity,
  or delegated token can be applied to the wrong route.
- Policy bypass by fallback: A gateway can silently route restricted data to a
  provider with different retention, residency, or training-use policy.
- Capability downgrade: Fallback can remove structured output, safety metadata,
  tool semantics, or quality guarantees.
- Alias drift: A mutable model name can change behavior without release review.
- Usage manipulation: Incomplete or inconsistent accounting can hide cost and
  abuse.

Controls: Use typed provider profiles, secret references, explicit capability
and policy registries, immutable model identity where available, route
allowlists, per-route evaluation, and observable fallback.

### Tool, MCP, And Agency Threats

- Excessive permissions: Agents, MCP servers, or remote agents can act with
  authority broader than the initiating user or task.
- Confused deputy: Model-generated tenant, resource, or identity arguments can
  cause actions under a stronger service identity.
- Catalog poisoning: A malicious server or gateway can alter tool names,
  descriptions, schemas, or UI resources.
- Unsafe generated inputs: Shell, code, SQL, paths, URLs, and requests can cause
  injection, SSRF, local-file access, secret access, or data exfiltration.
- Duplicate effects: Retry, replay, reconnect, or workflow restart can repeat a
  write.
- Approval bypass: Race, stale approval, changed arguments, or scope expansion
  can invalidate consent.
- Cross-agent delegation: A remote A2A agent can exceed the delegated purpose or
  return untrusted artifacts.

Controls: Use least-privilege identities, typed interfaces, semantic validation,
allowlists, sandboxing, egress controls, idempotency, approvals bound to exact
arguments, postcondition verification, protocol conformance, and audit.

### MCP App And UI Threats

- Bridge abuse: An App can invoke unauthorized host or tool methods.
- UI spoofing: The App can imitate host chrome, approval, or identity surfaces.
- Data leakage: UI resources, browser storage, network calls, or rendered tool
  output can cross tenant or policy boundaries.
- Injection: Untrusted result content can become executable HTML, script, URL,
  or style content.
- Generative code: Model-written UI code can execute unexpected logic or import
  untrusted dependencies.

Controls: Use sandboxed iframes, strict CSP, bridge allowlists, trusted host
approval surfaces, output encoding, network and storage restrictions, signed or
hashed resources, tenant-bound data, accessibility review, and fallback UI.

### MCP Task And Durable-Workflow Threats

- Handle theft or guessing: A task ID can expose progress or results.
- Cross-tenant access: Task retrieval, update, cancellation, or result fetch can
  use stale or incorrect ownership.
- Cancellation ambiguity: The protocol can acknowledge cancellation while the
  external side effect continues.
- Orphaned work: Server, gateway, or executor failure can leave unowned jobs.
- Replay: Duplicate tool calls can create multiple durable workflows.
- Retention mismatch: Task metadata, artifacts, traces, and downstream results
  can outlive the governing policy.

Controls: Use opaque unguessable handles, authorize every operation, bind to
caller and tenant, use idempotency and operation ledgers, reconcile executor
state, expose final cancellation outcome, and propagate retention and deletion.

### Data And Tenant Threats

- Cross-tenant retrieval, cache, memory, trace, App, task, adapter, or model
  leakage.
- Missing deletion or access-revocation propagation.
- Sensitive data in prompts, logs, traces, evaluation sets, feedback, or UI
  resources.
- Provider retention or training use outside policy.
- Model, data, skill, or code license violations.

Controls: Propagate verified identity, authorize before retrieval or execution,
use tenant-safe keys, encryption, retention and deletion workflows, redaction,
minimization, provider review, and lineage.

### Model And Supply-Chain Threats

- Untrusted model code, weights, tokenizer, adapter, image, package, Skill, MCP
  server, App resource, remote agent, or provider SDK.
- Malicious serialization, dependency, plugin, or remote-code execution.
- Artifact substitution, mutable tags, or unsigned promotion.
- Unsupported or abandoned protocol, extension, framework, or model versions.

Controls: Use trusted sources, checksums or signatures, software bills of
materials, scanning, sandboxing, version pinning, immutable artifacts, approval,
provenance, conformance, and controlled promotion.

Probabilistic guardrails can reduce risk but cannot replace deterministic
identity, authorization, network, filesystem, execution, protocol, and release
boundaries.

## Observability And Evaluation Identity

Use common trace and correlation identities across the user request, agent run,
workflow, model call, retrieval, tool call, MCP or A2A call, App, Task,
guardrail, serving route, gateway, and infrastructure.

Propagate a versioned evaluation identity containing:

```text
application and source revision
agent framework and version
workflow engine and workflow revision
provider adapter and version
provider profile and capability-registry revision
provider, base URL class, and model identity
gateway route and gateway version
model, adapter, tokenizer, chat template, and generation configuration
system prompt and instruction-bundle digests
Agent Skill and AGENTS.md digests
tool names and schema digests
MCP core revision
MCP client and server SDK versions
MCP framework and version
MCP transport, host, and gateway versions
MCP Apps and Tasks extension revisions
MCP App resource digest
MCP task and durable workflow identity
A2A, AG-UI, or ACP revisions where selected
authorization, approval, guardrail, and tenant-policy revisions
embedding model, index generation, reranker, and retrieval configuration
runtime, container digest, hardware class, and deployment configuration
evaluation dataset, evaluator, grader, and threshold revisions
```

Record operational metadata needed for attribution:

- Route identity: Provider, model, adapter, fallback, gateway, and credential
  class, without logging secrets.
- Agent identity: Agent, graph or workflow node, skill, instruction revision,
  state checkpoint, retry, and terminal status.
- Tool identity: Tool, MCP server, schema, caller identity class, authorization,
  approval, idempotency, result, and side-effect verification.
- Protocol identity: Negotiated core and extensions, SDK, host, transport,
  gateway, discovery catalog, App resource, and Task handle correlation.
- Retrieval identity: Source, index, embedding, strategy, filters, reranker,
  tenant policy, and provenance.
- Resource behavior: Latency, queue time, tokens or units, call counts, cache,
  replica, accelerator, memory, capacity, and cost.
- Policy behavior: Guardrail decision, policy version, refusal, fallback,
  failure, cancellation, and quality outcome.

Apply data minimization, redaction, encryption, access control, retention, and
deletion to telemetry. Do not assume prompts, retrieved context, tool arguments,
MCP content, task results, App data, or remote-agent messages are safe to record.

Monitor at least:

- task success, user corrections, acceptance, refusal, and fallback;
- provider route and model distribution, capability mismatch, and fallback rate;
- model, tool, MCP, A2A, workflow, and gateway errors;
- unsafe attempts, approval outcomes, authorization denials, and side effects;
- retrieval recall proxies, empty results, stale sources, score shifts, ACL
  denials, and citation quality;
- time to first token, output latency, end-to-end percentiles, queueing,
  saturation, throughput, and cold start;
- tokens or units, model calls, tool calls, cache hit rate, accelerator use,
  memory, and cost;
- MCP negotiation, discovery cache, App load, Task lifecycle, and conformance
  health;
- checkpoint, ingestion, index, registry, rollout, retention, and deletion
  health.

Metrics are aggregate signals, traces localize a path, and logs explain specific
events. Design correlation before an incident.

## Reliability And Recovery

For each provider, protocol, tool, agent, workflow, or distributed boundary
define:

- deadline propagation, timeout, cancellation, and bounded queue;
- retry owner, retryable errors, backoff, limit, and idempotency;
- partial failure and duplicate-delivery behavior;
- circuit breaking, admission control, load shedding, and degraded mode;
- fallback model, provider, tool, server, source, region, or human path and its
  quality and data policy;
- checkpoint, resume, replay, compensation, and reconciliation;
- task and workflow ownership after disconnect or restart;
- state corruption, stale cache, index mismatch, catalog mismatch, and artifact
  mismatch;
- health, readiness, safe terminal states, and operator action;
- backup, restore, rebuild, disaster recovery, and tested recovery objectives.

Do not hide a failure by silently switching provider, model, tool, or protocol
path. Make fallback observable, policy-controlled, and separately evaluated.

## Release, Rollout, And Rollback

Bind a release to exact versions of:

- application, agent framework, workflow, and source;
- provider adapters, profiles, gateway routes, and model aliases or revisions;
- model, adapter, tokenizer, template, and quantization;
- prompts, Skills, repository instructions, tools, schemas, and policies;
- MCP core, SDKs, framework, servers, Apps, Tasks, hosts, and gateway;
- A2A, AG-UI, or ACP components where selected;
- data snapshot, embedding, index, graph, reranker, and memory schema;
- runtime, server, router, infrastructure, container, and configuration;
- evaluation artifacts, conformance results, approvals, and rollback target.

Use the least risky applicable method:

- offline qualification;
- shadow traffic with privacy and side-effect controls;
- canary by tenant, provider, host, workload, or traffic percentage;
- A/B test with predeclared metrics and guardrails;
- gradual capacity, model, server, or gateway rollout;
- explicit rollback trigger and tested rollback procedure.

Model, provider, protocol, embedding, index, prompt, skill, tool, and gateway
changes can be data, behavioral, or interoperability migrations. Define
compatibility windows, dual-read or dual-run when justified, reconciliation,
and retirement.

## Cost And Capacity

Measure cost per accepted outcome, including:

- provider input, output, image, audio, tool, storage, and batch charges;
- gateway, MCP server, workflow, database, cache, and observability overhead;
- accelerator, CPU, memory, network, storage, idle, warm, and reserve capacity;
- data processing, embedding, indexing, reranking, training, checkpointing, and
  registry storage;
- retries, failures, evaluation, conformance, logging, tracing, and human review;
- engineering, platform, security, and on-call ownership when comparing managed
  and self-hosted options.

Capacity planning should use prompt and output distributions, concurrency,
arrival bursts, model and provider mix, tools, task duration, App traffic, cache
behavior, startup time, failure reserve, and growth. Average utilization alone
is insufficient.

Set budgets and alerts for per-request, per-agent-run, per-task, per-tenant,
per-provider, per-run, and total spend. Bound agent steps and tool use before
production.

## Production Learning Loop

Use production evidence to improve the system without contaminating evaluation
or violating privacy:

1. Capture governed outcomes, failures, corrections, and representative traces.
2. Classify failures by model, provider, adapter, retrieval, tool, MCP, A2A,
   workflow, data, runtime, policy, gateway, or application boundary.
3. Add approved examples to versioned evaluation sets with provenance.
4. Preserve a hidden holdout and monitor evaluator drift.
5. Change the narrowest responsible component.
6. Re-run required quality, safety, portability, and conformance gates.
7. Roll out gradually and monitor both improvement and regressions.
8. Retire obsolete models, adapters, providers, indexes, prompts, skills, tools,
   servers, protocol revisions, data, and telemetry safely.

Do not automatically train on raw user interactions, model outputs, memory,
production traces, task results, or MCP content without explicit rights,
filtering, governance, and quality controls.
