# Publication review

## Teaching consistency review — 2026-10-05

Reviewed `index.html` SHA-256: `fa810be9e467e4bdf72f8042c315f2113ee997111464b6997e94004cafd3de64`.

The catalog-wide semantic review covers all 89 chapters and 110 lab guides.
Safe corrections align teaching, glossary entries and commands with supplied
implementations while preserving this course's declared profile. Source/static
validation passes; see the [complete consistency review](../docs/course-format-validation.md#complete-teaching-consistency-review--2026-10-05)
for corrections, test results, artifact identities and remaining generic-checker
and mobile-diagram limitations. Browser and live GPU qualification were not rerun.
Earlier entries below apply to their own artifact identities.

## Catalog presentation audit — 2026-10-05

Reviewed `soperator/index.html` SHA-256:
`e6c86ee693875f1fbf82f7d125f7d9f9d7d680f3b1054c50f74eaafe2ad32647`.

The complete eight-course review confirms shared typography, declared profile
formatting, navigation and current HTML. All eight native validators and 380
focused tests pass; three PyTorch runtime checks are skipped. Thirty headless
Chrome 154.0.8037.93 checks cover all ten pages at 1440, 390 and 320 pixels,
including keyboard controls, local scrollers and doubled-text reflow. These
artifacts are unchanged by the build. Mobile diagram density and generic
checker diagnostics remain explicitly qualified in the
[complete evidence and artifact record](../docs/course-format-validation.md#complete-catalog-presentation-audit--2026-10-05).
No live installation, GPU run or external publication is established.

## Shared preparation referral review — 2026-10-03

Current preparation teaching points once per course entry or lab guide to the
shared Lab Guide, which selects the script by course and lab number. Duplicate
installer commands and competing manual setup procedures were removed while
preserving lab-specific prerequisites, native commands and historical results.

Inspected `soperator/index.html` SHA-256:
`e6c86ee693875f1fbf82f7d125f7d9f9d7d680f3b1054c50f74eaafe2ad32647`.

See [catalog preparation validation](../docs/catalog-preparation-validation.md)
for the eight-course/110-lab audit, 536 passing tests, native validators, artifact
identities and browser evidence at desktop/390px/320px. The final browser run had
one intermittent 320px guide navigation failure; three unchanged affected-case
replays passed. The observation and generic-checker limitations remain recorded.
Installed/runtime/live qualification was not rerun. Earlier entries apply to
their original artifacts.

## Tools-first navigation review — 2026-10-02

Source/static and browser checks pass for this navigation revision. GPU
Performance Tools precedes GPU Fundamentals in every course/guide menu.
Inspected `soperator/index.html` SHA-256:
`b1184c1f90073a3bdf64c2fe15fc3946d89198ae6d2758cf0e50012f1f6b4ffc`.

See [catalog navigation validation](../docs/catalog-navigation-validation.md)
for 243 focused tests, all eight native validators, 30 isolated headless Chrome
checks at desktop/390px/320px, exact evidence paths and cleanup. Existing lesson
bodies, lab sources, figures and results are preserved. The generic checker
retains its pre-existing profile/markup limitations. This is local navigation
evidence; runtime qualification and deployment are not claimed. Earlier review
entries describe their own artifact revisions.

## One-time setup and native commands — 2026-10-01

Artifact: `index.html`, SHA-256 `febf520917fd2c3f968b0d3d87c5646a0c5cd4861e805e28841153aba5bce597`.
All 110 catalog labs now explain their program and show one baseline submission.
Preparation is shared; native profiler options remain visible and maintainer
campaigns retain their supported automation. Source/argv, directory protection,
CPU/rank placement, server control/failure and helper parity checks pass locally.
All seven repository-native course validators pass. The installed generic skill
checker retains its task-start format diagnostics; it is not a passing gate.

Owned isolated headless Chrome 154.0.8037.93 checked the complete page at
1440, 390 and 320 pixels. Fragment targets, keyboard menu activation, local code
scrolling, normal layout and 200% text reflow pass with no automatic network
requests. Representative Practice views were visually reviewed. Task-local
artifacts: `browser.cjs`, `browser/report.json` and
`browser/soperator-<width>.png`. No trace was recorded. Contexts and browser closed.
The shared Lab Guide passed the same three viewports; its SHA-256 is
`ab05011e45c2c8b5c0bdd94fa1c26e9635e8335bf70afcc1c1ddf1cc03d54f2b`.

Installed dependencies, real Slurm/GPU execution and native profiler/report
qualification remain pending. Process spies do not prove real timeout behavior,
descendant cleanup or report flushing. Historical lab evidence is unchanged.

## Command-first lab workflow — local verification: 2026-10-01

HTML SHA-256: `004fb475f1d1ca1fd265713618018da8e0789d9b4f032789e510b62588cd64f4`.

Canonical learner instructions now show native Slurm submission with visible
private-directory preparation and exact-job log/JSON inspection. All 110 lab
prerequisite sections link once to the shared Lab Guide and retain their specific
prerequisites. The shared guide starts with basic GPU execution, explains retained
scripts, and introduces monitoring/publication and full profiler qualification
later. Workload implementations, result schemas and campaign helpers are unchanged.

Source/static: **pass**. All seven native course validators, generated artifact
freshness and shared-helper parity pass. The available offline suite has
**1,827 passed, 136 skipped and one deselected test**. Two PyTorch-dependent modules
could not be collected without PyTorch; the deselected seed test also requires it.
The focused regression run has 355 passes and two skips. New checks execute the
documented submissions with a scheduler spy, reject unsafe directories, preserve
arguments, check every learner shell block and enforce all 110 prerequisite links.
Read-only review compared 497 converted submissions with their original launcher,
Slurm and workload arguments. Scoped Ruff, ShellCheck on 35 launcher help changes,
shell parsing and whitespace checks pass. Markdown structural checks exclude the
repository's existing long-line convention; no lint configuration was changed.

Browser/layout: **pass**. Twenty-four full-page Playwright Test cases cover all
seven course pages and the Lab Guide in isolated, owned headless Chrome
**154.0.8037.58**, at **1440×1100, 390×1100 and 320×1100**. Checks cover figure
adjacency/containment, fragment and cross-page guide links, keyboard skip links,
mobile TOC interaction, local scrollers and 200% text reflow. No external requests
or page errors occurred. Nine additional visual captures cover the Lab Guide,
Fundamentals and CUDA at all three widths; representative captures were inspected.

Evidence group: `course-command-first-h1gxjdx3`, with `browser-results.json`,
per-page `browser/placement-<page>-<width>/identity.json`, `context.png`,
`visual-results.json` and `visual/visual-visual-<page>-<width>/context.png`.
Both Playwright runners exited and closed their owned browser resources; no
traces were recorded. The generic skill checker retains exactly its prior
markup/navigation/profile diagnostics and is **not a passing gate**.

Installed-environment, GPU runtime and live-target qualification: **not run**.
These source/browser checks do not qualify a cluster, compiler, container,
profiler capture or monitoring installation. No external publication occurred.

## Topic-specific diagram placement — local verification: 2026-10-01

HTML SHA-256: `004fb475f1d1ca1fd265713618018da8e0789d9b4f032789e510b62588cd64f4`.

The text-only profile remains unchanged, with no diagrams added. Included in all-course source and browser checks to verify the shared renderer preserves this exception.

Source/static: **pass**. All seven repository validators, generated HTML/archive
freshness and shared-helper parity pass. The focused publication/diagram suite
passes **257 tests**, with three existing skips. Scoped Ruff, Markdown and
whitespace checks pass. Independent source-to-HTML adjacency checks cover all
139 existing figures. The generic skill checker retains exactly its baseline
markup/navigation and explicit-profile diagnostics and is **not a passing gate**.

Browser/layout: **pass**. Twenty-one full-page Playwright Test cases use owned,
isolated headless Chrome **154.0.8037.58** at **1440×1100, 390×1100 and 320×1100**.
All figure predecessors match the source explanations; figure gaps, page/SVG
containment, label bounds, fragment targets, keyboard skip navigation, mobile
TOC, local scroller focus/scrolling and 200% text reflow pass. No external
requests or page script errors occurred.

Reviewed the unchanged text-only lesson layout in `browser/placement-soperator-{1440,390,320}/context.png`.

Evidence group: `course-diagram-placement-2yajkbyo`; assertion results and artifact
identities are in `browser-results.json`, `browser/placement-soperator-{1440,390,320}/identity.json`,
`visual-results.json` and `preservation.json`. The first assertion-run screenshots
were captured during scroll settling; the separate visual run uses immediate
positioning and confirms the figure is in view. No traces were recorded. Both
Playwright runners exited successfully and closed their owned browser resources.

Semantic review: **pass for placement** across all current figures, with short
local reading bridges where needed. Read-only code/security review found no
blocking issue; its paragraph-only test finding was resolved with general
adjacent-block comparison and list/table/worked-example coverage.
Installed-environment, runtime-activation and live-target evidence: **not rerun**;
this presentation revision preserves lab behavior and does not advance earlier
qualification. No external publication was performed.

## Course overview cleanup — current local verification: 2026-09-29

Current HTML SHA-256: `004fb475f1d1ca1fd265713618018da8e0789d9b4f032789e510b62588cd64f4`.

The overview follows the shared purpose, prerequisites and practical-scope
pattern. Soperator already matched the pattern and its teaching and HTML are unchanged.
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

Current HTML SHA-256: `004fb475f1d1ca1fd265713618018da8e0789d9b4f032789e510b62588cd64f4`.

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

## Historical: Shared jail wording — current source verification

Current `index.html` SHA-256: `e59ceaa3f692ab2fc98dc2e4bc03ac4cddb64f3017b43301395a40b53cee5c0e`.

Lesson 2 now names login, controller and worker Pods as mounting the shared
jail. It distinguishes the login-session and worker-job environment from the
controller's use of shared user information and Slurm configuration. The
node-local special-path caveat is retained. Versioned public Soperator 4.1.8
controller sources independently support the clarification and are included in
Official references. No deployment-specific evidence enters the course.

Source/static: the native text-only validator and selected-page build/parity
check pass. A task-start comparison confirms that only the intended paragraph
changed in COURSE.md and that all other course pages and the catalog are
byte-identical. Changed-scope content and security review found no new issue.
Course Markdown and whitespace checks pass. The design document retains its
pre-existing MD012 finding outside the changed feature. The generic course
checker reports the same six text-profile/shared-markup findings as before;
it is not claimed passing. The focused pytest suite could not run because
pytest is unavailable in the local Python environment; no dependencies were
installed.

Browser/visual: not rerun for this paragraph-only revision; earlier browser
evidence applies only to its recorded artifacts. Installed-environment,
runtime-activation and live-target verification are not required for this
text-only editorial change. No command examples were executed, no live target
was changed and no external publication occurred.

## Historical results naming and build budgets verification

Current HTML SHA-256: `d3fbb2cce7403d04519f11ee8119246d85a5c8bc30e418d545b55454563f1321`. All seven pages are byte-identical to the
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

Current `index.html` SHA-256: `d3fbb2cce7403d04519f11ee8119246d85a5c8bc30e418d545b55454563f1321`.

The text-only course HTML remains byte-identical and has no lab downloads.
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

Current `index.html` SHA-256: `d3fbb2cce7403d04519f11ee8119246d85a5c8bc30e418d545b55454563f1321`.

This text-only page remains byte-identical and has no lab downloads.
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

## Historical Course overview revision: 2026-09-27

Current `index.html` SHA-256: `d3fbb2cce7403d04519f11ee8119246d85a5c8bc30e418d545b55454563f1321`.

Shortened the overview from 287 to 101 words in three paragraphs. Consolidated
repeated goals, prerequisites, reading order and text-only scope. Retained the
authorized-use boundary and site resource policies. Version context remains in
lesson 2; detailed readiness checks remain in the README, syllabus and lesson
practice. All six lesson bodies are byte-identical to the pre-edit source.
Independent read-only editorial review found no lost essential context.

Source/static: the text-only course validator, selected-page build/parity check,
30 text-profile tests, scoped Markdown lint and whitespace checks pass. Shared
catalog and lab-guide output remain byte-identical. The generic skill checker
reports the same six text-profile/shared-markup findings before and after this
revision; it is not claimed passing. Changed-scope content and security review
found no new issue, active content, dependency or external-resource surface.

Browser/visual: not rerun for this overview-only revision; prior browser evidence
below applies only to its recorded artifacts. Installed-environment, runtime
and live-target checks are not applicable to this text-only editorial change.
No command examples were executed and no external publication occurred.

## Historical single course appendices review: 2026-09-25

Current `index.html` SHA-256: `3117318e250ab78bffc773ba8e7ad006ab318b049525928ae40427d7ff197d3d`.

This page has exactly one Where to Go Next, one Glossary with 21 A–Z entries,
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
and `browser-final/resumed-appendices-soperator-WIDTH/` with `identity.json`,
`glossary.png`, `next-steps.png` and `glossary-reflow.png`.
The trace-enabled resumed attempt timed out during finalization and was
interrupted; its partial traces and results remain in `browser-resumed/` and
`browser-resumed.json`. Final checks ran without tracing and exited successfully;
no final traces are available. The earlier pre-pause attempt remains separate.
Framework-owned browser/context resources closed, and the final runner exited;
no personal profile was used.

The installed generic checker reports 6 existing profile/markup diagnostics
on this page and is not claimed passing. Independent metadata-derived source
manifests were used; source membership and bytes match. Repository profile-aware
checks cover supported local navigation, embedded assets and the text-only or
labs-only exceptions. No new diagnostic category was introduced.

No dependencies were installed, labs rerun or pages externally published.
Installed-environment, runtime and live-target qualification retain their earlier
scope and limits; this editorial pass provides no new evidence for those lanes.
Earlier reviews below are historical and apply only to their recorded artifacts.

## Historical shared course format review: 2026-09-25

Current `index.html` SHA-256: `83933b04cf7a505e6eaccac377ce85b441c3194eeb44a45ee61dc4e984fb8ee6`.

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
`browser-final/course-format-soperator-WIDTH/identity.json`, screenshots and
`trace.zip` for each width. The initial browser run passed 20 of 21 cases; its
narrow-screen keyboard destination failure remains in `browser-report.json`
and `browser-results/`. The final harness explicitly waits for visible keyboard
focus before navigation. No product timing-cause or navigation-repair claim is
made. Framework-owned browser/context resources closed after the run; no
personal profile was used.

The unchanged generic skill checker reports 6 pre-existing profile/markup
diagnostics for this page and is not claimed passing. It does not accept all
repository-supported local navigation, embedded asset/footer markup and special
course profiles. No new diagnostics were introduced; conceptual lesson-field
ordering findings are resolved. Independent metadata-derived source allowlists
and repository source checks pass; browser checks observed no network assets.

No dependency installation, runtime activation, live lab execution or external
publication occurred. Earlier review sections below are historical and apply
only to their recorded artifact identities and qualification scope.

## Historical glossary and reference ordering review

Current `index.html` SHA-256: `cacb1757dd898facb61eebac134a6f2798136742f8600d18422d298ff9519c7c`.

All seven courses retain Glossary immediately before References in content and
navigation. All 84 conceptual lessons and five performance-tool guides now have
an A–Z local Glossary after Mental model; optional lesson References come last.
The advanced course remains labs-only. Existing teaching and all 357 protected
lab, launcher, environment, practical-guide and diagram files are unchanged.

Source/static: all seven course validators, 230 focused tests, generated-page
and helper parity, scoped Ruff/formatting, Markdown and whitespace checks pass.
Checks reject absent, unsorted, duplicate, misplaced or altered local glossary
entries and misplaced References. The generic skill checker reports 6
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

Current `index.html` SHA-256: `f816512a4ef9426a5b7d65a48c385513d20c9bedecfeb8716844fe9d9d0877d4`.

This course remains text-only, with no practical lab execution required. The six practical courses completed all 110 labs in both profiles; see [campaign coverage and limits](../docs/profiling-validation.md).

Reviewed the course teaching, practical guides, READMEs and supporting references against their supplied implementations. Corrected workload-profile descriptions, stale cross-course references and claims that exceeded the experiments' measured behavior. Preserved lesson identities, Practice sections, the text-only Soperator course and the advanced labs-only route. Training Lab 31's only code change in this documentation pass corrects its communication-course reference string; its computational AST is unchanged.

Source/static: all seven repository validators, generated-page freshness, shared helper/dashboard parity and configured Markdown checks pass. The focused documentation/content suite passed 333 tests with five skips. Results retain the exact source identities under which they ran; editorial updates do not rewrite campaign provenance.

Browser: all 27 Playwright Test cases passed across the catalog, shared guide and seven courses in owned, isolated headless Chrome 153.0.8010.53. Each page was checked at 1440, 390 and 320 pixels for local fragment targets, loaded images, keyboard skip navigation, applicable TOC interaction, local keyboard scrolling and enlarged root/body text reflow. No document-width overflow or browser script error was observed. Desktop and 320px captures of all nine pages were visually reviewed. This is sampled visual review, not an exhaustive accessibility audit. The passing run retained screenshots and JSON identities; traces were configured only for failures, and none occurred. Framework-owned browser/context resources closed when the test process exited.

Private review artifacts: `intake-01a0d07b-final-audit/documentation.spec.cjs`, `playwright.config.cjs`, `browser-results.json`, `browser-identities.json` and `browser-results/` screenshots. No external publication was performed.

The generic skill checker still reports the documented local-link, footer/embedded-markup and special-profile differences. Its result is not represented as passing; embedded source bytes and membership pass the repository checks. Earlier records below retain their original artifact identities and historical pending statements; they do not override this section's current campaign scope.

## Historical catalog order review

The catalog and all course switchers now begin with Soperator, followed by GPU
Fundamentals, GPU Performance Optimization, LLM Training, LLM Inference, Custom
CUDA Kernels and advanced communication labs. Lesson and lab identities are unchanged.

Reviewed `index.html` SHA-256: `40c1bcd198ec3edf4a216328c2a845d4ea9f61c12a22a26f4f300b0e532de9c6`.
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

Current `index.html` SHA-256: `5dee93bc7acc0f33da112c9353ad2124bb1d3cd4bab98590892850f88eb8c1b3`.

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

## Earlier series format review

Current `index.html` SHA-256: `5dee93bc7acc0f33da112c9353ad2124bb1d3cd4bab98590892850f88eb8c1b3`.

Source and static checks pass for all seven courses. The full CPU suite passed
1,019 tests plus two separate loopback fixtures; later focused format and prose
checks passed 318 and 91 tests respectively. These counts overlap.

This page passed desktop, 390px and 320px checks in the final 24-case owned,
isolated headless Chrome 153.0.8010.48 run. Contents navigation, keyboard use,
local scrollers, whole-page 200% text reflow and applicable downloads passed.
The current shared headings, titles and navigation replace earlier presentation
findings below. Browser resources were closed by Playwright.

The generic skill checker still reports 6 documented profile/format
findings; it is not represented as passing. No installed runtime or GPU qualification applies to this text-only course. Its command examples were not executed against a cluster.
See the [series format validation report](../docs/course-format-validation.md)
for exact scope, artifact identities, findings and evidence limits.

## Earlier review records

The records below describe previous artifacts and narrower checks. Their hashes,
counts and unresolved presentation findings are historical; the current review
above supersedes them for format and navigation only.

### Status

Authoring, source checks and scoped browser review are complete. This is an
explicitly text-only course: no executable labs, diagrams, runtime dependencies
or installation tasks are required. Command examples describe an existing
site-configured Slurm environment; none has been run on a live cluster here.

| Gate | Status | Evidence or limitation |
| --- | --- | --- |
| Source/static | Passed | Exact metadata, complete prose parity, seven-course navigation, text-only sync and shell syntax. See the validation report below. |
| Installed environment | Not applicable | Reading the course requires a browser only. |
| Runtime activation | Not applicable | No executable lab or supplied training application. |
| Live target | Not run | Illustrative commands are documentation, not cluster qualification. |
| Browser | Passed | 11 final owned headless Chrome checks: desktop, 390px and 320px, keyboard navigation/scrollers and 200% text sizing. |
| Semantic | Reviewed | SchedMD commands, Soperator 4.1.8 API/chart boundaries and PyTorch launcher semantics checked against public official sources; no external expert review claimed. |

### Teaching and safety

All six lessons have definition-first teaching, a worked example or component
comparison, an in-text comprehension check with an answer, and a final synthesis.
The six numbered titles match the syllabus and metadata. Practice requires no
execution. The text-only request explicitly excludes the standard diagram rule.

Examples are synthetic and independently authored. No private source details,
real environment identifiers, credentials or confidential excerpts are included.
Optional accounting, REST, health and telemetry behavior is qualified. Job
completion is separate from application correctness. Slurm CPUs, host memory,
GPU resources, task counts and child-worker counts have distinct meanings.

### Seventh-course navigation update

Current `index.html` SHA-256:
`ca9d6520f8d2bf0e8b95adecd743896259b1f754364546e0bb1f1b9e184691dd`.
The final advanced-course browser pass checked the new switcher at 390px in
owned isolated headless Chrome 153.0.8010.48. The six text lessons are unchanged.
This navigation evidence is separate from the earlier full text-course review.
See [the current catalog validation](../docs/advanced-course-validation.md).

### Earlier browser evidence

The earlier sixth-course `index.html` SHA-256 was
`46e618ef4c4722b71dc179a83d43cfd7d72e2503b265986a5452b08516794381`.
Owned isolated headless Chrome 153.0.8010.48 passed all 11 publication checks;
three covered this course at 1440, 390 and 320 pixels. Keyboard navigation,
local anchors, code/table scrolling and 200% text reflow passed. Visual review
corrected cramped enlarged-text table columns and aligned TOC numbering.

The private artifact set `soperator-text-course-20260918` contains
`browser-final.json`, per-case screenshots and Playwright traces. Playwright
closed its contexts and owned browser. Optional MCP exploration was not used.
Complete identities, assertions, source evidence and generic skill-checker
format differences are recorded in
[the validation report](../docs/soperator-course-validation.md).
No publication or live deployment is part of this change.

## Build and browser audit — 2026-10-02

Local build/static and browser checks pass for this page. See the
[complete eight-course build audit](../docs/course-build-validation.md) for
484 focused tests, all eight native validators, preservation checks, browser
assertions and the generic skill-checker limitations. This audit preserves
the existing teaching profile and does not change runtime qualification.

Inspected artifact: `soperator/index.html`, SHA-256
`4cade4b410df9b2f353c3c031cbcd6e60f84be957ba768f06aa04ca694d2a5a1`.
Owned isolated headless Chrome 154.0.8037.93 rendered the complete page at
1440×1000, 390×1000 and 320×1000 with JavaScript disabled, keyboard navigation,
local scrollers, doubled-text reflow and zero automatic network requests.
Visual captures were reviewed. Evidence group `course-build-audit-l57_57x5`,
`browser-complete/publication-soperator-publication-WIDTH/`,
contains the captures; `browser-complete.json` includes identity and assertion
results. Final traces were disabled. Owned browser resources were closed.
No live lab execution or external publication was performed.
