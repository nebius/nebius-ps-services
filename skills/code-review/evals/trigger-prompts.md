# Code Review Trigger Prompts

Use these examples when reviewing or tuning implicit invocation behavior.

## Should Trigger

- Review the current branch for implementation quality and maintainability.
- Do a deep code quality audit of this diff and look for missed simplifications.
- Audit these changed files for spaghetti growth, bad abstractions, and type
  boundary issues.
- Is this implementation too complicated? Look for a simpler code-judo move.
- Review this patch with a strict bar for modularity, file size, and
  maintainability.

## Should Not Trigger

- Review PR #42 and tell me if it is ready to merge. Use `review-pr`.
- Align the whole project after this behavior change. Use `align`.
- Review this ADR and compare architecture options. Use `system-design-rules`.
- Scan this repository for security issues and remediation. Use
  `apply-security`.
- Create or update a GitHub Actions workflow. Use `github-workflows`.
- Fix the failing tests and update the implementation. Use the relevant
  implementation or alignment skill unless the user asks for review first.

## Manual Runtime Check

When trigger precision matters, test these prompts in a fresh Codex thread
where the source skill is installed or discoverable:

- Should-trigger prompts should load `code-review` or produce a findings-first
  response that follows the strict implementation-quality review workflow.
- Should-not-trigger prompts should route to PR review, project alignment,
  design review, security review, workflow work, or implementation workflows.
- If the skill steals PR readiness, security, design, or direct implementation
  tasks, narrow the front matter `description` before changing the workflow
  body.

Report runtime activation as observed only after this check. Otherwise report
trigger readiness from metadata and static validation only.
