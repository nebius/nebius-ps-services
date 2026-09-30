# Publication review

## Course overview cleanup — current local verification: 2026-09-29

Current HTML SHA-256: `ad37d5163934e4faea87e7234270b887aeed82ca566cdbf44bb15919c387d8b7`.

The overview follows the shared purpose, prerequisites and practical-scope
pattern. Shared operations now belong to the Lab Guide; distinct course limits remain explicit.
Advanced measurement and completion guidance remains in its embedded course
guide. The Lab Guide now uses cxcli-generated access and password commands.

Source/static: 261 focused tests, all seven native validators, generated-output
and helper parity, scoped lint and independent review pass. All numbered lesson
bodies and 3,479 protected source/reference/evidence files are unchanged.
Browser/visual: 27 isolated headless Chrome 154.0.8037.58 cases pass at
1440px, 390px and 320px, including keyboard access and enlarged-text reflow.
Overview and connection screenshots were reviewed. Generic skill-checker
findings match baseline and are not reported as a pass.

See the [overview validation record](../docs/course-overview-validation.md) for
artifact hashes, screenshots, the retained initial harness stall and successful
retry, cleanup and limitations. No new installed-runtime, live-target or public
deployment qualification is claimed.

## Historical: Catalog navigation — local verification: 2026-09-28

Current HTML SHA-256: `779c9d31f5001516afcbcf5d64a72052e691a67cf7e9d887d33d161cad24bcb1`.

The shared resource menu now includes Lab Guide second after Soperator and keeps
one current-page marker. This course's HTML is byte-identical outside that menu;
its teaching, lab sources, diagrams and existing evidence remain unchanged.

Source/static: 161 focused tests, all seven native validators, generated-output
and helper parity, scoped lint and independent read-only review pass.
Browser/visual: all 27 page/viewport cases pass in isolated headless Chrome
153.0.8010.53 at desktop, 390px and 320px, with keyboard navigation and reflow.
The generic checker retains baseline diagnostics and is not reported as passing.

See the [catalog validation record](../docs/catalog-navigation-validation.md)
for artifact identities, preservation, screenshots and limitations. No external
publication, dependency installation or new live lab execution occurred.

## Historical: Results naming and build budgets — current local verification

Current HTML SHA-256: `9fd52d3940d585abfc55e5c5432a173777cd6c3dfed211b9d4e975cb028a389d`. All seven pages are byte-identical to the
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

Current `index.html` SHA-256: `bfcfef76a43c8c6ed47590082b7f78a442e31fe79b62ce152f6e1bdc465040b5`.

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

Current `index.html` SHA-256: `bbcf437e0e6c640c1991acafdf7b048147fef25abc6bfa4e4fe1049dd9598dac`.

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

Current `index.html` SHA-256: `236b7d7007c223be618f71fc1f1fd25216ee0361cf0e71ce672de4ad611e3459`.

This page has exactly one Where to Go Next, one Glossary with 62 A–Z entries,
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
and `browser-final/resumed-appendices-llm-training-WIDTH/` with `identity.json`,
`glossary.png`, `next-steps.png` and `glossary-reflow.png`.
The trace-enabled resumed attempt timed out during finalization and was
interrupted; its partial traces and results remain in `browser-resumed/` and
`browser-resumed.json`. Final checks ran without tracing and exited successfully;
no final traces are available. The earlier pre-pause attempt remains separate.
Framework-owned browser/context resources closed, and the final runner exited;
no personal profile was used.

The installed generic checker reports 5 existing profile/markup diagnostics
on this page and is not claimed passing. Independent metadata-derived source
manifests were used; source membership and bytes match. Repository profile-aware
checks cover supported local navigation, embedded assets and the text-only or
labs-only exceptions. No new diagnostic category was introduced.

No dependencies were installed, labs rerun or pages externally published.
Installed-environment, runtime and live-target qualification retain their earlier
scope and limits; this editorial pass provides no new evidence for those lanes.
Earlier reviews below are historical and apply only to their recorded artifacts.

