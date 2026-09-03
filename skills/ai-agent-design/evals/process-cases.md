# Supplemental Process Cases

These cases preserve workflow, routing, and manual runtime expectations.
`trigger-prompts.csv` is the sole canonical trigger authority; this document
does not define skill routing.

Static validation of these files does not prove runtime activation or output
quality.

## Core Process Assertions

- Every substantive design starts by freezing a workload contract and
  classifying each AI-enabled capability as deterministic code, a direct model
  call, a deterministic AI workflow, or an agent.
- The workflow rejects agentic control when a simpler class passes
  representative acceptance gates.
- A deterministic-only result is terminal for AI selection. It records
  `Skipped - no AI component required` instead of invoking `ai-stack`.
- One bounded application-level inference operation remains a direct model
  call across bounded transport or schema-repair retries when application code
  owns continuation and no tools or effects are introduced.
- One bounded agent is the topology baseline. Deterministic routing,
  agents-as-tools, parallel workers, orchestrator-workers, remote agents, and
  choreography require named acceptance, authority, isolation, ownership, or
  deployment triggers.
- Model autonomy, graph topology, durable execution, eventing, and deployment
  boundaries are decided independently.
- Authentication, authorization, tenant filtering, approvals, effect
  execution, state transitions, and postcondition verification remain
  deterministic and outside model discretion.
- Context authorization occurs before retrieval relevance; retrieved content,
  tool output, MCP servers, and remote agents remain untrusted.
- Task, conversation, working state, checkpoint, durable workflow state,
  governed memory, external knowledge, audit, and cache have separate owners.
- Effects define action risk, exact approval binding, idempotency, duplicate
  delivery, unknown outcome, compensation, and reconciliation semantics.
- Every effect-capable action persists its authorized immutable intent, action
  digest, and idempotency identity before dispatch, then records its receipt
  and independent verification after dispatch.
- Every production design covers representative trajectory and end-to-end
  evaluation, release identity, privacy-aware traces, promotion, rollback,
  incidents, and owners.
- Accepted agent designs separate trusted control and policy, model-directed
  execution, and durable session state behind versioned provider-neutral
  interfaces; a runtime product never erases those application obligations.
- The agent loop loads authoritative state, assembles bounded authorized
  context, obtains one structured decision, authorizes and executes eligible
  actions, checkpoints effects, verifies outcomes independently, and
  terminates or escalates within budgets.
- Long-running work exposes verified progress, versioned steering,
  cancellation, approvals, durable notifications, effect reconciliation, and
  cleanup.
- Context compaction remains recoverable from canonical state, and memory
  writes remain candidate data until validated, scoped, versioned, evaluated,
  and promoted through a governed policy.
- Hidden credentials do not grant safety by themselves; delegated actions use
  short-lived destination-bound authority, and sandbox output crosses a trusted
  validation and publication boundary.
- A completion claim is not proof. Deterministic verification takes priority,
  semantic grading is independent and bounded, and no grader can waive policy,
  authorization, schema, privacy, test, or safety gates.

## AI Stack Handoff Assertions

- `ai-agent-design` freezes behavior, authority, policy, workload, and
  acceptance constraints before using `ai-stack` when AI technology selection
  remains.
- The handoff includes outcome, behavior class, acceptance gates, failure
  impact, data and tenant constraints, autonomy ceiling, tool inventory,
  durability, quality, latency, availability, recovery, cost, owners, fixed
  decisions, assumptions, and blockers.
- The result incorporates `ai-stack`'s technology, component status, evidence
  state, owner, acceptance gate, and revisit trigger without duplicating its
  vendor-selection guidance.
- A deterministic-only result skips `ai-stack` and records that no AI component
  is required.
- An active `design` workflow receives the scoped agent-subsystem result once;
  neither skill recursively re-enters the other.
- A direct `ai-stack` request with a disputed provisional classification may
  delegate that contract decision to `ai-agent-design`; this skill returns the
  frozen contract to the originating `ai-stack` and skips its own nested stack
  handoff, so component selection executes exactly once.

## Routing Assertions

- Positive rows cover agent-specific architecture, control patterns, policies,
  contracts, production readiness, or material redesign.
- Stack-only selection routes to `ai-stack`.
- Whole-product architecture routes to `design` and `app-stack`.
- One volatile technology question routes to `research`.
- Checklist-only review routes to `system-design-rules`.
- Implementation routes to the relevant project skill; physical topology
  materialization routes to the owning scaffold or project workflow.
- Open-ended ideation routes to `brainstorm`, unknown persistent failures to
  `troubleshoot`, and implementation-diff review to `code-review`.

## Quality Case 1: Governed Support Workflow

