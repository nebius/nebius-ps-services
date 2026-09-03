# Investigation Protocol

## Failure Contract

Define the failure before changing the system:

- **Expected behavior:** observable contract or invariant.
- **Actual behavior:** exact deviation.
- **Scope:** affected and unaffected inputs, users, workloads, environments,
  versions, hosts, services, and boundaries.
- **Reproduction or characterization:** smallest safe command, request, test,
  state sequence, trace comparison, or evidence set.
- **Signature:** exit code, error type, invariant breach, metric pattern, trace
  boundary, or other evidence that separates this failure from unrelated ones.
- **Timeline:** last known good, first known bad, deployments, migrations,
  configuration changes, dependency changes, and mitigations.
- **Success criteria:** evidence required to prove resolution.
- **Constraints:** compatibility, safety, performance, time, and permitted
  changes.
- **Operational context:** exact target identity, ownership, environment class,
  blast radius, reversibility, maintenance window, data sensitivity,
  observability gaps, and allowed mutations.
- **Assertion boundary:** included and excluded components and dependencies,
  exercised control and data paths, and the incident-window start and end.

Do not let an unstable symptom become the oracle for bisection, minimization,
fuzzing, or repeated trials.

## Stack And Architecture Inventory

Complete discovery before choosing technology-specific diagnostics. Record:

| Field | Required evidence |
| --- | --- |
| Technologies and versions | Runtime-reported version, image or package identity, and matching official documentation version |
| Deployment model | Bare metal, VM, container, orchestrator, managed service, or hybrid |
| Configuration authorities | Active files, flags, environment inputs, generated resources, policies, and last-known changes |
| Components and dependencies | Expected instance, owner, dependency direction, and failure propagation path |
| Interfaces | Ports, protocols, DNS names or service identities, authentication, authorization, and encryption |
| Flows | Control, data, job, event, storage, and observability paths |
| Vendor comparison | Expected architecture, observed topology, drift, unsupported assumptions, and evidence gaps |

Use current official vendor architecture and configuration documentation. Do
not mark Design `PASS` merely because expected components exist: demonstrate
that the observed versions, roles, dependencies, and flows match the supported
model, or record `FAIL` or `UNKNOWN`.

## Component Verification Matrix

Create one row for every discovered component, including external dependencies
that can cause the symptom:

| Component | Exists and expected version | Active configuration | Process or workload health | Dependency reachability, authentication, and DNS | Resource pressure and time sync | Restart history and recent changes | Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |

Record negative evidence and partial access. A healthy status endpoint proves
only that endpoint; corroborate the component's real control or data path and
its incident-window logs.

## Incident Timeline

Establish the incident window before collecting logs. Normalize timestamps to
one timezone and record clock source, offset, and uncertainty for each host or
node. Do not correlate cross-host timestamps until synchronization is verified
or the measured skew is included.

Correlate with the strongest available identifiers: request, trace, job, pod,
process, host, node, device, volume, connection, and restart identifiers.

| Time | Source and clock basis | Identifier | Event | Evidence or inference |
| --- | --- | --- | --- | --- |

Separate stale historical errors from events in the incident window.

## Layered Log-Coverage Ledger

Investigate each relevant layer with bounded time and identifier filters:

| Layer | Source or configured location | Window and filters | Finding | Coverage status |
| --- | --- | --- | --- | --- |
| Component | Native service or subsystem log | | | examined / unavailable / unsafe / not applicable |
| Application or job | Application and job stdout or stderr | | | |
| Container or orchestrator | Current and previous workload logs, events, runtime | | | |
| Service manager | systemd journal or equivalent supervisor | | | |
| OS and kernel | Kernel, OOM, cgroup, RAS, driver, boot | | | |
| Network and firewall | DNS, routing, policy, firewall, CNI, fabric | | | |
| Storage | Filesystem, block, network storage, CSI, device | | | |
| GPU or hardware | Driver, Xid, ECC, RAS, PCIe, fabric | | | |

Every relevant row must name the source examined or why it was unavailable,
unsafe, or not applicable. Absence of a configured file may require a syslog or
journal fallback. Do not equate no matching lines with complete coverage unless
the source, retention, window, clock, and filters are all proven.

The canonical ledger contains exactly these eight rows in the documented
order. Use only `examined`, `unavailable`, `unsafe`, or `not applicable` as the
coverage status. Keep a row when its layer does not apply so the coverage gap
is explicit. Primary local evidence such as configured component or application
logs, container logs, journals, and kernel logs is baseline evidence gathering;
it is not subject to Grafana provider-admission gates. Remote observability
queries remain hypothesis-gated separately.

## Evidence Ledger

Keep these categories distinct:

