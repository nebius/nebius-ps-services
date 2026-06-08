---
name: align
description: "Use for project-wide alignment or review: reconcile code, wiring, tests, CI, CLI/help, config, README/design docs, workflows, and project skills. Make minimal evidence-backed fixes for inconsistencies. Do not use for skill-folder-only metadata validation; use align-skill."
---

# Align

Use this skill for safe, end-to-end project alignment and code review.

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

Before proposing fixes, understand the design, data flow, control flow,
dependencies, ownership boundaries, and intended runtime behavior.

## Safety Rules

- Inspect before changing.
- Prefer minimal, targeted changes.
- Preserve intended behavior unless there is clear evidence of a bug,
  inconsistency, stale contract, or broken wiring.
- Only change code when the issue is clear and the fix is low risk.
- Do not perform broad rewrites for style alone.
- Do not rename public APIs, commands, flags, environment variables, files,
  modules, workflows, or documented behavior unless all affected references are
  updated.
- Do not change public APIs, data contracts, database schemas, authentication,
  authorization, external integrations, business rules, security, secrets
  handling, persistence, migrations, deployment, or production workflows unless
  there is a clear reason, the impact is documented, and focused validation is
  run.
- Do not delete code, files, configs, tests, or docs unless they are proven
  stale, unreachable, duplicate, or harmful.
- Prefer one canonical path over compatibility shims unless the user
  explicitly requests legacy support.
- Do not hide uncertainty with speculative docs, tests, comments, fallbacks, or
  silent defaults.
- Do not introduce new dependencies unless they are necessary and consistent
  with the project.
- If behavior changes, explain the before-and-after behavior.
- If business logic is unclear, leave it unchanged and document the
  uncertainty, risk, and recommended follow-up.
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
- dependency manifests, lockfiles, type definitions, migrations, API
  contracts, and generated interfaces
- generated code or artifacts, either by regenerating them correctly or
  leaving them untouched with an explanation
- README files, design documents, architecture notes, templates, changelogs,
  and developer-facing docs
- linting, formatting, and type-check configuration that affects correctness or
  maintainability

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

## Workflow

1. Inspect before editing.
   Map the project layout, runtime entry points, main libraries or services,
   data flow, control flow, dependencies, test strategy, lint or format
   tooling, and CI workflows. Read enough code and docs to understand intended
   behavior and how the pieces connect.
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
   Make the smallest effective change only when the issue is clear and the
   fix is low risk. When behavior changes or a bug is fixed, update the
   relevant tests, docs, examples, help text, and workflow assumptions so the
   project has one canonical story.
6. Verify.
   Run the focused tests, lint or format checks, type checks, build checks, and
   relevant `--help` or smoke commands for the touched surfaces. Broaden to the
   full suite when feasible. If the repo has a `CHANGELOG.md`, update the
   active unreleased section when behavior, commands, workflows, or
   user-facing docs changed. Check the final diff for unrelated files, secrets,
   credentials, private endpoints, and environment-specific values.
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
- Preserve intended logic unless the current logic is clearly incorrect or the
  user explicitly asked to change it.
- Verify wiring from entry point to implementation where relevant.
- Apply relevant available skills for concrete subtasks.
- Do not invent requirements.
- Do not assume missing behavior is correct.
- State clearly when something cannot be verified from the codebase.

## Prohibited Behaviors

Do not:

- perform a large rewrite without evidence
- change public contracts casually
- change business logic silently
- delete files because they look unused without checking references
- add fallback behavior that hides broken wiring
- update docs to match broken code when the code is clearly wrong
- update tests to match a bug
- optimize speculative paths at the cost of clarity
- ignore README, design documents, CI, workflow references, or help output
- assume generated code should be edited manually
- claim validation passed when it was not run
- claim complete alignment if important areas were not inspected

## Output Contract

When using this skill:

1. Make safe fixes first when the issue is clear.
2. Use this report shape for broad alignment/code-review requests:
   - Summary of findings
   - Critical issues that must be fixed
   - Bugs or dead-end flows found
   - Performance and optimization opportunities
   - Consistency and alignment issues
   - Tests that are missing or should be improved
   - Safe code changes made, including why each change is safe
   - Changes intentionally not made because the logic was unclear
   - Risks, assumptions, and open questions
   - Final recommendation
3. Include applicable skills used and validation actually run.
4. Call out anything still unverified or intentionally deferred.
