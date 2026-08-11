# Software Failure Playbooks

Select only the playbook that answers the current causal question. Repository
tooling and official language/runtime documentation determine exact commands.
When application code may own the failure, also follow `code-debugging.md`.
Passing unit or integration tests is supporting evidence only; prove the
executed failure path, inputs, state, and earliest divergence before marking
Relevant code paths `PASS`.

## Regression

- Establish equivalent known-good and known-bad environments and a stable
  failure signature.
- Automate the oracle before revision bisection.
- Inspect the first bad change at hunk level and prove which changed invariant
  connects it to the symptom.
- Add a regression test independent of implementation details.

Do not bisect with an intermittent or ambiguous oracle; first stabilize or
statistically characterize it.

## Flaky Or Intermittent

- Measure pass, failure, timeout, timing, and signature distributions.
- Cluster signatures before assuming one underlying defect.
- Record seeds, random state, time inputs, concurrency, load, and prior state.
- Compare good and bad runs and reduce the failure-preserving sequence.
- Repeat before and after repair with enough trials to detect meaningful
  change.

A retry or sleep that changes frequency is timing evidence, not proof.

## Shell And Process Lifecycle

- Capture the exact interpreter, arguments, working directory, environment
  names without secret values, `PATH` resolution, exit status, and signal path.
- Check quoting, word splitting, globbing, pipeline status, subshell scope,
  redirection order, process groups, traps, temporary files, and cleanup.
- Test paths with spaces, empty values, metacharacters, partial input, signals,
  and missing commands.
- Prefer argv execution over string commands and avoid `eval`.

## Concurrency, Races, And Deadlocks

- Capture thread, goroutine, task, or process dumps and lock ownership.
- Build event ordering, wait-for, or happens-before relationships.
- Use the runtime's race detector or thread sanitizer on executed paths.
- Perturb scheduling narrowly and repeat under representative concurrency.
- Assert shared-state and lifecycle invariants near the earliest divergence.

Absence of a detector finding proves only that the executed paths produced no
reported finding.

## Memory Corruption And Undefined Behavior

- Match architecture, optimization, allocator, runtime, and symbolization to
  the failing environment.
- Use the runtime's memory and undefined-behavior diagnostics.
- Minimize the failure-preserving input or program.
- Reintroduce the faulty condition against the reduced case when safe.
- Verify adjacent ownership, bounds, lifetime, alignment, and concurrency.

## Performance Regression

- Define a representative workload, warmup, concurrency, latency percentile,
  throughput, memory, saturation, and variance contract.
- Compare known-good and known-bad CPU, wall-clock, allocation, lock, I/O, and
  distributed profiles under equivalent conditions.
- Explain why a path became active or more expensive; a hot frame alone is not
  a cause.
- Validate tail behavior and resource saturation, not averages alone.

## Input, Parser, And Data Corruption

- Reduce the input while preserving the exact signature.
- Capture values immediately before and after parsing, serialization,
  validation, encoding, storage, and transport boundaries.
- Check normalization, locale, timezone, integer limits, Unicode, truncation,
  ordering, duplicate handling, and partial writes.
- Confirm whether the first invalid value is produced, accepted, transformed,
  transmitted, or persisted at each boundary.

## Build, Dependency, And Cache Invalidations

- Compare lockfiles, resolved dependency identity, compiler/runtime versions,
  flags, generated files, incremental artifacts, and cache keys.
- Preserve the failing cache state before clearing it.
- Determine which input is missing from the cache key or dependency graph.
- Prove a clean build only as a differential observation; do not declare the
  cache the cause until the invalidation mechanism is identified.

## CI-Only And Environment Differences

- Compare OS, architecture, native versus translated execution, toolchain,
  dependency resolution, locale, timezone, filesystem behavior, resource
  limits, available commands, working directory, permissions, and container
  identity.
- Minimize the environmental delta instead of copying the entire CI system.
- Do not print environment values; compare selected safe identities or hashes.
