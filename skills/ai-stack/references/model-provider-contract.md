# Model Provider Contract

Use this reference to make agent applications portable across OpenAI, Anthropic,
Nebius Token Factory, self-hosted endpoints, and future providers without
reducing every provider to an unreliable least-common-denominator API.

Baseline date: 2026-08-07

## Contents

- Design objective
- Layered provider architecture
- Domain contract
- Configuration and secret handling
- Capability registry
- Request, response, and stream contract
- Provider profiles
- Routing and fallback
- Conformance suite
- Security and tenancy
- Operations and evaluation
- Official sources

## Design Objective

Build portability at the application boundary, not by pretending that all APIs
behave identically. Separate:

- Application semantics: Messages, content blocks, tools, schemas, budgets,
  deadlines, and trace context.
- Provider adapter: Translate the domain contract to a native provider SDK or
  documented compatible endpoint.
- Provider profile: Declare exact capabilities and known behavioral constraints
  for one model and endpoint.
- Gateway policy: Inject credentials, route, rate-limit, observe, and apply
  policy when a shared platform boundary is justified.
- Evaluation: Prove the exact provider, model, adapter, and feature combination.

Use native APIs when they preserve required features better. Use an
OpenAI-compatible endpoint only after its exact compatibility profile passes the
application conformance suite.

Do not require this full contract for one intentionally selected provider when
a narrow application-local seam around its native SDK is sufficient. Adopt the
capability-aware contract when the system must support multiple providers,
routing, evaluated fallback, self-hosted endpoints, regional or data-policy
selection, or a shared model platform. The abstraction must remove real domain
coupling; it must not become speculative infrastructure.

## Layered Provider Architecture

```text
Agent or deterministic application
  -> internal ModelProvider interface
  -> capability and policy resolver
  -> native provider adapter or compatible-protocol adapter
  -> optional agentgateway
  -> OpenAI, Anthropic, Nebius Token Factory, or self-hosted model endpoint
```

Keep framework types behind the adapter. Domain code must not depend on
provider-specific message, tool-call, streaming, or error classes.

Use a shared gateway when the organization needs centralized credentials,
provider routing, budgets, policy, tenancy, rate limits, and telemetry. Allow a
direct adapter for development and for workloads where the gateway adds no
material value.

## Domain Contract

Use a small internal interface. Keep provider extensions explicit rather than
leaking SDK objects throughout the application.

```python
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Protocol


@dataclass(frozen=True)
class ModelTarget:
    provider: str
    model: str
    endpoint_profile: str
    revision: str | None = None
    region: str | None = None


@dataclass(frozen=True)
class ModelCapabilities:
    streaming: bool
    cancellation: bool
    tool_calling: bool
    parallel_tool_calls: bool
    strict_json_schema: bool
    accepted_json_schema_profile: str | None
    multimodal_inputs: frozenset[str]
    multimodal_outputs: frozenset[str]
    reasoning_controls: bool
    prompt_caching: bool
    citations: bool
    batch: bool
    native_mcp: bool
    maximum_context_tokens: int | None
    maximum_output_tokens: int | None
    residency_regions: frozenset[str]
    retention_profiles: frozenset[str]


@dataclass(frozen=True)
class ContentBlock:
    kind: Literal[
        "text", "image", "audio", "document", "tool_call", "tool_result",
        "reasoning", "citation", "refusal"
    ]
    value: Any
    media_type: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelMessage:
    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: tuple[ContentBlock, ...]
    name: str | None = None


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any] | None
    risk_class: str


@dataclass(frozen=True)
class ModelRequest:
    target: ModelTarget
    messages: tuple[ModelMessage, ...]
    tools: tuple[ToolDefinition, ...] = ()
    response_schema: Mapping[str, Any] | None = None
    tool_choice: str | None = None
    temperature: float | None = None
    maximum_output_tokens: int | None = None
    deadline_ms: int | None = None
    tenant_id: str | None = None
    data_policy_class: str | None = None
    operation_id: str | None = None
    trace_context: Mapping[str, str] = field(default_factory=dict)
    provider_options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass(frozen=True)
class ModelResponse:
    content: tuple[ContentBlock, ...]
    finish_reason: str
    usage: Usage
    provider_request_id: str | None
    resolved_target: ModelTarget
    safety_metadata: Mapping[str, Any] = field(default_factory=dict)
    provider_extensions: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelEvent:
    kind: str
    sequence: int
    data: Any


class ModelProvider(Protocol):
    async def capabilities(self, target: ModelTarget) -> ModelCapabilities: ...
    async def generate(self, request: ModelRequest) -> ModelResponse: ...
    def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]: ...
```

