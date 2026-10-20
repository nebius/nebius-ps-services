# Investigation Protocol

## Failure Contract

Record expected behavior, actual symptom/signature, exact target/artifact,
incident window, affected and unaffected examples, prior interventions,
authority and success oracle. Preserve volatile evidence first. Separate incident
stabilization from diagnosis. Do not silently redefine success after a mitigation.

## Adaptive Scope And Coverage

Start with the implicated component, its effective configuration and immediate
dependencies. Follow the failing operation through only the boundaries that can
explain it. Expand for cross-boundary evidence, contradictory observations,
unknown dependencies or unexplained intermittency. Avoid full-stack inventory
before obtaining useful local evidence. Check versions and official documentation
when commands or interpretations depend on them.

Record this compact working ledger, adding rows only as useful:

| Boundary/source | Target and window | Observation/evidence | Status and reason | Next decision |
| --- | --- | --- | --- | --- |
| Implicated component | Exact artifact/process | Bounded evidence reference | examined | Discriminating branch |
| Material dependency | Exact scope or unknown | Missing/retained evidence | unexamined, unavailable or unsafe; reason | Impact on conclusion |

`unexamined` is `UNKNOWN`, not healthy or not applicable. `not applicable` needs a
positive explanation why that boundary cannot affect the declared conclusion.
A failed query is not a negative observation. Log layers are candidate sources
(component, application/job, orchestrator, service manager, OS/kernel, network,
storage, GPU/hardware), not eight mandatory records. Evidence must be complete
for the conclusion, not for the entire environment. No status token substitutes
for substantive evidence.

## Incident Timeline And Evidence Validity

For each retained observation record source, exact target and artifact/config
identity, collection time, incident window, correlation IDs, clock basis and
uncertainty, query/command, effects, bounds and result. Keep observed facts,
inferences, hypotheses, negative/contradictory evidence and unknowns distinct.
Before cross-host correlation measure or account for clock skew; uncertain event
ordering must remain uncertain. Check boot ID, retention and collection delay.
Record `timeout`, `unavailable`, `truncated`, `stale`, `changed-during-read`, and
`skipped` separately from a completed negative result.

Reuse observations only while their target, configuration, window and relevant
state remain valid. Deployment, restart, configuration or topology change
invalidates affected evidence. Preserve old observations as historical evidence;
do not reuse them to establish current health. After interruption reconcile
possibly completed actions against authoritative state before repetition.

## Hypotheses And Experiments

Keep the strongest plausible alternatives supported by evidence; no minimum
count. For each record support, contradiction, prediction, falsifier, confidence
and next decision. Identify initiating faults, contributing conditions,
downstream symptoms and independent blockers; multiple causes may coexist.

Before each consequential experiment record:

```text
Question and hypotheses addressed:
Predicted distinguishing outcomes:
Target, identity, window and relevant state:
Timeout and output bounds:
Effects, risk and rollback:
Next branch for each material result:
Observed result and evidence validity:
Model update and remaining alternatives:
```

Rank by discriminatory value, safety, execution cost and availability. Use
qualitative reasoning, not invented probabilities. Prefer observations with
mutually exclusive results and comparisons immediately before/after a boundary.
Change one relevant variable at a time unless a designed experiment requires more.

A low-information experiment is a completed observation that neither distinguishes
hypotheses nor narrows the causal boundary. After three consecutive such results,
rebuild the model or change localization dimension before another experiment.
Failed/unavailable probes are data gaps, not completed low-information trials;
repeated unavailable probes still require a new decision-changing reason.
This rule never resets or extends an attempt/time budget.

Do not rerun unchanged commands unless repetition itself measures frequency,
latency or variance. Never use indefinite tails, passive waits or unfiltered
large dumps. A bounded follow requires an event hypothesis, correlation ID,
deadline and next branch. Retries of remediation require newly acquired evidence
and a genuinely new falsifiable hypothesis; otherwise pursue another safe
observation, or report the decisive missing evidence when none remains.

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
- **Model-reset gate:** rebuild after three consecutive completed observations that neither distinguish hypotheses nor narrow the causal boundary; never reset the remediation budget.
- **Scope gate:** broaden searches or rebuilds only after localization evidence.
- **Evidence gate:** retain negative and contradictory evidence.
- **Retry-admission gate:** require new evidence and a genuinely new
  evidence-derived hypothesis before each remediation retry.
- **Completion gate:** one passing test or restart is not closure.
- **Remediation-budget gate:** use the saved 5/120 default or user-authorized
  profile through the 10/180 maxima for one blocker tranche; at its attempt or
  active-time limit, stop all tools and report instead of widening the search.
