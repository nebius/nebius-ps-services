# Catalog navigation validation

The subsequent [catalog badge consistency review](course-format-validation.md#consistent-catalog-badges--2026-10-05)
records the current catalog artifact and focused browser checks. The navigation
audit below retains its original artifact hashes.

## Catalog consistency — 2026-10-05

The catalog contains eight course packages and nine reader resources, ordered
Soperator, Lab Guide, GPU Performance Tools, GPU Fundamentals, GPU Performance
Optimization, LLM Training, LLM Inference, Custom CUDA Kernels, then Advanced
Labs. Cards and the learning path are numbered 01–09; Training, Inference,
Custom CUDA and Advanced Labs are 06–09. Every course and guide menu follows
the same order, with one current-page identity.

Validation: **280 focused tests**, all eight native course validators, the full
build wrapper, HTML/archive freshness, helper parity, scoped Ruff and configured
Markdown lint, Bash syntax, ShellCheck and whitespace checks pass. Independent
code and security review found no defects. All eight retained course bodies
and 3,483 practical assets, including all six result ZIPs, match the task-start
comparison.

**30 isolated browser checks pass** across all ten HTML pages at 1440, 390 and
320 pixels using installed headless Chrome. Checks cover exact card/menu order
and numbering, current identity, keyboard menu operation, fragment targets,
normal-width layout and doubled-text reflow. Desktop and mobile catalog and
mobile guide screenshots were visually reviewed. Browser contexts and the
owned browser closed after validation. This establishes local static and
browser behavior; live workloads and external publication were not performed.
The dated sections below retain their original evidence boundaries.

| Reviewed artifact | SHA-256 |
| --- | --- |
| `index.html` | `984ac70ef368df6f4707c521f3b433fbdf508c40c92fafc402bad21caeb5371e` |
| `lab-guide.html` | `c916d77157b9e5448a383ff37dec6cd2e6c55f502370d4bde405f4ae5985266b` |
| `soperator/index.html` | `e6c86ee693875f1fbf82f7d125f7d9f9d7d680f3b1054c50f74eaafe2ad32647` |
| `gpu-performance-tools/index.html` | `ada9be047b4ced5d1642aad60a213123448f6ace93321d7895967ed4d9c7fe6d` |
| `gpu-fundamentals/index.html` | `0fcab9d688020749cd3e8f530a99634fd7bcb419be395a40db81e92428d4445e` |
| `gpu-optimizations/index.html` | `d583d62b8c7ba844219aa24b24afcbb6c7b19dfcd1e46d7eb3cbee3b3762b79f` |
| `llm-training/index.html` | `362b6e9bbb2b6b25d8e28480c88518376e7b56d9ea2da86b7c449a8ebe389941` |
| `llm-inference/index.html` | `80e31fa0f7d9254b0a5bae442babe771b8cd8be3d89e2c4c53c4731a7e5c1dfa` |
| `custom-cuda-kernels/index.html` | `c726e0f632fed59574b0471e28d43570c75ccc7296e8c3836f9db8a246e3f4c1` |
| `advanced-gpu-communication/index.html` | `9d6054c89dde1011483cb2b76e65baa5bf1bd70e22fd7900a174a7087e652f10` |

## Tools before practical courses — 2026-10-02

The route at this revision was Soperator, Lab Guide, GPU Performance Tools, GPU
Fundamentals, GPU Performance Optimization, LLM Training, LLM Inference,
Custom CUDA Kernels, then Advanced Labs. The catalog cards, learning path,
README, generated guide and all course sidebars share this order. The reference
course no longer requires Fundamentals; practical overviews, READMEs and
syllabuses direct learners to it before experiments. The three specializations
retain their Fundamentals and Optimization prerequisites.

Source/static checks pass: **243 focused tests**, all eight native course
validators, generated HTML/archive freshness, canonical helper parity, scoped
Ruff, configured Markdown lint and whitespace checks. A 3,988-file starting
snapshot confirms all 3,190 protected lab-source, result, diagram and archive
files retain their bytes, including all six results ZIPs. All existing lesson
bodies are unchanged. The installed generic course checker produces exactly
the same pre-existing profile/markup diagnostics before and after this revision
for all eight courses; it is not a passing gate. The profile-aware native
validators provide the static acceptance checks. Independent read-only review
identified stale maintainer routing and duplicated design counts, which were
corrected; no material
code or security finding remains.

**30 Playwright Test cases pass**, covering ten complete HTML pages at
1440×1000, 390×1000 and 320×1000. Playwright 1.57.0 launched owned isolated
headless Chrome 154.0.8037.93 with page JavaScript disabled. Assertions cover
exact navigation order/current identity, working keyboard menus and links,
section TOCs and fragments, appendix order, local scrollers, inline image/SVG
containment, doubled-text reflow and zero automatic HTTP(S) requests. Reviewed
captures of the desktop catalog, narrow course menu, course overviews and
shared-guide introduction show readable wrapping without clipping or overlap.
Browser processes and contexts closed after execution. Traces were retained
only on failure; none were needed.

Local evidence group `tools-course-order-79_gflfe` contains
`publication.spec.cjs`, `playwright.config.cjs`, `browser-complete.json`,
`artifact-hashes.json`, `preservation.json` and `generic-checker.json`.
Screenshots are under `browser-complete/publication-PAGE-publication-WIDTH/`.
The exact inspected artifacts are listed below; earlier sections record
historical revisions. This navigation revision does not qualify installed
Slurm/profiler environments, run live GPU workloads or deploy the website.

| Page | SHA-256 |
| --- | --- |
| `index.html` | `b726abe8f79b23b3fee0a7709a59bc4fcc0ff30dbe16cff4587f0ef3a946c73a` |
| `lab-guide.html` | `4b9ce1ed9ee2ca563ff4bffb50150d1a207bedea53aa5e5a9763b0f44c1ca6f8` |
| `soperator/index.html` | `b1184c1f90073a3bdf64c2fe15fc3946d89198ae6d2758cf0e50012f1f6b4ffc` |
| `gpu-performance-tools/index.html` | `b72be28fac03275b00219507736ff6e790239868b7caaf3bbcc028ff73e7bef9` |
| `gpu-fundamentals/index.html` | `23de2cc98fce3d7cd71337da66501da939c7ba1aacddbd4bfec0737d84fc7ac6` |
| `gpu-optimizations/index.html` | `89b6014a15a0cc9e1afeb92176261c31727eda5a19ef222c02d07bf64906a7c6` |
| `llm-training/index.html` | `7f1504a91de65082c357e1ea6ca1a771e4e8ef12c22f24dba7df768e9f7a20fe` |
| `llm-inference/index.html` | `f65b00ec1b51e080f7f753802341b43904ae7dac2fc2ac196511e77e1d8e9632` |
| `custom-cuda-kernels/index.html` | `22c24ecabfd6d4de19f92fc7658f887c8a38900ffa32010bbeafe03ac0c82137` |
| `advanced-gpu-communication/index.html` | `298b636b1f1baea779bebb63e2f56a605d8718df5d767dd6872f9cd336259c7f` |

## Introduction cleanup: 2026-09-28

The README and generated Lab Guide now use **Explore the courses** as a
standalone link and **Browse the courses catalog** for the relative catalog
link, preserving both destinations. The maintainer link, lab-kit/build comments
and seven-courses reading-format paragraph are removed. The results explanation,
source-sync instruction and all lab sections remain.

All 127 focused catalog/shared-guide tests, generated HTML/archive freshness,
scoped Ruff, configured Markdown lint and whitespace checks pass. Independent
read-only code/security review found no serious issue. A 5,869-file baseline
comparison confirms only nine declared files changed; all instructional sections
and the footer, other course/catalog HTML, six archives and unrelated work remain
unchanged. The guide HTML delta matches the requested introduction edits exactly.

Six isolated headless Chrome 154.0.8037.58 Playwright Test cases pass at
1440×1000, 390×1000 and 320×1000 with page JavaScript disabled. They check exact
labels/destinations, removed text, keyboard navigation/TOC, current identity,
source focus, 200% text reflow, single footer and complete license access.
Final introduction captures at all three widths were visually reviewed for
spacing, wrapping and clipping. Owned browser resources closed successfully;
failure-only traces were not retained because all cases passed.

Current `lab-guide.html` SHA-256:
`da21c3d3f9acdeedbcf63695bda34e83e1673da39225c6dcfe87a03feeddd4e6`.
Local evidence group `guide-intro-u3gn803o` contains `browser-results.json`,
`browser-artifacts.json`, `preservation.json`, before-sources and screenshots
under `browser-results/`. Other HTML hashes remain as recorded below. Earlier
sections document previous revisions. This is local source/browser evidence;
no deployment or live-lab claim is made.

## README and Lab Guide follow-up: 2026-09-28

The README now labels its relative `index.html` link **Browse the catalog** and
places **Read this guide online** immediately under **How to set up the lab**.
It explains that the HTML guide comes from the same README. Build details live
in the maintainer guide, and the README attribution is its final paragraph.
The generated guide omits the standalone online pointer and article attribution,
retaining one standard footer with the complete license and third-party notices.

- All 127 focused catalog and shared-guide tests pass, including presentation
  boundaries, source parity, runtime referrals and documented shell syntax.
- All seven native course validators, HTML/archive freshness, helper parity,
  scoped Ruff, repository-configured Markdown lint and whitespace checks pass.
- Comparison against 5,869 starting file hashes confirms only the ten declared
  source, documentation, test and generated-guide files changed. The catalog,
  all seven course HTML pages, six archives and unrelated service work retain
  their bytes. Every README command block is unchanged. Reconstructing the old
  guide reproduces its original hash; the new HTML differs only in the catalog
  label, edition explanation and removal of the trailing attribution/build prose.
- Independent read-only code/security review found no serious issue.
- Six Playwright Test cases pass at 1440×1000, 390×1000 and 320×1000 using
  isolated owned headless Chrome 154.0.8037.58 with page JavaScript disabled.
  Checks cover guide/catalog navigation, exact current identity, keyboard menu
  and TOC controls, focusable source scrollers, 200% text reflow, footer placement,
  one attribution, and keyboard access to the complete license. Desktop and
  narrow setup/footer captures were visually inspected for wrapping and clipping.
  Owned browser resources closed; failure-only traces were not needed.

That revision’s `lab-guide.html` SHA-256:
`e7c430ca0448dfe24c95bd8f36b11243ddc8b3cd4851f7b9e6d89366b9158b5e`.
All other HTML hashes in the original catalog record below remain current.
Local evidence group `readme-guide-mdtwym26` holds `browser-results.json`,
`browser-artifacts.json`, `preservation.json`, `guide.diff` and viewport captures
under `browser-results/`. Generic full-course checker diagnostics for the seven
unchanged course pages remain the original baseline findings below; the shared
guide uses its repository-specific tests. This follow-up verifies local files
and browser behavior; it does not establish GitHub Pages deployment or lab execution.

## Original catalog revision: 2026-09-28

The README leads to the published GitHub Pages catalog and retains a local
catalog link. Cards, the learning path, and all eight course/guide menus use this
order: Soperator, Lab Guide, GPU Fundamentals, GPU Performance Optimization,
LLM Training, LLM Inference, Custom CUDA Kernels, Advanced Labs. The collection
contains six courses, an advanced laboratory collection, and one shared guide;
the build registry still contains seven real course packages.

## Source and static evidence

- All 161 focused catalog, shared-guide and text-course tests pass, including
  rejected reordered, missing, duplicate, misplaced and incorrect-current menus.
- All seven standalone course validators pass. Generated HTML/archive freshness,
  canonical helper parity, scoped Ruff lint and whitespace checks pass.
- A baseline comparison checks 3,737 tracked course files. All 3,702 files outside
  the declared edit set retain their hashes; this includes original teaching,
  labs, diagrams, dashboard/result evidence and all six result archives.
- Each of the seven course HTML pages is byte-identical outside its resource menu.
  Shared-guide setup, execution and browsing sections are byte-identical.
- The generic skill checker reports the same profile/markup diagnostics before
  and after this revision for all seven courses. It is not a passing gate;
  repository-native validators supply the profile-specific static checks.
- Scoped Markdown checks use the repository configuration. Existing Ruff
  formatting diagnostics remain in six previously unformatted files; the two
  previously clean renderer/config files pass formatting after this change.
- Independent read-only code and security review found no blocking issue in the
  changed surfaces. No dependencies, runtime interfaces or publication settings
  changed.

## Browser and visual evidence

Playwright Test completed 27 cases: the catalog, Lab Guide and all seven courses
at 1440×1000, 390×1000 and 320×1000. Each case used an isolated owned headless
Chrome 153.0.8010.53 context with page JavaScript disabled. Assertions cover exact
resource order, current identity, keyboard menu activation, guide/catalog return
navigation, separate section TOC controls, focusable source scrollers where
present, and page-width containment at normal and 200% root text size.

All cases passed. Artifact SHA-256 values, actual browser version and viewport
are bound to the test attachments. Captures of the catalog, new guide card,
learning path, and desktop/narrow menus were visually reviewed for wrapping,
spacing, focus and clipping. Additional catalog captures include a full desktop
page and the guide card/learning path at 320px. Owned browser processes and
contexts closed successfully.

Local evidence group: `course-catalog-ZotSOTTG`. It contains
`browser-results.json`, `browser-artifacts.json`, `preservation.json`,
`generic-checker.json`, viewport captures under `browser-results/`, and the
additional visual captures. Tracing was configured only for failed cases; no
trace was retained because all cases passed.

## Artifact identities

| Page | SHA-256 |
| --- | --- |
| `index.html` | `5e32f827bfeedcfb822687e00db10b06afd72bb9276b46fa9850135179c69996` |
| `lab-guide.html` | `1be61a4b1aad3624b11689e2ab2902230f087e07ab3e3d96080cb414d93ca6f1` |
| `soperator/index.html` | `004fb475f1d1ca1fd265713618018da8e0789d9b4f032789e510b62588cd64f4` |
| `gpu-fundamentals/index.html` | `6eeb681af089a63a90cee97b01e2c0cdd2b61fc7ad5ed5012b5930e03d265180` |
| `gpu-optimizations/index.html` | `cf4f433da5eae90e88ae9a9cecb16bb14d7b60f81916d1a90a824713a15dea09` |
| `llm-training/index.html` | `779c9d31f5001516afcbcf5d64a72052e691a67cf7e9d887d33d161cad24bcb1` |
| `llm-inference/index.html` | `2f6fbbb49681e374aecb721e1577d08bdbe53ff9fd91d16948426080bf19b394` |
| `custom-cuda-kernels/index.html` | `da74293a5fdd9a1dfc8ee5554cd2fbebea2d5023819405e6b133676379696023` |
| `advanced-gpu-communication/index.html` | `b6a9f6f9b32d48ca8afb33de44492a22ad0a5e4fb7bf9ff6f9bfbe16054842dc` |

## Evidence boundaries

This verifies local source and browser navigation for the listed artifacts.
It does not establish deployment of this revision to GitHub Pages. No packages
were installed, no course labs or live GPU workloads ran, and no infrastructure
or credentials changed. Existing runtime evidence remains associated with its
original artifacts; this presentation revision makes no new runtime claim.
