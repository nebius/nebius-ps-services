---
name: code-review
description: "Use for strict implementation-quality code review of the current branch, local diff, changed files, or provided patch: audit maintainability, abstraction quality, modularity, type boundaries, file size, spaghetti growth, ad-hoc conditionals, codebase health, and missed structural simplifications. Use when the user asks for a deep code quality audit, maintainability review, implementation review, or rigorous code-review findings. Do not use for GitHub PR readiness or branch updates; use review-pr for that."
---

# Code Review

## Purpose

Use this skill for unusually strict code reviews focused on implementation
quality, maintainability, abstraction quality, and codebase health. The goal is
not to rubber-stamp working code; it is to find structural regressions and
clear opportunities to make the implementation dramatically simpler while
preserving behavior.

## Use This Skill For

- Reviewing the current branch, local diff, changed files, or a provided patch
  for implementation quality.
- Auditing a change for maintainability, abstraction quality, modularity,
  file-size growth, type boundaries, code ownership, and long-term readability.
- Finding "code judo" opportunities: restructures that delete branches,
  helpers, modes, wrappers, or special cases instead of rearranging complexity.
- Challenging implementations that technically work but make the codebase more
  tangled, indirect, cast-heavy, or harder to reason about.

## When Not To Use

- Do not use for GitHub PR checkout, branch update, conflict repair, checks,
  reviews, or merge readiness; use `review-pr`.
- Do not use for project-wide changed-scope repair across tests, docs, CLI,
  workflows, and configuration; use `align`.
- Do not use for security-specific remediation; use `apply-security`.
- Do not use for design-phase architecture review before code exists; use
  `system-design-rules`.
- Do not edit code by default. This is review-first unless the user explicitly
  asks to implement the recommended fixes.

## Inputs

- Current working tree, branch diff, explicit file paths, patch text, or review
  target from the user.
- The base branch or comparison point when known. If absent, infer the most
  reasonable local base from repository context and state the assumption.
- Existing project instructions, local architecture conventions, tests, docs,
  and nearby implementations needed to judge whether a structure fits.

## Required Reads

Read `references/quality-rubric.md` before reviewing meaningful code changes.
Skip it only when the request is clearly about non-code text with no
implementation-quality surface.

For broad or long-running reviews, use `global-context-management` for task
state, bounded read-only sidecar exploration when authorized and available,
focused validation planning, and near-final risk review.

## Workflow

1. Identify the review target and comparison base. Prefer the actual local diff
   or provided patch over assumptions.
2. Map the changed surface with targeted reads: changed files, nearby owners,
   existing helpers, canonical modules, tests, and docs that define the local
   contract.
3. Review behavior enough to avoid false structural advice. Do not propose a
   refactor that changes semantics unless the user asked for behavior changes.
4. Apply the strict rubric:
   - structural code-quality regressions
   - missed dramatic simplifications
   - spaghetti or branching-complexity growth
   - boundary, abstraction, ownership, and type-contract problems
   - file-size and decomposition concerns
   - maintainability and legibility issues
5. Prefer high-conviction findings over a long list of nits. If a structural
   issue exists, lead with it and skip cosmetic comments unless they compound
   the same maintainability problem.
6. For each finding, explain the simpler shape. Name the existing helper,
   module, boundary, model, dispatcher, pure function, or deletion path that
   would make the code easier to reason about.
7. State the approval bar directly: approved, blocked by structural issues, or
   no blocking issue found with residual risks.

## Review Standards

- Be ambitious about simplification. Look for ways to delete complexity, not
  just move it around.
- Treat ad-hoc conditionals in unrelated flows as design problems unless the
  codebase already uses that pattern intentionally.
- Treat thin wrappers, identity abstractions, pass-through helpers, and generic
  magic as suspicious until they clearly reduce reader burden.
- Push logic toward the canonical layer that owns the concept. Do not normalize
  feature logic leaking through shared APIs or unrelated modules.
- Challenge `any`, `unknown`, casts, nullable modes, optional flags, and silent
  fallbacks when they obscure the invariant.
- Treat a PR or branch pushing a file from below 1000 lines to above 1000 lines
  as a presumptive decomposition concern.
- Flag unnecessary sequential orchestration or partial-update logic when a
  clearer parallel or atomic structure is obvious and behavior can remain the
  same.

## Guardrails

- Do not invent large rewrites from taste. Findings must point to current code,
  local patterns, or a concrete simpler structure.
- Do not approve merely because tests pass or behavior appears correct.
- Do not flood the user with low-value naming, formatting, or style nits when
  larger structural issues exist.
- Do not demand abstraction for its own sake. Prefer direct, boring code when
  it removes indirection.
- Do not preserve legacy compatibility layers, aliases, deprecated branches, or
  compatibility wrappers unless the user explicitly requests them.
- Do not expose secrets, private endpoints, customer data, broad raw logs, or
  proprietary internal material in review output or reusable skill sources.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings back
into this skill's local source materials before completion when the current task
contract allows source edits. Update the narrowest appropriate surface:
`SKILL.md` for runtime rules, `references/` for detailed guidance, `assets/`
for reusable templates, `scripts/` for deterministic helpers, and README or
changelog entries for human-facing or release-note updates.

If the current task is explicitly read-only/report-only, or source writes are
outside this skill's task contract, do not edit skill sources; report the
skipped source update instead.

Do not capture secrets, private URLs, customer data, raw logs, one-off local
state, or unverified/vendor-specific claims. If a useful learning is not safe,
not evidence-backed, or outside this skill's scope, report that it was skipped.

## Output Contract

Return findings first, ordered by severity and confidence. For each finding,
include:

- file and line reference when available
- why the current structure hurts maintainability
- the simpler shape or ownership boundary to consider
- whether it is a blocker or a strong recommendation

Then include:

- approval bar: approved, blocked, or no blocking issue found
- brief summary of the changed surface reviewed
- validation reviewed or recommended
- residual risks and open questions

## References

- Read `references/quality-rubric.md` for the detailed strict-review rubric,
  blocker guidance, and review phrases.
- Use `evals/trigger-prompts.md` when tuning or reviewing implicit invocation
  behavior.
