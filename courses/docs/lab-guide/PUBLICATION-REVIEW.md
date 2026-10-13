# Lab Guide publication review

## Shared burnt-orange comments — 2026-10-13

Scope: the shared stylesheet used by all nine course HTML pages and the Lab
Guide. Comments now use saturated burnt orange `#b34700`, with measured
**5.112:1** contrast against the `#f4f7f9` code background. This exceeds the
[WCAG normal-text minimum](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html).
This bounded color check is not a whole-site accessibility certification.

Source/static: **pass**. All 72 focused comment/presentation tests, all nine
native course validators, full build/freshness, scoped Ruff, repository-configured
Markdown lint and whitespace checks pass. All 452 embedded code blocks preserve
their exact parsed text, attributes and comment ranges. Comparison of eleven HTML
outputs shows only the shared color substitution and matching Lab Guide wording;
the catalog is byte-identical. Executable sources and result archives are unchanged.
Generic checker runs on before/after course snapshots retain identical diagnostics
for existing profiles and undeclared shared links; they are not a passing gate.

Browser/visual: **pass for this color change**. Playwright Test 1.57.0 drove owned,
isolated headless Chrome 155.0.8059.40 with page JavaScript disabled. Thirty
assertion cases cover all ten reader pages at 1440, 390 and 320 pixels: every
comment's computed color/background, contrast, keyboard skip/menu/TOC operation,
local code scrolling, normal/doubled-text reflow and no external requests.
Desktop and 320px PyTorch screenshots were visually reviewed; color is clear,
text remains legible, and phone code blocks retain horizontal scrolling.

Local evidence group `course-burnt-orange-tj2900ci` contains
`browser-assertions.json`, per-page/width JSON facts and
`comments-{1440,390,320}.png`. Initial artifact-capture runs timed out during
trace export and element-screenshot stability waits after page assertions had
passed. Those runs remain separate. The final 30-case run disables traces;
viewport captures use reduced motion, instant scrolling and background-throttling
disabling flags in a separate owned browser. Screenshot capture passed at all
three widths. All owned browser contexts and processes closed; no product change
was made for the harness failures. Passing traces are not available.

Installed-environment, runtime and live-target qualification: **not applicable
to this CSS-only change**; no lab or environment behavior changed or was run.
External publication was not performed. Earlier course-wide evidence retains
its original scope and artifact identities.

| Reviewed artifact (relative to courses) | SHA-256 |
| --- | --- |
| `advanced-gpu-communication/index.html` | `eec3e9a0047a93ebb7ce49ba94a48d89cdb59c429364748f7ae7715189dcb105` |
| `custom-cuda-kernels/index.html` | `3c3aab3a22b33b822c7ff18d9639000e7843ae5856ba7530d214fd07786370ea` |
| `gpu-fundamentals/index.html` | `9cf9b2ecb1bc4b0d05c2882c6d0deb86544f65a1bbceeb9e6c5e105ada3b1128` |
| `gpu-optimizations/index.html` | `2c0925d01f604f93b94cdcacf187ac1632345f6ea5aceedddb9b6095c63a9c69` |
| `gpu-performance-tools/index.html` | `dba514d24b06cb25a36f55369007ccef30c357ba97d140f96082342e59a390a5` |
| `lab-guide.html` | `fd2256d04e4a7e585a6ca4dac9c8d812ef0cae4f575e3c300d37cd262463b05d` |
| `llm-inference/index.html` | `2d9e0e532784b0b4660f4f19751ccf8e23b4bf58eab6e20604afbffe4894199f` |
| `llm-training/index.html` | `96055518f48d2acd4bdaef705d19273fef4501ba44539b70332caffbe1a66631` |
| `pytorch-gpu-performance-engineering/index.html` | `8748526dbd1785409d9ef4542c4f5062af48abc234f0804f7d4f6e0c3d2e3b92` |
| `soperator/index.html` | `0d66c033cf82b9bb92abf4c2ccd427f72ebdf01af8df5416e837bd390b751aa5` |

## Nine-course alignment — 2026-10-05

Reviewed `lab-guide.html` SHA-256: `1e536d93d4503059645c93c64f5fe21f0320a95329271a0523da8c22404f6725`.

