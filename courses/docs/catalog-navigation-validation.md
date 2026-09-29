# Catalog navigation validation

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
