# Create Learning Course

`create-learning-course` creates public-safe course workspaces from a learner
mission, trusted sources, lesson outcomes, exercises, and review checkpoints.
It is a course-authoring skill: mission-led, source-grounded, practice-heavy,
and built around explicit publication and security review.

## Files

- `SKILL.md`: runtime contract, workflow, guardrails, validation, and output
  contract.
- `agents/openai.yaml`: UI metadata and explicit invocation policy.
- `references/course-design-workflow.md`: course workspace structure, lesson
  design method, source ranking, and learning-record guidance.
- `references/publication-safety.md`: public-safe course rules, redaction
  guidance, HTML safety, high-stakes limitations, and review statuses.
- `assets/course-workspace-template/`: starter course files and HTML lesson
  template.
- `evals/trigger-prompts.md`: should-trigger and should-not-trigger examples.

## Boundaries

- This skill writes local course artifacts only. Publishing, hosting, emailing,
  or LMS upload requires a separate explicit request.
- Invoke it explicitly with `$create-learning-course`; implicit invocation is
  disabled because the workflow can create or revise many local files.
- Generated courses are public-safe by default. Private source material should
  be generalized or redacted; optional non-secret private planning notes belong
  outside the publishable course root.
- For high-stakes topics, the course must include expert-review requirements
  and must not claim certification, compliance, or professional advice.
