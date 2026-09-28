# Publication review

## Results naming and build budgets — current local verification

Current HTML SHA-256: `35d2514a547ce3483ff49e68e8ee582ff90b8bf51730214187ea781e910d6c68`. All seven pages are byte-identical to the
preceding revision except the approved download names and results wording.
All six combined archives retain their exact original bytes under the canonical
`<course>-lab-results.zip` name. Original lab sources, dashboards and results
remain unchanged; Soperator remains text-only.

All seven native validators and 337 focused tests pass (three optional Torch
cases skipped). Three wrapper builds produce identical bytes for 15 outputs.
Browser checks in isolated headless Chrome cover 18 practical-course cases at
1440/390/320px, six actual ZIP downloads with matching SHA-256, keyboard setup
navigation and seven offline pages. Captures of the changed region were checked
for desktop/narrow layout; course styles are byte-preserved.

Build and read-only check preflight the full repository publication candidate
against 104,857,600 bytes per file and 1,000,000,000 bytes total. Current estimate
is approximately 690.0 MB, with 310.0 MB headroom; largest file is 95.5 MiB.
This is local evidence, not a deployed Pages or new live-lab result. Earlier
records below describe their original revisions.

## Historical download simplification review: 2026-09-27

Current `index.html` SHA-256: `539f6bb03c550fb3d419b1043cc1bdf715ab68e63dbc9559f9f4b5833826dc56`.

The Practical labs section now offers one combined results ZIP and the shared
setup guide with separate labels and links. The redundant lab-kit ZIP is removed;
original runtime sources and displayed code remain intact.
Outside the revised lab introduction and heading, course content and styling
are unchanged. All six combined resources archives remain byte-identical.

Verification: 338 focused tests, all seven native validators, HTML/ZIP freshness,
helper parity, scoped lint and changed-scope code/security review passed. The
final introductory cleanup also passed all 122 affected tests. Generic skill
checker differences remain identical to the baseline; that checker is not
reported as passing.
Three actual `build-courses.sh` runs produced identical contents for all 15 outputs,
including two runs from another directory. Browser checks passed all 18 practical
course/viewport combinations, six actual ZIP checksum comparisons and seven
saved-page checks with JavaScript disabled. Current full viewport desktop/mobile captures with reduced-motion enabled
were inspected. Functional browser checks used the default motion setting. Headless Chrome 153.0.8010.53 used owned isolated contexts;
all browser resources closed. Evidence group `course-downloads-hgq8f8fb` retains
`browser-results.json`, the `captures/` viewport images and hash comparisons;
no tracing was collected for this scoped revision. No live lab rerun or Pages publication was performed.

See the [current validation record](../docs/course-architecture-validation.md)
for preservation, wrapper failure handling and the approximately 689.9 MB hosted
size estimate. The earlier reviews below retain their original evidence and apply
to their historical versions.

## Historical architecture and download review: 2026-09-27

Current `index.html` SHA-256: `f28b16844dce2a5529292756f97ec9cba008e53087e907a01d334c305bb25564`.

Downloads now use one external dashboards-and-results ZIP and one external lab kit. Original teaching and evidence files remain unchanged.
The shared Python builder is modular; CSS, diagrams, teaching images and complete
code listings remain embedded. Download pointers retain their existing anchors.

Source/static: all seven native validators, HTML/ZIP freshness, helper parity
and 997 distinct relevant tests passed across broad and focused runs. The preservation audit confirms 3,321 protected
files and all rendered content outside the agreed download changes are unchanged.
Scoped lint and changed-scope code/security review passed.

Browser/visual: isolated headless Chrome checks passed at 1440, 390 and 320
pixels, including keyboard, fragments, image loading, overflow and 200% text
reflow. Current captures were visually inspected. All seven offline pages and
all twelve practical-course ZIP downloads passed; downloaded bytes match SHA-256.

See the [architecture validation record](../docs/course-architecture-validation.md)
for archive inventories, preservation scope and the approximately 691 MB full-site
estimate, below the 1 GB Pages limit. This is local authoring/browser evidence;
no new live lab campaign, merge or publication was performed.

## Historical Single course appendices review: 2026-09-25

Current `index.html` SHA-256: `560f114cfe4e7e1ffb4564d76f02ecb724c44175dc7139aab3bf4939fd1c223f`.

