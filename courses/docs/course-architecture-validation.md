# Course architecture validation — 2026-09-27

## Packaging alignment follow-up

An additional scoped review repaired two helper edge cases: empty linked
publication roots now fail before inventory, and ZIP member names are validated
in their virtual namespace without consulting similarly named source files.
Source paths still reject symlinks and traversal. Both defects were reproduced
with failing regression tests before repair; 60 focused course tests and
69 skill plus six copied-template tests pass. Source/template helper bytes match.
The full build/check command, all seven course validators, helper synchronization,
Python/shell/Markdown checks and independent code/security review pass.
All 15 generated HTML/ZIP hashes remain unchanged; this pass does not require
new browser evidence and does not claim a deployment or model-driven skill run.

## Results archive naming and hosting budgets

The canonical six downloads now use `*-lab-results.zip`; archive bytes are
unchanged. Removed obsolete retirement logic from both builder and validator.
Builder and exporter share the 104,857,600-byte archive cap; build/check additionally
preflight the complete Git publication inventory plus planned output sizes against
1,000,000,000 bytes. Invalid paths, incomplete inventory and overflow fail before
replacement. Existing output bytes are not rewritten when unchanged.

Current verification: all seven native validators; 337 focused tests with three
optional Torch skips; 23 final budget/wrapper regressions; three identical builds
of all 15 outputs, including two from another directory. Browser: Chrome
153.0.8010.53 headless, isolated contexts, 18 cases at 1440/390/320px, six actual
download checksums, setup keyboard navigation and seven offline pages. No external
requests or page errors were observed. Owned contexts, browser and loopback server
were closed. Desktop and narrow captures were visually inspected.

Preservation comparison used current working bytes rather than HEAD: all seven
course pages are identical outside the approved names/wording, all six archives
are byte-identical, and 3,329 protected source/evidence files are unchanged.
Estimated publication size is approximately 690.0 MB, 310.0 MB below the selected
cap; largest archive is 95.5 MiB. No commit, deployment or live lab rerun occurred.
Generic skill-checker special-profile/markup limitations remain separate from
the repository validators. Earlier records below describe preceding revisions.

## Historical download simplification

All six practical courses now show **Practical labs → Download results**.
The non-linked labels **Download all lab results:** and **Setup the lab environment:**
are each followed on a new line by their link. The results link reads **Grafana
dashboards, Small and Large results** and downloads the existing combined archive;
**Lab setup guide** opens the shared guide as a normal page.

The six redundant lab-kit ZIPs were removed, saving **1,051,022 bytes**.
Their 402 original source members remain in the course trees. Real rsync fixture
checks cover runtime files, the advanced executable wrapper and the standalone
Compute companion dependencies. No lab source or executable permission changed.
All six resources ZIPs remain byte-identical, preserving 116 dashboards and
220 result profiles. Soperator and the catalog HTML remain byte-identical.
The shared guide gains only the matching download/sync/build explanation.

All seven course pages preserve their previous markup after removing only the
download introduction, removing the redundant setup/online-guide paragraph and
its introductory dashboard reminder, and normalizing the Practical labs heading. This retains
all lesson content, all 127 embedded source listings, CSS, images, diagrams and existing anchors.
Course HTML totals **3,437,867 bytes**, 3,095 bytes smaller than the preceding
external-download version. The additional HTML saving is intentionally small;
the previous refactor already removed the embedded download payloads.

### Build entry point and verification

Run `./build-courses.sh` to rebuild and check all seven courses, the catalog,
shared guide and six results ZIPs. Its help, failure and success messages now
cover both HTML and ZIP outputs. Three actual runs succeeded, including two
from another working directory; all 15 generated output hashes stayed identical.
No kit archives were recreated. File timestamps may change on successful runs.
The wrapper preserves build/check failure exit codes and reports success only
after both phases pass. Selected-output preflight and per-file atomic replacement
remain unchanged; an I/O failure can still require rerunning a partial refresh.

- 338 focused tests passed: 327 builder, course, sync, wrapper and publication
  presentation cases, plus 11 result-publisher cases using the cached official
  Prometheus client. The existing Torch environment emitted one missing-NumPy
  warning; no dependency installation or live GPU run was needed. After the final
  duplicate-introduction cleanup, all 122 affected tests passed again.
- All seven native validators, HTML/ZIP freshness, helper parity, scoped Ruff,
  Markdown lint, shell syntax/static checks and changed-scope code/security review
  passed. The updated run-labs evidence reference matches its installed copy.
- Browser checks passed 18 desktop/mobile cases at 1440, 390 and 320 pixels:
  label/link separation, no page overflow, loaded images and keyboard setup
  navigation. All six actual downloaded ZIPs matched SHA-256; all seven saved
  course pages remained readable with JavaScript disabled. Current full viewport
  captures with reduced-motion enabled were visually inspected; functional
  checks used default motion. Earlier capture-harness instability and
  clipping were corrected with full viewport captures without changing product CSS or layout.

The generic skill checker was rerun on all seven pages against independent
source inventories. Its pre-existing format/profile findings exactly match the
task baseline; it is not reported as passing. Repository-native validators and
browser checks supply the current scoped verification.

The estimated eligible whole-repository hosting size is approximately **689.9 MB**,
about **310 MB below** the documented
[1 GB GitHub Pages limit](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits).
This counts tracked and nonignored existing files, including original evidence,
archives and other projects; it excludes Git history and ignored local files.
It is a local estimate, not a deployed-artifact measurement. No commit, push,
merge, publication or live lab rerun was performed.

