# Durability, Failures, and Effects

Use this reference when a run is long-lived, concurrent, replayed,
multi-agent, externally effectful, or expected to survive process failure.

## Contents

- State ownership
- Runtime state machine
- History and projections
- Concurrency and delivery
- Failure taxonomy
- Retry and isolation policy
- Compensation and reconciliation

## State Ownership

Separate state classes and give each one authority:

| State | Owner | Requirement |
| --- | --- | --- |
| Business/task truth | Application domain | Authoritative, transactional, schema-versioned |
| Agent working state | Run owner | Bounded and disposable after retention |
| Graph checkpoint | Graph runtime | Exact graph continuation only |
| Durable workflow state | Workflow owner | Timers, retries, approvals, and recovery |
| Conversation state | Application | User continuity and retention |
| Long-term memory | Memory owner | Selected governed facts only |
| Artifact state | Artifact store | Immutable identity, lineage, and access |
| Audit history | Audit owner | Append-only action and approval evidence |
| Cache | Cache owner | Rebuildable and never canonical |

Do not make a provider session, prompt transcript, vector index, or cache the
canonical source of task or approval truth.

## Runtime State Machine

Define an explicit bounded state machine. Adapt names to the domain, but cover:

```text
accepted -> planning -> ready
ready -> needs-approval -> ready-to-execute -> executing -> verifying
ready -> executing  # only when no approval is required
verifying -> succeeded | failed | needs-owner
any active state -> cancelled | expired | failed | needs-owner
```

Specify allowed transitions, transition owner, preconditions, side effects,
postconditions, timeouts, resumable blockers, and safe terminal states. Approval
precedes consequential execution; verification follows the effect. A model may
propose a transition, but deterministic application or workflow code validates
and commits it.

Pause and approval states must persist the exact action proposal, release
identity, state version, expiry, and continuation point. Resume revalidates all
of them rather than trusting conversational continuity.

## History and Materialized Views

For recoverable workflows, prefer an append-only transition or event history
plus a materialized current-state view:

```text
command with expected version
  -> validate policy and transition
  -> atomically append transition/effect intent
  -> update or rebuild current projection
  -> publish through outbox when external delivery is required
```

History provides causality and audit; the projection supports efficient reads.
Do not claim atomic publication to an external broker with an ordinary database
commit. Use an outbox or equivalent relay and idempotent consumers.

## Concurrency Rules

- Give every task, attempt, action, event, artifact, and worker stable identity.
- Use a lease and heartbeat when multiple workers could claim one session;
  stale lease recovery must reconcile in-flight effects before reassignment.
- Use optimistic concurrency or another explicit ownership mechanism for
  shared task truth.
- Reject or rebase stale expected versions; do not let last-write-wins hide
  conflicting decisions.
- Freeze worker inputs and authority at dispatch.
- Avoid shared mutable model scratch state between workers.
- Bound fan-out, queue depth, concurrent model calls, and external effects.
- Propagate deadlines and cancellation to model calls, tools, and workers.
- Bound tenant concurrency, queue depth, provider demand, and notification
  fan-out; use backpressure rather than unbounded admission.
- Define deterministic merge, arbitration, or conflict escalation.
- Treat cancellation as a state transition with effect cleanup and partial
  result policy, not merely a client disconnect.

## Delivery and Idempotency

Assume at-least-once execution whenever queues, retries, workflow replay,
network timeouts, or process crashes can duplicate work.

For each effect define:

```text
idempotency key and scope
normalized request identity
deduplication retention window
stored outcome or authoritative lookup
concurrent duplicate behavior
late-arrival behavior
partial-effect detection
reconciliation path
```

Make the key express the caller's intended operation, not a random retry
attempt. A repeated key with materially different arguments is a conflict.

If the external system has no idempotency contract, wrap it with a domain-owned
effect record, unique constraint, verification, and reconciliation. Do not
blindly retry an unknown outcome.

