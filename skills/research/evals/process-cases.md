# Supplemental Process Cases

These cases preserve detailed workflow and output-quality expectations.
`trigger-prompts.csv` is the sole canonical trigger authority; this document
does not define skill routing.

Use the canonical row ranges below when reviewing `research` routing behavior.
Static validation does not prove runtime activation.

## Process Assertions

- Positive rows must produce focused senior-engineer due diligence with
  provenance labels, internals, operational behavior, limitations,
  alternatives, and actionable recommendations.
- Organization-specific questions must search available internal context first
  and verify technical claims against current authoritative external sources.
- The skill must remain bounded to one focal topic rather than taking ownership
  of application-stack, AI-stack, design, implementation, checklist-review, or
  SDLC work.
- Negative rows must route to the named adjacent owner, with research returning
  only for a bounded disputed or unfamiliar claim.

## Manual Runtime Check

When routing precision matters, exercise `research-positive-01` through
`research-positive-11` and `research-negative-01` through
`research-negative-07` in a fresh Codex thread where the source skill is
installed or discoverable. If `research` steals ideation, design, stack
selection, implementation, checklist review, or SDLC tasks, narrow the front
matter `description` before changing the workflow body.

Report runtime activation as observed only after this check. Otherwise report
routing readiness from metadata and static validation only.
