---
name: create-learning-course
description: "Use only when explicitly invoked for creating or revising public-safe learning courses, lesson plans, curricula, course workspaces, syllabi, exercises, glossaries, and learner progress records from a learner mission and trusted sources. Use when Codex needs to turn a topic, audience, resource set, or existing notes into a structured course with lessons, retrieval practice, citations, and publication/security review."
---

# Create Learning Course

## Help

For `$create-learning-course --help` or `$create-learning-course -h`, return concise help and stop before
any workflow step. Include the purpose, invocation policy, public usage/actions,
and `-h, --help` plus only documented skill-level options; say "No additional
public flags" when none exist. For internal or coordinator-only skills, state
that boundary and that no standalone public workflow action exists. After the
selected `SKILL.md` is loaded, help is report-only: do not call any additional
tools, inspect project state, or modify files, private state, Git, or external
systems. Never
expose private helper actions or treat help as workflow authorization.

## Purpose

Use this skill to create practical learning courses that are grounded in a
learner mission, built from trusted sources, and safe to share publicly by
default. The output can be a full course workspace, a smaller syllabus, or a
single lesson that fits an existing course.

Invoke this skill explicitly with `$create-learning-course`. It disables
implicit invocation because course creation can create or revise many local
files.

The course model is mission-led: curated resources, small lessons, reusable
assets, reference documents, and learning records. This skill is owned by this
repository and adds a stricter publication and security review so skills and
generated courses do not expose sensitive material.

## Invocation Policy

Explicit invocation required. Use only by explicit invocation with
`$create-learning-course`. Do not implicitly invoke this skill for broad
course-creation requests, because it can create or revise many local files.

## When To Use

- Creating a new course from a topic, target audience, desired outcome, source
  list, or folder of existing public-safe notes.
- Turning a learner mission into a syllabus, lesson sequence, exercises,
  glossary, reference sheets, and review checkpoints.
- Revising an existing course workspace while preserving its mission,
  terminology, source list, lesson numbering, and learner records.
- Creating public-safe training material from private or internal notes by
  generalizing examples and removing sensitive details.
- Adding retrieval practice, spacing, interleaving, and feedback loops to
  lessons that are currently only explanatory.

## When Not To Use

- Do not use for direct implementation, refactoring, debugging, code review,
  commits, PRs, publishing, or hosting. Use the appropriate implementation,
  review, Git, publishing, or hosting skill instead.
- Do not use for live tutoring when the user wants an interactive lesson in
  chat rather than reusable course artifacts.
- Do not use for general technical due diligence without a course output. Use
  `research` first when the user needs authoritative investigation before
  curriculum design.
- Do not certify high-stakes training, compliance status, professional
  licensing, medical/legal/financial advice, or safety-critical procedures.
  Recommend qualified expert review for those courses.
- Do not copy internal documents, customer material, private URLs, raw logs, or
  proprietary examples into public course artifacts.
- Do not create a broad encyclopedia. Courses should be built around observable
  learner outcomes and practice, not exhaustive topic coverage.

## Inputs

- Topic, learner audience, target skill level, desired outcome, timeline, and
  constraints.
- Trusted resources such as public URLs, books, papers, official
  documentation, source folders, or user-provided notes.
- Existing course workspace paths containing files such as `MISSION.md`,
  `COURSE.md`, `SYLLABUS.md`, `RESOURCES.md`, `lessons/`, `reference/`,
  `learning-records/`, `assets/`, or `PUBLICATION-REVIEW.md`.
- Output path or format constraints such as HTML lessons, Markdown lessons,
  workshop outline, self-study course, or instructor-led course.

## Required Reads

- Read `references/course-design-workflow.md` before creating or substantially
  revising a course workspace.
- Read `references/publication-safety.md` before writing final course artifacts,
  converting private material into public-safe examples, or reporting that a
  course is ready to share.
- Use `assets/course-workspace-template/` when creating a new course workspace
  or when an existing workspace is missing core files.
- Use `evals/trigger-prompts.md` when reviewing or tuning trigger behavior.

## Writes

- May create or update local course workspace files requested by the user or
  implied by the selected output path, including `MISSION.md`, `COURSE.md`,
  `SYLLABUS.md`, `RESOURCES.md`, `GLOSSARY.md`, `PUBLICATION-REVIEW.md`,
  `lessons/`, `reference/`, `learning-records/`, and `assets/`.
- May create optional private planning notes only when the user explicitly asks
  for them, and place them outside the publishable course root, such as
  `<course-folder>.private/NOTES.md`.
- May update this skill's source materials under the Learning Loop when the
  current task contract allows source edits and the learning is reusable,
  evidence-backed, and public-safe.
- Must not write to external services, LMS systems, websites, email, Slack,
  Confluence, Git remotes, or publishing platforms unless the user explicitly
  invokes a separate workflow for that action.

## Process

1. Clarify the mission.
   - If the learner goal, audience, success criteria, or constraints are vague,
     ask the smallest useful question before writing course artifacts.
   - If enough information exists, state assumptions and proceed.
2. Establish the output boundary.
   - Use the user-provided path when present.
   - Otherwise create or update a local course folder in the current workspace
     using a safe slug such as `course-<topic>`.
   - Do not overwrite existing course files without reading them first.