Treat this as a semantic sketch. Adapt names and types to the repository, but
preserve the separation between target identity, capabilities, normalized
content, provider extensions, and trace context.

## Configuration And Secret Handling

Represent credentials as secret references, not strings in application config:

```yaml
providers:
  openai-prod:
    kind: openai
    protocol: responses
    base_url: https://api.openai.com/v1
    credential_ref: secret://ai/providers/openai-prod

  anthropic-prod:
    kind: anthropic
    protocol: messages
    base_url: https://api.anthropic.com
    credential_ref: secret://ai/providers/anthropic-prod

  nebius-token-factory:
    kind: nebius
    protocol: openai-chat-completions
    base_url: https://api.tokenfactory.nebius.com/v1
    credential_ref: secret://ai/providers/nebius-token-factory
```

Require:

- Secret manager injection at process or gateway boundary.
- Short-lived workload identity or rotated API keys where supported.
- Separate credentials by environment, tenant class, and privilege.
- No provider key in prompts, tool arguments, traces, Skills, repositories, or
  browser-visible MCP Apps.
- Explicit egress allowlists for provider endpoints.
- Audit of credential use without logging credential material.

A pluggable API key is necessary but insufficient. The adapter must also know
the protocol shape, endpoint, model identity, capabilities, rate limits, error
model, data region, and retention policy.

## Capability Registry

Maintain capabilities per exact tuple:

```text
provider
endpoint profile
region
model ID
model revision or immutable alias
adapter version
protocol mode
```

Record at least:

- text, image, audio, video, and document input support;
- output modalities;
- streaming event semantics and cancellation;
- function or tool calling and parallel calls;
- strict JSON Schema behavior and accepted schema subset;
- tool-result and multi-turn formatting;
- context and output limits;
- reasoning controls and visibility;
- prompt or context caching;
- usage and cost fields;
- safety and refusal metadata;
- batch, fine-tuning, or dedicated-endpoint support;
- native MCP or hosted-tool support;
- data residency, retention, and zero-retention eligibility;
- rate limits, concurrency, timeouts, and retry guidance.

Populate documented capabilities from official sources, then override them only
with measured target-environment evidence. Treat unknown as unsupported until a
bounded test proves otherwise.

## Request, Response, And Stream Contract

Normalize stable semantics, but preserve meaningful provider differences.

### Messages And Content

Use typed content blocks rather than concatenating every modality into text.
Preserve the original role and content order. Translate system and developer
roles only through an adapter with tested semantics.

### Structured Output

Use a strict application schema and validate again after the provider response.
Do not assume a provider that accepts a `response_format` field enforces the
same JSON Schema subset or failure behavior as another provider.

### Tools

Use one canonical tool schema and create provider adapters for names, argument
encoding, tool choice, parallel calls, streamed arguments, and result messages.
Keep authorization outside the provider and model.

### Streaming

Normalize stream events for:

- response start and completion;
- text and reasoning deltas;
- tool-call start, argument deltas, and completion;
- usage updates;
- citations, refusals, and safety events;
- errors and cancellation.

Do not infer success from a closed connection. Require an explicit terminal
event or classified error.

### Errors

Map native errors into a stable taxonomy:

```text
AUTHENTICATION
AUTHORIZATION
INVALID_REQUEST
UNSUPPORTED_CAPABILITY
MODEL_NOT_FOUND
RATE_LIMITED
CAPACITY_UNAVAILABLE
TIMEOUT
CANCELLED
CONTENT_REJECTED
CONTEXT_EXCEEDED
PROVIDER_INTERNAL
NETWORK
UNKNOWN
```

Preserve provider status, code, request ID, retry hints, and safe redacted detail
in extensions. Retry only errors classified as transient and within the
operation deadline and retry budget.

## Provider Profiles

### OpenAI

Use the official OpenAI SDK and Responses API for OpenAI text or reasoning
generation supported by Responses. Use the documented task-specific API for
embeddings, transcription, realtime speech, and other direct workloads outside
that capability. Preserve native tool, structured output, multimodal,
reasoning, usage, and tracing semantics required by the application.

Use the OpenAI Agents SDK when the whole agent architecture is intentionally
OpenAI-first and its abstractions fit. Do not make its model classes the
organization-wide domain contract.

### Anthropic

Use the official Anthropic SDK and Messages API for the generic provider
adapter. Use Claude Agent SDK for Claude-native coding, filesystem, sandbox,
hook, subagent, MCP, Skill, and session workflows where its opinionated agent
loop is the desired product behavior.

Treat Claude Managed Agents as a separate hosted execution profile with its own
lifecycle, data, session, sandbox, and beta-status review. Do not treat it as a
portable implementation of the generic agent kernel.

### Nebius Token Factory

