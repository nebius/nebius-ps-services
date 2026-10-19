# Catalog preparation alignment validation

## Unified lab preparation and TOC alignment — 2026-10-15

Reviewed all nine registered courses against the unified `lab-setup.py` contract.
Aligned practical orientations, syllabus referrals, readiness and privacy text,
version notes and optional-exercise guidance with the single command and its
completion summary. The shared Lab Guide owns the command and failure/skip
interpretation. Default dependencies include optional containers, model snapshots
and the separate SM90a build; execution, correctness, fabric qualification and
profiler captures remain separate. Reading courses require no setup to read.

All course TOC identities and order remain unchanged, with unique, valid targets.
The catalog's smooth-scroll rule could leave rapid keyboard navigation at an
older location. Its authoritative stylesheet now uses native instant scrolling,
matching the course pages. The repository browser regression covers the catalog,
Lab Guide and every course TOC, opening disclosures with keyboard controls.
No lab implementation, launcher, runtime metadata, installation pin or historical
result was changed during this prose pass. The preexisting diff outside the
requested authoring surfaces was preserved.

### Source and local validation

- 507 focused preparation, authored-guide, catalog, link, presentation, sequence
  and profile tests pass against the final generated pages.
- All nine native profile validators, canonical helper parity and generated
  freshness pass. Scoped Ruff, Markdown, JavaScript syntax and whitespace checks
  pass; nested read-only code and security review found no remaining scoped issue.
- The generic skill checker has identical baseline/final diagnostics: nine for
  Advanced Labs and seven for each other course. These retained profile/markup
  findings are not a generic-checker pass. Native profile-aware validators and
  source parity remain the authoritative project checks.
- The canonical requirements/design pair records implemented local delivery;
  applicable live installation and runtime qualification remain separate.

### Browser and visual evidence

Playwright Test 1.57.0 with owned headless Chrome 155.0.8059.40 and isolated
contexts passes 33 full-page cases: nine courses, the Lab Guide and catalog at
1440×1000, 390×1000 and 320×1000. JavaScript is disabled. Each case checks keyboard
skip/menu/contents access, every TOC destination, document containment and 200%
CSS-zoom reflow. There are 1,062 TOC traversals and 27 keyboard horizontal-scroller
checks. The expanded repository navigation regression separately passes all
33 cases. No case is skipped or retried in either final run.

Visual review covers all course orientations and changed readiness/onward-reading
passages, plus the guide preparation section and catalog at desktop/phone widths.
Text wraps within its containers; long commands scroll locally. The scope does
not requalify unchanged lesson diagrams or their semantic teaching. At extreme
zoom the narrow catalog words wrap; no horizontal document overflow was observed.
Owned browser contexts/processes closed after execution, with no personal profile.

Earlier harness failures are retained: `End` was incorrectly used to assert
horizontal scrolling, then closed source disclosures were targeted without opening
them. Keyboard `ArrowRight` and opening the disclosures corrected those test
errors. A corrected full run passed 32 cases and reproduced a real catalog
390px navigation failure. In a CSS-only control, one smooth-scroll run missed two
of eleven headings and a second passed; both instant-scroll runs passed all eleven.
The final source fix passed both complete browser runs with unchanged assertions.
An initial causal probe also had an ambiguous duplicate-link locator; its corrected
probe uses the same first-link selector as the regression.

Private local evidence group `course-setup-text-align-ix8wof6m` retains source
baselines, `authoring.patch`, `generic-comparison.json`, `toc-preservation.json`,
`preservation.json`, `browser-complete-report.json`, `browser-complete-evidence.json`,
`browser-complete/` screenshots and `visual-manifest.json`. Earlier failed traces
remain in their separate run directories. Passing runs use assertions, JSON and
screenshots; no retained success trace is claimed. Evidence is bound to the hashes
below. No dependencies were installed, no Slurm/GPU/container workload was run, and
no external publication occurred. Applicable installed/runtime/live lanes remain
pending; reading-only profiles need no execution qualification for reading.

### Final artifacts