## Historical shared course format review: 2026-09-25

Current `index.html` SHA-256: `2ca00e08e4f1c3f7dff0e102c2c20f277a0449ca9da1c29443501fc00ca826ea`.

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
`browser-final/course-format-llm-training-WIDTH/identity.json`, screenshots and
`trace.zip` for each width. The initial browser run passed 20 of 21 cases; its
narrow-screen keyboard destination failure remains in `browser-report.json`
and `browser-results/`. The final harness explicitly waits for visible keyboard
focus before navigation. No product timing-cause or navigation-repair claim is
made. Framework-owned browser/context resources closed after the run; no
personal profile was used.

The unchanged generic skill checker reports 5 pre-existing profile/markup
diagnostics for this page and is not claimed passing. It does not accept all
repository-supported local navigation, embedded asset/footer markup and special
course profiles. No new diagnostics were introduced; conceptual lesson-field
ordering findings are resolved. Independent metadata-derived source allowlists
and repository source checks pass; browser checks observed no network assets.

No dependency installation, runtime activation, live lab execution or external
publication occurred. Earlier review sections below are historical and apply
only to their recorded artifact identities and qualification scope.

## Historical glossary and reference ordering review

Current `index.html` SHA-256: `1f0ba18758043d7ea66d4f9bf45392c59bb77a933e236beaf2e39d463787c038`.

All seven courses retain Glossary immediately before References in content and
navigation. All 84 conceptual lessons and five performance-tool guides now have
an A–Z local Glossary after Mental model; optional lesson References come last.
The advanced course remains labs-only. Existing teaching and all 357 protected
lab, launcher, environment, practical-guide and diagram files are unchanged.

Source/static: all seven course validators, 230 focused tests, generated-page
and helper parity, scoped Ruff/formatting, Markdown and whitespace checks pass.
Checks reject absent, unsorted, duplicate, misplaced or altered local glossary
entries and misplaced References. The generic skill checker reports 5
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

Current `index.html` SHA-256: `456cf10ec471e200b25cdfd38f89af43ec2dff23ec4c89ce2b3f05a0fb6984d5`.

All 16 practical labs completed both profiles: 32 verified prepared-H200 exports. Original result verification, applicable native report checks, actual Grafana/Nsight screenshot reviews, public artifact hashes and ZIP contents passed. See [campaign coverage and limits](../docs/profiling-validation.md) and [observed versions](VERSIONS.md). H100 and clean-install qualification remain separate.

Reviewed the course teaching, practical guides, READMEs and supporting references against their supplied implementations. Corrected workload-profile descriptions, stale cross-course references and claims that exceeded the experiments' measured behavior. Preserved lesson identities, Practice sections, the text-only Soperator course and the advanced labs-only route. Training Lab 31's only code change in this documentation pass corrects its communication-course reference string; its computational AST is unchanged.

Source/static: all seven repository validators, generated-page freshness, shared helper/dashboard parity and configured Markdown checks pass. The focused documentation/content suite passed 333 tests with five skips. Results retain the exact source identities under which they ran; editorial updates do not rewrite campaign provenance.

Browser: all 27 Playwright Test cases passed across the catalog, shared guide and seven courses in owned, isolated headless Chrome 153.0.8010.53. Each page was checked at 1440, 390 and 320 pixels for local fragment targets, loaded images, keyboard skip navigation, applicable TOC interaction, local keyboard scrolling and enlarged root/body text reflow. No document-width overflow or browser script error was observed. Desktop and 320px captures of all nine pages were visually reviewed. This is sampled visual review, not an exhaustive accessibility audit. The passing run retained screenshots and JSON identities; traces were configured only for failures, and none occurred. Framework-owned browser/context resources closed when the test process exited.

Private review artifacts: `intake-01a0d07b-final-audit/documentation.spec.cjs`, `playwright.config.cjs`, `browser-results.json`, `browser-identities.json` and `browser-results/` screenshots. No external publication was performed.

