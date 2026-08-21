# Supplemental Process Cases

These cases preserve detailed workflow and output-quality expectations.
`trigger-prompts.csv` is the sole canonical trigger authority; this document
does not define skill routing.

Use the canonical row ranges below when reviewing `system-design-rules` trigger
behavior. Static validation does not prove runtime activation.

## Process Assertions

- Positive rows must evaluate material design choices against ownership,
  interfaces, data, reliability, security, observability, scaling, cost,
  rollout, and operational tradeoffs.
- Results must identify risks, alternatives, and decision conditions without
  taking over complete solution design or implementation planning.
- Negative rows must route to design, research, code review, stack selection,
  implementation, SDLC, PR review, Terraform, or brainstorming as requested.

## Manual Runtime Check

When routing precision matters, exercise `design-rules-positive-01` through
`design-rules-positive-09` and `design-rules-negative-01` through
`design-rules-negative-09` in a fresh Codex thread where the source skill is
installed or discoverable. If the skill steals complete design,
implementation, SDLC, review, stack-selection, Terraform, or brainstorming
tasks, narrow the front matter `description` before changing the workflow body.

Report runtime activation as observed only after this check. Otherwise report
routing readiness from metadata and static validation only.