| Artifact | SHA-256 |
| --- | --- |
| `README.md` | `eb2eaae9bee5526558e56652adee72fb8c7a16a24fbc1b1699e7901971682188` |
| `index.html` | `f952af50168e3466dd16d089c7d828edb4578bd200da4eace7936db32a43368c` |
| `lab-guide.html` | `8fbe1efa210eec1e7a81e93397f9253cbc523a3892c292a06ace652647f0adb0` |
| `advanced-gpu-communication/index.html` | `e144a9d39c1b9062d5f35c3d9b87b8fa9eaffaaef2b045db10f96789cf1a6e4e` |
| `custom-cuda-kernels/index.html` | `64cc4f64d1735fba9bf1312516ef6ea63a6e3e76e23ba7398e3fd99fdaba5174` |
| `gpu-fundamentals/index.html` | `57cf892332946062f5f13b1dd45d3bfb2df91edcc30d1ed0c19989ba3fe82db8` |
| `gpu-optimizations/index.html` | `3cfce7e74c00dc8f234bfb8c816ef635875c6da6a6aff5acf23adf3c12bc9cf5` |
| `gpu-performance-tools/index.html` | `d60c15abdd8b1bc061875d8bcc2370125da81a383fdd7ee1a18add4c577eb832` |
| `llm-inference/index.html` | `a7436ca818650f8b9c8f3e5af359b01452062117b804942cd71446933603d8d0` |
| `llm-training/index.html` | `6c5bc017639bbbfde9461d07a93d8adb7fa67362d9654e270d9ed760187d2210` |
| `pytorch-gpu-performance-engineering/index.html` | `8847894bb5001bec6883a6649a9fa7a7d7e367d1ef9e871fe89c2968d92fb8ce` |
| `soperator/index.html` | `73b85903079879c87aaa31d05c2b027c52dfffbbfd1df68bf27de75cdb9a0430` |

Earlier sections below describe historical five-command preparation and apply
only to their recorded artifacts and trials.

## Scope — 2026-10-03

All eight courses and 110 practical lab guides were reviewed against the five
preparation groups. Each lab now refers once, from Before you start, to the
shared Lab Guide. Course introductions and current runbooks use the same route.
The guide provides a complete course/lab-number lookup and retains optional
variant selection. Its sidebar includes the lookup in authored heading order.

Removed duplicated installer commands and competing manual setup procedures.
Corrected current environment/version notes, CUDA rebuild guidance, runtime
activation wording and fabric/tool path ownership. Lab-specific hardware and
data prerequisites, qualification checks and intentional compilation experiments
remain in their owning labs. The shared preparation engine is unchanged.

## Source and preservation evidence

- 536 focused tests pass, including the exact 110-lab lookup, source/rendered
  referrals, validator rejection controls, runtime selection and native commands.
- All eight repository-native course validators pass. Complete HTML generation,
  freshness and six-course helper parity pass.
- All 685 learner Bash blocks parse; all 501 native lab command lines are
  unchanged from the task baseline.
- Scoped Ruff, Markdown and whitespace checks pass. Read-only code and security
  review found no concrete defect in the changed surfaces.
- All 2,872 historical result/archive files and 14 canonical runtime-engine
  Python files retain their baseline hashes. All six completed-run sections in
  VERSIONS.md remain byte-identical. Archives were regenerated and checked without
  changing their bytes.

The new native validator rejects a second Lab Guide link, misplaced preparation
referrals, setup script names and manual dependency installation in lab guides.
The renderer preserves direct shared-guide fragments. No dependency pins,
installation behavior, workload implementation or historical evidence changed.

## Browser and visual evidence

Owned, isolated headless Chrome 154.0.8037.93 with Playwright Test 1.57.0 checked
all nine pages at 1440×1000, 390×1000 and 320×1000. JavaScript was disabled.
Checks cover unique IDs, every TOC target, keyboard TOC controls and preparation
links, every rendered lab referral, page containment, keyboard local scrolling
and 200% text reflow. No automatic external requests or page errors occurred in
the successful cases. Recorded HTML identities match the final files below.

