# Contracts, Context, and Memory

Use this reference to define what enters, controls, and leaves an agent run.
Keep task truth, context assembly, working state, long-term memory, knowledge,
and audit history separate.

## Contents

- Task contract
- Agent definition contract
- Context pipeline
- Provenance and conflict handling
- Context lifecycle
- Derivation lineage and revocation
- Bounded artifact access
- Cache eligibility
- Memory classes
- Governed memory writes and curation
- Handoff contracts

## Task Contract

Give every run or delegated task a typed envelope. Use only fields needed by
the workload, but make the following decisions explicit:

```text
task_id and parent/correlation identifiers
task type and contract version
objective and observable success criteria
authorized principal, tenant, and policy context
input artifacts and immutable provenance
allowed capabilities and action-risk ceiling
quality, safety, latency, step, token, cost, and retry budgets
deadline, cancellation, and approval state
required output and evidence schema
idempotency and deduplication identity
current release identity
```

The objective states the desired outcome, not a hidden implementation. Success
criteria must be independently checkable. Preserve the original authorized
scope when decomposing tasks; workers may narrow it but never expand it.

## Agent Definition Contract

Version an agent as a governed release unit rather than a prompt string. Record:

```text
logical agent name and version
owned capabilities and non-goals
input, output, error, and evidence schemas
instruction and policy identities
model route and runtime identity from ai-stack
tool allowlist, scopes, and risk ceiling
context-source policy and token budgets
working-state and memory policies
termination and escalation conditions
evaluation suite and release gates
operational owner and rollback identity
```

Keep provider session identifiers and caches as replaceable optimizations. Do
not make them the canonical task, approval, or business-state authority.

## Context Pipeline

Build context through deterministic stages:

```text
authenticated principal and task
  -> authorize sources and documents
  -> retrieve from authoritative sources
  -> validate type, provenance, freshness, and integrity
  -> reconcile duplicates and conflicts
  -> rank by task relevance
  -> allocate explicit token budgets
  -> assemble labeled context
  -> record included and excluded provenance
```

Authorization precedes search, embedding similarity, reranking, or model
selection. A relevant document is not an authorized document. Apply tenant and
document filters at the trusted data boundary, not as prompt advice.

Label context by trust and purpose, for example:

- system and policy instructions;
- user objective and constraints;
- authoritative task or business state;
- retrieved external knowledge;
- prior conversation;
- selected governed memory;
- tool observations;
- untrusted content quoted for analysis.

Keep instructions structurally separate from data. Retrieved text and tool
output never gain instruction priority because they resemble a directive.

## Provenance, Freshness, and Conflict

For every decision-changing context item, carry:

```text
source identifier and owner
record or artifact version
retrieval or observation time
effective and expiry time when applicable
authorization decision identity
integrity or content digest where useful
trust class and transformation history
```

Prefer current authoritative business state over conversational summaries or
semantic memory. Define conflict policy before retrieval:

1. reject or quarantine unauthorized or integrity-failed data;
2. prefer the named authority for the field or decision;
3. compare versions and effective times;
4. retain conflicting evidence when the authority is unresolved;
5. abstain, request clarification, or route to an owner rather than silently
   blending incompatible facts.

Do not hide unresolved conflicts through summarization.

## Context Budgeting

Allocate budgets by class instead of filling the context window opportunistically:

```text
instructions and policies
task and current state
retrieved evidence
conversation continuity
working observations
reserved response/tool budget
```

Classify content as mandatory or optional before trimming. Mandatory content
includes current scope and restrictions, relevant approval/effect status,
unresolved failures and contradictions, and protocol-required tool-call/result
groups. Select optional evidence by value within the remaining budget.

Measure the complete serialized request, including instructions, tool schemas,
message wrappers, tool results, and reserved output, using a qualified tokenizer
or conservative estimator for the selected route. Budget for the smallest
eligible fallback route too, or rebuild and revalidate before fallback.
Recheck after transformations at the final dispatch boundary. If mandatory
content cannot fit, stop, request a narrower task, or partition through a
trusted policy; never silently truncate a restriction to make the call fit.

