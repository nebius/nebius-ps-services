# Open Agentic Standards And AAIF

Use this reference to distinguish open standards, interoperability protocols,
repository instructions, reusable skills, gateways, and complete agent
frameworks. Do not treat these layers as interchangeable.

Baseline date: 2026-08-07

## Contents

- AAIF role and project map
- Protocol and artifact taxonomy
- Selection rules
- Versioning and negotiation
- Recommended status by capability
- Interoperability boundaries
- Supply-chain and governance controls
- Official sources

## AAIF Role And Project Map

Treat the Agentic AI Foundation as a vendor-neutral governance and ecosystem
layer, not as one application framework. As of the baseline date, AAIF hosts:

- Model Context Protocol: Standardize agent-to-tool, agent-to-data, and
  host-to-server context integration.
- AGENTS.md: Provide repository-scoped instructions for coding agents.
- goose: Provide an open, local-first, model-agnostic developer agent runtime.
- agentgateway: Provide a shared gateway and policy plane for LLM, MCP, A2A,
  HTTP, gRPC, and related agent traffic.

Use AAIF projects as composable infrastructure:

```text
AGENTS.md and Agent Skills
  -> guide an agent

MCP
  -> connect an agent host to tools, resources, prompts, apps, and tasks

A2A
  -> connect independently deployed agents

agentgateway
  -> route, secure, observe, and govern agent, model, MCP, and API traffic

Goose
  -> run a local or distributed developer agent that consumes models and MCP
```

Do not select AAIF itself as the agent runtime. Select an application framework,
provider SDK, or local runtime separately.

## Protocol And Artifact Taxonomy

### Direct Typed Function Or API

Use a direct function or typed API when one application owns both sides, the
interface is private, the lifecycle is shared, and discovery adds no material
value. Prefer this before adding a protocol boundary.

### Model Context Protocol

Use MCP for reusable, discoverable tools and context that must work across
multiple agent hosts or have an independent deployment lifecycle. Treat MCP as
an integration protocol, not as an agent reasoning loop, memory system, or
workflow engine.

MCP exposes several capability families:

- Tools: Model-invoked operations with declared input and output schemas.
- Resources: Addressable context or artifacts retrieved through URIs.
- Prompts: Server-provided prompt templates, subject to host policy.
- Apps extension: Sandboxed interactive UI resources associated with tools.
- Tasks extension: Durable protocol handles for long-running tool execution.
- Elicitation and other negotiated capabilities: Host-mediated interaction that
  must be checked against the exact protocol and SDK revision.

### Agent Skills

Use Agent Skills for reusable procedural knowledge, instructions, scripts,
references, and assets that are loaded progressively by a compatible agent
runtime. Treat a Skill as a capability package for the agent, not as a remote
service protocol.

Use Skills for:

- organization-specific workflows;
- repeatable design or operational playbooks;
- scripts that make fragile steps deterministic;
- reference material that should load only when relevant;
- output templates and reusable assets.

Do not put credentials, mutable production state, or an uncontrolled executable
supply chain inside Skills. Sign, review, version, and allowlist enterprise
Skills.

### AGENTS.md

Use AGENTS.md for repository-local instructions to coding agents. Put build,
test, architectural, style, security, and directory-specific constraints there.
Keep it versioned with the repository.

Do not use AGENTS.md as a general runtime memory store or as a replacement for
application authorization.

### Agent-to-Agent Protocol

Current baseline: Use A2A v1.0 as the stable production protocol line. Pin the
requested `A2A-Version`, validate the remote Agent Card and declared
capabilities, and run the official technology compatibility tests where they
cover the selected transport and SDK. Do not silently fall back to an older
protocol version when required semantics would be lost.

Use A2A when independently deployed agents must discover each other, advertise
capabilities, exchange tasks or results, and retain implementation opacity.
Treat A2A as complementary to MCP:

- MCP: Agent or host to tool, data, prompt, resource, or application capability.
- A2A: Agent service to agent service.

Prefer agents-as-tools, ordinary APIs, or deterministic workflows before A2A.
Add A2A only when deployment ownership, trust boundaries, independent scaling,
or cross-organization interoperability justify a remote agent boundary.

### Agent Client Protocol

