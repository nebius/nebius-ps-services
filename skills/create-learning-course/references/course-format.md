# Standard Course Format

This is the reusable presentation and authoring contract. It fixes the reading
experience, not the subject, course length, hardware or programming language.

## Package And Single Source Of Truth

Use `assets/course-workspace-template/` as an authoring starter:

| Artifact | Authority |
| --- | --- |
| `MISSION.md` | Audience, real-world goal, constraints and exclusions |
| `SYLLABUS.md` | Ordered competencies, prerequisites, practice and readiness |
| `COURSE.md` | Complete canonical lesson prose, not an outline |
| `GLOSSARY.md` | Consistent definitions of introduced terms |
| `RESOURCES.md` | Public official/primary references grouped by topic |
| `NEXT-STEPS.md` | Optional Where to Go Next self-study |
| `PUBLICATION-REVIEW.md` | Review gates, evidence and unresolved limitations |
| `reference/course.json` | Identity and source associations |
| `reference/labs/` | Authored practical guides |
| `reference/diagrams/` | Original SVG sources |
| `index.html` | Generated complete self-contained publication |

For runnable work add `labs/`, tests, isolated environment definitions and
`VERSIONS.md`. Add launchers and target-smoke procedures only if appropriate.
For a multi-course series add a catalog and prerequisite map. No cross-course
runtime imports; package required helpers with their owning course.

Do not ship learner histories, source-coverage tables or comparisons with
superseded courses. Keep design/audit notes out of the public reading path.
A user-requested partial artifact need not contain every full-course file.

## Lesson Pattern

Apply this pattern to every lesson, including the first, across all subjects.
Use plain headings without trailing periods and exactly four main sections:

1. **Objective** — first after the lesson title. State an observable capability
   and its success conditions in concise language; detailed teaching follows.
2. **How it works** — the complete explanation, with concepts defined before
   application, connected prerequisite knowledge, purpose, mechanism, concrete
   worked reasoning, assumptions and limitations. Include at least one
   meaningful diagram of this lesson's core concepts inside this section.
3. **Practice** — link the exact owning lab, case or exercise and say what the
   learner will apply. For a lesson-only request, include the activity and
   feedback here instead of creating an unrequested course package.
4. **Mental model** — last. Give a concise synthesis of the relationships or
   process already taught, with any necessary qualification. Introduce no new
   concept, abbreviation, prerequisite or mechanism here.

### Conceptual Titles

Name the idea, relationship or capability taught. Avoid enumerating every
technology, component or command in a lesson title. For example, use "GPU
execution software layers" rather than "Read the driver, runtime, toolkit,
PTX, SASS and framework stack"; use "Tracing a request" rather than a list of
protocols and tools. Keep a technology name when it is the actual subject.
"Overview", "Basics" and "Concepts" alone are usually too vague. Use one exact
title across syllabus, TOC and lesson; update affected references on revision.

### Connected Explanations

Begin How it works by explaining what the concept is, what acts on what and
what results. The first lesson also establishes the subject's whole workflow
and beginner vocabulary. Later lessons briefly restate necessary prior ideas
and explain the dependency before adding the new concept. A prerequisite link
is supplemental; do not assume the reader already knows the needed concept.

Connect steps causally: explain what changes, why the next step follows, and
how the result supports the objective. Introduce terms before notation; define
symbols, units and assumptions, then work through a concrete example. Explain
where the approach helps and where it fails. Select current authoritative
sources for the actual claims and use original prose, not stitched summaries.
Depth follows conceptual difficulty and the objective, not a word quota.