Prefer compact structured records, bounded evidence excerpts, and references to
large artifacts. Preserve the data needed for audit or recovery outside the
prompt; context compaction is not archival storage.

## Context Lifecycle

- Rebuild context from canonical sources for each decision; do not treat a
  provider thread or compacted transcript as authoritative task state.
- Make compaction recoverable by retaining event and artifact references that
  can reload decision-changing evidence.
- Optimize compaction for constraint recall before token reduction. Evaluate
  whether required scope, approvals, failures, and unresolved conflicts survive.
- Replace stale large tool output with a verified summary plus a reference only
  after later steps no longer require the original payload.
- Separate stable instructions and tool definitions from rapidly changing state
  when an evaluated runtime cache can benefit; caching is an optimization, not
  authority.
- Treat any retrieval, ranking, compaction, or context-layout change as a
  release change and evaluate its effect on behavior, privacy, latency, and cost.

## Derivation Lineage And Revocation

Treat summaries, embeddings, notes, memory candidates, cached answers, and
opaque provider continuations as derived data. Record the full set of protected
inputs supplied to their producer, including uncited inputs and earlier
derivatives. Track this dependency closure outside model-generated citations;
a model cannot attest which inputs did or did not influence its output.

Before reuse, authorize the current principal, purpose, and destination against
that closure and current policy. The default audience is no broader than the
inputs permit. Removing a citation, paraphrasing, hashing an identifier, or
passing a content filter does not authorize broader disclosure. Any deliberate
declassification needs a separate, explicit policy and trusted owner.

If access is revoked or lineage is unavailable, invalidate the derivative and
rebuild from currently eligible sources. If an opaque continuation cannot
exclude affected history, discard it and restart from a clean authorized view.
Bound invalidation propagation and define the serving behavior during that
window; unknown authority must not permit protected output.

A summary remains untrusted derived data. Preserve source trust labels,
conflicts, restrictions, and reloadable evidence; do not install a model-written
summary as higher-priority instructions. Validate compaction over repeated
cycles against an uncompacted baseline, including constraint recall, revoked
uncited inputs, stale facts, and coherent tool protocol records.

## Bounded Artifact Access

When a tool result is too large, store it under the approved retention policy
before model delivery and return a small typed descriptor: identity, immutable
version or digest, classification, lineage, expiry, coverage, and read limits.
The descriptor is a locator, never an access grant or a credential-bearing URL.

Reauthorize every read, search, or page against current access and derivation
dependencies. Bound bytes/tokens returned, pages, underlying scan work, time,
and total task cost. Preserve truncation, omitted ranges, partial coverage, and
unavailable-source status so a short result cannot imply exhaustive evidence.
If authorized offload fails, return a typed failure or explicitly partial
result; do not fall back to exposing the oversized protected payload.

## Cache Eligibility

Treat a cache hit as a candidate result, not an authority decision. Choose keys
and entry metadata from the workload's correctness dependencies: principal and
tenant scope, task/purpose, source and corpus generations, policy, transformation
and release identities, dependency closure, effective time, and expiry.

- Recheck current authorization and validity before materializing protected
  data, even when the key includes an authorization revision.
- Invalidate query results when the eligible corpus changes, including a newly
  eligible record absent from the previous result. Tracking only returned rows
  cannot establish query freshness.
- Bound validity by scheduled effective-time changes as well as TTL; a record
  can become eligible or expire without an invalidation event.
- Treat lost invalidation, eviction, restore, and cache outage as expected
  conditions. Reconcile against the authority or rebuild. If that authority is
  unavailable, block protected serving rather than treating stale entries as
  a grant. Define separately whether non-sensitive stale data may be served.
- Never cache a reusable approval, lease, or permission as if it were current
  authority; canonical stores and current policy own those decisions.

These are conditional correctness requirements, not a requirement to add a
cache, lineage service, or vector database.

## Memory Classes

Keep these physically or logically distinct:

