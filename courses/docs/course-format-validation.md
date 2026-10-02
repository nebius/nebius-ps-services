# Seven-course format and navigation review

This report retains historical format and navigation evidence. The current
authoring contract is in [Maintaining the courses](maintaining-courses.md#educational-approach);
each course's `PUBLICATION-REVIEW.md` identifies its latest reviewed HTML.
Current lesson fields are Objective, How it works, Practice and Mental model,
followed only by optional References. Each course closes with one Where to Go
Next, one Glossary and final Official references.
The earlier counts, field order and hashes below apply to their original revisions.

## Fresh preparation alignment — 2026-10-01

Shared guide HTML SHA-256: `8f253a80467840f425ff95e29b7dd9e90e23e7e618b489e058f82f074e086fb0`.

Fresh alignment repaired two documented preparation gaps: Advanced Lab 01 now
creates its private fabric-tools prefix before installation, independently of
publishing; workstation monitoring verification restores the connection settings,
checkout and selected course directory in a new terminal. Both regression tests
failed for the original omissions and pass after the fixes, including a checkout
path containing spaces. Runtime helpers and workloads are unchanged.

Source/static: **pass**. 614 focused tests pass with two existing skips. All seven
native course validators, generated artifact freshness and helper parity pass.
Scoped Ruff, ShellCheck on 35 changed launchers, learner shell syntax, Markdown
structural checks and whitespace checks pass. Markdown checks retain long-line
and COURSE.md emphasis-field conventions; no lint configuration was changed.
Final read-only code/security review confirms both findings resolved.

Browser/layout: **pass**. Six owned, isolated headless Chrome 154.0.8037.58 cases
cover the two affected pages at 1440×1100, 390×1100 and 320×1100, including links,
figure containment/placement, keyboard navigation, TOC/scrollers and text reflow.
No external requests or page errors occurred. Two additional screenshots show the
repaired preparation blocks and were visually reviewed. Runners closed their owned
browser resources. Evidence group: `course-align-kvhui3wx`, with browser/visual
JSON results, per-page identities and screenshots; no traces were recorded.

Other course artifact hashes remain unchanged from the preceding review. This
follow-up does not replace its broader test evidence or resolve its unavailable
PyTorch/live-target lanes and generic-checker diagnostics. No live installation,
GPU execution, external publication or commit occurred.

## Command-first Lab Guide — local verification: 2026-10-01

Shared guide HTML SHA-256: `e4a5181e9632f846941d6fb8f0d1aabbd9ea8c822abf1c14a9a8dbe426cdace0`.

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

## Current single-appendix review

All seven course pages now have one course-wide Where to Go Next and one Glossary.
Removed 84 lesson-local and five performance-guide copies of both sections.
The shared glossaries retain all original keys and distinct meanings, including
93 migrated terms, for 369 total entries. Existing explanations, practical work,
optional study topics and local appendix anchors remain intact.

All seven repository validators, 242 focused tests (three existing skips),
generated/helper parity and scoped lint pass. Shared authoring validation rejects
local or nested appendices, duplicate headings/TOC entries, incorrect final order
and glossary source drift. Preservation checks cover 1,584 protected files,
including all 110 implementations and 110 lab guides. Read-only code/content
review and the scoped security review found no actionable issue.

All 21 complete-page Playwright Test cases passed in isolated headless Chrome
153.0.8010.53 at 1440, 390 and 320 pixels. Glossary text, appendix structure,
fragment/keyboard navigation, mobile TOC, focusable code blocks and doubled-text
reflow pass. Seven additional narrow-screen cases passed actual keyboard scrolling
inside local code/table regions. All seven narrow glossary views and selected desktop, next-step and
enlarged-text views were visually inspected. Final artifact identities,
screenshots, the omitted final traces and earlier failed attempt are recorded in
each course's publication review under private group `course-glossary-madfkwxf`.

The generic checker still reports the existing five conceptual-GPU, six text-only
and seven labs-only markup/profile diagnostics; it is not claimed passing.
Independent source manifests and repository source parity pass. This task did not
install dependencies, run labs or publish content; earlier runtime qualification
keeps its original scope. The sections below retain historical evidence only.

## Historical glossary ordering

Every course retains Glossary immediately before References in content and
navigation. All 84 conceptual lessons and five performance-tool guides now use
Objective → How it works → Practice → Mental model → Glossary, followed only
by optional References. Local glossaries use semantic definition lists, A–Z
terms and concise definitions; existing teaching and practical work are preserved.

All seven repository validators, 230 focused tests and generated/helper parity
pass. Twenty-one owned isolated headless Chrome 153.0.8010.53 cases pass across
1440, 390 and 320 pixels, with three additional local-glossary captures visually
reviewed. See each course's publication review for exact HTML identities,
artifacts, generic-checker differences and evidence limits. The following
sections preserve the earlier format review and its historical evidence.

## Outcome and scope

All seven courses now share canonical titles, readable contents navigation,
semantic lesson subheadings and responsive typography. The five conceptual GPU
courses retain 78 numbered lessons; Soperator retains six text-only lessons;
the advanced communication course retains 34 executable labs and setup-only
Lab 00. The catalog still contains 110 executable labs and 116 dashboards.
No lab implementation, launcher, environment or runtime dependency changed.

The review covered course missions, reading order, lesson titles and summaries,
syllabi, metadata, all local guide titles and section structures, moved-lab
references, generated pages and direct consumers of the shared renderer.
It used create-learning-course and the final align gate with code-review,
linter and apply-security. No installation, deployment, publishing or Git
operation was performed.

## Findings resolved

| Finding | Correction and evidence |
| --- | --- |
| Obsolete contents wrappers | Five GPU course TOCs now list numbered lessons directly. Lab contents use exact guide titles, without base-route wrappers or duplicated numbering. |
| Inconsistent lesson labels | Objective, How it works, Practice and Mental model have the same semantic h3 treatment under each lesson h2. The old Practice labs schema fails validation. |
| Title and reading-order drift | Course titles match metadata and READMEs; missions use Course mission; syllabus rows follow the preserved numbered lesson order. Canonical title guards reject drift. |
| Stale distributed references | Moved activity references point to their owning advanced guides. Cluster requirements describe execution of linked labs. Inference Lesson 15 links directly to Dynamo Labs 32–34. |
| Weak final-lesson framing | Fabric, training and inference objectives and summaries describe their specific learning outcomes. Repeated generic wording and several grammatical errors were corrected. |
| Enlarged-text overflow | Five GPU pages previously overflowed at 320px with 200% text. Shared wrapping and local table scrolling now pass on every page. The catalog received the same wrapping fix and its learning map identifies seven courses. |
| Keyboard and accessibility consistency | Semantic field headings and the license code block support consistent heading navigation and keyboard focus; local code/table scrollers remain focusable. |

The final changed-scope code and security review found no remaining blocking
issue in these surfaces. Escaping, course-local ownership, source preservation
and profile-specific constraints remain enforced. No new credential handling,
external resources, dependencies or public exposure were introduced.

## Verification

| Evidence lane | Result |
| --- | --- |
| Repository source validation | All seven course validators passed, including deterministic HTML parity, complete prose/source listings, metadata, dashboard/helper/kit parity and local references. |
| CPU regressions | 1,019 passed, with two loopback API fixtures run separately and both passing. |
| Final focused regressions | 318 passed after semantic-heading and scoped test-lint changes; 91 passed after the final prerequisite wording refinement. These overlap with the full suite and are not additional unique tests. |
| Changed-scope lint | Ruff check and format, configured Markdown lint and whitespace checks passed. Existing unrelated dirty work was preserved. |
| Full-page browser | 24 final Playwright Test cases passed: all seven courses plus the catalog at 1440, 390 and 320 pixels, using owned isolated headless Chrome 153.0.8010.48. |
| Browser interactions | TOC keyboard toggle, heading-in-viewport navigation, exact local fragment targets, sibling navigation, six practical setup-dashboard/ZIP downloads, figure text bounds, keyboard scrollers and whole-page 200% text reflow passed. No external request or page script error occurred. |
| Visual inspection | Representative desktop/mobile and enlarged-text pages were inspected alongside the repeatable browser assertions. |
| Executable preservation | Task-start hashes confirm every lab implementation, Slurm launcher and environment file is unchanged. |

The private artifact group `course-format-align-20260918` contains the final
browser JSON, viewport screenshots, traces, task-start hashes and test logs.
Final browser evidence is checked against the exact hashes below. Playwright
owned and closed all browser processes and contexts; no personal profile was
used. An earlier figure assertion ignored SVG rotation and was corrected to
compare transformed screen bounds. Earlier snapshots and failures remain
separate from final evidence.

## Generic checker and runtime limits

The installed generic skill checker was run on all seven complete pages with
exact source manifests. Embedded source membership and bytes pass. It reports
five format differences on each conceptual GPU page, six on the text-only
course and seven on the labs-only course. These include its rejection of the
favicon link, footer small element, relative navigation/data downloads and
cascading HTML-stack checks. Its required diagrams and conceptual lessons
conflict with the user's explicit text-only and labs-only profiles; the advanced
page also produces a cascading CSS finding. The generic checker is **not**
reported as passing. Repository profile-aware checks and actual browser
behavior provide the scoped format evidence.

Installed NVIDIA environments, native CUDA/NCCL/RDMA activation, live Slurm
execution and Grafana ingestion were not exercised. Existing live H100
qualification remains pending for practical courses. Soperator requires no
lab runtime; its command examples are documentation, not live cluster proof.
This review establishes source and presentation consistency, not measured
performance, speedups or blanket publication readiness.

## Final HTML identities

| Page | SHA-256 |
| --- | --- |
| catalog | `7fd80e99a48ba97673cf7f0af4505a8d59bf90d82b1d496047e0239c65f18735` |
| gpu-fundamentals | `cd35883ef5930f4ac2c73431cf1e25ea1520145716817165449e9b399a07b8b2` |
| gpu-optimizations | `850d593fa608f4bdc1f609fd357aed2b252e4af6b58ff1d63512df129e606dcf` |
| llm-training | `7a42db08e08ea3a4d08b7f3d742be5c903dad976901ae0dc51bc72e4e2d540cd` |
| llm-inference | `7a9154d55aea07a5cad606808d3f73c5cc3fefedbac6f66710ede8e51f91f656` |
| custom-cuda-kernels | `17c602b992c5c36cacf251aadf7187edbc0caf4f463744cc86c30b358ff1b2dd` |
| soperator | `5dee93bc7acc0f33da112c9353ad2124bb1d3cd4bab98590892850f88eb8c1b3` |
| advanced-gpu-communication | `4280d53b1989e620300f8b340b31d5d5526f82ff4d3ad9134af48e78767220cd` |