This page has exactly one Where to Go Next, one Glossary with 13 A–Z entries,
and final Official references, in that order in both content and navigation.
Lessons and performance-tool guides end with Mental model and optional References;
they contain neither appendix. Existing first-use explanations remain in place.

The seven-course preservation audit retains all glossary keys and distinct
meanings: 93 previously local-only terms join the course glossaries, bringing
their total from 276 to 369. It preserves all teaching outside the removed
appendix blocks, every optional study topic, all 110 lab implementations and
110 lab guides, and 1,584 protected implementation/reference files. Removed
next-lesson bullets repeat destination objectives or existing course directions.
The installed and source authoring skill remain unchanged since this task resumed.

Source/static: all seven repository validators, 242 focused tests (three existing
skips), generated-page/helper parity and scoped lint pass. Regressions reject
local, nested, repeated, missing, misplaced or altered appendices and enforce
source-to-HTML glossary parity. The read-only content and code review found no
remaining actionable issue; the changed-scope security review found no new
active content, dependency, credential or external-resource surface.

Browser/visual: 21 Playwright Test cases passed across all seven complete pages
at 1440×900, 390×900 and 320×900 in owned isolated headless Chrome 153.0.8010.53.
They check unique appendices, exact glossary text, fragment targets, keyboard
navigation, mobile contents toggling, focusable code blocks, doubled root/body
text reflow and document overflow. No external request or page script error was
observed. Seven additional 320px cases passed keyboard focus and ArrowRight
scrolling inside an overflowing local code/table region. Their configuration,
assertions and results are retained as `playwright.scrollers.config.cjs`,
`scrollers.spec.cjs` and `browser-scrollers.json` in the same evidence group.
Narrow glossary screenshots of all seven pages, plus sampled desktop,
onward-study and enlarged-text views, were visually reviewed: headings and
entries remain readable and wrap without overlap. This is scoped presentation
evidence, not an exhaustive accessibility audit.

Private evidence group `course-glossary-madfkwxf` retains
`playwright.final.config.cjs`, `resumed-appendices.spec.cjs`, `browser-final.json`
and `browser-final/resumed-appendices-advanced-gpu-communication-WIDTH/` with `identity.json`,
`glossary.png`, `next-steps.png` and `glossary-reflow.png`.
The trace-enabled resumed attempt timed out during finalization and was
interrupted; its partial traces and results remain in `browser-resumed/` and
`browser-resumed.json`. Final checks ran without tracing and exited successfully;
no final traces are available. The earlier pre-pause attempt remains separate.
Framework-owned browser/context resources closed, and the final runner exited;
no personal profile was used.

The installed generic checker reports 7 existing profile/markup diagnostics
on this page and is not claimed passing. Independent metadata-derived source
manifests were used; source membership and bytes match. Repository profile-aware
checks cover supported local navigation, embedded assets and the text-only or
labs-only exceptions. No new diagnostic category was introduced.

No dependencies were installed, labs rerun or pages externally published.
Installed-environment, runtime and live-target qualification retain their earlier
scope and limits; this editorial pass provides no new evidence for those lanes.
Earlier reviews below are historical and apply only to their recorded artifacts.

## Historical shared course format review: 2026-09-25

Current `index.html` SHA-256: `72cc01ec66f6518fd49fe605804bd684dabb62000500ce3137da7ad7668f8c1e`.

All seven courses share heading levels, typography and spacing for equivalent
content. Official references are numbered, Where to Go Next uses complete
bulleted directions, and Glossary has its own section and definition list
immediately before Official references. Learner content and navigation omit
Syllabus and Course mission. Useful scope, readiness and completion guidance
remains in the overview and existing guides; maintainer authoring sources remain.

Preservation review confirms all 84 original lesson bodies, all 276 course
glossary definitions and complete optional study directions remain. Concrete
bulleted next steps were added to 84 lessons and five tool guides, preserving
optional branches. All 3,396 protected lab, environment and evidence files are
byte-identical to the task-start snapshot, including all 110 lab guides.
Soperator remains text-only and advanced communication remains labs-only.

Source/static: all seven course validators, 278 focused tests (three existing
skips), generated-page and helper parity, scoped Ruff/formatting, Markdown and
whitespace checks pass. Regressions cover shared presentation, source/link
parity, heading order and empty onward-topic rejection. Final read-only content,
code and security review found no remaining actionable issue.

