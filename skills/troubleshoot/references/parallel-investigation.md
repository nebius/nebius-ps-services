# Conditional Parallel Investigation

One coordinator owns the failure contract, shared evidence, budgets, causal
conclusion, active diagnostics, remediation and report. Use existing native agents
only when independent questions can reduce time to a decision enough to justify
total work and coordination cost. A simple localized failure stays serial.

## Admission And Limits

- Preserve current host delegation authorization. Verify actual tool restrictions,
  native read-only roles and local overrides before dispatch. A prompt saying
  "read-only" is not enforcement. If unavailable, continue serially.
- Normally use two investigators; never exceed three simultaneously, including a
  vendor specialist. No recursive delegation. Never create another framework,
  scheduler, database, credential system or permission exception.
- Give each task at most five minutes, shortened by remaining task time. All workers
  consume the same overall time, action, query and remediation budgets; no separate
  tranches. Further work requires a new evidence-driven assignment within that
  budget. Reserve time for cancellation and reconciliation before exhaustion.
- Permit bounded passive reads only. Workers do not independently start tests,
  debug containers, packet captures, stress, restarts or repairs. Classify reads by
  effect, including Kubernetes debug/exec and GPU diagnostics. The coordinator
  owns active checks under the existing authority and live-evidence rules.
- Allocate disjoint source/query scopes where possible. Provider admission and query
  allowances remain adapter-owned and shared; concurrency cannot multiply them.

## Useful Decomposition

| Route | Independent questions, only when implicated |
| --- | --- |
| Code | Executed control/data path; effective configuration/dependencies; concurrency/state/regression evidence. |
| Infrastructure | Compute/OS resource boundary; storage path; network path. |
| Mixed or undetermined | Application boundary versus its immediate platform dependency. |
| Vendor-specific | Exact driver/runtime compatibility, CNI behavior or scheduler/fabric topology within the same ceiling. |

Do not dispatch every domain. Do not assign dependent questions before their input
exists or duplicate an existing read just to obtain agent agreement.

## Internal Assignment Template

These are working templates, not a public API or persisted protocol.

```text
Question and distinguishing alternatives:
Scope; target/artifact/configuration identity; observation window:
Existing evidence references and material uncertainty:
Permitted bounded passive reads; prohibited actions:
Deadline; remaining shared budget; cancellation condition:
Return findings with source references, counterevidence, uncertainty,
coverage/exclusions, next discriminating observation and completion status.
No delegation, tests, captures, mutations or independent conclusions of repair.
```

## Reconciliation And Completion

Workers return evidence-backed findings, not votes. Shared-source agreement is
duplicate evidence, not independent corroboration. Check identity, window, source
and effects before admitting any result. Missing access or a failed query remains
unknown. Preserve supporting and contradicting evidence together.

Resolve conflicting findings with a targeted discriminating check. Mark stale or
late results historical when their target/configuration/window has changed; reopen
affected exclusions when valid contradictory evidence arrives. One found fault
does not eliminate another fault or contributor. Never silently discard dissent.

Cancel unnecessary work after localization through native controls and reconcile
every outstanding worker before completion: completed, cancelled, failed or
unconfirmed. Stop scope violations immediately; preserve their effects in trial
history. Unconfirmed cancellation cannot support a quiescent replay or complete
accounting. Do not issue tools after the existing hard budget-stop boundary.

Count parent and worker diagnostic actions, usage and outcomes once by native
identity. Separate coordination overhead; do not sum overlapping elapsed durations
or add child tokens to an inclusive parent total. Missing child traces, usage or
terminal evidence leave efficiency qualification incomplete. The proposed
two/three-worker limits require measurement; they are not established optima.

## Sources

- [Orchestrator-worker pattern](https://www.anthropic.com/index/building-effective-agents)
- [Kubernetes live debugging effects](https://kubernetes.io/docs/tasks/debug/debug-application/debug-running-pod/)
- [DCGM diagnostic levels](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/dcgm-diagnostics.html)