The accepted design should classify the fixed retrieve, draft, approval, send,
and verify sequence as a deterministic AI workflow. It may introduce one
bounded agent only if the workload establishes a real need for model-owned tool
choice or continuation. Sending is a separate high-risk effect with
deterministic authorization, exact preview and approval, idempotent commit,
authoritative verification, and audit. It should not introduce multi-agent
topology, long-term memory, MCP, or a durable workflow engine without additional
requirements.

The approval record must bind authenticated approver identity, authentication
evidence, approver authorization, decision time, separation-of-duties status,
exact action arguments and preview, state preconditions, expiry, policy
version, and one-use replay protection.

## Quality Case 2: Durable Orchestrator-Workers

The accepted design should use one orchestrator for dynamic decomposition and
synthesis, bounded parallel workers with frozen task contracts and distinct
least-privilege context, durable canonical task state, append-only transition
history, optimistic concurrency, at-least-once delivery, idempotent consumers,
deterministic merge/conflict policy, cancellation, budget limits, and recovery
from crash, duplicate delivery, provider failure, and partial results.

The session contract should include an explicit state machine, versioned event
and effect history, lease or heartbeat ownership, effect-aware checkpoints,
backpressure, progress and cancellation controls, durable notifications, and
cleanup. Resume must reconcile in-flight work before reassignment.

## Quality Case 3: One Bounded Diagnostic Agent

The accepted design should use one bounded read-only agent because the model
must choose the next diagnostic tool from prior observations. It should reject
multi-agent coordination, keep identity and authorization deterministic,
define typed tool and observation contracts, enforce step/time/cost budgets,
and end in explicit solved, inconclusive, blocked, or escalated states. It
should return an evidence bundle, track competing hypotheses and falsification,
use calibrated causal language, and treat incomplete or contradictory
telemetry as a blocker. Any remediation is a separate deterministic,
explicitly authorized workflow. The `ai-stack` handoff selects components
without reopening the frozen single-agent topology.

## Quality Case 4: Direct-Call Lower Bound

The accepted design should classify one bounded extraction or classification
operation as a direct model call with deterministic input and output
validation. Bounded transport or schema-repair retries do not change that
classification while application code owns continuation and no tools or
effects are introduced. It should reject tools, memory, an agent runtime,
multi-agent coordination, and a durable workflow engine. The `ai-stack`
handoff is limited to the model API, structured-output support, evaluation,
and observability components justified by the workload.

## Quality Case 5: Controlled Effect And Ambiguous Delivery

The accepted design should separate trusted control, sandbox or tool execution,
and durable session/effect state. It should bind approval to one immutable
staging action, mediate short-lived destination-bound credentials, isolate code
execution, validate exported artifacts, persist an authorized immutable effect
intent and idempotency identity before dispatch, then persist the receipt and
verification. A crash or lost response after dispatch but before receipt
persistence must reconcile authoritative state before retry. The design should
also expose verified progress and cancellation and independently verify the
postcondition.

## Quality Case 6: Governed Memory Curation

The accepted design should justify memory against a no-memory baseline, keep
session, working state, knowledge, preferences, verified facts, audit, and
cache separate, and treat memory writes as governed proposals. Offline curation
uses a frozen input snapshot, separate candidate store, provenance-preserving
diff, conflict quarantine, privacy and retrieval-impact evaluation, reviewed
promotion, correction, deletion, expiry, and rollback. It must not claim the
model improved merely because memory changed.

## Quality Case 7: Deterministic-Code Lower Bound

The accepted design should classify a fixed, fully specified rules-based
capability as deterministic code even when the request initially proposes AI.
It should define ordinary validation, tests, ownership, and failure handling;
reject model, agent, retrieval, memory, and runtime components; and record
`Skipped - no AI component required` instead of invoking `ai-stack`. The
capability map still lists all four behavior classes and explains why the
deterministic baseline passes the acceptance gates.

## Quality Case 8: Originating AI-Stack Contract Return

The accepted output should assume an active `ai-stack` workflow delegated one
disputed provisional behavior or policy decision. `ai-agent-design` freezes
that contract, returns only the decision to the originating workflow, performs
no nested `ai-stack` handoff, and does not synthesize or reopen component
selection. The originating `ai-stack` remains the single owner that selects
components exactly once.

## Manual Runtime Check

After installation, exercise all positive and negative canonical rows in a
fresh Codex surface. Confirm that the skill loads for agent-subsystem design
and declines adjacent stack-only, whole-product, research, checklist,
implementation, brainstorming, troubleshooting, review, and scaffolding work.

Report `RUNTIME_PASS` only when the fresh surface actually loads or declines
the skill as expected. Otherwise report metadata and static readiness only.