Browser/visual: all 21 Playwright Test cases pass on the complete seven pages
at 1440, 390 and 320 pixels in owned isolated headless Chrome 153.0.8010.53.
Checks cover matching typography, section/list structure, fragment targets,
keyboard section navigation, contents toggling, local scroller focus, enlarged
root/body text reflow, no document-width overflow and no external requests or
script errors. Final identity records match the current HTML bytes. Sampled
screenshots across all seven courses show readable headings and content without
overlap at normal text size; enlarged text wraps within the narrow layout.
This is a scoped layout review, not an exhaustive accessibility audit.

Private evidence group `course-style-fob0yo6k` contains `browser-final.json`,
`browser-final/course-format-advanced-gpu-communication-WIDTH/identity.json`, screenshots and
`trace.zip` for each width. The initial browser run passed 20 of 21 cases; its
narrow-screen keyboard destination failure remains in `browser-report.json`
and `browser-results/`. The final harness explicitly waits for visible keyboard
focus before navigation. No product timing-cause or navigation-repair claim is
made. Framework-owned browser/context resources closed after the run; no
personal profile was used.

The unchanged generic skill checker reports 7 pre-existing profile/markup
diagnostics for this page and is not claimed passing. It does not accept all
repository-supported local navigation, embedded asset/footer markup and special
course profiles. No new diagnostics were introduced; conceptual lesson-field
ordering findings are resolved. Independent metadata-derived source allowlists
and repository source checks pass; browser checks observed no network assets.

No dependency installation, runtime activation, live lab execution or external
publication occurred. Earlier review sections below are historical and apply
only to their recorded artifact identities and qualification scope.

## Historical glossary and reference ordering review

Current `index.html` SHA-256: `3c71dbb2955c7c0655fefc7683edca2b621da55f5f929e9840ba954c539913e5`.

All seven courses retain Glossary immediately before References in content and
navigation. All 84 conceptual lessons and five performance-tool guides now have
an A–Z local Glossary after Mental model; optional lesson References come last.
The advanced course remains labs-only. Existing teaching and all 357 protected
lab, launcher, environment, practical-guide and diagram files are unchanged.

Source/static: all seven course validators, 230 focused tests, generated-page
and helper parity, scoped Ruff/formatting, Markdown and whitespace checks pass.
Checks reject absent, unsorted, duplicate, misplaced or altered local glossary
entries and misplaced References. The generic skill checker reports 7
pre-existing markup/profile diagnostics for this page; it is not a passing
checker result. No new diagnostics were introduced, and missing-lesson-glossary
findings are resolved for conceptual courses. Independent metadata-derived
source allowlists and canonical source bytes were checked separately.

Browser/visual: all 21 Playwright Test cases pass on the seven complete pages
at 1440, 390 and 320 pixels in owned isolated headless Chrome 153.0.8010.53.
Assertions cover course/lesson order, A–Z entries, keyboard glossary navigation,
mobile contents toggling, page overflow, local scroller focus and 200% text
reflow. Three additional cases capture the exact local-glossary element for
visual review; desktop and narrow-screen definitions are readable without
overlap or clipping. Course-level table glossaries retain their local scrollers.

Private evidence group `course-glossary-order-sfvmomxj` contains
`browser-final.json`, `browser-results-final/*/identity.json` and course-glossary
screenshots, plus `browser-visual.json` and `browser-visual/*/lesson-glossary.png`
for the inspected local definitions. The initial trace-enabled attempt timed
out and remains separate; the final run collected no traces. Initial viewport
captures did not wait for smooth scrolling, so local visual review uses the
subsequent element captures. Owned browsers and contexts were closed; no personal
profile was used. No dependency installation, runtime activation, live lab run
or external publication occurred. Earlier evidence below retains its original
artifact scope and qualification limits.

## Current campaign and documentation review: 2026-09-25

Current `index.html` SHA-256: `e915c515a24f5f66394715447a0d23a4b0c9ac923e0adbea70428fb7cc87c616`.

All 34 practical labs completed both profiles: 68 verified prepared-H200 exports. Original result verification, applicable native report checks, actual Grafana/Nsight screenshot reviews, public artifact hashes and ZIP contents passed. See [campaign coverage and limits](../docs/profiling-validation.md) and [observed versions](VERSIONS.md). H100 and clean-install qualification remain separate.

