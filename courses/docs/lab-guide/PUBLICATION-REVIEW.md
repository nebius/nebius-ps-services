# Lab Guide publication review

## Unified lab preparation and TOC alignment — 2026-10-15

Reviewed HTML SHA-256: `8fbe1efa210eec1e7a81e93397f9253cbc523a3892c292a06ace652647f0adb0`.
The canonical README and generated guide retain one software-preparation command,
with completion-summary interpretation before separate GPU/profiler qualification.
All 24 guide TOC destinations pass at 1440px, 390px and 320px, with keyboard access,
local command scrolling and 200% reflow. The complete catalog passes 507 focused
tests and all nine native validators; both final browser suites pass 33 cases.
See the [catalog preparation review](../catalog-preparation-validation.md#unified-lab-preparation-and-toc-alignment--2026-10-15)
for exact evidence, final artifacts, harness history and separate live limits.
No installation, live workload or external publication occurred. Earlier entries
remain historical evidence for their recorded artifacts.

## Catalog correctness and navigation — 2026-10-13

Current HTML SHA-256: `2fda4862875918e7f188333767bd6c462011f70dc27f65fe533b64c619453c29`.
The source-owned shared stylesheet now uses native instant scrolling. The
original heading-arrival criterion passes for every Lab Guide TOC destination
at 1440px, 390px and 320px; the committed regression also passes twice at each
width. This supersedes the navigation gap for this artifact, while preserving
the failed earlier trials below. See the [catalog review](../course-format-validation.md#catalog-correctness-and-navigation--2026-10-13)
for the CSS-only causal comparison, full-page checks, repeated regressions,
exact artifacts and remaining evidence limits. No runtime or live-target
qualification or external publication was performed.

## Alignment follow-up — 2026-10-13

Rechecked the exact README and HTML hashes recorded in the next section. All
427 focused tests passed in one run (25.23 seconds). All nine native course
validators, shared-helper parity, generated freshness, 33 Bash-block syntax
checks, 12 documented preparation commands in read-only plan mode, scoped
Markdown/Ruff lint, whitespace and canonical-spec validation passed. The ten
other HTML outputs still match the authoring task's baseline. Changed-scope
command, code and security reviews found no new blocking issue; no further
source or runtime repair was justified.

Browser qualification remains open. The full Playwright interaction run passed
at 1440px and 390px, then failed at 320px: the first preparation-section link
changed the fragment but left its heading outside the viewport. A separate
probe passed all 30 heading/fragment checks across the same widths. These passing
observations do not supersede the reproduced failure or establish its cause.
The guide, stylesheet and original viewport criterion were unchanged.

A bounded eight-case 320px experiment compared viewport initialization and URL
assertions, with two trials per combination. Seven passed and one reproduced
the same failure; neither test-setup hypothesis was established as the cause.

The same local evidence group retains `align-browser-results.json`,
`align-browser-artifacts/`, `align-navigation-diagnostic.json` and the
`align-navigation-matrix-*` results. Owned browser contexts were closed. Requirement
REQ-029 remains active and FEAT-045 remains implemented, with full browser
verification pending. No installation, cluster operation, GPU execution or
external publication was performed.

## Concise shared workflow and current commands — 2026-10-13

Scope: the shared `README.md` and generated `lab-guide.html`, the directly
related maintainer initialization paragraph, and the existing setup-order test.
The guide is a workflow reference, not a numbered course. It now puts selective
preparation before group installation, uses the compatibility lab's native batch
launcher, separates benchmark and diagnostic runs, and explains automatic
publication without repeated initialization instructions. All 110 lab mappings,
four main topics, 26 subsection targets and distinct safety boundaries remain.
Word count fell from 5,809 to 4,690 (about 19%). Runtime implementations, the
catalog and all nine course pages were unchanged by this revision.

| Artifact | SHA-256 |
| --- | --- |
| `README.md` | `2ea7447f558831696446c2145afb6c6346a635ede4f0aa898484db9fe616739b` |
| `lab-guide.html` | `0f3cab1be0ba0ae9770ab0148fc351550800fbc4f82e342cbd53965af0671016` |

| Review lane | Result and scope |
| --- | --- |
| Source/static | Pass: native preflight/atomic guide build and exact source parity; all nine course validators, aggregate helper parity and full generated freshness; 33 Bash-block syntax checks; 12 documented preparation selections in read-only plan mode; scoped Markdown, Ruff and whitespace checks. |
| Regression tests | 426 of 427 focused cases passed initially. The remaining assertion expected the old direct-Python compatibility command and monitoring order; it now checks monitoring, runtime preparation, then native compatibility submission. All 39 cases in that module passed afterward, covering 427 distinct cases in total. |
| Semantic and changed-scope review | Current setup, job, publication and cxcli implementations support the examples. Independent read-only review found no serious issue; the final assertion update was also reviewed. No security attestation is implied. |
| Browser/visual | Mixed: a complete basic three-width assertion run and settled visual captures passed, but stricter rapid keyboard navigation/viewport checks were intermittent. Full interaction qualification remains pending; see below. |
| Installed environment | Pending/not rerun: no dependencies were installed or upgraded. Existing local CLI help confirms the documented uv and Nebius flags. |
| Runtime activation and live target | Pending/not rerun: no compiler, server, Slurm job, GPU workload, monitoring connection or deployment was exercised. |

Playwright Test 1.57.0 used owned, isolated headless Chrome 155.0.8059.40 at
1440×1000, 390×1000 and 320×1000, with page JavaScript disabled. Basic assertions
passed all three widths: exact topic/subsection navigation, unique IDs and real
fragment targets, skip link, keyboard TOC toggling, fragment changes, native code
scrolling, loaded images, page containment, doubled-text reflow and no external
requests or page errors. Additional passing mobile cases cover keyboard scrolling
of both tables. Settled desktop/mobile screenshots show readable prose, contained
code/tables and no overlapping navigation; wide tables intentionally scroll.

The stronger checks require each keyboard-selected heading to reach the viewport.
They sometimes observed the correct fragment while the viewport remained near
the navigation or a previous section. A focused 390px replay and a separate
20-navigation mobile probe passed, but later multi-width runs reproduced failures.
The last run passed desktop, failed 390px and was interrupted at 320px. The cause
is not established; neither passing replays nor screenshots resolve that gap.
No product, stylesheet or browser-foreground change was made to obtain a pass.
Earlier fixture failures (an invented heading, screenshot stability waiting and
subpixel rounding) remain separate from this unresolved interaction result.

Local evidence group `lab-guide-refresh-0fmvs4i4` retains the basic passing run
under `browser-first-pass-*`, successive assertion results, the final
`browser-results.json`, scripts and per-width facts. Settled images are under
`visual-captures/`; enlarged-text captures are in the passing assertion artifacts.
Traces were not collected. Owned contexts and browser processes were closed.

The generic full-course skill checker reports the same 12 diagnostics for the
before/after guide with explicit companion-link manifests. These include course
structure expectations and existing shell/image markup; it is not a passing
shared-guide gate. Native guide/profile checks pass. Initial checker calls with
unresolved temporary-directory paths were invalid-input fixture attempts, not
content validation. No external publication or publication-readiness claim is made.

Command review used current source, local help, and official references for
[Slurm submission](https://slurm.schedmd.com/sbatch.html),
[job accounting](https://slurm.schedmd.com/sacct.html),
[uv tools](https://docs.astral.sh/uv/concepts/tools/),
[Nebius cluster access](https://docs.nebius.com/kubernetes/connect) and
[kubectl forwarding](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_port-forward/).
Earlier reviews retain their original scope and artifact identities.

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
