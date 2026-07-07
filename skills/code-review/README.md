# Code Review

`code-review` is an implicit review skill for neutral, evidence-based code
reviews of the current local branch, local diff, changed files, modules,
repository areas, or provided patches. It focuses on bugs, regressions, test
gaps, reliability risks, security-adjacent issues, maintainability,
abstraction quality, modularity, type boundaries, file-size growth, spaghetti
branches, and missed simplifications.

It is intentionally review-first. It should report high-conviction findings
and clearer structures before editing code, unless the user explicitly asks for
fixes. Findings should stay evidence-based, proportional, and neutral.

## Files

- `SKILL.md`: runtime review contract, workflow, guardrails, and output shape.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.
- `references/quality-rubric.md`: neutral review rubric, severity model,
  blocking conditions, strict implementation-quality standards, and approval
  bar.
- `evals/trigger-prompts.md`: should-trigger and should-not-trigger examples.

## Boundaries

- Use `code-review` for neutral findings-first review of local code changes,
  modules, repository areas, or provided patches.
- Use `review-pr` for GitHub PR review by number, URL, or current branch,
  branch updates, conflict repair, checks, reviews, and merge readiness.
- Use `align` for project-wide repair across code, tests, docs, CLI, workflows,
  and configuration.
- Use `system-design-rules` for design-phase architecture decisions before
  implementation.
- Use `apply-security` for security-specific scans, threat modeling, and
  remediation.
