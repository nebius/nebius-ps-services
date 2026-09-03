# Runtime Architecture and Sessions

Use this reference when an accepted agent needs a runtime boundary, durable
session, sandbox, asynchronous execution, user control, or process-failure
recovery. Keep these logical contracts provider-neutral; `ai-stack` owns the
technology and deployment choices that implement them.

## Contents

- Trusted reference architecture
- Provider-neutral interfaces
- Durable session contract
- Bounded agent loop
- User and operator controls
- Recovery and operational review

## Trusted Reference Architecture

Separate control, execution, and durable state even when the first release
runs them in one process:

| Logical component | Owns | Must not own |
| --- | --- | --- |
| Product API and identity | Caller authentication, tenant identity, request validation, product authorization | Model-generated authorization decisions |
| Agent control plane | Session lifecycle, model calls, routing to the policy authority, budgets, approval coordination, checkpoints, recovery | Policy or approval decisions, reusable production credentials, or unrestricted execution |
| Model runtime adapter | Provider request mapping, capability negotiation, response normalization, usage metadata | Business policy or silent semantic fallback |
| Context builder | Authorized selection, provenance, trust labels, redaction, token allocation | Canonical business state or instruction trust for retrieved text |
| Durable session store | State transitions, events, checkpoints, versions, approval records, effects, artifacts, outcome status | Approval validity, the entire prompt, or long-term memory by default |
| Policy and approval gateway | The single action-policy and approval decision authority: risk classification, policy authorization, argument constraints, and approval validity | Delegating final authority to the proposing model or an execution adapter |
| Tool capability gateway | Typed dispatch, enforcement of bound policy decisions, resource-specific authorization, identity propagation, credential mediation, idempotency, result normalization | Re-deciding policy or approval validity, broad raw credentials, or generic unbounded actions |
| Isolated execution plane | Model-directed files, code, packages, and bounded computation | Control-plane secrets, cross-tenant state, unrestricted egress |
| Knowledge and memory | Governed retrieval, provenance, scopes, versions, retention | Automatic promotion of arbitrary model or tool output |
| Observability and evaluation | Traces, metrics, audit linkage, eval results, release evidence | Private chain-of-thought as an operational dependency |

These are responsibility boundaries, not mandatory services. Co-locate them
when one owner, failure domain, and scale allow it. Split a boundary only for a
named isolation, ownership, scaling, recovery, compliance, or deployment need.

## Provider-Neutral Interfaces

Define only the interfaces required by the accepted workload:

- **Model runtime**: structured decision, tool proposal, usage, cancellation,
  capability metadata, and normalized error.
- **Session store**: append event, commit checkpoint, compare-and-transition,
  acquire or renew lease, reconcile, and load the next valid state.
- **Policy gateway**: as the single policy and approval decision authority,
  authorize, deny, require approval, constrain arguments, revalidate current
  state, and record an immutable decision.
- **Tool gateway**: validate, enforce the bound policy decision, apply
  resource-specific authorization at the domain boundary, execute, observe,
  compensate, and return an effect receipt. It cannot broaden or replace the
  policy decision.
- **Sandbox**: provision, execute, inspect, snapshot when justified, export a
  validated artifact, cancel, and destroy.
- **Memory**: search, read, propose, validate, version, promote, quarantine,
  correct, redact, and delete.
- **Trace sink**: emit redacted lifecycle, model, context, tool, policy,
  approval, effect, verification, and budget events.

Do not force every provider into a lowest-common-denominator adapter. Pass
required capabilities and compatibility gates to `ai-stack`; use explicit
capability negotiation and reject a profile that cannot meet a hard contract.
Provider sessions, caches, background jobs, or hosted sandboxes remain
replaceable implementation details unless independently proven as canonical
for the selected product boundary.

## Durable Session Contract

Use a durable session only when continuity, effects, approvals, asynchronous
work, audit, or recovery requires it. The minimum envelope records:

```text
session, task, tenant, principal, and correlation identities
task type, goal version, state, and state version
agent definition, model profile, prompt, tool, policy, context, and memory versions
turn, tool, write, time, fan-out, and cost budgets
current checkpoint, pending action, and outcome status
owner or lease identity, deadline, expiry, created time, and updated time
```

