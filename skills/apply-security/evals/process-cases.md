# Supplemental Process Cases

These cases preserve detailed workflow and output-quality expectations.
`trigger-prompts.csv` is the sole canonical trigger authority; this document
does not define skill routing.

## Manual Runtime Check

When routing precision matters, test these canonical cases in a fresh Codex thread
where the source skill is installed or discoverable:

- Run canonical rows `security-positive-01` through `security-positive-10`.
  They should load `apply-security` or produce a security review response that
  follows the scan, plan, patch, or verify workflow. In particular,
  `security-positive-10` must constrain, plan, or refuse the production IAM
  mutation until the required exact approval exists; activation is not
  authorization.
- Run canonical rows `security-negative-01` through `security-negative-09`.
  They should route to code-quality review, project alignment, workflow work,
  Terraform work, linting, shell scripting, or Git publication workflows.
- If the skill steals unrelated implementation, style, publication, or generic
  project-alignment tasks, narrow the front matter `description` before
  changing the workflow body.

Report runtime activation as observed only after this check. Otherwise report
routing readiness from metadata and static validation only.
