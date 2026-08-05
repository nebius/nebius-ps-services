---
name: optimize-pytest
description: "Use for pytest suite performance measurement, review, and safe optimization in existing Python applications, especially large suites: distinguish startup, collection, setup, call, and teardown costs; inspect fixtures, imports, parametrization, plugins, coverage, selection, parallelism, and CI lanes; rank cumulative bottlenecks; and verify like-for-like improvements. Use when pytest collection or execution is slow, fixtures repeat expensive work, test lanes need scaling, or xdist, testmon, and sharding are under evaluation. Do not use for generic failing-test diagnosis, flaky-test root cause, Python project scaffolding, or production-code performance unless pytest-suite speed is the primary request."
---

# Optimize Pytest

## Help

For `$optimize-pytest --help` or `$optimize-pytest -h`, return concise help and stop before
any workflow step. Include the purpose, invocation policy, public usage/actions,
and `-h, --help` plus only documented skill-level options; say "No additional
public flags" when none exist. For internal or coordinator-only skills, state
that boundary and that no standalone public workflow action exists. After the
selected `SKILL.md` is loaded, help is report-only: do not call any additional
tools, inspect project state, or modify files, private state, Git, or external
systems. Never
expose private helper actions or treat help as workflow authorization.

## Purpose

Measure and improve pytest feedback time without weakening correctness,
isolation, failure diagnostics, or the final validation gate. Optimize from
evidence: inspect first, measure a safe representative lane, rank cumulative
cost, apply the smallest justified change, and prove a like-for-like result.

## Use This Skill For

- Diagnosing slow pytest startup, collection, fixture setup, test calls, or
  teardown.
- Reviewing expensive autouse fixtures, fixture scopes, application/database
  startup, imports, parametrization, test data, sleeps, subprocesses, coverage,
  or reporting overhead.
- Designing fast, full, integration, slow, and scheduled test lanes.
- Evaluating pytest-xdist, pytest-testmon, pytest-split, or a later build-system
  escalation from measured evidence.
- Implementing focused test or pytest-configuration improvements when the user
  asks to optimize, fix, refactor, or apply the recommendations.

## When Not To Use

- Use `troubleshoot` for failing, hanging, intermittent, or flaky tests whose
  cause is not established as test-suite performance.
- Use `python-project` for Python project or test-layout scaffolding.
- Use `code-review` for a generic implementation review.
- Use `github-workflows` for substantive GitHub Actions implementation after
  this skill has defined the test-performance requirements.
- Do not use this skill merely to run a feature's tests or profile production
  code when pytest-suite speed is not the primary objective.

## Inputs

- Project or test-suite path, requested test selection, timing complaint,
  existing benchmark, CI evidence, or candidate optimization.
- Local instructions, pytest configuration, dependency metadata, fixtures,
  plugins, tests, CI commands, and relevant documentation.
- User constraints for report-only work, allowed edits, runtime budget,
  external services, artifacts, dependencies, and final correctness gates.

## Required Reads

Before executing pytest, inspect the applicable instructions and the project's
pytest configuration, dependency files, lockfiles, root and nested
`conftest.py` files, plugin hooks, marker registration, testpaths, native test
commands, CI lanes, and artifact ignore rules.

Read:

- `references/safe-measurement.md` before running any measurement or profiler.
- `references/optimization-playbook.md` when diagnosing or changing suite
  structure, fixtures, imports, data, or test classification.
- `references/scaling-and-ci.md` before recommending or adopting parallelism,
  affected-test selection, coverage changes, sharding, CI thresholds, or Pants.

Verify version-sensitive pytest, plugin, and Python profiler guidance against
current official documentation before changing the reusable instructions or
introducing a dependency.

## Process

1. Determine the request mode:
   - Treat review, measure, diagnose, assess, and "why is this slow" prompts as
     report-only.
   - Treat optimize, fix, refactor, implement, and apply prompts as permission
     for focused project changes within the user's stated scope.
   - When intent is ambiguous, stay report-only.
2. Perform a static preflight:
   - Resolve the repository root and preserve existing user changes.
   - Identify an already-installed project interpreter and pytest environment.
     Do not install dependencies to create a benchmark environment.
   - Inspect how native commands expand; do not use a target that may bootstrap
     or update dependencies as a measurement command.
   - Identify collection-time code, external services, subprocesses,
     containers, shared infrastructure, mutable global state, and unbounded
     waits before executing tests.
