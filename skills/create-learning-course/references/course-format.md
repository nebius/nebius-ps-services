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

Use plain headings and labels without trailing periods. The first lesson
starts with a substantive **Start here** explanation: what, why, how, vocabulary,
workflow and beginner route. Subsequent lessons begin with **What it is**,
before application, objectives or lab instructions.

Then keep these teaching roles in this order:

1. Objective — an observable competency.
2. Prerequisite bridge — prior knowledge and why it is needed.
3. Recall — a short retrieval task with later feedback.
4. Why it matters — a concrete use and its limitations.
5. Mental model — the components and their relationship.
6. Mechanism — what actually happens, in order, with definitions.
7. Context — relevant environment/domain assumptions; not a forced GPU field.
8. Worked example — inputs, units, assumptions, steps and interpretation.
9. Trade-offs — when the approach helps, costs or fails.
10. Practice — exact lab/exercise title and entry point.
11. Evidence — what counts as success and what must be recorded.
12. Interpretation — how to reason from observations and uncertainty.
13. Common failure — symptom, cause, diagnostic and safe response.
14. Answer — explanation, worked solution or assessment rubric.
15. Review — retrieval cue and a next independent task.

Use meaningful prose beneath labels; headings alone do not satisfy the role.
Related roles can share a coherent paragraph in a very short lesson, but must
not disappear. The learner sees the concept before being asked to apply it.
Use a consistent field-to-CSS mapping across courses; avoid a rainbow of boxes.

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

Create original inline SVGs for workflows, topology, hierarchy, memory/data
movement, execution timelines, scheduling and parallelism when they clarify
relationships. Other subjects use similarly meaningful visual forms.

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
properties. It does not render Markdown or verify full prose parity; the
course-owned generator and tests own that additional gate.

The starter's metadata is illustrative, not a prescribed runtime API. A course
may retain a sound existing schema while satisfying these same invariants.
