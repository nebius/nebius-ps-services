---
name: align
description: Safely review and align a project end to end across code, wiring, modules, tests, CI, CLI, documentation, README, design documents, workflows, and applicable project skills. Use when the user wants implementation-level fixes that make the repository internally consistent without harmful rewrites or speculative changes.
---

# Align

Use this skill for safe, end-to-end project alignment.

The goal is to make the repository consistent, correctly wired, modular where
reasonable, and aligned with the behavior described by its README, design
documents, workflows, tests, CLI/help output, and existing implementation.

This is not a broad rewrite skill. Treat it as an audit-first, evidence-driven
alignment skill that makes minimal, focused, reviewable changes.

## Use This Skill For

- Repo-wide, service-wide, package-wide, or subsystem-wide consistency passes
- Requests to reconcile implementation, tests, CI, CLI behavior, config, docs,
  examples, and `--help` output
- Broken wiring, stale imports, duplicate paths, missing registration, flawed
  command flow, or mismatched config/schema behavior
- Cleanup of incorrect, incomplete, stale, misleading, or contradictory
  project surfaces
- Modularity improvements that reduce real inconsistency, duplication,
  coupling, or wiring risk
- "Fix anything inconsistent or incomplete" requests where the agent should
  implement safe fixes, not only report them

## Core Principle

First understand the project contract, then align affected surfaces to that
contract.

The contract must come from repository evidence: README files, design or
architecture documents, source code, tests, fixtures, CI workflows, Makefiles
or task runners, CLI definitions, help output, config schemas, deployment
automation, examples, templates, changelogs, and nearby conventions.

Do not invent requirements. Do not assume docs, tests, or implementation are
automatically correct. Use current, nearby, working code as stronger evidence
than stale prose or isolated examples.

## Safety Rules

- Inspect before changing.
- Prefer minimal, targeted changes.
- Preserve intended behavior unless there is clear evidence of a bug,
  inconsistency, stale contract, or broken wiring.
- Do not perform broad rewrites for style alone.
- Do not rename public APIs, commands, flags, environment variables, files,
  modules, workflows, or documented behavior unless all affected references are
  updated.
- Do not delete code, files, configs, tests, or docs unless they are proven
  stale, unreachable, duplicate, or harmful.
- Prefer one canonical path over compatibility shims unless the user
  explicitly requests legacy support.
- Do not hide uncertainty with speculative docs, tests, comments, fallbacks, or
  silent defaults.
- Do not change security, authentication, authorization, secrets handling,
  persistence, migrations, deployment, or production workflows without strong
  repository evidence and focused validation.
- Keep changes easy to review.

When uncertain, report the uncertainty instead of changing behavior.

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
- generated code or artifacts, either by regenerating them correctly or
  leaving them untouched with an explanation
- README files, design documents, architecture notes, templates, changelogs,
  and developer-facing docs
- linting, formatting, and type-check configuration that affects correctness or
  maintainability

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

## Workflow

1. Inspect before editing.
   Map the project layout, runtime entry points, main libraries or services,
   test strategy, lint or format tooling, and CI workflows. Read enough code
   and docs to understand intended behavior and how the pieces connect.
2. Check applicable skills.
   Use additional skills only when their metadata clearly matches a concrete
   part of the task, such as workflow, Helm, shell, lint, Terraform, Python,
   documentation, spreadsheet, slide, PDF, or repo-local skill work. Read the
   relevant `SKILL.md`, keep scope narrow, and do not let another skill
   override these safety rules unless the user explicitly asked for that
   behavior.
3. Establish the actual contract.
   Compare implementation against tests, CLI help, examples, workflows, and
   documentation. Treat mismatches as evidence to resolve, not as proof that
   any one surface is automatically correct.
4. Identify and prioritize alignment gaps.
   Prioritize:
   - bugs and incorrect behavior
   - broken runtime or workflow paths
   - missing or inconsistent configuration flow
   - flawed wiring or duplicate implementations
   - stale tests that hide real behavior
   - missing tests for repaired behavior
   - stale docs, examples, help output, or changelog entries
   - modularity issues that directly cause maintenance risk
5. Make focused changes.
   When behavior changes or a bug is fixed, update the relevant tests, docs,
   examples, help text, and workflow assumptions so the project has one
   canonical story.
6. Verify.
   Run the focused tests, lint or format checks, type checks, build checks, and
   relevant `--help` or smoke commands for the touched surfaces. Broaden to the
   full suite when feasible. If the repo has a `CHANGELOG.md`, update the
   active unreleased section when behavior, commands, workflows, or
   user-facing docs changed.
7. Report remaining uncertainty clearly.
   If something cannot be verified from the codebase or local tooling, say
   exactly what could not be confirmed and why. Do not guess.

## Required Behaviors

- Review and fix issues. Do not stop at a discrepancy list unless the user
  explicitly asks for audit-only output.
- Update tests to match intended behavior.
- Update documentation and help text to match the implementation.
- Keep commands, flags, examples, and docs aligned.
- Remove dead ends, broken flows, contradictions, and obvious bugs.
- Verify wiring from entry point to implementation where relevant.
- Apply relevant available skills for concrete subtasks.
- Preserve intended behavior unless there is a clear bug or inconsistency.
- Do not invent requirements.
- Do not assume missing behavior is correct.
- State clearly when something cannot be verified from the codebase.

## Prohibited Behaviors

Do not:

- perform a large rewrite without evidence
- change public contracts casually
- delete files because they look unused without checking references
- add fallback behavior that hides broken wiring
- update docs to match broken code when the code is clearly wrong
- update tests to match a bug
- ignore README, design documents, CI, workflow references, or help output
- assume generated code should be edited manually
- claim validation passed when it was not run
- claim complete alignment if important areas were not inspected

## Output Contract

When using this skill:

1. Make the fixes.
2. Summarize the concrete changes.
3. Call out wiring gaps fixed and modularity improvements made, if any.
4. Note README, design, workflow, test, CLI, config, or docs alignment
   performed.
5. List applicable skills used.
6. Report validation actually run and the result.
7. Call out anything still unverified or intentionally deferred.