3. Define a safe representative selection. Start with collection or one
   targeted module, remembering that collection still imports and executes
   project and plugin code. Broaden only after the narrower run is safe and
   useful. Obtain explicit direction before an unbounded full suite,
   integration lane, live service, package installation, or persistent
   artifact is required.
4. Capture the baseline context and invariants: source identity, dirty state,
   exact command, interpreter, pytest/plugins/config, cache policy, collected
   and deselected counts, outcome counts, exit status, raw samples, median, and
   spread.
5. Measure startup/collection and setup/call/teardown with the least intrusive
   tool that answers the question. Keep wall-time baselines separate from
   instrumented, coverage, profiling, disabled-plugin, or parallel runs.
6. Diagnose cumulative cost, not only the single slowest test. Rank findings by
   per-invocation cost multiplied by invocation count, confidence, risk, and
   expected benefit.
7. In report-only mode, return recommendations and the proof required before
   each change. In implementation mode, apply the smallest focused change and
   update tests, configuration, CI, docs, and changelog only where the changed
   contract requires it.
8. Re-run the equivalent measurement and correctness checks. Claim a speedup
   only when selection, outcomes, environment, and measurement method remain
   comparable. Treat selection, sharding, and affected-test workflows as
   separate feedback architectures rather than like-for-like speedups.

## Failure Handling

- If the baseline is already failing, incomplete, or changes during sampling,
  report that state and do not attribute timing differences to an optimization.
- If collection or a test unexpectedly reaches an external system, stop the
  run, report the boundary, and require a safer selection or explicit
  environment authorization.
- If samples are noisy or overlap, increase evidence quality or report the
  result as inconclusive; do not promote a marginal median difference.
- If a candidate changes collected, deselected, passed, failed, skipped,
  xfailed, or rerun counts unexpectedly, treat it as a correctness regression,
  not a performance improvement.

## Guardrails

- Implicit invocation grants no additional authority to execute broad tests,
  install plugins, edit files, change CI, or access live systems.
- Do not write profiles, JUnit XML, timing databases, or benchmark files into
  the repository unless the user wants persistent artifacts and the project
  defines an intentional ignored or committed location.
- Use only a fresh, validated, task-owned temporary directory for pytest cache,
  profile, or `--basetemp` output. Pytest may clear an existing `--basetemp`;
  never point it at a reused or broad directory.
- Do not remove tests, weaken assertions, hide failures, add permanent reruns,
  combine unrelated tests, or relabel integration behavior as unit behavior to
  improve displayed runtime.
- Do not widen fixture scope without proving immutable sharing or deterministic
  per-test reset. Preserve lightweight safety autouse fixtures unless evidence
  shows a safer explicit design.
- Do not make `-n auto`, disabled plugin autoload, testmon, marker filtering, or
  sharding the canonical correctness command before required plugins,
  isolation, debugging, coverage, and final-gate behavior are verified.
- Do not run tests that mutate production, shared infrastructure, credentials,
  cloud resources, databases, or external services without explicit action
  scope and a confirmed safe environment.
- Keep reusable guidance public, generic, and free of secrets, private
  endpoints, customer data, internal hostnames, raw logs, and one-off paths.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return:

- Request mode and execution boundary.
- Baseline context, exact selection, sample count, median, spread, and
  comparability status, or the exact reason measurement was blocked.
- Startup, collection, setup, call, and teardown findings, marking each as
  measured, inferred, not measured, or not applicable.
- Ranked cumulative contributors with evidence, risk, and expected benefit.
- Recommendations or changes made.
- Before/after selection and outcome invariants plus timing evidence.
- Correctness validation completed, skipped, or blocked.
- Remaining uncertainty, external-safety boundaries, and next escalation.

## References

- `references/safe-measurement.md`: safe measurement commands, artifact
  handling, comparability, and profiler interpretation.
- `references/optimization-playbook.md`: ordered suite-review and remediation
  rubric.
- `references/scaling-and-ci.md`: parallelism, affected-test selection, lanes,
  sharding, coverage, governance, and Pants escalation.
- `evals/trigger-prompts.md`: should-trigger and should-not-trigger examples.
