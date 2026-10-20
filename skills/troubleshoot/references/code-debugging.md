# Code Debugging Playbook

Use this playbook whenever application, service, script, controller, plugin, or
client code remains a plausible owner. Passing tests, lint, or static analysis
does not prove code is bug-free; it proves only the exercised checks reported no
finding.

## Adaptive Decision Guide

Use only rows implicated by the failing path; record material unexamined boundaries.

<!-- markdownlint-disable MD013 -->

| Symptom | Competing explanations | Cheapest distinguishing evidence | Interpretation | Next branch |
| --- | --- | --- | --- | --- |
| Wrong result | wrong artifact / configuration precedence / transformation defect | executed build identity, effective input/configuration and values across the suspect boundary | earliest incorrect value identifies the owner; repository HEAD may not be deployed | reduce input while preserving the oracle and compare one unaffected case |
| Intermittent crash or lost update | race / lifetime / memory corruption / process ordering | symbolized failure and ordering evidence; bounded race or memory diagnostic if supported | a sanitizer changes timing; a clean run is not absence proof | reduce schedule/state sequence and confirm repair with relevant instrumentation |
| Regression between revisions | code / dependency / environment drift | stable oracle on equivalent good/bad builds and fixed inputs | untestable revisions must be skipped, not classified bad | bisect only with stable oracle; retain build and dependency identity |
| Slow code | CPU execution / allocation / wait / changed workload | matched workload, wall/CPU split and differential profiles | hot frames show cost, not necessarily cause of the regression | compare same inputs/build settings; investigate waiting through its owner |

<!-- markdownlint-enable MD013 -->

### Escalation Conditions

Escalate only if the next observation can separate remaining explanations. Bound
time, output, target and effects; preserve evidence and original settings. Treat
production or unconfirmed targets as read-only without exact action authority.

### Verification

Replay the original operation from the same relevant identity and workload, compare
an unaffected control, and verify the implicated boundary independently. Invalidate
pre-change observations and state material unverified scope. A passing status
command cannot replace the workload oracle.

## Reproduce Or Characterize

Freeze the exact revision, build or image, runtime, entry point, arguments,
working directory, configuration and environment identities without secret
values, input or request identity, data preconditions, dependency versions,
concurrency, seed, clock inputs, and stable failure signature. Reproduce with
the smallest safe real path, or characterize affected and unaffected executions
when reproduction is unsafe.

## Trace The Real Path

Follow the failing execution from public entry point through control flow, data
transformations, state reads and writes, serialization, boundary calls, retries,
timeouts, cleanup, and error propagation. Compare expected and actual values at
the earliest divergent boundary. Confirm which deployed artifact and code path
actually ran; source inspection alone cannot prove runtime behavior.

Inspect as relevant:

- complete stack traces with symbols, exception chains, task or thread dumps,
  core dumps, crash reports, and process exit or signal evidence;
- request, job, trace, process, thread, task, and data correlation identifiers;
- configuration resolution, environment inputs, flags, generated files,
  permissions, locale, timezone, limits, and dependency responses;
- known-good and known-bad revisions, recent changes, generated artifacts,
  lockfiles, build flags, and deployment identity;
- concurrency ordering, ownership, lifecycle, cache invalidation, partial
  failure, idempotency, and state cleanup.

## Focused Analysis

Choose checks from the hypothesis, not from habit:

- focused unit, integration, contract, or regression tests that execute the
  suspect path and preserve the original oracle;
- language and runtime static analysis, type checking, race, memory,
  undefined-behavior, leak, or sanitizer diagnostics;
- revision bisection only with a stable and equivalent oracle;
- profiling or tracing only when it can separate named hypotheses;
- input or sequence reduction while preserving the exact signature.

Record commands, bounds, versions, and results. Add a regression oracle before
or with the repair when feasible. Verify it fails against the faulty state and
passes against the repaired state without encoding implementation details.

## Temporary Instrumentation

Add narrow structured instrumentation at the predicted divergence only after
stating the expected supporting and falsifying observation. Scope it to safe
identifiers, bound volume and duration, estimate performance impact, redact
secrets and customer data, and define removal or rollback. Do not log raw
payloads or credentials. Remove temporary instrumentation and verify repository
and runtime cleanup before closure.

## Code-Path Completion Gate

Mark Relevant code paths `PASS` only when evidence identifies the executed path,
compares inputs and state at the suspected boundaries, addresses stack or crash
evidence when available, examines relevant recent changes, runs relevant
checks, and either proves the causal repair or eliminates code with a supported
boundary handoff. Use `UNKNOWN` when the runtime artifact, reproducer,
symbols, inputs, or decisive path evidence is unavailable.

## Controlled Debug Escalation

Runtime debug flags, profilers, tracers, core capture, or added logs can alter
timing, expose data, and consume resources. Define target, predicate, duration,
output bound, performance warning, storage and access, redaction, original
settings, rollback, and success or stop conditions. Production or unconfirmed
targets require exact live-change authorization.

## Official Sources

Use the current official documentation for the observed language, compiler,
runtime, framework, debugger, sanitizer, profiler, and build tool. Match the
deployed versions; do not copy flags from a different release.
