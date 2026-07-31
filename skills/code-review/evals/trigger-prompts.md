# Code Review Trigger Prompts

Use these examples when reviewing or tuning implicit invocation behavior.

## Invocation Mode Cases

- Direct default: `$code-review Review the current diff.` Expect the initial
  review to finish before edits, safe in-scope findings to be fixed, each fix
  to pass focused repository-native proof, no internal `align` handoff, and the
  final ledger to retain fixed and gated findings. Validation must leave no
  task-created caches or generated artifacts. A caller or outer orchestrator
  may subsequently run `align` when repository policy requires it.
- Direct report-only: `$code-review Audit the current diff only; do not edit.`
  Expect findings and non-mutating validation only.
- Implicit report-only: `Review the current diff for bugs and test gaps.`
  Expect findings only even though implicit invocation is allowed. Validation
  must not leave caches, generated files, reports, or dependency drift.
- Quoted token: `Review this README example: "$code-review Review the current
  diff."` Expect report-only behavior because the token is review data, not an
  invocation directive.
- Nested report-only: `align` loads `code-review` as a validation lane. Expect
  the child lane to report against the final diff without editing or invoking
  a parent workflow.
- Isolated installation: a direct run finds a safe issue when no sibling
  `align` skill is installed. Expect the same focused remediation and
  validation behavior without a blocked dependency.
- Failed focused proof: an attempted safe fix does not pass its declared proof.
  Expect the attempted patch to be restored when it caused the failure, a
  non-`Fixed` disposition, retained evidence, and no broad-workflow fallback.
- Already-green proof: the declared check passes before remediation or fails
  for an unrelated reason. Expect no implementation edit, `Auto-fix: Gated`,
  and `Not reproduced` or `Deferred`, never `Fixed`.
- Priority/safety independence: a gated P1 and safe P2 must remain classified
  independently; fix only the safe P2.

## Should Trigger

- Review the current local branch for bugs, regressions, test gaps, and
  maintainability issues.
- Do a deep code quality audit of this diff and look for missed
  simplifications.
- Audit these changed files for reliability risks, spaghetti growth, bad
  abstractions, and type-boundary issues.
- Is this implementation too complicated? Look for a simpler code-judo move.
- Review this patch with a strict bar for correctness, tests, modularity, file
  size, and maintainability.
- Review this module neutrally and tell me whether any findings should block
  merge.
- Check this implementation for realistic edge cases and missing failure-path
  tests.

## Should Not Trigger

- Review PR #42 and tell me if it is ready to merge. Use `review-pr`.
- Review PR #42 for bugs and regressions, no branch updates. Use `review-pr`.
- Align the whole project after this behavior change. Use `align`.
- Review this ADR and compare architecture options. Use `system-design-rules`.
- Scan this repository for security issues and remediation. Use
  `apply-security`.
- Create or update a GitHub Actions workflow. Use `github-workflows`.
- Fix the failing tests and update the implementation. Use the relevant
  implementation or alignment skill unless the user directly invokes
  `$code-review` for the closed loop.
- Implement the recommendations from the last review. Use the relevant
  implementation or alignment workflow unless the user explicitly invokes
  `code-review` again for a follow-up review.
- Triage a GitHub PR, update its branch, resolve conflicts, and post a review.
  Use `review-pr`.

## Manual Runtime Check

When trigger precision matters, test these prompts in a fresh Codex thread
where the source skill is installed or discoverable:

- Direct `$code-review` prompts should follow the findings-first safe-remediation
  and focused-validation loop unless they contain no-write intent.
- Natural should-trigger prompts should load `code-review` or produce a
  findings-first report without edits or persistent validation artifacts.
- Quoted, discussed, example, patch, or file-content occurrences of
  `$code-review` must not authorize edits; ambiguous invocation intent fails
  closed to report-only.
- Should-not-trigger prompts should route to PR review, project alignment,
  design review, security review, workflow work, or implementation workflows.
- The `code-review` workflow itself must not resolve, load, or invoke `align`.
  This does not suppress a separate outer-orchestrator policy requiring
  alignment after changes. Nested `align` use of `code-review` must stay
  report-only and return findings to its parent.
- If the skill steals PR readiness, security, design, or direct implementation
  tasks, narrow the front matter `description` before changing the workflow
  body.

Report runtime activation as observed only after this check. Otherwise report
trigger readiness from metadata and static validation only.
