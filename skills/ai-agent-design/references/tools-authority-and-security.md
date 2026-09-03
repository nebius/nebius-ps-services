# Tools, Authority, and Security

Use this reference when an agent can retrieve protected data, call tools,
execute code, contact remote agents, or cause side effects.

## Contents

- Tool contract
- Action-risk policy
- Approval binding
- Tool and protocol design
- Credential mediation
- Sandbox and artifact boundary
- Trust boundaries
- Prompt-injection controls
- Security review

## Tool Contract

Declare every capability with a narrow, versioned contract:

```text
name, version, owner, and purpose
input and output JSON Schema
authenticated caller and tenant source
required scopes and resource boundaries
read/write class and action-risk tier
semantic validation and policy checks
idempotency and deduplication behavior
deadline, cancellation, rate, and cost limits
approval, preview, commit, and verification paths
error taxonomy and retry ownership
trace, provenance, and audit fields
```

Prefer domain operations such as `create_draft_invoice` over generic shell,
SQL, URL fetch, filesystem, or cloud-admin tools. Split read, propose, mutate,
and verify capabilities when they have different authority or risk.

Schema validation checks shape, not permission or business semantics. The tool
boundary must authenticate, authorize, validate invariants, enforce tenant and
resource scope, and return typed errors outside model discretion.

Keep decision ownership singular. The policy and approval gateway owns the
action-policy decision and approval validity. The control plane coordinates
that decision and the session store persists it. A tool or domain boundary
performs resource-specific authorization and deterministically enforces the
immutable decision; it may narrow or deny execution but never broaden,
reapprove, or reinterpret the approved action.

## Action-Risk Policy

Use a risk scale that the product can map to its own policy:

| Tier | Typical effect | Default control |
| --- | --- | --- |
| R0 | Pure reasoning or local formatting | Bounded execution |
| R1 | Read public or already-authorized low-sensitivity data | Audit and rate limits |
| R2 | Read sensitive data or create reversible private drafts | Strong identity, scope, audit, optional review |
| R3 | Reversible mutation with bounded impact | Preview plus explicit approval or constrained policy grant |
| R4 | Material external, financial, availability, access, or publication effect | Exact human approval, idempotency, verification, rollback/reconciliation |
| R5 | Irreversible, destructive, credential, IAM, safety-critical, or broad-impact action | Default deny; action-specific authority and independent safeguards |

Risk depends on target, data, authority, reversibility, blast radius, cost, and
environment. A nominally read-only action may be high risk when it exposes
sensitive data or changes criterion-relevant state through access side effects.

Define the maximum tier per agent, task, user, environment, and tool. The model
may propose an action but never self-elevate its tier or authority.

## Approval Binding

Bind approval to the exact intent:

```text
requesting principal and tenant
authenticated approver identity and authentication evidence
approver authorization or policy decision
decision timestamp and separation-of-duties status
tool and contract version
action and normalized arguments
resource and environment
before-state or version precondition
expected effects and risk tier
expiry and one-use/reuse policy
idempotency key or action digest
```

Show the approver a preview with material effects and alternatives. Before
commit, reauthenticate and reauthorize the approver whenever the action-risk
policy requires it; require both for R4 actions. Revalidate separation of
duties, current state, tool version, and action digest. Material drift
invalidates the approval and starts a new proposal; do not silently adjust or
reuse it.

Approval is not execution. The approval record must prove who decided, how that
identity was authenticated, why that identity was authorized, when the decision
occurred, and whether requester/approver separation was required and met. After
commit, verify authoritative postconditions independently and retain a
tamper-evident audit record.

## Preview, Commit, Verify

For consequential actions prefer:

```text
plan or proposal
  -> deterministic validation and authorization
  -> bounded preview or dry run
  -> exact approval
  -> idempotent commit
  -> authoritative verification
  -> completion, compensation, or reconciliation
```

Do not let a preview endpoint mutate criterion-relevant state. Do not claim
success from the tool response alone when an authoritative read can verify the
result.

## Tool Design Rules

- Give tools one purpose and the minimum input surface.
- Use enums and resource identifiers instead of free-form commands and paths.
- Validate semantic constraints after schema validation.
- Inject credentials server-side; never ask the model to select or emit them.
- Propagate verified identity and tenant context through trusted channels.
- Apply deadlines, cancellation, quotas, and output-size limits.
- Sanitize returned content and label it as untrusted observation.
- Use stable error classes such as invalid, unauthorized, forbidden, conflict,
  rate-limited, transient, permanent, unknown, and partial-effect.
- Assign one retry owner per failure class.
- Make mutation idempotency explicit; if impossible, require reconciliation.
- Log bounded metadata and digests, not secrets or unrestricted payloads.

## Credential Mediation