Shared preparation now skips all three reading profiles. The final guide passes
three isolated headless Chrome checks at 1440, 390 and 320 pixels. Source/build
checks and the complete 2,503-test offline suite pass; no actual installation or
live-target qualification was performed. See the
[complete alignment record](../course-format-validation.md#nine-course-alignment--2026-10-05)
for artifact identities, preservation, browser evidence and remaining limits.

## Catalog presentation audit — 2026-10-05

Reviewed `lab-guide.html` SHA-256:
`c916d77157b9e5448a383ff37dec6cd2e6c55f502370d4bde405f4ae5985266b`.

The complete eight-course review confirms shared typography, declared profile
formatting, navigation and current HTML. All eight native validators and 380
focused tests pass; three PyTorch runtime checks are skipped. Thirty headless
Chrome 154.0.8037.93 checks cover all ten pages at 1440, 390 and 320 pixels,
including keyboard controls, local scrollers and doubled-text reflow. These
artifacts are unchanged by the build. Mobile diagram density and generic
checker diagnostics remain explicitly qualified in the
[complete evidence and artifact record](../course-format-validation.md#complete-catalog-presentation-audit--2026-10-05).
No live installation, GPU run or external publication is established.

## Course and lab-number lookup — 2026-10-03

The guide now maps all 110 course/lab numbers to their preparation groups,
including the two reading-course boundaries and optional-variant referral.
All eight courses use this shared preparation route. The sidebar follows all
four topics and 25 subsections in body order.

Inspected `lab-guide.html` SHA-256:
`d770a9ef56018aa851f805ad661d67f74d5f18e68f665a1f2c045e0f142c52dd`.

See [catalog preparation validation](../catalog-preparation-validation.md) for
536 passing tests, native validation, final artifact identities, visual review
and desktop/mobile browser evidence. One intermittent 320px navigation failure
remains recorded; three unchanged replays passed. The generic checker retains
its baseline limitations. No installation, runtime or live check was rerun.
Earlier entries describe their own artifact revisions.

## Preparation teaching and navigation — 2026-10-03

Scope: the complete shared workflow guide, authored in `README.md` and generated
as `lab-guide.html`. This reference is not a numbered course. The new Lab
Preparation Scripts topic explains prerequisites, all five preparation groups,
automated dependencies, model reuse, selection, recovery and optional containers.
The sidebar follows all four topics and 24 subsections in body order. Native
readiness examples and monitoring instructions match the current scripts.

Inspected `lab-guide.html` SHA-256:
`85fc8c30874908148ea9f533b85397b6df27993a1edaa06334311d6dafea1ded`.

| Review lane | Result and scope |
| --- | --- |
| Source/static | Pass: 166 focused tests, complete HTML build/freshness, helper parity, guide Bash syntax, scoped Ruff, Markdown and whitespace checks. New navigation tests verify ordered real targets and exclude fenced-code headings. Read-only plans accept every documented group and optional variant. |
| Semantic | Pass for the complete guide: script purposes and dependencies match the catalog; setup, preparation, submission, profiling, monitoring and result browsing agree. Installation and hardware qualification remain distinct. |
| Browser/visual | Pass: three complete-page cases in owned, isolated headless Chrome 154.0.8037.93, using Playwright Test 1.57.0 at 1440×1000, 390×1000 and 320×1000. |
| Installed environment | Not rerun: this revision changes teaching and navigation, with no preparation-engine or dependency changes. Earlier installation evidence retains its original scope. |
| Runtime activation | Not rerun: documented selections were checked read-only; no compiler, server or GPU workload was started. |
| Live target | Not rerun: no cluster operation or deployment was performed for this guide revision. |

Browser assertions cover exact topic/subsection order, unique IDs, every sidebar
target, keyboard TOC opening/closing and navigation to the preparation topic,
each script subsection and profiling. They also cover page containment, mobile
local scrollers and 200% text reflow. JavaScript was disabled; there were no
automatic external requests or page errors. Visual inspection of desktop and
mobile captures found readable prose, contained code/tables and no overlapping
navigation. Existing monitoring artwork and the shared stylesheet are retained.

Local evidence group: `lab-guide-preparation-f1alze5l`. Results and identities are
in `browser-results.json` and `browser-verified.json`; screenshots and traces are
under `browser-artifacts/guide-complete-guide-{1440,390,320}/` as `full-page.png`,
`lab-preparation-scripts.png`, `serving-labs.png`, `text-reflow.png` and `trace.zip`.
The successful runner exited with three passes and no skipped or flaky cases;
its owned contexts and browser closed. Earlier interrupted runs and harness
assertion/scroll failures remain separate evidence under `interrupted-browser-*`,
`node26-browser-*` and `fixture-browser-*`. A direct navigation probe and corrected
keyboard/reflow fixture preceded the successful run; no guide defect was
established from those harness failures.

The generic full-course skill checker reports the same 11 diagnostics before
and after this revision, including course-only structure requirements and
unsupported existing guide markup/navigation. It is **not a passing gate**.
The native builder, source parity tests and browser assertions validate this
shared-guide profile. The comparison is retained in `skill-checker.json`.

Read-only code/security review found no concrete defect in the scoped changes.
Historical results, runtime state and unrelated work remain preserved. No
external publication was performed.
