# Code Review

`code-review` is a neutral, evidence-based review skill for the current local
branch, local diff, changed files, modules, repository areas, or provided
patches. It focuses on bugs, regressions, test gaps, reliability risks,
security-adjacent issues, maintainability, abstraction quality, modularity,
type boundaries, file-size growth, spaghetti branches, and missed
simplifications.

Every run is findings-first. A direct standalone `$code-review` invocation
then fixes only safe in-scope findings, validates each fix with focused
repository-native red-before/green-after proof, reviews its touched diff, and
returns the complete prioritized ledger. An already-green or unrelated check
cannot authorize a fix. All modes use no-write/no-cache validation settings
where available and remove exact task-created artifacts before reporting.
Implicit selection, nested workflow use, and explicit no-write requests such as
review-only, audit-only, or report-only stay non-mutating and restore the exact
initial worktree state. Priority and auto-fix safety remain independent.

## Files

- `SKILL.md`: runtime review contract, workflow, guardrails, and output shape.
- `agents/openai.yaml`: UI metadata and implicit invocation policy.
- `references/quality-rubric.md`: neutral review rubric, severity model,
  blocking conditions, focused-fix validation, strict implementation-quality
  standards, and approval bar.
- `evals/trigger-prompts.csv`: canonical should-trigger and should-not-trigger examples.
- `evals/process-cases.md`: supplemental invocation and runtime-check cases.
- `scripts/test_code_review_contract.py`: deterministic contract checks for
  invocation modes, remediation safety, focused validation, reporting, and
  isolated installation.

## Boundaries

- Use `code-review` for neutral findings-first review of local code changes,
  modules, repository areas, or provided patches.
- Use `review-pr` for GitHub PR review by number, URL, or current branch,
  branch updates, conflict repair, checks, reviews, and merge readiness.
- Use `align` for project-wide repair across code, tests, docs, CLI, workflows,
  and configuration. It is an explicit outer workflow: `code-review` itself
  never resolves, loads, or invokes it, while the caller remains responsible
  for any separate repository policy requiring a post-change alignment pass.
- Treat focused fix proof as narrower than project alignment. A direct review
  reruns the finding-specific proof, affected repository-native checks, scoped
  static checks when available, `git diff --check`, and a final review of its
  touched diff.
- Keep `align`'s nested `code-review` lane report-only. `align` owns any
  separately authorized cross-surface remediation.
- Use `system-design-rules` for design-phase architecture decisions before
  implementation.
- Use `apply-security` for security-specific scans, threat modeling, and
  remediation.