Keep reusable raw credentials out of model-visible context, generated code,
tool results, logs, and artifacts. Execute authorized capabilities through a
trusted credential broker or tool gateway that provides:

- delegation bound to the verified user or workload principal and tenant;
- least-privilege resource, action, audience, destination, and duration scope;
- short-lived credentials minted or retrieved only at execution time;
- revocation and rotation propagation to active sessions;
- non-disclosure in errors, traces, files, callbacks, and tool results; and
- an audit link from proposal and policy decision to broker identity, target,
  scope, effect receipt, and observed postcondition.

Credential invisibility prevents one theft path; it does not prevent a confused
deputy, an over-broad delegated action, or exfiltration through a legitimate
API. Reauthorize every action at the trusted boundary.

## Sandbox And Artifact Boundary

When the agent can execute code or manipulate files, define proportionate:

- ephemeral per-session or per-workload isolation and tenant separation;
- non-root execution, patched runtime, minimal pinned dependencies, scanning,
  and reproducible image identity;
- CPU, memory, disk, process, runtime, and network budgets;
- deny-by-default egress with explicit destinations and observable mediation;
- controlled mounts, read-only inputs where possible, path validation, and no
  reusable secret material in the filesystem;
- package and tool allowlists, integrity verification, and supply-chain policy;
- cancellation, cleanup, retention, and incident-preservation behavior; and
- artifact type, path, size, malware, secret, and policy validation before a
  trusted publication service exports the result.

Treat sandbox output as untrusted. A managed sandbox is an execution-isolation
component, not proof of authorization, tenancy, durability, data governance,
evaluation, or production readiness.

## MCP and Remote-Agent Policy

Use direct typed functions first, ordinary authenticated APIs across normal
service boundaries, MCP for independently deployed reusable capabilities, and
remote-agent protocols only for independently owned task-level agents.

MCP schemas and discovery improve interoperability; they do not provide the
application's authorization, tenant isolation, consent, context selection,
idempotency, or business-policy boundary. The host and server must still
enforce identity, audience, scopes, transport security, consent, and audit.

Do not pass through tokens issued for another audience. Prefer short-lived,
audience-bound credentials and explicit user or workload identity. Pin and
qualify the protocol, SDK, transport, extension, server, and host combination
through `ai-stack`.

Treat remote agents as untrusted peers. Bound their task, data, tools, deadline,
budget, output schema, and authority at the protocol boundary.

## Trust Boundaries

Model these boundaries explicitly:

- user/client to application;
- application to model provider;
- application to retrieval and data sources;
- agent runtime to tools, sandboxes, and code execution;
- host to MCP server or remote agent;
- orchestration to workers;
- runtime to canonical state, audit, and secret stores;
- evaluation and observability pipelines to production data.
- sandbox package sources and artifact publication to trusted consumers.

At each boundary define authentication, authorization, data classification,
validation, encryption, retention, audit, failure behavior, and owner.

## Prompt-Injection Policy

Assume direct and indirect prompt injection will occur. Apply defense in depth:

- keep system policy and untrusted content structurally distinct;
- authorize sources before retrieval and label trust/provenance;
- never treat retrieved instructions as higher-priority policy;
- minimize tools, context, credentials, network, and filesystem access;
- validate model-selected tools and arguments deterministically;
- require approval for consequential effects;
- sandbox code and constrain filesystem, process, network, CPU, memory, time,
  and credentials;
- filter or escape content only as an additional control, not the authority;
- detect unusual tool sequences, exfiltration attempts, and policy violations;
- test cross-tenant access, confused deputy, SSRF, data exfiltration, secret
  leakage, and sandbox escape assumptions.

Model-based guardrails may classify or detect suspicious content, but they must
not be the final authorization or policy enforcement layer.

## Security Review

- Are identity and tenant derived from a trusted channel?
- Is each tool least-privilege and narrowly typed?
- Can the model create arbitrary code, SQL, URLs, paths, or credentials?
- Are actions risk-classified and approval-bound to exact state?
- Are credentials server-side, scoped, rotated, and absent from prompts/traces?
- Are delegated credentials short-lived, audience- and destination-bound, and
  revoked when the principal or approval changes?
- Are retrieval ACLs enforced before ranking?
- Are tools, MCP servers, remote agents, and content treated as untrusted?
- Are sandbox and egress controls proportional to the capability?
- Are dependencies and exported artifacts validated at a trusted boundary?
- Can duplicate or replayed calls repeat an effect?
- Are security events observable without leaking sensitive content?

## Primary Sources

- [OWASP AI Agent Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html)
- [MCP authorization security considerations](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations)
- [MCP server concepts](https://modelcontextprotocol.io/docs/2026-07-28/learn/server-concepts)
- [OpenAI guardrails and approvals](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals)
