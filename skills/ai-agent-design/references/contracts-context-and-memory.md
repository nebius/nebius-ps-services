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
- Are conflicts surfaced rather than averaged away?
- Are canonical state, conversation, working state, memory, knowledge, audit,
  and cache owned separately?
- Can memory be corrected, expired, deleted, and evaluated?
- Do handoffs preserve scope, authority, evidence, and cancellation?

## Primary Sources

- [NIST AI Risk Management Framework](https://www.nist.gov/itl/ai-risk-management-framework)
- [OWASP AI Agent Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html)
