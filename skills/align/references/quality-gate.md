# Align Quality Gate Reference

Read this file when mapping or validating a changed scope with `align`, or
when you need the detailed surface, wiring, and modularity checklist.

## Scope Checklist

Review and align the relevant surfaces:

- source code, module boundaries, imports, exports, and package structure
- entry points, dependency wiring, service registration, routing, jobs, and
  workers
- CLI commands, flags, defaults, config loading, environment variables,
  schemas, validation, examples, and help output
- tests, fixtures, snapshots, mocks, and test utilities
- CI workflows, build scripts, Makefiles, task runners, Dockerfiles, compose
  files, and deployment automation
- dependency manifests, lockfiles, type definitions, migrations, API
  contracts, and generated interfaces
- generated code or artifacts, either by regenerating them correctly or
  leaving them untouched with an explanation
- README files, design documents, architecture notes, templates, changelogs,
  and developer-facing docs
- linting, formatting, and type-check configuration that affects correctness
  or maintainability

## Review Focus

Look for issues that affect:

- correctness and reliability: obvious bugs, dead ends, unreachable code,
  unsafe assumptions, weak error handling, edge cases, and race conditions
- security and safety: secret exposure, unsafe defaults, auth or permission
  drift, and unvalidated external inputs
- performance: inefficient hot paths or avoidable work, without speculative
  optimization or added complexity
- maintainability: duplicated logic, dead code, unclear ownership boundaries,
  weak abstractions, inconsistent naming, and style drift from nearby code
- test coverage: missing tests for important flows, regressions, edge cases,
  and error paths

## Wiring Checks

Verify important paths end to end:

- entry points call the intended modules
- public APIs are exported from expected locations
- imports resolve to the canonical implementation
- duplicate implementation paths do not compete
- CLI commands reach the correct handlers
- flags map to the correct config fields
- defaults are applied in one predictable place
- config schemas match actual config usage
- environment variables are documented and consumed consistently
- constructors and dependency injection receive required dependencies
- routers, handlers, controllers, jobs, and workers are registered
- workflows and docs call commands and scripts that exist
- tests exercise the same paths production code uses where practical
- errors surface clearly instead of being swallowed

If wiring is incomplete, fix the smallest responsible layer and update affected
references.

## Modularity Guidance

Improve modularity only when it reduces real inconsistency, duplication,
coupling, or wiring risk.

Prefer clear ownership, one canonical implementation per behavior, explicit
subsystem boundaries, small focused functions, stable shared helpers, and tests
close to the behavior they protect.

Avoid premature abstraction, aesthetic file moves, broad unrelated refactors,
new local frameworks, patterns that conflict with nearby code, or splitting
code so aggressively that wiring becomes harder to trace.