Reviewed the course teaching, practical guides, READMEs and supporting references against their supplied implementations. Corrected workload-profile descriptions, stale cross-course references and claims that exceeded the experiments' measured behavior. Preserved lesson identities, Practice sections, the text-only Soperator course and the advanced labs-only route. Training Lab 31's only code change in this documentation pass corrects its communication-course reference string; its computational AST is unchanged.

Source/static: all seven repository validators, generated-page freshness, shared helper/dashboard parity and configured Markdown checks pass. The focused documentation/content suite passed 333 tests with five skips. Results retain the exact source identities under which they ran; editorial updates do not rewrite campaign provenance.

Browser: all 27 Playwright Test cases passed across the catalog, shared guide and seven courses in owned, isolated headless Chrome 153.0.8010.53. Each page was checked at 1440, 390 and 320 pixels for local fragment targets, loaded images, keyboard skip navigation, applicable TOC interaction, local keyboard scrolling and enlarged root/body text reflow. No document-width overflow or browser script error was observed. Desktop and 320px captures of all nine pages were visually reviewed. This is sampled visual review, not an exhaustive accessibility audit. The passing run retained screenshots and JSON identities; traces were configured only for failures, and none occurred. Framework-owned browser/context resources closed when the test process exited.

Private review artifacts: `intake-01a0d07b-final-audit/documentation.spec.cjs`, `playwright.config.cjs`, `browser-results.json`, `browser-identities.json` and `browser-results/` screenshots. No external publication was performed.

The generic skill checker still reports the documented local-link, footer/embedded-markup and special-profile differences. Its result is not represented as passing; embedded source bytes and membership pass the repository checks. Earlier records below retain their original artifact identities and historical pending statements; they do not override this section's current campaign scope.

## Historical catalog order review

The catalog and all course switchers now begin with Soperator, followed by GPU
Fundamentals, GPU Performance Optimization, LLM Training, LLM Inference, Custom
CUDA Kernels and advanced communication labs. Lesson and lab identities are unchanged.

Reviewed `index.html` SHA-256: `efb09ce6b8ca39e18cc3d4d42f20ff05a068eeeef2dc50ed93d42a313811984c`.
Source/static: all seven validators, generated-page and helper parity, 69 focused
catalog/text-profile tests, exact README list/table and switcher-order checks pass.

Browser/navigation: 24 Playwright Test checks pass in owned, isolated headless
Chrome 153.0.8010.53 at 1440, 390 and 320 pixels. Checks cover card and switcher
order, current-course identity, prerequisite labels, keyboard entry/navigation
and 200% text reflow. Catalog, learning-map and mobile-switcher screenshots were
visually reviewed. Local evidence is retained outside the repository under
`course-order-qmwb8m8_/`: `browser-final.json`, `order.spec.cjs`,
`playwright-final.config.cjs` and `browser-final/` screenshots. Final trace capture
was disabled; the initial run's failures came from reading the URL before
navigation completed. The corrected harness waits for the destination URL.
Owned browser resources were closed.

The generic skill checker reports the same profile/markup diagnostics as the
task-start pages; it is not claimed passed. Existing Ruff formatting differences
in the renderer and validator predate this revision. Scoped Ruff lint, repository
Markdown rules and whitespace checks pass. No installed-environment, GPU runtime,
live cluster or publication validation was performed for this navigation revision.
Earlier review evidence below applies to its recorded artifact identities.

## Historical course standardization review

Current `index.html` SHA-256: `0a8cf98bc546caeeb9ae27ec5f6aff691f41a4615feba0b500f96ced0ba337ee`.

Reviewed this course under the create-learning-course standard and final align
gate. All seven project validators pass; 430 focused content, renderer,
sequence, diagram, profile and standalone-package checks pass. All 560 protected
source files across the series remain byte-identical, including all 110 lab
guides and 116 dashboards. Lesson identities and lab associations are preserved.
Soperator remains text-only and the advanced route remains labs-only.

This page passed desktop, 390px and 320px checks in the final 24-case owned,
isolated headless Chrome 153.0.8010.48 run. Keyboard navigation, local scrollers,
200% text reflow, source identities and applicable downloads passed. The five
revised diagrams passed ten additional checks and were visually inspected.
The initial browser run had one intermittent 320px anchor failure; its evidence
is retained and the final harness waits for keyboard focus before navigation.
No page-repair or proven timing-cause claim is made.

