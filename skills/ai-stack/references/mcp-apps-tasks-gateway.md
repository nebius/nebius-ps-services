# MCP Apps, Tasks, And Gateway

Use this reference only when MCP needs capability-owned embedded UI, a
protocol-visible durable operation handle, or a shared traffic and policy
plane. Read `mcp-core-conformance.md` first.

Baseline date: 2026-08-07

## Contents

- Extension profile
- MCP Apps
- MCP Tasks
- Durable execution boundary
- Shared gateway
- Security and conformance
- Official sources

## Extension Profile

Extensions version independently from MCP core and require negotiation. Record:

```yaml
extensions:
  io.modelcontextprotocol/ui:
    revision: "2026-01-26"
    lifecycle: "Stable"
  io.modelcontextprotocol/tasks:
    revision: "<exact-selected-revision>"
    lifecycle: "Draft"
```

MCP Apps revision `2026-01-26` is Stable. Host support remains product- and
version-specific, so qualify every host. At the baseline date, MCP Tasks is
Draft; keep adoption `Conditional` and make accepted lifecycle risk explicit.

## MCP Apps

Use MCP Apps when the interactive UI belongs to one MCP capability. Continue
using the normal product UI for navigation, authentication, major workflows,
global accessibility, and broad application state.

An App combines:

```text
MCP tool
  + ui:// resource
  + sandboxed iframe
  + JSON-RPC bridge
  + host-mediated tool access
```

Appropriate uses include interactive charts, forms, dashboards, canvases,
previews, and inspectors owned by the capability.

### App Contract

Record:

- tool and input/output schemas;
- `ui://` resource identity, content digest, version, and cache policy;
- bridge methods and directions;
- host capabilities and fallback behavior;
- network, storage, clipboard, download, and navigation policy;
- tenant and caller binding;
- accessibility and text fallback;
- approval points and host-owned confirmation UI.

### Required Controls

- Sandboxed iframe isolation.
- Strict Content Security Policy.
- Explicit bridge-method allowlist.
- Host-mediated tool access and trusted approval UI.
- Context-appropriate output encoding.
- Tenant-bound data and cache partitioning.
- Restricted network and browser storage.
- Resource integrity and versioning.
- Text fallback and accessibility tests.
- Per-host compatibility tests.

Treat browser content, resource metadata, bridge messages, tool results, and
model output as untrusted. Never place provider or downstream credentials in
the iframe. Generative UI that executes model-written code remains
`Conditional` behind stronger sandbox, provenance, dependency, network, and
supply-chain controls.

## MCP Tasks

Use MCP Tasks when an operation cannot complete within the interactive request
deadline and clients need a protocol-visible handle for observation or control.
Current lifecycle operations include `tasks/get`, `tasks/update`, and
`tasks/cancel`; verify them against the exact selected Draft revision.

### Task Start Pattern

```text
tools/call
  -> authenticate and authorize caller
  -> validate input and policy
  -> derive idempotency identity
  -> start or attach to durable execution
  -> persist tenant, caller, owner, retention, and trace identity
  -> return an opaque task handle
```

Every operation on a task must validate:

- authenticated caller and tenant;
- current authorization and task ownership;
- handle authenticity and non-enumerability;
- state transition and optimistic-concurrency rules;
- expiry and retention;
- cancellation and compensation policy;
- result visibility and deletion policy;
- trace and audit identity.

Do not encode tenant, user, or provider secrets in the handle. Do not assume a
caller remains authorized because it created the task.

## Durable Execution Boundary

An MCP Task is a protocol-visible handle, not a workflow engine. Back it with
one durable owner, such as:

- Temporal for cross-service workflows, timers, approvals, compensation, and
  process/infrastructure recovery;
- a Kubernetes Job for bounded cluster-owned batch execution;
- a queue worker for idempotent asynchronous work;
- a provider batch API for provider-owned batch processing;
- an explicit database state machine for a small bounded lifecycle.

Assign retries, deadlines, idempotency, cancellation, compensation, and state
to one owner. The MCP layer translates protocol operations to that owner; it
does not independently recreate workflow state.

Define terminal states, result publication, partial failure, orphan recovery,
worker restart, caller disconnect, cancellation races, and task deletion.

## Shared Gateway

Treat `agentgateway` as shared platform infrastructure, not an application
framework and not a mandatory component inside every service.

Use it when several independently owned applications, providers, MCP servers,
A2A agents, HTTP services, or gRPC services would otherwise duplicate:

- authentication and authorization;
- routing and provider policy;
- rate, quota, budget, and tenant control;
- tool and model policy;
- observability and audit;
- federation and shared lifecycle control.

Keep it `Deferred` for one application, one provider, and private capabilities
unless another platform requirement justifies the control plane.

The gateway must not become the source of application business state or hide
provider and protocol compatibility. Keep application-level authorization at
the domain boundary and preserve end-to-end caller and trace identity.

Record route ownership, policy source, credential injection, tenant binding,
failure and bypass behavior, rate limits, upgrade, rollback, capacity, and
operational owner.

## Security And Conformance

### Apps Suite

Test:

- extension negotiation and unsupported-host fallback;
- resource integrity, caching, and version change;
- iframe sandbox and CSP enforcement;
- bridge allowlist and malformed/untrusted messages;
- host-owned approvals and forbidden direct tool access;
- tenant isolation, network/storage restrictions, and credential absence;
- encoding, accessibility, keyboard use, text fallback, and host compatibility.

### Tasks Suite

Test:

- start/attach idempotency and opaque handle properties;
- every selected lifecycle operation and invalid transition;
- caller, tenant, ownership, expiry, and authorization changes;
- cancellation before, during, and after durable side effects;
- retry, worker restart, disconnect, orphan recovery, and terminal publication;
- retention, deletion, audit, trace, and result visibility;
- behavior when the client, gateway, server, and durable owner run different
  qualified versions.

### Gateway Suite

Test:

- identity propagation and tenant isolation;
- model, tool, route, budget, and rate policy;
- credential injection without prompt, response, App, or trace leakage;
- upstream/downstream timeout, retry, cancellation, and overload semantics;
- trace continuity and stable audit events;
- policy/config rollout, rollback, gateway outage, and approved bypass behavior;
- exact MCP, A2A, HTTP, gRPC, and provider compatibility in use.

Block release when a required host or durable owner cannot pass the exact
selected extension contract.

## Official Sources

- [MCP Apps overview](https://apps.extensions.modelcontextprotocol.io/)
- [MCP Apps specification](https://apps.extensions.modelcontextprotocol.io/specification/)
- [Official MCP Apps package](https://github.com/modelcontextprotocol/ext-apps)
- [MCP Apps SEP](https://modelcontextprotocol.io/seps/1865-mcp-apps-interactive-user-interfaces-for-mcp)
- [MCP Tasks overview](https://tasks.extensions.modelcontextprotocol.io/)
- [MCP Tasks Draft specification](https://tasks.extensions.modelcontextprotocol.io/specification/draft/tasks)
- [MCP Tasks SEP](https://modelcontextprotocol.io/seps/2663-tasks-extension)
- [FastMCP Apps](https://gofastmcp.com/apps/overview)
- [agentgateway](https://agentgateway.dev/)
- [agentgateway MCP integration](https://agentgateway.dev/docs/local/main/integrations/mcp-servers/)
- [Temporal documentation](https://docs.temporal.io/)
