# Publication review

## Indexing clarity revision — 2026-10-06

Reviewed `index.html` SHA-256:
`e850c96df4260031c83bdfca5c0c66ba16578b88fd856702ca356789978be767`.

Lesson 2 defines index and indices before use, labels each grid/row/column
choice, and explains selection, slicing and reshaping through one concrete
example. Its two diagrams show the highlighted value and two independent
selections. Definitions use affirmative explanations. Lesson 6 owns inferred
reshape sizes and axis reordering; lesson 9 owns the linear layer's feature-axis
rule. Related definitions, glossary, mission, syllabus and README align. The
reading course retains eighteen lessons and twenty diagrams, with twenty-four
Python blocks after splitting the indexing example into small steps.

| Lane | Status | Evidence and limits |
| --- | --- | --- |
| Source/static | Passed | 26 focused reading/diagram tests, native lessons-only validator, selected build/freshness, all 24 Python blocks parsed, Markdown lint and whitespace checks. Shared catalog and guide bytes match the pre-edit snapshot. |
| Semantic | Passed | Changed explanations and diagram semantics reviewed. Independent review found a moved reshape example using the preceding lesson's tensor size; the correction uses lesson 6's six-value tensor and passes a CPU check. No remaining concrete review findings. |
| Browser/visual | Passed | Three final isolated headless Chrome checks at 1440px, 390px and 320px cover navigation, keyboard controls, local scrolling, text reflow, API emphasis and SVG glyph bounds. Changed figures visually inspected on desktop and phone. |
| Example execution | Passed for changed examples | Six blocks from lessons 2, 6 and 9 pass 16 independent value, shape, storage and linear-layer checks with existing CPU PyTorch 2.9.0. This is local example evidence, separate from the referenced PyTorch 2.14 semantics. |
| Installed environment | Not applicable | Existing local tools and packages reused; no dependency or runtime installation performed. |
| Live target | Not applicable | This is a reading-course revision. CUDA and compiler examples remain unexecuted in this revision; no GPU performance or deployment claim is made. |

Local evidence group `pytorch-index-clarity-vwfacjls` contains `cpu-results.json`,
the browser harness and `browser-results.json`. The final `browser/` directory
records HTML identity, Chrome 154.0.8037.98, geometry, figure/lesson screenshots
and reflow captures. Playwright Test 1.57.0 owns and closes its headless browser
and isolated contexts, with page JavaScript disabled. `browser-trial1/` retains
the initial clipped row-label failure; `browser-trial2/` records the repaired
diagram before final editorial changes. Final browser assertions all pass.

The generic authoring checker emits the same six standard-profile/shared-shell
diagnostics for the before and after HTML. Native lessons-only acceptance
passes; the generic checker and its expectations remain unchanged. The following
reviews describe their own earlier artifact identities.

## Alignment follow-up — 2026-10-06

Reviewed `index.html` SHA-256:
`855a809171b09734548af48f4ae0e2aee73f940e32a44a681b4d6dff7124a534`.

A fresh independent review found one minor accuracy issue (PT-R1): lesson 12
called the loader's default batch a tuple. The corrected explanation distinguishes
tuple samples from the collated one-item list containing a two-by-four tensor.
The example code was already correct. Existing CPU PyTorch and the official
data-loading documentation confirm the sample type, batch type, shape, values
and unpacking. The reviewer confirmed the fix with no remaining concrete findings.

Fresh validation passed 282 focused tests, all nine native course validators,
33 browser checks across all eleven pages, lint, security review, spec validation
and publication freshness. After the prose correction, 25 course regressions,
all three reading validators, freshness and three further responsive browser
checks passed. Lesson 12's final phone rendering was visually inspected.
The review preserves the prior CPU-example evidence and unexecuted CUDA/compiler
limits below; this follow-up did not qualify GPU execution.