## Historical architecture refactor

The following measurements describe the preceding two-download version. The
current section above supersedes its lab-kit, HTML-size and download-count claims.

### Delivered behavior

The stdlib Python builder now separates canonical inventory, Markdown,
passive visual assets, reusable content, page composition and output planning.
The existing CLI and shared authored CSS remain. Each practical course has an
external combined dashboards-and-results ZIP and an external lab kit. Soperator
has neither. No frontend framework or JavaScript was added.

The resources ZIP has `grafana-dashboards/`, `small/LAB/` and `large/LAB/`.
All 116 dashboards and 220 existing result profiles are represented without
nested archives. The six old generated results ZIPs were removed only after
exact member-byte comparison with their replacements. Original evidence,
manifest checksums, dashboard files and lab sources remain intact.

### Preservation

Compared with the dirty working tree captured before implementation, 3,321
protected teaching, lab, guide, CSS, SVG, dashboard and evidence files are
byte-identical. All seven pages retain identical markup after normalizing only
the agreed download groups, dashboard pointers, result-file lists and whitespace
between tags. Lesson IDs, navigation, complete code listings and inline visual
assets remain. The catalog, shared guide and Soperator page are byte-identical.

| Course | Previous HTML bytes | Current HTML bytes |
| --- | ---: | ---: |
| advanced-gpu-communication | 2,453,557 | 747,076 |
| custom-cuda-kernels | 1,117,214 | 424,537 |
| gpu-fundamentals | 1,038,797 | 460,996 |
| gpu-optimizations | 1,254,876 | 512,562 |
| llm-inference | 1,849,206 | 678,900 |
| llm-training | 1,467,698 | 557,650 |
| soperator | 59,241 | 59,241 |

Total course HTML falls from **9,240,589 to 3,440,962 bytes**, a 62.8% reduction.
ZIPs remain separate downloadable files; offline HTML reading retains CSS,
teaching images, diagrams and code, while downloads need the companion files.

### Static and local verification

- 997 distinct test cases passed across broad and focused runs covering 27
  relevant modules. The final broad run passed 996 cases; a completed
  pre-results publication fixture then passed with all 48 focused cases. This includes archive identity,
  checksums, ownership, symlinks, traversal, permissions, determinism, obsolete
  archive preservation, strict Markdown/SVG rules, generated IDs/fragments,
  full-plan failure before writes, atomic replacement cleanup and no-write check
  mode. An existing Torch environment emitted one missing-NumPy warning; no
  dependency installation or GPU execution was needed.
- All seven native course validators passed. HTML/ZIP source parity and canonical
  helper parity passed. ZIP members and bytes were checked independently by the
  standalone validators.
- Scoped Ruff, Markdown lint, shell syntax and whitespace checks passed. The
  changed-scope code/security review found no remaining actionable issue. Existing
  export locks, journals and identity/checksum checks remain in their owners.
- The four changed source-owned run-labs skill payloads match their installed
  project copies. This is installation parity, not a new live campaign result.

### Browser and visual verification

An owned, isolated headless Chrome 153.0.8010.53 session passed all 21 course and
viewport combinations at 1440, 390 and 320 pixels. Checks covered keyboard skip
navigation and native disclosure controls, unique IDs and resolved fragments,
loaded images, absence of JavaScript, horizontal overflow and 200% text reflow.
All 12 actual browser ZIP downloads matched the corresponding files by SHA-256.
All seven saved course HTML files remained readable with networking offline.

Current desktop/mobile captures were visually inspected. Twenty overview
comparisons were pixel-identical; the remaining comparison differed at nine of
368,631 pixels by one channel level, within the declared raster tolerance and
with unchanged layout/text. Earlier screenshot-harness attempts encountered
scroll/capture errors; the final run used settled scroll positions and bounded
viewport captures. No product layout change was made for those harness errors.

### Hosted size estimate

The conservative full-repository estimate is approximately **691 MB**
(**659 MiB**), including original public evidence, external ZIPs, source files
and other repository projects. It excludes Git history and ignored local
files/environments. This is about **309 MB below** the documented
[1 GB GitHub Pages site limit](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits).
It is an estimate of eligible working-tree content; the actual deployment
artifact may be smaller. Moving downloads outside HTML does not remove them
from Pages storage accounting.

The largest file is the advanced course resources ZIP: **100,140,877 bytes**
(**95.5 MiB**), below GitHub's
[100 MiB individual Git file limit](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github).
It has about 4.5 MiB of file-size headroom, so recheck that archive as results grow.

| Course | Resources ZIP bytes | Dashboards | Result profiles |
| --- | ---: | ---: | ---: |
| advanced-gpu-communication | 100,140,877 | 35 | 68 |
| custom-cuda-kernels | 24,517,623 | 14 | 26 |
| gpu-fundamentals | 19,837,681 | 12 | 22 |
| gpu-optimizations | 53,191,440 | 15 | 28 |
| llm-inference | 70,890,810 | 23 | 44 |
| llm-training | 42,538,449 | 17 | 32 |

No new live lab run, Git commit, push, merge or Pages publication was performed.
Local checks do not establish deployment success. The reviewed merge must include
all generated HTML and ZIP files; verify the resulting Pages build and deployed
revision separately.