The generic skill checker still reports the documented local-link, embedded
asset/footer and special-profile differences. Those are not relabeled as a
pass. See the [complete standardization review](../docs/course-standardization-validation.md)
for exact coverage, evidence boundaries and current HTML identities.

No live GPU, Slurm, fabric, Nsight or Grafana qualification was performed.
Earlier sections below retain historical evidence; this section identifies the
current publication bytes without superseding pending live qualification.

## Earlier profiling and dashboard review

Current `index.html` SHA-256: `e3a2e68d75476574b211bc6288a3e9d5e749cccbfd94cc578071b4965fa189e9`.

All executable lab guides now declare applicable Systems captures or explicit exceptions, exact report views and their own Grafana dashboard imports. Source and standalone-package checks pass. The final CPU suite passed 1,151 tests plus two separate loopback fixtures. Browser evidence covers this page at 1440, 390 and 320 pixels, including rendered lab instructions, matching dashboard bytes, downloads and 200% reflow. Initial hidden-link and intermittent heading-position failures are retained separately; focused repeated checks passed after contents-layout synchronization in the harness. No UI repair is claimed from those repeats.

See the [all-lab audit](../docs/lab-profiling-audit.md) for exact coverage, exceptions and limits. Live H100 captures, native profiler qualification and Grafana ingestion/rendering remain pending. Earlier hashes and test counts below describe their original artifacts.

## Earlier series format review

Current `index.html` SHA-256: `4280d53b1989e620300f8b340b31d5d5526f82ff4d3ad9134af48e78767220cd`.

Source and static checks pass for all seven courses. The full CPU suite passed
1,019 tests plus two separate loopback fixtures; later focused format and prose
checks passed 318 and 91 tests respectively. These counts overlap.

This page passed desktop, 390px and 320px checks in the final 24-case owned,
isolated headless Chrome 153.0.8010.48 run. Contents navigation, keyboard use,
local scrollers, whole-page 200% text reflow and applicable downloads passed.
The current shared headings, titles and navigation replace earlier presentation
findings below. Browser resources were closed by Playwright.

The generic skill checker still reports 7 documented profile/format
findings; it is not represented as passing. Installed environment, Runtime activation and Live H100 qualification remain pending; source and browser success do not establish live performance.
See the [series format validation report](../docs/course-format-validation.md)
for exact scope, artifact identities, findings and evidence limits.

## Earlier review records

The records below describe previous artifacts and narrower checks. Their hashes,
counts and unresolved presentation findings are historical; the current review
above supersedes them for format and navigation only.

### Status

Authoring and the repository's source/browser checks are complete. This all-labs course contains setup-only Lab 00 and 34 executable activities for two eight-H100 Soperator workers. Native installations and live sixteen-H100 qualification remain pending; the course is not declared publication-ready.

| Evidence lane | Status | Evidence or limitation |
| --- | --- | --- |
| Source/static | Passed repository gates | All seven course validators, generated parity, dashboard/helper/kit checks, 1017 CPU regressions and two separate local API fixtures. Final focused checks cover later changes. |
| Browser/visual | Passed | All 12 final owned headless Chrome 153.0.8010.48 checks; desktop, 390px and 320px, keyboard, downloads, diagrams and 200% reflow. |
| Generic skill checker | Format differences remain | Eight documented differences, including the explicitly excluded conceptual-lesson requirement; not represented as passing. |
| Installed environment | Pending | NVIDIA runtime versions are source-reviewed qualification candidates. No packages were installed here. |
| Runtime activation | Pending | Native CUDA, NCCL, RDMA, Bridge, NIXL and Dynamo processes were not executed on GPU hardware. |
| Live target | Pending | No Slurm/GPU deployment, fabric qualification or real monitoring ingestion was performed. |

### Artifact and review

The final `index.html` SHA-256 is
`cf54013a1cc1e14147a3753da3c24cb12f03da784a52dd6df9f2ad8576170f37`.

The private artifact group `advanced-course-20260918` contains `browser-final.json`, screenshots and traces. Playwright owned and closed its browser resources. The complete [validation report](../docs/advanced-course-validation.md) records assertions, earlier findings, fixes, artifact identities and evidence limits.

Canonical prose and commands were reviewed against the implementations and official NVIDIA source interfaces. Training throughput, collective/link bandwidth, request latency and goodput remain distinct quantities. Illustrative calculations are labeled; no measured performance or speedup is invented. Raw traces, host identities, addresses and model outputs remain private runtime artifacts.