3. Gather and rank sources.
   - Prefer official documentation, primary sources, reputable textbooks,
     peer-reviewed material, source code, standards, and expert-authored
     public resources.
   - Use private or internal material only as input context. Convert it into
     public-safe, generalized course content unless the user explicitly says
     the course is private.
4. Build the course structure.
   - Create or update `MISSION.md`, `COURSE.md`, `SYLLABUS.md`,
     `RESOURCES.md`, `GLOSSARY.md` when useful, `lessons/`, `reference/`,
     `learning-records/`, `assets/`, and `PUBLICATION-REVIEW.md`.
   - Do not create private notes by default. If the user explicitly asks for
     non-secret private planning notes, keep them outside the publishable course
     root.
5. Design lessons.
   - Make each lesson teach one tight outcome and produce one tangible learner
     win.
   - Include a short explanation, cited source links, retrieval practice,
     worked examples, exercises, feedback or answer keys, and next review cues.
   - Reuse assets before creating new lesson-specific styles or widgets.
6. Run the publication safety review.
   - Check course files for secrets, private endpoints, customer data,
     proprietary details, unsafe claims, missing citations, and license issues.
   - Redact or generalize unsafe content before calling a course public-safe.
7. Report the result.
   - Summarize the course path, artifacts created or updated, sources used,
     safety review outcome, validation run, and remaining review needs.

## Idempotency

- Before creating a course workspace, check whether the target path exists and
  read its core files instead of replacing them.
- Preserve existing lesson, reference, and learning-record numbering; add the
  next sequential number for new files.
- Update existing course files in place when revising the same mission, and
  create a learning record when the mission or course strategy changes.
- Reuse existing `assets/` components before adding new styles, scripts, or
  widgets.
- If a previous publication review exists, update its status and notes rather
  than starting a competing review file.

## Failure Handling

- If the learner mission is too vague to produce useful course artifacts, ask
  concise mission questions before writing files.
- If trusted public sources are missing, create a source gap in `RESOURCES.md`
  and avoid unsupported claims in lessons.
- If private context appears necessary, distinguish private course status from
  write permission. Secrets, private endpoints, customer data, regulated data,
  raw logs, and credential material remain never-write. Use generalized
  examples unless the user explicitly approves non-secret private planning
  notes outside the publishable course root.
- If a publication-safety scan finds a concrete risk, redact or generalize the
  content before reporting the course as public-safe.
- If validation tooling is unavailable, perform manual static inspection and
  report the skipped command.

## Guardrails

- Generated skills and generated courses must remain public, generic, and
  reusable unless the user explicitly marks a course private.
- Use placeholders such as `{API_TOKEN}`, `{PROJECT_ID}`, `{PRIVATE_ENDPOINT}`,
  `{CUSTOMER_NAME}`, and `{INTERNAL_SYSTEM}` instead of real values.
- Never print, persist, or copy secrets, private endpoints, customer data,
  internal hostnames, raw logs, credentials, certificates, or non-public URLs
  into course files, skill files, examples, final responses, or task state.
- HTML lessons must avoid analytics, trackers, remote scripts, remote fonts,
  and unreviewed third-party embeds. Prefer local CSS and plain HTML.
- Cite public sources for factual claims. Mark claims as unverified when a
  trusted source does not support them.
- For high-stakes topics, create educational material only with explicit
  limitations and a required expert-review item in `PUBLICATION-REVIEW.md`.

## Must Not

- Do not publish, upload, email, or otherwise distribute a course from this
  skill.
- Do not overwrite existing course files without reading them first.
- Do not include secrets, private endpoints, customer data, internal hostnames,
  raw logs, credential material, or non-public URLs in public course artifacts.
- Do not include secrets, private endpoints, customer data, regulated data, raw
  logs, credential material, or non-public URLs in private notes either.
- Do not cite private material in public course files.
- Do not claim runtime trigger activation unless it was observed in a fresh
  Codex surface.

## Completion Criteria

- The course mission, audience, outcomes, constraints, and out-of-scope topics
  are recorded or explicitly marked as assumptions.
- Course artifacts requested by the user exist and link to the relevant source,
  lesson, reference, or review files.
- Lessons include practice, feedback, and review cues rather than explanation
  only.
- `PUBLICATION-REVIEW.md` reflects the current public-safe status and any
  remaining expert, source, license, or security review needs.
- Validation or manual static inspection has been run and limitations are
  reported.

## Validation

- Validate the skill itself with the repo skill validators after edits.
- Validate generated course artifacts with a static publication review before
  calling them public-safe.
- Use file-listing secret scans or local inspection patterns that do not paste
  suspected secret values into the conversation.
- Treat browser rendering, live web publishing, external LMS uploads, and
  third-party service calls as live validation. Run them only after explicit
  user request and safety confirmation.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Output Contract

Return:

- Course path and artifacts created or updated.
- Learner mission, audience, outcomes, and constraints used.
- Source basis and any unverified claims.
- Lesson sequence and practice model.
- Public-safety/security review result.
- Validation run and skipped live checks.
- Remaining expert, learner, or publication review needed.

## References

- `references/course-design-workflow.md`: course workspace design, lesson
  sequence, learning records, and source inspiration/attribution notes.
- `references/publication-safety.md`: public-safe course creation rules,
  redaction guidance, secret handling, and final review checklist.
- `assets/course-workspace-template/`: starter workspace files for new courses.
- `evals/trigger-prompts.md`: should-trigger and should-not-trigger examples.
