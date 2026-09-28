# Publication Review

## Status

Draft. Replace each pending gate with exact scoped evidence or a justified
not-applicable reason. Do not mark the course ready while required gates remain
open. This author review is not learner-facing banner content.

| Gate | Status | Evidence or limitation |
| --- | --- | --- |
| Source/static | Pending | {Links, identity, lint, full prose/source parity} |
| Installed environment | Pending | {Applicable target or non-code reason} |
| Runtime activation | Pending | {Startup/correctness evidence or reason} |
| Live target | Pending | {Declared target evidence or reason} |
| Browser/visual | Pending | {Headless browser evidence below; desktop, 390px, 320px, keyboard, zoom, figures} |
| Semantic/expert | Pending | {Concept accuracy, assessments and required expertise} |

## Browser Evidence

For permitted full-course checks, use owned headless Playwright Test with
Chrome and isolated state. Keep optional headless, isolated MCP exploration
separate. If browser execution is unavailable or denied, retain Pending and
record the limitation; do not request desktop unlock or bypass the denial.
Review-only and lesson-only scope does not implicitly require browser work.

- Inspected artifact path and revision or content digest: {pending}
- Actual browser/version and headless mode: {pending}
- Viewports and results for desktop, 390px and 320px: {pending}
- Assertions for keyboard, zoom/reflow, mobile TOC and local scrollers: {pending}
- Screenshot and trace paths: {pending}
- Visual findings for diagram meaning, text fit, overlap and reading: {pending}
- Owned-browser cleanup, including after failures: {pending}
- Optional MCP exploration, separate findings or not used: {not used}
- Missing evidence or execution limitations: {pending}

## Completeness And Alignment

- [ ] Shared fonts, type scale and heading roles match across courses.
- [ ] Official references are numbered; next-step options use bullets.
- [ ] Exactly one course Glossary and one Where to Go Next have independent
  top-level sections and TOC entries; no lesson/guide-local copies exist.
- [ ] Mission/syllabus are absent from published headings/navigation; unique
  learner context is retained in the orientation or owning lesson.
- [ ] Conceptual titles match authoring sources, TOC and lesson body.
- [ ] Every lesson follows Objective, How it works, Practice, Mental model.
- [ ] How it works connects definitions, prerequisites, purpose and mechanism.
- [ ] Unfamiliar abbreviations are expanded and explained in context.
- [ ] Definitions precede use cases, notation and practical work.
- [ ] Each How it works includes a meaningful core diagram and worked example.
- [ ] Mental model closes teaching without adding new concepts.
- [ ] The course Where to Go Next offers concrete onward options before Glossary.
- [ ] Optional onward study relates to completed competencies and adds no gates.
- [ ] The shared Glossary covers all taught terms and preserves distinct meanings.
- [ ] Glossary entries have accurate expansions/definitions and A–Z displayed keys.
- [ ] Abbreviations sort without case sensitivity, not by their expansions.
- [ ] Optional lesson References follow Mental model; course Official references
  follow Glossary and remain last.
- [ ] Outcomes, prerequisites, syllabus order and assessment agree.
- [ ] Unique material is preserved; purposeful refreshers are distinguished.
- [ ] Every practical guide follows the seven sections and exact lab identity.
- [ ] Commands, supported variations, output paths and results match source.
- [ ] Complete Markdown and code are preserved in generated HTML.
- [ ] Every TOC/local link works; figures sit beside their explanations.
- [ ] Diagrams have correct semantics, readable labels, no accidental overlap.
- [ ] Narrow layouts and source/table scrollers are usable with keyboard/zoom.

## Safety And Sources

- [ ] No secrets, private identifiers/URLs, personal records or raw logs.
- [ ] Synthetic/public examples and asset/code licenses are reviewed.
- [ ] No trackers, hidden requests or unreviewed active content.
- [ ] Public official references support the taught claims.
- [ ] Measurements, qualification and review status are not invented.
- [ ] Required high-stakes expert review is complete or remains pending.

## Build and companion-download evidence

Record the configured renderer and dependency mechanism, publication root and
inventory mode, byte limits, largest file, total and headroom. Record repeated
build/freshness results, approved companion links, archive membership/source
parity and offline-reading checks. Keep site-size estimates, installation,
browser evidence and actual deployment as separate claims.
