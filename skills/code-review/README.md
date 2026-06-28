# Code Review

`code-review` is an implicit review skill for strict implementation-quality
audits of the current branch, local diff, changed files, or provided patch. It
focuses on maintainability, abstraction quality, modularity, type boundaries,
file-size growth, spaghetti branches, and missed simplifications.

It is intentionally review-first. It should report high-conviction findings
and clearer structures before editing code, unless the user explicitly asks for
fixes.

## Files

- `SKILL.md`: runtime review contract, workflow, guardrails, and output shape.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.
- `references/quality-rubric.md`: strict implementation-quality rubric and
  approval bar.
- `evals/trigger-prompts.md`: should-trigger and should-not-trigger examples.

## Boundaries

- Use `code-review` for implementation-quality review of local code changes.
- Use `review-pr` for GitHub PR review, branch updates, conflict repair,
  checks, reviews, and merge readiness.
- Use `align` for project-wide repair across code, tests, docs, CLI, workflows,
  and configuration.
- Use `system-design-rules` for design-phase architecture decisions before
  implementation.
- Use `apply-security` for security-specific review and remediation.