The generic skill checker still reports the documented local-link, footer/embedded-markup and special-profile differences. Its result is not represented as passing; embedded source bytes and membership pass the repository checks. Earlier records below retain their original artifact identities and historical pending statements; they do not override this section's current campaign scope.

## Historical catalog order review

The catalog and all course switchers now begin with Soperator, followed by GPU
Fundamentals, GPU Performance Optimization, LLM Training, LLM Inference, Custom
CUDA Kernels and advanced communication labs. Lesson and lab identities are unchanged.

Reviewed `index.html` SHA-256: `7e0847c60d5ff28fa10418e964b8d4f6e29ebecccf735bdbdb637736bce7cdd5`.
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

Current `index.html` SHA-256: `aa144eea32d39de116a2f4afcb0cd6d5c20cb4d4d67ae6c5b90c4485d490a245`.

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

Current `index.html` SHA-256: `4d58a3357e0ca2ea0f789368236dcfbe03a5806f53ffd17afbf633346bfd16db`.

All executable lab guides now declare applicable Systems captures or explicit exceptions, exact report views and their own Grafana dashboard imports. Source and standalone-package checks pass. The final CPU suite passed 1,151 tests plus two separate loopback fixtures. Browser evidence covers this page at 1440, 390 and 320 pixels, including rendered lab instructions, matching dashboard bytes, downloads and 200% reflow. Initial hidden-link and intermittent heading-position failures are retained separately; focused repeated checks passed after contents-layout synchronization in the harness. No UI repair is claimed from those repeats.

See the [all-lab audit](../docs/lab-profiling-audit.md) for exact coverage, exceptions and limits. Live H100 captures, native profiler qualification and Grafana ingestion/rendering remain pending. Earlier hashes and test counts below describe their original artifacts.

## Earlier series format review

Current `index.html` SHA-256: `7a42db08e08ea3a4d08b7f3d742be5c903dad976901ae0dc51bc72e4e2d540cd`.

Source and static checks pass for all seven courses. The full CPU suite passed
1,019 tests plus two separate loopback fixtures; later focused format and prose
checks passed 318 and 91 tests respectively. These counts overlap.

This page passed desktop, 390px and 320px checks in the final 24-case owned,
isolated headless Chrome 153.0.8010.48 run. Contents navigation, keyboard use,
local scrollers, whole-page 200% text reflow and applicable downloads passed.
The current shared headings, titles and navigation replace earlier presentation
findings below. Browser resources were closed by Playwright.

The generic skill checker still reports 5 documented profile/format
findings; it is not represented as passing. Installed environment, Runtime activation and Live H100 qualification remain pending; source and browser success do not establish live performance.
See the [series format validation report](../docs/course-format-validation.md)
for exact scope, artifact identities, findings and evidence limits.

## Earlier review records

The records below describe previous artifacts and narrower checks. Their hashes,
counts and unresolved presentation findings are historical; the current review
above supersedes them for format and navigation only.