The final full run passed 26 cases and failed one 320px guide navigation
visibility assertion. The URL fragment changed correctly but the heading was
outside the viewport. Three unchanged replays of that exact case passed; nine
bounded probes with fresh, scroller-first and settled input sequences also
reached the correct heading. The cause is not established, and no product or
harness repair is claimed. Thus all 27 distinct page/viewport combinations have
successful coverage, with an unresolved intermittent navigation observation;
this is not a clean single-run 27-case result.

Separate settled screenshots cover every course and the guide, including the
mobile lookup's horizontally scrolled group links and 200% text reflow. Visual
inspection found readable preparation prose, reachable links and contained local
code/table scrolling. These targeted views supplement complete-page DOM checks;
they are not an accessibility certification. Earlier automated screenshots had
inconsistent framing, so the supplemental capture asserts visible positioning.

## Artifact identities

| HTML artifact | SHA-256 |
| --- | --- |
| `lab-guide.html` | `d770a9ef56018aa851f805ad661d67f74d5f18e68f665a1f2c045e0f142c52dd` |
| `soperator/index.html` | `e6c86ee693875f1fbf82f7d125f7d9f9d7d680f3b1054c50f74eaafe2ad32647` |
| `gpu-performance-tools/index.html` | `ada9be047b4ced5d1642aad60a213123448f6ace93321d7895967ed4d9c7fe6d` |
| `gpu-fundamentals/index.html` | `0fcab9d688020749cd3e8f530a99634fd7bcb419be395a40db81e92428d4445e` |
| `gpu-optimizations/index.html` | `d583d62b8c7ba844219aa24b24afcbb6c7b19dfcd1e46d7eb3cbee3b3762b79f` |
| `llm-training/index.html` | `362b6e9bbb2b6b25d8e28480c88518376e7b56d9ea2da86b7c449a8ebe389941` |
| `llm-inference/index.html` | `80e31fa0f7d9254b0a5bae442babe771b8cd8be3d89e2c4c53c4731a7e5c1dfa` |
| `custom-cuda-kernels/index.html` | `c726e0f632fed59574b0471e28d43570c75ccc7296e8c3836f9db8a246e3f4c1` |
| `advanced-gpu-communication/index.html` | `9d6054c89dde1011483cb2b76e65baa5bf1bd70e22fd7900a174a7087e652f10` |

## Evidence locations and limits

Local evidence group: `course-preparation-alignment-3o96xtfp`.

- Source: `tests-verified.log`, `final-quality-gates.json`, `preservation.json`,
  `native-command-preservation.json`, `versions-preservation.json`.
- Browser: `browser-results.json`, `browser-verified.json`,
  `navigation-replay-results.json`, `navigation-probe.json`.
- Per-case screenshots/traces: `browser-artifacts/catalog-<page>-<width>/`
  and `navigation-replay-artifacts/`; screenshots are `lookup.png`,
  `preparation-referral.png` and `text-reflow.png`; traces are `trace.zip`.
- Settled visual captures: `visual-results.json` and `visual/`.

Earlier failures are retained under `initial-browser-*` and
`interrupted-late-*`: the original harness selected the closed course switcher
instead of the TOC; its selector was corrected. `before-final-wording-browser-*`
records a clean 27-case run before the last prose corrections, and does not
replace the final-artifact evidence above. Owned browsers and contexts were
closed; the interrupted runner's remaining owned Node processes were terminated.

The generic full-course skill checker retains exactly its baseline diagnostics:
6 per ordinary practical/Soperator page, 8 for Advanced Labs, 7 for GPU
Performance Tools and 11 for the shared guide. It is **not a passing gate**.
`skill-checker.json` records the bounded markup comparison using private page
copies and an empty source allowlist. Native validators and source-parity tests
supply actual embedded-listing verification; the empty allowlist does not.

Installed-environment, runtime activation, GPU qualification, live-target and
deployment checks were not rerun for this documentation revision. Prior setup
and live evidence retain their original scope. No external publication occurred.