Local evidence group `align-pytorch-4gj9_66u` contains `pytest.log`, `native.log`,
`browser-results-before.json` and `browser-before/` for the initial checks;
`browser-results.json`, `browser/` and `freshness-after.log` record the corrected
artifact. The browser directories include exact page hashes. The only course
teaching change in this follow-up is the dataset/batch explanation; the changelog
and generated publication agree. Unrelated worktree changes were preserved.

## Teaching clarity revision — 2026-10-06

Reviewed `index.html` SHA-256:
`8f914dea0d54e7b05f47647ab7bd9a622f23f7ae7c2c16d8a18db01e08537c51`.

The current course contains eighteen lessons, twenty original passive SVGs and
twenty-two commented Python examples in an estimated three-hour reading route.
Every lesson has an observable objective, definition-first explanation and a
Performance connection. Exact PyTorch API names use bold monospace; application
meanings stay plain. Headings and callout labels may remain bold. The course
remains lessons-only, without Practice, labs, setup or required execution.

The revision clarifies indexed axes and application meanings, concrete
two-by-three-by-four tensors, tensor attributes and random distributions. It
adds batching/vectorization and a focused compilation lesson, preserves unique
teaching and caveats, and consolidates duplicate explanations. Canonical sources,
syllabus, metadata, glossary, references and generated HTML agree. The shared
stylesheet now preserves bold emphasis inside inline code; all eleven HTML
publications were rebuilt and inspected for that shared change.

| Lane | Status | Evidence and limits |
| --- | --- | --- |
| Source/static | Passed | 282 focused regressions, all nine native course validators, full build/freshness, scoped Ruff, configured Markdown lint and whitespace checks. All 22 Python blocks parse. |
| Semantic | Passed | Full-course preservation and technical review, exact API versus example notation, arithmetic and diagram meaning reviewed. Independent review findings were corrected and rechecked; no material open findings. |
| Browser/visual | Passed | 33 checks cover all 11 pages at 1440px, 390px and 320px. Three further checks cover the final PyTorch caption/notation edits. All 20 figures were visually inspected on desktop and phone. |
| Example execution | Partial | Fifteen unchanged CPU snippets and 37 independent assertions passed using the existing PyTorch 2.13.0 environment. Six CUDA snippets and the optional compilation snippet remain unexecuted. |
| Installed environment | Not applicable | No installation or runtime change is required or performed for this reading course. |
| Live target | Not applicable | No live GPU execution, measured speedup, cluster qualification or external publication is claimed. |

Browser checks used Playwright Test 1.57.0 and owned headless Chrome
154.0.8037.98 with page JavaScript disabled. They verify exact navigation and
lesson order, fragment targets, keyboard menus and TOC, local code scrolling,
doubled-text reflow, no automatic remote requests, actual bold API font weights,
and diagram glyph bounds with a minimum rendered label size of 13px. The owned
browser and contexts closed after each run.

Local evidence group `pytorch-clarity-3wrcufx2` retains `pytest-final.log`,
`native-final.log`, build/freshness logs and the browser harness. Passing
all-page evidence is in `browser-results-trial3.json` and `browser-trial3/`;
final PyTorch evidence is in `browser-results.json` and `browser/`. Each browser
directory records inspected HTML hashes, browser identity, text geometry and
screenshots. Earlier trials are retained separately: the first exposed two
diagram text-fit defects that were repaired; the second exposed an arbitrary
test-count assumption that was replaced by checks of actual API names and
font weights. The independent CPU verification is recorded in the review tool
transcript, not an executable artifact in this evidence group.

The generic authoring checker remains diagnostic: its standard Practice and
shared-shell markup diagnostics are identical to the previous sixteen-lesson
artifact. The native profile-aware validator and dedicated regressions own the
explicit lessons-only exception. The generic checker was not modified.

Official PyTorch API documentation and tutorials support the technical claims;
the course references include the relevant sampling, batching, compilation,
dropout and gradient-reset semantics. Reading time remains an authoring estimate,
not a measured learner outcome. The following reviews retain their historical
artifact identities and do not establish current GPU runtime behavior.

