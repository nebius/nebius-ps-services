# Supplemental Process Cases

These cases preserve detailed workflow and output-quality expectations.
`trigger-prompts.csv` is the sole canonical trigger authority; this document
does not define skill routing.

Use the canonical row ranges below when reviewing `design` routing behavior.
Static validation does not prove runtime activation.

## Process Assertions

- Positive rows must inspect the relevant system, compare viable options,
  integrate bounded research and specialist decisions, and end with a concrete
  implementation-plan handoff.
- Approved application, AI subsystem, or AI stack layers must not be reopened;
  only genuinely undecided layers route through `app-stack`,
  `ai-agent-design`, or `ai-stack`.
- AI subsystem work follows `design` to `ai-agent-design` to `ai-stack` exactly
  once. AI-enabled capabilities must be classified as deterministic code,
  direct calls, deterministic workflows, or agents; unknown-count code-owned
  loops remain deterministic, while durability is evaluated independently from
  agentic control flow.
- A troubleshooting handoff may enter design only for a proven remediation
  that changes a system contract; difficult private implementation work remains
  with `troubleshoot`.
- Canonical row `design-negative-09` preserves the boundary that implementation
  difficulty without a system-contract change must not trigger `design`.
- Negative rows must route to brainstorming, checklist review, stack selection,
  Agentic SDLC, implementation, scaffolding, troubleshooting, or PR workflows.

## Manual Runtime Check

When routing precision matters, exercise `design-positive-01` through
`design-positive-14` and `design-negative-01` through `design-negative-11` in a
fresh Codex thread where the source skill is installed or discoverable. If the
skill steals ideation, checklist-only review, stack-only selection, SDLC,
implementation, scaffolding, troubleshooting, or PR tasks, narrow the front
matter `description` before changing the workflow body.

Report runtime activation as observed only after this check. Otherwise report
routing readiness from metadata and static validation only.
