# Soperator course validation

This record describes the earlier sixth-course delivery. Current seven-course
ownership, counts and navigation evidence are recorded in
[advanced-course validation](advanced-course-validation.md); the older hashes
below identify historical artifacts.

The sixth course is a text-only introduction with six lessons and no executable
labs, diagrams, setup, dependency installation or runtime assets. The five GPU
courses retain 101 executable labs and their existing runtime qualification
boundaries. The new catalog lists six courses while the GPU prerequisite graph
and shared-runtime distribution remain limited to their five existing packages.

## Source and static evidence

- The first text-course regression failed because the GPU parser did not support
  the requested format. The implemented separate text profile passes.
- Full shared regression run: 983 passed, with two loopback API fixtures excluded
  because the filesystem sandbox also restricts local socket binding. Those two
  fixtures passed separately with loopback permission; no live cluster was used.
- Final presentation/content regression after scoped CSS changes: 120 passed.
  Final text validator/security follow-up: 24 passed, including stale prose,
  malformed metadata/lesson order, invalid links, active elements and malformed HTML.
- All five GPU validators and the text-course validator pass. Atomic build/check,
  shared helper parity, shell syntax, ShellCheck and configured Markdown pass.
  New/changed production Python and new tests pass Ruff. Six existing style
  findings remain in the pre-existing catalog/sync test code; none was introduced.
- Shell examples pass syntax checks only. They were not submitted to Slurm.
- Canonical lessons, glossary and references were reviewed against SchedMD,
  Soperator 4.1.8 API/chart definitions and PyTorch launcher sources. A bounded
  independent review corrected the distinction between machine provisioning and
  Kubernetes container placement; no further actionable findings remained.
- The task-start course source inventory has no missing files. Existing lab
  implementations, lessons and runtime configuration were preserved.

The installed generic course-skill checker reports seven distinct findings.
Its required diagram is explicitly excluded by this course's text-only request.
The other findings concern the shared catalog shell: relative course navigation,
standard favicon `link` and footer `small` elements outside its allowlist,
resulting false unbalanced/unclosed-element reports, and a wrapped license
`pre` without keyboard focus. The actual text-page parser verifies balanced
HTML, allowed public links, local anchors and navigation. The license wraps
rather than requiring horizontal scrolling. These checker-format differences
are reported, not represented as a passing generic checker result.

## Follow-up alignment

The scoped alignment pass corrected the sync guide's destination tree and
discovery rule to include the text-only course (`reference/course.json` plus
`COURSE.md`), and updated the repository changelog to describe six courses.
No implementation or course-content changes were needed. Requirements and design
already describe this behavior and remain unchanged.

All 215 focused text-course, catalog, sync, content and visual-structure tests
passed: 213 in the standard sandbox and two isolated loopback API fixtures with
local socket permission. All six course validators, generated-source parity,
shared-helper parity, Markdown, ShellCheck, shell syntax and production/new-test
Ruff checks passed. Ten course shell examples passed syntax checks. The six
existing Ruff findings in older catalog/sync tests remain unchanged.

Nested read-only code review and the scoped security review found no new
actionable issues. All seven generated HTML files are byte-for-byte unchanged,
so the browser evidence below remains applicable; this pass did not rerun the
browser or execute any examples on a cluster. No task-start files were removed.

## Browser evidence

Owned isolated headless Chrome 153.0.8010.48, managed and closed by Playwright
Test: 11 of 11 final checks passed. The new course and catalog were checked at
1440, 390 and 320 pixels wide; each existing GPU course's new switcher link was
checked at 390 pixels. No personal browser profile or external assets were used.

The text course passed keyboard TOC and switcher actions, all local anchors,
code/table keyboard scrolling, six ordered lesson sections, normal-width layout
and 200% text sizing without page-level overflow. A visual review found narrow
table columns during the first enlarged-text pass; minimum column widths now
preserve readable text within the local table scroller. Final table-width
assertions and visual inspection passed. Desktop contents numbers align with
the first line of each title.

Private validation artifacts are grouped under the task artifact name
`soperator-text-course-20260918`: `browser-final.json`, its `evidence` attachments,
and `browser-final/` screenshots and traces. Reviewed images include `entry.png`,
`batch-lesson.png`, `text-200-percent.png` and `sixth-card.png`. Optional browser
MCP exploration was not used. Playwright closed its contexts and owned browser
on completion. Artifact hashes were independently checked against the final files.

| Artifact | SHA-256 |
| --- | --- |
| soperator/index.html | `46e618ef4c4722b71dc179a83d43cfd7d72e2503b265986a5452b08516794381` |
| index.html (catalog) | `880d6472db8961fbc8c98899abcfe3e0355645273f1b4094df55a4e692b80fff` |
| gpu-fundamentals/index.html | `a3b274defba57b11f974f3aa7ea08fb580cffd38fef26995c4c2766f69f6549e` |
| gpu-optimizations/index.html | `f5c4ca4d23c70008e48b3aab230c0e520b0d89311e331d555d42482412080593` |
| llm-training/index.html | `8d706336a5f02823f438b685d07cc092d32435670129615c6a02c9efc5de0e9c` |
| llm-inference/index.html | `984142077b707ddaf3a8603d2b036cb2b1f9d3c1e7885130bc5dc970d286f088` |
| custom-cuda-kernels/index.html | `83596d9e32580dc32723e6496f0a5a08bd48e7e1fa9d49a6b4fa3859dbce97da` |

## Evidence limits

No packages were installed, no Slurm jobs were run and no infrastructure was
changed. Runtime/live lanes are not required to read this text-only course;
its command examples remain site-dependent and unexecuted here. No deployment,
public publication, external expert endorsement or learner-outcome study is
claimed. Existing GPU hardware and live-profiler qualification remain separate.