## Historical nine-course alignment — 2026-10-05

Reviewed `index.html` SHA-256: `bb7fa40b14a62c425a216c9301acf254e57487c7c5bfa8c22c58271ab162a373`.

The complete nine-package alignment passes 2,503 offline tests, all nine native
validators, build/helper parity and the align review/lint/security lanes.
Responsive checks cover every page at desktop, 390px and 320px; final guide and
PyTorch overview edits have fresh focused checks. See the
[current alignment evidence](../docs/course-format-validation.md#nine-course-alignment--2026-10-05)
for fixes, exact browser artifacts, preserved assets and remaining generic-checker,
dense-diagram and live-qualification limits. Earlier entries retain their own scope.

## Original sixteen-lesson scope

Sixteen lessons, sixteen original SVG diagrams and small commented examples in
a lessons-only reading course. Estimated guided hours: 1.75. No Practice sections,
labs, required execution, installers, Slurm jobs or result archives.

The canonical Markdown, metadata, syllabus, glossary, references and diagram
manifest generate the course HTML through the existing catalog builder.
The explicit no-practice requirement overrides the standard course skill's
Practice section; native profile-aware checks own that exception.

## Original evidence lanes

| Lane | Status | Evidence and limits |
| --- | --- | --- |
| Source/static | Passed | 306 focused tests, all nine native validators, full build and publication freshness, helper parity, scoped Ruff, configured Markdown lint and whitespace checks pass. All 19 Python blocks parse. |
| Semantic | Passed | Full prose, expected shapes/values, first-use definitions and all 16 diagrams reviewed. Independent code/security review found no concrete defect. |
| Browser/visual | Passed | 33 isolated checks cover all 11 HTML pages at 1440px, 390px and 320px. All diagram labels fit their viewboxes and render at least 13px high; desktop and phone figures were visually inspected. |
| Installed environment | Not applicable | Reading has no installation or required runtime. |
| Example execution | Partial | Twelve CPU blocks and independent numeric/shape/storage assertions passed with existing PyTorch 2.13.0. Six CUDA examples and the optional compiler example were not executed. |
| Live target | Not applicable | No live GPU course lab or performance qualification is claimed. |

Browser validation used Playwright Test 1.57.0 and owned headless Chrome
154.0.8037.98 with page JavaScript disabled. Checks include exact catalog/menu
order and destinations, one current identity, keyboard menu/TOC operation,
fragment targets, local code scrolling, normal and doubled-text reflow, no
automatic network requests, and the complete lesson/diagram sequence. The
owned contexts and browser closed after the run. Initial runs exposed two
test-selector assumptions; their logs remain separate from the passing run.
No product change was made to satisfy those failed test assumptions.

The generic authoring checker is diagnostic, not a passing acceptance gate:
it requires the standard Practice section and rejects existing shared shell
markup (`link`, `small`, link/structure diagnostics). The same diagnostics
also occur for the existing Tools reading course. The explicit lessons-only
contract is checked by the passing native validator and dedicated regressions;
the generic checker remains unchanged.

Local evidence group `pytorch-course-01a10ebe` contains the CPU results,
native validation and freshness logs, generic-checker reports, browser test
source and `browser-results-verified.json`. The `browser-verified` directory
contains screenshots, SVG text measurements, browser identity and SHA-256
values for each inspected HTML file. A final catalog copy alignment passed
three additional browser cases, 91 catalog regressions and freshness;
`browser-catalog-final` contains that final catalog identity. The catalog
validation record includes
the exact publication hashes.

## Original technical sources

Official PyTorch 2.14 API documentation and current official tutorials support
the taught tensor, autograd, CUDA, transfer, precision and compiler semantics.
Examples and diagrams use original small synthetic values. Supplied topic
material informed the scope and simplified worked-example selection; no
attachment instructions or private operational material are published.

## Original remaining limits

The reading-time estimate is an authoring target, not a measured learner result.
No measured speedup, GPU-runtime qualification or external publication is claimed.
