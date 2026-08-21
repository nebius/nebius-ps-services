# Supplemental Process Cases

These cases preserve detailed workflow and output-quality expectations.
`trigger-prompts.csv` is the sole canonical trigger authority; this document
does not define skill routing.

Use the canonical CSV when reviewing or tuning `create-learning-course`
routing. Its prompts are static examples; they do not prove runtime
activation until they are tried in the target Codex surface.

## Manual Runtime Check

When routing precision matters, test these canonical cases in a fresh Codex thread
where the source skill is installed or discoverable:

- Run canonical rows `course-positive-01` through `course-positive-07`. They
  should load `create-learning-course` and produce course workspace artifacts
  with mission, syllabus, sources, lessons, practice, and publication safety
  review.
- Run canonical rows `course-negative-01` through `course-negative-10`. They
  must not implicitly load this skill and should remain unselected or route to
  teaching, research, design, security review, documentation, or publishing
  workflows as appropriate.
- If this skill activates implicitly for direct course-creation requests without
  `$create-learning-course`, or steals direct tutoring, technical research,
  implementation design, security scanning, or publishing tasks, narrow the
  front matter `description` or confirm `agents/openai.yaml` has
  `allow_implicit_invocation: false` before changing the workflow body.

Report runtime activation as observed only after this check. Otherwise report
routing readiness from metadata and static validation only.
