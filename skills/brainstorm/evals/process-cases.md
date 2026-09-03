# Supplemental Process Cases

These cases preserve detailed workflow and output-quality expectations.
`trigger-prompts.csv` is the sole canonical trigger authority; this document
does not define skill routing.

Use the canonical row ranges below when reviewing `brainstorm` trigger
behavior. Static validation does not prove runtime activation.

## Process Assertions

- Positive rows must remain chat-only, gather only relevant source-ranked
  context, challenge assumptions, and return options plus open questions.
- Advisor skills may inform the discussion but must not take over the workflow
  or turn it into a final design or implementation plan.
- Bounded research may resolve a source conflict, after which control returns
  to the brainstorming discussion.
- Negative rows must route to the requested implementation, SDLC, review,
  publication, communication, research, design, or commit owner.

## Manual Runtime Check

When routing precision matters, exercise `brainstorm-positive-01` through
`brainstorm-positive-11` and `brainstorm-negative-01` through
`brainstorm-negative-07` in a fresh Codex thread where the source skill is
installed or discoverable. If `brainstorm` steals implementation, SDLC,
research, design, review, communication, or commit tasks, narrow the front
matter `description` before changing the workflow body.

Report runtime activation as observed only after this check. Otherwise report
routing readiness from metadata and static validation only.
