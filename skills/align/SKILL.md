---
name: align
description: Review a project end to end and fix incorrect, inconsistent, incomplete, or misleading code, tests, CI, CLI, documentation, and help output. Use when the user wants the codebase aligned across implementation, tests, workflows, commands, flags, examples, and docs instead of only receiving an audit report.
---

# Align

Use this skill for end-to-end project alignment work. Inspect the codebase
broadly enough to understand how the pieces fit together before changing
anything.

## Use This Skill For

- Repo-wide or subsystem-wide correctness and alignment passes
- Requests to reconcile code, tests, CI, CLI behavior, docs, and `--help`
  output
- Cleanup of broken flows, dead ends, stale examples, weak tests, or
  misleading wording
- "Fix anything inconsistent or incomplete" requests where the agent should
  implement the fixes, not only report them

## Scope Checklist

Review and align:

- source code and config files
- tests under `tests/` plus adjacent fixtures or snapshots
- CI workflows and automation entry points
- CLI commands, flags, defaults, examples, and `--help` output
- README plus other user-facing or developer-facing docs
- lint, formatting, and wording issues that affect correctness or clarity

## Workflow

1. Inspect before editing.
   Map the project layout, runtime entry points, main libraries or services,
   test strategy, lint or format tooling, and CI workflows. Read enough code
   and docs to understand intended behavior and how the pieces connect.
2. Establish the actual contract.
   Compare implementation against tests, CLI help, examples, workflows, and
   documentation. Treat mismatches as evidence to resolve, not as proof that
   any one surface is automatically correct.
3. Fix the highest-signal problems first.
   Prioritize:
   - bugs and incorrect behavior
   - dead ends, broken flows, and missing paths
   - inconsistencies across code, tests, CI, CLI, and docs
   - missing or weak test coverage
   - lint, formatting, and wording cleanup
4. Update every affected surface in the same turn.
   When behavior changes or a bug is fixed, update the relevant tests, docs,
   examples, help text, and workflow assumptions so the project has one
   canonical story.
5. Verify.
   Run the focused tests, lint or format checks, and the relevant `--help` or
   smoke commands for the touched surfaces. If the repo has a `CHANGELOG.md`,
   update the active unreleased section when behavior or user-facing workflows
   changed.
6. Report remaining uncertainty clearly.
   If something cannot be verified from the codebase or local tooling, say
   exactly what could not be confirmed and why. Do not guess.

## Required Behaviors

- Review and fix issues. Do not stop at a discrepancy list unless the user
  explicitly asks for audit-only output.
- Update tests to match intended behavior.
- Update documentation and help text to match the implementation.
- Keep commands, flags, examples, and docs aligned.
- Remove dead ends, broken flows, contradictions, and obvious bugs.
- Preserve intended behavior unless there is a clear bug or inconsistency.
- Do not invent requirements.
- Do not assume missing behavior is correct.
- State clearly when something cannot be verified from the codebase.

## Guardrails

- Prefer a single canonical code path over compatibility shims unless the user
  explicitly requests legacy support.
- Do not paper over uncertainty with speculative docs or tests.
- Do not leave failing or stale examples in docs or help output after changing
  behavior.
- When a mismatch exists, use the available codebase evidence to determine the
  intended contract and align all affected surfaces around it.

## Output Contract

When using this skill:

1. Make the fixes.
2. Summarize the concrete changes.
3. Report validation actually run.
4. Call out anything still unverified or intentionally deferred.
