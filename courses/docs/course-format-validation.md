# Course format and navigation review

This report records current and historical format and navigation evidence. The current
authoring contract is in [Maintaining the courses](maintaining-courses.md#educational-approach);
each course's `PUBLICATION-REVIEW.md` identifies its latest reviewed HTML.
Conceptual GPU lessons use Objective, How it works, Practice and Mental model,
followed only by optional References. Reading and labs-only courses retain their
declared profiles. Each course closes with one Where to Go Next, one Glossary
and final Official references.
The earlier counts, field order and hashes below apply to their original revisions.

## Complete teaching consistency review — 2026-10-05

Reviewed the current eight course packages, shared Lab Guide and catalog against
their canonical requirements, syllabi, metadata and supplied implementations,
using create-learning-course and a final align pass. Coverage includes all 89
lessons/reference chapters and the substantive teaching in all 110 lab guides:
objectives, definitions, worked reasoning, practice, evidence interpretation,
troubleshooting and transfer. Repeated command/publication blocks also receive
mechanical checks. Suspect implementation claims were traced to their producers;
this is not an exhaustive execution audit of every source line.

| Course | Chapters | Labs | Corrections |
| --- | ---: | ---: | --- |
| Soperator | 6 | 0 | Match the log-reading example to the earlier batch output paths. |
| GPU Performance Tools | 5 | 0 | Define SM and DCGM at first use and expand the shared glossary. |
| GPU Fundamentals | 12 | 11 | Merge four duplicate glossary concepts, retain their meanings, add ILP and replace a retired workload label. |
| GPU Performance Optimization | 16 | 14 | Identify refreshed CUDA Graph inputs as an optional extension of the supplied fixed-input experiment. |
| LLM Training | 17 | 16 | Add MLP/RoPE and expand Kullback–Leibler in the glossary. |
| LLM Inference | 17 | 22 | Correct Compute host-memory requests, positional concurrency syntax, job-owned result/log paths, glossary gaps and the modeled placement competency. |
| Custom CUDA Kernels | 16 | 13 | Distinguish supplied SIMT/cluster probes from optional architecture-specific work, correct the stencil comparison, explain H200 acceptance and add taught glossary terms. |
| Advanced Labs | 0 | 34 | Correct report paths, workload labels and two Lab 12 link labels; expand the glossary and remove an empty command block. |

The two affected observability manifests now match the corrected learner
commands and comparison instructions. Catalog order, lesson/lab identities,
prerequisites, explicit profile exceptions and shared presentation are preserved.
Soperator remains text-only, Tools remains reference-only, and Advanced Labs
remains labs-only. The project-defined Practice heading in practical guides is
intentional. These repairs restore existing contracts; they do not change the
canonical requirements/design pair.

All lab implementations, launchers, diagrams and six result ZIPs remain
byte-identical to their task-start artifacts. Generated course HTML and the
shared Lab Guide were rebuilt from canonical sources. The catalog HTML is
unchanged. No runtime performance change or optimization benefit is claimed.

Source/static: all eight native validators and generated-output freshness checks
pass, including prose/source parity, navigation, guide identities, glossary
ordering and bounded public-safety checks. Markdown lint, JSON syntax, focused
diff review and public-safety review pass. Independent review found no blocking
issue in the corrections. The final full offline suite passed **2,454 tests**
with no skips or failures. One environment warning reports unavailable NumPy;
no covered test failed because of it.

An earlier full run overlapped source editing: 2,443 tests passed and 11 freshness
checks failed because the HTML still held the previous prose. The pages were
rebuilt before the final run. The first interpreter lacked pytest and another
existing environment lacked test dependencies; the final run uses an existing
Python 3.12 environment with pytest and PyTorch. No dependency was installed.

The unchanged generic skill checker was run on all eight courses with exact
source and local-link manifests. It retains the documented five diagnostics per
conceptual course, six for Soperator and seven each for Tools and Advanced Labs.
These cover shared favicon/footer markup and resulting structure diagnostics,
plus explicit profile differences and the existing labs-only CSS diagnostic.
It is not recorded as passing; native checks are profile-aware, and no checker
was weakened.

Browser/visual: not rerun for this editorial review. The earlier presentation
audit below applies to its recorded artifacts; it is not fresh evidence for
these changed pages. Its dense-diagram limitation at 320px remains unresolved.
Installed environments, runtime activation, real Slurm/GPU execution and external
publication were not requalified. Applicable qualification gates remain pending;
this review does not declare publication readiness or prove every vendor-version
claim. No new test cases were added for these reversible documentation repairs;
existing parity and command-contract checks provide the mechanical regressions.

The host-memory correction follows [Slurm submission memory semantics](https://slurm.schedmd.com/sbatch.html)
and [Nsight Compute replay behavior](https://docs.nvidia.com/nsight-compute/ProfilingGuide/).
Architecture wording was checked against the supplied CMake/CUDA sources and
[NVIDIA architecture-specific target guidance](https://developer.nvidia.com/blog/nvidia-blackwell-and-nvidia-cuda-12-9-introduce-family-specific-architecture-features/).
The example's 256 GiB request is explicit, not a guarantee of sufficient memory.

| Reviewed artifact | SHA-256 |
| --- | --- |
| `index.html` | `c2809b51a263207af7871e94a741b87c51ecbaf8562803442d3e992f29a6cc3e` |
| `lab-guide.html` | `b63dfe9a1de3a1e54cd23e06d9e809b00377696de1d420ba314ecce9ebf8883e` |
| `soperator/index.html` | `fa810be9e467e4bdf72f8042c315f2113ee997111464b6997e94004cafd3de64` |
| `gpu-performance-tools/index.html` | `2f3dfd9f57aa53312793a1f2346ff1095604b4b0f033eba25bc9664939a4884e` |
| `gpu-fundamentals/index.html` | `b6363a0ee0b6e3764410c981e49e966533fe60b7a9ae233d25caef629237ab15` |
| `gpu-optimizations/index.html` | `49c8b945f38d5aa4fa0230aa9c91863b6f6bf2f9617d52183f7b3dcba37c044d` |
| `llm-training/index.html` | `f72a299c89a2455ddc2866b9a28eaaaf2b247497e3e9c84317b994ef37d26d25` |
| `llm-inference/index.html` | `751fb2d9e693442f3f97dbd96ecefe5b8624741925b954523696f1446e07ebb6` |
| `custom-cuda-kernels/index.html` | `8e6c811e979c10d7c721dbb5f5781e6e348f07a8f82ab538c6c519db672a051b` |
| `advanced-gpu-communication/index.html` | `c08cf9fc29c35b67dafe736a8d17a772b2291c23a71339af6fafb420556e548c` |

## Consistent catalog badges — 2026-10-05

Removed the course-specific badge overrides from `tools/catalog.css` and rebuilt
the catalog. All nine card badges now use the same mint background (`#edf6f0`)
and teal text (`#206757`), including Fundamentals (04), Training (06) and
Inference (07). Browser checks at 1440, 390 and 320 pixels confirm these colors
for every badge, unchanged numbering, and no page overflow or script errors.
All 88 catalog tests and full generated-output freshness
checks pass. Only the catalog HTML changed; the other nine pages and six result
ZIPs are byte-identical to the preceding audit.

Current catalog SHA-256:
`c2809b51a263207af7871e94a741b87c51ecbaf8562803442d3e992f29a6cc3e`.
The complete audit below records the preceding revision and its limitations.

## Complete catalog presentation audit — 2026-10-05

Scope: all eight course packages, the catalog and the shared Lab Guide. The
review covers page structure, shared typography, formatting, navigation and
build freshness. It does not requalify technical claims or live lab behavior.
Soperator remains text-only, GPU Performance Tools remains reference-only, and
Advanced Labs remains labs-only. These intentional profiles share the same
course stylesheet and equivalent heading roles. The catalog uses its existing
card stylesheet with the same visual palette and font family.

Source/static checks: **380 passed, 3 skipped**. The skipped checks require
PyTorch and exercise runtime correctness; no dependency was installed for this
presentation review. All eight native validators, helper parity and the full
`./build-courses.sh` build/check pass. The ten HTML pages and six result ZIPs
remain byte-identical to their task-start artifacts. Lesson order, appendices,
source listings, glossary ordering, contextual figures and all practical
content are preserved. No CSS or learner-source change was needed.

One existing supporting-guide test failed because it omitted the shared guide
link mapping passed by the production renderer. The test now uses that same
mapping and independently asserts both the Advanced Labs destination and the
Lab Guide preparation fragment. The original failure was observed and the
repaired test passes. Its inventory test now describes Advanced Labs as last,
without a stale ordinal in the test name. No renderer fallback was introduced.

Browser/interaction checks: **30 passed**, with an owned, isolated, headless
Chrome **154.0.8037.93** driven by Playwright Test. Every complete page was
loaded at **1440×1000, 390×1000 and 320×1000**. Assertions cover exact resource
order and numbering, current identity, single page titles, fragment targets,
unique IDs, course CSS identity, closing-section order, lesson counts, keyboard
skip links and collapsible menus, local table/code scrolling, SVG/text bounds,
normal-width containment and doubled-text reflow. No script errors or automatic
external requests occurred. Framework-owned browser/context resources closed
after the run; no personal browser profile was used.

Visual review confirms consistent page typography, spacing, teaching-field
hierarchy, captions and local table/code containment. **Mobile diagram density
remains a limitation:** some detailed wide SVGs have internal labels too small
to read comfortably at default 320px scale. The inference lifecycle and custom
kernel application diagrams are examples; their adjacent captions remain
readable. Containment and reflow success do not establish label readability.
A diagnostic inventory found labels below 7 CSS pixels in 55 of 136 figures;
that threshold describes density, not a universal accessibility criterion.
Stacked or split mobile variants remain a separate diagram-authoring follow-up.
This audit therefore does not claim unrestricted mobile visual readiness.

The installed skill checker was run unchanged on all eight rendered courses
using exact source and companion-link manifests. It still reports five generic
markup diagnostics for each conceptual GPU course, six for Soperator, and seven
for each reference-only and labs-only course. These include the favicon,
license-footer markup and cascading parser diagnostics, plus profile-specific
lesson/diagram expectations. The labs-only page has its existing CSS diagnostic.
The first checker harness omitted advanced runtime-helper sources; correcting
the independent source manifest resolved that harness-only diagnostic. Exact
embedded-source bytes and native profile checks pass. The generic checker is
**not** recorded as passing, and neither the checker nor course profiles were
weakened to suppress its diagnostics.

Changed-scope alignment includes read-only code/security review, Ruff,
configured Markdown lint and whitespace checks. Local builds and publication
size checks do not prove deployment. Installed environments, runtime activation
and live Slurm/GPU runs were not part of this review; existing qualification
requirements remain in force.

Local evidence group: `course-presentation-b22ml58a`. Its `browser-report.json`
contains per-page/browser records; screenshots use `<resource>-1440.png`,
`<resource>-320.png` and `<resource>-figure-<width>.png` for pages with figures.
Representative desktop and mobile captures from every resource were inspected.
Tracing was configured for failures; the successful run retained no traces.
Raw browser logs and screenshots remain outside the repository.

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