Use an explicit state machine. Adapt names to the domain, but distinguish at
least created, running, waiting for input, waiting for approval, retry
scheduled, succeeded, failed, canceled, and expired. For every transition
record the initiator, previous state and version, reason, controlling release
identity, preconditions, effects, and next valid actions.

Persist externally observable events, not hidden reasoning:

- normalized user, system, steering, cancellation, and timeout events;
- model request and response identities, capability route, usage, and latency;
- validated tool arguments, policy and approval decisions, execution result,
  effect receipt, verification result, and retry disposition;
- state transitions, checkpoints, compaction, lease, resume, and budget events;
- artifact identity, digest, scan, version, publication, retention, and
  deletion events; and
- outcome verifier identity, result, feedback, revision count, and escalation.

Minimize sensitive payloads. Store references, digests, classifications, and
bounded summaries when full content is unnecessary.

## Checkpoint And Resume Contract

A checkpoint is the smallest validated state from which work can continue
without losing or duplicating effects. Include:

```text
current approved goal and constraints
completed milestones with independent verification
pending work, blockers, and next eligible transitions
action IDs, idempotency keys, effect receipts, and observed postconditions
artifact identities, versions, and content digests
context source references needed to rebuild the next view
release identity for model, prompt, tools, policy, runtime, data, and memory
remaining budgets, deadlines, cancellation state, and owner or lease
```

Resume by acquiring ownership, loading the latest committed checkpoint,
reconciling every in-flight or ambiguous effect, rebuilding context from
canonical sources, and taking only a valid transition. Conversation replay is
not effect recovery.

## Bounded Agent Loop

Make the loop explicit and inspectable:

```text
load authoritative session state, release identity, and remaining budgets
while the session is eligible to run:
  build the smallest authorized context for the next decision
  request and validate one structured model decision
  pause and checkpoint for required input or approval
  authorize every proposed tool and constrain its exact arguments
  before any effect-capable dispatch, persist its immutable authorized intent,
    exact action digest, and idempotency identity
  execute once through the capability gateway
  persist the observation, effect receipt, checkpoint, and budget use
  verify any claimed effect or completion independently
  succeed, request one bounded revision, wait, escalate, or stop safely
```

Terminate on verified success, required input, required approval, missing
authority, budget exhaustion, unrecoverable failure, cancellation, or expiry.
The model may propose continuation; deterministic state, policy, and budget
owners decide whether continuation is eligible.

A crash after dispatch but before receipt persistence is an ambiguous effect.
Resume must reconcile it from authoritative state using the persisted intent
and idempotency identity before retry, compensation, or terminal disposition.

## User And Operator Controls

For long-running or consequential work, expose:

- current state, last verified progress, pending action, blocker, and remaining
  budgets;
- steer as a versioned task-contract change that invalidates stale plans and
  approvals when material;
- cancel that propagates to model requests, tools, sandboxes, workers, and
  scheduled retries;
- exact approval previews with deny and expiry behavior;
- durable notifications for completion, input, approval, failure, and expiry;
- partial-result and artifact access according to authorization and retention;
  and
- effect-ledger and reconciliation views for operators.

Do not represent streaming text, heartbeats, or an optimistic progress message
as verified progress.

## Recovery And Operational Review

- Use leases and heartbeats when more than one worker could claim a session.
- Commit after every external effect and meaningful verified milestone.
- Bound tenant concurrency, queue depth, fan-out, provider demand, and retries.
- Reconcile sessions left between dispatch and acknowledgement.
- Keep dependencies and release identities compatible across supported resume
  windows, or terminate with an explicit migration requirement.
- Clean up sandboxes, temporary artifacts, delegated credentials, leases, and
  scheduled work according to retention and incident policy.
- Test kill, restart, duplicate event, stale lease, provider failure,
  cancellation, approval timeout, cleanup failure, and ambiguous effect.

Reject a runtime design when it cannot explain who owns the current state,
which effect may already have happened, how the next worker proves authority,
or how a user can stop the work safely.

## Primary Sources

- [OpenAI, A practical guide to building agents](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf)
- [Anthropic, Scaling Managed Agents](https://www.anthropic.com/engineering/managed-agents)
- [Anthropic, Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