- **Observed fact:** directly measured, reproduced, or captured.
- **Derived inference:** follows from observed facts and the current model.
- **Hypothesis:** plausible causal explanation not yet proven.
- **Unknown:** missing evidence that could change the diagnosis.
- **Negative evidence:** observation that weakens or eliminates a hypothesis.

For each hypothesis record:

```text
Hypothesis:
Supporting evidence:
Contradicting evidence:
Prediction:
Falsifying observation:
Next experiment:
Status:
```

Maintain three to seven plausible hypotheses when possible. Rank them by
evidence and information value, not narrative appeal or recency.

## Experiment Ledger

Every consequential command or instrumentation change must answer a question:

```text
Question:
Hypotheses addressed:
Single changed variable:
Prediction:
Falsifying result:
Expected supporting evidence:
Timeout or output bound:
Risk and rollback:
Next branch for each material result:
Observed result:
Ledger update:
```

Prefer experiments with mutually exclusive outcomes. For example, capture a
value immediately before and after a boundary to distinguish upstream
production from boundary corruption.

Do not rerun an unchanged command unless repetition is the measurement. After
three experiments that do not materially update the ledger, reconstruct the
system model and choose another localization dimension.

Never use indefinite `tail -f`, arbitrary sleeps, passive terminal waiting, or
large unfiltered log dumps. A bounded follow operation is allowed only when it
tests a stated event hypothesis, has a deadline, and records the correlation
identifier and next branch.

Diagnostic experiments are not remediation attempts. Once a remediation is
applied and the original reproducer still shows the same blocker, record the
failed attempt under [remediation-budget.md](remediation-budget.md) before
another repair. Do not admit the retry until a new log observation, stack
trace, code inspection, runtime-state observation, or equivalent evidence
updates the model and supports a genuinely new falsifiable hypothesis.
Rephrasing the previous hypothesis or reusing its evidence is not a retry plan.
If the evidence or hypothesis gate cannot be satisfied, stop and return the
structured investigation report with the missing evidence and next action.

## Minimal System Model

Trace only the path capable of explaining the failure:

- entry point and caller
- control flow and data transformations
- state reads, writes, ownership, and lifecycle
- process, thread, task, host, container, service, network, datastore, queue,
  filesystem, identity, and external dependency boundaries
- configuration resolution, feature flags, generated artifacts, and caches
- retries, deadlines, backoff, idempotency, and error propagation
- deployment, release, dependency, and migration identity
- observability at each boundary

Represent distributed failures as one request, transaction, job, or event
moving through this boundary graph. Compare affected and unaffected examples at
the same boundaries.

## Localization Dimensions

- **Temporal:** revision, release, deployment, migration, dependency, image,
  configuration, certificate, or policy change.
- **Spatial:** function, component, process, service, host, zone, network path,
  queue, or storage boundary.
- **Input:** field, byte range, record, event, request property, file, or batch.
- **Environment:** OS, architecture, runtime, compiler, package resolution,
  locale, timezone, filesystem, resource limit, container, or host.
- **State sequence:** ordering, retry, race, previous request, cleanup,
  failover, leader change, or lifecycle transition.

Use the dimension whose next experiment most sharply separates the remaining
hypotheses.

## Root-Cause Proof

A root-cause claim needs:

1. A complete causal chain from trigger to violated invariant to symptom.
2. An explanation that fits timing, scope, affected inputs, and unaffected
   cases.
3. A counterfactual where neutralizing the proposed cause removes the original
   failure while preserving intended behavior.
4. Reintroduction of the faulty condition when safe and practical.
5. Evidence contradicting strong alternatives.
6. A confidence label: `proven`, `high confidence`, `probable`, or `unknown`.

When decisive evidence is inaccessible, lower confidence and name the exact
next experiment. Do not convert incomplete access into false certainty.

## Anti-Thrashing Gates

- **Command gate:** the result must be able to change the next decision.
- **Change gate:** change one causally relevant variable at a time.
- **Repetition gate:** repeat only to measure frequency, timing, or variance.
- **Patch gate:** do not chain speculative functional fixes.
- **Model-reset gate:** rebuild after three low-information experiments.
- **Scope gate:** broaden searches or rebuilds only after localization evidence.
- **Evidence gate:** retain negative and contradictory evidence.
- **Retry-admission gate:** require new evidence and a genuinely new
  evidence-derived hypothesis before each remediation retry.
- **Completion gate:** one passing test or restart is not closure.
- **Remediation-budget gate:** use the saved 5/120 default or user-authorized
  profile through the 10/180 maxima for one blocker tranche; at its attempt or
  active-time limit, stop all tools and report instead of widening the search.
