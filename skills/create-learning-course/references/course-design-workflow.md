# Course Design Workflow

Use this reference when creating a new course workspace or substantially
revising an existing one.

## Source Inspiration And Attribution

This skill is our course-authoring workflow. Its mission-led learning model was
inspired by Matt Pocock's public `teach` skill pattern:

- <https://github.com/mattpocock/skills/tree/main/skills/productivity/teach>

The upstream repository is MIT-licensed. If this skill ever copies substantial
upstream text or implementation structure beyond the current high-level
learning model, keep the upstream copyright and permission notice with the
copied material.

Keep the attribution here rather than making the runtime identity sound like a
fork. This skill now owns different behavior: explicit invocation, course
workspace creation, publication safety, redaction, citations, reusable course
templates, and review checkpoints.

## Course Workspace

Default course layout:

```text
course-topic/
|-- MISSION.md
|-- COURSE.md
|-- SYLLABUS.md
|-- RESOURCES.md
|-- GLOSSARY.md
|-- PUBLICATION-REVIEW.md
|-- assets/
|   `-- styles.css
|-- lessons/
|   `-- 0001-lesson-title.html
|-- learning-records/
|   `-- 0001-observed-understanding.md
`-- reference/
    `-- topic-reference.md
```

Use this layout as a starting point, not a rigid contract. A short course may
only need `MISSION.md`, `SYLLABUS.md`, `RESOURCES.md`, and one lesson. A longer
course should grow reference sheets, exercises, and learning records.

Do not create private notes inside the publishable course root by default. If
the user explicitly asks for non-secret private planning notes, place them in a
sibling path such as `course-topic.private/NOTES.md` and keep them out of any
public bundle.

## Mission First

Before writing lessons, establish:

- learner audience and prior knowledge
- concrete real-world goal
- observable success criteria
- time, tooling, accessibility, budget, and language constraints
- topics that are intentionally out of scope

If these are missing, ask concise questions. A vague mission produces generic
lessons and weak practice.

## Source Ranking

Rank course sources in this order:

1. Official documentation, standards, specifications, primary research, and
   source code.
2. Recognized expert books, papers, conference talks, or long-form guides.
3. Maintainer discussions, issue trackers, or operational postmortems.
4. Community material with strong moderation and clear provenance.
5. User notes, only after classifying whether they are public-safe.

Every lesson should cite the specific public sources that support factual
claims. If a useful claim comes only from private material, generalize it and
cite no private link in public artifacts.

## Course Blueprint

Write `COURSE.md` as the stable blueprint:

- title and audience
- prerequisite knowledge
- course outcomes
- lesson sequence
- practice and review strategy
- assessment approach
- publication status

Keep it short enough that a future agent can quickly decide what to teach,
revise, or validate next.

## Lesson Design

Each lesson should:

- teach one tightly scoped outcome
- start from a motivating learner task
- explain only the knowledge needed for that task
- include retrieval practice before or after explanation
- include at least one exercise with feedback or answer key
- link to relevant `reference/` files and previous lessons
- end with a review cue or next-step prompt

Prefer a sequence of small lessons over one long chapter. If a lesson needs too
many prerequisites, split it and update the syllabus.

## Practice Model

Use learning techniques deliberately:

- Retrieval practice: ask learners to recall, predict, classify, or produce.
- Spacing: add review cues that revisit earlier material after later lessons.
- Interleaving: mix related skills after basics are stable.
- Worked examples: show a complete solution, then fade support over exercises.
- Feedback loops: include immediate feedback where possible, especially for
  quizzes and short coding or reasoning tasks.

Do not add difficulty for its own sake. Difficulty should serve retention,
transfer, or real-world performance.

## Learning Records

Use `learning-records/` to store durable learner evidence or course decisions:

- prior knowledge established
- misconceptions corrected
- skills demonstrated
- mission shifts
- terms promoted to the glossary
- design decisions that affect future lessons

Learning records are not session logs. Keep each record short and tied to a
future course decision.

## References And Glossary

Use `reference/` for compressed, reusable material:

- terminology and glossary entries
- algorithms, checklists, commands, or syntax
- decision trees and diagrams
- source summaries with citations

Create `GLOSSARY.md` when terminology matters. Use one canonical term for each
concept and avoid teaching with inconsistent synonyms.

## Course Update Flow

When revising an existing course:

1. Read `MISSION.md`, `COURSE.md`, `SYLLABUS.md`, `RESOURCES.md`, and
   `PUBLICATION-REVIEW.md` first.
2. Inspect the target lesson or reference file.
3. Preserve numbering and links unless the user asks for a restructure.
4. Update learning records when a durable learner or course-design fact
   changes.
5. Re-run the publication safety review on touched files.

## Final Report

Report:

- workspace path
- files created or updated
- sources used
- lesson outcomes
- practice model
- publication-safety status
- remaining review or source gaps