| Evidence lane | Status |
| --- | --- |
| Advanced-course migration | Current `index.html` SHA-256 `e13b47013ebaf1eb9d9fd1d79789f10351ac8da0a3f2ffa4d63e64c91fac469b`. Distributed practical ownership now resides in the seventh course; conceptual lessons remain. All repository validators pass. Final owned headless Chrome 153.0.8010.48 checked this page’s seventh-course navigation at 390px, including migrated practice links where present; earlier browser/runtime evidence below remains historical. Shared setup-dashboard downloads and kit bytes pass source checks. See [advanced-course validation](../docs/advanced-course-validation.md) for scope and pending live qualification. |
| Simple setup and advanced fabric route | Final `index.html` SHA-256 `3e7a6e46216fae6804f2a2ecfca2a3bd35fcbabef30dd3a56ce4dfc99c085496`. Source/CPU checks: 967 tests across the full run and two permitted loopback fixtures; final setup follow-up: 45 passed. All five validators, dashboard/helper/source-kit parity, configured Markdown, shell checks and Python formatting pass; no new Ruff findings. All 15 owned isolated headless Chrome 153.0.8010.48 Playwright Test cases pass at 1440, 390 and 320 pixels, including advanced route ordering, keyboard navigation, fragments, per-lab dashboard downloads and complete kits. Five new diagrams were inspected; captions match their top-to-bottom flow. Setup and new headings fit enlarged text. Existing whole-page enlarged-text limits and eight installed-skill format findings remain separate. Task-local evidence: `course-fabric-revision-20260918/browser-final5.json`, screenshots and traces; framework-owned contexts and browsers closed. No live sixteen-H100, GPU-buffer registration, profiler/runtime or deployed Grafana qualification was performed. |
| Profiling alignment follow-up | Final `index.html` SHA-256 `c5f363c02c2175979f964aacfccf6b5cafb9ccefd7891d8f630c12660c8f4cd1`. All 15 isolated headless Chrome 153.0.8010.48 Playwright Test checks pass at 1440, 390 and 320 pixels after the historical-time query and Lab 09 comparison repairs. Downloaded dashboard assets, standalone kits, keyboard navigation and setup/primer enlarged-text checks pass. Whole-page enlarged-text overflow remains unchanged from the alignment baseline. Task-local evidence: `course-profiling-align-20260917/browser.json`, screenshots and traces; framework-owned browser resources closed. Actual Grafana query execution and H100 qualification remain pending. |
| GPU profiling integration | Final `index.html` SHA-256 `dc1b8798cb795f3b12434ac659e6046c0e42024505f7174e0ac1b74221f7fac3`. Owned isolated headless Chrome 153.0.8010.48 through Playwright Test: all 15 course/viewport checks pass at 1440, 390 and 320 pixels, including fragment links, keyboard TOC, command scrolling, dashboard downloads and the standalone lab kit. The new primer and setup fit 200% root text at 320 pixels. Whole-page enlarged-text overflow remains identical to the task-start artifact; eight existing installed-skill checker findings remain unchanged. Representative final primer screenshots were reviewed. Task-local evidence: `publication.spec.cjs`, `browser-trial3.json`, per-test screenshots and traces; Playwright-owned contexts/browser and the separate exploratory browser/server were closed. Source and live evidence are separated in [profiling validation](../docs/profiling-validation.md); live qualification remains pending. |
| Lab alignment follow-up | Removed residual reading detours, brought the existing optional CUDA configure/build/test procedure into its owning Lab 10, and corrected duplicated or unclear instructions. Empty setup files now produce actionable errors, and standalone validators reject reference-destination drift. Six negative regression cases failed before the repairs and pass afterward. All 885 offline tests, five validators, generated parity, scoped lint, independent code/content/security review and unchanged canonical-spec validation pass. Original executable behavior and all 100 prior lab command blocks are preserved. |
| Prior browser qualification | Final `index.html` SHA-256 `231080c495a1a5ec472ac6c99d6ef8b047b934913e80c4688697604dba38a3bb`; owned isolated headless Chrome 153.0.8010.48. All five pages pass normal-layout, fragment, keyboard-TOC and command-scrolling checks at 1440, 390 and 320 pixels. Setup and the relocated CUDA procedure were visually sampled. Task-local artifacts: `browser-review.cjs`, `browser/report.json`, setup/Practice/optional-build screenshots and `browser/trace.zip`; browser and context cleanup confirmed. Setup fits 200% root text at 320 pixels, but whole-page enlarged-text overflow remains identical to the task-start baseline. Eight unchanged installed-skill checker format findings and target-runtime qualification remain open; no cloud or lab execution occurred. |
| Self-contained lab revision | Revised all 94 executable guides across five courses: concise purposes, local explanations, no Theory preparation reading lists, and short Practice commands. Added setup-only Lab 00 to each package; preserved former preflight exercises as dedicated verification labs. All 100 original Bash blocks and executable source behavior are preserved; five source identities were renamed consistently. All 879 offline tests, five validators, generated parity, changed-source lint and scoped review pass. Installed-environment, Linux/CUDA activation and live H100/Slurm qualification remain pending; no deployment or lab execution was performed. |
| Lab revision browser evidence | Artifact `index.html`, SHA-256 `b8d7fde10e90cbd6106aa8dced64a353bcde4cf61d61a637fa5344e9129df47a`. Owned, isolated headless Chrome 153.0.8010.48; all five pages checked at 1440, 390 and 320 pixels. Normal layout, fragment targets, keyboard TOC and command-scroller interaction pass. The new setup guide fits 200% root text at 320 pixels. Whole-page enlarged-text overflow persists at the same widths as the task-start pages, so full reflow qualification remains open. Representative setup and Practice screenshots were inspected. Task-local evidence: `browser-review.cjs`, `browser/report.json`, `llm-training-<width>-setup.png`, `fundamentals-<width>-practice.png` and `browser/trace.zip`; owned context and browser closed. The installed skill checker retains the same eight pre-existing format findings on original and revised pages; source-list membership and bytes pass. |
| NVIDIA terminology review | Reviewed 16 lessons and 24 lab guides; aligned checkpoint/recomputation terminology with the actual alternate-block and all-block variants, and clarified precision and communication timing against official NVIDIA and owning-framework references. Canonical prose, glossaries, connected diagram labels/captions, syllabuses, worksheets and reference lists aligned; all five textbooks rebuilt. All 766 offline tests, five validators, generated parity and changed-source lint pass. All 94 executable lab sources and their output keys are unchanged. Seventeen changed figures were rendered and inspected; a roofline label collision was corrected. Browser checks loaded every textbook at 1440, 390 and 320 pixels with no document-level horizontal overflow or missing fragment targets; sampled text/reflow, lab navigation, keyboard TOC operation and source scrolling passed. This is not exhaustive page-by-page accessibility or zoom qualification. The installed skill checker reports eight identical pre-existing format failures on current and task-start snapshots; embedded source membership and bytes pass. Independent review found no outstanding terminology defect in the changed material. Installed-environment and live CUDA/H100/Slurm qualification remain pending. |
| Lesson structure and diagram revision | All 73 lessons now use conceptual titles and Objective → How it works → Practice labs → Mental model. Definitions and causal explanations retain useful prerequisites; unfamiliar terms and worked reasoning are refined. Added 13 core diagrams (122 total), with at least one inside every How it works section. All 94 executable labs are unchanged. Five validators and generated parity pass; 632 shared tests pass, 133 skip, and one fails because PyTorch is unavailable (same failure on the starting snapshot). Final editorial/publication/diagram tests: 261 passed, eight dependency skips. Independent semantic/code review, lint and 13 rendered-asset inspections completed. The installed skill checker has the same six format incompatibilities before/after; source listing membership and bytes pass. Browser security policy blocks local-page inspection, so desktop/mobile browser and runtime qualification remain pending |
| Mechanism clarity revision | Revised all 73 lesson mechanisms across the five courses with causal prose, defined quantities and preserved worked examples. Reviewed all 109 figures; improved all 54 overview captions/layouts and five detailed diagrams. All 748 offline tests, five course validators, exact generated-source parity, changed-source lint/format, specification validation and whitespace checks pass. The 59 changed figures were directly rendered and inspected, including corrected roofline and copy-completion labels. All 94 lab guides and executable labs, commands, metadata, syllabi, dependencies and shared styles remain unchanged. The installed course-skill checker reports the same six publication-format incompatibilities on current and hash-matched task-start pages; embedded-source membership and bytes pass. Browser policy blocked full-page inspection; browser and target-runtime evidence remain pending |
| Audience-routing cleanup | Removed separate learner/engineer routes and repeated reading instructions from all five courses, missions and READMEs. Worked examples and concrete prerequisites remain. Rebuilt all pages; source parity, five standalone validators, 103 focused tests and configured Markdown lint pass. Browser and H100 qualification remain pending |
| Architecture and lab-prose revision | All five pages rebuilt; standalone validators and 742 offline tests pass. Lab-specific setup, fixture explanations and result recipes are consolidated in owning guides; conceptual examples and prerequisite routes are preserved. Fundamentals adds an H100 SXM SM/L2/HBM overview before the SM enlargement, with separate hardware counts and residency limits. Table parity rejects changed or missing cells and preserves fenced literals. The new SVG was inspected at 900 and 320 pixels. Full-page browser review remains pending after local-file access was denied. The installed skill checker reports pre-existing incompatibilities with catalog markup, identical on HEAD and revised pages; it is not a passing gate. No new GPU/runtime or publication qualification is claimed |
| Post-refactor course alignment | Reviewed all five courses against the approved theory/Practice boundary. Moved remaining packaging and network-tuning procedures into their owning labs while retaining lesson concepts; added explicit prerequisite routes to Training and Inference quick starts. Corrected stale specification wording and inventory. Repaired table-aware narrative validation in the shared template and all five copies, with nine positive/negative controls that retain literal prose/code pipes. All 703 shared tests, five validators, exact source-to-HTML parity, five bounded HTML/source checks, changed-source lint, 154 shell-example syntax checks and publication-safety checks pass. All original course files, lab commands, runtime sources, launchers, diagrams, dependencies and styling remain. Independent content and code rechecks found no remaining actionable issue in these repairs. Browser/keyboard/reflow and live H100 qualification remain pending |
| Lesson-to-lab Practice refactor | All 73 lessons retain definition-led theory and finish with exact Practice labs links. All 94 guides integrate applied examples and execution in Practice, with hardware context, evidence, interpretation, trade-offs, troubleshooting and review in their relevant sections. Every moved passage is accounted for; 100 original guide command fences, 189 executable/launcher/diagram files and all 108 SVG payloads are unchanged. Ten applied figures now have lab homes. All 694 shared tests, five standalone validators, source-to-HTML parity and five bounded HTML/source checks pass; the bounded checker uses the requested Practice heading with all other checks unchanged. Changed Python and Markdown, 154 shell examples, publication safety and whitespace checks pass. Independent content and implementation reviews found no remaining concrete issue. Browser/keyboard/reflow and live H100 qualification remain pending |
| Table of contents review | Verified all 243 TOC links across the five courses, including 73 lessons, 94 labs and 46 supporting guides. Titles, ordering, targets and unique IDs agree with the canonical sources. Corrected the shared closing-section heading to match the Where to Go Next link and source title. All five standalone validators now reject mismatched closing headings or TOC labels. The focused regressions failed before the fix and pass afterward; all 660 shared tests, five validators and five bounded HTML/source checks pass. Independent source review found no remaining TOC issue. Numbering, navigation order, anchors, styles and lab code are unchanged. Full-page browser and keyboard/reflow checks remain pending under the existing browser-policy restriction |
| Full wording and grammar review | Proofread all 168 Markdown files, including 73 lessons and 94 lab guides, plus diagram labels, accessible descriptions and human-facing source text. Corrected grammar, awkward phrasing and unclear references while preserving technical meaning. Independent rechecks closed the identified findings. All 660 shared tests pass in an isolated copy of the task-start sources with only these proofreading edits applied. The current course pages pass all five validators, five bounded HTML/source checks and exact source-to-HTML parity; all 168 Markdown files and 155 shell examples pass lint/syntax checks. Changed Python code retains the same executable syntax tree, and commands, headings, identities and SVG geometry are preserved. The two diagrams with changed visible labels were rendered and inspected at 1200- and 320-pixel widths. This is editorial, source, CPU-test and asset-level evidence; full-page browser review and target-runtime qualification remain pending |
| Final topic and numerical alignment | Reviewed all 73 lessons and 94 lab routes against their sources. Corrected prior-theory ordering, preflight claims, a relative-error assumption and a reduction example. Five numerical gates now reject non-finite results; the Training and Inference guides explain the checks and collective verdict. Independent rechecks found no remaining concrete issue in these repairs. All 660 shared tests, including 87 focused numerical controls, five course validators, exact source-to-HTML parity and five bounded HTML/source checks pass. Configured Markdown and shell-example syntax checks pass. All original files, lesson/lab identities, diagrams, styles and dependency settings remain. This is source and CPU-test evidence; browser review is blocked by the existing policy and target-runtime qualification remains pending |
| Prior theory for every lab | Reviewed all 94 labs against their actual computation, measurement, validation and coordination paths. Expanded 41 owning lessons with definitions, purpose and basic usage; all guides now name their theory preparation. Syllabi distinguish reading previews from first full execution, including separately gated modes. Current checks: 573 shared tests, five validators, exact source-to-HTML parity and five bounded HTML/source checks pass; 168 Markdown files and 154 shell examples pass lint/syntax. All 420 original files remain and all 247 non-Markdown/non-HTML files are unchanged. This is source/editorial and local-test evidence; the existing browser-policy block and pending target-runtime qualification remain |
| Topic-level conceptual definitions | Reviewed all 73 lessons, 94 lab guides and 27 optional study topics across the catalog, including later subtopics. Added definitions before applications and clarified new vocabulary, formulas and mechanism distinctions. Independent read-only rechecks closed the identified gaps. All 573 shared tests, five validators, exact source-to-HTML parity and bounded HTML/source checks pass. Markdown lint covers 168 files; 154 fenced shell examples pass Bash syntax checks. All executable sources, commands, lesson identities, diagrams and shared styling are preserved. Semantic and static checks do not establish full-page browser or target-runtime qualification; browser inspection remains blocked by the existing policy |
| Catalog-wide editorial alignment | Reviewed all 73 lessons, 94 lab guides, front/back matter, supporting runbooks and 108 diagrams. Corrected technical wording, units, comparison controls, grammar, glossary format and accessible diagram text while preserving executable code and the shared stylesheet. All 573 shared tests, five validators and bounded HTML/source checks pass. Markdown lint covers 168 files; all 153 shell examples pass syntax checks. Six documented CPU KV-policy commands pass. All SVG assets were rendered at 320 pixels and up to 1200 pixels; overview sheets and changed assets were inspected. Independent rechecks found no remaining concrete editorial issue. This is source/CPU/asset evidence only; full-page browser review remains blocked by browser policy and live GPU qualification remains pending |
| Transfer-profiling adoption and publication alignment | Added complete H2D/D2H, real DDP-hook and modeled KV-tier practice across their three owners, with four guides and diagrams. All 573 shared tests, five course validators and the course skill's bounded HTML/source checks pass. New CPU controls cover ownership, failures, numerical references and cache policies; a mocked two-node Nsight launch checks arguments, private output and error propagation. Six direct CPU policy scenarios pass. Four new diagram assets were rendered and inspected at 420- and 320-pixel widths. Changed-source lint/format, shell checks, Markdown and full source parity pass. These are source, CPU and asset-only results. Clean PyTorch 2.14 installation, live H100/CUDA/NCCL, actual Nsight/Slurm behavior and GDS/engine performance remain pending. Browser policy blocked local HTML, so full-page desktop/390/320 review remains pending |
| Executable-review alignment | All five validators and 546 shared tests pass locally. Fault injection verifies rejection of omitted training work and non-finite KV-cache comparisons; inference measurements retain no autograd graph. The BF16 library-first lab accepts valid intermediate rounding against an independent FP64 reference and rejects corrupted outputs before timing. Standalone validators compile without persistent temporary bytecode. Ruff/check/format covers 116 Python files; Bash syntax and ShellCheck cover 32 launchers. Generated HTML matches canonical sources. No H100, CUDA Graph, Slurm, engine or PyTorch 2.14 installation qualification is claimed |
| Quick-start and engine-handoff alignment | All five validators and 502 shared tests pass locally, including 15 new quick-start and Triton handoff regressions. Candidate setup is separated from qualified target execution; submitting-shell privacy gates and environment links are consistent. The Triton launcher passes the canonical output directory and exports the server/client run ID. Independent read-only review found no outstanding issue in these repairs. All 115 Python files pass Ruff/format; configured Markdown and 32 shell syntax/ShellCheck checks pass. The task-start comparison found no removed files or changes to lesson text, syllabi, mappings, styles or diagrams. Full source-to-HTML parity passes. This is source/CPU-fixture evidence, not installed-environment, live Triton/Slurm/H100 or full-browser qualification |
| Source and static validation | Passed locally 2026-09-05: all five validators and 464 shared tests. Editorial review covered all 72 lessons, 88 lab guides, syllabi, glossaries, learner READMEs, supporting runbooks/worksheets, diagram captions and all 99 SVGs' visible labels, titles and descriptions. Corrections clarify assumptions, timing boundaries, numerical criteria, first-token accounting, actual lab capabilities and prerequisite routes while retaining detailed teaching material. Nine new regressions guard selected semantic distinctions; automated checks do not establish comprehensive grammatical or educational correctness. All 72 lesson titles and teaching-field sequences, 88 lab-guide structures and 99 diagram geometries remain; no files were removed. All 171 executable/launcher/diagram asset files are unchanged in this editorial pass; three generated diagram descriptions were clarified. Source-to-HTML parity, changed-Python Ruff/check/format, configured Markdown, publication-safety and whitespace checks pass. Editorial review and static/CPU-fixture success are not browser, CUDA, NCCL or H100 proof |
| Further-study research and integration | Reviewed 2026-09-05: 27 optional topics across five courses, with plain-English definitions, study questions, official sources and hardware/maturity limits. Independent review replaced a deprecated model guide and verified the replacement. Sixteen new regressions cover closing placement, complete prose, sidebar navigation, narrow official links and canonical bibliography destinations; all 464 tests and five course validators pass. Existing lessons, labs, environment pins, guided hours and 99 embedded diagrams are preserved. This records documentation and local integration evidence, not installed-tool, browser or H100 qualification |
| Diagram asset review | Rendered all 99 SVGs locally at 1200- and 320-pixel widths with embedded course styles. Inspected the five new or relocated opening workflows, including the H100 physical/logical map and host/API/device path; corrected a heading-crossing transfer arrow. A conservative Arial/Verdana glyph and connector audit reports no findings. Native title/description and visible captions remain. This is asset-level evidence, not full-page browser layout proof |
| Installed environment | Existing PyTorch 2.13/Transformers 5.16.1 environment is internally consistent but stale; clean 2.14.0, Transformer Engine, and Linux/H100 qualification pending |
| Runtime activation | CUDA/model/Transformer Engine activation pending |
| Introductory CPU mechanics | Lab 32 executed with existing local PyTorch 2.13.0: initial loss 10, first gradient -10, learned weight approximately 2, held-out prediction approximately 6, no inference update. This does not qualify the clean target environment or H100 performance |
| Live H100 completion | Pending user-provided Slurm cluster |
| Browser qualification status | See current browser qualification above. Normal-width desktop/mobile inspection passes; whole-page enlarged-text qualification remains open. |
| Publication privacy review | No sensitive values found in publishable sources; raw environments, builds, results, logs, and profiler artifacts are excluded and must remain private |

Publish no training
data, checkpoints, prompts, model outputs, scheduler identity, hostnames, or
unverified speedup, quality, MFU, or scale claims.

### Sixth-course navigation alignment

The catalog and switcher now include the independent text-only Soperator course.
This course's lessons, lab implementations and runtime configuration are unchanged.
The shared validator copy recognizes the sixth navigation destination; its GPU
validation contract remains intact. Source checks and a 390px headless Chrome
switcher test pass. Current HTML identity and scoped browser evidence are in
[the Soperator integration validation](../docs/soperator-course-validation.md).
Earlier GPU publication reviews describe their own artifact revisions; live
hardware/runtime qualification remains unchanged.
