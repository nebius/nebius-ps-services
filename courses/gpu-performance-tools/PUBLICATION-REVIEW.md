# Publication review

## Nine-course alignment — 2026-10-05

Reviewed `index.html` SHA-256: `68a02c5f4476df6fd32daadd7524c198f4427a59bd46e674dcef5bac9f6b119f`.

The complete nine-package alignment passes 2,503 offline tests, all nine native
validators, build/helper parity and the align review/lint/security lanes.
Responsive checks cover every page at desktop, 390px and 320px; final guide and
PyTorch overview edits have fresh focused checks. See the
[current alignment evidence](../docs/course-format-validation.md#nine-course-alignment--2026-10-05)
for fixes, exact browser artifacts, preserved assets and remaining generic-checker,
dense-diagram and live-qualification limits. Earlier entries retain their own scope.

## Teaching consistency review — 2026-10-05

Reviewed `index.html` SHA-256: `2f3dfd9f57aa53312793a1f2346ff1095604b4b0f033eba25bc9664939a4884e`.

The catalog-wide semantic review covers all 89 chapters and 110 lab guides.
Safe corrections align teaching, glossary entries and commands with supplied
implementations while preserving this course's declared profile. Source/static
validation passes; see the [complete consistency review](../docs/course-format-validation.md#complete-teaching-consistency-review--2026-10-05)
for corrections, test results, artifact identities and remaining generic-checker
and mobile-diagram limitations. Browser and live GPU qualification were not rerun.
Earlier entries below apply to their own artifact identities.

## Catalog presentation audit — 2026-10-05

Reviewed `gpu-performance-tools/index.html` SHA-256:
`ada9be047b4ced5d1642aad60a213123448f6ace93321d7895967ed4d9c7fe6d`.

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

Inspected `gpu-performance-tools/index.html` SHA-256:
`ada9be047b4ced5d1642aad60a213123448f6ace93321d7895967ed4d9c7fe6d`.

See [catalog preparation validation](../docs/catalog-preparation-validation.md)
for the eight-course/110-lab audit, 536 passing tests, native validators, artifact
identities and browser evidence at desktop/390px/320px. The final browser run had
one intermittent 320px guide navigation failure; three unchanged affected-case
replays passed. The observation and generic-checker limitations remain recorded.
Installed/runtime/live qualification was not rerun. Earlier entries apply to
their original artifacts.

## Collection and query diagram review — 2026-10-02

Replaced Lesson 5's text sketch with `reference/diagrams/tools-metrics-paths.svg`,
using the existing palette, typography and rounded cards. The diagram separates
sample collection from Grafana requests and returned data, identifies VMAgent as
the scrape initiator, and places panels inside Grafana. Its authored marker and
manifest entry give it one inline home immediately after the explanation.
This fulfills the existing REQ-002 and FEAT-040 presentation contract.

Source/static checks pass: 93 contextual-figure and reading-course tests, both
reading validators, generated-page freshness, configured Markdown lint and
whitespace checks. Read-only semantic, wiring and passive-asset review found no
remaining issue. Other course HTML retains its task-start hashes. The generic
skill checker retains the same seven existing profile/markup diagnostics listed
in the build audit below; it is not claimed passing.

Three Playwright Test cases pass in owned isolated headless Chrome
154.0.8037.93 at 1440×1000, 390×1000 and 320×1000 with page JavaScript disabled.
Checks cover exact inline placement, accessible title/description, text fit and
non-overlap, page overflow, keyboard TOC controls, applicable local scrolling,
doubled-text reflow and zero automatic network requests. Final captures at all
three widths were visually reviewed. The inspected `index.html` SHA-256 is
`54305513c9ca394bb9f3bbb118d45e3134282be6ac92a7f5438baecc9bb5b510`.

Local evidence group `metrics-diagram-4zlfdgi4` contains `diagram.spec.cjs`,
`playwright.config.cjs`, `browser-verified.json` and
`browser-verified/diagram-metrics-diagram-in-complete-course-WIDTH/` with
`diagram.png` and `context.png`. Traces were disabled; owned contexts and
browsers closed. Earlier label-fit failures and harness-only selector/style
injection failures remain separate from the final passing run.
Installed-toolchain, runtime and live-target qualification remain unchanged;
this diagram revision performs no lab execution or external publication.

## Tools-first navigation review — 2026-10-02

Source/static and browser checks pass for this navigation revision. GPU
Performance Tools precedes GPU Fundamentals in every course/guide menu.
Inspected `gpu-performance-tools/index.html` SHA-256:
`b72be28fac03275b00219507736ff6e790239868b7caaf3bbcc028ff73e7bef9`.

See [catalog navigation validation](../docs/catalog-navigation-validation.md)
for 243 focused tests, all eight native validators, 30 isolated headless Chrome
checks at desktop/390px/320px, exact evidence paths and cleanup. Existing lesson
bodies, lab sources, figures and results are preserved. The generic checker
retains its pre-existing profile/markup limitations. This is local navigation
evidence; runtime qualification and deployment are not claimed. Earlier review
entries describe their own artifact revisions.

Reference-only course; no runnable labs, exercises, runtime setup or result archives. Diagrams are synthetic teaching assets. Native flags are grounded in official tool documentation; actual installed Slurm, CUDA, profiler and monitoring qualification remains separate.

## Build and browser audit — 2026-10-02

Local build/static and browser checks pass for this page. See the
[complete eight-course build audit](../docs/course-build-validation.md) for
484 focused tests, all eight native validators, preservation checks, browser
assertions and the generic skill-checker limitations. This audit preserves
the existing teaching profile and does not change runtime qualification.

Inspected artifact: `gpu-performance-tools/index.html`, SHA-256
`f019f39c6ab42c1caff4bb57272bb9abcefaa1fc0ccd6a5218ac02563ec07b60`.
Owned isolated headless Chrome 154.0.8037.93 rendered the complete page at
1440×1000, 390×1000 and 320×1000 with JavaScript disabled, keyboard navigation,
local scrollers, doubled-text reflow and zero automatic network requests.
Visual captures were reviewed. Evidence group `course-build-audit-l57_57x5`,
`browser-complete/publication-gpu-performance-tools-publication-WIDTH/`,
contains the captures; `browser-complete.json` includes identity and assertion
results. Final traces were disabled. Owned browser resources were closed.
No live lab execution or external publication was performed.