Use ACP for coding-agent integration with editors, terminals, or other coding
clients. Do not use ACP as a general service-to-service agent protocol.

### Agent User Interaction Protocol

Use AG-UI when several agent runtimes must drive a common frontend through a
standard event stream, including messages, tool events, state, interrupts, and
human interaction.

Use stable MCP Apps revision `2026-01-26`, extension identifier
`io.modelcontextprotocol/ui`, when an MCP tool itself needs to render an inline,
sandboxed, interactive UI. Negotiate exact host support. The two can coexist:

- AG-UI: Agent runtime to application frontend.
- MCP Apps: MCP server capability to host-rendered inline UI.

### agentgateway

Use agentgateway as a shared platform gateway when multiple applications,
providers, MCP servers, or remote agents need consistent routing, credentials,
identity, authorization, rate limits, policy, and OpenTelemetry correlation.

Treat it as Conditional for one small application with one provider and a few
private tools. Treat it as Required for a shared production platform only after
its exact release passes the required protocol conformance and interoperability
tests.

Do not assume that AAIF governance proves that every release supports the latest
MCP core or every extension. Verify the exact agentgateway version against the
selected MCP, A2A, and provider features.

### goose

Use goose as a local-first or organization-customized developer agent when the
requirements favor model choice, MCP reuse, local execution, repository work,
and an open runtime. Use it as a reference host in MCP interoperability tests.

Do not select goose as the application framework for an unrelated production
service merely because it is an AAIF project.

## Selection Rules

Apply this sequence:

```text
private deterministic operation
  -> direct typed function or API

reusable procedure for an agent
  -> Agent Skill

repository guidance for coding agents
  -> AGENTS.md

reusable tool or context server
  -> MCP

inline UI owned by an MCP capability
  -> MCP Apps

long-running MCP operation
  -> MCP Tasks backed by a workflow or job system

agent runtime to reusable application frontend
  -> AG-UI

coding agent to editor or terminal client
  -> ACP

independently deployed agent service
  -> A2A

shared traffic policy and routing
  -> agentgateway
```

Reject protocol multiplication when one stable boundary is sufficient. Every
additional protocol requires ownership for versions, authentication,
authorization, conformance, observability, upgrades, and incident response.

## Versioning And Negotiation

Do not call the current protocol "MCP v2." MCP core revisions are identified by
dates. As of the baseline date, use `2026-07-28` as the current production
revision unless a required client or server has a documented compatibility
constraint.

Distinguish these version spaces:

- MCP core revision: Date-based protocol contract, such as `2026-07-28`.
- MCP extension revision: Independently versioned Apps, Tasks, or another
  extension negotiated by capability.
- Official SDK major version: Package release line, not the protocol name.
- FastMCP major version: Framework package line, not the protocol name.
- Host capability version: Product-specific support that can lag the protocol.
- A2A protocol version: Semantic version, with v1.0 as the stable baseline.
  Pin `A2A-Version` and required Agent Card capabilities independently of MCP.

For `2026-07-28`, design for the stateless core:

- Do not require connection-scoped initialization or a protocol session ID.
- Include version, client identity, and capabilities in request metadata as
  required by the selected specification.
- Use discovery and extension negotiation before invoking optional features.
- Route and meter on protocol headers where supported by the exact revision.
- Respect cache metadata for list and resource results.
- Treat Apps and Tasks as extensions rather than unconditional core behavior.

Maintain a bounded compatibility path only when the user explicitly requires
an older selected host or client:

```text
current protocol adapter
  + optional previous-revision adapter
  + explicit sunset date
  + conformance suite for both
```

Do not build new production services on deprecated legacy HTTP plus SSE
transport semantics.

## Recommended Status By Capability

Use these defaults as of the baseline date:

