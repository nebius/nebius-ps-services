---
name: code-review
description: "Use for neutral, evidence-based code review of the current local branch, local diff, changed files, module, repository area, or provided patch: find bugs, regressions, test gaps, reliability risks, security-adjacent issues, maintainability problems, abstraction drift, type-boundary problems, file-size growth, spaghetti branches, and missed structural simplifications. Use when the user asks for code review, code quality audit, maintainability review, implementation review, or rigorous findings-first feedback on code. Do not use for GitHub PR review by number, URL, or current branch; PR readiness; branch updates; dedicated security scans; whole-project alignment; or design-phase architecture review."
---

# Code Review

## Purpose

Use this skill for neutral, evidence-based code reviews of local code,
provided patches, changed files, modules, or repository areas. The review
should prioritize bugs, regressions, meaningful test gaps, reliability and
security-adjacent risks, maintainability, abstraction quality, and codebase
health.

The goal is not to rubber-stamp working code or enforce personal taste. It is
to identify concrete risks, separate blockers from non-blocking improvements,
and find structural simplifications that preserve behavior while making the
implementation easier to reason about.

## Use This Skill For

- Reviewing the current local branch, local diff, changed files, module,
  repository area, or provided patch for implementation quality and bug risk.
- Auditing a change for maintainability, abstraction quality, modularity,
  file-size growth, type boundaries, code ownership, and long-term readability.
- Finding likely correctness regressions, realistic edge-case failures,
  meaningful missing tests, reliability problems, and operational risk.
- Reviewing security-sensitive code paths as part of an implementation review
  when the user did not ask for a dedicated security scan or remediation pass.
- Finding "code judo" opportunities: restructures that delete branches,
  helpers, modes, wrappers, or special cases instead of rearranging complexity.
- Challenging implementations that technically work but make the codebase more
  tangled, indirect, cast-heavy, or harder to reason about.

## When Not To Use

- Do not use for GitHub PR checkout, branch update, conflict repair, checks,
  reviews, or merge readiness; use `review-pr`.
- Do not use for project-wide changed-scope repair across tests, docs, CLI,
  workflows, and configuration; use `align`.
- Do not use for security-specific scans, threat modeling, or remediation; use
  `apply-security`.
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
It owns the detailed neutral-review rubric, severity model, blocking
conditions, review modes, and strict structural-quality guidance. Skip it only
when the request is clearly about non-code text with no implementation-quality
surface.

For broad or long-running reviews, use `global-context-management` for task
state, bounded read-only sidecar exploration when authorized and available,
focused validation planning, and near-final risk review.

## Workflow

1. Identify the review target, review mode, and comparison base. Prefer the
   actual local diff or provided patch over assumptions.
2. Map the changed surface with targeted reads: changed files, nearby owners,
   existing helpers, canonical modules, tests, and docs that define the local
   contract.
3. Review behavior enough to avoid false structural advice. Trace callers,
   downstream consumers, main paths, edge cases, error paths, retries,
   boundary values, partial failures, and rollback or cleanup paths where they
   are relevant.
4. Apply the risk rubric:
   - likely bugs, regressions, and broken contracts
   - missing tests for changed behavior and realistic failure modes
   - security-adjacent risks visible in the implementation
   - reliability, state, concurrency, and operational risks
   - structural code-quality regressions
   - missed dramatic simplifications
   - spaghetti or branching-complexity growth
   - boundary, abstraction, ownership, and type-contract problems
   - file-size and decomposition concerns
   - maintainability and legibility issues
5. Prefer high-conviction findings over a long list of nits. If a bug,
   regression, data risk, security-adjacent issue, or structural problem
   exists, lead with it and skip cosmetic comments unless they compound the
   same risk.
6. For each finding, include evidence, impact, confidence, and the smallest
   remediation that would resolve the risk. For structural findings, explain
   the simpler shape and name the existing helper, module, boundary, model,
   dispatcher, pure function, or deletion path that would make the code easier
   to reason about.
7. State the review decision directly: approve, request changes, needs owner
   review, blocked by missing context, or no blocking issue found with
   residual risks.

## Review Standards

- Be ambitious about simplification. Look for ways to delete complexity, not
  just move it around.
- Be neutral and evidence-based. Critique code behavior and risk, not the
  author or the author's intent.
- Treat tests as evidence, not proof. Passing tests reduce uncertainty only
  when they cover the changed behavior and important failure modes.
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
- Ask for domain, security, data, or operations owner review when the change
  crosses a boundary that cannot be judged from code evidence alone.

## Guardrails

- Do not invent large rewrites from taste. Findings must point to current code,
  local patterns, or a concrete simpler structure.
- Do not approve merely because tests pass or behavior appears correct.
- Do not claim code is secure, correct, reliable, production-ready, or
  bug-free beyond the evidence reviewed.
- Do not assume dependency, framework, runtime, or language behavior without
  repository evidence or authoritative documentation when that assumption is
  material to a finding.
- Do not run migrations, contact external services, install packages, rewrite
  history, delete files, or change configuration unless the user explicitly
  asks for that action.
- Do not flood the user with low-value naming, formatting, or style nits when
  larger structural issues exist.
- Do not demand abstraction for its own sake. Prefer direct, boring code when
  it removes indirection.
- Do not preserve legacy compatibility layers, aliases, deprecated branches, or
  compatibility wrappers unless the user explicitly requests them.
- Do not expose secrets, private endpoints, customer data, broad raw logs, or
  proprietary internal material in review output or reusable skill sources.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return findings first, ordered by severity and confidence. If there are no
findings, say that directly and then report residual risk and verification
gaps.

For each finding, include:

- file and line reference when available
- severity and whether it blocks merge or release
- root cause and evidence
- impact on users, operators, data, security, reliability, or maintainability
- smallest practical remediation or owner decision needed
- verification that should prove the fix

Then include:

- review decision: approve, request changes, needs owner review, blocked by
  missing context, or no blocking issue found with residual risks
- brief summary of the changed surface reviewed
- validation performed, reviewed, or recommended
- residual risks and open questions

## References

- Read `references/quality-rubric.md` for the detailed neutral-review rubric,
  blocker guidance, strict structural standards, and review phrases.
- Use `evals/trigger-prompts.md` when tuning or reviewing implicit invocation
  behavior.