Expand unfamiliar abbreviations at first meaningful use in each lesson and
explain their meaning or role. For example, introduce
[Parallel Thread Execution (PTX)](https://docs.nvidia.com/cuda/parallel-thread-execution/)
before relying on PTX; GPU and CPU need no expansion for an audience that
already knows them. A term with no authoritative expansion needs a clear
explanation, not an invented full name. Check ambiguous abbreviations and
product names in context; never apply blind global substitutions. The glossary
reinforces the local explanation and never replaces it.

Do not add standalone Start here, What it is, Prerequisite bridge, Recall,
Why it matters or Mechanism sections. Preserve their useful substance in How
it works. Integrate examples and trade-offs into that prose. If a long
explanation needs subheadings, use a few specific conceptual h4 headings,
not a repeated checklist of authoring roles. A separate course-level orientation
may still be called Start here; it is not a lesson section. Keep commands, experiment setup,
evidence collection, result interpretation, troubleshooting, answer keys and
retrieval/transfer tasks in their owning practice guides. A worked conceptual
example stays in How it works so the learner is prepared before practice.

### Rendered Structure

Use `section.lesson` with its title in a direct `h2`, followed by four direct
`div` containers in order: `.lesson-outcome`, `.how-it-works`, `.practice-links`
and `.mental-model`. Each starts with one direct `h3` with the matching visible
label above and contains substantive content. All other lesson content belongs
inside those sections; nothing follows Mental model within the lesson.
Place every lesson figure inside `.how-it-works`, near its explanatory prose.
The starter metadata's diagram `section` names the enclosing section, not a
sibling after which to append the figure. A real inline SVG in a contextual
figure is required for each lesson; a
caption-only placeholder or a linked figure elsewhere does not count.
Use consistent classes without adding a rainbow of boxes. The bundled checker
verifies this bounded structure for a complete standard HTML course. It does
not evaluate title quality, concept accuracy, prose completeness, abbreviation
meaning or diagram usefulness. Review those semantically. Syllabus-only and
review-only requests do not require this complete-HTML checker.

## Practical Guide Pattern

Every guide has an exact ID/title, a detailed purpose paragraph immediately
after the title, and these seven headings in order:

1. Before you start
2. Concepts and code path
3. Run the experiment
4. Check your results
5. Investigate the behavior
6. If something goes wrong
7. Takeaways and next step

For non-code activities, "code path" explains the case/process/artifact flow;
state that no code is involved. Keep the recognizable headings, but do not
invent machinery. Full content rules are in `practical-work.md`.

## Light Digital-Textbook Layout

Reuse `assets/styles.css` and `assets/textbook-shell.html`. The palette is
white/paper with pale mint and blue, dark readable text and restrained amber
warnings. Preserve the existing design tokens unless the user requests a new
identity. Keep essential meaning independent of color.

- Banner: main course title and a short estimated guided-hours line only.
  No status badges, marketing text, author details or timing calculations.
- Desktop: wide centered layout, up to 1800px, with a 240px persistent side TOC
  and flexible content. Paragraphs have an approximately 88ch reading measure;
  figures and source panels may use the full content width.
- TOC: meaningful section groups, ordered lesson titles and exact lab titles.
  Generate it from the same identities as body anchors. Use a native details
  control on smaller screens; keep everything usable without JavaScript.
- Small screens: one column, wrapping navigation, readable text and no
  page-level horizontal scrolling. Test 390px and 320px as well as desktop.
- Use semantic headings, a skip link, visible focus, adequate contrast and
  keyboard-accessible local table/code scrollers.
- No repeated Back to contents links or Read the diagram in text expanders.
  Essential diagram meaning still belongs in adjacent visible prose.
- End with supporting material, Where to Go Next and official references.
  Do not show a research-review dateline. Optional reading adds no silent core
  prerequisites, runtime dependencies or required guided hours.

## Diagram Contract

Every lesson needs at least one original inline SVG inside How it works that
explains the core concept. Use workflows, hierarchy, timelines, comparisons,
branching decisions or other forms appropriate to the subject. A nontechnical
lesson might show evidence leading to a decision, cause and effect, or a case
comparison. Never force a hardware diagram into another domain or add a
cosmetic diagram merely to meet the count.

Each figure has a unique ID, SVG viewBox, accessible title and description,
visible caption, readable labels and one primary inline placement immediately
after the relevant explanation. Secondary mentions link to the primary figure.
Define the notation: arrows mean a stated flow/order/dependency, boxes mean
stated entities/containment, scales have units. Do not draw arrows between
unrelated list items or imply physical wiring for a logical relationship.

Explain essential relationships and conclusions in the surrounding text.
Use text/tspan labels, deliberate line breaks, padding and enough whitespace;
expand a box or split a complex view before shrinking type. Keep labels and
arrowheads away from boundaries and other objects. Distinguish intentional
containment/edge connections from unintended overlap.

Use responsive SVG containment with no forced minimum width. Narrow diagrams
must remain readable; use stacked or split views when downscaling makes labels
tiny. A geometry check is not a browser font-fit check. Inspect rendered assets
and the integrated page independently. Default target contrast is WCAG 2.2 AA:
4.5:1 normal text, 3:1 large text; meaningful non-text boundaries also need
sufficient contrast. Do not claim conformance from a scanner alone.

## Builder And Parity Contract

The bundled shell is deliberately renderer-neutral. For each full course,
reuse its existing builder or create a small course-owned generator using the
project's established Markdown tooling. Do not create a new LMS, remote build
service or custom Markdown dialect merely for this format.

The generator must:

1. Read the canonical prose, guides, metadata, CSS, diagrams and explicit
   learner-source allowlist; never glob private outputs into a publication.
2. Render all supported Markdown blocks without dropping paragraphs, tables,
   fences, lists or links. Reject unsupported input instead of truncating it.
3. Generate unique stable IDs, the TOC and local reference rewrites together.
   Preserve external official links and internal destinations.
4. Embed CSS, original SVGs and complete escaped source files. Include helpers
   needed to understand/run a lab; no ellipses standing in for real source.
5. Create one page with no remote scripts, fonts, trackers, assets or automatic
   requests. Normal user-followed public reference links are allowed.
6. Render/validate fully before atomically replacing `index.html`; reject
   symlinked or out-of-package input/output targets.
7. Provide a read-only `--check` that fails when generated output is stale.
   Include round-trip checks for full prose and exact source bytes, not only a
   heading count or a checksum of a source the page did not actually render.

Mark embedded source as `<code data-source="labs/example.py">...</code>`.
Use one such marker per included file; repeat mentions link to the primary
listing. `scripts/check_course.py` checks exact UTF-8 bytes after HTML entity
decoding against a separate allowlist, along with selected navigation/safety
properties and the four-section, per-lesson diagram contract. It does not
render Markdown or verify full prose parity; the
course-owned generator and tests own that additional gate.

The starter's metadata is illustrative, not a prescribed runtime API. A course
may retain a sound existing schema while satisfying these same invariants.
