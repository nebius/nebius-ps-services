# Supplemental Process Cases

These cases preserve detailed workflow and output-quality expectations.
`trigger-prompts.csv` is the sole canonical trigger authority; this document
does not define skill routing.

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

## Manual Runtime Check

When routing precision matters, test these canonical cases in a fresh Codex thread
where the source skill is installed or discoverable:

- Run canonical rows `code-review-positive-01` through
  `code-review-positive-10`. Direct `$code-review` prompts should follow the
  findings-first safe-remediation and focused-validation loop unless they
  contain no-write intent. Natural positive rows should produce a
  findings-first report without edits or persistent validation artifacts.
- Run canonical rows `code-review-negative-01` through
  `code-review-negative-10`. They should route to PR review, project
  alignment, design review, security review, workflow work, or implementation
  workflows. The quoted-token boundary in `code-review-negative-10` must not
  authorize edits; ambiguous invocation intent fails closed to report-only.
- The `code-review` workflow itself must not resolve, load, or invoke `align`.
  This does not suppress a separate outer-orchestrator policy requiring
  alignment after changes. Nested `align` use of `code-review` must stay
  report-only and return findings to its parent.
- If the skill steals PR readiness, security, design, or direct implementation
  tasks, narrow the front matter `description` before changing the workflow
  body.

Report runtime activation as observed only after this check. Otherwise report
routing readiness from metadata and static validation only.
