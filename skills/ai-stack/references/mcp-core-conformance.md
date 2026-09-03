# MCP Core And Conformance

Use this reference to choose and qualify MCP core, SDK, framework, transport,
authorization, host, and deployment behavior. Read
`mcp-apps-tasks-gateway.md` when Apps, Tasks, or a shared gateway is in scope.

Baseline date: 2026-08-07

## Contents

- Version model
- Current production profile
- Core architecture
- SDK and framework boundary
- Transport and deployment
- Authorization and tenancy
- Server and client design
- Conformance suite
- Migration and release record
- Official sources

## Version Model

Do not call MCP "V2." Track these independently:

- MCP core date-based protocol revision.
- Client SDK package and exact version.
- Server SDK package and exact version.
- Productivity framework and exact version.
- Transport and authorization profile.
- Extension identifier and exact revision.
- Host, server, gateway, and compatibility-matrix versions.

A FastMCP major is a framework version, not an MCP protocol revision.

## Current Production Profile

Use this as the starting profile, then verify every selected package and host:

```yaml
mcp:
  core_revision: "2026-07-28"
  client_sdk: "<official-tier-1-sdk>@<exact-version>"
  server_sdk: "<official-tier-1-sdk>@<exact-version>"
  framework: "<none-or-fastmcp>@<exact-version>"
  transport: "stdio | streamable-http"
  authorization_profile: "<exact-profile>"
  host: "<product>@<exact-version>"
  gateway: "<none-or-product>@<exact-version>"
  extensions: {}
```

Use an official Tier 1 SDK as the protocol and conformance authority. Use
FastMCP as a qualified Python productivity layer only after its exact release
passes the selected official client and server paths.

## Core Architecture

MCP core revision `2026-07-28` is stateless at the protocol layer. It removes
the required initialization exchange and protocol session ID and introduces
self-describing requests, optional discovery, routing headers, cacheable list
results, authorization hardening, Multi Round-Trip Requests, and a formal
extension framework.

Stateless core does not make the application stateless. Put application or
workflow state behind explicit authenticated handles and normal storage. Do not
recreate a hidden protocol session ID in a framework wrapper.

The host owns:

- user identity, consent, approvals, and capability visibility;
- client lifecycle, server trust, routing, and UI isolation;
- secret injection and downstream credential policy;
- tenant binding, audit, cancellation, and user-facing errors.

The server owns:

- tool, resource, and prompt schemas and implementations;
- domain authorization and tenant-safe data access;
- idempotency, side effects, deadlines, and output validation;
- its state, storage, scaling, recovery, and lifecycle.

The model owns neither identity nor authorization.

## SDK And Framework Boundary

Use the official Tier 1 TypeScript, Python, Go, or C# SDK path as the baseline
for:

- protocol messages and serialization;
- discovery and capability negotiation;
- transports and routing headers;
- cache behavior and errors;
- authorization and trace propagation;
- migration and deprecation behavior;
- extension negotiation.

Use FastMCP when typed Python tools, resources, prompts, deterministic clients,
local `stdio`, remote Streamable HTTP, or test harnesses materially reduce code.
Keep application logic and policy outside framework decorators so another SDK
path can exercise the same domain behavior.

Do not infer protocol support from imports or a package major. Pin the exact
release and record implemented core revision, extensions, transport, and known
host limitations.

## Transport And Deployment

### Local

Use `stdio` when host and server share a local machine or process boundary.
Keep stdout protocol-only, send diagnostics to stderr, propagate cancellation,
and define child-process lifetime and exit behavior.

### Remote

Use Streamable HTTP when the server is independently deployed, reused by
multiple clients, or requires normal service scaling and policy. Define:

- TLS and network boundary;
- protected-resource discovery and authorization;
- routing-header ownership and validation;
- load balancing and any application handle affinity;
- timeouts, request limits, backpressure, and cancellation;
- readiness, graceful shutdown, upgrade, and rollback;
- structured logs, metrics, and trace propagation.

Do not place every private function behind a remote MCP server. Prefer a typed
function or ordinary internal API until independent reusable ownership is real.

## Authorization And Tenancy

For remote user-delegated access:

- bind access tokens to the intended protected resource and audience;
- validate issuer, signature, audience, expiry, scopes, and tenant context;
- use least-privilege scopes and short-lived credentials;
- keep trusted approval UI host-owned;
- reject identity, tenant, policy, or credential fields supplied by the model.

For service-to-service access, prefer workload identity and explicit service
authorization. Do not forward an upstream bearer token to downstream services
unless an explicit token-exchange and policy design requires it.

Authorize at the domain boundary before reading data or performing a side
effect. Partition caches, application state, task handles, resources, traces,
and rate limits by the authoritative tenant and caller identity.

Never expose provider keys or downstream credentials to prompts, tool
arguments, model-visible errors, browser code, logs, or reusable Skill files.

## Server And Client Design

Keep schemas narrow and stable:

- use JSON Schema for shape and domain validation for semantics;
- use deterministic names and descriptions;
- separate read-only and consequential capabilities;
- make side effects explicit and idempotent where possible;
- return stable structured errors without secrets or internal traces;
- cap payloads and provide resource links for large immutable content;
- encode output for its consumer and treat retrieved content as untrusted.

Client behavior must define discovery caching, invalidation, schema change,
timeouts, cancellation, retry classes, error normalization, trace context, and
host approval. Do not retry non-idempotent tools automatically.

## Conformance Suite

Release requires interoperability with an official Tier 1 client and server
path. Test:

- self-describing requests and optional discovery;
- tools, resources, prompts, list pagination, and cache validators;
- local and remote transport behavior as selected;
- routing headers and rejection of invalid routing data;
- authorization success and failure, resource/audience binding, and tenancy;
- JSON Schema translation and domain validation;
- errors, timeouts, cancellation, payload limits, and disconnects;
- trace propagation and credential redaction;
- framework-to-official-SDK interoperability;
- upgrade, rollback, and selected previous-revision behavior only when the user
  explicitly requires legacy support.

Use the same domain fixtures through the official SDK and productivity
framework path. A framework-only test is insufficient.

## Migration And Release Record

Revalidate when any core revision, SDK, framework, host, gateway,
authorization profile, tool/resource schema, cache policy, or identity policy
changes.

Keep one complete record:

```text
core revision
client SDK and exact version
server SDK and exact version
framework and exact version
transport
authorization profile
extensions and exact revisions
host and exact version
gateway and exact version
conformance suite revision and result
```

When the user explicitly requires an older core revision, bounded compatibility
must be an explicit adapter with a removal trigger. Do not silently downgrade
or maintain dual semantics.

## Official Sources

- [MCP latest specification](https://modelcontextprotocol.io/specification/latest)
- [MCP 2026-07-28 release](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
- [MCP SDK overview](https://modelcontextprotocol.io/docs/sdk)
- [MCP SDK tiers](https://modelcontextprotocol.io/community/sdk-tiers)
- [MCP conformance repository](https://github.com/modelcontextprotocol/conformance)
- [MCP transports](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports)
- [MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
- [FastMCP](https://gofastmcp.com/)
- [OAuth Resource Indicators](https://www.rfc-editor.org/rfc/rfc8707)
- [OAuth Protected Resource Metadata](https://www.rfc-editor.org/rfc/rfc9728)
