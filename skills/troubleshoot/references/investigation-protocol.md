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

Do not let an unstable symptom become the oracle for bisection, minimization,
fuzzing, or repeated trials.

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
Risk and rollback:
Observed result:
Ledger update:
```

Prefer experiments with mutually exclusive outcomes. For example, capture a
value immediately before and after a boundary to distinguish upstream
production from boundary corruption.

Do not rerun an unchanged command unless repetition is the measurement. After
three experiments that do not materially update the ledger, reconstruct the
system model and choose another localization dimension.

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
- **Completion gate:** one passing test or restart is not closure.