| Class | Purpose | Authority | Typical lifetime |
| --- | --- | --- | --- |
| Request context | One operation | Caller/task contract | Request |
| Conversation history | Interaction continuity | Application | Session/retention policy |
| Agent working state | Plans, observations, intermediate results | Run owner | Run |
| Execution checkpoint | Exact graph/workflow progress | Execution owner | Workflow |
| Long-term memory | Selected reusable facts or preferences | Governed memory owner | Policy-defined |
| External knowledge | Source-of-truth domain data | Source system | Source-defined |
| Audit history | Actions, approvals, versions, outcomes | Audit owner | Compliance policy |
| Cache | Performance optimization | Cache owner | Evictable |

Long-term memory is optional. Add it only when repeated tasks demonstrate value
that current task state, conversation continuity, or authoritative retrieval
cannot provide.

For every memory write define:

- eligible fact types and forbidden content;
- source, consent, tenant, and subject;
- validation and conflict policy;
- retention, expiry, deletion, correction, and export;
- sensitivity, encryption, access, and audit;
- retrieval criteria and evaluation;
- whether a human or deterministic rule must approve promotion.

Do not store raw chain-of-thought, unrestricted transcripts, secrets, or
unverified model conclusions as durable memory. Prefer selected facts with
provenance and confidence.

## Governed Memory Writes And Curation

Treat an agent memory write as a proposal, not an immediate fact:

```text
candidate -> validate and classify -> deduplicate and detect conflict
  -> approve, quarantine, or reject -> versioned write
  -> evaluate retrieval and behavior impact -> promote or roll back
```

Bind every retained item to scope, type, provenance, confidence derived from
source quality, effective and expiry time, sensitivity, allowed consumers,
immutable version history, and review state. Keep proposed, verified,
quarantined, superseded, expired, and rejected states distinct.

For offline curation:

1. Freeze the input sessions, artifacts, and memory snapshot.
2. Produce a separate candidate store, never an in-place rewrite.
3. Generate a provenance-preserving diff for additions, removals, merges, and
   conflicts.
4. Run retrieval, poisoning, leakage, and behavior regression evaluations on
   current and candidate stores.
5. Require the applicable human or policy owner for high-impact shared facts.
6. Promote gradually with a recorded switch and retain a tested rollback.

Call this memory curation, consolidation, or promotion. Do not describe it as
model self-improvement when model weights and the governing product contract
did not change.

## Handoff Contracts

Use typed handoffs for agent-to-agent or agent-to-workflow boundaries:

```text
task and contract version
delegator and worker identity
authorized scope and denied scope
input evidence and provenance
expected output and evidence schema
budget and deadline
tool and data boundary
error and retry classification
cancellation and partial-result semantics
```

The receiver must be able to accept, reject, or return a structured blocker.
Do not transfer hidden conversational authority, shared mutable scratch state,
or credentials implicitly.

## Review Checklist

- Does each task have an independently testable completion condition?
- Is context authorized before relevance ranking?
- Are instructions distinct from untrusted data?
- Can every decision-changing fact be traced to a source and version?
- Does derived-data eligibility include uncited and transitive inputs?
- Can revocation invalidate summaries, caches, and provider continuations?
- Does the complete request fit without losing mandatory context?
- Are artifact reads bounded and reauthorized, and cache freshness sensitive
  to corpus changes and time?
- Are conflicts surfaced rather than averaged away?
- Are canonical state, conversation, working state, memory, knowledge, audit,
  and cache owned separately?
- Can memory be corrected, expired, deleted, and evaluated?
- Do handoffs preserve scope, authority, evidence, and cancellation?

## Primary Sources

The authorization and context principles below are supported by these public
sources. Complete dependency closure, final-request budgeting, and cache
eligibility are conservative design rules derived from those principles;
they are not claims that a framework supplies these controls automatically.

- [Anthropic, Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
  supports deliberate context selection and recall-focused compaction.
- [OWASP Authorization Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)
  supports deny-by-default access and permission checks on every request.

- [NIST AI Risk Management Framework](https://www.nist.gov/itl/ai-risk-management-framework)
- [OWASP AI Agent Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html)