| Capability | Status | Default | Acceptance condition |
| --- | --- | --- | --- |
| Repository agent instructions | Conditional | AGENTS.md | Coding agents operate in the repository and consume the intended nearest-scope instructions |
| Reusable procedural capability | Conditional | Agent Skills | The runtime supports Skills, reuse exists, and the package is reviewed, versioned, bounded, and reproducible |
| Local reusable tool boundary | Conditional | MCP over `stdio` | Interoperability value exceeds process and supply-chain cost |
| Remote reusable tool boundary | Conditional | MCP Streamable HTTP | Identity, policy, tenancy, conformance, cancellation, and rollout tests pass |
| Inline MCP UI | Conditional | MCP Apps | Host support, sandboxing, fallback content, accessibility, and policy tests pass |
| Long-running MCP call | Conditional | MCP Tasks | Task handle maps safely to durable execution, cancellation, and retention |
| Shared agent frontend protocol | Conditional | AG-UI | Multiple runtimes or frontends justify the event contract |
| Coding editor protocol | Conditional | ACP | The product is an editor or coding-client integration |
| Remote agent interoperability | Conditional | A2A v1.0 | Independent agent ownership and opacity are material requirements, and version, Agent Card, identity, task, artifact, and cancellation tests pass |
| Shared agent traffic gateway | Conditional | agentgateway | Platform-scale shared routing and policy is required, and the exact release passes target protocol, policy, tenancy, and performance gates |
| Open local developer agent | Conditional | goose | Local-first and model-agnostic behavior meets the use case |
| Skills transported as an MCP core feature | Deferred | None | Adopt only after a stable, interoperable specification exists |
| Public registry as enterprise trust root | Rejected | Internal curated registry | Public metadata alone cannot establish organizational trust |

## Interoperability Boundaries

Define one contract per edge:

- Agent runtime to model provider: Use `model-provider-contract.md`.
- Agent runtime to MCP client: Pin core revision, SDK, extensions, transport,
  identity, cancellation, and trace behavior.
- MCP host to MCP App: Pin Apps extension, content type, sandbox, CSP, allowed
  bridge methods, fallback output, and host feature support.
- MCP server to durable job: Map task ID, operation ID, tenant, idempotency key,
  workflow ID, status, retention, cancellation, and result location.
- Agent to remote agent: Pin A2A profile, identity, advertised capabilities,
  task semantics, deadlines, artifacts, and error taxonomy.
- Agent runtime to frontend: Pin AG-UI event version, ordering, replay,
  backpressure, state ownership, interrupt, and redaction behavior.
- Coding agent to repository: Define AGENTS.md scope and Skill resolution order.
- Gateway to every protocol: Verify pass-through or mediation semantics without
  silently changing identity, schemas, streaming, cancellation, or trace IDs.

## Supply-Chain And Governance Controls

Apply these controls to Skills, MCP servers, agent packages, registries, and
remote agents:

- Curate: Maintain an approved internal catalog with owner and purpose.
- Pin: Record package, image, source revision, protocol revision, and artifact
  digest.
- Verify: Require signatures, provenance, SBOM, vulnerability scanning, and
  license review where applicable.
- Isolate: Run untrusted local servers, code tools, and generated UI in bounded
  sandboxes with minimal filesystem, network, and credential access.
- Authorize: Derive caller, tenant, and resource scope from verified identity,
  never from model-generated parameters.
- Observe: Correlate host, agent, gateway, MCP, job, model, and downstream traces.
- Revoke: Support immediate disablement of a server, Skill, tool, model, agent,
  provider key, or registry entry.
- Retire: Publish deprecation dates and test migrations before removing a
  protocol revision or extension.

## Official Sources

Revalidate before each production adoption:

- AAIF projects: <https://aaif.io/projects/>
- AAIF formation and governance: <https://aaif.io/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation-aaif-anchored-by-new-project-contributions-including-model-context-protocol-mcp-goose-and-agents-md/>
- agentgateway: <https://agentgateway.dev/>
- MCP specification: <https://modelcontextprotocol.io/specification/latest>
- MCP SDK tiers: <https://modelcontextprotocol.io/community/sdk-tiers>
- MCP Apps extension: <https://github.com/modelcontextprotocol/ext-apps>
- MCP Tasks SEP: <https://modelcontextprotocol.io/seps/2663-tasks-extension>
- A2A v1.0: <https://a2a-protocol.org/latest/>
- A2A v1.0 announcement: <https://a2a-protocol.org/latest/announcing-1.0/>
- A2A specification: <https://a2a-protocol.org/latest/specification/>
- Agent Skills: <https://agentskills.io/specification>
- AGENTS.md: <https://agents.md/>
- ACP: <https://agentclientprotocol.com/>
- AG-UI: <https://docs.ag-ui.com/>
- goose: <https://block.github.io/goose/>
