# Resource Safety And Admission

Use for large tool outputs, execution environments, concurrent or distributed
agents, and shared capacity. These are workload-driven design rules, not a
mandate for a cluster, a scheduler, or fixed universal memory thresholds.

## Resource Contract

Admit a resource vector covering model demand, execution slots, memory, disk,
processes, open streams, buffered output, artifact capture and cleanup/checkpoint
reserve. Give each dimension an owner, measurement scope, hard bound, reservation
lifetime and release evidence. A task count alone is not resource capacity.

Count admitted tasks, waiting tasks, active execution, model calls and workspaces
separately. Keep unknown usage/reservations charged until reconciled. Replanning,
retry or replacement cannot reset spent, committed or uncertain allowances.

## Trustworthy Pressure Response

Measure the actual enforcement scope and relevant shared ancestor limits with
identity, freshness and available/reclaimable capacity. Raw free memory or a
single process's RSS is insufficient. Swap is not spare RAM. Unknown visibility
is an explicit state, not zero use or unlimited headroom.

Define warning, admission throttling, shedding, draining, stop and resume
behavior per workload. Reserve enough capacity to checkpoint and clean up.
Monitor throughout long tools, model streams and artifact operations, not only
between model turns. Loss of trustworthy measurement prevents new resource-heavy
admission; cleanup and bounded diagnosis remain available. Stop only exact owned
work through its lifecycle owner. Never tune the host or kill unrelated jobs as
an implicit recovery step.

A sampled threshold is an operational control, not a physical reservation or a
proof against instantaneous allocation. State the enforcement limits honestly.

## Bound Intermediate Work

Bound producers, stream buffers, decoding, decompression, archive members,
parsers, temporary files and output capture before allocation. A small final
preview does not bound a multi-gigabyte intermediate result. Reject, truncate
with explicit incompleteness, spill into authorized bounded artifacts, or
stream safely according to the contract.

Do not invoke model summarization as an emergency resource-safety mechanism.
Context compaction is a separately budgeted quality feature; it cannot repair
unbounded tool capture or bypass mandatory context and lineage requirements.

## Delegation And Lifetime Budgets

For distributed work, deterministic admission checks assignment/dependency
identity, permitted graph shape, depth, edges, total lifetime tasks, revisions,
write-conflict domains and output limits before persistence or dispatch.
Completed, replaced and superseded work counts toward lifetime expansion.
Finite concurrent fan-out alone does not bound total work.

Child and group budgets are disjoint reservations from the parent's remaining
allowance. Include model retries, tools, delegation, artifacts and cleanup in
the appropriate accounting scopes. Do not give every child a copy of the entire
parent budget. Reconcile a child's spend and unresolved effects before returning
unused reservations. Unknown overlapping writes serialize or reject; a model's
claim that tasks are independent is insufficient.

For genuinely shared capacity, add bounded queue admission, tenant fairness and
backpressure. Do not add a second scheduler where an existing owner can enforce
these constraints.

## Acceptance Scenarios

- A tiny final answer follows an oversized compressed tool result: limits
  apply before decoding and parsing.
- Host or ancestor measurement disappears during a long command: new heavy
  admission stops while owned cleanup remains possible.
- Replanning repeatedly replaces completed children: lifetime task and budget
  limits still hold.
- Cancellation leaves a remote child unresolved: its reservation is retained
  until independent quiescence evidence arrives.
- A concurrent tenant consumes headroom: the system follows its declared
  pressure policy without stopping unrelated work.

These policies extend the runtime, durability and capacity contracts. Qualify
the selected operating system, container/runtime and remote-resource controls
against their current official sources during implementation.
