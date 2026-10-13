# Course format and navigation review

This report records current and historical format and navigation evidence. The current
authoring contract is in [Maintaining the courses](maintaining-courses.md#educational-approach);
each course's `PUBLICATION-REVIEW.md` identifies its latest reviewed HTML.
Conceptual GPU lessons use Objective, How it works, Practice and Mental model,
followed only by optional References. Reading and labs-only courses retain their
declared profiles. Each course closes with one Where to Go Next, one Glossary
and final Official references.
The earlier counts, field order and hashes below apply to their original revisions.

## Complete catalog review and alignment — 2026-10-13

Reviewed all nine course packages: 105 lesson/reference chapters, 110 compact
lab guides, missions, syllabi, READMEs, glossary and onward-study sources,
metadata, shared styles and navigation. Preserved the text-only, reference-only,
lessons-only and labs-only profiles and every lesson/lab identity.

### Corrections

- Added Advanced Labs’ missing **Complete runtime support** TOC destination.
  Corrected maintainer catalog counts and retained the shared typography.
- Put the Optimization, Training and Inference syllabus summaries in their
  numbered reading order; aligned all five practical syllabi with compact labs.
- Added 11 taught-term glossary entries, defined tensors/operators before the
  profiler explanation, and made every Tools onward option a bullet.
- Clarified autograd’s gradient label, participating ranks in the serving
  diagram, CUDA preparation versus preflight, and sanitizer/profiler selections.
- Connected CUDA capstone teaching to its supplied three-trial selection while
  identifying application integration as a separate learner exercise. Located
  the introductory AIPerf submission in Readiness checks with its actual defaults.
- Changed Lab 09’s illustrated and baseline recipe budget from one to two output
  tokens so it performs both prefill and continuation. Aligned the guide, five
  observability commands, and five source/installed campaign recipe fields.
  The eight-token candidate and one-token edge-case coverage remain valid.

### Verification and limits

| Lane | Result |
| --- | --- |
| Source/static | All nine native validators, full build and HTML/archive freshness pass. 748 focused tests pass with three unavailable-Torch skips and one Linux-only skip; 113 focused tests pass after the AIPerf wording repair. Ruff, configured Markdown lint, whitespace and paired spec validation pass. |
| Semantic/code/security | Four bounded read-only source reviews covered the catalog. Independent final review caught and resolved misleading AIPerf help/command wording. No remaining concrete changed-scope finding. This is not renewed expert qualification of every technology or external reference. |
| Preservation | All 3,249 tracked lab, launcher, environment, dashboard and historical-result/archive files are unchanged. The six archive SHA-256 values match the task-start snapshot. Source and installed recipe JSON remain byte-identical. |
| Browser/visual | 33 isolated full-page checks pass at 1440×1000, 390×1000 and 320×1000. Three Inference cases were rerun after its final prose correction; all 33 fact records match the final hashes below. Exact catalog/menu order, fragment targets, unique IDs, appendices/glossary order, keyboard controls, local scrollers, page containment and doubled-text reflow pass. Computed body/H2/H3 typography agrees across all nine courses at each width. |
| Installed environment/runtime/live target | No packages installed, GPU/Slurm jobs submitted, serving workloads run, or external publication performed. The 21-command regression executes the existing pure generation scheduler, not a real model. Existing target qualification limits remain in force. |

Browser evidence uses Playwright Test 1.62.1 and owned headless Chrome
155.0.8059.40, with page JavaScript disabled and isolated contexts. Entry,
glossary and selected figure captures were visually inspected across all nine
courses, plus the catalog and Lab Guide. No page-level overflow or desktop SVG
label overflow was found. The revised autograd label is readable at 320px.
Older dense multi-column diagrams, including the serving-parallelism figure,
still have small phone labels; their visible captions explain the relationships.
This review does not claim unrestricted mobile diagram readability or full
publication readiness. Required live-target gates remain pending.

The generic skill checker was run on all nine current pages and reconstructed
starting pages. It reports identical diagnostics for shared shell/CSS/listing
markup, relative navigation and explicit nonstandard course profiles. Native
profile-aware validators remain the acceptance gates; the generic checker is
not claimed passing or weakened.

Local evidence group `course-review-mv9sjep6` contains the baseline and final
hash inventories, semantic regression negative control, build/native/test logs,
generic-checker reports, `publication-verified.spec.cjs`,
`playwright-verified.config.cjs`, `browser-verified/<page>-<width>/facts.json`,
`entry.png`, `glossary.png` and selected figure captures. The 33-case log is
`browser-verified.log`; `browser-inference-final.log` records the final three
cases. Traces use `browser-traces-verified` with retain-on-failure, so passing
final cases produce no retained trace. Test-owned contexts and browser processes
closed; no personal browser profile or server was used.

Initial evidence remains separate: builds rejected an unsupported source
fragment and an extra compact-guide introduction paragraph; both were repaired.
A prematurely launched test run observed stale generated pages, then the complete
post-build suite passed. The older Playwright 1.57.0 trial timed out on Lab Guide;
the available 1.62.1 runner and explicit screenshot positioning passed against
the same guide bytes. This is a harness correction, not a product fix. Tests use
existing cached pytest 9.1.1 and dependencies; they do not qualify a clean install.

Terminology checks used the official [PyTorch AdamW documentation](https://docs.pytorch.org/docs/2.14/generated/torch.optim.AdamW.html),
[PyTorch profiler documentation](https://docs.pytorch.org/docs/2.14/profiler.html)
and [NCCL collective definitions](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/usage/collectives.html).
Browser harness options were checked against [Playwright configuration](https://playwright.dev/docs/test-use-options).

### Reviewed artifacts

| Page | SHA-256 |
| --- | --- |
| `index.html` | `b2a0076e140ebfd647f9bc74e4cc2467c3b74115810a7c0d6fd10efe141ae349` |
| `lab-guide.html` | `fd2256d04e4a7e585a6ca4dac9c8d812ef0cae4f575e3c300d37cd262463b05d` |
| `gpu-fundamentals/index.html` | `9cf9b2ecb1bc4b0d05c2882c6d0deb86544f65a1bbceeb9e6c5e105ada3b1128` |
| `custom-cuda-kernels/index.html` | `60b6efc61a676ba67dc3c6932320fbd479c6df836000c4ea9f599ecc05a9c134` |
| `advanced-gpu-communication/index.html` | `1b51b58d11ab2b6e42d8d7108cb381c2bd9891c3de581dcace71bdf362eb65bd` |
| `gpu-performance-tools/index.html` | `d23659e2dc45da9dd070018fc7976152e96841f11e544577132769990a89d6ce` |
| `pytorch-gpu-performance-engineering/index.html` | `2ade22b24a401a65cf5678e50921f25c0b12b6450654bf75008e1d3171054ebb` |
| `llm-inference/index.html` | `d56e57ce26be532df63f273692c907719ade656a9980e854f4a2cbf52917fb5a` |
| `soperator/index.html` | `ccc6e8804cb25eb5836722203e13967187327a81ea2b1a4073d673eeea92eaa3` |
| `llm-training/index.html` | `fc2a0f42b3c07838aafbc450a72332b12b9b728107f9e350ff5cfdfee95cc904` |
| `gpu-optimizations/index.html` | `2c0925d01f604f93b94cdcacf187ac1632345f6ea5aceedddb9b6095c63a9c69` |

## Course guide cleanup — 2026-10-13

All nine READMEs were reviewed and shortened from 9,534 to 3,029 words. Removed
obsolete lesson-format descriptions, layout commentary and accumulated
implementation notes. Retained course prerequisites, first activities, completion
criteria and distinct evidence limits; kept worked lab interpretation examples.
Five syllabi no longer repeat the obsolete template paragraph. Local readiness
runbooks hand off distributed work to Advanced Labs and CUDA no longer advertises
nonexistent multi-node exercises.

The six practical pages share ordered guides with matching body/sidebar titles:
Getting started, Versions and environment, Readiness checks, Lab mechanisms and
evidence, Benchmark worksheet, and Evidence and privacy. Optimization retains
its additional Diagnostic tooling reference. Advanced Labs now includes the
same submenu. Three reading profiles keep their existing appendix structure.

| Review lane | Evidence and limits |
| --- | --- |
| Source/static | All nine native validators, complete build/freshness, 353 focused tests, six final guide-order checks, configured Markdown lint, Ruff and whitespace checks pass. Three existing Torch-dependent tests were skipped because the test environment lacks Torch. |
| Preservation | At guide-cleanup verification, 1,546 snapshotted lab, launcher, lesson, diagram, version, metadata and result/archive files retained their bytes. The three reading-only HTML pages and catalog were unchanged. |
| Semantic/security | Independent read-only review found one retained CUDA hardware-scope error, corrected and re-reviewed. No further actionable finding remains within this scope. Privacy guides and qualification limits are retained. |
| Browser/visual | 21 complete-page Playwright Test cases pass: six practical pages plus Lab Guide at 1440, 390 and 320 pixels. Guide order/titles, keyboard fragment navigation, skip link, mobile TOC, local scrolling, unique IDs, fragments, passive resources and doubled-text reflow pass. Representative desktop/mobile guide captures were visually inspected. |
| Installed environment/runtime/live target | No dependency installation or lab/runtime behavior change. GPU, Slurm, fabric, serving and profiler execution were not performed; earlier qualification records keep their own scope. |

Browser: owned isolated headless Chrome 155.0.8059.40, page JavaScript disabled.
The test runner owns and closes its browser contexts/process. Local evidence group
`course-guides-acye6xey` contains `guides.spec.cjs`, `playwright.config.cjs`,
`browser-results-final.json`, `browser-final/<course>-<width>/facts.json`, and
`getting-started.png`/`readiness.png` captures. Final traces use
`browser-traces-final` with retain-on-failure; no failing final trace is expected.

The initial browser trial retained four failed navigation assertions and three
passes before stopping. Sequential focus/fragment actions were sampled during
smooth scrolling. The harness now waits for scrolling to settle and checks the
exact fragment and target heading, with background throttling disabled. The
formerly failing 390px case and the complete final matrix pass without product
scrolling/CSS changes. Initial logs and partial traces remain separate.

The generic authoring checker was run against all nine reviewed pages and exact
reconstructed task-start pages, whose digests matched the initial snapshot.
Its diagnostics are unchanged: shared-shell markup/CSS and relative-link rules,
plus explicit reading/labs-only profile differences. Native profile-aware gates
and independent generated-link checks pass; this does not declare every generic
checker rule satisfied. Early checker setup failures (missing baseline copies
and a symlinked temporary-root spelling) are separate from the completed checks.
No live qualification or external publication is claimed.

A concurrent task subsequently changed shared code-comment styling and regenerated
pages. Those changes are preserved and are outside this browser trial. The evidence
above applies to the exact reviewed digests below, even when the working-tree
HTML differs. A subsequent read-only build freshness check passed for the shared
working tree; it does not extend the browser evidence to the later styling.

### Reviewed artifacts

| Page | SHA-256 |
| --- | --- |
| `gpu-fundamentals/index.html` | `04e7271bca54b31cd3a10c6aeb5763f4e06bd39c0bce19389fae943087221cdd` |
| `gpu-optimizations/index.html` | `d5cd3891189b5ece6b54e36b3264e2897490652448c466aa1baab7f61569b956` |
| `llm-training/index.html` | `694e5fcf581a5efe90d80b9e9d9ec34de173a6c3c712538f28265fedffcd4f7d` |
| `llm-inference/index.html` | `3529812a26ea8de69794eaadcfba6f8ec372dc0f0a2136c741982e58368a9681` |
| `custom-cuda-kernels/index.html` | `320b2d13c5f43efab95081f176e58193fce9ccafc6f96102eb078eead997efd3` |
| `advanced-gpu-communication/index.html` | `3a9192825c68e7579bcd52b45658ac41870b9f9bb01d07ae0180b5706c48801d` |
| `lab-guide.html` | `e27705b18a7e3fe51033bca3d3b8bba5732b0851b7db494e482117268e263860` |

## Consolidated-job alignment follow-up — 2026-10-12

A fresh alignment pass repaired three reproduced contract defects: host-copy
verification expected the retired direct-Python command; nine fabric labs reported
two-node scope for supported one-node submissions; and reader JSON silently omitted
original JSON larger than its 8 MiB limit. Canonical recipe validation, validated
allocation-dependent scope, and explicit data errors now preserve the intended
execution and evidence boundaries. Failed or oversized inputs retain their files.

- The full Python suite passed **2,528 tests**, with one Linux-only skip on macOS
  and the existing missing-NumPy warning. **79 isolated Linux fixture tests**
  passed, including child adoption. Negative controls reproduced each defect.
- Independent read-only review checked all eighteen affected fabric variants at
  one and two nodes and all four host-copy recipe combinations. No remaining
  blocking correctness or security finding was identified in the reviewed scope.
- All nine native validators, full build/freshness, helper and 27-file skill
  payload parity, scoped lint, 113 shell checks and 220-unit dry-run pass.
- Playwright Test passed three fresh Lab Guide cases at 1440, 390 and 320 pixels.
  It is the only changed HTML page; its current SHA-256 is
  `405e49813523b8bed3bf152f9f95e74cce85654bf51cd52b3e54db33345c6c6b`. The other ten HTML identities below
  remain unchanged, as do every lab program and all 2,872 historical evidence
  and archive files. Owned browser resources and the loopback server were closed.

README, maintainer guidance, changelog and paired design evidence reflect the
repairs. Prior verification below retains its original revision scope, including
the earlier Lab Guide hash. Generic-checker limitations and the separate pending
live GPU/Slurm/Nsight qualification remain unchanged.

## Compact labs and consolidated native jobs — 2026-10-12

All 110 practical labs now present a concise purpose, complete actual source,
optional collapsed diagram, two canonical submissions and exact-job accounting
and JSON commands. Twelve existing lab figures remain unchanged. Reading-only
profiles, course downloads and lesson Practice links are preserved. The native
interface has 110 canonical launchers and three auxiliary jobs; profiler and
optional-experiment selectors replace the retired variant filenames.

### Verification

- Full Python suite: **2,519 passed, one skipped** in 377.91 seconds. The skip is
  Linux child adoption on macOS; an isolated Python 3.12 Linux container passed
  **71 native-job, capture and lifecycle tests**, including that regression.
  The local PyTorch environment reported its existing missing-NumPy warning.
- Final focused checks after the auxiliary-preparation fix and aggregate
  exclusion regression: **109 passed**. Native fixtures cover failures,
  unsupported selections, diagnostic order, partial output, report checks,
  timeout, interruption, cleanup and malformed/nonfinite measurement JSON.
  The complete metadata matrix covers all normal and declared diagnostic paths;
  fixtures do not execute 110 real GPU workloads.
- All nine native course validators, full build/freshness, shared-helper parity,
  27-file source/installed run-labs parity, 211-file scoped Ruff lint, 165-file
  Markdown lint, Bash syntax, ShellCheck error checks and diff whitespace pass.
  A read-only all-course campaign resolves **220 small/large units**.
- All 97 Python programs preserve their executable AST except the authorized
  Lab 12 distributed-initialization repair and its unused import removal.
  All 13 CUDA programs preserve tokens apart from comments. All twelve lab SVGs
  and **2,872 historical result/archive files** retain their original bytes.
- Bounded code/security review findings were repaired and verified: detached
  child ownership after early shell exit, instrumented-result eligibility,
  overflowing JSON numbers, malformed measurement sections and auxiliary-job
  preparation. No live target was used.

### Browser evidence

Owned, isolated headless Chrome **155.0.8059.39**, driven by Playwright Test,
passed **33 cases** across all eleven rendered pages at 1440, 390 and 320 pixels.
Three additional Custom CUDA cases refreshed its final preparation wording.
Practical-course assertions check every lab's order, exact source bytes, collapsed
source/diagram state, two submissions, exact-job results and absence of repeated
ZIP pointers. Keyboard disclosures, navigation controls, local code scrollers,
page width and doubled-text reflow pass. Shared/reading pages preserve their
profiles and valid fragment targets. The eighteen practical cases took 11.3
seconds; fifteen shared/reading cases took 3.3 seconds.

The private validation bundle retains the repeatable test, logs and inspected
`verified-desktop.png` and `verified-mobile.png` screenshots. An earlier harness
assertion counted the `.sbatch` filename suffix as another command; anchoring
command detection to line starts corrected the test, and fresh runs passed.
The failed attempt's trace export timed out and is not evidence. Playwright MCP
exploration was supplementary. Owned browser resources and the loopback server
were closed; the task-created MCP snapshot was removed.

### Limits

The generic create-learning-course checker was run on all six practical pages.
It still rejects this repository's supported companion links, markup/CSS and
labs-only profile, so it is not reported as a passing gate. Profile-aware native
validators and browser checks pass. Retained diagrams can have dense small labels
on narrow screens; their captions remain readable. No live GPU, Slurm, Nsight,
cluster preparation, performance acceptance or external publication is claimed.
Original measurements remain authoritative; the new reader JSON is never clean
benchmark evidence.

### Reviewed HTML identities

| Page | SHA-256 |
|---|---|
| `index.html` | `4e6b17804a36e3194a91eecc0c97916d1eecba5ad1a637d494689d4100e4a019` |
| `lab-guide.html` | `f9c403dd0921312bcfa390305f7cb5d4006180a17bfa1999d886d33d2b03f610` |
| `advanced-gpu-communication/index.html` | `8daa89f394669232a718f25a7c969c34539e3f882c2938f552178ca682eff139` |
| `custom-cuda-kernels/index.html` | `244ee829c931046829277f8dadad43faffbb36a8bc7555d71f4a5a0c765ba9bf` |
| `gpu-fundamentals/index.html` | `09137d9f4961d87de2431ba065ebe2d8856fe1a6578b4d457473ebd194ea5fe5` |
| `gpu-optimizations/index.html` | `159b12f0854970efec3200653944c1498d272d04b151c7f9437949281d73ffa6` |
| `gpu-performance-tools/index.html` | `44070a489d360aa6791d7e7ab238a98e0e51bf2a06026408e8af63519cd1aa22` |
| `llm-inference/index.html` | `2d765cdfb7260dba43915eccf0a253434f1d06fa97bcc6105be93044b29d7516` |
| `llm-training/index.html` | `68130c1e2fd50867c0b125e7badf0398dc9f41e481038862ba41185caaf04b43` |
| `pytorch-gpu-performance-engineering/index.html` | `db528da5f2465c9d0c4bcd78570fcaf9b2bf2d0c3f071cb1ea40bd983cbff64f` |
| `soperator/index.html` | `027bd9d511b0bb4c39b24c3ed929737aa58672a5b260cdc091774dfaee554aed` |

## Nine-course text and format alignment — 2026-10-12

Reviewed all nine course packages, 105 lesson/reference chapters and the concepts,
identities and takeaways of 110 practical guides. The catalog and shared Lab Guide
bring the rendered scope to eleven pages. Existing course profiles, prerequisite
routes and lesson identities remain intact, including PyTorch's sixteen lessons.

### Changes

- Added 46 definitions for terms already taught, bringing the nine glossaries to
  636 unique course-local entries. Definitions use consistent sentence casing;
  APIs keep their exact spelling. Sorted keys, rendered definitions and lookup
  destinations match the canonical Markdown.
- Corrected five READMEs that described course-level appendices as per-lesson
  sections. Every course has one Where to Go Next, one Glossary and final Official
  references, in that order in both the page and its TOC.
- Matched the Custom CUDA fusion explanation and visual caption to the supplied
  two-kernel, one-intermediate baseline: 20 logical bytes per FP32 output versus
  12 for the fused path. Clarified supported normalization widths and the optional
  Tile lesson; no kernel implementation changed.
- Distinguished optional CUDA Graph shape bucketing from supplied behavior.
  Corrected Advanced Labs' readiness framing and the Lab 05 onward reference to
  Labs 12 and 13. Expanded MPI/PMIx and distinguished first-content timing from
  token-level TTFT.
- Added the missing inline operator-profiling diagram to GPU Performance Tools
  lesson 4, with a visible explanation of CPU submission, GPU work, summary totals
  and timeline order. All five reference lessons now have a contextual diagram.

### Verified evidence

**Source and build:** 480 focused tests pass, as do all nine native validators,
complete HTML/archive freshness, the shared build, configured Markdown lint and
whitespace checks. Independent read-only code and security review found no
concrete defect. The final NaN definition received source/HTML freshness checks
and is included in the final browser artifact. No runtime implementation changed.

**Browser:** all 33 final Playwright Test cases pass using isolated, owned headless
Chrome 155.0.8059.39 at 1440×1000, 390×1000 and 320×1000, with page JavaScript
disabled. All ten reading resources share the same computed body and heading
styles at each width; the catalog retains its landing-page typography. Assertions
cover exact catalog/menu order and identity, unique IDs, every local fragment,
closing-section and TOC order, sorted unique glossary keys, keyboard skip/menu and
visible local-scroller behavior, no page-width overflow, doubled-text reflow and
no automatic HTTP requests. SVG text bounds include transforms; no label extends
outside its figure viewBox. The new profiler figure also passes a 13px minimum
rendered-label check at all three widths. Visual review covers phone glossaries,
course entries and the new figure. Owned contexts and browser processes close at
completion.

The initial baseline run reported three catalog failures because the harness
tried to focus a code block inside a closed license disclosure. The first
post-edit run reported three Tools capture timeouts while the harness waited for
scroll stability after a keyboard-scroller check. The final harness selects
visible scrollers and moves to the figure with an explicit instant scroll after
removing the prior focus. No application behavior was changed to satisfy these
harness corrections. Earlier failed runs and traces remain separate evidence.

**Preservation:** all 3,308 inventoried lab implementations, launchers, environment,
result and archive files retain their task-start bytes; no baseline file was
removed. Four practical guides received only the described prose corrections.
Existing dirty edits were preserved. No staging, commit, push, installation or
external publication was performed.

### Limits and traceability

The unmodified generic skill checker was run on every course using exact embedded
source and companion-link manifests in an isolated fixture. It reports five
known shared favicon/footer/structure/link diagnostics per standard course, six
for each reading course and seven for the labs-only Advanced course. These
profile and shared-markup diagnostics remain separate from the passing native
validators; the generic checker is not recorded as passing.

Older dense diagrams still shrink some labels below 13px at 320px. All diagrams
fit their page and retain their labels, but containment is not proof of comfortable
phone readability. PyTorch's 21 diagrams and the new operator figure pass the
minimum-label check. This audit does not claim unrestricted mobile visual
readiness. GPU execution, compiler speedups, installed environments and live
Slurm/Nsight/Grafana qualification were not refreshed by this authoring work.

Local evidence group `courses-align-3dxs35el` contains task-start hashes,
source snapshots, build/native/test/lint logs, checker diagnostics, preservation
results and the final `browser-verified` captures, harness and JSON report. The
following hashes identify the final pages; historical sections retain their own
counts and artifacts.

| Artifact | SHA-256 |
| --- | --- |
| `index.html` | `4e6b17804a36e3194a91eecc0c97916d1eecba5ad1a637d494689d4100e4a019` |
| `lab-guide.html` | `3c9b7a5c054824902f104d403d0da7cb9d7ad9045e1e1e65387704f5b89b0e18` |
| `soperator/index.html` | `976120e42c78a7d901a3b8195905e3f43b58c4613447269301645d6aff1dc6ea` |
| `gpu-performance-tools/index.html` | `bef9be5ebb76281d34cd91456d461716c679134c1370da6b35dd9d2c2e9b713d` |
| `pytorch-gpu-performance-engineering/index.html` | `b822af049a6b0d246df70ccb3528dc6c955c44fcd46e13b589df68a1848e2d62` |
| `gpu-fundamentals/index.html` | `81c9ee778300be473c5a4dedc96c535497696dc610208dc409c1ed62bb405373` |
| `gpu-optimizations/index.html` | `b5668d7b0b5487c2e23dd0d130ae4120b04f2f8b516d8116e2c5f941e8029b69` |
| `llm-training/index.html` | `aeae1ee2212df5b69098ef5171aa46c12ed709dc2bb43a920cf650bd26b7b5ee` |
| `llm-inference/index.html` | `12165cd6d248177fe4ed8fafca01e2f655b52a59221b1798bc251687aeffe422` |
| `custom-cuda-kernels/index.html` | `43cc2d547ead2f4e6775d575d425b7cb4446cf94e46292156639844e08cad400` |
| `advanced-gpu-communication/index.html` | `7db5c16760aca9cb58c1cc52f977cbda92d71b58ad81d68984eaaf312a6edcb6` |

Terminology was checked against the owning course implementations and current
primary documentation: [PyTorch autograd](https://docs.pytorch.org/docs/2.14/notes/autograd.html#locally-disabling-gradient-computation),
[Kubernetes nodes](https://kubernetes.io/docs/concepts/architecture/nodes/),
[Kubernetes custom resources](https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/),
[Linux DMA-BUF](https://docs.kernel.org/driver-api/dma-buf.html),
[CUDA Cooperative Groups](https://docs.nvidia.com/cuda/cuda-programming-guide/04-special-topics/cooperative-groups.html),
[CUDA compiler driver](https://docs.nvidia.com/cuda/cuda-compiler-driver-nvcc/index.html),
[CUDA Tile C++](https://docs.nvidia.com/cuda/cuda-tile-cpp-api-reference/general_principles.html),
[CTest](https://cmake.org/cmake/help/latest/manual/ctest.1.html) and
[PMIx](https://pmix.org/). No version or target was declared qualified merely from
reading its documentation.

The work used create-learning-course, global-context-management,
maintain-project-specs and align, with linter and nested read-only code-review and
apply-security. Prior notes guided preservation and profile handling; current
source and generated pages determined the actual inventory. All four audit/review
helpers finished; this host exposes no agent-close control. No reusable skill
change was needed.

## Nine-course alignment — 2026-10-05

Audited all nine current course packages, ten catalog resources and eleven HTML
pages with create-learning-course, followed by align. The learning route remains
Soperator, Lab Guide, GPU Performance Tools, PyTorch for GPU Performance
Engineering, GPU Fundamentals, GPU Performance Optimization, the three independent
specializations and Advanced Labs. The course packages retain their declared
text-only, reference-only, lessons-only and labs-only exceptions.

Coverage includes all 105 lesson/reference chapters and the identities, concept
explanations and takeaways of all 110 lab guides. Changed practical claims were
traced to the supplied implementation and result fields. Command/result sections
also receive native contract checks; this is not an exhaustive line-by-line
execution audit of every guide or runtime source.

| Course | Chapters | Local labs | Alignment outcome |
| --- | ---: | ---: | --- |
| Soperator | 6 | 0 | Text-only teaching and current route retained. |
| GPU Performance Tools | 5 | 0 | Reference-only teaching retained; active specs now assign its tools primer to the correct owner. |
| PyTorch for GPU Performance Engineering | 16 | 0 | Lessons-only profile excluded from shared runtime preparation; overview grouped into purpose, audience/readiness and reading route. |
| GPU Fundamentals | 12 | 11 | Mission distinguishes local hardware from Advanced Labs; precision guide matches actual policies and supported square-size control; glossary expanded. |
| GPU Performance Optimization | 16 | 14 | Supplied shape/dtype survey distinguished from optional padding/cropping extension. |
| LLM Training | 17 | 16 | GQA, operator-level recomputation and sharding examples identified as conceptual extensions; objective lab no longer promises unavailable timing/policy outputs; glossary expanded. |
| LLM Inference | 17 | 22 | Capstone instruction corrected and taught abbreviations collected in the glossary. |
| Custom CUDA Kernels | 16 | 13 | Objectives and capstone claims match supplied grouping/kernel trials; application integration remains separate; glossary expanded. |
| Advanced Labs | 0 | 34 | Catalog prerequisites match the owning overview; glossary gains communication, precision and parallelism terminology. |

### Confirmed gaps and repairs

The shared setup discovery and private-directory preparation recognized text-only
and reference-only metadata but treated lessons-only metadata as practical.
Adding the PyTorch course therefore caused discovery to request a nonexistent
runtime manifest, and preparation to reject its intentionally absent lab list.
Both canonical owners now exclude all three reading profiles before practical
inventory processing. All six standalone copies are synchronized. Unknown and
practical profiles still fail on a missing inventory; no compatibility path or
fallback was added.

Six isolated reading-profile regressions established the negative control:
the two lessons-only cases failed before the fix while the existing four profile
cases passed. All six pass afterward. The actual catalog still resolves exactly
six practical packages, 110 labs and 288 launchers. All five preparation groups
exclude the reading course. Tests that assumed every course except the two older
reading packages needed downloads or Practice now use the declared profiles and
practical inventory. README and active requirements/design describe the same
boundary.

The content fixes preserve learner capabilities while distinguishing supplied
experiments from separately implemented extensions. Five glossaries gained 55
lookup entries and clarified existing expansions; splitting one combined Advanced
parallelism entry gives a net increase of 54 keys. Each glossary remains one
alphabetical course appendix. The PyTorch overview retains every sentence while
matching the shared three-part orientation pattern.

### Verification and review

Source/static: the fresh full offline suite passes **2,503 tests** with no skips or failures. One existing environment warning reports unavailable NumPy. After the final prose-only overview regroup, 193 focused publication/profile tests also pass. All nine native course validators, complete
HTML/ZIP freshness, helper parity, changed Python syntax/Ruff, configured Markdown
lint and whitespace checks pass. The five focused preparation/profile test files
pass 274 cases. Independent review additionally passes the six new regressions
and 24 private-path/error cases. No serious code-review or security finding remains;
the duplicate test import found during review was removed. No performance or
architectural rewrite was warranted.

The initial exploratory full run reported 18 failures and 2,480 passes. Those
failures exposed the two missing runtime exclusions and stale reading-profile
test assumptions; the fresh post-repair run supersedes it. No checker was relaxed
to invent labs, downloads or Practice for the reading course.

Browser/visual: 33 isolated Playwright Test 1.57.0 cases passed for all eleven
pages at 1440×1000, 390×1000 and 320×1000 using owned headless Chrome
154.0.8037.98, with page JavaScript disabled. The final guide wording received
three additional passing cases; the final PyTorch overview likewise received three passing cases. Checks cover exact resource
order, destinations/current identity, fragments, keyboard menus, applicable local
scrollers, no page-width overflow, doubled-text reflow and no automatic HTTP
requests. PyTorch's sixteen unchanged figures retain their font-fit assertions.
Visual inspection covered all five changed glossaries at phone width and
representative desktop, course-entry, catalog, guide and enlarged-text views.
Owned contexts and browser processes closed after each run.

The generic skill checker ran unchanged on all nine HTML courses using exact
source and companion-link manifests in an isolated publication fixture. It
retains five diagnostics per standard conceptual course, six for Soperator and
PyTorch, and seven for Tools and Advanced Labs. These are the documented shared
favicon/footer, resulting structure/link diagnostics and explicit profile/CSS
differences. This checker is not recorded as passing; profile-aware native
validation and source parity remain separate evidence.

Preservation: all 3,387 inventoried lab implementations, launchers, diagram assets,
result files and archives retain their task-start bytes. No baseline file was
removed. The intentional runtime-source change is limited to two canonical shared
helpers and their twelve standalone copies. Existing dirty work was preserved;
there was no Git staging, commit, push, dependency installation or publication.

### Evidence and limits

Local evidence group `courses-align-u__m62w_` contains baseline hashes, task diffs,
`pytest-final.log`, `regressions-after.log`, native/build/lint logs,
`checker-results.json`, `preservation.json`, the browser harnesses and JSON results.
`browser/`, `browser-guide-final/` and `browser-pytorch-final/` contain screenshots,
actual browser identity and HTML hashes. Passing runs produced no failure traces;
the configured trace policy retains traces only on failure. The table below binds
the final generated artifacts. Historical entries below retain their original
scope, counts and hashes.

Installed-environment, runtime-activation and live-target qualification were not
refreshed. The setup repair is established by source/fixture tests and read-only
plans, not an actual package installation or Slurm/GPU run. The existing dense
legacy-diagram readability limitation at 320px remains; page containment does not
prove every diagram label is comfortably readable. These limits and the generic
checker diagnostics prevent an unrestricted publication-readiness claim.

Context came from the current registry, canonical sources/specs, existing dirty
diff, relevant prior review notes and scoped memory. Older references to a GPU
Performance Engineering package were superseded by the current nine-package
inventory; that absent package was not restored. Skills used: create-learning-course,
global-context-management, maintain-project-specs and align, including nested
report-only code-review, linter and apply-security. All bounded read-only helpers
finished; this host exposes no agent-close control. Reusable skill sources needed
no change.

Terminology checks used current primary sources: [PyTorch matmul precision](https://docs.pytorch.org/docs/2.14/generated/torch.set_float32_matmul_precision.html),
[NVIDIA Compute Sanitizer](https://docs.nvidia.com/compute-sanitizer/ComputeSanitizer/index.html),
[CUDA Core Compute Libraries](https://nvidia.github.io/cccl/),
[NVIDIA RoCE guidance](https://docs.nvidia.com/networking-ethernet-software/cumulus-linux/Layer-1-and-Switch-Ports/Quality-of-Service/RDMA-over-Converged-Ethernet-RoCE/),
[NVIDIA SHARP](https://docs.nvidia.com/networking/display/sharpv3110/Using%2BNVIDIA%2BSHARP%2Bwith%2BNVIDIA%2BNCCL)
and [UCX](https://openucx.github.io/ucx/index).

| Final artifact | SHA-256 |
| --- | --- |
| `index.html` | `29d2301dc08803c204a10dacb6a3c9e4c6ce0485fa7c879aa5f8c6e41dc13d1c` |
| `lab-guide.html` | `1e536d93d4503059645c93c64f5fe21f0320a95329271a0523da8c22404f6725` |
| `soperator/index.html` | `1b48d60f78544c8eb51d3f50022edea2dd7564013676e7bc5ca5035f5972b96f` |
| `gpu-performance-tools/index.html` | `68a02c5f4476df6fd32daadd7524c198f4427a59bd46e674dcef5bac9f6b119f` |
| `pytorch-gpu-performance-engineering/index.html` | `bb7fa40b14a62c425a216c9301acf254e57487c7c5bfa8c22c58271ab162a373` |
| `gpu-fundamentals/index.html` | `a04cf29283efd699818af8c9d008e3faa6d72ddd0f4250ffb09a5ede65d5dcda` |
| `gpu-optimizations/index.html` | `3cfadd6218d236c0303f4f044ef9928a583f709f000c473dd8156f94ce82d8ea` |
| `llm-training/index.html` | `07641ac6b767b551029c26855bac0b4d9d8c2d5cd717a603f75adee92e67e44f` |
| `llm-inference/index.html` | `d5250b62764ccec93cb069be33e55ce451234727108653e171cb03382d91dbe5` |
| `custom-cuda-kernels/index.html` | `0b3950e0bc6a4a4025b92b487a3bcba2abed3c2f6b4edc73ef4709b93b750cc2` |
| `advanced-gpu-communication/index.html` | `2e9713272ea6b862b07f74beac12d3d59f22a7e10f433756e5df6ba545df7876` |

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
