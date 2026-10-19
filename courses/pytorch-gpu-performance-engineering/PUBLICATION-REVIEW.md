# Publication review

## Unified lab preparation and TOC alignment — 2026-10-15

Reviewed HTML SHA-256: `8847894bb5001bec6883a6649a9fa7a7d7e367d1ef9e871fe89c2968d92fb8ce`.
Setup-related prose and navigation align with the single shared preparation
command, its completion summary and separate hardware/workload qualification.
The course's profile, TOC identities, order and lab boundaries are preserved.
All nine native validators and 507 focused tests pass. Browser checks cover this
artifact at 1440px, 390px and 320px, including every TOC destination, keyboard
controls, local scrolling and 200% reflow. See the [catalog preparation review](../docs/catalog-preparation-validation.md#unified-lab-preparation-and-toc-alignment--2026-10-15)
for exact scope, generic-checker findings, browser evidence and limitations.
No installation, live workload or external publication occurred. Earlier entries
remain historical evidence for their own artifacts.

## Catalog correctness and navigation — 2026-10-13

Current HTML SHA-256: `0b489d1a0ee84a721ece109f266714dd294d7b3623a7a44b530ec0c06d19a38a`.
The full-catalog source/build checks and three-width browser review pass for
this artifact. Native instant navigation resolves the reproduced scroll race;
the shared navigation regression passes twice across all ten course/guide pages.
See the [catalog review](../docs/course-format-validation.md#catalog-correctness-and-navigation--2026-10-13)
for the 845-test result, 33 full-page and 60 repeated navigation cases, causal
controls, screenshots, cleanup, preservation and remaining limits. Dense phone
diagrams and applicable installed/runtime/live-target qualification remain
separate; no external publication was performed. Earlier entries are historical.

## Complete catalog review and alignment — 2026-10-13

Reviewed `index.html` SHA-256: `2ade22b24a401a65cf5678e50921f25c0b12b6450654bf75008e1d3171054ebb`.

Source, syllabus, glossary, compact-guide directions and navigation were reviewed
under this course’s declared profile. Native validators and source/build parity
pass. The complete 748-test scope passes with four documented skips; browser
checks cover this final artifact at desktop, 390px and 320px widths.

See the [complete catalog review](../docs/course-format-validation.md#complete-catalog-review-and-alignment--2026-10-13)
for changes, final artifact hashes, scoped reruns, visual findings, generic-checker
diagnostics and harness history. Dense phone diagrams and applicable live-target
qualification remain explicit limitations. No installation, live lab execution
or external publication occurred. Earlier records retain their own artifact scope.

## Course guide cleanup — 2026-10-13

Reviewed `index.html` SHA-256: `1bf46ef86d5332870c94ca32ff01777d7f32ec890724706b5ee3658a0a62ef3d`.

Reviewed and simplified README orientation. At guide-cleanup verification,
canonical lessons and the rendered page were byte-identical to the task-start
artifact; no practical guides or runtime requirements were added. Native
validation and focused profile tests pass. This README-only change does not
replace the prior HTML browser evidence.

See the [guide review record](../docs/course-format-validation.md#course-guide-cleanup--2026-10-13)
for artifact identities, preservation, browser evidence and cleanup, initial
harness failures, generic-checker limitations and validation scope. No live lab
execution or external publication was performed. Earlier records apply to their
own artifact identities. Later concurrent styling changes are outside this
review; its evidence applies to the reviewed digest above.

## Links that preserve the current page — 2026-10-13

Reviewed `index.html` SHA-256: `1bf46ef86d5332870c94ca32ff01777d7f32ec890724706b5ee3658a0a62ef3d`.

Links to other pages now open new tabs while section links and results downloads
retain native behavior. Source/static checks and 33 whole-catalog browser cases
pass at desktop, 390px and 320px widths, including pointer/keyboard activation,
original reading-position preservation and opener isolation. Course content,
styles, profiles and archive bytes are preserved. See the
[navigation verification record](../docs/catalog-navigation-validation.md#links-that-preserve-the-current-page--2026-10-13)
for exact tools, local traces/screenshots, cleanup, generic-checker limits and
prior harness failures. No live lab qualification or external publication was
performed. Earlier entries apply to their own reviewed artifact identities.

## Nine-course text and format alignment — 2026-10-12

Reviewed `index.html` SHA-256: `b822af049a6b0d246df70ccb3528dc6c955c44fcd46e13b589df68a1848e2d62`.

The current all-course review preserves this course's declared profile and aligns
terminology, glossary formatting and coverage, teaching claims and navigation.
The shared build, 480 focused tests, all nine native validators, scoped lint and
independent read-only code/security review pass. All eleven pages pass 33 final
headless Chrome cases at desktop, 390px and 320px widths. See the
[current alignment evidence](../docs/course-format-validation.md#nine-course-text-and-format-alignment--2026-10-12)
for exact scope, artifacts and preserved sources. Generic-checker diagnostics,
older dense diagram labels on narrow screens and unrefreshed live/GPU
qualification remain explicit limits. Earlier entries apply to their own hashes.

## Final lesson: torch.compile and kernel fusion — 2026-10-12

Reviewed `index.html` SHA-256:
`a9f4bb37a442ee271bb724ec2270950048bafd6d12f9d04222b6cc6581030778`.

Replace the former lessons 16–18 with one standalone lesson 16, torch.compile
can combine GPU operations. Define compile, eager execution, kernels and fusion
before tracing multiply-by-two and add-three through two kernels or one possible
fused kernel. Explain the intermediate tensor's global-memory traffic and why
one Python line alone does not cause fusion. The complete commented CUDA example
compares eager and compiled outputs with allclose. Distinguish output agreement
from proof of fusion or speedup, and compilation cost from repeated execution.

The course now has 16 lessons and 21 registered diagrams. Lessons 1–15 remain
byte-identical; entry/readiness text, syllabus, mission, glossary, references,
metadata, count regression, builder documentation and generated page align.
Remove the two orphan diagrams; profiler details remain in GPU Performance Tools.
The lessons-only profile and approximate three-hour reading estimate remain.

| Lane | Status | Evidence and limits |
| --- | --- | --- |
| Source/static | Passed | 239 focused reading, figure, source-integrity, format and catalog tests; all three native reading validators; selected build/freshness, Markdown lint, Ruff and whitespace. All 24 Python snippets parse; the retained 15 lessons and shared catalog/guide bytes are unchanged. All 21 diagram files have exactly one manifest entry. |
| Semantic/security | Passed | Current official PyTorch compile, allclose, tuning and compiler-tutorial references support the explanation. Independent read-only code/security review found no actionable findings. Synthetic examples and passive SVGs introduce no private data or active resources. |
| Existing environment / CPU | Passed, bounded | Existing PyTorch 2.14.1 runs the exact function body on CPU. Seven assertions verify values, shape, unchanged input, distinct output storage and tolerance-check behavior. This is an arithmetic check, not execution of the complete CUDA example or compiled code. No installation. |
| Browser/visual | Passed | Three isolated headless Chrome 155.0.8059.39 cases at 1440px, 390px and 320px check the 16-lesson route, removal of 17/18 anchors, keyboard navigation, code scrolling, normal/doubled-text reflow and diagram text size, bounds and collisions. Desktop/mobile diagram and full desktop lesson captures were visually inspected. |
| CUDA / compiler / performance | Pending | Local CUDA availability is false. No CUDA example, compilation, actual kernel fusion or speedup was measured. Source, CPU and browser checks do not establish those behaviors. |
| Live target/publication | Not applicable | No deployment or external publication requested or performed. |

Evidence group `pytorch-compile-nb6yfcmt` holds the baseline snapshot, preservation
checks, CPU assertions, browser harness/results, geometry and screenshots.
Browser evidence matches the final HTML digest. An initial sandboxed Chrome
launch failed before page checks; that evidence is retained separately. The
same isolated checks then passed with approved host launch access. Playwright
Test owns and closes its browser contexts. The generic authoring checker has
exactly the same six baseline standard-profile/shared-shell diagnostics;
native lessons-only validation remains authoritative.

## Lesson 15 GPU timing and warm-up — 2026-10-12

Reviewed `index.html` SHA-256:
`0fcead560c194eabe2c8b6864f15c7231e059e8774b44119af3714830084829a`.

Retitle the lesson to Measure GPU time with CUDA events. Define asynchronous
CPU submission and ordered CUDA streams before a four-row function table.
Distinguish creating an event, recording a GPU timestamp, waiting for device
completion and reading elapsed milliseconds. Explain representative warm-up
and conditional initialization, allocation, preparation and clock effects.
One commented CUDA example warms up a 4096-square matrix multiplication,
finishes warm-up, then times one product. The two-lane CPU/GPU diagram shows
the timed sequence and marks the interval between GPU timestamps.

Preserve the narrower event-wait option, measurement exclusions, possible GPU
idle gaps, repeated measurements, correctness checks and total-latency context.
Five warm-ups are illustrative, not a stable-clock or steady-state guarantee.
Align title metadata, glossary, references, manifest, mission, syllabus, README
and generated HTML while preserving the lessons-only profile.

| Lane | Status | Evidence and limits |
| --- | --- | --- |
| Source/static | Passed | 148 focused reading, renderer and figure tests; all three native reading validators; selected build/freshness, Markdown lint and whitespace. All 25 Python snippets parse; the other 17 lessons, 24 Python ASTs and shared catalog/guide bytes are unchanged. Six static code-structure checks confirm timing flags, warm-up placement, ordered markers/waits and one measured matmul. |
| Semantic/security | Passed | Official PyTorch Event, synchronize, CUDA-semantics and benchmarking references plus NVIDIA matrix-measurement guidance support the explanation. Independent read-only code/security review found no actionable findings. The synthetic example and passive SVG add no private data or active resources. |
| Existing environment | Inspected | Existing PyTorch 2.14.1 reports CUDA unavailable. No dependency installation. |
| Browser/visual | Passed | Three isolated headless Chrome 155.0.8059.39 cases at 1440px, 390px and 320px check navigation, keyboard controls, table/code scrolling, normal/doubled-text reflow and diagram label size, bounds and collisions. Final desktop/mobile timeline, function table and full lesson captures were visually inspected. |
| CUDA / timing | Pending | The CUDA example was not executed. Static ordering, official documentation and browser checks do not establish actual GPU timing, synchronization, clock stability or performance. No measured duration or speedup is claimed. |
| Live target/publication | Not applicable | No installation, deployment or external publication requested or performed. |

Evidence group `pytorch-timing-8clpclki` holds the source snapshot, static
preservation and boundary checks, runtime availability probe, browser
harness/results, geometry and screenshots. Browser evidence matches the final
HTML digest. Playwright Test owns and closes its isolated browser contexts.
The generic authoring checker retains exactly the same six baseline
standard-profile/shared-shell diagnostics; native lessons-only validation
is authoritative.

## Lesson 14 batching and vectorization — 2026-10-12

Reviewed `index.html` SHA-256:
`60d0bc1ac568c9ee7a7c63117b5edda11542917b097bb959b5938277764cc135`.

Separate forming a batch from computing on it. Explain that x * 2 + 1 applies
PyTorch element-wise operations to an existing tensor, and show explicit input
grouping with torch.stack(). Pair the sample loop and whole-batch computation
with two original diagrams and two complete commented examples. Both routes
produce the same three output rows; the loop groups only its finished outputs.
Connect DataLoader batch_size to ordinary training and retain independence,
device, launch-count, throughput, latency and memory qualifications.

A bounded Markdown comparison container retains canonical headings, prose,
registered figures and fenced code. Two columns become sequential panels on
narrow screens. Shared CSS is regenerated into dependent publications; their
non-style HTML is unchanged. Align lesson identity, metadata, glossary, official
references, mission, syllabus and authoring instructions.

| Lane | Status | Evidence and limits |
| --- | --- | --- |
| Source/static | Passed | 273 focused/shared-renderer tests, all nine native course validators, full build/freshness, Ruff, Markdown lint and whitespace. Twelve new comparison regressions failed before implementation and pass afterward, covering malformed boundaries, nesting, fence literals, escaping and figure forwarding. All 25 snippets parse; the other 17 lessons and 23 Python ASTs are unchanged. Ten dependent page bodies are identical after excluding embedded styles. |
| Semantic/security | Passed | Current official PyTorch stack, mul, add and DataLoader references support the explanation. Organization-specific sources were irrelevant to upstream tensor behavior; local course context supplied the teaching constraints. Independent read-only code/security review found no actionable findings. Figures are passive SVG; the renderer retains escaping and registered-figure validation. |
| Existing environment / CPU | Passed | Existing PyTorch 2.14.1 executes both examples unchanged. Twelve assertions verify matching values and printed results, input order, shapes, unchanged inputs, independent stacked storage, CPU/FP32 placement, and DataLoader grouping with a partial final batch. No dependency installation. |
| Browser/visual | Passed | Three isolated headless Chrome 155.0.8059.39 tests at 1440px, 390px and 320px check two wide-screen columns versus stacked mobile panels, navigation, keyboard use, each code scroller, normal/doubled-text reflow, and both SVGs' bounds, label size and collisions. Desktop/mobile comparison and full desktop lesson captures were visually reviewed. |
| GPU / performance | Pending | No GPU execution, launch-count measurement, timing or speedup is claimed. CPU equality and browser checks do not establish GPU performance. |
| Live target/publication | Not applicable | No installation, deployment or external publication requested or performed. |

Evidence group `pytorch-batching-eew6nv6n` holds the source snapshot, CPU and
preservation checks, native results, browser harness/results, geometry and
screenshots. Playwright Test owns and closes its isolated browser contexts.
The initial build rejected glossary ordering; sorting by displayed terms fixed
it before browser validation. The generic authoring checker retains the same
six standard-profile/shared-shell diagnostics as the baseline; native
lessons-only validation is authoritative.

## Lesson 13 CUDA memory lifecycle — 2026-10-12

Reviewed `index.html` SHA-256:
`8f8e14d0a251471380d93c21e8799db22019431cd0a9392be69cd156d6e95ab8`.

Explain why PyTorch reserves reusable CUDA blocks, how live storage returns to
that cache, and why cached capacity is storage rather than saved answers. Replace
the manual cleanup example with a scoped matmul batch helper that keeps weights
and returns CPU predictions to a CPU consumer. Distinguish automatic allocation
and safe reuse from retained tensor references; cover training intermediates,
logging, peak memory, OOM, detach(), del and occasional empty_cache() use.
Align the original pool diagram, glossary, official references, manifest,
mission, syllabus, README and generated page.

| Lane | Status | Evidence and limits |
| --- | --- | --- |
| Source/static | Passed | 136 focused tests, all three native reading validators, selected build/freshness, scoped Markdown lint and whitespace. All 24 snippets parse; 23 other Python ASTs, 17 other lessons and shared catalog/guide bytes match the task-start snapshot. |
| Semantic/security | Passed | Current official PyTorch CUDA/FAQ/API documentation and versioned allocator/cuBLAS source support allocation, lifetime, workspace and cache guidance. Independent read-only review found no actionable finding. The original passive SVG and synthetic example introduce no private data or active external resources. |
| Existing environment / CPU | Passed with adaptation | Existing PyTorch 2.14.1 passes nine checks for batch computation, independent copied storage, local tensor lifetime, persistent weights and disabled recording. The private CPU adaptation replaces device transfers with independent CPU copies and omits CUDA memory counters; it does not execute the CUDA example unchanged. No installation. |
| Browser/visual | Passed | Three isolated headless Chrome 155.0.8059.39 cases at 1440px, 390px and 320px check navigation, keyboard controls, local code scrolling, normal/doubled-text reflow and SVG label geometry. Desktop/mobile figures and the complete desktop lesson were visually inspected. Browser evidence matches the final HTML digest. |
| CUDA / allocator | Pending | Local CUDA availability is false. No CUDA counters, cache reuse, GPU asynchronous lifetime, workspace size or performance measurement was executed. CPU and browser checks do not establish these behaviors. |
| Live target/publication | Not applicable | No installation, deployment or external publication requested or performed. |

Evidence group `pytorch-memory-t___tpnn` holds the snapshot, adapted CPU checks,
source-preservation proof, browser results, geometry and screenshots. Playwright
Test owns and closes its browser and isolated contexts. Initial glossary ordering
blocked the build; the first browser attempt consequently used stale HTML and
was stopped. Those artifacts remain separate and excluded from final evidence.
After correcting the order and rebuilding, fresh native and browser checks pass.
The generic authoring checker retains the same six standard-profile/shared-shell
diagnostics as the baseline; native lessons-only validation is authoritative.

## Lesson 9 linear-layer matrix calculation — 2026-10-11

Reviewed `index.html` SHA-256:
`8e0a2db8a12e443891ac3a5dea32101bea45d4edf88365140bea858a5b52d72c`.

Added a symbolic matrix diagram for the existing four-input, two-neuron layer.
Connect each neuron's weighted sum to y = Wx + b and row-batch X @ W.T + b,
then trace the next layer's own weights and biases. Preserve the existing
4 → 2 → 1 network and commented code. Explain ReLU separately, GPU parallel
matrix work, and vocabulary scores followed by token selection. Align the
objective, mental model, glossary, manifest, official references and authoring
descriptions without introducing practice or execution requirements.

| Lane | Status | Evidence and limits |
| --- | --- | --- |
| Source/static | Passed | 136 focused tests, all three native reading validators, selected build/freshness, scoped Markdown lint and whitespace. All 24 Python ASTs, 17 other lessons and shared catalog/guide bytes match the task-start snapshot. |
| Semantic/security | Passed | Official PyTorch Linear/ReLU/Sequential and text-decoder references plus NVIDIA matrix guidance support the explanation. Independent read-only review found one storage-cost wording issue, corrected by distinguishing more input vectors from more input features. The new diagram is original passive SVG with no active resources or private data. |
| Existing environment / CPU | Passed | Existing PyTorch 2.14.1 runs the exact unchanged lesson example. Thirteen assertions verify shapes, separate parameters, scalar weighted sums, column/row matrix equivalence, six-vector reuse, ReLU, final output and rejection of incompatible input features. No dependency installation. |
| Browser/visual | Passed | Three isolated headless Chrome 155.0.8059.39 tests at 1440px, 390px and 320px check lesson navigation, keyboard controls, local code scrolling, normal/doubled-text reflow and both diagrams' bounds, font sizes and label collisions. Desktop/mobile figures and the complete desktop lesson were visually inspected. |
| GPU / compiler | Pending | No GPU execution, compiler qualification or performance measurement; CPU numerical checks do not establish GPU behavior or speedups. |
| Live target/publication | Not applicable | No installation, deployment or external publication requested or performed. |

Evidence group `pytorch-linear-2u11b03z` holds the snapshot, CPU checks,
source-preservation proof, browser results, geometry and screenshots. Playwright
Test owns and closes its browser and isolated contexts. The first native check
rejected newly added reference domains; the final references use verified
PyTorch-owned documentation, and native validation and all 136 tests pass.
The generic authoring checker retains the same six standard-profile/shared-shell
diagnostics as the baseline; native lessons-only validation is authoritative.

## Lesson 11 dropout and prediction wording — 2026-10-11

Reviewed `index.html` SHA-256:
`f40cb7a127e8913dcc901a06c679e39f22a9af1383c6fb596e7d2160c7d97aed`.

Added a compact train()/eval() dropout comparison and explained why ordinary
inference keeps dropout inactive. The eval() comments now describe preparing
layers for validation or prediction; inference_mode() separately controls
recording for backpropagation. Executable statements and diagrams are preserved.

| Lane | Status | Evidence and limits |
| --- | --- | --- |
| Source/static | Passed | 136 focused tests; all three native reading validators; selected build/freshness, scoped Markdown lint and whitespace. All 24 examples retain identical Python ASTs; the other 17 lessons and shared catalog/guide are byte-identical to the follow-up snapshot. |
| Semantic/security | Passed | Focused review agrees with the official PyTorch mode/dropout references already checked for this lesson. No executable behavior, external requests or data handling changes. |
| Browser/visual | Passed | Three isolated headless Chrome 155.0.8059.39 cases at 1440px, 390px and 320px check table text/API emphasis, keyboard scrolling, navigation, normal/doubled-text reflow and existing diagram geometry. Desktop and mobile table captures were visually inspected. |
| CPU / GPU | Not rerun | Python ASTs are unchanged from the preceding CPU-verified examples. No new CPU run, CUDA/compiler execution or performance measurement is claimed. |
| Live target/publication | Not applicable | No installation, deployment or external publication requested or performed. |

Evidence group `pytorch-dropout-rd0hrag_` holds before/after checks, browser
results, geometry, screenshots and artifact identity. Playwright Test closes
its isolated browser contexts. The generic checker retains the same six
baseline standard-profile/shared-shell diagnostics; native lessons-only
validators pass.

## Lesson 11 training, validation and inference — 2026-10-11

Reviewed `index.html` SHA-256:
`1349bdabb0f938bbf7928732d17b8ddca32495050ec39376ffb6665a6b7a2cfd`.

Split lesson 11 into training followed by validation after each epoch, then
inference on new inputs, each with its own workflow diagram and commented code.
Define known targets, dropout and gradient recording; distinguish validation
from final testing and layer behavior from backward recording. Explain that
both validation and inference use eval() with inference_mode(). Rename the
lesson consistently and align glossary, references, manifest and README.

| Lane | Status | Evidence and limits |
| --- | --- | --- |
| Source/static | Passed | 136 focused reading, renderer and figure regressions; all three native reading validators; selected build/freshness; scoped Markdown lint and whitespace checks. All 24 Python snippets parse. The other 17 lessons and shared catalog/guide bytes match the task-start snapshot. |
| Semantic/security | Passed | Current official PyTorch documentation supports the modes, dropout and target/loss semantics. Independent read-only review found no actionable issue. Synthetic CPU fixtures and passive SVGs introduce no private data or automatic external requests. |
| Existing environment / CPU | Passed | Existing PyTorch 2.14.1 executes the exact two examples. Two epochs perform four parameter updates and two validation passes; validation/inference preserve parameters, inference preserves existing gradients, and checks distinguish eval behavior, dropout and gradient recording. Prepared fixtures are verification context, not a course setup requirement. |
| Browser/visual | Passed | Three isolated headless Chrome 155.0.8059.39 tests at 1440px, 390px and 320px cover lesson navigation, keyboard controls, code scrolling, normal/doubled-text reflow and both SVGs' bounds, label sizes and collisions. Desktop/320px figures and the full desktop lesson were visually inspected. |
| GPU / compiler | Pending | No CUDA/compiler execution or GPU performance measurement; CPU correctness does not establish GPU behavior or speedups. |
| Live target/publication | Not applicable | No live target, installation, deployment or external publication requested or performed. |

Evidence group `pytorch-lesson11-0yzj9l0i` contains the task-start snapshot,
CPU verification, browser harness/results, geometry, screenshots and exact
artifact identity. Playwright Test owns and closes its browser and isolated
contexts. Trial 1 timed out in element-screenshot stability and was stopped;
its evidence remains separate. The final harness uses page clipping and
explicit browser background settings, with all three cases passing. No course
change was made to satisfy the screenshot timeout.

The generic authoring checker retains the same six baseline standard-profile
and shared-shell diagnostics. The native lessons-only validators pass; the
generic checker and course profile are unchanged.

## Lesson 9 network and tensor shapes — 2026-10-09

Reviewed `index.html` SHA-256:
`390cf4994da6cba4c864f70f3ae727b68d974030cf4904eb22c3ffaf3c0a4b13`.

Paired a four-input, two-hidden-neuron, one-output network on the left with its
tensor-shape flow on the right. Explained input features, neurons, hidden versus
output layers and shared weights. Added the matching final linear layer to the
commented example, producing shape `[2, 3, 1]`. Glossary, manifest, syllabus,
README and generated page agree; other lessons retain their preceding content.

| Lane | Status | Evidence and limits |
| --- | --- | --- |
| Source/static | Passed | 77 existing focused tests, all three native reading validators, selected build/freshness, Markdown lint and whitespace. All 23 snippets parse; 22 remain byte-identical. The lesson-9 example alone changes. The other 17 lessons and shared catalog/guide are byte-identical to the preceding artifact. |
| Semantic/security | Passed | Official PyTorch 2.14 Linear documentation confirms feature-axis behavior and parameter shapes. Independent read-only review found no actionable issue. Changed-scope review found no concrete public-safety issue; the example uses synthetic values and the diagram remains passive SVG. |
| Existing local environment / CPU | Passed | Existing PyTorch 2.14.1 executes the exact example and passes 12 checks covering shapes, parameters, ReLU, numerical outputs, shared weights and rejection of an incompatible feature size. No new environment was installed. |
| Browser/visual | Passed | Three isolated headless Chrome 155.0.8059.39 checks at 1440px, 390px and 320px cover navigation, keyboard controls, lesson-9 code scrolling, reflow, bold APIs, SVG bounds/font sizes and changed-diagram label collisions. Desktop and 320px diagram captures and the complete desktop lesson were visually inspected. |
| GPU / compiler | Pending | No CUDA/compiler execution or performance measurement. CPU correctness and browser checks do not establish GPU behavior or speedups. |
| Live target/publication | Not applicable | No live target, deployment or external publication was requested or performed. |

Evidence group `pytorch-network-hxt6bj2d` contains source comparisons, CPU
checks, Playwright harness/results, captures, geometry and artifact identity.
Playwright Test owned and closed the browser and isolated contexts; no automatic
external requests or page errors occurred. Generic authoring-checker findings
match the six existing lessons-only/shared-shell diagnostics; native reading
validation passes.

## Lesson 8 device placement and scalar readback — 2026-10-09

Reviewed `index.html` SHA-256:
`876793f3c17854bf1c9767e802d92f04defd87057c6b5bfca24afc3668c193c9`.

Replaced lesson 8's diagram with CPU/GPU memory columns tracing the input copy,
GPU multiplication and sum, scalar readback and printing. Separated extraction
from printing in the example and explained that the original scalar tensor stays
on the GPU. Added concise CPU-wait versus stream-dependency teaching and four
performance aims, preserving required dependencies and qualifying overlap.
Glossary, manifest, syllabus, README, references and generated page agree.

| Lane | Status | Evidence and limits |
| --- | --- | --- |
| Source/static | Passed | 77 existing focused tests, all three native reading validators, selected build/freshness, Markdown lint and whitespace. All 23 snippets parse; 22 are byte-identical. The sole code change splits scalar extraction from printing. Every other lesson and the shared catalog/guide are byte-identical to the preceding artifact. |
| Semantic/security | Passed | Official PyTorch 2.14 item(), synchronize() and Stream.wait_stream() references support the call semantics; the performance tuning guide supports the synchronization explanation. Independent read-only review found no actionable issue. Changed-scope review found no concrete public-safety issue. |
| Browser/visual | Passed | Three final isolated headless Chrome 155.0.8059.39 checks at 1440px, 390px and 320px cover navigation, keyboard controls, lesson-8 code scrolling, reflow, bold APIs, SVG bounds/font sizes and changed-diagram label collisions. Desktop and 320px diagram captures were visually inspected; the complete lesson was also reviewed visually. |
| Existing environment / CUDA / compiler | Pending | Reused existing authoring tools without installation. The CUDA example was syntax-checked, not executed; no runtime, concurrent execution or performance qualification is claimed. |
| Live target/publication | Not applicable | No live target, deployment or external publication was requested or performed. |

Evidence group `pytorch-device-s5ljp2r1` contains source comparisons, the
Playwright harness/results, captures, geometry and artifact identity. Initial
passing browser checks preceded a visual spacing correction to the return-arrow
label; those captures are retained separately from the final passing run.
Playwright Test owned and closed the browser and isolated contexts; no automatic
external requests or page errors occurred. Generic authoring-checker findings
match the six existing lessons-only/shared-shell diagnostics; native reading
validation passes.

## Lesson 7 mental-model wording — 2026-10-09

Reviewed `index.html` SHA-256:
`96210ffcabeb9d763fb2d19f1e74e38d2ee318dd75499d2a47b661e36bd50380`.

Rephrased the mental model around choosing a dtype that preserves required
range and precision, then checking accuracy and measuring the full calculation.
The selected page was rebuilt; all three native reading validators, page
freshness, Markdown lint and whitespace checks pass. This wording-only change
adds no performance claim or executable example. Browser and execution checks
were not rerun; their preceding evidence remains tied to the artifact below.

## Lesson 7 numeric-format refinement — 2026-10-09

Reviewed `index.html` SHA-256:
`49a2cff70fcb3fbb30670f0c106a4a4cdd36aaa060973ad07839e2280bce0501`.

Integrated the seven requested formats into lesson 7 with sign/exponent/fraction
definitions, maximum finite values, explicit E4M3FN naming, packed FP4 storage,
a short scaling example and conditional GPU-performance implications. Retained
the byte-count diagram and all 23 code examples byte for byte. Other lessons
are unchanged from the preceding GPU-cost revision.

| Lane | Status | Evidence and limits |
| --- | --- | --- |
| Source/static | Passed | 77 focused tests, all three native reading validators, selected build/freshness, Markdown lint and whitespace checks. All 23 unchanged snippets parse. Shared guide/catalog bytes are unchanged. |
| Semantic/security | Passed | Official PyTorch 2.14 dtype documentation, NVIDIA floating-point/Transformer Engine documentation and OCP MX encoding tables support the comparison. Independent read-only review found no actionable issue. Changed-scope public-safety review found no concrete issue. |
| Existing local environment / CPU | Passed | Existing PyTorch 2.14.1 executes lesson 7 with five independent byte-count, dtype, value and rounding checks. Six maxima and widths match torch.finfo; FP4's maximum matches the OCP E2M1 encoding. All seven sign/exponent/fraction sums agree. No new environment was installed. |
| Browser/visual | Passed | Three final isolated headless Chrome 155.0.8059.39 checks at 1440px, 390px and 320px cover navigation, reflow, code/table keyboard scrolling, new dtype API emphasis and all SVG label bounds. Lesson 7 and the narrow-screen maximum-value column were visually inspected. |
| GPU / FP8-FP4 arithmetic / compiler | Pending | No GPU execution, low-precision arithmetic qualification or performance measurements. Existing CUDA/compiler snippets remain unexecuted. Format metadata and CPU checks establish no hardware speedup. |
| Live target/publication | Not applicable | No live operation, installation, deployment or external publication requested or performed. |

Evidence group `pytorch-formats-zey98o4h` contains the prior artifact, unchanged
snippet report, numeric checks, browser harness/results, screenshots, geometry
and final artifact identity. Playwright Test owned and closed its browser and
isolated contexts; no automatic external requests or page errors occurred.

Initial reference-domain validation failures were resolved by using the
supported official NVIDIA documentation URLs; validator policy is unchanged.
The numeric verification uses decimal arithmetic because the rounded FP64
maximum itself exceeds Python's largest finite binary float. Final checks
pass. Generic authoring-checker diagnostics remain identical to the six
existing lessons-only/shared-shell baseline findings.

## GPU-cost clarity revision — 2026-10-09

Reviewed `index.html` SHA-256:
`e302d66dc8a3aba0879221301136ef0c0d91e99880cbef3b0f1fc6cf564845dc`.

Reviewed all eighteen lessons, API notation and supporting sources. Each
performance connection now explains the relevant data movement, arithmetic,
launch, transfer or waiting cost. Lesson 1 separates memory capacity from
bandwidth; lesson 4 defines memory-bound and compute-bound work. Lesson 6
compares layout properties and APIs by meaning and copy behavior, explains
actual strides and copy order, and weighs copy cost against later access.
Exact API names, including stride(), use bold monospace in prose and figures;
general concepts and example expressions retain their separate notation.
Preserved all twenty diagrams, lesson identities and commented examples.

| Lane | Status | Evidence and limits |
| --- | --- | --- |
| Source/static | Passed | 77 existing focused reading, source-integrity and shared-format tests; all three native reading validators; selected build/freshness, configured Markdown lint and whitespace checks. All 23 snippets retain identical executable ASTs. Shared catalog and guide bytes are unchanged. |
| Semantic/security review | Passed | Read every lesson and diagram label; checked storage and performance claims against official PyTorch and NVIDIA documentation. Independent review found an undefined first use of kernel; moved the definition to lesson 6 and re-reviewed. No remaining concrete correctness or changed-scope public-safety finding. |
| Existing local environment / CPU execution | Passed | Reused existing PyTorch 2.14.1; all 17 CPU blocks pass 37 independent checks covering values, shapes, aliasing, copies, rounding, gradients and batching. No dependency installation or clean-environment qualification. |
| CUDA/compiler execution | Pending | Five CUDA snippets and the compiler continuation remain unexecuted; local CUDA is unavailable. Source identity and CPU checks do not establish GPU/compiler behavior or speedups. |
| Browser/visual | Passed | Three final isolated headless Chrome 155.0.8059.39 checks at 1440px, 390px and 320px pass navigation, keyboard controls, code/table scrolling, doubled-text reflow, computed API emphasis and all SVG text bounds. Representative lesson, stride-figure and table captures were visually inspected. |
| Live target/publication | Not applicable | No cluster operation, live measurement, deployment or external publication was requested or performed. |

Local evidence group `pytorch-performance-xo4urgej` contains the unchanged-AST
report, CPU harness/results, browser test source/results, artifact identity,
figure/lesson screenshots and SVG geometry. The final browser run includes
lesson 6 table keyboard-scroll assertions and checks stride(), transpose(),
contiguous(), reshape() and view() emphasis. Page JavaScript was disabled; no
automatic external requests or page errors occurred. Playwright Test owned and
closed the browser and isolated contexts.

An initial test overlapped the final rebuild and observed stale HTML; rerunning
after the completed build passed all 77 tests. Generic-checker input paths were
resolved to symlink-free temporary copies; before/after results match the six
existing standard-profile/shared-shell diagnostics. Those diagnostics remain
separate from native lessons-only acceptance. The final browser evidence
supersedes the earlier passing run before the first-use definition correction.
GPU/compiler qualification remains pending; no measured performance claim is
made.

## Plain-English snippet revision — 2026-10-08

Reviewed `index.html` SHA-256:
`2b9e8345029ff694ba9581110675a918651f231089dd12ea5961de0b611fa58b`.

Reviewed all 23 Python snippets across all eighteen lessons. Each executable
line now has a plain-English action comment; output-only annotations are
replaced. Removed duplicated walkthrough prose, including the final step's
repeat table. Kept definitions, diagram explanations and distinct numerical,
storage, dependency and timing constraints. The glossary includes direct
entries for dim, mean(), keepdim=True and broadcasting, plus reduction cost.
All twenty diagrams and the lessons-only profile are preserved.

| Lane | Status | Evidence and limits |
| --- | --- | --- |
| Source/static | Passed | 77 existing focused reading, source-integrity and format tests; three native reading validators; build/freshness; Markdown lint with repository configuration and whitespace checks. All 23 snippets parse and have identical executable ASTs to the prior source. Every code line has an adjacent action comment. |
| Semantic | Passed | Full snippet/prose review and independent read-only review. Corrected one ambiguous numel comment to explicitly describe the number of elements. Mean, keepdim and broadcasting definitions checked against official PyTorch documentation. No remaining concrete review or changed-scope security finding. |
| CPU example execution | Passed | All 17 CPU blocks execute unchanged with existing PyTorch 2.14.1; 37 independent checks cover values, shapes, storage, rounding, gradients, inference and batching. |
| CUDA/compiler execution | Pending | Five CUDA snippets and the compiler continuation remain unexecuted; local CUDA is unavailable. AST identity establishes unchanged code, not GPU or compiler qualification. |
| Browser/visual | Passed | Three final isolated headless Chrome 155.0.8059.39 checks at 1440px, 390px and 320px pass navigation, keyboard controls, local code scrolling, doubled-text reflow, API emphasis and all SVG text bounds. Representative reduction/mobile and batching/desktop captures were visually inspected. Diagram sources are unchanged. |
| Installed environment | Not applicable | Reused existing tools and environments; installed no dependencies. |
| Live target/publication | Not applicable | Editorial work requested no cluster operation, live measurement, deployment or external publication. |

Local evidence group `pytorch-comments-b5wq5iu8` contains the snippet identity
report, CPU harness/results, browser test source/results, final screenshots,
SVG geometry and artifact identity. Playwright Test owns and closes its browser
and isolated contexts; page JavaScript was disabled. Shared guide and catalog
remain byte-identical in Git. The generic authoring checker reports the same
six standard-profile/shared-shell diagnostics before and after; native
lessons-only acceptance passes. Generic checker input-path errors during
setup were corrected using contained temporary copies before comparing results.

## Scan-focused revision — 2026-10-07

Reviewed `index.html` SHA-256:
`39c6087d653ff7451f9b3d7b0df079311cc4578f34dbcc91550aa041bfa34247`.

All eighteen lessons were reviewed and simplified around their core concepts.
Broadcasting now has one matrix-plus-bias example; reductions focus on row
means; models use built-in composition; transfers show a prepared batch and
its copy. Removed unrelated expand/repeat, softmax, mixed-precision setup,
loader collation and compiler-internals detours. The twenty diagrams remain;
five have simpler labels or examples matching the revised teaching. Glossary,
references, mission, syllabus and README align with the lessons-only profile.
Source length is about 36% lower, with 146 code lines instead of 224; these are
editorial counts, not evidence of learning outcomes. The three-hour reading
estimate remains an authoring estimate.

| Lane | Status | Evidence and limits |
| --- | --- | --- |
| Source/static | Passed | 112 focused reading, source-integrity and shared-format regressions; all three native reading validators; selected build/freshness; Markdown lint and whitespace checks. All 23 Python blocks parse. Shared guide and catalog bytes remain unchanged. |
| Semantic | Passed | Full-course review and independent read-only follow-up found no remaining concrete issue. The review's FP16 first-use definition finding was corrected. Examples, diagrams, glossary and API notation agree. Official PyTorch 2.14 documentation supports the retained semantics. |
| CPU example execution | Passed | All 17 CPU blocks executed unchanged on existing PyTorch 2.14.1; 37 independent checks cover values, shapes, shared storage, copies, rounding, gradients, inference and batched equivalence. |
| CUDA/compiler execution | Pending | Five CUDA examples and the optional compiler continuation were syntax-checked but not executed. CUDA is unavailable in the local example environment. No runtime or performance qualification is claimed. |
| Browser/visual | Passed | Three final isolated headless Chrome 154.0.8037.98 checks at 1440px, 390px and 320px; navigation, fragments, keyboard controls, local scrolling, doubled-text reflow, API emphasis, SVG text bounds and absence of automatic network requests pass. All figures were visually reviewed at phone width; changed lesson captures were inspected. |
| Installed environment | Not applicable | Existing tools were reused; this course adds no runtime environment or dependency installation. |
| Live target/publication | Not applicable | No live lab, cluster, deployment or external publication was requested or performed. |

Local evidence group `pytorch-scan-xtj08dam` contains `check_cpu.py`,
`cpu-results.json`, `pytest.log`, `native.log`, `freshness.log`, lint results,
the browser test source and `browser-results.json`. Final captures, SVG
geometry, browser identity and artifact digest are in `browser/`.
Playwright Test 1.57.0 owned and closed its headless browser and isolated
contexts, with page JavaScript disabled. Earlier passing captures are retained
separately and do not identify the final artifact.

The generic authoring checker reports the same six standard-profile and
shared-shell diagnostics on the before and after HTML, including its required
Practice section. Native lessons-only acceptance passes; neither the generic
checker nor the course's explicit profile was weakened. The alignment review
found no new secret, private reference or active-resource issue in the scoped
sources and passive assets. No skill-source or memory changes were made.

## Reading subsection hierarchy — 2026-10-07

Reviewed `index.html` SHA-256:
`75118c21ef3ed8648f079595543d04167352a352c10deb9c0c9bc789b9fd2892`.

The full offline suite exposed one heading-hierarchy regression: the diagram
rendering path added two levels to authored reading-course subsections. The
renderer now preserves their levels, matching the path without diagrams.
The regenerated page differs only in four `h6` to `h4` tag pairs; all prose,
examples, diagrams and fragment identities remain unchanged.

All 203 focused reading, source-integrity, catalog and shared-format tests pass,
including the previously failing cross-profile check. The three native reading
validators, selected publication freshness, scoped Ruff, Markdown lint and
whitespace checks pass. No browser, CUDA, Slurm or compiler execution was rerun
for this correction. Earlier reviews retain their own artifact identities.

## Indexing clarity revision — 2026-10-06

Reviewed `index.html` SHA-256:
`e850c96df4260031c83bdfca5c0c66ba16578b88fd856702ca356789978be767`.

Lesson 2 defines index and indices before use, labels each grid/row/column
choice, and explains selection, slicing and reshaping through one concrete
example. Its two diagrams show the highlighted value and two independent
selections. Definitions use affirmative explanations. Lesson 6 owns inferred
reshape sizes and axis reordering; lesson 9 owns the linear layer's feature-axis
rule. Related definitions, glossary, mission, syllabus and README align. The
reading course retains eighteen lessons and twenty diagrams, with twenty-four
Python blocks after splitting the indexing example into small steps.

| Lane | Status | Evidence and limits |
| --- | --- | --- |
| Source/static | Passed | 26 focused reading/diagram tests, native lessons-only validator, selected build/freshness, all 24 Python blocks parsed, Markdown lint and whitespace checks. Shared catalog and guide bytes match the pre-edit snapshot. |
| Semantic | Passed | Changed explanations and diagram semantics reviewed. Independent review found a moved reshape example using the preceding lesson's tensor size; the correction uses lesson 6's six-value tensor and passes a CPU check. No remaining concrete review findings. |
| Browser/visual | Passed | Three final isolated headless Chrome checks at 1440px, 390px and 320px cover navigation, keyboard controls, local scrolling, text reflow, API emphasis and SVG glyph bounds. Changed figures visually inspected on desktop and phone. |
| Example execution | Passed for changed examples | Six blocks from lessons 2, 6 and 9 pass 16 independent value, shape, storage and linear-layer checks with existing CPU PyTorch 2.9.0. This is local example evidence, separate from the referenced PyTorch 2.14 semantics. |
| Installed environment | Not applicable | Existing local tools and packages reused; no dependency or runtime installation performed. |
| Live target | Not applicable | This is a reading-course revision. CUDA and compiler examples remain unexecuted in this revision; no GPU performance or deployment claim is made. |

Local evidence group `pytorch-index-clarity-vwfacjls` contains `cpu-results.json`,
the browser harness and `browser-results.json`. The final `browser/` directory
records HTML identity, Chrome 154.0.8037.98, geometry, figure/lesson screenshots
and reflow captures. Playwright Test 1.57.0 owns and closes its headless browser
and isolated contexts, with page JavaScript disabled. `browser-trial1/` retains
the initial clipped row-label failure; `browser-trial2/` records the repaired
diagram before final editorial changes. Final browser assertions all pass.

The generic authoring checker emits the same six standard-profile/shared-shell
diagnostics for the before and after HTML. Native lessons-only acceptance
passes; the generic checker and its expectations remain unchanged. The following
reviews describe their own earlier artifact identities.

## Alignment follow-up — 2026-10-06

Reviewed `index.html` SHA-256:
`855a809171b09734548af48f4ae0e2aee73f940e32a44a681b4d6dff7124a534`.

A fresh independent review found one minor accuracy issue (PT-R1): lesson 12
called the loader's default batch a tuple. The corrected explanation distinguishes
tuple samples from the collated one-item list containing a two-by-four tensor.
The example code was already correct. Existing CPU PyTorch and the official
data-loading documentation confirm the sample type, batch type, shape, values
and unpacking. The reviewer confirmed the fix with no remaining concrete findings.

Fresh validation passed 282 focused tests, all nine native course validators,
33 browser checks across all eleven pages, lint, security review, spec validation
and publication freshness. After the prose correction, 25 course regressions,
all three reading validators, freshness and three further responsive browser
checks passed. Lesson 12's final phone rendering was visually inspected.
The review preserves the prior CPU-example evidence and unexecuted CUDA/compiler
limits below; this follow-up did not qualify GPU execution.

Local evidence group `align-pytorch-4gj9_66u` contains `pytest.log`, `native.log`,
`browser-results-before.json` and `browser-before/` for the initial checks;
`browser-results.json`, `browser/` and `freshness-after.log` record the corrected
artifact. The browser directories include exact page hashes. The only course
teaching change in this follow-up is the dataset/batch explanation; the changelog
and generated publication agree. Unrelated worktree changes were preserved.

## Teaching clarity revision — 2026-10-06

Reviewed `index.html` SHA-256:
`8f914dea0d54e7b05f47647ab7bd9a622f23f7ae7c2c16d8a18db01e08537c51`.

The current course contains eighteen lessons, twenty original passive SVGs and
twenty-two commented Python examples in an estimated three-hour reading route.
Every lesson has an observable objective, definition-first explanation and a
Performance connection. Exact PyTorch API names use bold monospace; application
meanings stay plain. Headings and callout labels may remain bold. The course
remains lessons-only, without Practice, labs, setup or required execution.

The revision clarifies indexed axes and application meanings, concrete
two-by-three-by-four tensors, tensor attributes and random distributions. It
adds batching/vectorization and a focused compilation lesson, preserves unique
teaching and caveats, and consolidates duplicate explanations. Canonical sources,
syllabus, metadata, glossary, references and generated HTML agree. The shared
stylesheet now preserves bold emphasis inside inline code; all eleven HTML
publications were rebuilt and inspected for that shared change.

| Lane | Status | Evidence and limits |
| --- | --- | --- |
| Source/static | Passed | 282 focused regressions, all nine native course validators, full build/freshness, scoped Ruff, configured Markdown lint and whitespace checks. All 22 Python blocks parse. |
| Semantic | Passed | Full-course preservation and technical review, exact API versus example notation, arithmetic and diagram meaning reviewed. Independent review findings were corrected and rechecked; no material open findings. |
| Browser/visual | Passed | 33 checks cover all 11 pages at 1440px, 390px and 320px. Three further checks cover the final PyTorch caption/notation edits. All 20 figures were visually inspected on desktop and phone. |
| Example execution | Partial | Fifteen unchanged CPU snippets and 37 independent assertions passed using the existing PyTorch 2.13.0 environment. Six CUDA snippets and the optional compilation snippet remain unexecuted. |
| Installed environment | Not applicable | No installation or runtime change is required or performed for this reading course. |
| Live target | Not applicable | No live GPU execution, measured speedup, cluster qualification or external publication is claimed. |

Browser checks used Playwright Test 1.57.0 and owned headless Chrome
154.0.8037.98 with page JavaScript disabled. They verify exact navigation and
lesson order, fragment targets, keyboard menus and TOC, local code scrolling,
doubled-text reflow, no automatic remote requests, actual bold API font weights,
and diagram glyph bounds with a minimum rendered label size of 13px. The owned
browser and contexts closed after each run.

Local evidence group `pytorch-clarity-3wrcufx2` retains `pytest-final.log`,
`native-final.log`, build/freshness logs and the browser harness. Passing
all-page evidence is in `browser-results-trial3.json` and `browser-trial3/`;
final PyTorch evidence is in `browser-results.json` and `browser/`. Each browser
directory records inspected HTML hashes, browser identity, text geometry and
screenshots. Earlier trials are retained separately: the first exposed two
diagram text-fit defects that were repaired; the second exposed an arbitrary
test-count assumption that was replaced by checks of actual API names and
font weights. The independent CPU verification is recorded in the review tool
transcript, not an executable artifact in this evidence group.

The generic authoring checker remains diagnostic: its standard Practice and
shared-shell markup diagnostics are identical to the previous sixteen-lesson
artifact. The native profile-aware validator and dedicated regressions own the
explicit lessons-only exception. The generic checker was not modified.

Official PyTorch API documentation and tutorials support the technical claims;
the course references include the relevant sampling, batching, compilation,
dropout and gradient-reset semantics. Reading time remains an authoring estimate,
not a measured learner outcome. The following reviews retain their historical
artifact identities and do not establish current GPU runtime behavior.

## Historical nine-course alignment — 2026-10-05

Reviewed `index.html` SHA-256: `bb7fa40b14a62c425a216c9301acf254e57487c7c5bfa8c22c58271ab162a373`.

The complete nine-package alignment passes 2,503 offline tests, all nine native
validators, build/helper parity and the align review/lint/security lanes.
Responsive checks cover every page at desktop, 390px and 320px; final guide and
PyTorch overview edits have fresh focused checks. See the
[current alignment evidence](../docs/course-format-validation.md#nine-course-alignment--2026-10-05)
for fixes, exact browser artifacts, preserved assets and remaining generic-checker,
dense-diagram and live-qualification limits. Earlier entries retain their own scope.

## Original sixteen-lesson scope

Sixteen lessons, sixteen original SVG diagrams and small commented examples in
a lessons-only reading course. Estimated guided hours: 1.75. No Practice sections,
labs, required execution, installers, Slurm jobs or result archives.

The canonical Markdown, metadata, syllabus, glossary, references and diagram
manifest generate the course HTML through the existing catalog builder.
The explicit no-practice requirement overrides the standard course skill's
Practice section; native profile-aware checks own that exception.

## Original evidence lanes

| Lane | Status | Evidence and limits |
| --- | --- | --- |
| Source/static | Passed | 306 focused tests, all nine native validators, full build and publication freshness, helper parity, scoped Ruff, configured Markdown lint and whitespace checks pass. All 19 Python blocks parse. |
| Semantic | Passed | Full prose, expected shapes/values, first-use definitions and all 16 diagrams reviewed. Independent code/security review found no concrete defect. |
| Browser/visual | Passed | 33 isolated checks cover all 11 HTML pages at 1440px, 390px and 320px. All diagram labels fit their viewboxes and render at least 13px high; desktop and phone figures were visually inspected. |
| Installed environment | Not applicable | Reading has no installation or required runtime. |
| Example execution | Partial | Twelve CPU blocks and independent numeric/shape/storage assertions passed with existing PyTorch 2.13.0. Six CUDA examples and the optional compiler example were not executed. |
| Live target | Not applicable | No live GPU course lab or performance qualification is claimed. |

Browser validation used Playwright Test 1.57.0 and owned headless Chrome
154.0.8037.98 with page JavaScript disabled. Checks include exact catalog/menu
order and destinations, one current identity, keyboard menu/TOC operation,
fragment targets, local code scrolling, normal and doubled-text reflow, no
automatic network requests, and the complete lesson/diagram sequence. The
owned contexts and browser closed after the run. Initial runs exposed two
test-selector assumptions; their logs remain separate from the passing run.
No product change was made to satisfy those failed test assumptions.

The generic authoring checker is diagnostic, not a passing acceptance gate:
it requires the standard Practice section and rejects existing shared shell
markup (`link`, `small`, link/structure diagnostics). The same diagnostics
also occur for the existing Tools reading course. The explicit lessons-only
contract is checked by the passing native validator and dedicated regressions;
the generic checker remains unchanged.

Local evidence group `pytorch-course-01a10ebe` contains the CPU results,
native validation and freshness logs, generic-checker reports, browser test
source and `browser-results-verified.json`. The `browser-verified` directory
contains screenshots, SVG text measurements, browser identity and SHA-256
values for each inspected HTML file. A final catalog copy alignment passed
three additional browser cases, 91 catalog regressions and freshness;
`browser-catalog-final` contains that final catalog identity. The catalog
validation record includes
the exact publication hashes.

## Original technical sources

Official PyTorch 2.14 API documentation and current official tutorials support
the taught tensor, autograd, CUDA, transfer, precision and compiler semantics.
Examples and diagrams use original small synthetic values. Supplied topic
material informed the scope and simplified worked-example selection; no
attachment instructions or private operational material are published.

## Original remaining limits

The reading-time estimate is an authoring target, not a measured learner result.
No measured speedup, GPU-runtime qualification or external publication is claimed.
