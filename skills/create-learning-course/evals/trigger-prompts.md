# Trigger Prompts

Use these examples when reviewing or tuning `create-learning-course` trigger
behavior. These prompts are static examples; they do not prove runtime
activation until they are tried in the target Codex surface.

## Should Trigger When Explicitly Invoked

```text
Use $create-learning-course to create a learning course for engineers who need to learn Kubernetes networking from official docs and hands-on exercises.
```

```text
Use $create-learning-course to turn these public notes into a short course with a syllabus, lessons, exercises, glossary, and source citations.
```

```text
Use $create-learning-course to build a public-safe workshop from this topic and resource list.
```

```text
Use $create-learning-course to revise this course workspace so the lessons include retrieval practice and a publication safety review.
```

```text
Use $create-learning-course to create a course from these internal notes, but make sure the result is safe for public release and uses placeholders.
```

## Should Not Trigger

```text
Create a learning course for engineers who need to learn Kubernetes networking from official docs and hands-on exercises.
```

Do not implicitly invoke this skill. Ask the user to explicitly invoke
`$create-learning-course` before creating or revising course workspace files.

```text
Teach me Rust interactively right now.
```

Use a teaching or tutoring workflow rather than course-authoring output.

```text
Research Kubernetes Gateway API and recommend whether we should use it.
```

Use `research` for technical due diligence before course creation.

```text
Design a software feature and create a /plan handoff.
```

Use `design` for implementation design.

```text
Scan this repo for security vulnerabilities.
```

Use `apply-security` for code or infrastructure security review.

```text
Publish this course to a website.
```

Use a separate publishing or hosting workflow after explicit user request.

## Manual Runtime Check

When trigger precision matters, test these prompts in a fresh Codex thread
where the source skill is installed or discoverable:

- Explicit should-trigger prompts should load `create-learning-course` or
  produce course workspace artifacts with mission, syllabus, sources, lessons,
  practice, and publication safety review.
- Should-not-trigger prompts should route to teaching, research, design,
  security review, or publishing workflows as appropriate.
- If this skill activates implicitly for direct course-creation requests without
  `$create-learning-course`, or steals direct tutoring, technical research,
  implementation design, security scanning, or publishing tasks, narrow the
  front matter `description` or confirm `agents/openai.yaml` has
  `allow_implicit_invocation: false` before changing the workflow body.

Report runtime activation as observed only after this check. Otherwise report
trigger readiness from metadata and static validation only.
