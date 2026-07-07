# Apply Security Trigger Prompts

Use these examples when reviewing or tuning implicit invocation behavior.

## Should Trigger

- Review this design for security risks before implementation.
- Harden this Terraform module and fix only safe low-risk findings.
- Check this GitHub Actions workflow for token, event-trust, and supply-chain
  risks.
- I changed Kubernetes manifests; review privilege, RBAC, exposure, and
  network-policy risks.
- Scan this diff for secret leakage, injection, unsafe deserialization, and
  auth regressions.
- During this implementation, call out security risks and patch obvious safe
  issues only.

## Should Not Trigger

- Review this branch for maintainability, bad abstractions, and file-size
  growth. Use `code-review`.
- Align the whole project after this behavior change. Use `align`.
- Create a GitHub Actions workflow that only changes naming or scheduling and
  has no permission, secret, dependency, release, or untrusted-code execution
  surface. Use `github-workflows`.
- Scaffold a Terraform module limited to local naming/examples with no IAM,
  public exposure, state, provider, network, or secret-handling decision. Use
  `terraform`.
- Lint shell scripts for style and formatting only. Use `linter` or
  `shell-scripting`.
- Commit and push this work. Use `commit-push`.

## Manual Runtime Check

When trigger precision matters, test these prompts in a fresh Codex thread
where the source skill is installed or discoverable:

- Should-trigger prompts should load `apply-security` or produce a security
  review response that follows the scan, plan, patch, or verify workflow.
- Should-not-trigger prompts should route to code-quality review, project
  alignment, workflow work, Terraform work, linting, shell scripting, or Git
  publication workflows.
- If the skill steals unrelated implementation, style, publication, or generic
  project-alignment tasks, narrow the front matter `description` before
  changing the workflow body.

Report runtime activation as observed only after this check. Otherwise report
trigger readiness from metadata and static validation only.