## Failure Taxonomy

Classify before retrying:

| Class | Examples | Default response |
| --- | --- | --- |
| Invalid | Schema or invariant failure | Fix input; no retry |
| Unauthorized/forbidden | Missing identity, scope, approval | Stop and route to authority |
| Conflict/stale | Version or resource state changed | Refresh, replan, or request decision |
| Rate/overload | Quota, throttling, saturation | Bounded backoff and admission control |
| Transient dependency | Timeout, temporary outage | Bounded retry by one owner |
| Permanent dependency | Unsupported feature, terminal provider error | Fail or use evaluated alternative |
| Budget exhausted | Steps, tokens, time, cost, fan-out | Stop safely and report partial evidence |
| Policy/security | Injection, exfiltration, unsafe action | Deny, contain, audit, escalate |
| Partial effect | Some external work committed | Verify and compensate/reconcile |
| Unknown outcome | Timeout after submission | Read authoritative state before retry |
| Model quality | Wrong plan, tool, argument, or answer | Evaluate, replan within budget, or escalate |

Model-quality retries are not infrastructure retries. Provider fallback is a
new evaluated route, not an automatic replay of a side effect.

## Retry Policy

- Assign one retry owner per failure class.
- Retry only failures shown to be transient or safely idempotent.
- Use bounded attempts, exponential backoff, jitter, deadlines, and retry
  budgets.
- Honor server retry guidance when compatible with the deadline.
- Keep non-deterministic model and external calls outside deterministic
  workflow replay code; execute them in activities or equivalent boundaries.
- Persist exact model, prompt, tool, policy, and idempotency identities needed
  to interpret a replay.
- Do not put unbounded token streaming or large payloads in durable history;
  store bounded summaries or artifact references.
- After budget exhaustion, transition to a safe terminal or owner-required
  state rather than retrying indefinitely.

## Circuit Breakers and Bulkheads

Use circuit breakers for dependencies whose repeated failure would waste
capacity or amplify an outage. Define open/half-open behavior, probe ownership,
fallback eligibility, and user-visible degradation.

Use bulkheads to isolate tenants, tools, providers, agents, queues, or risk
classes when one failure domain could exhaust shared capacity. Pair limits with
backpressure, admission control, and bounded queues.

## Compensation and Reconciliation

Compensation is a new business action, not a database rollback. Define:

- which completed effects are compensable;
- reverse action, authority, idempotency, and deadlines;
- ordering and dependency constraints;
- what happens when compensation fails;
- irreversible effects and required preapproval;
- audit linkage between original and compensating actions.

Reconciliation compares authoritative desired and observed state, explains
drift, and performs only authorized repairs. Use it for unknown outcomes,
irreversible partial success, external systems without transactions, and
eventual consistency.

Do not let the model invent compensation. The domain owns valid compensating
actions and invariants; the model may propose among allowed options.

## Recovery Review

- Can the process resume after application, worker, provider, and workflow
  owner failure?
- Is the checkpoint before the earliest unverified effect?
- Can duplicate delivery repeat an effect?
- Does every timeout distinguish not-started, failed, committed, and unknown?
- Are retry, fallback, and compensation owners unique?
- Can approval expire or become stale while paused?
- Are cancellation and partial results durable?
- Can a user observe verified progress, steer by creating a new task version,
  cancel all owned execution, and receive durable input/approval/completion
  notifications?
- Are sandboxes, leases, delegated credentials, scheduled retries, and
  temporary artifacts cleaned or retained under an explicit policy?
- Can operators reconcile authoritative state without hidden model memory?
- Are rollback and recovery objectives tested at representative scale?

## Primary Sources

- [Temporal retry policies](https://docs.temporal.io/encyclopedia/retry-policies)
- [AWS, Making retries safe with idempotent APIs](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/)
- [AWS saga patterns](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/saga-patterns.html)