Use the official OpenAI-compatible endpoint profile, or a framework adapter
whose current official documentation explicitly supports Nebius. Configure base
URL, API key reference, and exact model ID independently.

Do not assume every Token Factory model supports the same tool calling,
structured output, context length, tokenizer behavior, or multimodal features.
Maintain a capability record and conformance result per exact model and model
tag.

Use dedicated endpoints when isolated capacity, region, GPU configuration,
autoscaling, or custom weights justify the additional deployment contract.

### Self-Hosted OpenAI-Compatible Endpoints

Use vLLM, SGLang, TensorRT-LLM, or another engine behind a compatible adapter
only for the operations the endpoint has passed. OpenAI-compatible text
completion does not prove compatible tool calling, strict JSON, streaming,
usage, multimodal input, error handling, or cancellation.

### Additional Framework Adapters

Allow Pydantic AI, LangChain, Microsoft Agent Framework, Google ADK, Strands,
or another framework to implement the internal provider interface. Do not make
framework installation equivalent to provider conformance.

## Routing And Fallback

Route only on explicit policy and capabilities. Define:

- allowed providers and models per workload and data class;
- tenant, region, residency, and retention constraints;
- minimum capabilities and quality threshold;
- cost and latency budget;
- capacity and rate-limit policy;
- fallback order and maximum attempts;
- semantic compatibility between primary and fallback;
- audit and evaluation identity.

Do not silently fall back between models with different safety, tool, schema,
context, or data-handling behavior. Surface the resolved model and provider in
telemetry and durable state.

Use a gateway for centralized routing, but keep application-level acceptance
rules in a versioned policy artifact. Gateway availability does not justify
arbitrary fallback.

## Conformance Suite

Run the suite for every provider, exact model, adapter version, endpoint profile,
and required feature:

- Basic generation: Roles, Unicode, stop behavior, token limits, and finish
  reasons.
- Streaming: Event order, partial text, tool arguments, usage, terminal event,
  cancellation, and disconnect.
- Tools: No call, one call, parallel calls, malformed arguments, tool result,
  refusal, and multi-turn continuation.
- Structured output: Valid schema, edge values, unsupported keywords, invalid
  model output, and repair policy.
- Multimodal: Every required media type, size, ordering, and unsupported input.
- Context: Maximum practical input, truncation policy, and context-exceeded
  classification.
- Errors: Authentication, rate limit, timeout, capacity, invalid model, safety,
  and provider failure.
- Accounting: Model identity, request ID, input and output tokens, cache fields,
  cost attribution, and trace correlation.
- Safety and data: Refusal metadata, region, logging, retention, and redaction.
- Framework integration: Checkpoint, resume, replay, retry, and tool idempotency
  behavior.

Block promotion when a required capability is unknown or fails. Do not accept a
framework demo or provider marketing example as conformance evidence.

## Security And Tenancy

- Derive tenant and caller from verified application or workload identity.
- Do not allow model output to select arbitrary provider endpoints or credentials.
- Validate base URLs against an allowlist to prevent SSRF and credential theft.
- Apply data classification before routing to a provider.
- Redact or tokenize sensitive fields only through an approved reversible or
  irreversible policy.
- Partition rate limits, budgets, caches, traces, and provider credentials by
  tenant or workload where required.
- Treat provider responses, citations, and tool calls as untrusted input.
- Keep raw prompts and responses out of telemetry unless policy explicitly
  permits them.

## Operations And Evaluation

Include these dimensions in the evaluation identity:

```text
application revision
agent framework and version
provider adapter and version
endpoint profile
provider
model ID and revision
capability-profile revision
prompt and tool-schema digest
policy revision
runtime and region
```

Measure task success, structured-output validity, tool correctness, safety,
latency percentiles, streaming quality, cancellation, token accounting, cost per
accepted outcome, provider errors, fallback rate, and trace completeness.

Revalidate when the provider changes an API version, model alias, tool schema,
stream event shape, safety policy, rate limit, endpoint, SDK major version, or
data-handling terms.

## Official Sources

- OpenAI API models and Responses: <https://developers.openai.com/api/docs/>
- OpenAI Agents SDK models: <https://openai.github.io/openai-agents-python/models/>
- Anthropic API: <https://docs.anthropic.com/en/api/>
- Claude Agent SDK: <https://platform.claude.com/docs/en/agent-sdk/overview>
- Nebius Token Factory API: <https://docs.nebius.com/studio/inference/quickstart>
- Nebius Token Factory function calling: <https://docs.nebius.com/studio/inference/function-calling>
- Pydantic AI model and provider abstractions: <https://ai.pydantic.dev/models/overview/>
- agentgateway: <https://agentgateway.dev/>
