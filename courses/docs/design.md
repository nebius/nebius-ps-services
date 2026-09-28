<!-- markdownlint-disable MD001 MD024 -->
<!-- maintain-project-specs:design:start schema=maintain-project-specs/design-v2 -->
# Project Design

<!-- FEATURE: FEAT-001 reqs=REQ-001 status=ready delivery=implemented priority=P0 version=6 -->
### FEAT-001: Standalone course catalog

#### Requirements Covered

- REQ-001: GPU learning path, Slurm introduction and advanced fabric labs.

#### Context Evidence

Five independent course roots support general GPU foundations and three specializations.

#### Design Details

Use the canonical order Soperator, GPU Fundamentals, GPU Performance Optimization, LLM Training, LLM Inference, Custom CUDA Kernels, then Advanced Labs: Multi-GPUs Multi-Nodes communication optimization. Start the catalog hero and learning path with the text-only Slurm introduction. Show cluster essentials first, the two GPU foundations next, the three specializations next, and advanced communication last. Derive prerequisite labels from course identity so changing display position cannot change technical requirements. Align README lists, tables and trees, all-course registries and every generated course switcher. Use Performance Engineering Courses as the shared collection title and remove H100 collection branding while retaining technical hardware requirements. Keep runtime helpers within the six executable course packages and preserve lesson/lab identities and the existing specialization prerequisite graph.

#### Selected Option

Independent self-contained packages with one canonical path.

#### Alternatives Considered

Shared runtime dependencies would couple learner environments unnecessarily.

#### Implementation Boundaries

Courses only; no provisioning, publication or unrelated repository changes.

#### Test-First Success Criteria

- TDD-001: Exact seven-root catalog, five-GPU prerequisite and independent-helper assertions pass.

#### Validation Plan

Check inventory, links, source parity and standalone validators.

#### Test Plan

Exercise course-local commands and domain ownership.

#### Evaluation Plan

Each course is navigable independently.

#### Rollout And Rollback

Atomic HTML generation; version control protects source changes.

#### Done Definition

Seven roots and correct prerequisite links are present.

#### Implementation Evidence

The renderer registry, catalog entry link, learning path, numbered cards, all seven course switchers, standalone-validator registry, course/root READMEs and root landing page now use the Soperator-first route. README tables and destination trees include all seven courses. Prerequisite labels use course identity; technical prerequisites and lesson/lab identities are unchanged. All eight course/catalog HTML outputs were rebuilt; task-owned deltas were reviewed against pre-existing working files.

#### Verification Evidence

All seven standalone validators, HTML/helper parity and 69 focused catalog/text-course tests pass. Independent checks confirm exact README list/table and all seven switcher orders. Twenty-four owned isolated headless Chrome 153.0.8010.53 checks pass at 1440, 390 and 320 pixels, including entry/navigation, current-course identity, prerequisite labels and 200% text reflow; catalog and mobile navigation screenshots were visually reviewed. The initial harness read the URL before navigation settled; the final assertions wait for the destination. Ruff lint, repository Markdown checks, scoped whitespace and independent read-only review pass. Existing Ruff formatting differences and generic skill-checker profile/markup diagnostics match task-start files. Publication reviews record current HTML identities. No live execution or external publication was performed.

<!-- /FEATURE: FEAT-001 -->

<!-- FEATURE: FEAT-002 reqs=REQ-002 status=ready delivery=implemented priority=P0 version=17 -->
### FEAT-002: Shared authoring and publication contract

#### Requirements Covered

- REQ-002: Consistent practical and publication-safe teaching contract.

#### Context Evidence

The packages contain long-form lessons, runnable labs and self-contained HTML.

#### Design Details

Each course has exactly one Where to Go Next in NEXT-STEPS.md and one A–Z Glossary in GLOSSARY.md, rendered as independent top-level sections in that order before final Official references. Merge lesson and performance-guide glossary entries by normalized displayed term, retaining expansions and distinct meanings. Consolidate distinct onward options into NEXT-STEPS.md; remove repeated next-lesson pointers only after proving that they repeat the destination objective and ordered navigation. Retain transfer prompts and optional-study limits in the course-level guide. Lessons and performance guides contain Objective, How it works, Practice and Mental model, followed only by optional References. Preserve first-use explanations, worked examples, practice, diagrams, lesson/lab identities, runtime sources and the next-steps/guide-glossary anchors. Builder and standalone validators reject local appendix fields and enforce one of each course appendix, source parity, alphabetical glossary keys and final References across all profiles. Use the revised installed create-learning-course skill; its user-maintained source is outside the resumed edit scope.

Use one renderer and shared light stylesheet, complete source listings, accessible SVGs, supporting guides and official end references for the GPU profiles. FEAT-030 adds an explicit labs-only renderer with complete practical teaching and no conceptual lesson placeholders. FEAT-029 provides an explicit text-only renderer without lab/runtime or diagram requirements. FEAT-011 owns the publication simplification; FEAT-021 defines the current lesson-to-lab boundary and unified Practice section.

Mark maintainer-only README sections with a concise **For course maintainers**
label. Within mixed-audience sections, label only the authoring or publication
instructions. Keep learner environment checks and lab execution guidance
available to learners. Rebuild any course HTML that embeds an edited README.

Use one shared TOC and reflow treatment across all seven profiles. Remove obsolete base-route wrappers when a course has a single conceptual route. Use ordinary ordered lesson markers with titles derived from the owning lesson, and exact lab titles without extra numbering. Keep only meaningful profile differences. Standardize the canonical Practice label without a legacy alias. Review source titles, syllabi, mission/prerequisites, moved-lab references and final summaries; preserve useful teaching and executable behavior. Verify all seven full pages at desktop, 390px and 320px widths, including enlarged text and local scrollers.

Apply the create-learning-course standard through a preservation-first semantic pass: make every conceptual Practice state its stage-specific action, retain previews and revisits from the syllabus, define advanced terms before using them, and replace generic investigation figures with core relationships. Correct the RDMA-only profiling contradiction without changing the lab exception. Compare pre/post source identities for all executable code, guide recipes, metadata and dashboards. Soperator remains text-only with reading checks; the advanced course retains complete local lab guides.

Sort each canonical `GLOSSARY.md` by its displayed term, retaining complete list entries or table rows and all definitions. Render Glossary after Where to Go Next and immediately before References in all three course profiles, with matching TOC order. Keep the existing `guide-glossary` target and license footer. Rebuild all seven course pages; verify source ordering, entry preservation, final section adjacency and navigation across every profile.

Use the shared renderer and CSS for matching heading hierarchy across all seven profiles: course title h1, primary topics h2, lesson fields and guide titles h3, subordinate concepts h4. Omit MISSION.md and SYLLABUS.md from learner content and navigation, preserving their unique context in the overview and existing guides. Keep their authoring sources without learner-facing links. Render the course glossary as its own section and definition list; render numbered official references and complete bulleted onward-study topics. Keep concrete next learning options in the single course appendix without changing the text-only or labs-only exceptions, executable content, lesson identities, or required completion boundaries. Validate preservation against task-start sources, generated parity, focused regressions and isolated browser checks at desktop, 390px and 320px.

#### Selected Option

Course-local canonical content with dependency-light shared authoring tools.

#### Alternatives Considered

Manual per-course HTML would introduce drift.

#### Implementation Boundaries

Do not publish private data, unsupported claims or external runtime assets.

#### Test-First Success Criteria

- TDD-001: Source parity, complete guide navigation and accessible diagrams are enforced.

#### Validation Plan

Run validators, public-safety checks and permitted desktop/mobile review.

#### Test Plan

Test exact listings, lesson fields, links and responsive CSS.

#### Evaluation Plan

Every important lesson leads to evidence or a clear advanced-topic limitation.

#### Rollout And Rollback

Generate each HTML file atomically.

#### Done Definition

One coherent authoring/presentation contract applies to every course.

#### Implementation Evidence

Single-appendix delivery on 2026-09-25: every course closes with exactly one Where to Go Next, one A–Z Glossary and final Official references. Removed 84 lesson-local and five performance-guide copies of both appendices. Merged 93 local-only glossary keys into their course glossaries, preserving all keys and distinct meanings; total entries increased from 276 to 369. All teaching outside the removed blocks, optional study topics, 110 lab implementations and 110 lab guides remain unchanged. Shared renderer, text/GPU/labs-only validators, six generated validator copies, regressions and maintainer documentation enforce the same contract. The user-maintained installed and source skill are unchanged since resume. Earlier lesson-local appendix delivery below is historical and superseded by this contract.

Shared-format delivery on 2026-09-25: all seven profiles use matching semantic heading levels and one stylesheet. Numbered official references, complete bulleted next directions and standalone glossary definition lists are rendered consistently. Learner mission/syllabus sections and links are removed; unique scope, readiness and completion guidance is retained in overviews and existing guides, with authoring sources preserved. All 84 original lesson bodies and 276 course glossary definitions remain; concrete onward steps now appear in 84 lessons and five tool guides. The benchmark reminder belongs to the existing Review card. All 3,396 protected lab, environment and evidence files remain byte-identical, including 110 lab guides. Source Markdown fragments and generated targets agree across profiles.

The following implementation records are historical and retain their original scope.

All seven canonical glossaries now sort their 276 complete entries by displayed term. All three renderer profiles place Glossary immediately before References in both content and TOC, using the existing glossary anchor. The GPU benchmark reminder now precedes Glossary so References remains the final content. The shared README and maintainer guide describe the ordering, and generated pages are rebuilt.

Current preservation-focused standardization covers all seven profiles. All 78 numbered GPU lessons now state their stage-specific Practice action, with previews, execution and revisits aligned to their syllabi. The shared renderer preserves that prose and its authored guide order while requiring the exact assigned guide set; standalone validators also reject stale rendered context. Advanced definitions, two worked training examples, five relationship diagrams and the RDMA-only profiling exception are clarified. Final summaries add no unrelated mechanism. Soperator retains six text-only lessons, and advanced communication remains labs-only.

A before/after comparison confirms 560 protected source files are byte-identical, including every executable lab guide and dashboard. All 84 numbered lesson identities and 134 lesson-to-lab associations remain. Runtime, launcher, environment, profiling recipes and dashboard behavior are unchanged; only standalone validation adopts the current teaching contract.

Earlier feature-delivery evidence follows for provenance.

The five courses contain 73 lessons, 94 numbered labs and 122 diagrams. FEAT-011 supplies the publication UI; FEAT-019 and FEAT-020 supply the networking and transfer additions, FEAT-021 defines the lesson/lab boundary, and FEAT-023 defines the current lesson presentation and diagram coverage.

The catalog README labels website publication, authoring advice, offline
validation and lab-guide maintenance for course maintainers. The Fundamentals
README labels its publication instruction, including in the generated HTML.

#### Verification Evidence

Single-appendix verification: all seven repository validators, generated-page/helper parity and 242 focused tests pass, with three existing skips. Preservation checks cover 1,584 protected implementation/reference files and all 69 installed/source skill files. Scoped lint and read-only content/code/security review pass. Twenty-one complete-page Playwright Test cases pass at 1440, 390 and 320 pixels in isolated headless Chrome 153.0.8010.53, plus seven actual keyboard-scrolling cases at 320 pixels. Exact glossary content, closing structure, navigation, mobile TOC and enlarged-text reflow pass. Narrow glossary screenshots of all seven pages and selected desktop, onward-study and enlarged-text views were reviewed. Final HTML hashes and private evidence group course-glossary-madfkwxf are recorded in each PUBLICATION-REVIEW.md. The interrupted trace-enabled attempt remains separate; final passing checks have no traces. Framework-owned browser resources closed and final runners exited successfully. The generic checker retains its existing profile/markup diagnostics and is not claimed passing; independent source manifests and repository source parity pass. No installation, new runtime/live qualification or external publication occurred. Broader feature delivery remains implemented.

Earlier verification records below are historical and retain their own artifact scope.

Shared-format verification on 2026-09-25: all seven validators, generated-page/helper parity and 278 focused tests pass with three existing skips. Scoped Ruff/formatting, configured Markdown and whitespace checks pass. Twenty-one isolated owned headless Chrome 153.0.8010.53 cases cover the seven current HTML artifacts at 1440, 390 and 320 pixels: typography, section/list structure, fragments, keyboard navigation, local scroller focus, enlarged text reflow, page overflow and absence of external requests or script errors. All identity hashes match current pages; sampled screenshots across every course were visually reviewed. Initial 20/21 browser evidence is retained separately; final focus synchronization passes without a product timing-cause or navigation-repair claim. Read-only semantic and code/security reviews report no remaining finding. Each publication review records exact artifact identity and private evidence references. The generic skill checker retains its pre-existing profile/markup diagnostics and is not claimed passing; independent source membership and canonical bytes pass repository checks. No installed, live-target or external publication qualification was performed. Broader delivery remains implemented.

The following verification records are historical and do not supersede the current format evidence.

Glossary ordering verification: independent source and HTML parsing confirms all 276 definitions are byte-preserved as complete entries, A–Z ordering ignores case and Markdown code formatting, the final content and TOC positions match across all seven courses, and unrelated course HTML is unchanged after excluding the moved glossary, its TOC link and the benchmark reminder. All seven course validators, generated-page/helper parity, 56 focused presentation/text/labs-profile tests, scoped Ruff and Markdown checks pass. The default interpreter lacked pytest; focused tests ran in an isolated uv environment with the repository-pinned pytest version. This is local source/build validation; no website publication was performed.

All seven project validators, HTML/helper/dashboard/kit parity and 430 focused CPU checks pass. Scoped Ruff, formatting, Markdown and whitespace checks pass. Final owned isolated headless Chrome 153.0.8010.48 passes 24 whole-page cases across the seven courses and catalog at 1440, 390 and 320 pixels, with keyboard navigation, downloads, local scrollers, figure bounds and 200% reflow. Ten additional checks cover the five changed diagrams, which were visually reviewed. Every browser record matches the current HTML hash. An initial intermittent 320px anchor failure is retained; the final harness waits for focus before navigation and no page-repair claim is made.

The unmodified generic skill checker was executed on all seven pages and does not pass the documented local-navigation, embedded-asset/footer and explicit special-profile differences. Project-specific checks cover those actual contracts; the checker is not relabeled as passing. Source and browser evidence do not qualify installed or live GPU, Slurm, fabric, Nsight or Grafana behavior. See docs/course-standardization-validation.md and each PUBLICATION-REVIEW.md. Final nested code/security review found no blocking changed-scope issue.

Earlier verification records below are historical and do not override the
current format/browser result.

Source checks pass; fresh visual and H100 qualification remain separate.

For the maintainer labels, review of the catalog and all five course READMEs,
Markdown lint, selected-page source/HTML parity, the Fundamentals standalone
validator and diff checks passed. These checks cover documentation only.

<!-- /FEATURE: FEAT-002 -->

<!-- FEATURE: FEAT-003 reqs=REQ-003,REQ-004 status=ready delivery=implemented priority=P0 version=3 -->
### FEAT-003: General GPU curriculum boundary

#### Requirements Covered

- REQ-003: H100 GPU fundamentals coverage.
- REQ-004: General PyTorch GPU optimization workflow.

#### Context Evidence

The existing Fundamentals and Optimizations courses have strong runnable foundations, while compatibility, health, sharing, tail diagnosis, and specialization ownership need refinement.

#### Design Details

Expand Fundamentals with platform and operational mental models. Keep Optimizations framework-level and evidence-first; move training checkpointing and inference attention into their owning courses.

The accepted Lesson 1 revision adds a simple SM/L2/HBM overview before the
existing single-SM enlargement. Use H100 SXM 80 GB counts and explicitly
distinguish the PCIe product and full GH100 die. Define the hardware hierarchy
GPC/TPC/SM/SMSP separately from a grid's blocks and their threads grouped into
warps. Derive a launch-size example and resident-thread upper bound. Explain
that blocks have a program-selected size: 32 resident blocks is the hardware
ceiling, while 1,024-thread blocks allow at most two per SM. Add a nearby
block-size comparison from 32 to 1,024 threads; apply the thread, warp and block
limits together and qualify the results by register and shared-memory use.
Define a resident block as placed on an SM with register space and any needed
shared memory reserved. Explain that its working values and progress remain
tracked while a warp waits for data and another ready warp executes. Connect
the resident-thread count to retained work, not instructions executed per cycle.
Preserve the existing deterministic
renderer, course identity, lab logic and target contract. This addition is implemented. Its source checks and SVG asset review pass;
full-page browser and H100 evidence remain separate and pending.

#### Selected Option

Teach hardware understanding before general optimization and link to specialized courses at the library-versus-custom boundary.

#### Alternatives Considered

Duplicating domain labs was rejected because it creates drift and obscures course ownership.

#### Implementation Boundaries

Health and sharing exercises are read-only. Low-level CUDA construction belongs to FEAT-006.

#### Test-First Success Criteria

- TDD-001: Coverage and ownership tests prove the added general topics and the single domain ownership.

#### Validation Plan

Run course validators, Python/static tests, browser review, and documented H100 smoke tests.

#### Test Plan

Add pure tests for compatibility, health, scheduling/tails, and library-first decision evidence.

#### Evaluation Plan

Confirm learners can identify a bottleneck and choose the next evidence to collect or specialization to pursue.

#### Rollout And Rollback

Keep existing stable labs where still owned and introduce focused additions without compatibility wrappers.

#### Done Definition

Both courses have clear non-overlapping missions and the required lessons, labs, diagrams, and validation.

#### Implementation Evidence

Lesson 1 introduces an SM/L2/HBM SVG before the enlarged SM map. H100 SXM
80 GB counts, GPC/TPC/SM/SMSP names, logical work grouping and a qualified
launch/residency example use official NVIDIA references. The SVG was inspected
at 900 and 320 pixels.

The residency table now names the two-block limit for 1,024-thread blocks.
An adjacent six-row comparison covers 32 through 1,024 threads per block,
derives the thread/warp bounds and explains why 32 blocks of 32 threads reach
the block ceiling with only half the thread capacity. Register and shared-memory
constraints remain explicit; this is a capacity explanation, not a measured
schedule or performance claim.

The lesson and glossary now define residency using SM placement, retained
register values and reserved shared memory where needed. A waiting warp and
an executing warp can both remain resident; the explanation connects this
example to the 2,048-thread capacity. The official hardware-multithreading
reference is included in the end resources.

Fundamentals now provides 13 H100-focused labs, including compatibility, tail
scheduling, and read-only health evidence. Optimizations provides 15 general
PyTorch performance labs, including tail/load diagnosis and a library-first
escalation capstone; training checkpointing and SDPA belong exclusively to their specialized courses.

The individual-course alignment pass additionally preserves unknown MIG query values instead of classifying them as full-device evidence. General timing prose distinguishes host medians, event min/median/p90 summaries, elapsed marker intervals, and raw-sample extensions. Fusion examples use logical traffic and require profiler evidence for physical HBM claims. Onboarding names the required two-node preflight.

#### Verification Evidence

Residency clarification: all five standalone validators, source-to-HTML parity,
57 focused content/opening tests and configured Markdown lint pass. Reviewed
the six numerical rows against the NVIDIA limits verified for this revision.
The concrete residency definition and waiting-warp example were checked against
NVIDIA's hardware-multithreading description; the same 57 focused tests and
five validators pass after the wording and glossary update.
Browser and H100 evidence remain pending.

Earlier revision verification: all five standalone validators and 742 offline tests
pass. Negative controls cover altered/missing table cells and literal code
pipes with and without a blank line before a fence. Independent read-only
review closed preservation and prerequisite findings. Lab executables and
launchers remain unchanged. Full-page browser review is pending after local-file
access was denied. The installed bounded skill checker reports the same
pre-existing catalog-markup incompatibilities on HEAD and revised pages;
its gate is not claimed passed. No new target-runtime qualification is claimed.

Course validators, ownership tests, pure helper tests, Python help checks, and
HTML parity pass. CUDA execution, profiler evidence, NCCL, and one- or two-H100
completion remain pending on the declared Linux Slurm target.

Individual-course alignment verification on 2026-09-04: all five validators and 268 tests pass, including 40 new source/CPU regressions and host-only C++20 argument tests. Ruff/check and format cover 100 Python files; configured Markdown, 31 shell syntax/ShellCheck checks, 134 command-fence syntax checks, and source parity pass. Current publication-safety scans cover 354 course files. The 59 style, diagram and inventory source files are byte-identical to the alignment checkpoint. Final independent read-only reviews report no remaining actionable source findings in the repaired surfaces. CUDA build/CTest, sanitizers, profilers, environment qualification, live H100 and browser review remain pending; delivery is implemented, not verified.

<!-- /FEATURE: FEAT-003 -->

<!-- FEATURE: FEAT-004 reqs=REQ-005 status=ready delivery=implemented priority=P0 version=2 -->
### FEAT-004: Standalone LLM training course

#### Requirements Covered

- REQ-005: Practical LLM training optimization course.

#### Context Evidence

Training has its own model, optimizer, data and distributed-state curriculum.

#### Design Details

Provide training-owned labs for resume, packing, recomputation, pipeline, fusion/graphs, overlap, parallelism, profiling, and capstone exercises with training-specific evidence.

#### Selected Option

Use a standalone Python/PyTorch package with one- and two-node Slurm profiles and optional site-qualified Transformer Engine.

#### Alternatives Considered

Keeping shared LLM documentation was rejected because independent learners and environments require explicit ownership.

#### Implementation Boundaries

No serving engines or inference-latency material. Multi-node results are bounded mechanics, not scale proof.

#### Test-First Success Criteria

- TDD-001: Training tests cover domain ownership, numerical behavior, resume, padding, parallel payloads, and evidence output.

#### Validation Plan

Run offline/static gates, dependency checks, one-H100 smoke, and bounded DDP/FSDP2 two-node trials.

#### Test Plan

Use deterministic small models and pure helpers locally; reserve CUDA and Slurm behavior for target validation.

#### Evaluation Plan

Compare correctness, loss, step time, throughput, memory, communication, and MFU under controlled changes.

#### Rollout And Rollback

Keep qualification evidence course-specific and pending until it is collected.

#### Done Definition

The package is standalone, complete, and statically verified, with live gates accurately marked.

#### Implementation Evidence

The standalone Training package contains 22 numbered PyTorch labs covering the
training curriculum including RNG-active checkpoint resume, packing, recomputation,
pipeline starvation, fused/graph traces, communication overlap, TP/PP/CP/EP,
grouped expert work, profiling, and an aggregated causal capstone.

#### Verification Evidence

The package validator, shared static tests, import/help checks, source parity,
and launcher syntax checks pass. A clean PyTorch 2.14 environment, Transformer
Engine activation, and one- and two-H100 execution remain pending.

<!-- /FEATURE: FEAT-004 -->

<!-- FEATURE: FEAT-005 reqs=REQ-006 status=ready delivery=implemented priority=P0 version=6 -->
### FEAT-005: Standalone LLM inference course

#### Requirements Covered

- REQ-006: Practical LLM inference optimization course.

#### Context Evidence

Inference separates mechanics and serving environments and owns engine workflows.

#### Design Details

Provide inference-owned labs for cache/scheduling/quantization/metrics/parallelism exercises, and provide gated engine profiles for vLLM, TensorRT-LLM/Triton, AIPerf, and advanced Dynamo material.

Lab 30 uses separate owned OpenAI and Triton launchers. Each starts a prepared immutable engine with its reviewed cached model or repository, binds loopback inside one Slurm allocation, waits for readiness, invokes the unchanged bounded HTTP probe, and cleans up on success, failure or signal. OpenAI uses the existing vLLM serving contract and pinned model revision. Triton starts the image-owned `mpirun --allow-run-as-root --oversubscribe -n 1` before `tritonserver` inside the single-task Slurm allocation. MPI oversubscription allows the backend to spawn its worker alongside the launcher when Slurm advertises only the single task slot, within the unchanged CPU/GPU allocation. This initializes a dedicated MPI environment for the backend instead of relying on direct-launch Slurm PMI compatibility; it preserves the same loopback ports, request, readiness bound and owned cleanup. The launcher also disables configuration auto-completion, avoiding a temporary MPI-aware Python stub before the persistent model instance. Prepared config.pbtxt files must explicitly preserve the batch and transaction-policy values that the reviewed model YAML would supply; the course does not edit them during execution. Both routes retain the selected workload profile and one run identity in result/log paths; neither performs installation or model downloads, and neither claims a GPU capture or engine performance comparison. A missing server must fail before measurement. Local lifecycle tests precede fresh target qualification.

#### Selected Option

Use separate mechanics and serving environments plus immutable engine containers for heavyweight optional profiles.

#### Alternatives Considered

One merged environment was rejected because platform and dependency conflicts weaken reproducibility.

#### Implementation Boundaries

Dynamo is advanced/conditional. No unsupported RDMA, topology, quality, or performance claims.

#### Test-First Success Criteria

- TDD-001: Inference tests cover cache math, metrics, scheduling, quantization comparison, clients, launcher safety, and gated profiles.

#### Validation Plan

Run static gates, both dependency profiles, one-H100 mechanics/serving smoke, and bounded two-node serving trials.

#### Test Plan

Use pure simulators and mocked payload tests locally; execute real engines only on supported Linux/H100 targets.

#### Evaluation Plan

Compare TTFT, ITL/TPOT, throughput, output rate, memory, concurrency, and quality under fixed workloads.

#### Rollout And Rollback

Keep engine claims pending until target execution.

#### Done Definition

The package is standalone, complete, and statically verified with explicit live-engine gates.

#### Implementation Evidence

Lab 30 now has a dedicated OpenAI launcher with an immutable vLLM image, offline cached model/revision, one allocation, loopback binding, per-run readiness identity and bounded cleanup. Its recipe and learner instructions use that launcher; the HTTP probe and separate Triton route retain their qualification semantics.

The standalone Inference package contains 24 numbered Python labs, separate
mechanics and serving manifests, and immutable-container launchers for vLLM,
TensorRT-LLM/Triton, AIPerf, and conditional Dynamo. Live chunked-prefill,
prefix-cache, speculative, parallelism, and capstone profiles use independent
restarts, correctness gates, and counterbalanced trials where applicable.

#### Verification Evidence

Nine focused launcher/recipe cases pass with local HTTP fixtures, covering OpenAI success, malformed generation, early server exit, occupied port and foreign server identity, plus Triton MPI and disabled-auto-completion command wiring, successful generation, startup failure and invalid-response cleanup. The new Triton cases failed against the prior direct-server command before the MPI launch repair; an independent real-image diagnostic confirms that a first MPI-initializing child succeeds and a successive child inheriting the same rank fails. Disabling temporary auto-completion passed that boundary in a fresh full server run, which then failed dynamic MPI worker spawn because its task slot was occupied. A separate real-image CPU-only counterfactual reproduced that spawn rejection and succeeded with MPI oversubscription in the same one-task, sixteen-CPU allocation. These are prerequisite proofs; full Triton readiness, generation and cleanup qualification remain pending. Shell syntax and ShellCheck pass. Fresh qualification on one H200 completed successfully using the authored launcher, prepared vLLM image and cached pinned model: the original result passed the OpenAI response gate, Slurm completed with exit zero, and an independent worker-side check found the owned loopback port closed afterward. This qualifies the bounded OpenAI protocol lifecycle only; the separate Triton route and full all-course evidence campaign remain pending.

Cache, scheduling, metric, client, revision, source-parity, and launcher safety
tests pass locally. Clean PyTorch 2.14 installation, engine activation, Linux
container compatibility, and one- and two-H100 serving runs remain pending.

<!-- /FEATURE: FEAT-005 -->

<!-- FEATURE: FEAT-006 reqs=REQ-007 status=ready delivery=implemented priority=P0 version=2 -->
### FEAT-006: CUDA C++ custom-kernel course

#### Requirements Covered

- REQ-007: CUDA C++ custom-kernel optimization course.

#### Context Evidence

The current learning path discusses low-level techniques but has no dedicated CUDA C++ package or build/test contract.

#### Design Details

Create a CMake CUDA C++20 package with thirteen progressive labs, common error/timing helpers, trusted references, one-node Slurm launchers, and gated SM90a/Tile C++ extensions.

#### Selected Option

Use explicit SM90 builds and library baselines, teaching fusion, memory, synchronization, resources, pipelines, Hopper features, and one LLM-relevant kernel.

#### Alternatives Considered

Python bindings and handwritten production GEMM were rejected as unnecessary complexity and misleading defaults for this course.

#### Implementation Boundaries

Required acceptance is one H100. Distributed CUDA is out of scope. Optional architecture features fail closed when unavailable.

#### Test-First Success Criteria

- TDD-001: Static tests cover C++/HTML parity and build contracts; target CTest covers correctness and edge shapes.

#### Validation Plan

Run local static checks, then CMake, CTest, Compute Sanitizer, Nsight, and repeated benchmarks on one H100.

#### Test Plan

Use explicit tolerances, CUDA error checks, library references, resource reports, and optional-feature gates.

#### Evaluation Plan

Accept a custom implementation only when correctness, safety, profiler, kernel, and end-to-end evidence justify it.

#### Rollout And Rollback

Keep optional labs isolated behind build options and use Git history to revert course additions.

#### Done Definition

All thirteen labs, documentation, diagrams, build files, launchers, and validators are present and statically verified.

#### Implementation Evidence

The Custom CUDA package contains 13 CUDA C++20 labs, CMake SM90 targets,
fail-closed optional SM90a and CUDA Tile material, a required source-pinned
CUTLASS 4.6.1 epilogue comparison, library references, sanitizer/profiler
launchers, and the residual-plus-RMSNorm case study.

The individual-course alignment pass sets the default SM90 architecture before CUDA compiler initialization, uses the Runtime API's individual cluster-dimension members, and binds capstone commands to the completed build directory. Simple CLI flags are validated in a CUDA-independent header before device activation; unknown or extra arguments cannot select a full workload silently. Existing valid flags and kernel computations are preserved.

#### Verification Evidence

Static C++ listing parity, build-contract, sanitizer-tool, educational-content,
launcher syntax, and HTML checks pass. No local CUDA compiler is available, so
CMake configuration, compilation, CTest, sanitizers, profilers, and H100 timing
remain pending.

Individual-course alignment verification on 2026-09-04: all five validators and 268 tests pass, including 40 new source/CPU regressions and host-only C++20 argument tests. Ruff/check and format cover 100 Python files; configured Markdown, 31 shell syntax/ShellCheck checks, 134 command-fence syntax checks, and source parity pass. Current publication-safety scans cover 354 course files. The 59 style, diagram and inventory source files are byte-identical to the alignment checkpoint. Final independent read-only reviews report no remaining actionable source findings in the repaired surfaces. CUDA build/CTest, sanitizers, profilers, environment qualification, live H100 and browser review remain pending; delivery is implemented, not verified.

<!-- /FEATURE: FEAT-006 -->

<!-- FEATURE: FEAT-007 reqs=REQ-008 status=ready delivery=implemented priority=P0 version=6 -->
### FEAT-007: Environment and evidence qualification

#### Requirements Covered

- REQ-008: Separate evidence lanes and reproducible environments.

#### Context Evidence

Existing offline validation is strong, while CUDA, engine, Slurm, and H100 execution remain target-only evidence.

#### Design Details

Represent source/static, installed dependencies, runtime activation, and live H100 completion independently in every course. Pin only qualified versions and keep unavailable live gates pending.

For Optimizations Lab 16, retain the BF16 workload and use the original BF16
inputs converted to FP64 to calculate `P = X @ W` and `R = relu(P + bias)`
outside timing. Check each output independently with the elementwise budget
`0.01 + 0.01 * abs(R) + u * (1 + u) * abs(P)`, where BF16 unit roundoff
`u = 2^-8`. The last term accounts for rounding the composition's matrix
product before bias addition and propagating that error through final rounding;
the original absolute/relative tolerances remain the residual acceptance budget.
ReLU does not amplify absolute error. This is the declared lab acceptance
policy, not a guarantee for arbitrary reduction algorithms or inputs. Require
finite references/budgets and finite, nonnegative BF16 outputs of the expected
shape. Reject failure before timing or result publication. Publish separate
per-path reference checks and error metrics; remove the former pairwise
`allclose` gate without a compatibility alias.

Keep pytest optimization inside test helpers and fixtures. Publication scans
prune `.venv*` directories before descent and retain the original eligible-file
and suffix rules. Lab 16's 24 corruption cases use independent 4-by-4 CPU
fixtures containing cancellation, negative preactivation and bias-only positive
outputs; the three 512-by-512 cancellation/seeded acceptance cases remain.
All cases still execute the real lab entry point and numerical checks. Preserve
function-scoped mutable inputs, fresh lab module imports, subprocess validation
and the full serial test command. Compare the same node IDs, outcomes, installed
interpreter/plugins and cache policy over five bounded baseline/candidate runs.

#### Selected Option

Use per-course environments, inference profile isolation, pinned containers for heavyweight engines, and private raw evidence with public sanitized summaries.

#### Alternatives Considered

One shared environment and static-as-runtime claims were rejected because they hide compatibility and evidence boundaries.

#### Implementation Boundaries

No cluster provisioning, credentials, scheduler administration, GPU reconfiguration, or publication of private runtime artifacts.

#### Test-First Success Criteria

- TDD-001: Tests require complete version records, explicit pending states, tolerances, sample controls, and fail-closed optional profiles.
- TDD-002: Lab 16 accepts the exact cancellation counterexample and both smoke seeds against its independent FP64 reference, while rejecting shared corruption, omitted bias/ReLU, non-finite values and malformed output shape/dtype before measurement.
- TDD-003: Optimized publication traversal selects the same eligible files and still rejects forbidden source content. All original pytest node IDs and outcomes remain, fault cases stop before timing/publication, and repeated timings demonstrate the improvement without production changes or new dependencies.

#### Validation Plan

Execute the four evidence lanes independently and record only the highest completed state.

#### Test Plan

Validate environment manifests and evidence schemas offline; execute runtime and live tests only on declared targets.

#### Evaluation Plan

Reject any performance, scale, or activation claim not supported by the matching evidence lane.

#### Rollout And Rollback

Version baselines may differ by course only when the compatibility blocker and qualified fallback are documented.

#### Done Definition

All course claims, versions, and publication states accurately reflect completed evidence.

#### Implementation Evidence

Each course now records source/static, installed environment, runtime
activation, and live H100 evidence independently. Ordinary PyTorch manifests
target 2.14.0; Inference separates mechanics from lightweight serving clients,
and heavyweight engines plus the CUDA toolchain require immutable image digests.

The individual-course alignment pass corrects the Inference mechanics example to select its shared mechanics interpreter explicitly. LLM dependency sets are described as candidate pins with target installation and joint compatibility pending, not an approved installed fallback.

#### Verification Evidence

The source/static lane passes all current local gates. Existing local 2.13
environments are historical and not authority for the new 2.14 manifests; clean
2.14 installs, CUDA/runtime activation, engines, Slurm, and live H100 completion
remain explicitly pending in publication records.

Individual-course alignment verification on 2026-09-04: all five validators and 268 tests pass, including 40 new source/CPU regressions and host-only C++20 argument tests. Ruff/check and format cover 100 Python files; configured Markdown, 31 shell syntax/ShellCheck checks, 134 command-fence syntax checks, and source parity pass. Current publication-safety scans cover 354 course files. The 59 style, diagram and inventory source files are byte-identical to the alignment checkpoint. Final independent read-only reviews report no remaining actionable source findings in the repaired surfaces. CUDA build/CTest, sanitizers, profilers, environment qualification, live H100 and browser review remain pending; delivery is implemented, not verified.

#### Pytest feedback optimization

The test-only implementation prunes existing `.venv*` exclusions before
publication traversal and reduces only the 24 deterministic BF16 rejection
fixtures to 4-by-4. The three 512-by-512 acceptance fixtures and all assertions
remain. Exact traversal comparisons preserve 81 ownership-check files and 292
publication-check files. Synthetic controls verify excluded trees are never
entered, file/directory symlink behavior is retained and forbidden content in
eligible source still fails. Independent read-only review found no regression.

On 2026-09-06, five serial warm-cache runs of the unchanged 546-test selection
passed before and after the change using local macOS CPU Python 3.12.14,
pytest 9.1.1 and PyTorch 2.13.0, with no third-party pytest plugins. The command
was `python -m pytest -q -p no:cacheprovider --basetemp <fresh-task-temp> tests`
with bytecode writes disabled and a 90-second process-group deadline per run.
Source fingerprints were stable within each sample; all ordered node IDs and
outcome counts matched, with no skips, deselections, failures or reruns.

Uninstrumented wall-clock samples were 24.1972, 25.2250, 25.3029, 25.6987 and
25.8099 seconds before, versus 19.5180, 19.8382, 20.1597, 19.9260 and 20.2829
seconds after. The median fell from 25.30 to 19.93 seconds (21.25% reduction),
with non-overlapping observed ranges. Separate three-sample startup/collection
medians were 0.229 and 0.235 seconds; startup was not isolated from collection.
Separate duration diagnostics attributed about 3.80 seconds to the corruption
matrix and 2.62 seconds to publication traversal before the change. Afterwards,
each corruption case was below 0.005 seconds and the two scans totaled about
0.01 seconds at reporting precision. Setup was dominated by the retained host
C++ probe compilation; teardown was below displayed precision. Required
validator/help subprocesses remain. These are local feedback measurements,
not a CI threshold or PyTorch 2.14/CUDA/H100 qualification.

The subsequent 660-test pass reuses a fresh renderer within each presentation
test and extracts CLI options once per lab source within each course's guide
test. Renderer loads fall from 328 to 10 and AST parses from 157 to 75; all 99
shell-command checks remain identical. Temporary fault controls still reject
invalid options, shell syntax and missing sources, and a new invocation rereads
changed sources. Neither cache nor module state crosses test invocations.

Five runs per frozen source snapshot, using the same environment, command and
cache policy above, preserve all 660 ordered node IDs and passing outcomes.
Baseline wall times are 22.9746, 23.2326, 22.9500, 23.3418 and 23.0753 seconds;
candidate times are 21.6858, 21.3996, 21.4293, 21.4835 and 21.7324 seconds.
The median falls from 23.0753 to 21.4835 seconds (6.90%), with non-overlapping
ranges. Separate diagnostics put the ten affected presentation cases at
1.1429 versus 0.0675 seconds and the five guide-command cases at 1.2913 versus
1.1325 seconds. Startup and collection combined remain about 0.33 seconds;
they were not measured separately. Setup and teardown are not optimization
targets. Frozen snapshots isolate these measurements from concurrent course
edits; they are local feedback evidence, without a CI or GPU-runtime claim.

#### All-course alignment follow-up

Optimizations Lab 16 now implements the independent FP64 reference and explicit
intermediate BF16 rounding allowance defined above. Both paths are checked
before timing, with separate correctness fields and per-path absolute error and
budget-fraction evidence. CPU regressions reproduce the former failure with an
exact cancellation fixture and seed 1234; those cases and seed 17 pass after
repair. All 24 injections across either or both paths reject missing bias/ReLU,
shared offset, zero output, non-finite values and malformed shape/dtype before
timing or publication. A separate invalid-reference control rejects NaN.

The authored-guide rendering test now includes the generated bibliography's
URL/anchor mapping, allowing the lab's official numerical-accuracy citation
while retaining narrative and link parity checks. The complete local suite
passes 546 tests, all five course validators pass, and generated HTML matches
canonical source. This evidence uses local CPU PyTorch 2.13; target PyTorch 2.14,
CUDA and H100 execution remain unverified. No original executable-review finding
awaits a numerical-policy decision.

The executable review restores the existing equivalent-training-update and
inference-evidence contracts. Labs 27 and 31 share one check for finite, present
gradients, gradient agreement normalized by the reference tensor's maximum
magnitude, and exact reconstruction of each plain-SGD update from the initial
parameters. At least one parameter must change. Loss and whole-parameter
closeness alone cannot establish that backward and optimizer work occurred.

Inference Lab 08 encloses cache construction, numerical checks, warmup and
timing in inference mode, and rejects non-finite prompt or continuation errors.
The standalone validator template and five copies compile Python in memory and
disable bytecode output in help subprocesses, eliminating shared temporary
bytecode files. No dependency, CLI, result schema or target qualification changed.

CPU fault injection first reproduced acceptance of an omitted training update,
retained autograd tensors, NaN comparisons and persistent validator bytecode.
The corresponding repaired checks pass with matched positive controls in the
existing PyTorch 2.13 environment. This proves the local numerical/control
semantics, not PyTorch 2.14 installation, CUDA Graph replay or H100 timing.

The request to use the alignment workflow and repair gaps restores the existing
REQ-002/008/009/010 contract; it adds no new course, dependency, supported platform
or live-performance promise. The current ready pair was inspected before repair.
The five quick starts now link to their version record, separate candidate
installation from qualified execution and apply submitter-side privacy guidance.
The CUDA direct-build path explicitly requires an allocated H100 and a qualified
development image. A repeated closing-invitation wording error is corrected.

The TensorRT-LLM/Triton launcher now passes Lab 30's canonical --output-dir and
exports one validated run ID for server/client correlation. The guide describes
the actual directory and filenames. No API, model, numerical workload, endpoint,
credential, dependency or infrastructure configuration changed.

All five validators and 502 pytest tests pass, including 15 new regressions.
Thirteen finding-specific checks failed before repair; the two existing
invalid-response controls remained green. The executable fixture tests the real
shell-to-client handoff, with HTTP replaced locally; it does not run a server.
All 115 Python files pass Ruff/check/format, all configured Markdown passes and
all 32 shell launchers/scripts pass Bash syntax and ShellCheck. All five HTML
pages are regenerated with exact source parity. Independent nested code review
and changed-scope security review found no outstanding issue in the repairs.

A task-start comparison confirms no removed files or changes to the 73 lesson
bodies, syllabi, lab/diagram mappings, shared style or 104 diagram definitions.
No speculative performance or architectural change was needed. Publication
records retain pending clean target environments, CUDA/compiler/engine activation,
H100/Slurm execution and full-page browser verification. Delivery remains
implemented, not live verified.

#### Numerical Acceptance Alignment

The final topic/source review identified acceptance checks that could certify
non-finite results. Training Lab 02 now checks every sampled reference and
candidate gradient before aggregation. Training Lab 19 maps a non-finite
tensor, norm or relative error to a failing value and lets every rank reach
the collective MIN verdict before exiting. Inference Lab 18 validates every
prompt's final real-token logits and norm calculation before taking the
maximum error. Optimizations Labs 02 and 05 also require finite scalar sums.
The existing tolerance values and result identities are unchanged.

CPU fault controls cover non-finite values in different parameter, prompt and
metric positions, invalid references, overflowing norm/error arithmetic,
missing gradients and the actual acceptance/publication paths. All 87 focused
controls pass within the 660-test shared suite. These are local source/CPU
results, not proof of live NCCL rejection propagation or H100 execution.

<!-- /FEATURE: FEAT-007 -->

<!-- FEATURE: FEAT-008 reqs=REQ-009 status=ready delivery=implemented priority=P0 version=7 -->
### FEAT-008: Long-form teaching and content integrity

#### Requirements Covered

- REQ-009: Complete educational explanations and content integrity.

#### Context Evidence

Engineers need mechanisms and interpretation, not lists of topic names.

#### Design Details

Every lesson connects definitions, prerequisites, purpose, mental model and mechanism, then links to its practical labs. The labs own H100 implications, integrated worked practice, trade-offs, evidence, failure and review under FEAT-021. Publish complete text and explicit visual homes across the lesson and lab sections. Correct the reviewed causal and numerical statements, explain missing first-use terms and loss/sampling/GRPO mechanics, and match lab instructions to implemented capabilities. Preserve advanced concepts through concrete, explicitly labeled learner extensions rather than implying unsupported CLI options or measurements.

#### Selected Option

Connected long-form instruction plus integrated runnable practice.

For the catalog-wide editorial alignment, retain the shared light textbook
style and complete teaching route. Correct grammar, terminology, ambiguous lab
references and factual wording at their canonical source, then rebuild all five
pages. Preserve code, commands, lesson identities and numerical criteria unless
a separately proven mismatch requires a scoped correction. Review static
formatting, source parity and diagram assets separately from full-page browser
and live GPU evidence.

#### Alternatives Considered

Terse checklist-only lessons do not teach causal reasoning.

#### Implementation Boundaries

Do not reduce substantive instruction when simplifying presentation.

#### Test-First Success Criteria

- TDD-001: All lessons satisfy substantive stage/depth checks.
- TDD-002: Every current lab and detailed visual remains reachable and synchronized.

#### Validation Plan

Review lesson text, diagrams, source listings and guide links.

#### Test Plan

Use direct current-content assertions, not provenance or comparison matrices.

#### Evaluation Plan

Confirm learners can explain what, why, how, when, limitations and evidence.

#### Rollout And Rollback

Regenerate pages atomically and preserve educational assets.

#### Done Definition

Full narrative and all current educational assets are published.

#### Implementation Evidence

The 2026-09-08 mechanism revision reviews all 73 lessons across the five
courses. Their Mechanism sections now explain the actors, sequence, state
changes and completion conditions in connected prose, with descriptive
subheadings for longer topics. Existing examples, formulas, numerical gates,
prerequisite routes and practical capabilities remain. Clarifications include
host/device timing, token-weighted accumulation, allocator accounting, shared
KV ownership and the distinction between supplied experiments and extensions.

The catalog-wide editorial pass reviewed all 73 lessons, 94 complete lab guides,
front and back matter, supporting runbooks and 108 diagrams. Corrections clarify
Amdahl latency/speedup arithmetic, transfer-lab scope, normalized error metrics,
DDP gradient communication, accumulation semantics, quantized resident memory,
startup TTFT, KV-policy controls and rectangular-transpose addressing. Optional
SM90a packaging and fixed kernel fixtures are described without implying
unsupported instruction requirements or command options. Glossary definitions,
prose spelling, code formatting, submitting-shell masks and accessible diagram
text follow the established course conventions. No executable code changed.

All 72 lessons retain the 15-stage teaching sequence and all 87 numbered labs. Corrected roofline/divergence assumptions, resource arithmetic, packing boundaries, token-weighted/DDP loss, GRPO and sampling mechanics, first-token/latency accounting, and custom-kernel scope. Expanded operational and mathematical definitions in lessons and glossaries. Supplied baselines and concrete learner extensions are explicitly distinguished for graphs, input pipelines, shape padding, KV mechanics, serving, CUTLASS and RMSNorm. Official references support the corrections; no learner lab code or environment was changed.

The individual-course alignment pass reconciles syllabuses, runbooks and lessons with actual tracked-parameter, recomputation, LoRA, phase-timing and capstone evidence. It preserves LoRA's bounded held-out token/digest observations while distinguishing adapter persistence and broader quality evaluation as extensions. Conditional live pipeline-parallel serving is identified accurately without production-scale claims.

#### Verification Evidence

Mechanism revision verification on 2026-09-08: all 748 offline tests, five
standalone validators, exact generated-source parity, changed-source
Markdown/Python lint and formatting, specification validation and whitespace
checks pass. Independent read-only prose reviews cover all 73 mechanisms.
The task-start comparison confirms unchanged lesson identities, all 94 lab
guides and executables, commands, metadata, syllabi, dependencies and shared
styles, with no original file removed. The installed course-skill checker
reports identical publication-format failures on current and task-start pages;
all five baseline rebuilds match their original HTML hashes. Its source
allowlist and byte checks pass. Browser and target-runtime evidence remain
pending.

All 573 shared tests and all five standalone validators pass. All five rebuilt
pages pass exact source parity and the course skill's bounded HTML/source
checker. Configured Markdown lint covers 168 files; all 153 shell example blocks
pass syntax checks. Six documented CPU KV-policy commands run successfully,
with restart and revision comparisons preserving baseline tier capacities.
Independent read-only rechecks closed all reported editorial findings.

All 108 SVGs were rendered at 320 pixels and at up to 1200 pixels with their
applicable shared styles; narrow overview sheets and the three changed assets
were inspected. The parent reviewed every source delta and preserved all 188
code, launcher, test, dependency, stylesheet, metadata and syllabus files in the
protected baseline set. No file was removed. Existing bytecode caches predate
this task; no new validation bytecode was created.

This is source, editorial, CPU and asset-level evidence. Browser policy blocked
full-page inspection, so desktop and 390-/320-pixel layout, keyboard and reflow
checks remain pending. No new installed-target, CUDA build, sanitizer, Nsight,
Slurm, NCCL, engine or H100 performance evidence is claimed.

<!-- /FEATURE: FEAT-008 -->

<!-- FEATURE: FEAT-009 reqs=REQ-002,REQ-009 status=superseded delivery=unassessed priority=P0 version=4 -->
### FEAT-009: Presentation allocation mechanism

#### Requirements Covered

- REQ-002: Consistent practical and publication-safe teaching contract.
- REQ-009: Complete educational explanations and content integrity.

#### Context Evidence

The active publication contract is defined by FEAT-011.

#### Design Details

Do not use this allocation mechanism in the current renderer.

#### Selected Option

FEAT-011 supplies concise metadata and a clean learner presentation.

#### Alternatives Considered

Exposing instructional planning arithmetic is unnecessary for the learner.

#### Implementation Boundaries

Keep educational text and diagrams intact.

#### Test-First Success Criteria

- TDD-001: FEAT-011 tests enforce the active presentation contract.

#### Validation Plan

Use FEAT-011 validation.

#### Test Plan

Use FEAT-011 regression checks.

#### Evaluation Plan

Evaluate the current course experience.

#### Rollout And Rollback

No compatibility path is retained.

#### Done Definition

Current content follows FEAT-011.

#### Implementation Evidence

Superseded by the active clean-publication design.

#### Verification Evidence

No runtime or publication approval is implied.

<!-- /FEATURE: FEAT-009 -->
<!-- FEATURE: FEAT-010 reqs=REQ-003,REQ-005,REQ-006,REQ-007,REQ-008,REQ-009 status=ready delivery=implemented priority=P0 version=2 -->
### FEAT-010: Complete the reviewed laboratory mechanisms and evidence

#### Requirements Covered

- REQ-003: Accurate Fundamentals mechanics.
- REQ-005: Training FLOP accounting.
- REQ-006: Inference allocator and scheduler experiments.
- REQ-007: CUDA arithmetic-intensity sweep.
- REQ-008: Honest evidence.
- REQ-009: Complete educational explanations.

#### Context Evidence

The latest review identified inflated training FLOP accounting, a mislabeled
unchunked scheduler, aggregate-only paged-KV accounting, a missing lane-work
comparison, and a fixed-work async-copy lab advertised as an intensity sweep.

#### Design Details

Correct the training numerator to the GEMMs actually required by autograd.
Implement a physical-block paged-KV lifecycle with atomic capacity rejection.
Compare non-preemptible full prefill with bounded chunks using explicit modeled
service time, conserved work, fixed arrivals, and event traces. Add an executable
lane-work model to Fundamentals Lab 11 alongside its measured grid-tail probe;
clearly separate the model from real branch measurements in Custom Kernels Lab 06.
Give CUDA Lab 08 a bounded arithmetic-work sweep, independent CPU reference,
logical intensity accounting, partial-tile coverage, and repeated timing.
Expand lesson examples and learner evidence worksheets, preserving old material.

#### Selected Option

Repair each causal source and its educational contract in place. Keep Python
mechanics simple and GPU claims contingent on the matching target experiment.

#### Alternatives Considered

Changing only labels would leave missing lifecycle and sweep experiments.
Treating Python masking as measured warp divergence would teach a false model.

#### Implementation Boundaries

No cluster provisioning or publication. No cross-course runtime imports.
Do not claim CUDA compilation, browser QA, or H100 qualification from CPU tests.

#### Test-First Success Criteria

- TDD-001: Actual CPU autograd GEMM counts match the training FLOP formula.
- TDD-002: KV growth preserves mappings, freed blocks are reused, and failed
  reservations leave state unchanged.
- TDD-003: Full prefill is never split; chunked work obeys the budget; elapsed
  service accounts for long iterations; both policies conserve identical work.
- TDD-004: Uniform and mixed lane models preserve useful work with different
  modeled utilization, without claiming measured hardware branch efficiency.
- TDD-005: CUDA sweep inputs, workload accounting, independent reference,
  partial tiles, source parity, and target CTest coverage are explicit.

#### Validation Plan

Run focused pure/CPU regressions, all five validators, shared tests, lint,
source parity, content integrity and privacy checks. Leave unavailable lanes pending.

#### Test Plan

Include negative sizes, capacity failures, invalid schedules, arrival gaps,
gradient/no-gradient cases, and bounded CUDA parameter validation.

#### Evaluation Plan

Trace each experiment from objective through numerical example, observable
result, interpretation, failure mode, and review answer.

#### Rollout And Rollback

Regenerate all HTML atomically. Preserve educational material and unrelated work.

#### Done Definition

Five reviewed gaps are implemented and source-tested, all courses remain
consistent, and outstanding installation/runtime/live gates are disclosed.

#### Implementation Evidence

Training Lab 31 now counts its actual forward and weight-gradient GEMMs,
records the 4TH² numerator and gradient flag, and labels utilization as a
matmul-only lower bound. Inference Lab 27 tracks physical block identities,
atomic rejected growth, release and reuse while retaining the demand worksheet.
Lab 28 compares non-preemptible full prefill with 64- and 256-token chunks,
charges occupied service quanta, and reports arrival-relative first-token
times, event traces, conserved work and request completion.

Fundamentals Lab 11 adds the executable common-loop lane-work comparison,
clearly separated from its measured uniform Triton grid-tail experiment and
the optional real divergence-kernel follow-on in Custom Kernels Lab 06.
CUDA Lab 08 sweeps 0, 8, 32 and 128 FMAs, accepts a bounded work override,
checks both variants against independent CPU FMA references with NaN sentinels,
includes partial tiles and CTest edge cases, and discloses its single-block
scope and logical-byte convention. The larger profile is deliberately bounded.

All five courses publish worked lab-mechanism guides through the shared HTML
contents. Lessons, source listings, launch procedures, benchmark
worksheets and README links are synchronized. The
existing 72 lessons, 87 labs and 97 diagrams remain; educational assets remain complete.

#### Verification Evidence

The initial ten targeted tests failed before source changes and passed after
implementation. The final 129-test suite includes 21 new autograd counting,
allocator lifecycle/failure, scheduler arrival/conservation, lane-model,
invalid-peak, CUDA source-contract and published-guide regressions. Thirty
deterministic scheduler workloads across three policies and a 100-event
allocator sequence exercise invariants. Five validators, atomic HTML parity,
Ruff/format, configured Markdown, shell syntax/ShellCheck, content integrity, privacy
and whitespace checks pass. No sensitive-pattern matches were found in the
publishable material; unrelated working-tree changes remain untouched.

CPU autograd proof used the existing PyTorch 2.13 environment, not a qualified
2.14/H100 installation. CUDA compilation, CTest, sanitizers, engines, Slurm,
one-/two-H100 execution and fresh desktop/390-pixel browser inspection remain
pending. These limitations prevent verified delivery or publication approval;
modeled scheduling/lane values and logical intensity are not performance claims.

<!-- /FEATURE: FEAT-010 -->
<!-- FEATURE: FEAT-011 reqs=REQ-001,REQ-002,REQ-009 status=ready delivery=implemented priority=P0 version=2 -->
### FEAT-011: Light digital-textbook publication

#### Requirements Covered

- REQ-001: Standalone canonical course packages.
- REQ-002: Clean, consistent publication experience.
- REQ-009: Complete educational content without provenance clutter.

#### Context Evidence

The user prefers the side-panel TOC and light colors. Courses are new and do not need publication history or backward-compatibility support.

#### Design Details

Refine the existing native HTML/CSS into a light digital textbook: warm off-white canvas, white reading surface, mint/blue accents, restrained callouts, readable line length, sticky numbered side TOC, focus/target states, mobile and print rules. Keep the short estimated-hours banner label but remove all time calculations and allocation tables. Use neutral course and visual manifests with no edition provenance. Remove course-history, learning-record and source-coverage artifacts; keep their substantive technical limitations in lessons. Official/vendor references remain at the end.

#### Selected Option

Fixed dependency-light Python generator, semantic HTML, inline CSS/SVG and no JavaScript, CDN or theme framework.

#### Alternatives Considered

Dashboard/card-heavy layouts fragment long reading. Plain unstyled HTML loses navigation and hierarchy. Adopting a Sphinx framework adds unnecessary build/runtime assets. Apply only the useful documentation/textbook layout principles.

#### Implementation Boundaries

Preserve all lesson prose, executable labs, detailed diagrams and safety qualifications. No learner-lab or environment changes; remove the unused benchmark-client detector from the general tooling preflight along with its documentation. No compatibility aliases, infrastructure or publication actions. Internal organization research is irrelevant to this visual task.

#### Test-First Success Criteria

- TDD-001: Published pages have no time formula/table, provenance comparison, source-coverage guide or stale links.
- TDD-002: Exact lab source, full lesson text, neutral visual manifests and accessible navigation remain enforced.
- TDD-003: Light colors, restrained reading width, sticky sidebar, mobile containment, focus contrast and print rules are checked.

#### Validation Plan

Run five validators, shared tests, Ruff, Markdown, shell, privacy and HTML parity. Review desktop/390 layouts only through permitted tools; do not bypass an unavailable browser surface.

#### Test Plan

Replace obsolete history/allocation assertions with direct current-content and publication-boundary checks. Add contrast and no-external-asset checks.

#### Evaluation Plan

Confirm content is easy to read and navigate while all meaningful teaching remains accessible.

#### Rollout And Rollback

Back up the exact removable metadata artifacts outside published roots; regenerate pages atomically. Do not create wrappers or duplicate publication paths.

#### Done Definition

All five courses share the light textbook presentation, clean learner navigation and end references, without formulas or historical scaffolding.

#### Implementation Evidence

Implemented shared light CSS and generation with 74ch reading width, sticky side TOC, restrained callouts, current course metadata, core/optional lab labels, neutral visual manifests and official end references. Removed time calculations, publication-history guides, coverage tables and historical records with a private recovery archive. Preserved 72 lessons, 87 numbered labs and 97 SVGs; detailed diagrams changed only palette plus one current benchmark-tool label. Preserved advanced multi-LoRA and multimodal qualifications in Inference Lesson 14. Removed the unused benchmark-client check and its references without introducing aliases.

#### Verification Evidence

Research: [PyData layout](https://pydata-sphinx-theme.readthedocs.io/en/stable/user_guide/layout.html), [GOV.UK layout](https://design-system.service.gov.uk/styles/layout/), [W3C page styling](https://www.w3.org/WAI/tutorials/page-structure/styling/), and [Diataxis](https://diataxis.fr/start-here/). These support sidebar/article separation, readable line length, responsive accessibility and distinct learning/reference needs. Visual preference is a project design judgment, not a universal best-style claim. Validation: 141 shared tests and all five course validators pass; exact HTML/source parity, Python help/compilation, Ruff/check and formatting (95 Python files), configured Markdown lint, shell syntax, ShellCheck, publication-safety scans and Git whitespace checks pass. Compared all 79 Python lab/helper files against the task-start recovery archive: unchanged. All 43 detailed SVGs preserve geometry and explanation aside from palette and the benchmark label. Contrast checks cover primary text/accent combinations against the light surfaces. Bounded read-only final review found an obsolete tool check, now removed and regression-tested. Desktop and 390-pixel browser rendering remain pending after unavailable/blocked browser access; no alternative access workaround was used. Installed-target environments, CUDA build/runtime, engines and live H100 qualification are unchanged and pending.

<!-- /FEATURE: FEAT-011 -->
<!-- FEATURE: FEAT-012 reqs=REQ-002,REQ-009 status=ready delivery=implemented priority=P0 version=4 -->
### FEAT-012: Contextual, accurate diagrams and uncluttered navigation

#### Requirements Covered

- REQ-002: Consistent labels, contextual figures and responsive presentation.
- REQ-009: Complete educational text and visual integration.

#### Context Evidence

The shared sidebar already provides navigation. Repeated return links and
diagram-label disclosure controls add unnecessary UI. Diagram review also found
that title-inferred layouts can imply incorrect containment or causal links.

#### Design Details

Keep punctuation-free labels, one inline home per diagram, secondary links,
the 1800px frame, 240px sidebar, 88ch prose and fit-to-width SVGs. Remove repeated
Back to contents links and both diagram-reading disclosure variants, including
unused styling. Preserve SVG title/description associations and visible captions.

Declare the relationship layout in each overview visual plan and validate it
strictly. Use non-directional comparisons for independent alternatives,
containment only for nested entities, proper axes for workload matrices, and
explicit concurrency for overlap. Review every overview and detailed SVG against
its lesson and official technical sources; correct labels, arithmetic, arrows,
boundaries and directly contradictory adjacent prose without deleting topics.

For the Lesson 1 host/device timeline, separate CPU submission and waiting
from execution in one GPU stream. Label submission and event completion
distinctly. Place an explicit reading guide beside the drawing: arrows show
order; box heights and gaps do not show how long operations take. Preserve
the host-timer scope and distinguish event completion from copying results.

#### Selected Option

Native HTML/CSS with explicit semantic SVG layouts and no extra navigation or
transcript controls. Keep all 72 lessons, 87 labs and 97 diagrams.

#### Alternatives Considered

Title heuristics cannot establish a technical relationship. Removing every
accessible description would harm nonvisual access. FEAT-008 owns the additionally authorized editorial findings. Learner-code
expansion is not required when a baseline and a concrete extension teach the
concept accurately.

#### Implementation Boundaries

No dependency, learner runtime, cluster or publication changes. Preserve
unrelated edits and course detail. Do not bypass blocked browser access.

#### Test-First Success Criteria

- TDD-001: All pages omit return links and diagram-reading controls while
  sidebar links, answers, lab source disclosures and SVG accessibility remain.
- TDD-002: Overview layouts are explicit, valid and not inferred from titles.
- TDD-003: Diagram regressions preserve corrected relationships and quantities;
  all figures remain inside their declared lesson after the assigned field.

#### Validation Plan

Run targeted regressions, five validators, shared tests, exact source parity,
lint and privacy checks. Rasterize and inspect all SVG assets with permitted
local tooling. Browser rendering remains a separate lane.

#### Test Plan

Include invalid layout metadata, removed-control absence, title/description
association, figure counts/placements and targeted semantic diagram assertions.

#### Evaluation Plan

A learner can interpret the relationship correctly in the adjacent explanation
without navigating away or mistaking alternatives for a causal sequence.

#### Rollout And Rollback

Regenerate all pages atomically; preserve a task-scoped recovery snapshot.

#### Done Definition

The simplified controls and corrected diagrams are consistent across all five
courses. Evidence does not overclaim browser or H100 validation.

#### Implementation Evidence

The Lesson 1 host/device timeline now uses distinct submission and completion
arrows, separate timer start/stop boxes and an explicit reading guide. It says
that box heights and gaps do not show operation durations. The caption retains
submission versus execution, one-stream order, host-timer scope and the need
for a separate result copy. The existing figure identity and Lesson 8 link stay
stable.

The 2026-09-08 mechanism revision audits all 109 current figures and revises
all 54 overview captions and layouts plus five detailed diagrams. Compact
vertical flows, nested containers and explicit time axes retain readable label
scale. The host/event timeline separates submission from completion; transfer
cycles show when buffers can be reused; the DDP flow separates bucket readiness,
communication and update; and the KV-tier tree labels reuse, restore and
recompute branches. Disaggregation shows forward KV handoff, with separate
coordination described in its caption. Native titles, descriptions, contextual
homes and all figures remain.

Removed repeated return links and both diagram-reading disclosure variants from all five generated pages and their shared renderer/styles. Native SVG title/description associations, visible captions, sidebar navigation, answer keys and source disclosures remain. All 54 overview diagrams declare a validated relationship layout rather than inferring one from the title. Reviewed all 43 detailed diagrams and corrected residual/data-flow arrows, cache and token boundaries, latency intervals, physical KV identity, workload axes, scheduling and topology relationships. Regenerated all five pages atomically; preserved 97 inline figures, 72 lessons and 87 labs.

#### Verification Evidence

The revised host/device SVG was rendered and inspected at 560- and 320-pixel
widths: labels fit, connectors avoid text and the duration note is legible.
The GPU Fundamentals page was rebuilt; all five validators and 45 focused
contextual/diagram tests pass. This is source and asset-rendering evidence;
full-page browser review remains pending under the existing URL restriction.

Mechanism revision verification on 2026-09-08: all 59 changed SVG figures
were directly rendered and inspected. Review corrected a roofline label
collision and a copy-completion label placed after dependent computation;
the latter has a failing-before/passing-after regression. Overview scale,
semantic direction, accessibility and figure/source parity checks pass within
the 748-test offline suite. Full-page browser inspection was blocked by URL
policy; no alternate route was used. Asset-level inspection does not establish
page reflow, keyboard behavior or target-runtime qualification.

On 2026-09-04, all five validators, 195 shared tests, exact source parity, Ruff/format, Markdown, Python compilation/help, launcher syntax and publication-safety checks pass. All 97 SVG assets were rasterized locally and visually reviewed; final label/connector repairs were rendered and inspected again. Report-only final review found no remaining actionable issues in the repaired surfaces. Desktop/390px browser rendering was not performed because browser access was unavailable; no alternate browser or serving route was used. Browser and live H100 qualification remain pending.

<!-- /FEATURE: FEAT-012 -->
<!-- FEATURE: FEAT-013 reqs=REQ-002,REQ-006,REQ-008,REQ-009,REQ-010 status=ready delivery=implemented priority=P0 version=2 -->
### FEAT-013: Authored lab guides and explicit lesson integration

#### Requirements Covered

- REQ-002: Consistent accessible practical presentation.
- REQ-006: Honest inference mechanics and service-metric boundaries.
- REQ-008: Separate numerical, source and target evidence.
- REQ-009: Complete educational explanations.
- REQ-010: Lab-specific teaching guides and stable identities.

#### Context Evidence

Before this implementation, lab cards repeated four generic sentences and
derived human titles and some launchers from filenames. Source review found
orphan practice assignments,
unsupported exercise promises and synthetic recurrence metrics named like
language-model token latency.

#### Design Details

Retain the existing Python standard-library renderer, light CSS, Markdown and
standalone course packages. Store one authored guide at
reference/labs/SOURCE_STEM.md. Its H1 is Lab NN: Human-readable title and owns
the display title. The source filename owns the stable number. Extend each
reference/course.json lab record to path, optional and explicit lessons.
Reject missing/extra guides, invalid IDs, duplicate metadata and invalid lessons.

After each title place an unabridged purpose paragraph. Use the same seven
sections: Before you start; Concepts and code path; Practice;
Check your results; Investigate the behavior; If something goes wrong;
Takeaways and next step. Markdown commands are authored for the actual
experiment, not chosen by filename heuristics. Explain shared helpers and
state/data flow where relevant. Keep complete source in a separate disclosure.
Use an easy-to-read single-column guide, not a four-cell generic card.

The renderer uses each guide's title in the lab heading, TOC and lesson practice
links. Generate explicit practice-lab links from metadata; repair contradictory
course prose and runbooks. Preserve detailed mechanism walkthroughs and link to
them from relevant guides without replacing them with summaries.

Inference Lab 25 remains a synthetic prompt-projection/recurrent-operator cost
experiment. Rename misleading TTFT/TPOT/token-rate fields to actual operator
boundaries and units; preserve declared recurrence work instead of presenting
it as a real LM decode loop. Actual client token metrics remain in serving labs.
No compatibility aliases. State finite-only and scoped numerical gates honestly;
extensions may add stronger proof but are not presented as implemented behavior.

#### Selected Option

One course-local Markdown guide per source, explicit lesson membership in
existing metadata, one shared fail-fast rendering/validation contract.

#### Alternatives Considered

Repeated generic cards are cheap but do not teach each experiment. A single
large JSON prose object complicates authoring. A new course platform adds
runtime dependencies and publication complexity without serving this request.
The fixed stack is retained; no AI/agent platform or model selection is involved.
The publishing workflow is deterministic; existing model labs are fixed
experiments, not autonomous agents.

#### Implementation Boundaries

Courses only. Preserve standalone environments and all 87 numbered labs. No
cluster configuration, model downloads, credentials, dependency upgrades or
external publication. Limit executable changes to proven semantic/measurement
defects; do not expand every extension into a new GPU feature. Prior browser
access restrictions remain in force.

#### Test-First Success Criteria

- TDD-001: Every source has one title/guide and valid explicit lesson mappings;
  both directions of page navigation use that title and stable number.
- TDD-002: All seven sections and substantive introduction are preserved in
  generated HTML; generic cards and heuristic titles/commands are absent.
- TDD-003: Invalid/missing/duplicate guides, mismatched titles/numbers and
  lesson associations fail before publication.
- TDD-004: Synthetic operator metrics contain no unsupported token-latency
  claim; pure/static tests guard their work and boundary definitions.
- TDD-005: All five standalone validators and shared tests preserve full
  source parity, privacy, known numerical qualifications and diagram counts.

#### Validation Plan

Run focused negative/positive guide tests, all five validators, shared pytest,
Ruff/format, Markdown, shell syntax and publication safety. Review authored
guides against actual source. Browser, CUDA, engine and H100 evidence remain
separate and pending when unavailable.

#### Test Plan

Implement the metadata/parser/rendering slice with a representative simple and
complex guide, then populate all 87 sources. Include acronym/title identity,
multiple lessons, optional labs, engine prerequisites, one-/two-node variants,
command fences, source-listing equality and duplicate/extra guide rejection.

#### Evaluation Plan

Apply system-design-rules selectively: one owner per title/identity, rebuildable
HTML, explicit execution boundaries, fail-fast authoring errors, private
evidence and no new runtime. An independent read-only review samples every
course and checks corrected mismatches against source.

#### Rollout And Rollback

Preserve a scoped task-start snapshot. Author and validate all guides before
atomically regenerating pages. Keep useful walkthroughs and unrelated edits.
No compatibility path or externally visible service rollout is required.

#### Done Definition

All 87 labs have detailed, accurate, consistently rendered guides and appropriate
lesson associations; identified teaching gaps are repaired and evidence lanes
are honestly recorded.

#### Implementation Evidence

Implemented 2026-09-04: 87 authored course-local lab guides, explicit lesson
memberships, canonical number/title identity, bidirectional practice links and
seven complete learner-facing sections. Updated the shared HTML renderer,
single-column guide styling, validator template and all five standalone copies.
Preserved all 87 executables, 72 lessons and 97 diagrams. Course prose, READMEs
and runbooks now distinguish supported experiments from learner extensions.

Inference Lab 25 reports synthetic projection/recurrent operator work and
directly times the joined interval without token-latency aliases. Only this
learner executable changed for the lab-guide implementation. Review corrections
clarify reference precision, resource-estimate limits, peak versus gradient
memory, SFT supervision, recomputation timing and optional cluster-build setup.

The individual-course alignment pass adds source-level regressions for the repaired setup, evidence and build boundaries. The standalone privacy-text validator now includes C++ .hpp/.h headers; one directly testable owner retains the existing scan patterns and is duplicated consistently across all five standalone packages.

#### Verification Evidence

Source/static checks passed 2026-09-04: five validators and 228 shared tests;
all five HTML pages regenerated and exact narrative/source parity verified.
The 33 authored-guide tests include negative controls for missing/orphaned
guides, invalid identities and lesson mappings, stale rendered narrative,
unsupported links, command syntax, and synthetic measurement boundaries.
Scoped Ruff check/format, repository-configured Markdown checks, 33 runbook
Bash fences and whitespace checks pass. Supplemental sensitive-pattern scans
found no matches in 108 publishable files. Independent read-only mapping
reviews found no remaining actionable issues in the repaired examples.

No installed-environment qualification, CUDA build, CTest, sanitizer, live
engine or H100 execution was performed. Desktop and 390-pixel browser review
remains pending permitted access. Delivery is implemented, not verified;
source completeness does not establish runtime correctness or performance.

Individual-course alignment verification on 2026-09-04: all five validators and 268 tests pass, including 40 new source/CPU regressions and host-only C++20 argument tests. Ruff/check and format cover 100 Python files; configured Markdown, 31 shell syntax/ShellCheck checks, 134 command-fence syntax checks, and source parity pass. Current publication-safety scans cover 354 course files. The 59 style, diagram and inventory source files are byte-identical to the alignment checkpoint. Final independent read-only reviews report no remaining actionable source findings in the repaired surfaces. CUDA build/CTest, sanitizers, profilers, environment qualification, live H100 and browser review remain pending; delivery is implemented, not verified.

<!-- /FEATURE: FEAT-013 -->
<!-- FEATURE: FEAT-014 reqs=REQ-002,REQ-009,REQ-010 status=ready delivery=implemented priority=P0 version=1 -->
### FEAT-014: Prerequisite-first competency progression

#### Requirements Covered

- REQ-002: Consistent practical course presentation.
- REQ-009: Complete explanations and prerequisite-first lesson sequence.
- REQ-010: Accurate lab identities and lesson integration.

#### Context Evidence

Individual sequence review found memory resources defined after occupancy,
live inference clients before server lifecycle, and CUDA safety tools after
several optimization experiments. Applied training objectives interrupted the
core single-device and distributed progression.

#### Design Details

Reorder complete lesson blocks without abridging teaching fields. Fundamentals
introduces memory before occupancy and basic timing before its first benchmark.
Optimizations diagnoses local imbalance before distributed scaling. Training
builds correctness, state/memory, local optimization and distributed competence
before applying SFT/LoRA and GRPO. Inference establishes safe artifacts,
generation, sampling, cache/workload, engine lifecycle and metrics before
scheduling and advanced optimization. Custom CUDA teaches validation after the
first correct kernel and places optional Hopper/Tile material after the core
capstone; resource reasoning precedes tail diagnosis.

Each syllabus lists every lesson in reading order with its competency and
practice activity. Explain that lab identifiers are stable references, not a
separate required execution order. Preserve meaningful optional branches and
clearly identify read-only previews and later experiment revisits. Update
prerequisite bridges, course-local lesson references, lab memberships and both
visual placement manifests together. HTML navigation follows canonical lesson
order and retains title-based anchors.

#### Selected Option

Existing Markdown, explicit numeric lesson associations and shared renderer,
with focused semantic sequence regressions. No new platform or metadata layer.

#### Alternatives Considered

Numeric lab order is not the pedagogical order. Wholesale renumbering of
executable files adds churn without improving comprehension. A generic learning
graph engine would duplicate the authored curriculum unnecessarily.

#### Implementation Boundaries

Preserve all 72 lessons, 87 lab sources/guides and 97 diagrams. No learner
runtime, environment, launcher, dependency, cluster or styling changes.
No backward-compatibility aliases or publication of historical comparisons.

#### Test-First Success Criteria

- TDD-001: Required concept precedes dependent lesson in each course.
- TDD-002: Syllabus order, lesson numbering and complete HTML TOC agree.
- TDD-003: Lab and diagram associations retain their semantic lesson titles
  after reordering; changed practice assignments are explicitly tested.
- TDD-004: Previews and advanced branches do not require unexplained execution.
- TDD-005: Full narrative, lab sources, visual assets and privacy boundaries
  remain intact.

#### Validation Plan

Run sequence regressions, all five validators, the shared tests, Markdown and
Python checks, source parity, preservation and privacy checks. Independent
read-only review evaluates the resulting progression, not just numbering.

#### Test Plan

Use title-based prerequisite edges, explicit expected order, negative controls,
syllabus row/TOC agreement and lab/visual semantic mapping tests. Existing
completeness and source-parity tests guard against dropped materials.

#### Evaluation Plan

A learner can identify what they already know, the new competency, the current
practice and the evidence needed before proceeding. Optional qualifications
remain distinct from core completion.

#### Rollout And Rollback

Keep a task-scoped source snapshot; update canonical sources as one coherent
change and regenerate HTML atomically. Do not retain a second curriculum path.

#### Done Definition

All five courses have reviewed prerequisite-first progression, synchronized
syllabuses/navigation/practice/visuals, preserved content and passing source
checks. Browser and live H100 evidence remain separate.

#### Implementation Evidence

Implemented 2026-09-04: reordered complete lessons in all five courses and
expanded every syllabus into an explicit lesson/competency/practice route with
readiness checkpoints. Updated bridges, previews, distributed-preflight timing,
README/runbook guidance, lesson memberships and visual placements. CUDA safety
practice now reuses the completed vector lab; optional Hopper/Tile branches
follow core acceptance. Packing-plan/mask checks remain an early mechanics lab;
loss/gradient checks are assigned only to the implemented masking lab after the
correct-update lesson. Preserved all 72 lesson identities, 87 lab identities,
core/optional status and 97 diagrams. Generated HTML remains self-contained.

#### Verification Evidence

All five validators and 379 current shared tests pass, including 33 focused
sequencing regressions. The initial 19 sequence/scaffolding checks failed
against the pre-change curriculum and passed after repair. Existing substantive
editorial assertions now resolve semantic lesson titles instead of stale
positions without weakening their arithmetic or implementation-scope checks.
Changed-Python Ruff/check/format, configured Markdown, source parity, privacy
and whitespace checks pass. A snapshot comparison preserves all diagram
semantic homes and confirms 43 detailed SVG assets plus the shared stylesheet
are byte-identical. Three bounded read-only reviews found and rechecked early
distributed-preflight conflicts and confirmed their repair. Concurrent
workspace edits were preserved; sequencing did not change runtime code.
Browser inspection, installed-target qualification, CUDA and live H100 evidence
remain separate and pending. Delivery is implemented, not target verified.

<!-- /FEATURE: FEAT-014 -->
<!-- FEATURE: FEAT-015 reqs=REQ-002,REQ-007,REQ-009,REQ-010 status=ready delivery=implemented priority=P0 version=1 -->
### FEAT-015: Definition-first course entry and complete conceptual bridges

#### Requirements Covered

- REQ-002: Consistent accessible inline teaching and public-safe references.
- REQ-007: Kernel identity, dispatch, library reuse and distribution guidance.
- REQ-009: Complete prerequisite-based explanations and topic coverage.
- REQ-010: Accurate introductory lab guides and implementation boundaries.

#### Context Evidence

Current opening lessons introduce artifact audits, training-stage taxonomies
and library escalation before their foundational definitions. A complete
compact-input audit found major mechanisms covered but several conceptual
subtopics and beginner transitions too terse.

#### Design Details

Retain an authored course entry inside How it works, after Objective as specified
by FEAT-023, before
the existing teaching fields. Define subject, purpose, vocabulary, workflow,
CPU/GPU responsibility and worked reasoning. Keep 72 substantive
lessons and all existing lab identities and explanations. Refine opening
titles and synchronized syllabus sequences where needed. Use one learning
sequence with concrete prerequisites and recall questions. Remove audience
splits, route-selection commentary and repeated descriptions of how to read
the course from lessons, missions, READMEs and generated pages.

Render the new field through the shared renderer and check its full HTML
parity and inline diagram home. Add a compact H100 hierarchy diagram separating
logical work, physical resources, command submission and memory movement.
Reuse full training/inference/optimization workflow diagrams at the entry point
and retain links from their detailed lessons. Add a CUDA host/device workflow
diagram. SM subpartitions are not quadrants of the whole GPU; shared memory is
not a mandatory cache stage.

Add two small standalone Python/PyTorch mechanics labs: scalar training with
autograd and held-out checks; fixed-parameter toy autoregressive inference with
tokens, logits, greedy selection and stopping. Both have explicit CPU or H100
device selection, no downloads, no performance claims, full guides and result
gates. They are educational models, not transformer quality or serving tests.
Reuse existing CPU/GPU crossover, dispatch, vector-add and profiling labs.

Integrate remaining compact-input explanations into existing owning lessons:
telemetry sampling semantics, numeric encodings/scaling/rounding, MFU/HFU,
prefix-index structures, speculative proposal families and benchmark-tool
orientation. Do not copy obsolete APIs, unsafe monitoring commands, unsupported
topologies or simulation-as-serving claims. Explain CUDA distribution through
API/support/release contracts and an explicitly labeled packaging extension;
do not publish packages or claim existing executables are installed libraries.

#### Selected Option

Keep the existing Markdown/SVG/HTML pipeline and lesson sequence, with one
consistent introductory field and two minimal practical labs.

#### Alternatives Considered

A generic paragraph would not teach the missing mechanisms. Adding a new
introductory chapter to every course would unnecessarily renumber dependent
labs and diagrams. Replacing advanced lessons would lose useful depth.

#### Implementation Boundaries

No dependency upgrades, cluster operations, external publication, compatibility
aliases or unrelated cleanup. Retain H100 qualification and privacy boundaries.
Fixed native HTML/Python/CUDA stack; no application or AI-stack decision or
agent subsystem is introduced. This is a reversible local curriculum design,
so a full architecture checklist is unnecessary.

#### Test-First Success Criteria

- TDD-001: Each opening defines its domain before advanced fields and includes
  substantial what/why/how, a worked example and an inline workflow.
- TDD-002: CPU introductory labs demonstrate declared state transitions and
  reject invalid arguments; help works without loading PyTorch.
- TDD-003: Syllabus, exact lab titles, guides, TOC, complete source and diagram
  placements agree; all prior lesson/lab content remains available.
- TDD-004: H100 diagrams accurately separate address spaces, caches, SM
  subpartitions and logical threads; label-fit and accessibility checks pass.
- TDD-005: Public material contains official references, no private guide
  attribution or coverage tables, and no unsupported performance claims.

#### Validation Plan

Run focused negative controls, CPU mechanics tests, five validators, full shared
tests, Ruff/format, Markdown, source parity and public-safety checks. Render
new/relocated SVGs locally. Browser and H100 execution remain separate lanes.

#### Test Plan

Add beginner-entry content/ordering/diagram assertions and real CPU numerical
checks for both introductory labs. Retain semantic diagram, lab-guide and
sequence regressions, adjusting exact expected titles/counts only as required.

#### Evaluation Plan

A newcomer can explain what changes, what stays fixed, where computation
executes, why an optimization is useful and what to observe before progressing.
Independent read-only review checks both technical semantics and teaching flow.

#### Rollout And Rollback

Use a private task-start snapshot, focused source edits and atomic HTML rebuilds.
Do not create parallel curriculum or compatibility paths.

#### Done Definition

All five entry paths and reference gaps are integrated and locally validated.
Record source, CPU, browser and H100 evidence separately without overclaiming.

#### Implementation Evidence

All five first lessons now begin with substantial what/why/how explanations,
worked reasoning and concrete prerequisites. Separate audience routes and
repeated reading instructions are removed from all five courses, missions
and READMEs. The shared renderer preserves their
complete Start here content before advanced fields. Training and Inference
first-lesson titles, syllabus routes and new Lab 32/Lab 35 guides agree.

Added scalar-learning and fixed-parameter autoregressive CPU/H100 mechanics
with lazy runtime imports, explicit device choice, bounded stopping and
private no-clobber results. Added H100 physical/logical and CUDA host/API/device
SVGs, and placed the existing complete workflow diagrams at the entry points.
Integrated telemetry, precision, MFU/HFU, prefix-index, speculative-family,
benchmark-tool and kernel-distribution explanations with public primary sources.

Updated all five missions, syllabuses, READMEs, glossaries, resources, metadata,
visual plans and publication records. Rebuilt all five HTML pages atomically.
There are 72 lessons, 89 labs and 99 diagrams. Snapshot comparison preserves
all existing lab identities and 255 lab/guide/launcher/SVG files byte-for-byte;
no existing file was removed. Existing lesson fields are retained or extended
apart from two opening titles and two bounded practice/prerequisite refinements.

#### Verification Evidence

The audience-routing editorial follow-up passes all five standalone validators,
source-to-HTML parity, 103 focused content/publication/sequence tests and
configured Markdown lint. Browser and H100 qualification remain pending.

Original implementation evidence follows.

Local source gate: 398 shared tests and all five standalone validators pass,
including new introductory content, CPU mechanics and optional-field diagram
placement regressions. Source-to-HTML parity, changed-Python Ruff/check/format,
repository-configured Markdown, privacy and whitespace checks pass.

Actual CPU runs with existing PyTorch 2.13.0 reproduce the scalar learning
result and both EOS and length-limited fixed-token generation. Help and invalid
budget handling work without importing PyTorch. This does not qualify the
separate target environments or approve a dependency fallback.

All 99 SVGs were rasterized at 1200/320 pixels with shared styles; the five
new/relocated entry diagrams received focused visual review. A heading-crossing
arrow was repaired; conservative Arial/Verdana glyph and connector checks
report no findings. Independent read-only review closes the optional Start here
parser finding and reports no remaining concrete teaching/guide mismatch.

Browser desktop/390-pixel full-page inspection, clean target installations,
CUDA activation and live H100 performance remain pending. No speedup or
production-quality claim is derived from these introductory examples.

<!-- /FEATURE: FEAT-015 -->

<!-- FEATURE: FEAT-016 reqs=REQ-001,REQ-002,REQ-009,REQ-010 status=ready delivery=implemented priority=P0 version=2 -->
### FEAT-016: Topic ownership and deliberate reinforcement

#### Requirements Covered

- REQ-001: One course owner for complete topics and experiments.
- REQ-002: Consistent navigation, evidence and publication.
- REQ-009: Preserve useful information while removing exact repeated teaching.
- REQ-010: Lab guides explain the purpose of each initial or repeated activity.

#### Context Evidence

A full five-course topic and lab audit distinguishes repeated vocabulary from
duplicated competencies. Inference Labs 19 and 31 repeat column-sharded linear
reconstruction, while the latter adds common-state broadcast and strict
reference checks. General diagnostic workshops and capstones reuse earlier
operations for a different evidence question. Several syllabus assignments
blur preview, initial execution and later interpretation.

#### Design Details

Retain all 72 lessons and all unique mechanisms. Consolidate inference
partition mechanics into Lab 19: rank-zero input/weight broadcast, rank-ordered
all-gather, row-parallel all-reduce, finite/reference checks, absolute and
relative errors, logical byte accounting, repeated slowest-rank timing and
guaranteed distributed cleanup. Expose a positive batch-size option so a
batch-one exercise remains available. Preserve column elementwise BF16
tolerances and the explicitly explained row-reduction relative-L2 tolerance.
Remove only redundant Lab 31 source, guide and metadata after integration;
update every consumer and inventory count without aliases.

Keep Fundamentals wave mechanics and the Optimization diagnostic control in
their standalone environments. Explain why the latter revisits a known probe
before investigating balanced/skewed task makespan. Likewise identify the
fusion/synchronization profiler workshop and attention/training capstones as
evidence applications, not new versions of the same lesson. Stage scheduler,
model, mask and precision assignments as preview, first experiment or reuse.
Refocus the optimization layout extension on the whole producer/consumer
pipeline rather than repeating a standalone stride-conversion exercise.

No complete topic was found in the wrong course: foundational models,
general measurement, training state/gradients, inference request behavior and
CUDA implementation remain distinct owners. Keep the custom-course library
decision as a prerequisite refresher followed by CUDA-specific contracts.

#### Selected Option

One canonical inference partition lab plus explicit progression and refresher
purpose throughout the existing curriculum.

#### Alternatives Considered

Removing every repeated term would destroy the prerequisite bridges and
standalone usability. Keeping two identical partition exercises would preserve
unnecessary repetition. Cross-course runtime imports would couple environments.

#### Implementation Boundaries

Courses only. No dependency changes, installations, GPU/Slurm operations,
browser work, external publication, compatibility paths or unrelated cleanup.
Preserve all diagrams, existing advanced context and independent course setup.

#### Test-First Success Criteria

- TDD-001: One inference partition lab contains both collective patterns,
  common-state initialization, batch-one support and all preserved checks.
- TDD-002: CPU fixtures prove reconstruction, reordered-output rejection,
  finite/error validation, argument handling and cleanup on failure.
- TDD-003: Lesson/syllabus/guide references and 88-lab inventory agree.
- TDD-004: Refresher/control/assessment purpose is explicit; no complete
  canonical lesson or lab body is duplicated except standalone preflight.
- TDD-005: Source parity, all course validators and focused/public-safety gates pass.

#### Validation Plan

Compare with a private task-start snapshot; review exact matches and semantic
overlap. Run focused regressions, all course validators, the shared suite,
Ruff/format, configured Markdown, links and privacy checks. Live H100 collective
and numerical qualification remains a separate pending lane.

#### Test Plan

Add CPU/mocked reconstruction and lifecycle tests, inventory/no-orphan checks
and focused progression assertions. Preserve existing per-course numerical
and source-listing tests.

#### Evaluation Plan

A learner can name the new question in every repeated activity. An independent
read-only reviewer verifies preserved capabilities and final topic ownership.

#### Rollout And Rollback

Merge useful checks first, then remove the exact redundant pair and rebuild
all HTML atomically. The private task snapshot preserves recoverability.

#### Done Definition

No remaining exact repeated complete teaching experiment in reviewed scope;
refreshers and specialized applications retain their useful information.
Record source and target-runtime evidence separately.

#### Implementation Evidence

Consolidated inference partition mechanics into Lab 19 and removed only the
redundant Lab 31 source and guide. Common-state broadcasts, rank-ordered
column reconstruction, row-partial summation, finite/reference checks,
maximum absolute and relative errors, positive batch-size control and
guaranteed cleanup are implemented. Repeated slowest-rank samples retain
the stronger timing path; the guide bounds operator timing, logical weight
ownership and BF16 reduction tolerances without implying serving results.

Aligned metadata, syllabus, course practice, guides, smoke commands and
inventory tests. Explicit preview/run/reuse instructions now distinguish
lane models from residency, forward shape inspection from complete updates,
memory ledgers from precision trials, and profiles from capstone decisions.
General wave/fusion/synchronization controls and the attention capstone retain
their distinct evidence questions. No cross-course move was warranted.

Preserved all 72 lesson titles, objectives, beginner entries, mechanisms,
worked examples, H100 focus, trade-offs, mental models, answers and review
prompts against the task-start snapshot. All 99 embedded SVGs are identical.
There are 88 labs with complete guides. Updated the catalog and publication
records and rebuilt all five HTML pages atomically. No runtime imports,
dependency manifests, launchers, styles or infrastructure were changed.

#### Verification Evidence

All five validators and 410 shared tests pass. Twelve new regressions cover
the canonical inventory, CPU shard computations and rank-ordered assembly,
reordered/nonfinite rejection, broadcast/orchestration fixtures, global success
agreement, cleanup on failure, positive batch arguments, the two-rank guard,
and absence of identical complete lessons or non-preflight learner sources.
The negative control failed before consolidation. CPU collective fixtures do
not exercise a real distributed backend or validate BF16 H100 numerics.

Source-to-HTML parity, dependency-light help, Python compilation,
changed-Python Ruff/check/format, repository-configured Markdown, links,
publication-safety and whitespace checks pass. A private snapshot comparison
confirms only the two intended duplicate files were removed; 170 existing
code/launcher/diagram files remain byte-identical. Independent read-only review
found no concrete remaining loss of unique teaching, misplaced topic, or
source/guide mismatch in the selected changes.

Clean target-environment qualification, CUDA/NCCL execution, full-page browser
inspection and live H100/Slurm performance remain pending. This alignment
does not change publication approval or claim a measured speedup.

<!-- /FEATURE: FEAT-016 -->

<!-- FEATURE: FEAT-017 reqs=REQ-002,REQ-009,REQ-010 status=ready delivery=implemented priority=P0 version=6 -->
### FEAT-017: Define each concept before applying it

#### Requirements Covered

- REQ-002: Consistent complete teaching and HTML publication.
- REQ-009: Beginner-accessible definitions before application throughout every course.
- REQ-010: Clear links from concepts to the existing practical guides.

#### Context Evidence

Course-level Start here entries define the overall subjects, but many later
lessons open with objectives, resource constraints or interventions before
defining their named technology. CUDA Graphs is a concrete example: capture,
replay and static-buffer restrictions appear before a clear description of
the graph itself. Similar gaps affect occupancy, compilation, memory
allocation, training parallelism, inference engines and CUDA primitives.

#### Design Details

Preserve the five substantive course entries within How it works. Following
FEAT-023, place Objective first, then define the named concept in How it works,
its basic operation and its main distinction from nearby ideas. Definitions
are lesson-specific prose, not generated benefits or acronym lists. Expand
essential first-use terms in those openings and the existing course entries;
retain all advanced fields, worked examples, lab identities and diagrams.

Use the shared renderer's How it works field and the existing reading-width
and light typography rules. Standalone validators require a substantive
course entry and a nontrivial definition before application, and
verify complete canonical-to-HTML narrative parity. Word counts catch missing
or token primers, not semantic completeness; human editorial review remains
necessary. Keep diagrams beside their existing mechanisms and preserve all
SVG content. Expand a directly affected lab introduction where it benefits
from the same definition, without changing runnable lab capabilities.

Apply this sequence at each new topic inside a lesson, practical guide or
optional study entry, rather than treating the lesson-level primer as blanket
coverage. Add a concise conceptual explanation at the owning introduction
before procedures, equations or tuning advice: identify the kind of thing,
the essential operation and any distinction needed to understand that use.
Retain useful later detail. Previously taught prerequisites and explicitly
labeled previews may use brief reminders instead of repeated full primers.
Review all 73 lessons and 94 lab guides against this topic-level contract;
presence and word-count checks alone cannot establish conceptual clarity.

Trace every lab's actual techniques back to theory available along its
prerequisite route before execution. Expand the owning lesson where a concept
is currently explained only inside a guide, or where the theory names a
technique without teaching its purpose and basic use. Each guide's Before you
start section identifies this preparation, including measurement and numerical
checks when they are essential to the experiment. Keep previews separate from
full execution and align the lesson Practice field, syllabus and metadata.
Use existing lesson homes and guide structure; preserve lab identities and
executable behavior. Ordinary language syntax remains an explicit course
prerequisite, rather than a reason to turn each lesson into an API glossary.

#### Selected Option

Additive concept-first openings throughout all five courses, supported by
shared rendering, standalone validation and independent editorial review.

#### Alternatives Considered

Glossary-only definitions force beginners away from the lesson. Replacing
mechanisms with simpler summaries loses advanced information. Repeating the
long course entry in every lesson adds bulk without explaining that lesson's
technology. A bespoke presentation for each course would reintroduce drift.

#### Implementation Boundaries

Course text, directly related guides/resources/README, shared authoring tools,
tests, publication records and generated HTML, plus the bounded correction of
Fundamentals lab 01's numerical comparison to scale relative tolerance by the
stated CPU reference. No new runnable capabilities, dependency upgrades,
extra compatibility paths, infrastructure, publication, GPU execution or
browser bypass. Existing course and lab numbering remains.

#### Test-First Success Criteria

- TDD-001: All 73 lessons have Objective followed by definition-led How it works in
  canonical source and rendered HTML; incomplete openings fail validation.
- TDD-002: The CUDA Graphs primer defines operations, dependencies, capture and
  replay and distinguishes replay from fusion and compilation.
- TDD-003: Each specialized primer expands its key terminology and does not
  assume a glossary has already been learned.
- TDD-004: All 94 labs, 108 diagrams and substantive existing lesson fields
  remain; source-to-HTML parity and navigation pass across five courses.
- TDD-005: Semantic review covers new topics within all lessons, practical
  guides and optional study entries; each introduction defines the concept
  before its first substantive application. Structural tests are supporting
  evidence and do not replace this review.
- TDD-006: Review all 94 labs against the lesson route, including computation,
  measurement, validation and coordination. Every guide names prior theory
  teaching what its techniques are, their purpose and basic use; an early
  preview cannot require full execution of an untaught technique.

#### Validation Plan

Review every lesson against its title and first-use concepts, consult current
official documentation for technology-specific claims, run focused negative
tests, five validators, shared tests, Ruff/format, configured Markdown and
publication-safety checks. Compare against a private task-start snapshot.

#### Test Plan

Test renderer field ordering/full prose, optional diagram placement and
standalone missing/short-primer rejection; run selected semantic assertions
for central concepts and all-course coverage. Retain existing depth checks.

#### Evaluation Plan

An independent read-only review asks whether a newcomer can explain what
each technology is before encountering optimization advice. Distinguish that
editorial judgment from automated structure and target-runtime evidence.

#### Rollout And Rollback

Apply additive source patches and rebuild every HTML atomically. Preserve
the task-start snapshot for exact comparison; no content removal is intended.

#### Done Definition

Every lesson introduces its central concept before applying it, existing
educational depth remains, and rendered courses follow the same pattern.
Each lab's required theory is available before its first full execution and
its guide identifies that preparation, including any separately gated mode.
New topics within lessons and practical guides also define their concepts
before substantive application, with explicit prerequisite bridges for revisits.

#### Implementation Evidence

All five canonical courses now open each later lesson with an authored What
it is primer: 67 in total. Five substantive Start here entries remain, with
four expanded to define essential course-entry vocabulary. Primers describe
the concept and its operation before applications, including CUDA Graphs
versus fusion/compilation, checkpoint/resume versus recomputation, SDPA versus
attention backends, training versus serving parallelism, and CUDA programming
abstractions versus physical hardware. The CUDA Graphs lab guide introduces
the execution-plan model and keeps its implemented fixed-shape boundary.

The shared renderer places the new field before Objective. The validator
template and all five standalone copies require exactly one substantive
opening and check its source and HTML ordering. README explains the authoring
pattern; official NVIDIA/PyTorch/Hugging Face references support terminology.
All five HTML pages were regenerated atomically without stylesheet changes.

#### Verification Evidence

Local source validation on 2026-09-05 passed all five validators and 422 shared
tests. The 12 new regressions failed before implementation and pass afterward;
they cover all-course source/rendered primer presence and order, invalid
openings, CUDA Graphs distinctions, and contextual diagram placement. Ruff
check/format, repository-configured Markdown, source-to-HTML parity,
publication-safety and whitespace checks pass.

Independent read-only editorial reviews covered all 67 new primers and the
expanded entries. Their bounded refinements were applied: DDP input ownership,
attention query/key/value definitions, graph-pool/bucket terminology and
capture-specific constraints, RDMA mechanics, block lifetime, bank conflicts,
loss/parameter definitions and median wording. No remaining concrete primer
or renderer defect was identified in that review.

The task-start comparison confirms no files removed, all 72 lesson titles and
99 embedded SVGs preserved, and 171 executable/launcher/diagram files unchanged.
All 88 labs remain. Existing labeled teaching fields are unchanged except two
CUDA Graphs statements qualified to the actual lab's capture contract; richer
beginners' explanations add context rather than replace advanced material.
Clean installed environments, CUDA/H100/Slurm execution and permitted full-page
browser inspection remain separate pending evidence lanes.

#### Follow-up Alignment Evidence

A second, lesson-to-lab readiness review covered all 72 lessons and 88 lab
guides. Ten guides now introduce missing first-use concepts before commands:
tensor operations and profiler self time, strides and storage views, masked
launch tails, numerical tolerances and relative L2 error, scalar extraction,
quantization, LoRA composition and affine/tanh operations. Training adds a
small LoRA matrix-composition calculation. Its capstone explicitly requires
profiling its own baseline and candidate rather than attributing a different
transformer's bottleneck to the capstone. Custom Kernels introduces GEMM,
bias and epilogues before their first example. All five syllabi and README
describe the same concept-to-practice reading route.

The validator template and five identical standalone copies now compare each
complete rendered opening with its own canonical lesson. Negative regressions
reject introductions swapped between lessons or truncated inside one lesson,
even if the full text exists elsewhere on the page. A numerical boundary
fixture exposed the CPU/GPU comparison's reference-argument mismatch; the
single argument-order correction aligns the executable with its explanation.

Final local checks on 2026-09-05 pass 439 tests, all five validators, Ruff and
format checks for the nine affected Python files, configured Markdown,
publication-safety checks and source-to-HTML parity. Seventeen additional
regressions failed before their corresponding repairs and pass afterward.
Independent read-only follow-up reviews identified no remaining concrete
defect in the reviewed explanatory bridges or validator changes.

Comparison with the follow-up task-start snapshot confirms all 72 lesson
titles, 88 lab guides and 99 embedded SVGs retained, with no removed files.
Of 171 executable/launcher/diagram files, 170 are byte-identical and the
remaining lab differs only in its CPU-reference tolerance argument order.
Existing labeled lesson fields remain except the training capstone's
corrected Practice statement; new definitions add to the retained content.
All five HTML pages are regenerated. Browser rendering, clean target
environments and live CUDA/H100/Slurm evidence remain pending.

#### Topic-Level Definition Review

The 2026-09-06 review covered all 73 lessons, 94 practical guides and 27
optional study topics, including concepts introduced after a lesson's opening.
Supporting mechanism walkthroughs and the diagnostic setup guide were also
reviewed. Read-only reviewers identified and rechecked 37 concrete gaps;
follow-up vocabulary clarifications were applied without changing experiments.

Definitions now explain instruction-level parallelism, quantization scales,
matrix arithmetic and precision policy, allocator stream tracking, L2 cache
retention, Python worker concurrency, nonlinear functions, numerical error
measures, checkpointing variants, FP8 caching and accumulation, virtual pipeline
stages, state-space layers, multi-LoRA and multimodal serving, piecewise
execution, quantization recipes, tool loops, constant memory, synchronization
domains, math modes, Cooperative Groups and validation sentinels. Small
examples connect dimensions, running results and error limits to their meaning.
Official NVIDIA and framework documentation and original method descriptions
were checked for the relevant technical distinctions. README records the
topic-level authoring rule; substantive explanations and practical work remain.

All five rebuilt textbooks pass the course validators, exact source-to-HTML
parity and the course skill's bounded HTML/source checks. All 573 shared tests
pass. Configured Markdown lint covers 168 files and Bash syntax checks cover
154 fenced examples. A task-start comparison confirms no files removed, no
changes to the 247 non-Markdown/non-HTML source files, and unchanged lesson
titles, practical-guide structure and fenced commands. The 108 SVG assets and
shared stylesheet are unchanged. Private-material and changed-source reviews
found no actionable issue. These are semantic, source and CPU-test results;
the existing browser-policy block still leaves full-page layout, keyboard and
reflow inspection pending. No new target-runtime or H100 evidence is claimed.

#### Prior-Theory Lab Review

The follow-up review traced all 94 labs to the lessons available before their
first full execution. The source inventory covered computation, measurement,
validation and coordination, including shared model helpers and launch routes.
Forty-one owning lessons now supply missing definitions, purpose and basic
usage: early reference tolerances and matrix/operator semantics, Triton
indexing, views/strides, input and output ownership, distributed updates,
precision/clipping, partitioned gradients, inference attention/caches/clients,
quantization, C++ ownership, validation sentinels and CUDA/library interfaces.
Every practical guide names its theory preparation. All five syllabi and the
catalog README explain that preparation; previews and separately gated modes
remain distinct from full execution. The casebook's guide also now matches its
actual timing-then-validation sequence before results are written.

All 573 shared tests pass locally. Five course validators, exact canonical
HTML parity, five bounded HTML/source checks, configured Markdown lint over
168 files and syntax checks for 154 shell examples pass. The task-start
comparison preserves all 420 original files, all lesson/lab identities and
all 247 non-Markdown/non-HTML files, including code, launchers, dependency
settings, diagrams and shared styling. No additional tests were introduced
merely to assert the wording of prose. Semantic review complements the bounded
automated checks; full-page browser review remains blocked by the existing
policy and installed-target, CUDA/H100, Slurm and serving-runtime evidence
remain pending. This review does not claim a measured optimization gain.

#### Final Cross-Course Alignment

A further review covered all 73 lessons, 94 practical guides and their actual
sources across the five courses. The owning theory now explains output
sentinels before the scheduling labs, and NCCL Work handles and stream joins
before the transport sweep. Fundamentals' relative-L2 explanation states the
precision lab's actual nonzero-reference assumption. CUDA preflight text
matches emitted evidence, the reduction example states its block boundary,
and vector-add execution in Lesson 3 is separate from Lesson 4's sanitizer
revisit. The supplied capstone's operation precedes its optional packaging
extension. Theory and guides explain the finite-value acceptance corrections
recorded in FEAT-007. These repairs restore existing teaching and correctness
requirements rather than introducing new lab capabilities.

Independent read-only rechecks found no remaining concrete issue in those
changes. All 660 shared tests, five course validators, exact generated-source
parity and five bounded HTML/source checks pass. Configured Markdown and shell
example syntax checks pass. The original 420 files, lesson/lab identities,
108 SVGs, shared styles and dependency settings remain. Browser inspection
remains blocked by the existing policy; clean installed environments and live
CUDA/H100, Slurm, NCCL and serving qualification remain separate pending work.

<!-- /FEATURE: FEAT-017 -->

<!-- FEATURE: FEAT-018 reqs=REQ-009 status=ready delivery=implemented priority=P1 version=4 -->
### FEAT-018: Current-technology self-study directions

#### Requirements Covered

- REQ-009: Complete educational explanations and content integrity (AC-013).

#### Context Evidence

The user requests current research and concise closing topics in all five
courses so learners can continue independently. Public official sources are
reviewed as of 2026-09-05; research freshness is not runtime qualification.

#### Design Details

Author one NEXT-STEPS.md per course. Each unnumbered topic defines the
technology, explains why it extends that course, poses a concrete study
question and states hardware and maturity limits. An opening without a research-review date separates
recent developments from established advanced concepts and declares all
reading optional. Keep public vendor/project links beside entries in Markdown;
the existing HTML reference renderer resolves them to the final bibliography.

Render the complete guide exactly once after supporting guides and before
official references, with a persistent sidebar link. Reuse existing light
styles and Markdown rendering. Require source/HTML parity and safe links in
all five standalone validators. No new numbered lessons, labs or hours.

#### Selected Option

Small authored reading lists with a shared rendering and validation contract.

#### Alternatives Considered

A single cross-course catalog loses standalone usefulness. Inserting emerging
tools into required labs implies qualification not established by research.
A long release catalog overwhelms the intended concise next-study guidance.

#### Implementation Boundaries

Five new guides, shared builder and validator template/copies, focused tests,
README and publication records, generated HTML and this canonical pair.
No dependency upgrades, executable learner changes, new infrastructure,
compatibility aliases, remote publication or browser-denial bypass.

#### Test-First Success Criteria

- TDD-001: Every course has one complete optional next-study guide without a research-review date.
- TDD-002: HTML placement and sidebar navigation resolve without duplicates.
- TDD-003: Missing, truncated, reordered or duplicated guide content fails validation.
- TDD-004: Lessons, lab inventory, diagrams and environment pins remain intact.

#### Validation Plan

Research official current documentation, inspect content ownership and hardware
claims, run focused negative tests, all five validators, shared pytest,
Ruff/format, configured Markdown and publication-safety checks.

#### Test Plan

Exercise full local guide parity, required-file and ordering failures, link
allowlisting, standalone validator-copy parity and all-course regeneration.

#### Evaluation Plan

An independent read-only review checks concise what/why explanations, useful
study questions, official links and H100 versus newer-hardware boundaries.
Browser and live target validation remain separate pending lanes.

#### Rollout And Rollback

Add source guides and regenerate all HTML atomically. A private task-start
snapshot supports exact preservation checks and scoped recovery if needed.

#### Done Definition

Five navigable self-study sections preserve the complete required courses,
state research freshness honestly and pass local publication checks.

#### Implementation Evidence

Five authored NEXT-STEPS.md guides provide 27 optional topics, reviewed against
official NVIDIA and primary project documentation on 2026-09-05. Each topic
defines the concept, supplies an independent-study question and states
hardware or maturity limits. The lists distinguish recent releases, established
advanced concepts, newer-hardware comparisons and experimental paths.

The shared renderer publishes each complete guide once, after supporting
guides and before the official bibliography, with a sidebar link. README files
explain the optional route. The validator template and five identical copies
require the guide and verify its local ordered prose, closing placement,
navigation and each canonical reference destination. The public-link policy
adds only the needed official documentation hosts and exact project paths.
All five HTML pages are regenerated atomically; no stylesheet, learner
executable, environment pin, course numbering or guided-hour changes were made.

#### Verification Evidence

Local validation passed all five validators and 464 shared tests on 2026-09-05.
Sixteen new regressions failed before their corresponding implementation and
pass afterward. They cover all-course guide presence and rendering, absent,
duplicated, truncated or reordered guide content, wrong placement, missing
navigation, narrow link acceptance and incorrect existing bibliography targets.
Ruff/check/format for eight Python files, configured Markdown, publication
privacy scans and source-to-HTML parity pass.

Independent read-only reviews covered all 27 topics and the complete changed
rendering/validation integration. One deprecated model recipe link was replaced
with the maintained Nemotron 3 Nano architecture guide. A standalone link-parity
gap was reproduced, repaired and independently rechecked across all five
courses. Both findings are closed with no remaining concrete review defect.

The private task-start audit confirms no removed files, all 72 canonical
lessons byte-identical, all 99 embedded SVGs identical and every pre-existing
file outside the explicit publication/tooling change set unchanged. Existing
labs, pins and metadata are preserved. Browser rendering, clean installed
environments, runtime activation and live CUDA/H100/Slurm remain separate
pending evidence lanes; researched support is not qualification.

#### Display-date refinement

The user requests removal of the research-review dateline from all five
closing guides. Remove only that label, preserve topic content and actual
technology release dates, and align README descriptions, the validator
template/copies and tests. Historical research and verification dates remain
in project evidence, not in the learner-facing opening. The label is removed from all five canonical guides and regenerated HTML pages.
README descriptions and the template plus five identical standalone validators
no longer require or promise a displayed date. Actual technology release dates,
topic descriptions and references remain unchanged.

Five updated all-course regressions failed before removal and pass afterward.
All 44 focused next-study/content-contract tests, five course validators,
seven-file Ruff/check/format, configured Markdown and whitespace checks pass.
An independent byte comparison against the task-start archive confirms the
five guides differ only by the requested dateline removal; generated pages
differ only by that removal and aligned README wording. All 1351 other snapshot
files outside the explicit change set remain unchanged, with no files removed.
Security and scoped code review found no additional issue. Browser and live
target evidence remain pending and were not needed for this text-only change.

<!-- /FEATURE: FEAT-018 -->

<!-- FEATURE: FEAT-019 reqs=REQ-003,REQ-004,REQ-008,REQ-009,REQ-010,REQ-011 status=ready delivery=implemented priority=P1 version=2 -->
### FEAT-019: Prerequisite-first GPU networking and NCCL tuning workshop

#### Requirements Covered

- REQ-003: Fundamentals networking theory.
- REQ-004: Optimizations hands-on communication ownership.
- REQ-008: Separate static, installed, runtime and live evidence.
- REQ-009: Clear conceptual openings.
- REQ-010: Complete practical guides.
- REQ-011: Networking layers, safe Slurm experiments and measured interpretation.

#### Context Evidence

The user requests NVIDIA-focused networking definitions and diagrams, GPU
communication tuning and NCCL Tests on the learner-managed cluster, and explicitly
requests aligned topic ordering and TOC. Existing Fundamentals Lesson 12 explains
collectives; before this addition, Optimizations moved directly from local
imbalance to scaling and overlap.
Current official NCCL 2.31 documentation, GPUDirect RDMA, DGX H100 and upstream
NCCL Tests documentation supply research evidence, not environment qualification.

#### Design Details

Expand Fundamentals Lesson 12 into a theory primer in this order: node/rank and
GPU/NIC attachment; NVLink/NVSwitch; InfiniBand versus Ethernet/RoCE; RDMA and
GPUDirect RDMA; NCCL collective semantics and measurement limits. Preserve Lab 06.

Insert Optimizations Lesson 11 on topology, transport and NCCL benchmarking.
Move existing scaling/overlap and library-first capstone to Lessons 12 and 13,
preserving their content and stable lab IDs. Update syllabus, metadata, diagrams,
cross-references and generated navigation. Add Lab 17 for a bounded FP32 PyTorch
message-size sweep with device-event samples, slowest-rank summaries and exact
known-sum checks. Add Lab 18 to launch a supplied MPI-enabled benchmark and parse validated
upstream all_reduce_perf results.
A dedicated MPI-aware Slurm launcher runs a supplied upstream binary, one process
per GPU; it is not passed through torchrun. The default allocation remains two
nodes with one H100 each; larger allocations are an explicit conditional route.

Tuning runs in fresh processes using a small reviewed profile set: default,
Socket transport via NCCL_NET=Socket, GDR disabled via NCCL_NET_GDR_LEVEL=LOC,
Ring or Tree, and conditional QP counts.
Do not override cluster-required interface or plugin configuration silently.
Separate diagnostic logging from acceptance timing. Record the actual loaded
NCCL and external benchmark identity; treat nccl-tests v2.20.0 only as a reviewed
candidate until the learner qualifies its CUDA/NCCL/MPI build. No package upgrade.

Add original accessible layer, topology, direct-memory-path and evidence-flow
diagrams at their related explanation. Keep official references at the end,
private raw diagnostic outputs local, and public summaries free of identifiers.

#### Selected Option

Extend existing foundations and insert one practical prerequisite lesson with
two focused Python labs plus bounded orchestration. Reuse the established
framework, rendering, Slurm conventions and standalone course helpers.

#### Alternatives Considered

Appending networking after the capstone breaks prerequisite order. Combining all
material into the existing overlap lesson obscures transport diagnosis. Installing
or administrating a fabric exceeds course ownership. A separate networking course
would expand the requested catalog and is unnecessary.

#### Implementation Boundaries

Fundamentals and Optimizations text, diagrams, metadata, guides and references;
two Python labs and one upstream benchmark Slurm launcher; focused shared tests,
catalog and canonical specs; regenerated HTML. No infrastructure deployment,
GPU CI, secrets, dependency upgrades, backward compatibility or browser bypass.
Fixed stack needs no application/AI stack selection or agent subsystem design.

#### Test-First Success Criteria

- TDD-001: Lesson and TOC order place network qualification before scaling/overlap.
- TDD-002: NCCL Tests parsing rejects malformed, incomplete, duplicate or failed correctness rows and retains both result modes with explicit units.
- TDD-003: Launchers fail outside Slurm or for unsupported placement, preserve GPU isolation, validate bounded settings and use fresh processes.
- TDD-004: Tuning changes one documented factor and cannot silently mix inherited experimental overrides.
- TDD-005: New source, guides and diagrams appear in matching lessons without loss of existing material.

#### Validation Plan

Official-source research; test-first parser/profile/launcher checks; all five
validators, shared pytest, Ruff, Markdown, shell checks, SVG fit/accessibility
and publication safety; independent read-only risk review.

#### Test Plan

CPU fixtures cover result formats, units, correctness, profile environments and
mocked launch arguments. H100 testing requires at least three independent default
and candidate runs, device correctness and version/path evidence.

#### Evaluation Plan

Learners distinguish capability, selected path, measured performance and application
benefit. Diagnose TCP fallback, skew, incorrect collectives or congestion before
tuning. Revert a candidate that improves a synthetic curve but harms step time.

#### Rollout And Rollback

Preserve a private task-start archive, edit canonical sources, regenerate all pages
atomically and compare changed surfaces. Recover only task-owned edits if needed.

#### Done Definition

The educational and executable source addition is complete when integrated local
checks pass. Runtime/H100 and full-browser publication remain pending until
independently observed; no performance improvement is promised by source delivery.

#### Implementation Evidence

Expanded Fundamentals Lesson 12 with plain-English networking layers and three
inline diagrams. Inserted Optimizations Lesson 11 with two inline diagrams and
complete guides for Labs 17/18; scaling/overlap and the capstone now follow as
Lessons 12/13. Syllabi, lesson metadata, navigation, references, glossaries,
benchmark worksheet, setup and smoke instructions match the revised route.

Lab 17 checks exact FP32 all-reduce sums and reports repeated max-rank device
and synchronized host-wall samples, using a consistent statistical median.
Lab 18 has separate run/parse modes, strict checked v2.20.0 table parsing,
a dedicated MPI Slurm launcher, bounded settings, private raw logs and a public
allowlist report. Offline parses are never acceptance-timing evidence.
Profiles isolate one job-local factor; site-required settings are not removed.
No infrastructure, dependencies or pre-existing learner lab code was changed.

#### Verification Evidence

All five course validators and 487 shared pytest tests pass locally. Twenty-three
new networking tests cover parser rejection, correctness, units, profile isolation,
private evidence, launch arguments and mocked MPI success/failure. Test-first
regressions closed two independent review findings: inconsistent even-sample
median bandwidth and offline logs being eligible for acceptance-timing status.
The narrow independent recheck reports no outstanding finding.

Eight changed Python files pass Ruff and format checks. The new launcher passes
Bash syntax and ShellCheck. All five pages are regenerated and source parity
passes. Five new SVGs were rendered and inspected at 1100- and 390-pixel widths;
a connector-label collision was repaired. This is asset-level, not full-browser
proof. The task-start archive comparison finds no removed files, unchanged
pre-existing lab code and byte-identical specialized course trees.

Publication reviews retain separate evidence lanes. No clean installed MPI/NCCL
Tests build, target CUDA activation, live H100/RDMA/GDR/QP performance, or full-page
browser evidence was collected. The external v2.20.0 benchmark remains a candidate
until learner qualification; this delivery is implemented, not live verified.

<!-- /FEATURE: FEAT-019 -->

<!-- FEATURE: FEAT-020 reqs=REQ-004,REQ-005,REQ-006,REQ-008,REQ-009,REQ-010,REQ-012 status=ready delivery=implemented priority=P1 version=2 -->
### FEAT-020: Transfer pipelines, DDP hooks and KV retention practice

#### Requirements Covered

- REQ-004: General performance and transfer ownership.
- REQ-005: Training gradient and update semantics.
- REQ-006: Inference cache and serving ownership.
- REQ-008: Separate evidence lanes and fixed numerical contracts.
- REQ-009: Definition-first preserved teaching.
- REQ-010: Complete practical guides and publication parity.
- REQ-012: Missing transfer and cache experiments.

#### Context Evidence

All five current course packages were compared with the supplied topic reference.
Existing worker, copy-mode, compute-stream, networking and synthetic gradient
readiness labs remain useful. They do not implement complete H2D/D2H pipelines,
real DDP communication-hook tuning or cache-tier retention policy.
NVIDIA CUDA, Nsight, GDS and Dynamo documentation and current PyTorch API guidance
inform the design; documentation does not qualify an installed target.

#### Design Details

Add Optimizations Labs 19/20 to the existing input and memory-lifetime route.
Lab 19 isolates stream placement using preallocated bounded input/device slots,
per-slot events, equivalent deterministic computations and joined loop timing.
Lab 20 progresses from serial output through workers, pinned pooling,
nonblocking copy and a dedicated egress stream. Futures bound the queue; event
completion precedes CPU access; consumption precedes reuse; all work drains.

Add Training Lab 33 after Lab 28's readiness model. Use actual DDP, one bucket
cap/hook configuration per process launch, fixed equal rank batches, FP32 reference
updates, finite/error gates and observed public GradBucket metadata. Report
warm-up separately; retain lossy trajectory differences and never infer convergence.

Add Inference Lab 36 to prefix reuse. A deterministic bounded cache model
represents fast resident and slower retained prefixes, LRU eviction, idle TTL,
identity separation and restart loss. Compare modeled restore and recomputation
costs including write traffic. No real storage, serving engine or CUDA is started.
Explain GDS direct storage paths, CPU control work and possible staged fallback;
hardware validation remains an explicit optional qualified experiment.

Extend owning lessons, guides, metadata, syllabi, diagrams, glossaries, setup,
versions and review records. Preserve Fundamentals and Custom Kernels and existing
lab identities. Reuse the deterministic shared builder and standalone helpers.
Align the shared HTML shell with the course skill's bounded checker: a main
landmark skip target, concise guided-hours label and source identity on each
literal code listing. Remove the unnecessary favicon element. These publication
and validator changes apply to all five packages without changing Fundamentals
or Custom Kernels teaching or lab code.

#### Selected Option

Four bounded labs inside existing courses, with fuller existing lessons and
focused diagrams. Reuse current dependency candidates and ordinary Slurm
launchers. Add a two-node Nsight launcher with private per-node reports, literal
argument forwarding and step termination when either node fails.

#### Alternatives Considered

Duplicating introductory profiling and topology adds no capability. A new CUDA
kernel does not address host scheduling. Deploying a storage product or asserting
transcript benchmark gains would exceed scope and available evidence.

#### Implementation Boundaries

Three owning course trees, shared publication/validator tooling and focused
tests, all five generated pages, catalog and canonical specs.
No infrastructure changes, publishing, dependency installation or legacy paths.

#### Test-First Success Criteria

- TDD-001: Early buffer reuse, omitted drain and consumer failure cannot produce an accepted result.
- TDD-002: DDP reference checks detect missing/nonfinite gradients and updates; bucket evidence reflects actual calls rather than the cap alone.
- TDD-003: Cache expiry, eviction, identities, zero capacity and recompute choice behave deterministically without fabricated measurements.
- TDD-004: All new teaching, guides, diagrams and complete sources are reachable in generated HTML.

#### Validation Plan

Focused CPU behavior tests and CLI checks, all five validators and shared tests,
format/lint, source parity, scoped risk review and permitted browser inspection.

#### Test Plan

Use pure state machines and deterministic small tensor references offline.
On a qualified H100 allocation, run complete pipelines and two-rank DDP,
inspect short NVTX traces and compare at least three independent unprofiled runs.

#### Evaluation Plan

Explain critical-path changes with equivalent inputs and outputs. Distinguish
host submission from device overlap, timeline gaps from whole-GPU utilization,
synthetic training error from task convergence, and modeled cache hits from engine
TTFT. Reject an optimization whose relevant end-to-end outcome worsens.

#### Rollout And Rollback

Preserve the task-start files and unrelated dirty work, edit canonical sources,
regenerate publications and inspect only the task-owned delta.

#### Done Definition

Authored implementation completes with integrated source/CPU gates. Installed
Linux/H100, CUDA/NCCL/GDS, live measurements and browser proof remain pending
where not independently exercised.

#### Implementation Evidence

Optimizations Labs 19/20 implement bounded slot ownership, matched deterministic
work, explicit completion events and joined timing. Training Lab 33 implements
real DDP hook dispatch, actual bucket observations, checked SGD and an
independently evolving full-batch oracle. Inference Lab 36 implements the
documented deterministic tier policy and private JSON result format.

Four complete guides and diagrams are integrated into the owning lessons,
syllabi, metadata, glossaries, smoke runbooks and benchmark worksheets. The
catalog now contains 73 lessons, 94 labs and 108 diagrams. Shared publications
embed the complete sources and satisfy both standalone and skill checks.

#### Verification Evidence

All 573 shared tests and all five course validators pass locally. The 27 new
tests cover CPU control loops, output ownership/failure propagation, corrupted
inputs, gradient/update checks, hook dispatch, cache policy and a mocked
two-node profiler launch, including literal arguments, report permissions and
nonzero exit propagation. These controls do not implement CUDA concurrency.

Six direct CPU CLI runs exercise retention, expiry, slow restore, restart and
revision scenarios and write private result files. Four diagram assets were
rendered and inspected at 420- and 320-pixel widths. Changed Python lint and
format, Markdown, Bash syntax, ShellCheck and source-to-HTML parity pass.
The create-learning-course bounded HTML/source checker passes all five pages
against explicit source allowlists. Independent read-only review found and
closed the new launcher's missing kill-on-bad-exit policy; no open finding remains.

Clean PyTorch 2.14 installation, H100/CUDA pipelines, two-node NCCL hooks,
real Slurm failure propagation, actual Nsight capture and GDS/engine performance
remain unqualified. Browser policy denied local HTML access; full desktop,
390-pixel and 320-pixel page review is pending and no workaround was attempted.
No performance speedup or publication-ready claim follows from these checks.

<!-- /FEATURE: FEAT-020 -->

<!-- FEATURE: FEAT-021 reqs=REQ-002,REQ-009,REQ-010 status=ready delivery=implemented priority=P0 version=5 -->
### FEAT-021: Theory lessons linked to complete practical labs

#### Requirements Covered

- REQ-002: Consistent lesson and lab presentation.
- REQ-009: Complete conceptual teaching and preservation of useful explanations.
- REQ-010: Unified practical guides with explicit ownership and prerequisites.

#### Context Evidence

The current lessons repeat applied instructions already covered by their lab
guides. The accepted boundary moves all nine published fields from H100 focus
through Review into the owning practical guides; only Practice links stay
after lesson theory. Worked examples and execution become one Practice flow.

#### Design Details

Use the lesson presentation in FEAT-023: Objective, How it works, Practice
and final Mental model. Generate Practice links from explicit metadata.
Remove the nine applied fields from the
lesson schema and canonical lesson sources, without compatibility aliases.

Apply the same boundary inside How it works prose:
move lab-specific readiness checks, workload descriptions, commands and result
recipes into the owning guide, merging existing explanations rather than
appending duplicates. Keep conceptual definitions and useful hand-worked
reasoning in lessons. Keep necessary prerequisite explanations local to the guide; FEAT-026 removes
Theory preparation pointers and makes Practice concise. The catalog-wide residual-prose revision is implemented. Read-only editorial
review verified preservation and corrected prerequisite pointers; full-page
browser review remains pending.

Map every moved explanation to a meaningful existing lab before removing it.
Place hardware/qualification context in Before you start; keep worked
reasoning in Concepts and code path and concise commands in Practice; integrate evidence
and interpretation into Check your results, trade-offs into Investigate the
behavior, failure guidance into If something goes wrong, and answers/review
into Takeaways and next step. Keep distinct examples when the actual code or
measured scope differs, and label advanced extensions and later revisits.

Add previously missing lesson associations to the relevant existing lab:
Optimizations Lesson 1 to Lab 01, Inference Lesson 15 to Lab 30, and Custom CUDA
Lesson 16 to Lab 03's optional transpose-port evaluation. These additions do
not make later or optional material a prerequisite for earlier core execution.
Keep standalone commands, runtime behavior, source IDs, lesson numbering and
course prerequisites unchanged.

Keep conceptual diagrams beside remaining lesson theory. Move figures that
explain applied evidence into their lab sections. Detailed and overview
placement records explicitly declare their home as lesson or lab:SOURCE_STEM;
retain semantic lesson associations and an exact section label. Validate the
home, referenced source and section before rendering. Preserve one primary
figure, all accessible labels, and links from related lessons.

#### Selected Option

Use the existing deterministic Python builder, canonical Markdown, shared CSS
and standalone validators. Edit authored guides with a private source-to-owner
preservation audit; do not synthesize teaching from filenames or retain a
second lesson-shaped appendix inside every lab.

#### Alternatives Considered

Keeping both worked examples and lab execution in lessons preserves the
confusing parallel route. Removing applied text without integrating its unique
reasoning loses educational depth. Copying each tail to every associated lab
creates repetition and may imply that a lab implements an unrelated technique.
A new publication framework or executable wrapper is unnecessary.

#### Implementation Boundaries

Courses only; no lab logic, dependencies, target configuration or publication.
No change to skill source is required. The canonical Practice field follows
the reusable skill while each course profile keeps its explicit exceptions. The fixed stack has
no AI-agent or service architecture decision. This is a local reversible
content/schema refactor; apply ownership, preservation, input validation and
accessibility checks without an unrelated system-architecture redesign.

#### Test-First Success Criteria

- TDD-001: All 73 lessons use the new theory schema and exact Practice links;
  none retains a removed applied field or loses its conceptual opening.
- TDD-002: All 94 guides contain the seven current sections, including Practice,
  with supplied commands, numerical gates and distinct examples preserved.
- TDD-003: Missing/invalid lab ownership, obsolete section labels and invalid
  figure homes or section targets fail clearly rather than silently dropping text.
- TDD-004: All 108 figures remain accessible once; semantic lesson relationships
  and primary lesson/lab placement agree with the published destination.
- TDD-005: Source/HTML parity, TOC identities, independent review and original
  executable-source preservation pass for all five courses.

#### Validation Plan

Run a representative lesson-to-lab slice, focused schema/placement failures,
all course validators and the complete shared suite, bounded HTML/source
checks, lint, command syntax, link integrity and the private preservation audit.
Keep full-page browser and target-runtime qualification separate and pending.

#### Test Plan

Update old lesson-field assertions to inspect the owning guide when the content
moves. Test meaningful semantic claims at their new owner; do not delete them
or reduce depth checks merely to obtain a pass. Include shared-lab revisit
routes, optional CUDA Tile evaluation, AIPerf versus probe evidence and both
lesson and lab diagram homes.

#### Evaluation Plan

Independently read each course's moves: the learner can explain a technique
before opening its lab, then follow one coherent example-to-experiment flow.
Review actual overlap, terminology, numerical assumptions, supported modes and
prerequisite gates separately from structural assertions.

#### Rollout And Rollback

Preserve the task-start sources privately, update canonical schemas and sources
together, then rebuild all five HTML pages atomically. Compare only task-owned
changes and preserve unrelated work. No legacy rendering path is retained.

#### Done Definition

All five courses use theory followed by Practice links, practical guides
own the complete applied material, and current static/editorial checks pass.
Outstanding browser or target evidence remains explicit.

#### Implementation Evidence

Current series alignment covers all seven courses: 78 conceptual GPU lessons,
six text-only Slurm lessons and 110 executable labs, including the labs-only
advanced course. Canonical titles, direct numbered TOCs, semantic Practice
subheadings, moved-lab references and responsive layouts are aligned. The
inference serving lesson links the three Dynamo activities in the advanced
course. Executable lab, launcher and environment hashes remain unchanged.

Earlier feature-delivery evidence follows for provenance.

Lab-specific readiness, fixture API details, validation recipes and procedures
are consolidated into owning guides across all five courses. Definitions,
mathematical examples and algorithms remain in theory; prerequisites are
aligned. The canonical opening validator and its standalone copies preserve
table cells and fenced code with one shared normalization path.

All 73 lessons retain definition-led theory and finish with exact Practice labs
links. All 94 authored guides use the unified Practice section. A private
source-to-owner audit accounts for 669 fragments from 657 applied fields,
including explicit splits and documented semantic merges. Three prerequisite
definitions were added to retained mechanisms where moving the application
would otherwise remove necessary theory. Shared guides identify first execution,
later revisits and optional evaluations. Distinct paper calculations, supplied
mechanics and separately qualified engine campaigns retain explicit boundaries.

All 108 figures retain their SVG content and accessible labels. Ten applied
detailed diagrams now belong to lab sections; conceptual diagrams remain with
theory. Explicit homes, actual rendered placement, unique section identifiers
and cross-links are enforced by the shared builder and five standalone
validators. README, syllabi, visual records and semantic tests use the new
contract. No lab logic, command, dependency or target configuration changed.

The follow-up alignment moves the remaining packaging exercise from the Custom
CUDA capstone lesson to Lab 12's optional Practice extension, and network
path-discovery/comparison procedures from Optimization Lesson 11 to Lab 17.
Both lessons retain conceptual explanations. Training and Inference README
examples explicitly link their lesson prerequisites and practical guides.
Canonical visual-placement wording and the 73-lesson, 94-lab, 108-figure
inventory agree with the implemented catalog.

Standalone narrative validation now recognizes the renderer's Markdown table
syntax while preserving ordered cell text. HTML text extraction separates
cells and nested headings. Table normalization excludes fenced code, including
code blocks containing blank lines; literal pipes in prose and commands remain
part of the expected content.

#### Verification Evidence

Current series gate: all seven repository validators and generated parity pass;
1,019 full-suite CPU checks and two separately run loopback fixtures pass.
Final heading/style and prose follow-ups pass 318 and 91 overlapping focused
checks. All 24 final owned isolated headless Chrome 153.0.8010.48 cases pass
for seven courses and the catalog at 1440, 390 and 320 pixels, including
heading-in-viewport keyboard navigation, local scrollers, downloads and 200%
text reflow. Scoped Ruff, formatting, Markdown and whitespace checks pass.
Final code/security review found no blocking issue in the changed surfaces.
See docs/course-format-validation.md for exact HTML identities, generic skill
checker differences and the still-pending live GPU qualification lanes.

Earlier verification records below are historical and do not override the
current format/browser result.

Revision verification: all five standalone validators and 742 offline tests
pass. Negative controls cover altered/missing table cells and literal code
pipes with and without a blank line before a fence. Independent read-only
review closed preservation and prerequisite findings. Lab executables and
launchers remain unchanged. Full-page browser review is pending after local-file
access was denied. The installed bounded skill checker reports the same
pre-existing catalog-markup incompatibilities on HEAD and revised pages;
its gate is not claimed passed. No new target-runtime qualification is claimed.

Passed after alignment: 703 shared tests, five standalone validators,
source-to-HTML parity and five bounded HTML/source checks. The bounded checker
uses the requested Practice heading in place of its default execution heading;
all other checks ran unchanged. Nine new table controls accept correct output
and reject changed headers/cells, missing rows/tables, reordered cells, and
removed literal pipes in prose, shell pipelines and table-shaped heredocs.
The positive table case failed before the normalizer repair.

The original migration audit accounts for 669 fragments from 657 applied
fields and preserves 100 original guide command fences and 108 SVG payloads.
The follow-up alignment retains all 423 task-start files. Its source ledger
accounts for each learner-content replacement and relocation, and all original
commands in the six edited course documents remain unchanged. All lab sources,
launchers, diagrams, dependencies and shared styling remain unchanged in this
pass. The template and five standalone validator copies agree byte for byte.

The initial all-source checks covered 124 Python files, 168 Markdown files and
33 shell scripts/launchers. Changed Python passes Ruff and format checks;
changed Markdown, 154 shell-example syntax checks, publication safety and
whitespace checks pass. Independent read-only content and implementation
rechecks found no remaining actionable issue in the repairs.

These are source, editorial and local CPU/static results. Full-page browser,
keyboard and narrow-screen review remain pending under the existing browser
policy restriction. H100, CUDA, Slurm, profiler and live-engine qualification
remain separate; no speedup, publication or target-runtime claim is made.

<!-- /FEATURE: FEAT-021 -->

<!-- FEATURE: FEAT-022 reqs=REQ-013 status=ready delivery=implemented priority=P1 version=9 -->
### FEAT-022: Branch-published Nebius learning website

#### Requirements Covered

- REQ-013: Nebius course website and navigation.

#### Context Evidence

Five self-contained course pages and canonical course metadata exist. The repository is Apache-2.0 licensed and has no course-specific license. At task start, the website catalog, root HTML entry and Pages publishing configuration were absent.

#### Design Details

Current approved results/build-template revision: Rename six combined archives to `reference/<slug>-lab-results.zip` without changing their bytes; update callers, links and markers and remove the former retired-results mechanism. Use a stdlib publication preflight over Git tracked and nonignored untracked regular files from the repository root, overlay planned bytes once, and reject incomplete inventory, symlinks/submodules or limits above 104857600 bytes per file and 1000000000 bytes total. Run it in build and --check before writes. The exporter checks the archive cap without changing its lock/journal lifecycle. Wrapper prerequisites include Git. No automatic splitting, deletion, upload or history rewriting. Preserve all seven course contents/styles and existing results layout. Test thresholds, unrelated-root files, repeatability, before-write failures and actual downloads.


Use the existing shared download renderer for all six practical courses: H2 Practical labs, H3 Download results, then two paragraphs with non-linked labels and line breaks before their links. Render Download all lab results: followed by Grafana dashboards, Small and Large results, preserving `reference/<slug>-lab-results.zip` and its download attribute. Render Setup the lab environment: followed by Lab setup guide linking to ../lab-guide.html without a download attribute. Preserve course-downloads and labs anchors, existing styling, complete code listings and other reading content. Remove the duplicate shared setup/run/online-guide paragraph and its introductory dashboard reminder; the new setup link is the single introduction route. Retain per-lab dashboard anchors and pointers, source-owned guide links, and all lab teaching. Validate the new exact setup route within the download group instead of requiring the removed setup/run fragment pair. Remove exactly the six generated kit ZIPs and kit assembly/checking; retain source completeness and executable-mode coverage in sync tests. Allow only the exact additional guide route. Resource archive identity/checksum rules and exporter locks/journals remain unchanged. Update focused tests, docs and publication reviews; verify content hashes, all seven native validators, build/helper parity, mobile/desktop navigation and actual downloads. Local checks do not establish deployment.

Extend the local builder with a catalog renderer and embedded editorial stylesheet. Derive titles, hours, ordering and link destinations from canonical course metadata and the catalog registry; author concise summaries and outcomes alongside the renderer. Present a Soperator-first hero and learning path, then the two GPU foundation cards and three specialization cards. Add a catalog link and native HTML course switcher before each course's lesson contents. Keep the current course marked and link directly to its catalog siblings. FEAT-029 supplies the text-only introduction displayed first and FEAT-030 supplies the advanced laboratory course displayed seventh. Keep six direct sibling links per course, seven catalog cards, and the existing five-course GPU prerequisite graph with an explicit advanced-practice route. Each course metadata file carries a stable slug; standalone validators use that identity independently of the checkout folder name.

The compact footer states: Copyright 2026 Nebius B.V.; provided free of charge for learning and education; licensed under Apache License 2.0. Preserve third-party licensing. Embed the unmodified repository license in a collapsed license section and link locally to it. Reading resources remain embedded. Downloads use exact course-owned external ZIP paths; navigation exceptions remain limited to declared course routes.

A small root welcome page links to the catalog and repository. Add root `.nojekyll` and configure branch-based Pages from `main` `/` after the reviewed change merges. No custom workflow or runtime dependency is required.

Add a small executable `build-courses.sh` authoring entry point in the courses root. Resolve the script directory using Bash, check for `python3` and the adjacent builder, then invoke that builder without course names followed by `--check`, covering both HTML and the sole results ZIP per practical course. Align help and status output with both types, preserve error codes, and test repeated runs from another working directory for byte-identical outputs. The Python registry remains authoritative; no course list or rendering logic is duplicated. Accept no arguments or a bare `--` for rebuilding and `-h` / `--help` for dependency-free help; reject other arguments with status 2 before building. Preserve subprocess output and exit status, stop after a failed build, and announce success only after parity passes. Use existing shell help and terminal-only color conventions with clean redirected and `NO_COLOR` output.

After a successful build, print a blank line and bold cyan `Checking...` before invoking `--check`. The Python checker owns green current-status lines on stdout and red stale/missing-page diagnostics on stderr, including direct checker invocations. Detect terminal capability independently per stream, disable styling for `TERM=dumb` or any defined `NO_COLOR`, and reset each message. Flush successful results before any later failure. Keep existing diagnostic wording and stop at the first failed page; a missing course output receives a concise failure instead of a traceback. Do not parse Python output in Bash, add flags or change generated HTML.

Rebuilds are content-idempotent, with the existing per-file atomic replacement and possible timestamp changes. All selected pages and archives must render and validate before the first replacement. A source error leaves outputs unchanged. An I/O failure during per-file replacement can still leave earlier outputs refreshed; fix the error and rerun. The wrapper does not rewrite authored Markdown, synchronize repeated wording, install dependencies, run full validators, synchronize remote files or publish the site.

#### Course builder and download architecture

Retain the stdlib Python entry point and authored CSS, with focused course_builder modules for catalog/configuration, canonical parsing/inventory, strict Markdown, passive visual assets, reusable content/page rendering and orchestration. Keep CSS, teaching PNG/SVG and complete code listings embedded for offline reading; no browser JavaScript or framework is introduced. Shared page framing must preserve current markup and presentation.

Use one deterministic archive assembler for the builder and the source-owned run-labs exporter. Each practical course publishes `reference/<slug>-lab-results.zip` with grafana-dashboards/ and `small/<lab>/` plus `large/<lab>/`, as its only download. Original runtime files and executable permissions remain in the course tree and are delivered by sync-labs.sh; no lab-kit archive is generated or published. The resources ZIP contains exact dashboard and manifest-owned result bytes, no nested archives. Keep all original sources. Replace individual downloads/file lists with concise existing-style links and preserve their anchors. Rename the six former resources ZIPs once after byte preservation is proven; no ongoing legacy cleanup route remains. Soperator stays text-only. A saved HTML file supports reading; downloads need the companion files or the website.

Validate the complete selected output plan before mutations; perform atomic replacement per file and no writes in --check. Check mode verifies ZIP and HTML determinism. Reject unsupported Markdown rather than silently flattening it, declare intentional plain-text destinations by source, and validate all authored SVGs with one passive policy. Preserve exporter identity/checksum checks and existing locked/journaled evidence replacement. Measure site size because external ZIPs still consume Pages storage.

The architecture is implemented and locally validated against the captured dirty-worktree baseline. The current preservation, archive inventories, test and browser evidence are recorded in docs/course-architecture-validation.md and each course publication review. No live lab rerun, merge or publication is part of this refactor.

#### Selected Option

Generate the course website locally and include its HTML and external ZIPs in the reviewed commit; serve the complete selected branch root through GitHub Pages.

Expose the established build/check sequence through one Bash command; retain the Python builder as the sole rendering owner.

#### Alternatives Considered

A handwritten catalog would duplicate metadata. A custom deployment workflow is unnecessary for the selected whole-root publication. Noncommercial content terms were considered and rejected in favor of the existing Apache-2.0 license and a free educational offering statement.

Calling Python directly remains useful for selected-course and check-only maintenance, but requires maintainers to remember the sequence. Duplicating rendering in Bash or introducing a build framework adds unnecessary ownership and dependency overhead.

#### Implementation Boundaries

Website rendering, explicit course navigation, validators, documentation and authorized Pages settings only. Preserve course teaching, lab logic, dependencies, existing copyright notices and branch protection. Standalone validators must not require sibling directories or the enclosing repository license file.

#### Test-First Success Criteria

- TDD-001: The catalog remains current with metadata and rejects missing or stale committed output.
- TDD-002: All course navigation edges work; unknown destinations, misplaced relative links and external resources remain rejected.
- TDD-003: The embedded license matches its canonical source and the attribution introduces no additional usage restriction.

- TDD-004: The wrapper rebuilds and checks all registered pages from an unrelated directory and a path containing spaces; unchanged inputs yield identical HTML bytes across repeated runs. Lesson and embedded-guide edits reach generated output. Help, invalid arguments, missing prerequisites and build/check failures have the documented effects and statuses.

- TDD-005: Pseudo-terminal checks show the separated cyan check heading, green current results and red stale/missing diagnostics. Redirected streams and color opt-outs remain plain, streams are independent, and build/check failures suppress subsequent stages and final success. Generated HTML content is unchanged.

#### Validation Plan

Run focused tests, the shared pytest suite, all standalone validators and alignment. Review desktop/mobile and keyboard behavior locally, then verify deployed content and the Pages source revision independently.

For the wrapper, run Bash syntax, ShellCheck, executable/help/error checks and temporary stub failure scenarios. Rebuild an isolated copy of current course sources twice, compare HTML hashes, verify authored-input propagation and run all standalone course validators. Preserve existing working-tree generated pages during validation.

#### Test Plan

Add metadata, prerequisite, link graph, current-course marker, license parity and negative publication controls. Preserve existing course-content and presentation coverage.

#### Evaluation Plan

The learner can choose a course, understand its prerequisites, switch directly to another course and read the licensing terms without loading external resources.

#### Rollout And Rollback

Reuse the current branch and preserve its history. Merge through the required approving review, then enable HTTPS Pages from `main` `/`. Verify live routes before declaring publication complete. Revert website defects through the normal reviewed branch process.

The wrapper can be rolled back by removing its entry point and reverting only its associated documentation additions; preserve existing authored and generated changes.

#### Done Definition

The catalog, root welcome page, cross-course navigation and attribution are implemented and validated; the intended revision is independently confirmed on the live Pages site.

The authoring wrapper is complete when executable, documented and independently checked against the build, parity and failure contract. This local evidence does not establish live publication.

#### Implementation Evidence

Implemented six byte-preserving results ZIP renames, canonical link/helper/exporter alignment, removal of obsolete retirement routes, complete Git publication preflight and exact-byte size caps. Both source and project-installed run-labs archive callers agree; locks and evidence journals are unchanged. Wrapper and documentation describe Git/Python prerequisites, results downloads and local validation. All teaching and styles remain intact. Earlier evidence below applies to preceding revisions.

The single-results-download revision is implemented. All six practical pages use separate labels and links for combined results and shared setup, without the duplicate introductory setup/dashboard paragraphs. Kit assembly and the six generated kit ZIPs are removed; all original source members remain. The wrapper help/status and source sync tests cover the resulting HTML/ZIP workflow. Older implementation evidence below describes prior revisions.

Architecture update, 2026-09-27: the stable Python CLI delegates to focused course_builder modules and the shared course_archives assembler. The builder preflights all selected HTML, ZIPs and generated link targets before per-file atomic replacement. Check mode is read-only. Source-scoped Markdown destinations and a shared passive SVG policy fail on unsupported inputs. The preceding architecture version exposed two external ZIPs per practical course, combining 116 dashboards and 220 existing profiles in the resources archives. Six obsolete generated results ZIPs were retired after exact member-byte proof. Only the run-labs bundle function changed; export locks and journals remain intact, and source/project-installed skill payloads agree.

Check-output color revision: implemented the separated bold cyan checking heading in the Bash wrapper and source-owned green current/red failed check statuses in the Python builder. Successful results flush to stdout; failures retain existing diagnostic wording and exit through stderr at the first failed page. Missing course outputs now have a concise diagnostic. Stream-specific terminal detection, `TERM=dumb`, defined `NO_COLOR` and style resets apply consistently. Help, the maintainer README and changelog describe this presentation behavior.

Wrapper revision: implemented the executable courses-root entry point, dependency-free help, strict argument handling, prerequisite checks, directory-independent builder invocation, build-then-check flow, exact failure propagation and terminal-only status color. The maintainer README and repository changelog describe the command, source ownership, repeatability and local-only scope. The existing Python renderer and course sources are unchanged.

Current series alignment covers all seven courses: 78 conceptual GPU lessons,
six text-only Slurm lessons and 110 executable labs, including the labs-only
advanced course. Canonical titles, direct numbered TOCs, semantic Practice
subheadings, moved-lab references and responsive layouts are aligned. The
inference serving lesson links the three Dynamo activities in the advanced
course. Executable lab, launcher and environment hashes remain unchanged.

Earlier feature-delivery evidence follows for provenance.

Implemented the catalog renderer and embedded stylesheet, root welcome page and .nojekyll, course switchers, complete embedded Apache license and attribution, canonical metadata slugs, scoped navigation validation, publication regression tests and authoring documentation.

#### Verification Evidence

Alignment follow-up preserves the existing contract: reject empty symlinked publication roots before inventory and validate archive member names lexically rather than against the source filesystem. Two negative controls fail before repair; 60 focused tests, full build/check, all seven native validators, helper parity and scoped lint pass afterward. All 15 generated output hashes remain identical. Final independent code/security review found no further blockers; earlier browser evidence remains bound to unchanged artifacts.

All seven native validators, 337 focused tests with three optional Torch skips, and 23 final budget/wrapper regressions pass. Three actual wrapper builds (two from another directory) yield identical bytes for all 15 outputs. Independent hash comparison preserves all six archive bytes, all seven course pages outside approved names/wording and 3329 protected source/evidence files. Isolated headless Chrome passed 18 desktop/narrow cases, six actual download checksums, keyboard setup navigation and seven offline pages. Source/helper parity, scoped lint and final code/security review pass. Estimated full publication size is about 690.0 MB, with 310.0 MB headroom; largest file 95.5 MiB. Browser resources were closed. No new lab execution, commit or deployment is claimed. Earlier evidence below is historical.

Current download revision: 338 distinct focused cases passed, with all 122 affected cases rerun after the final introductory cleanup. All seven native validators, HTML/ZIP freshness, helper parity and scoped lint passed. Three real wrapper runs on the final inputs, including two from another directory, produced identical hashes for all 15 generated outputs; failure-boundary tests preserve exit codes. Independent preservation confirms all six resources ZIPs unchanged, all 127 embedded source listings retained, and course markup identical outside the agreed introduction/heading changes. Final browser checks cover 18 desktop/mobile cases, six actual ZIP checksums and seven saved pages with JavaScript disabled. Generic skill-checker findings are unchanged from the task baseline and are not reported as passing. Source/installed evidence-reference parity and changed-scope code/security review passed. See docs/course-architecture-validation.md and current publication reviews for artifact hashes, visual checks and estimated hosting size. No live lab rerun, merge or deployment was performed. Earlier evidence below is historical.

Architecture update, 2026-09-27: 997 distinct tests across broad and focused runs covering 27 relevant modules, all seven standalone validators, generated HTML/ZIP freshness, helper parity and scoped lint pass. The audit preserves 3,321 protected source/evidence files and all generated markup outside the agreed download changes. Course HTML falls from 9,240,589 to 3,440,962 bytes. Isolated headless Chrome 153 passed all seven courses at 1440/390/320 pixels with keyboard, fragments, loaded images, no horizontal overflow and 200% text reflow; captures were visually inspected. All twelve actual ZIP downloads matched SHA-256 and all seven offline pages remained readable. The conservative full-repository hosted-size estimate is about 691 MB, around 309 MB below the published 1 GB Pages limit. The largest resources ZIP is 95.5 MiB, below the 100 MiB Git file limit. These are historical local source/browser results, not a new live lab campaign or deployed-revision proof. See docs/course-architecture-validation.md for evidence boundaries and the final publication reviews for HTML hashes.

Check-output color revision: pseudo-terminal fixtures verify all eight green current results, red stale and missing catalog/course failures, independent stdout/stderr redirection, plain redirected output, empty/nonempty `NO_COLOR`, `TERM=dumb`, prior-success ordering and preserved stop-at-first-failure behavior. Wrapper fixtures verify the cyan heading after a successful build, its absence after a failed build, exact failure status propagation and no false final success. All 42 focused catalog tests pass in an isolated source copy with the existing repository welcome page and license. In-memory renderer hashes and all eight working-tree HTML hashes are unchanged. Bash syntax, ShellCheck, Ruff lint/format, Markdown lint, scoped whitespace and independent code/security review pass. This verifies console presentation and local checking only; no generated-page update or live publication is claimed. Earlier wrapper evidence below applies to its initial implementation.

Wrapper revision: Bash syntax, ShellCheck, executable mode, Markdown lint and changed-scope whitespace checks pass. Independent read-only code and security review found no concrete findings. In an isolated copy, invocation from the courses directory and an unrelated directory, a checkout path containing spaces, system Bash and a bare `--` all pass. Two rebuilds produce identical hashes for the catalog and seven course pages. Lesson and embedded-guide edits both reach generated output; restoring the sources restores the original output hashes. Temporary fixtures verify help without Python, invalid arguments, missing prerequisites, build/check exit statuses and suppression of false success. All six practical course validators and the text-only validator pass against rebuilt outputs. The eight working-tree HTML files remain byte-identical to their pre-validation state. This verifies the local wrapper only; the broader website feature retains its existing implemented state and live publication remains separate.

Current series gate: all seven repository validators and generated parity pass;
1,019 full-suite CPU checks and two separately run loopback fixtures pass.
Final heading/style and prose follow-ups pass 318 and 91 overlapping focused
checks. All 24 final owned isolated headless Chrome 153.0.8010.48 cases pass
for seven courses and the catalog at 1440, 390 and 320 pixels, including
heading-in-viewport keyboard navigation, local scrollers, downloads and 200%
text reflow. Scoped Ruff, formatting, Markdown and whitespace checks pass.
Final code/security review found no blocking issue in the changed surfaces.
See docs/course-format-validation.md for exact HTML identities, generic skill
checker differences and the still-pending live GPU qualification lanes.

Earlier verification records below are historical and do not override the
current format/browser result.

All 740 offline pytest tests passed. After the final favicon edit and rebuild, 78 focused publication/content tests, generated-source parity and all five standalone validators passed. Tests cover renamed standalone course copies, every local route, stale/missing catalog output, metadata escaping, navigation restrictions and license parity. Ruff, formatting, configured Markdown lint and whitespace checks passed. Browser checks covered desktop, tablet, 390px and 320px layouts; all five courses, root and catalog routes; keyboard switching and visible focus; embedded licensing; no horizontal overflow or external loaded resources. The final browser console had no errors. Existing GitHub CodeQL checks passed on the website implementation commit. Pages configuration and live deployed-revision verification remain pending the required approving review and merge.

<!-- /FEATURE: FEAT-022 -->

<!-- FEATURE: FEAT-023 reqs=REQ-002,REQ-009 status=ready delivery=implemented priority=P0 version=5 -->
### FEAT-023: Coherent conceptual lessons with integrated diagrams

#### Requirements Covered

- REQ-002: Consistent lesson structure and meaningful diagrams.
- REQ-009: Conceptual titles, complete causal teaching and terminology.

#### Context Evidence

The 73 lessons contain useful explanations but repeat template labels and
frequently enumerate components in their titles. Some summaries introduce
new concepts and some lessons lack a diagram at their explanatory home.

#### Design Details

Use Objective, How it works, Practice, Mental model in that
order, followed only by optional References. Every conceptual lesson includes
its key terms in the single course-wide A–Z glossary; render that glossary
as a semantic definition list without local glossary sections. Keep course-level Glossary immediately
before References in both content and navigation. Preserve the text-only and
labs-only profiles, lesson identities, existing teaching and executable work. Render each field as a semantic h3 under its numbered h2 lesson,
matching the text-only course and preserving a compact visual hierarchy.
Start How it works with the central definition; integrate prerequisite
connections and purpose into connected teaching. Preserve course introductions,
worked conceptual arithmetic and unique qualifications. Move substantial
teaching out of summaries into the explanation and remove redundant recall
prompts; practical guides retain retrieval and transfer activities.

Give each lesson a concise subject title, updating syllabus and local links.
Expand unfamiliar abbreviations in context, with CPU/GPU exempt from forced
expansion. Preserve precise technology names in the explanatory prose.
Use existing original diagrams where they teach the core concept, add authored
diagrams for uncovered lessons, and embed each within How it works with a
caption and accessible description. Preserve a single primary figure home.

#### Selected Option

Refine canonical Markdown and existing overview/manifest placement records;
update the deterministic renderer and identical standalone validators.

#### Alternatives Considered

Renaming fields alone leaves disconnected teaching. Deleting unique prerequisite
or summary content loses explanations. Duplicating lab figures changes their
ownership; add a conceptual diagram where a lesson has no suitable primary one.

#### Implementation Boundaries

Courses only. Preserve pre-existing work, all existing lab identities and executable
behavior. No dependencies, installations, skill-source edits or publication.

#### Test-First Success Criteria

- TDD-001: All 84 conceptual lessons use the exact ordered fields; retired
  fields fail; optional References follow Mental model and end the lesson. Local appendix fields fail; course appendices render once in the declared closing order.
- TDD-002: Every How it works contains an accessible core diagram; missing or
  externally placed diagrams fail even when another lesson has extra figures.
- TDD-003: Full canonical prose parity, title/link consistency and unchanged
  executable sources hold throughout the six conceptual courses; the seventh
  course retains its labs-only profile.

#### Validation Plan

Review all lessons semantically, check new terms against official references,
run focused regression tests followed by catalog validation and the shared
suite. Inspect desktop and narrow-screen publications where tools permit.

#### Test Plan

Retain meaningful content assertions under their new section ownership. Add
negative tests for order, missing definitions and missing/escaped diagrams.

#### Evaluation Plan

Trace the central concept from definition through operation and consequence.
Check every diagram against the prose and every summary for new terminology.

#### Rollout And Rollback

Build local HTML atomically; no external publishing. Preserve a task-start
snapshot so the changed content can be compared without reverting other work.

#### Done Definition

The full catalog follows the selected lesson pattern with useful teaching
preserved, source/static validation passed and other evidence lanes explicit.

#### Implementation Evidence

All 84 conceptual lessons and five performance-tool guides now include local
A–Z glossaries after Mental model. The shared renderer emits definition lists
and permits optional References only last; standalone validators reject
missing, unsorted or misplaced glossaries and verify each definition against
its owning source. The seven course-level glossaries retain their existing
position immediately before References. Teaching and all 357 protected lab,
launcher, environment, guide and diagram files remain unchanged.

Current series alignment covers all seven courses: 78 conceptual GPU lessons,
six text-only Slurm lessons and 110 executable labs, including the labs-only
advanced course. Canonical titles, direct numbered TOCs, semantic Practice
subheadings, moved-lab references and responsive layouts are aligned. The
inference serving lesson links the three Dynamo activities in the advanced
course. Executable lab, launcher and environment hashes remain unchanged.

Earlier feature-delivery evidence follows for provenance.

All 73 lessons use conceptual titles and the four ordered sections. Definitions,
causal mechanisms, useful prerequisite connections and purpose are integrated
within How it works; final summaries contain already-taught concepts. Added
worked reasoning and unfamiliar-term definitions, corrected ambiguous L2 and
product-name expansions, and reconciled attention, speculative sampling and
parallelism explanations with primary references. Added 12 overview diagrams
and one branching adapter diagram, giving 122 figures and core coverage for
every lesson. All executable labs remain byte-identical to the task-start
snapshot; one lab guide changes only its reference to a renamed lesson.

The renderer places diagrams inside the explanation while retaining full figure
width and readable prose. Identical standalone validators enforce field order,
per-lesson diagram containment and full narrative parity. Syllabi, lesson links,
README, references and focused regression tests follow the new contract.

#### Verification Evidence

Glossary-order verification: all seven course validators, 230 focused tests,
generated/helper parity, scoped Ruff/formatting, Markdown and whitespace checks
pass. Independent preservation checks compare every original lesson body and
357 protected files with the task-start snapshot. All 21 owned isolated
headless Chrome 153.0.8010.53 cases pass at 1440, 390 and 320 pixels, checking
course/lesson order, keyboard navigation, local scrollers and 200% text reflow.
Publication reviews bind these observations to exact HTML hashes. The generic
skill checker no longer reports missing lesson glossaries; its five GPU, six
text-only and seven labs-only pre-existing markup/profile diagnostics remain.
No installation, live lab run or external publication is claimed. Earlier
format evidence below predates this addition.

Current series gate: all seven repository validators and generated parity pass;
1,019 full-suite CPU checks and two separately run loopback fixtures pass.
Final heading/style and prose follow-ups pass 318 and 91 overlapping focused
checks. All 24 final owned isolated headless Chrome 153.0.8010.48 cases pass
for seven courses and the catalog at 1440, 390 and 320 pixels, including
heading-in-viewport keyboard navigation, local scrollers, downloads and 200%
text reflow. Scoped Ruff, formatting, Markdown and whitespace checks pass.
Final code/security review found no blocking issue in the changed surfaces.
See docs/course-format-validation.md for exact HTML identities, generic skill
checker differences and the still-pending live GPU qualification lanes.

Earlier verification records below are historical and do not override the
current format/browser result.

All five course validators and generated-source parity pass. The complete
shared suite reports 632 passed, 133 skipped and one missing-PyTorch failure;
the identical failure was independently reproduced on the task-start snapshot.
The final focused editorial/publication/diagram suite reports 261 passed and
eight dependency skips. Missing/escaped diagrams, incorrect field order, retired fields, swapped
or truncated explanations, and altered table/list literals have negative checks.
Ruff, formatting, configured Markdown lint and whitespace checks pass.

Independent read-only reviews cover all 73 lessons and changed rendering code.
All 13 new diagram assets were rendered and visually inspected; full-page
desktop/mobile browser review is pending because local-page access was blocked
by browser security policy. The installed skill checker reports the same six
format incompatibilities on baseline and current HTML, with embedded-source
membership and bytes passing. No installed-environment, runtime activation or
live H100 qualification is claimed. Delivery remains implemented while those
publication/qualification lanes remain open.

<!-- /FEATURE: FEAT-023 -->

<!-- FEATURE: FEAT-024 reqs=REQ-014 status=ready delivery=implemented priority=P0 version=1 -->
### FEAT-024: NVIDIA terminology review and contextual corrections

#### Requirements Covered

- REQ-014: Authoritative technical terminology throughout the catalog.

#### Context Evidence

The catalog has 73 lessons and 94 numbered lab guides. Fundamentals Lab 01 uses
execution boundary as an unexplained timing label, motivating a complete review
of related vocabulary rather than a single phrase replacement.

#### Design Details

Read each canonical lesson and guide with its technical sources; inspect lab
implementations before changing claims. Use CUDA and NVIDIA library/profiler
references for their concepts and official framework documentation for framework
semantics. Explain measurement scope through CPU timers, CUDA events and the
operations included. Separate data storage, launch, execution, transfer and
synchronization, retaining useful plain-language explanations. Reconcile
connected glossary entries, SVG labels, captions, metadata and reference links.
Keep detailed audit inventories outside the learner publication.

#### Selected Option

Contextual corrections in the existing canonical files and deterministic
regeneration of the five textbooks.

#### Alternatives Considered

A word blacklist cannot establish correct meaning in context. Renaming every
ordinary word into jargon reduces clarity; verbatim vendor prose loses the
course's original teaching and can introduce licensing problems.

#### Implementation Boundaries

Courses only; preserve code behavior, identifiers, commands, prerequisites and
unrelated changes. Read-only helpers may audit independent course groups; the
root agent owns edits, source decisions and paired spec publication.

#### Test-First Success Criteria

- TDD-001: Existing full-prose/source parity and course validators remain green.
- TDD-002: Reviewed terminology has the same meaning in lessons, guides,
  glossary entries and related diagrams; concrete timing labels name what runs.

#### Validation Plan

Verify official definitions, read corrected passages in context, regenerate all
pages, run the existing offline checks and inspect changed visuals when permitted.

#### Test Plan

Use existing editorial, diagram-fit, navigation, source-parity and full offline
regression checks. Avoid tests that merely freeze replacement prose.

#### Evaluation Plan

Confirm complete lesson/lab audit coverage and check that students can relate
terms to official references and actual supplied implementations.

#### Rollout And Rollback

Atomic local HTML generation and focused version-controlled source changes;
no external publication in this task.

#### Done Definition

All lessons and guides reviewed, actionable terminology gaps corrected, related
sources aligned and verification lanes reported independently.

#### Implementation Evidence

Reviewed all 73 lessons and 94 lab guides against official NVIDIA and owning
framework definitions, checking actual implementations before changing claims.
Contextual corrections distinguish CPU timers and CUDA events, storage residency
and execution residency, memory spaces and caches, coalescing and contiguity,
theoretical and achieved occupancy, warp participation masks, thread-block
clusters, asynchronous copy APIs, checkpoint variants and serving metrics.

Aligned glossaries, related diagrams/captions, syllabuses, worksheets, references
and authoring guidance; rebuilt all five textbooks. Seventeen SVG figures changed.
Lab source behavior, commands and result keys remain unchanged. Three existing
editorial tests now check the clarified concepts or exact result keys instead of
obsolete prose; the renderer change only updates an asynchronous-copy label.

#### Verification Evidence

All 766 offline tests, five standalone/catalog validators, generated-source
parity, changed-source lint/format and whitespace checks pass. Source listing
membership and bytes match all 94 canonical lab sources. Independent read-only
review found no outstanding defect in the corrected technical material.

Rendered and inspected all 17 changed figures and corrected a roofline-label
collision. Browser checks loaded all five textbooks at desktop, 390 and 320 pixel
widths without document-level horizontal overflow or missing fragment targets.
Sampled text/reflow, lab navigation, keyboard TOC operation and source scrolling
passed; exhaustive page-by-page accessibility and zoom testing remain unclaimed.

The installed course-skill checker reports eight identical format failures on
current and task-start snapshots. This is baseline-equivalent failure, not a
passing checker gate. Per-course PUBLICATION-REVIEW.md records these evidence
limits. Installed-environment, runtime activation and live CUDA/H100/Slurm
qualification remain separate and pending.

<!-- /FEATURE: FEAT-024 -->

<!-- FEATURE: FEAT-025 reqs=REQ-015 status=ready delivery=verified priority=P1 version=6 -->
### FEAT-025: Discover, sync and enter the Slurm login node

#### Requirements Covered

- REQ-015: Incremental local course sync to a Slurm login node.

#### Context Evidence

`sync-labs.sh` already selects complete course packages using Git, transfers them
with one rsync invocation and supports explicit root/default and user overrides.
The existing tests use real rsync and isolated SSH receivers. The external login
Service and internal headless Service both carry the login component label in
[Soperator's renderer](https://github.com/nebius/soperator/blob/main/internal/render/login/service.go).

#### Design Details

Keep the Bash 3.2, Git, OpenSSH and rsync path. Locate the source beside the script,
build a NUL-delimited manifest of current tracked and non-ignored untracked course
files plus catalog HTML, and preserve safe links, paths, permissions and times.
Local source wins, remote-only files survive, and unchanged repeats transfer no
file contents. Interrupted transfers remain rerunnable. Keep destination guards,
read-only preflight, normal host verification and signal-aware cancellation.

Accept zero or one positional `[user@]TARGET`. Explicit DNS, IPv4/IPv6 and SSH
alias targets bypass Kubernetes. Bare targets select root regardless of SSH
configuration User. Preserve `--dry-run`, `--dest`, `--port`, `--identity`, help
and the option terminator. Parse and validate inputs before external access.
Normal runs require terminal stdin; dry runs do not.

For omitted targets, require kubectl, read and pin its current context, and query
Services in namespace soperator with label app.kubernetes.io/component=login.
Use a 15-second request timeout and a checked JSONPath projection whose record
boundaries and empty fields survive Bash parsing; no jq or Python dependency.
Select exactly one LoadBalancer Service, excluding ClusterIP/headless and
NodePort Services. Require one TCP Service port and one distinct external
endpoint from status.loadBalancer.ingress; prefer each entry's ip, otherwise its
hostname, and deduplicate identical addresses. An explicit --port overrides the
validated Service port. Never use ClusterIP, nodePort or requested loadBalancerIP
as the discovered endpoint. Missing/pending, ambiguous or malformed output fails
before preflight and transfer. Report candidate names/addresses on ambiguity and
suggest an explicit target. Do not change kubeconfig, authenticate interactively
on the user's behalf, poll indefinitely or silently choose a candidate.

Use one normalized target and shared SSH options for preflight, transfer and
interactive access. Keep -T on preflight and transfer only. After a successful
normal transfer, print the destination, remove temporary transfer state, reset
sync traps and exec foreground ssh -t. Its POSIX-quoted remote command changes
to the selected directory beneath remote HOME and execs the account shell via
"${SHELL:-/bin/sh}" -il. A failed directory change does not launch a shell. The
remote POSIX-compatible shell must support interactive/login options. SSH owns
terminal signals after handoff and its exit status becomes the script status.
Dry runs stop after the preview; discovery/preflight/transfer errors never launch
interactive SSH.

#### Selected Option

Deterministic Bash orchestration with conditional kubectl discovery and a final
foreground SSH handoff. Kubernetes owns endpoint identity, Git owns selection,
rsync owns transfer, and SSH owns authentication and the terminal. No application
or AI stack selection is needed; there are no model calls or agents in the script.

#### Alternatives Considered

Manual discovery and a separate SSH command leave the requested workflow
incomplete. Selecting the first endpoint can target the wrong cluster. A chooser,
new JSON runtime, sync database or compatibility path adds unnecessary behavior.
Skipping SSH without a terminal contradicts the selected fail-fast contract.

#### Implementation Boundaries

Script, focused tests, catalog README and canonical spec pair only. Preserve
unrelated edits. Do not provision accounts, install remote dependencies, change
credentials or infrastructure, submit jobs or perform live transfers for tests.
Remote home must be shared with workers. This local reversible change needs
focused correctness/security review rather than a full architecture review.

#### Test-First Success Criteria

- TDD-001: Existing complete-package, local-wins, remote-only, incremental,
  metadata-only and interrupted-transfer convergence cases continue passing.
- TDD-002: Explicit targets bypass kubectl and preserve root/user, identity,
  destination and port across preflight, transfer and interactive SSH.
- TDD-003: Discovery selects only the unique external login Service/address,
  honors the Service port and explicit override, and fails before transfer for
  missing dependencies, current-context/API failures, malformed, pending or
  ambiguous responses.
- TDD-004: Terminal runs hand off only after success, clean temporary state,
  enter the selected folder and return SSH status. Terminal signals belong to
  SSH after handoff. Nonterminal normal runs fail before transfer; dry runs never
  launch a shell or create a missing remote destination.

#### Validation Plan

Run Bash 3.2 syntax, ShellCheck, focused pytest, Python lint/format, Markdown
checks, canonical paired-spec validation and align on the changed surfaces.

#### Test Plan

Extend disposable Git/real-rsync tests with controlled kubectl and SSH programs.
Use pseudo-terminals for normal invocations and a foreground terminal handoff
case. Exercise stdin without a terminal separately. Test JSONPath against a
local fake Kubernetes API when kubectl is available, without live cluster calls.

#### Evaluation Plan

Help and README start with no-target, bare-IP and explicit-user commands. A learner
arrives in the synced destination after transfer. Fixture tests establish script
behavior, not actual account authorization, cluster reachability or GPU readiness.

#### Rollout And Rollback

Ship the executable script without an installation step. No-target discovery
requires configured kubectl access to soperator; explicit targets need no kubectl.
Normal invocations now require a terminal and open SSH automatically. Dry preview
remains available. Roll back the script/docs revision without deleting remote
files; rerunning sync converges matching sources and retains remote-only work.

#### Done Definition

All invocation forms and failure paths pass focused tests, documentation and
paired specs match the delivered behavior, and local versus live evidence is
reported separately.

#### Implementation Evidence

The script accepts an omitted target, queries the pinned current Kubernetes
context for login-labeled Services, validates the unique LoadBalancer endpoint
and TCP port, and normalizes discovery to root. Explicit targets bypass kubectl.
Normal invocations require terminal stdin. Transfer and preflight retain -T;
a successful sync cleans temporary state and execs foreground ssh -t with the
shared account, identity and port. The quoted remote command enters the chosen
folder and launches the account's interactive login shell. Help and README
present discovery, explicit targets, ambiguity handling and preview behavior.

#### Verification Evidence

All 94 focused sync tests pass locally, including the preceding 52-test baseline.
Real rsync fixtures verify complete packages, incremental bytes, remote-only
preservation, permissions, unchanged repeats and interrupted retry. Controlled
kubectl tests cover endpoint/port selection, context/API failures, pending and
ambiguous results, unsafe input and explicit-target bypass. Two tests use real
kubectl against an isolated local Kubernetes HTTP fixture to check the actual
JSONPath projection, namespace/label query, endpoint port and pending ingress.

Pseudo-terminal tests verify all three SSH phases, root/user and option parity,
shell location, cleanup before handoff, SSH status propagation and foreground
Ctrl+C ownership. Failure and dry-run tests prove no interactive shell starts;
nonterminal normal runs stop before discovery or transfer. Test fixtures required
an explicit POSIX shell on their restricted PATH and disabled PTY echo to avoid
macOS terminal-drain blocking; these changes belong to the test harness.

Bash 3.2 syntax, ShellCheck, Ruff lint/format, configured Markdown lint and scoped
whitespace checks pass. Independent changed-scope code/security review found no
blocking issue. Paired-spec validation passes. No live Kubernetes discovery,
real SSH authentication, remote upload or GPU/Slurm validation was performed;
local acceptance does not establish those environment capabilities.

<!-- /FEATURE: FEAT-025 -->

<!-- FEATURE: FEAT-026 reqs=REQ-010,REQ-016 status=ready delivery=verified priority=P1 version=1 -->
### FEAT-026: Local lab explanations and setup-only Lab 00

#### Requirements Covered

- REQ-010: Preserve complete, source-matched practical teaching.
- REQ-016: Remove reading detours and provide concise shared environment setup.

#### Context Evidence

All five courses have repeated Theory preparation reading lists. Practice
sections mix worked theory, navigation, setup and execution. Existing Lab 00
sources implement distributed or compiler/device verification rather than
cluster provisioning and login access.

#### Design Details

Keep each purpose paragraph first. Replace reading referrals with essential
local explanations; preserve distinct calculations in Concepts and code path,
questions in Investigate the behavior and optional extensions in Takeaways.
Practice retains supported commands and short experiment-specific instructions.

Introduce explicit setup_guide metadata pointing to reference/setup.md. Render
its full prose before numbered executable labs, with title Lab 00: Setting up
lab environment and anchor lab-00-setting-up-lab-environment. Validate its exact
identity and five setup steps separately from executable seven-section guides.
No related-lesson panel or executable listing belongs to this setup guide.

Preserve platform checks as dedicated verification labs: Fundamentals 13,
Optimizations 21, Training 34, Inference 37 and Custom CUDA 13. Rename source,
result identity and guide together, update all active references and membership,
and retain existing execution contracts. Keep optional CUDA cluster-probe
teaching in its own probe lab.

#### Selected Option

A setup-only document per standalone package plus explicitly preserved
verification labs gives a short common entry without coupling runtime packages.

#### Alternatives Considered

Reject attaching an old preflight executable to setup prose, dropping the
platform checks, or introducing unnumbered source exceptions to parity checks.

#### Implementation Boundaries

Changes stay within courses. Preserve unrelated edits, existing sync behavior,
public-safe examples and separate static, browser and live evidence. No cloud
commands, installs or publishing run during authoring.

#### Test-First Success Criteria

- TDD-001: Missing, stale or misidentified setup guides fail validation.
- TDD-002: All guides omit Theory preparation and Practice reading detours.
- TDD-003: Renamed verification sources retain source/HTML and lesson parity.

#### Validation Plan

Review complete changed prose, exact supported commands, guide associations,
source listings, generated parity and bounded publication-safety checks.

#### Test Plan

Run focused guide, content, sequence and renderer tests, then catalog validators
and the existing offline test suite where available.

#### Evaluation Plan

Inspect the setup reading route and representative lab rendering. Keep browser
and GPU/runtime gates distinct from source checks.

#### Rollout And Rollback

Rebuild all five standalone textbooks atomically through the existing builder.
Review the focused diff; no deployment or publication is part of this change.

#### Done Definition

All lab reading lists are removed with useful teaching retained, setup is
concise and source-free, old preflight identities are replaced consistently,
and source/static checks pass with remaining evidence lanes stated separately.

#### Implementation Evidence

Revised all 94 executable lab guides, retaining concise purposes and local
explanations while removing Theory preparation reading lists. Practice keeps
supported commands and experiment-specific controls. Added five setup-only
Lab 00 guides, renderer/metadata support and standalone validation. Preserved
verification sources under their new identities and moved the optional CUDA
probe exercise to its owning lab. Lesson links, syllabi, READMEs, diagrams,
source listings and tests agree. Setup headings wrap at enlarged text sizes.

#### Verification Evidence

All 879 offline tests, five course validators and exact generated parity pass.
The preservation audit confirms all 100 original Bash blocks and all executable
source behavior remain; only five preflight identities changed. Changed-source
lint and independent content/code/security review pass. The paired specs are
validated separately from runtime evidence.

Owned isolated headless Chrome 153.0.8010.48 checks all five pages at 1440, 390
and 320 pixels: normal layout, fragment targets, keyboard TOC and command
scrolling pass. The setup guide fits 200% root text at 320 pixels. Existing
whole-page enlarged-text overflow is identical before and after, and remains
an open publication gate. Publication reviews record artifact digests, visual
samples, trace and cleanup. The installed course-skill checker has the same
eight pre-existing format findings on task-start and revised pages; source
membership and bytes pass. Installed-environment, Linux/CUDA activation and
live H100/Slurm gates remain pending. No cloud operation or lab execution ran.

<!-- /FEATURE: FEAT-026 -->

<!-- FEATURE: FEAT-027 reqs=REQ-017 status=ready delivery=implemented priority=P1 version=10 -->
### FEAT-027: GPU profiling and per-lab Grafana evidence

#### Requirements Covered

- REQ-017: GPU profiling and per-lab Grafana evidence.

#### Context Evidence

Existing course result writers, seven-section lab guides and cxcli Grafana catalog, ordinary-app workflow and private cluster handoff provide the integration boundaries.

#### Design Details

Compute selectors match full demangled kernel names with simplification disabled. Generic function names can carry the GEMM identity only in template arguments; keep the existing NVTX and launch-count gates. Inference Labs 09, 17 and 23 and Training Labs 02, 06, 07, 14, 21, 22 and 30 add capture-only operation ranges while preserving clean callable behavior and algorithm order. Training Lab 06 selects its first objective reduction, without a matrix filter. The other affected selectors use matrix names, including GEMV; Training Lab 07 diagnoses generation and Lab 22 diagnoses BF16, not the complete update or FP8 path.

Optimization Labs 19 and 20 select their pipeline GEMM for Compute: consume_batch for H2D, and produce_output with the kernel expression .*(gemm|nvjet).* for D2H. The latter excludes input fill within its range; both exclude weight initialization. With default arguments the first selected GEMM is a warmup call. Treat its counters as diagnostic; use the full Systems reports for copy, compute and CPU-consumer ordering, and separate unprofiled trials for whole-loop timing.

Optimization Lab 14's compute comparison treats dtype and logical minimum I/O bytes as derived from its declared mode change, while preserving width, input scaling and useful operations. Repeating the same mode retains both derived fields. Lab 15 selects uniform_tail_probe inside the opt-in tail_measure range; only measured probe launches enter that range, so Compute excludes input initialization, warmup, the compilation probe and sentinel validation. Its single capture covers the first measured grid; Systems retains the complete task and grid survey.

Optimization Lab 07 keeps internal PyTorch trace export and external NVIDIA profiling in separate runs. Its Compute command selects the projection NVTX range around matrix multiplication; the first matching launch is a warmup GEMM with default arguments. Systems retains the full workload range. Treat both internal-profiler repetitions and native captures as diagnostic evidence, with no clean application-timing claim.

Use one shared implementation copied into each standalone course. Store per-lab explicit measurement and profiling recipes beside metadata; generate stable Grafana JSON and embed its complete bytes. Add opt-in NVTX capture without changing clean benchmark boundaries. Publish both selected slots atomically using the official Python client and validate readback through the existing local metrics database. Serialize each workspace, use numeric provenance values rather than arbitrary run labels, and retain full provenance in private artifacts. Install private Grafana and the separate Pushgateway App through cxcli grafana install with --pushgateway. Course discovery verifies their accepted identities and one native cxcli-pushgateway scrape; it creates no infrastructure or catalog. Use candidate Nsight Systems 2026.5.1 and Compute 2026.3.1 until live qualified. Preserve existing lesson numbers and standalone package rules.

Complete cxcli monitoring installation and connection discovery before importing dashboards. Select
or create the matching course folder through Grafana and explicitly export its
observed UID for the selected course. Setup exports the prepared metrics
data-source UID. Each guide imports its assigned JSON immediately through
`nebius-cxcli grafana import` with explicit config, target, folder UID, data-source
mapping and overwrite, then validates JSON and bindings. Retain independent
rendered-dashboard inspection. Remove obsolete dashboard flags and the per-lab
catalog attachment/render/apply sequence; do not add a compatibility path.

Every executable recipe declares Systems applicability, a concrete report inspection target or an explicit exception, and whether GPU telemetry is relevant. Keep systems_command as the single capture command. GPU correctness and optional CUDA exercises use the existing worker launcher with opt-in NVTX ranges; vendor captures wrap the actual worker-side executable and preserve clean vendor output. Dynamo goodput uses server capture and suppresses publishable results during diagnosis. CPU-only dashboards omit GPU panels. Extend standalone validation and focused launch fixtures to enforce the metadata, guide, dashboard and actual-process boundary.

#### Selected Option

Reuse NVIDIA Nsight, existing Soperator collectors and local VictoriaMetrics, private Grafana and a finite Pushgateway comparison cache. Keep immutable result files authoritative.

#### Alternatives Considered

Reject a custom dashboard application, duplicate monitoring stack, public Grafana exposure, infrastructure reconciliation for dashboard updates and unbounded per-run metric labels.

#### Implementation Boundaries

Courses own teaching, lab code, result artifacts, recipes, dashboards, connection discovery and validators. cxcli owns application admission, datasource rendering and private Grafana access. Installation/live checks require the exact designated target; no target is inferred.

#### Test-First Success Criteria

- TDD-001: Missing dashboards, unsupported recipes, mismatched comparisons, stale publication and unsafe ordinary-app dispatch fail clearly.
- TDD-002: Repeated imports preserve stable identity; source checks never imply live qualification.

#### Validation Plan

Validate exact CLI commands, semantic measurement mappings, dataset provenance, private networking and generated artifact parity.

#### Test Plan

Run focused Python, shell, dashboard and course tests; check current deployment authority, ownership rejection and port-forward cleanup with isolated fixtures.

#### Evaluation Plan

Verify the complete Lab 00 and Lab 01 flow, then representative distributed, training, serving and CUDA paths on the designated non-production two-H100 target when its identity and access are provided.

#### Rollout And Rollback

Implement one complete course slice before extending all families. Roll back only task-owned source changes or explicitly owned optional application resources; preserve results and cluster infrastructure.

#### Done Definition

All requested source paths and documentation are wired and checked, with live and browser coverage reported separately and no fabricated results.

#### Implementation Evidence

The shared profiler explicitly requests demangled, unrenamed names and is synchronized into all six standalone courses. The affected inference/training source, observability metadata, both recipe representations and learner guides use the same operation ranges. Training Lab 06 baseline follows the guide default group size of eight, with its group-size-four comparison retained. Inference Lab 16 requires online Hub metadata access; a lab-scoped offline override preserves the prepared offline default for other labs.

The Lab 14 conditional comparison recipe uses the publisher's existing varies_with contract for mode-dependent dtype and logical bytes. Lab 15's measured loop uses annotated_operation, whose clean path preserves callable identity; its guide, metadata and run-labs recipe pass the same range and kernel filters. The guide now states that default twenty-sample nearest-rank p99 equals the maximum.

The Lab 07 metadata, guide and course README select projection for Compute and explain the warmup boundary. The workload source and correctness remain unchanged.

The all-lab audit covers 110 executable labs in six practical courses, with 99 applicable Systems capture recipes, 11 explicit exceptions and 116 dashboards including six setup dashboards. Every guide declares the actual capture process, report view and assigned dashboard. Added 15 missing captures, opt-in annotations, worker-side vendor output separation and owned-server goodput capture; corrected moved NCCL and distributed-scaling wiring. Six CPU/model/protocol dashboards omit unrelated GPU telemetry. The text-only Soperator course has no labs. Standalone validation enforces applicability, exact guide commands, dashboard identity, query metrics and units. See [all-lab profiling audit](lab-profiling-audit.md) for the full assignment table.

The current CLI repair updates all 110 lab guides and six setup guides to
immediate dashboard import and binding validation. The setup helper exports the
prepared data-source UID, and each course explicitly selects or creates its
Grafana folder before recording its observed UID. Initial monitoring render and
apply remain setup steps. Shared validators and their six standalone copies
reject obsolete commands; generated dashboards and HTML reflect the current
small/large workload terminology.

#### Verification Evidence

The template-GEMM regression fails twice before the fix and passes for both direct and companion captures afterward; the focused profiling/controller/observability suites pass 334 tests. Source-operation equivalence checks cover all ten new annotation sites. Live Optimization Lab 19 completed both profiles and Lab 20 completed small with independently checked originals, native reports, actual reviewed PNGs and verified exports. Lab 20 large completed the workload but its old Compute capture failed: Systems correlated CUTLASS Kernel2 with an sgemm template argument, proving that short-name filtering excluded the operation. The failed unit remains preserved; a fresh affected-profile run and all future inference/training qualification remain pending.

Six focused regressions reproduced missing Lab 19/20 workload selectors before repair. All 137 controller, observability and optimization comparison tests pass after repair, including both profile expansions and the actual capture entrypoint using a fixture process. Metadata, guides and both run-labs recipe representations share the same selectors. Fresh Lab 19/20 native verification and publication remain pending.

The original Lab 14 small-profile compute pair reproduced the dtype-invariant rejection while the other three case pairs passed. Offline replay of those immutable originals against repaired metadata admits all four pairs; this is publisher contract verification, not completed live publication. Five regression failures exposed the metadata and missing selector before repair. All 226 focused publisher, capture, controller, evidence-runner, profiling and course checks pass, including both workload-profile recipe expansions and the real capture entrypoint with a fixture process. Fresh H200 execution now completes Labs 14, 15 and 16 in both profiles with original correctness checks, independent native report verification, actual Grafana/Nsight image review and hash-checked sanitized exports. Lab 14 retains every mode pair, Lab 15 selects the measured uniform-tail probe, and Lab 16 retains three independent repetitions. All observed Lab 15 task GEMMs serialize despite multiple streams; the evidence does not establish concurrent execution. Lab 16 retains separate activation kernels for both expressions; scalar reference-error checks do not retain full output arrays. This is bounded live evidence, not full-catalog or H100 qualification.

Lab 07 source repair: three focused regressions reproduced incompatible modes or a missing projection selector before repair. All 134 controller, evidence-runner and observability tests pass, including both profile expansions and the real capture entrypoint with a fixture process. All seven course validators, generated HTML freshness, shared-tool parity, scoped Markdown and whitespace checks pass. Source and project-installed skill payloads match. Changed-scope code/security review found no blocking issue; the preexisting Ruff E731 in an unchanged test remains. The previous Optimization campaign was closed after five labs completed both profiles, retaining all completed jobs and exports. Fresh Lab 07 execution now passes both profiles on a prepared H200 target: eight completed jobs, four original PyTorch traces with 20 shape-checked and correlated iterations each, four independently qualified native reports, and six visually reviewed Grafana/Nsight images. Native Systems reports prove five warmup and twenty measured steps; Compute selects the first warmup projection and agrees with the internal traces on kernel and launch geometry. Each profile export has two sanitized original results, three actual images, a zero-metric summary and a manifest; hashes, privacy checks and ZIP bytes pass. Final finite-loss booleans do not establish reference-output equality, and profiler timings remain diagnostic. This is bounded Lab 07 live proof; full-catalog qualification remains incomplete.

All seven repository validators pass, including deterministic helpers, dashboards, rendered HTML and embedded kit bytes. The final CPU suite passed 1,151 tests plus two separately run loopback fixtures; tests cover wrong identity/units/commands, worker/rank argument and exit propagation, private vendor output, CPU capture rejection and profiled-goodput publication exclusion. All six renamed standalone packages validate. Browser coverage includes all 18 course/viewport combinations with current hashes, exact dashboard bytes, real downloads and enlarged-text reflow; earlier hidden-link and intermittent heading-position failures remain recorded, and 12 repeated checks passed after harness layout synchronization. Markdown, shell, Python formatting and whitespace checks pass; three preexisting Ruff findings remain in unchanged lines. Changed-scope code/security review found no remaining blocking source issue. That earlier source audit performed no live cluster operation. Actual H100 captures, native profiler/runtime qualification, counter permissions, Grafana imports/rendering and ingestion remain pending; delivery is implemented, not live verified, and REQ-017 remains active.

The current CLI repair passed 118 focused tests with two Torch-dependent tests
skipped on the laptop. Seven failures reproduced the obsolete setup flow and
missing UID export before repair. All seven course validators, helper/dashboard
parity, HTML build/source parity, scoped Ruff, repository-configured Markdown
lint and whitespace checks pass. Exact-host tests admit the official Grafana
reference and reject a foreign suffix. Source review retains explicit target,
folder and data-source selection and quoted shell values. These checks do not
qualify the initial live monitoring installation or folder-creation workflow.

The three changed Advanced Communication dashboard descriptions were imported
through the current CLI and passed its binding validation. Independent Grafana
API readback matched every source field except documented server metadata,
including descriptions, and preserved each dashboard UID and folder. This is
live dashboard-update evidence; per-lab rendering and workload acceptance remain
separate from this source repair.

<!-- /FEATURE: FEAT-027 -->

<!-- FEATURE: FEAT-028 reqs=REQ-018 status=ready delivery=implemented priority=P1 version=2 -->
### FEAT-028: Simple setup and separate GPU-fabric experiments

#### Requirements Covered

- REQ-018: Simple setup and a separate GPU-fabric learning route.

#### Context Evidence

The five standalone packages contain 18 distributed labs and one multi-node
serving launcher. Existing two-rank mechanics are distinct from fabric-scale
experiments. cxcli now owns private monitoring installation;
Slurm opens stdout/stderr before the batch script can create their directory.

#### Design Details

Retain the five conceptual courses; FEAT-030 transfers the advanced practical activities to their own seventh course with unique identities. Add explicit advanced
lesson membership to canonical metadata; render the base route first and the
advanced lesson/lab group separately using original identities. Explicit ordered
membership places Optimizations lessons 14 and 15 before its 11, 12 and 16. GPU Optimizations
owns fabric readiness, NVLink/NVSwitch, RDMA, collective layout and distributed
profiler investigations. Training owns distributed training-state tuning and
Inference owns sharded generation and serving placement. Preserve two-rank
mechanics on the qualified advanced cluster; add validated one-node/eight-rank
and two-node/sixteen-rank launch paths rather than blindly expanding hard-coded
algorithms. Retain CPU capacity models in the base route.

A shared submission helper validates metadata and paths, creates private per-lab
logs before sbatch, passes absolute chdir/output/error arguments, and preserves
unprofiled paths. Shared hardware guards fail clearly on one-GPU workers.
Setup defines CLUSTER_CONFIG, CLUSTER_TARGET and COURSE_TOOLS explicitly.
Course setup discovers the cxcli-installed services and writes separate private
laptop and worker connection environments. It never changes catalogs or wraps
cxcli commands. Receipts are isolated by deployment/target; explicit kubeconfig
and context bind observations to the selected cluster. Removed installer flags
and legacy receipts have no compatibility path.

New labs use the shared artifact/provenance, publisher, NVTX and per-lab dashboard
contract. GPU peer bandwidth is intra-node; inter-node GPU communication uses
NCCL/InfiniBand and qualified GPUDirect RDMA. PyTorch traces and Systems reports
have rank-specific identities, one shared run ID and bounded captures. The
publisher detects one declared parameter change from artifacts; no extra
comparison-control flag is introduced. Compare profiled diagnostics
separately from unprofiled acceptance. Vendor tools are installed/prepared once
in setup with version/capability evidence, without changing cluster drivers.

#### Selected Option

One advanced route within existing standalone courses, with shared setup and
small common launch/readiness helpers; preserve existing runtimes and tools.

#### Alternatives Considered

The earlier in-course route minimized separate runtime packaging. FEAT-030 now deliberately selects an independent advanced laboratory course while retaining useful conceptual teaching in its original subject. Changing
rank counts globally would invalidate two-rank routing examples. Plain default
Grafana without the reviewed catalog would lose privacy and result ingestion.

#### Implementation Boundaries

Course sources, tooling, tests, supporting documentation and generated assets.
Use existing cxcli public interfaces; no new cloud lifecycle or catalog protocol.
No live mutation, dependency installation or public publication is implied.

#### Test-First Success Criteria

- TDD-001: Undefined/legacy variables, catalog shadowing, home-relative scheduler
  logs and advanced launch on the base environment fail scoped contracts.
- TDD-002: Invalid topology/placement, failed tool validation and mismatched
  workloads never produce an accepted optimization comparison.

#### Validation Plan

Review each preserved/added lab's actual code and tool flags; verify all advanced
associations, source preservation, dashboard mappings and local output ownership.

#### Test Plan

Use fake sbatch and vendor-tool outputs, CPU numerical fixtures, parser/placement
failures, publisher contracts, standalone builders and isolated browser checks.

#### Evaluation Plan

Pending designated hardware: verify both full eight-H100 workers, NVLink/NVSwitch,
active InfiniBand, GPU-buffer registration, NCCL correctness, rank traces and
representative training/inference tuning without promising a speedup.

#### Rollout And Rollback

First simplify setup and submission; then separate existing distributed work;
then add fabric-scale experiments and rebuild all packages. Preserve original
artifacts and unrelated changes. No live resources are created by this rollout.

#### Done Definition

Every requested topic has owned teaching and executable practice, complete local
recipes and valid evidence mappings; source and target qualification are explicit.

#### Implementation Evidence

Implemented 101 executable labs and 106 dashboards across five courses. Seven
new advanced labs cover topology, directed peer copies, validated RDMA, collective
hierarchy, rank profiling, fixed-work DDP and partitioned generation. All 18
existing distributed labs retain IDs in the separate advanced route. Native
NCCL Tests supports eight/sixteen ranks; the actual vLLM server launcher supports
TP8/PP2, TP16 and compatible EP16. Ordinary private Grafana setup, persistent
variables, per-lab submission logs, user-space vendor tools, hardware guards,
syllabuses, source kits, guided hours and generated pages are aligned.

#### Verification Evidence

Source evidence: 965 tests passed in the full local run, plus the two loopback
fixtures with socket permission (967 total). The final setup follow-up passed
45 focused tests. Five standalone course validators and dashboard/helper parity
checks pass. Changed Python introduces no Ruff findings; formatting, ShellCheck
and configured Markdown pass. Owned headless Chrome checks cover five pages at
1440, 390 and 320 pixels, keyboard navigation, advanced ordering, fragment targets
and downloads; artifact hashes and final browser results are recorded in
[profiling validation](profiling-validation.md) and per-course publication reviews.
The existing eight installed-skill format findings and whole-page enlarged-text
limitations remain explicitly separate. No live sixteen-H100, vendor build,
profiler/runtime, GPU-buffer registration or deployed Grafana qualification was
performed. Delivery is implemented, not verified; REQ-018 remains active.

<!-- /FEATURE: FEAT-028 -->

<!-- FEATURE: FEAT-029 reqs=REQ-019 status=ready delivery=verified priority=P1 version=4 -->
### FEAT-029: Text-only Slurm and Soperator introduction

#### Requirements Covered

- REQ-019: Concise text-only Soperator course.

#### Context Evidence

The shared renderer, GPU validators and runtime-helper distribution currently
serve five practical courses. Synchronization discovers courses by a labs
folder, which excludes a text-only package. Soperator 4.1.8 source distinguishes
SlurmCluster service roles from worker NodeSet resources.

#### Design Details

Add courses/soperator with canonical mission, syllabus, six complete lessons,
glossary, public references, next steps, metadata, publication review and HTML.
Teach Slurm resource allocation; Soperator architecture; job/step/task requests;
batch and interactive launches; distributed process counts; and job evidence.
Use worked synthetic examples and short reading checks with answers. No labs,
diagrams, setup, dependency files or runtime assets belong to this profile.

Keep the Course overview to three short paragraphs covering purpose and
capabilities, entry knowledge and reading route, then text-only practice and
authorized example use. Consolidate repeated mission/syllabus context without
changing the six lessons. Keep version context in lesson 2 and detailed
readiness checks in the existing README, syllabus and lesson practice.

Register six courses for catalog/navigation but retain five runtime-helper and
GPU-validator destinations. Select the text renderer by canonical identity and
validate an exact text-only metadata schema. Reuse Markdown, layout, embedded
CSS, licensing and atomic generation; preserve complete canonical prose and
use direct lesson h2 and six direct h3 containers: Objective, How it works,
Practice, Mental model, followed only by optional References. Add a text validator with
source/HTML parity and navigation checks. Synchronization discovers metadata
plus COURSE.md while retaining its existing path and symlink safeguards.

The architecture explanation uses versioned Soperator 4.1.8 API and chart sources
for service roles, NodeSets and optional components. Slurm and PyTorch command
semantics come from their public owning documentation. Re-author source topics;
private operational details never enter public course files or review reports.

#### Selected Option

Explicit text-only course profile inside the shared publication pipeline; no
weakened GPU contract and no separate manually maintained HTML document.

#### Alternatives Considered

An empty lab package would misrepresent the requested learning experience.
Duplicating the whole renderer would create drift. Inserting Slurm lessons
into all five GPU courses would repeat teaching unnecessarily.

#### Implementation Boundaries

Course package, catalog/build/validation/synchronization wiring, focused tests,
course docs and relevant root catalog wording. No cluster operations, package
installation, infrastructure changes, Confluence writes or publication.

#### Test-First Success Criteria

- TDD-001: The no-labs Soperator package appears first in the catalog and switchers and remains included in sync output.
- TDD-002: Malformed text metadata, incorrect lesson order, missing canonical
  prose and stale output fail without relaxing existing GPU validations.
- TDD-003: Keyboard navigation, local code/table scrolling and text reflow work
  at desktop, 390px and 320px widths without external resource loading.

#### Validation Plan

Review all teaching against public sources; inspect complete source parity,
lesson identities, glossary consistency, reference destinations and privacy.

#### Test Plan

Run new text-profile tests, catalog/link and sync fixtures, relevant GPU content
and standalone regressions, atomic build/check, syntax/lint and owned isolated
headless Chrome checks. Do not execute illustrative Slurm commands.

#### Evaluation Plan

Evaluate reading checks for resource arithmetic, task/launcher distinctions,
allocation versus execution, log paths, missing accounting and failure evidence.
Record browser/static verification separately from unexecuted cluster examples.

#### Rollout And Rollback

Publish specs before implementation; author canonical Markdown, add explicit
profile wiring, rebuild all navigation and validate. Preserve unrelated dirty
work and existing GPU content; no live rollout is part of this change.

#### Done Definition

The first catalog course is text-only, complete, accurately sourced, navigable, synchronized
and locally validated with scoped publication evidence and no added labs.

#### Implementation Evidence

Overview revision: consolidated the canonical preamble from 287 to 101 words
in three paragraphs and rebuilt Soperator HTML. Kept the six lesson bodies
unchanged; version context and detailed readiness checks remain in their
existing owning sections. README and publication review describe the scope.

Implemented the six-lesson text-only package, exact metadata and parser, shared
renderer dispatch, public references and reading checks. Catalog, switchers,
root wording and sync discovery include the sixth course. The five GPU courses
retain their runtime assets, validators, lessons and lab implementations.
Publication evidence is in [Soperator course validation](soperator-course-validation.md).

#### Verification Evidence

Overview revision: independent read-only editorial review found no lost
essential context. Text-only validation, selected-page generation/parity, 30
text-profile tests and scoped Markdown/whitespace checks pass. Task-start
comparison proves unchanged lesson bodies, shared catalog and lab guide. The
generic checker retains the same six profile/markup findings. Browser checks
were not rerun for this overview-only edit; earlier browser evidence remains
bound to its recorded artifact. No live examples or publication were executed.

Full shared source run: 983 passed plus two separately permitted local-loopback
fixtures. Final content/presentation follow-up: 120 passed; final text validation
follow-up: 24 passed. Five GPU validators and the text-only validator, generated
parity, helper parity, syntax and scoped lint pass; existing test style findings
are recorded separately. All 11 owned isolated headless Chrome 153.0.8010.48
checks pass, including new-page desktop/390/320 layouts, keyboard navigation,
local scrollers, enlarged text and all existing course switchers. Final HTML
hashes match the evidence. Semantic/read-only review completed; an imprecise
Kubernetes provisioning statement and cramped enlarged-text table columns were
corrected. Generic skill-checker differences for the explicit text profile and
shared catalog shell are documented without claiming that checker passed.
No live Slurm, infrastructure, package installation, publication or external
expert review occurred. The text-only reading contract is verified; existing
GPU live/runtime qualification remains unchanged.

<!-- /FEATURE: FEAT-029 -->

<!-- FEATURE: FEAT-030 reqs=REQ-020 status=ready delivery=implemented priority=P1 version=5 -->
### FEAT-030: Standalone advanced GPU communication labs

#### Requirements Covered

- REQ-020: Advanced GPU communication laboratory course.

#### Context Evidence

Five GPU packages currently contain 25 advanced activities with shared evidence
helpers and mixed two-rank versus sixteen-rank launchers. Two training/inference
stems collide. The sixth course is text-only; current GPU renderers require
numbered conceptual lessons and cannot truthfully represent an all-labs course.

#### Design Details

Create advanced-gpu-communication with an explicit labs-only metadata profile,
setup Lab 00 and 34 uniquely numbered executable lab identities: 25 moved
activities and nine new communication/runtime investigations. Order topology,
local links, network transfers and collectives before training and serving.
Preserve complete implementations, intentional two-rank mathematical references,
per-lab defaults, helpers and source-owned tests; update all practical links to
the single new owner. Existing conceptual lessons retain their explanatory value.
The package has its own common/runtime helpers and no sibling imports.

Reuse the deterministic builder, light layout, source allowlist, source kits,
seven-section guides, existing evidence/publication pipeline and dashboard
renderer. Add narrow labs-only rendering/validation with empty lesson metadata,
lab-owned diagrams, complete source/prose parity and the existing safety guards.
Keep conceptual course validation strict and text-only validation independent.

Setup selects two eight-H100 workers, verifies actual GPU/fabric access and
prepares isolated vendor environments in user-owned storage. Vendor revisions
are source-qualified candidates until built and tested. Slurm owns allocations;
Dynamo control-plane and serving processes stay inside a bounded private job.
No Kubernetes service installation is needed for those experiments.

The serving launcher adds the selected Dynamo interpreter's containing directory
to its child processes' `PATH`, ahead of the preserved inherited tool paths.
Keep the interpreter's venv directory when Python itself is a symlink: runtime
JIT compilation must find that environment's Ninja without activating the
submission shell or changing its environment.
Worker readiness and frontend model discovery are separate transitions. The
launcher polls an empty frontend model list within one 600-second deadline,
requires exactly `course-model` before any request, and rejects unexpected or
malformed identities. Preserve owned-process liveness checks during the wait
and record the successful discovery and number of empty polls in the job log.

The Dynamo native installer accepts a separate `DYNAMO_UCX_PREFIX`, pointing to
CUDA-aware UCX headers and libraries prepared for the same runtime and RDMA
provider stack. The vendor installer calls one course-owned
`tools/install_dynamo_native.py` helper after creating the isolated Dynamo venv.
That helper retains Dynamo 1.4.2 and its pinned NIXL 1.3.2 source revision, builds
NIXL and NIXL-EP in one wheel with `build_nixl_ep=true`, and installs that wheel
with dependency resolution disabled so the qualified Torch/vLLM stack stays
fixed. Build tools and source live in a new private build prefix; existing
prefixes fail rather than being overwritten. The wheel must contain both
Python extensions and the UCX plugin and must not vendor a second UCX bundle.
A build or metadata check alone does not establish GPU-runtime qualification.

Keep NIXLBench's separate NIXL/UCX versions and process-scoped library paths.
Do not repair Dynamo through import-order tricks, LD_PRELOAD, binary patches,
or by changing serving workload sizes. The whole native package boundary is
the repair owner. Both import orders and a real NCCL-plus-NIXL construction
check precede the unchanged two-worker serving replay. Source installation,
live package loading and original workload evidence remain separate lanes.

Bridge training disables dataset attention-mask construction when its batch
adapter omits that mask; the attention backend retains causal masking. This
avoids allocating unused quadratic boolean masks in DataLoader shared memory,
without changing sequence lengths, worker counts or the training reference.

Dynamo workers explicitly enable vLLM batch invariance and the RMSNorm custom
operation, select FlashAttention 2, and disable the nondeterministic fused all-reduce/RMSNorm compilation pass for
every serving layout. The pinned Hopper FlashAttention 3 path can violate batch
invariance even without the fused reduction. Explicit RMSNorm selection routes
normalization through its batch-invariant CUDA implementation; the compiler
default otherwise selects the native implementation. Compilation and CUDA graphs
remain enabled. These settings stay fixed across both comparison slots and can
affect performance. Labs 32 and 33 retain their strict output-equivalence gates;
Lab 34 uses the fixed-work contract below. Backend settings alone are not proof
of identical generated outputs, and this redesign does not claim a repair of
inference determinism.

Lab 34 retains the pinned two-worker aggregated deployment, round-robin routing,
128 seeded sequential requests, zero AIPerf warm-up and the existing latency
objectives. Input-generation targets remain 256/2048 tokens and output lengths
32/128 tokens for smoke/h100. Set min_tokens=max_completion_tokens to the output
length, ignore_eos=true and n=1, with streamed usage and no explicit stop lists.
Keep model thinking and all shared worker settings fixed. AIPerf uses server
counts and raw export; no extra learner flag, replay service or corpus tool is
introduced.

The Lab 34 summarizer joins raw payloads, metrics and outputs by request ID,
requires one response in each of 128 distinct single-turn logical sessions,
and validates the actual transmitted controls. Hash canonical payloads in
logical session order, excluding transport metadata rather than request fields.
Require positive integer server prompt counts and exact completion counts from
final streamed usage. Completion counts already include reasoning tokens; do
not add them twice or substitute visible-text tokenization. Reconcile sequence
lengths and summary aggregates. Chat formatting may change server prompt counts
relative to the synthetic input target; require ordered equality across runs.
Missing usage, malformed evidence, cancellations and unsuccessful requests fail.

The Lab 34 recipe requires measurement_contract=fixed-token-goodput-v1,
workload_sha256, requested_output_tokens, completed requests and verified actual
length arrays/means. Keep output_signature only as a diagnostic. Existing
publisher controls still freeze runtime, source, SLOs and every parameter except
concurrency. Required new fields reject old artifacts without a legacy path;
shared publisher and Labs 32/33 behavior stay unchanged. Raw exports remain
private. Learner Practice uses one 8/16 pair, with repetitions and concurrency
32 as follow-ups. Zero goodput and either performance direction are valid.

Dynamo server captures start CUDA collection through both workers' profiler
endpoints after model readiness, then stop collection before engine shutdown.
Both controls must acknowledge success, with a 90-second start timeout and a
600-second stop timeout to allow collection-end report finalization. Large
captures can take more than three minutes to serialize; a stop timeout still
fails the capture and does not trigger a retry. The lab then waits up to
180 seconds for both reports while the engines remain alive, then interrupts its owned Slurm steps
with signal forwarding and a bounded finalization wait for each primary server.
Capture includes GPU worker descendants. Missing or empty reports fail the job;
inspect actual request-phase GPU activity and profiler warnings in both reports.
File creation alone does not establish trace completeness.
Nsight disables the extra per-process `cudaProfilerStop` flush to reduce
multi-GPU context synchronization; collection-end flushing remains enabled.

Distributed report review checks exported statistics for every rank and opens
representative reports from each worker in manageable groups. Grafana selectors
use worker hostname and local GPU index; physical UUID verification is separate.
Short workloads can fall between telemetry samples, so low sampled utilization
does not establish absent GPU work.

Dynamo client latency uses a monotonic clock. Separate per-request epoch bounds
and a measured-phase window identify the workload interval for profiler
correlation; cross-node interpretation additionally requires measured clock
error bounds. Warm-up and server initialization remain outside that interval.
The goodput lab passes the canonical tokenizer repository and pinned revision
to AIPerf's offline cache resolver, with a child-scoped cache root. Setup retains
the complete immutable snapshot, including repository metadata, so offline
validation does not depend on a local directory being accepted as a Hub ID.

New investigations target NCCL small-message latency/algorithm selection,
InfiniBand latency and bounded NIC selection, NIXL device-buffer movement,
Megatron gradient-reduction overlap and hierarchical context exchange, Dynamo
aggregated versus prefill/decode pools and cache-aware routing, and goodput under
fixed latency objectives. Use official released/source interfaces, explicit
runtime checks and fail-fast unsupported configurations. Keep raw traces private;
publish only mapped numeric summaries after timing. No arbitrary environment
or fabricated vendor results enter dashboards.

#### Selected Option

One independent labs-only package with shared publication tooling and complete
course-local runtimes, rather than another conceptual textbook or duplicate labs.

#### Alternatives Considered

Keeping advanced experiments scattered conflicts with the requested ownership.
Converting existing lessons into empty lab wrappers would hide missing teaching.
Assuming every existing exercise scales to sixteen ranks breaks several exact
mathematical references, so allocation size and exercise rank count stay distinct.

#### Implementation Boundaries

Course content, executable sources, launchers, vendor adapters, tests, shared
builders/validators, navigation and relevant repository catalog documentation.
No dependency installation, live infrastructure changes, public serving or
external publication. No unsupported automatic tuning of driver/fabric policy.

#### Test-First Success Criteria

- TDD-001: All 25 moved activities have exactly one executable owner with distinct IDs, complete guides, assets and preserved capabilities.
- TDD-002: Labs-only metadata renders zero conceptual lessons, full setup/practical prose, valid diagrams, escaped source listings and seven-way navigation; missing assets and malformed metadata fail.
- TDD-003: New vendor adapters reject invalid inputs, malformed results, unsupported hardware/version assumptions and mismatched comparisons in CPU fixtures before any target execution.
- TDD-004: Private output, rank counts, owned-process cleanup, bounded capture and latency/throughput units are checked without implying live fabric qualification.
- TDD-005: The native installer fails before runtime replacement for absent
  prerequisites, an existing build prefix, wrong source revision, or a wheel
  missing EP or containing bundled UCX. The actual shell workflow builds both
  components and preserves the pinned runtime dependencies; original serving
  and collective probes separately prove native behavior on allocated GPUs.

#### Validation Plan

Review official vendor sources and all guide-to-command mappings. Preserve
historical evidence as historical and record new scope/qualification separately.

#### Test Plan

Run focused migration, metadata, vendor adapter and launcher tests; all-course
build/validator/dashboard/helper checks; scoped lint and broad CPU regression
after path migration. Run isolated desktop, 390px and 320px browser checks.

#### Evaluation Plan

Compare equal payloads, token work and allocations where required. Explain
confounders and both throughput and latency objectives, validate correctness,
and include guided comparisons followed by independent transfer investigations.

#### Rollout And Rollback

Publish this specification pair before implementation. Move complete packages
under an explicit mapping, rebuild atomically, and audit source preservation.
Version control retains the original content; do not reset unrelated changes.

#### Done Definition

The seventh course owns the advanced lab set, adds executable NVIDIA-focused
investigations and passes available source/browser gates with installed/runtime
and live sixteen-H100 evidence honestly marked pending where unavailable.

#### Implementation Evidence

The Lab 34 fixed-work revision is implemented in the runner, its comparison
recipe, regression tests, guide, README, course introduction, generated
HTML/download kit and dashboard description. The focused CPU suite passes
160 tests with one optional torch-dependent skip; all seven course validators
and HTML freshness checks pass. Lab 34 dashboard freshness passes separately
from an unrelated pre-existing stale Lab 30 dashboard.

Six fresh unprofiled runs on a two-worker, sixteen-H200 test cluster completed:
four smoke runs in concurrency order 8/16/16/8 and one larger-profile 8/16 pair.
Every run contains 128 successful requests, with exactly 32 or 128 server-reported
completion tokens for its profile and matching transmitted workload hashes.
Independent raw-evidence checks reconstruct request joins, latency statistics,
SLO counts and throughput. The first smoke pair differs in 23 response texts:
the former equality gate rejects it and the fixed-work contract accepts it.
The larger pair has 128 and 126 SLO-passing requests respectively; successful
completion does not imply that every request meets the teaching objectives.

Playwright verifies both installed Grafana comparisons against their result
JSON, including values, generation, job identities and sampled telemetry for
all sixteen GPUs. The stale dashboard description was reconciled with the
generated source without changing its queries. Both separate Systems captures
complete and native report-content checks find kernels and NCCL on all sixteen
GPUs within controlled collection windows encompassing the request exercise.
The reports warn of incomplete CUDA/NVTX events, missing NCCL API correlations
and timestamp-range resets. They establish bounded execution evidence, not a
lossless trace or cross-host per-request timing attribution.

An isolated Compute diagnostic uses pinned first-layer model weights and
synthetic BF16 activations. Its Triton RMSNorm symbol and launch geometry occur
in the fresh Systems capture, and its output matches the numerical reference.
Native report parsing and Playwright inspection verify duration, occupancy
and throughput counters. Runtime arguments come from the model rather than
being recovered from the serving trace; this is not serving-batch replay or
an end-to-end timing result. Existing vendor GUI frequency-unit defects are
excluded from the verification. The guide explains workload sizes, profiler
warning limits, clock boundaries and opening large reports one at a time.
This H200 evidence does not claim live H100 qualification or all-course
completion. Existing course evidence below predates the fixed-work revision.

The advanced-gpu-communication package owns 34 executable labs and setup-only
Lab 00. All 25 prior activities have a single source/guide/dashboard owner;
nine new vendor/network investigations implement the designed comparisons.
The earlier conceptual courses use external_labs metadata for moved practice.
All seven HTML pages and the catalog build from canonical sources. Downloaded
kits preserve executable permissions; setup dashboards are directly available.
Runtime support is course-local, vendor environments are isolated candidates,
and monitored GPU counts follow the two-versus-sixteen course contract.

New comparison guards validate process completion, exact AIPerf request joins,
output/corpus identity, finite vendor measurements and final-weight reference
identity. Profiles remain separate from unprofiled acceptance timings. The
NIXL benchmark disables automatic sample-count reduction. Private Slurm logs,
owned server processes and candidate installation instructions remain bounded
within the authored workflow. Root catalog docs and navigation are aligned.

#### Verification Evidence

Official NVIDIA interfaces and immutable source candidates were reviewed.
The full CPU run passed 1017 tests; two isolated loopback API fixtures passed
separately. Later focused passes covered migration/vendor/readiness, downloads,
and final presentation changes. All seven course validators, source/helper/
dashboard parity, scoped production/new-test lint and shell/Markdown checks pass.
Nested read-only code/security review found no remaining actionable findings.

All 12 final owned headless Chrome 153.0.8010.48 checks passed: complete advanced
page and catalog at 1440/390/320 pixels, keyboard navigation, local scrollers,
200% text reflow, diagrams, actual downloads and all prior-course switchers.
Artifact identities and review details are in docs/advanced-course-validation.md.
The generic skill checker retains eight documented profile/shell differences;
older test code also has out-of-scope lint findings, so neither a generic checker
pass nor a clean whole-repository lint run is claimed.

The original course-authoring pass performed no native installation or live
cluster qualification. Subsequent live validation under FEAT-032 localized a
Dynamo package interaction: importing NIXL-EP brought the original bundled UCX
back into a process using a separately built NIXL binding. Paired fresh-process
controls with real NCCL reproduce the crash in that combination and pass with
a joint source build. The canonical installer has since built and installed
the joint package in an isolated runtime. Fresh checks pass both import orders
and real NCCL-plus-NIXL construction, and unchanged aggregated and disaggregated
serving runs complete on both workers. Independent checks reconstruct nine
numeric fields from each run's 64 requests and verify request epoch bounds and
all sixteen tensor-parallel worker startup records.

Four configuration regressions and the pinned dataset helper verify that the
unused Bridge mask is absent while loss masks and positions are unchanged.
The original long-sequence overlap workload then completes all 25 steps, with
independent checks of final reference weights and twenty retained measured
step durations. The offline tokenizer repair passes four regressions and a
counterfactual comparing exact token IDs against the same local snapshot.
Full serving variants, report-content reviews and dashboard comparisons remain
in progress under FEAT-032; these scoped results are not all-course completion.

<!-- /FEATURE: FEAT-030 -->

<!-- FEATURE: FEAT-031 reqs=REQ-008,REQ-017,REQ-020 status=ready delivery=implemented priority=P1 version=2 -->
### FEAT-031: Gradient acceptance and isolated Compute diagnostics

#### Requirements Covered

- REQ-008: Separate evidence lanes and reproducible environments.
- REQ-017: GPU profiling and per-lab Grafana evidence.
- REQ-020: Advanced GPU communication laboratory course.

#### Context Evidence

Lab 19's fixed absolute tolerance accepts zero gradients at the actual BF16
bucket scale. Both capstone aggregators omit instrumented-result admission.
The advanced NCU launcher selects an absent NVTX range and has no supported
recipe. These three review findings motivate the approved repairs.

#### Design Details

Share course-local BF16 bucket/loss functions between Lab 19 and an unnumbered
local Compute companion. Compare FP32 gradients with relative tolerance 0.01
and absolute tolerance 0.01 times the reference maximum magnitude; require
exact zeros for a zero reference. Invalid shape, dtype, count, empty or
nonfinite gradients produce a local failure before rank-MIN consensus, then
all ranks exit before warmup or timing. Record per-bucket errors and preserve
existing correctness fields. Pairwise agreement does not prove communication.

Both capstone loaders require an object experiment with instrumented exactly
false and reject explicit acceptance_timing=false before output. Missing
provenance requires a clean rerun; schema identifiers do not change.

The single-visible-H100 companion preserves BF16 casts in an independent
local derivative, validates outside capture and uses no collectives or hooks.
Warmup and forward construction precede a gradient_backward NVTX push/pop
range around one backward call. The fixed launcher uses the shared profiler:
process-scoped NVTX, kernel replay, kill=no, one selected kernel, existing
basic sections and private report/receipt paths. It emits no acceptance result.
Metadata binds compute_companion.path and compute_companion.nvtx_range;
only an exact Python/script prefix may enter this distributed-recipe exception.
Reject wrappers, interpreter modes, server capture, range mismatches and
multi-rank environments before report-directory creation or process launch.

#### Selected Option

Keep 34 numbered advanced labs and add a diagnostic companion to Lab 19 using
the existing deterministic Python/PyTorch, Slurm and Nsight stack.

#### Alternatives Considered

The existing tolerance and missing provenance admit invalid acceptance.
Removing the launcher omits the requested Compute workflow; adding a numbered
lab needlessly changes the course identities. Do not profile live collectives
through this isolated-kernel recipe or introduce compatibility aliases.

#### Implementation Boundaries

Course-local workload helpers, two standalone aggregators, the canonical
profiler/validator and their six copies, Lab 19 metadata, launcher and guides.
Preserve prior capture fixes and unrelated edits. No dependency installation,
cluster submission, infrastructure change, publication or Git action.

#### Test-First Success Criteria

- TDD-001: BF16 positive/negative controls detect scale corruption and remote rank failure stops timing and result output.
- TDD-002: All three capstone inputs require clean provenance; valid decisions remain unchanged.
- TDD-003: Only the exact local companion is admitted; generated kits include it without changing lab counts.

#### Validation Plan

Run focused CPU and shell regressions, canonical helper/dashboard parity,
all seven validators, changed-scope lint and the complete offline suite when
the declared validation dependencies are available.

#### Test Plan

Exercise real BF16 CPU autograd and independent references, malformed gradient
buckets, per-rank MIN consensus, malformed/profiler-contaminated final capstone
inputs, launcher dispatch and profiler command/environment rejection before
side effects. Verify source, metadata, guide and embedded-kit parity.

#### Evaluation Plan

Qualify CUDA BF16 agreement and an actual matching nonempty NCU kernel report
on an explicitly authorized H100 target, separately from offline source proof.
Use Systems traces for distributed overlap interpretation.

#### Rollout And Rollback

Deliver independent acceptance repairs and a bounded Compute companion.
Retain failed native evidence and leave native qualification pending until it
passes. Revert only this task's exact source changes if needed.

#### Done Definition

The three reviewed source defects are repaired, offline checks pass, and
native/runtime qualification limits are reported explicitly.

#### Implementation Evidence

Implemented both strict capstone loaders, shared BF16 workload/reference and
validation helpers, Lab 19 rank consensus, the isolated Compute companion,
fixed launcher, exact-command/range/process admission and missing/empty-report
failure. Canonical profiler/validator copies, metadata, READMEs, guides and six
changed HTML/embedded-kit artifacts are synchronized. The 34 advanced lab and
110 catalog lab identities and all dashboard mappings are preserved.

#### Verification Evidence

The final available offline suite passed 1,252 tests. Nine publication test
functions were explicitly excluded because the existing environment lacks the
declared prometheus-client dependency; no installation was performed.
Focused evidence includes real CPU BF16 autograd at the actual 8 MiB bucket
size, corrupted gradients, both simulated ranks exiting on one rank's failure,
capstone rejection before output, real Bash launcher dispatch with fixture
Slurm/NCU commands, malformed command/environment rejection, failed/empty
capture receipts, and isolated downloaded-companion imports. All seven course
validators, canonical-helper/dashboard parity, source/HTML/kit parity,
changed Python lint/format, configured Markdown, Bash syntax, ShellCheck and
whitespace checks pass. Independent read-only code/security review found no
blocking source issue and verified helper/archive parity.

Native H100 BF16 agreement, NCCL execution and an actual matching NCU kernel
report remain unqualified. Nonempty-report admission is not matching-kernel
proof. No cluster, infrastructure, dependency installation, publication or Git
action occurred. Earlier browser evidence does not cover regenerated pages;
requirements remain active and delivery is implemented, not live verified.

<!-- /FEATURE: FEAT-031 -->

<!-- FEATURE: FEAT-032 reqs=REQ-016,REQ-017,REQ-018 status=ready delivery=implemented priority=P1 version=2 -->
### FEAT-032: Installed-tool and shared-cluster lab qualification

#### Requirements Covered

- REQ-016: Reuse a qualified cluster for concise lab setup.
- REQ-017: Prove applicable workload, profiler and dashboard behavior live.
- REQ-018: Separate one-GPU allocations from full fabric capacity.

#### Context Evidence

Live discovery found two eight-H200 workers, although the requested qualification and existing course guards require H100. The user approved H200 functional validation for all single-GPU and multi-GPU labs; H100 performance qualification remains separate. Browser access to installed Systems and Compute GUIs works. The readiness script rejects the installed Systems version before executing a canary. Monitoring currently derives an exact two-GPU expectation from the base course slug instead of the selected deployment topology. Existing recipe metadata enumerates 110 executable labs, 99 Systems captures, 55 Compute captures and 116 dashboards; these counts are inventory, not qualification.

#### Design Details

The pinned fabric-tool installer checks the PCI development header and link library before creating a build prefix or cloning source. Honor the same owner-supplied compiler and link flags used by the build. Lab 00 names the required development packages and the pinned nvbandwidth CMake dependency; it does not require an obsolete Boost dependency. This prerequisite check compiles and links a small probe without executing PCI access.

Retain the existing Slurm, profiler and Grafana architecture. Use one shared admission path for full H100 and H200 devices. Validate the observed family, SM90 architecture, full-device visibility and existing fabric constraints; reject unsupported devices and mixed-family fabric allocations. Do not introduce a hardware-selection flag or change learner-facing H100 teaching text. Record measured hardware identity throughout Python, CUDA and vendor-result paths. Keep H100 teaching titles and workload-size profiles, and label H200 evidence as functional validation rather than H100 performance proof. Use one GPU per base workload and the full declared allocation for fabric experiments. Make monitoring compare fresh distinct GPU telemetry with the selected target's GPU inventory, while separately enforcing course minimum topology. Do not hide additional GPUs through filtering. Readiness records actual CLI versions and qualifies their required features by executing and independently inspecting CUDA canaries and reports. Candidate installer pins remain explicit inputs for a new manual installation; existing tools are not reinstalled solely to satisfy a string comparison. Viewer compatibility remains a separate browser check.

Run each baseline and documented variant with a fresh result identity. Keep diagnostic captures separate from acceptance timings, require the intended CUDA/NVTX/kernel evidence, and reconcile selected JSON measurements, units and generation with the rendered dashboard. Explicit CPU/protocol/RDMA applicability exceptions remain. Source synchronization and runtime preparation are harness setup; environment recovery cannot prove a course workflow transition.

#### Selected Option

Reuse the installed tools and selected cluster through measured capability checks, exact inventory and existing course workflows.

#### Alternatives Considered

Forcing a separate two-GPU cluster adds unnecessary infrastructure. Suppressing extra telemetry weakens readiness. Blindly upgrading profilers may break viewer compatibility and does not prove report correctness. Preserve explicit installer pins and require actual execution instead.

#### Implementation Boundaries

Shared course helpers, standalone copies, recipes, dashboard generator and affected learner prose. No secret values, private endpoints or environment-specific identifiers enter source. Preserve unrelated dirty work. Keep fixes at the proven owner.

#### Test-First Success Criteria

- TDD-001: Both supported cluster capacities admit base one-GPU work without suppressing expected telemetry; missing/stale GPUs fail.
- TDD-002: Actual profiler versions are recorded, malformed version output fails, and canary/report failures cannot be reported as readiness.
- TDD-003: Every lab retains explicit live/not-run/failed/inapplicable evidence per workload, Systems, Compute and dashboard lane.

#### Validation Plan

Focused helper and topology regression checks, standalone parity, generated dashboards and HTML, catalog validators, then clean live replay on both workers.

#### Test Plan

Exercise profiler identity parsing and canary failures; accepted versus observed target inventory; numerical and publication oracles for each proven lab defect. Do not replace runtime tests with source assertions.

#### Evaluation Plan

Use Playwright to inspect actual reports and dashboard panels. Correlate GPU telemetry only within bounded recorded run windows and avoid asserting exclusive attribution without allocation evidence.

#### Rollout And Rollback

Preserve the source baseline and use private run directories. Re-sync changed helpers before a fresh trial. Revert only task-owned changes if the regression oracle fails; retain previous trial outcomes.

#### Done Definition

All requested labs and applicable variants have completed independent runtime, report-content and dashboard checks, or precise remaining prerequisites are recorded without an all-course success claim.

#### Implementation Evidence

Added the PCI compile/link preflight, actionable setup guidance, and a regression that rejects the missing dependency before network/build effects. A real compiler test covers private include/library paths containing spaces. The setup guide, README and generated HTML are aligned.

Readiness comparison recipes for Advanced Labs 03 and 04 use the actual world-size and distinct-host-count measurements as invariants. Both original two-host runs must pass their NCCL correctness gates, and missing or changed placement fields are rejected. Two regression cases reproduce the former missing-backend-field failure and pass after the recipe repair; thirty adjacent publication tests pass.

Implemented shared full-Hopper admission, observed device identity, exact telemetry inventory and installed-profiler capability qualification. Readiness compares raw CUDA symbols across reports to avoid differing display-name simplification. Functional campaigns and per-lab GUI/publication qualification remain in progress.

#### Verification Evidence

The original missing-header build failed. After supplying SHA-verified public PCI development/runtime packages in isolated fixture storage, a fresh build completed the pinned nvbandwidth, perftest and MPI NCCL Tests candidates. Independent checks matched installer/binary hashes and resolved build-time libraries. Two focused tests and thirty-nine adjacent tests pass in their required environments; generated HTML, the advanced course validator, Ruff and whitespace checks pass. This proves the build prerequisite repair only; both-worker binary execution, remaining labs and all-course qualification remain in progress.

The original version and hardware admission failures were reproduced and repaired. Executed canaries validate matching kernel symbols, NVTX and counters. The required CUDA build passes fifteen included smoke checks; a fresh sixteen-rank topology run passes after adding the missing NumPy dependency. Seventy-one course reports pass current content checks, with thirty-two exact cross-tool kernel matches; GUI qualification remains incomplete, including an unresolved Compute unit-display discrepancy. All 116 dashboard pages loaded without query errors, and thirty-three selected comparisons additionally match their published numeric values in Playwright. Full workload variants, vendor environments and remaining report/dashboard lanes are still in progress. These observations do not establish all-course completion or H100 performance.

<!-- /FEATURE: FEAT-032 -->

<!-- FEATURE: FEAT-033 reqs=REQ-017 status=ready delivery=implemented priority=P1 version=3 -->
### FEAT-033: Discover cxcli-owned monitoring for course results

#### Requirements Covered

- REQ-017: Preserve existing private monitoring while adding course result evidence.

#### Context Evidence

cxcli now installs Grafana and a separate Pushgateway App, renders native VMAgent scraping and resolves datasource UIDs. The old course helper duplicates installation through Grafana extraObjects. The course verifier still expects its former VMServiceScrape. Neither product has compatibility consumers, as confirmed by the user.

#### Design Details

Keep course_setup.py as a discovery-only consumer of the accepted generated manifest and target Flux HelmReleases. Require explicit config, target, kubeconfig and context; compare source-config digest, accepted Kubernetes identity, generated/live ownership, readiness and private Services. Resolve the metrics-local datasource UID/URL, Pushgateway Service including overrides, native VMAgent inline scrape and local VMSingle. Emit only private v2 monitoring receipts and quoted workstation/worker environments. Remove source-catalog/image flags, catalog writing, object generation and CLI wrapping. Verify one fresh healthy cxcli-pushgateway scrape using service DNS and ready endpoints, label preservation and exact backend identity. Unsupported shapes, stale receipts and missing or ambiguous owners fail without repair. Keep publisher measurement selection and readback unchanged.

#### Selected Option

Reuse cxcli installation and retain a small course discovery/verification consumer. Course artifacts own connection observations and result semantics, never platform lifecycle.

#### Alternatives Considered

Retaining the course installer duplicates ownership. Manual endpoint copying is error-prone and was rejected in favor of discovery. Do not parse CLI display output, import private cxcli modules, or retain a legacy path.

#### Implementation Boundaries

Course discovery, verification, tests, standalone copies, README and generated guide/catalog. Document the generated artifact consumer boundary in cxcli observability guidance. No live deployment, resource deletion, credential or IAM changes.

#### Test-First Success Criteria

- TDD-001: Missing, foreign, stale, ambiguous or unready monitoring fails before connection outputs.
- TDD-002: Default and overridden service identities and rendered datasource UIDs round-trip without infrastructure mutation.
- TDD-003: Native DNS scraping, label preservation, duplicate routes, backend identity and publication readback remain verified.

#### Validation Plan

Run focused setup, verifier, publisher and guide tests, helper parity, all generated-page checks and standalone validators. Review code/security and perform scoped lint. Source checks do not establish live installation.

#### Test Plan

Use current cxcli-shaped generated/live fixtures, captured read-only kubectl commands, private output/quoting tests and negative receipt/schema cases. Test inline image embedding and unsafe image rejection.

#### Evaluation Plan

Check the rendered guide and unchanged original PNG at desktop/mobile widths. A later independently authorized target trial must prove installation, discovery, scrape, publication and dashboard readback separately.

#### Rollout And Rollback

Ship one canonical source path and synchronized standalone copies. Remove old flags and reject old receipts. Revert focused source changes and rebuild for rollback; never delete platform resources.

#### Done Definition

Discovery-only setup, native monitoring verification, general branding and illustrated instructions are aligned and locally validated. Live verification remains explicitly separate.

#### Implementation Evidence

Implemented discovery-only setup against the cxcli v2 manifest and accepted generated artifacts, private Helm-owned Services, native scrape configuration and the existing local metrics datasource. Removed course resource/catalog generation, old installer flags and CLI wrapping. Explicit kubeconfig/context and private v2 receipts bind subsequent verification; all six standalone helper copies are synchronized. Flux API-default normalization preserves healthy live resources while rejecting real drift, suspension, termination and remote kubeconfig binding. The verifier rechecks native remote-write routing as well as scrape and backend identities.

Historical evidence for the previous catalog-extension design follows; it does not verify this replacement.

Implemented existing-owner discovery, frozen-source binding, private service equivalence, namespace-aware bridge generation and preservation of existing Grafana settings. Shared helpers, standalone copies and setup instructions use the same path.

#### Verification Evidence

Local replacement verification: 337 focused tests pass and five PyTorch-dependent tests are skipped in the isolated validation environment. Discovery/ownership, default and overridden Services, stale artifacts, private outputs, removed flags/receipts, native DNS scraping, duplicate routes, backend drift and the reproduced Flux-default regression are covered. All seven course validators, helper and generated-page parity pass. Code and security review closes the Flux-default finding with no remaining blocking issue. Scoped source lint, configured Markdown checks and shell checks pass; ten unchanged Ruff findings in existing guide/sync test code were compared against the pre-change snapshot. Six pre-existing fabric-test import failures reproduce from the saved pre-change test and pass when the canonical tools directory is on the test import path. These are local source checks; no live deployment, scrape, publication or GPU workload was executed.

Historical evidence for the previous design follows.

Thirty-six focused setup and monitoring tests pass. Actual cxcli rendering preserved existing release settings and unrelated resources; normal apply completed, and independent checks found one healthy results scrape and sixteen fresh GPU series. Thirty-three selected comparisons pass Playwright checks against every expected numeric value and publication generation. This verifies the existing-Grafana extension; complete all-lab qualification remains separate under FEAT-032.

<!-- /FEATURE: FEAT-033 -->

<!-- FEATURE: FEAT-034 reqs=REQ-021 status=ready delivery=implemented priority=P1 version=38 -->
### FEAT-034: Course-owned run-labs automation

#### Requirements Covered

- REQ-021: Repeatable campaigns and student evidence.

#### Context Evidence

Six practical course catalogs contain 110 executable labs. Before this feature, shared submit, profile, verify and publish helpers existed without a full campaign controller. Observability baseline commands do not encode all guide variants or reference dependencies. Installed cxcli profiling uses separate HTTP and TURN services and shared read-only reports. Existing results archives are not complete portable student bundles.

#### Design Details

The resources exporter remains the sole archive producer shared with the HTML builder. Course pages link one combined resources archive and the shared lab setup page. Remove kit-only UI/build/validation dependencies while preserving all original sources, source sync permissions, evidence bytes and exporter locking/journaling. No live GPU rerun is required for this presentation and packaging change.

Inference Lab 30 retains two independent jobs for each prepared protocol in each profile: OpenAI and its repeat, then Triton and its repeat. Each pair uses identical launcher arguments and is published separately through the existing comparison validator. Keep all four originals and distinct dispatch identities; do not compare protocols or reuse one original in both slots. Each launcher owns its loopback server, readiness, one bounded generation request and cleanup. Both profile labels retain the same effective qualification workload. Two probes do not establish a latency distribution, semantic quality or an engine speedup. The recipe requires a fresh campaign after installation; active campaigns retain their frozen recipe inputs.

Prepared container admission is checked against each synchronized workspace before job submission and before prepared-asset proofs are frozen. Where the task-owned runner restricts working directories, setup registers only the exact owned campaign root and records the configuration identity before and after binding. Image and executable checks alone do not prove workspace admission. Preserve unrelated settings and the runner's guards; never admit an entire home directory. This setup is harness evidence, separate from successful lab execution.

An image's declared Python interpreter search path remains part of its prepared runtime contract. Generic runner defaults must not hide a packaged virtual environment. Qualify required imports through the effective runner environment; installed package metadata is insufficient. Bound any prerequisite diagnostic separately from the lab campaign, retain negative evidence, and preserve unrelated images and settings when repairing an image-specific override.

Prepared containers keep mutable module, lock and compiler caches in writable job-isolated directories, separate from immutable model mounts. In a Transformers-backed runtime, qualify the effective `HF_MODULES_CACHE` with the backend operation that acquires its configuration lock; an existing directory alone is not sufficient. Preserve model assets, offline settings and unrelated runner configuration. Package imports may initialize CUDA; record observed effects instead of assuming a prerequisite import probe is CPU-only. Cache recovery remains harness setup, and a fresh lab trial is required before claiming server qualification.

Inference Lab 32 and Training Lab 31 each run their three-child capstone launcher twice independently per profile. Keep repetitions at one and result_count at three for each launcher job; retain all six clean originals and both independently validated aggregates. Pair corresponding child indices with matching seeds and variant orders across the two groups for publication. Keep recipe comparisons empty because those stages have multiple originals; index-aware evidence adapters own the three declared child pairs. Each native diagnostic still runs one separate process per profiler, not another acceptance group.

Training Lab 32 retains two clean CUDA jobs and two clean CPU jobs per profile, with a separate equivalent pair for each device. Only its CUDA configuration has a Systems capture; CPU mode produces no GPU evidence and there is no Compute stage. Both profiles retain the same deterministic scalar-learning exercise without a throughput claim.

Inference Lab 36 declares five one-control policy comparisons across its six original records: no-tier to storage, then storage to TTL, bandwidth, restart and revision variants. Preserve the common fixed request trace and both profile labels. Computed foreground and write-service costs remain CPU model outputs, not measured I/O or serving latency; no native profiler stage is required.

Inference Lab 35 retains two independent unprofiled jobs for each CUDA, CPU and one-token CPU configuration. Publish the three equivalent pairs separately through the unchanged comparison validator, preserving all six originals. Its sole Systems diagnostic executes the CUDA configuration; CPU runs do not produce GPU evidence. Both profile labels keep the same fixed bigram exercise. Regression coverage binds distinct dispatch identities, equivalent pair arguments, profile propagation and the guide's CUDA-only capture scope.

Inference Lab 10 diagnostic recipes retain --in-process while clean timing uses the default child process. Its Compute command selects the first matrix kernel matching .*(gemm|gemv|nvjet).* inside measured vllm_generate, excluding staged request-buffer writes. Verify the actual launch and its role against Systems; one matrix capture does not cover all model layers or generation phases. Keep the learner command, observability metadata and both reviewed recipe representations identical. Freeze a stage-local environment mapping that currently accepts only SBATCH_MEM_PER_NODE as a positive decimal MiB string. Recipe stage values override prepared defaults only for that stage; Compute requests 262144 MiB without changing the GPU workload. Reject unknown keys and malformed values before effects. Keep source recipe and guide parity, input immutability and both-profile isolation covered by focused tests; verify actual allocation separately during live execution.

Compute selectors match full demangled kernel names with simplification disabled. Generic function names can carry the GEMM identity only in template arguments; keep the existing NVTX and launch-count gates. Inference Labs 09, 17 and 23 and Training Labs 02, 06, 07, 14, 21, 22 and 30 add capture-only operation ranges while preserving clean callable behavior and algorithm order. Training Lab 06 selects its first objective reduction, without a matrix filter. The other affected selectors use matrix names, including GEMV; Training Lab 07 diagnoses generation and Lab 22 diagnoses BF16, not the complete update or FP8 path.

Optimization Labs 19 and 20 select their pipeline GEMM for Compute: consume_batch for H2D, and produce_output with the kernel expression .*(gemm|nvjet).* for D2H. The latter excludes input fill within its range; both exclude weight initialization. With default arguments the first selected GEMM is a warmup call. Treat its counters as diagnostic; use the full Systems reports for copy, compute and CPU-consumer ordering, and separate unprofiled trials for whole-loop timing. Keep the template and executable profiling-stage argv identical. Shell examples quote the complete export argument containing the regular expression; argv recipes pass it literally.

Keep Optimization Lab 15's canonical Compute selector aligned with its measured uniform-grid probe: tail_measure plus uniform_tail_probe, one matching launch. Do not substitute initialization or the one-block compilation probe for the studied grid; retain Systems coverage of the other grids and concurrent task sets. Frozen recipe or course metadata repair starts a new campaign after exact owned jobs are quiescent and old claims are released.

Optimization Labs 09, 16 and 19 run three independent jobs per configured variant and profile, reversing variant order for the middle repetition. Retain every original and compare each declared pair at every repetition. Lab 09's three in-process rounds are additional observations, not fresh processes; native captures remain separate diagnostics. Its result environment owns GPU model identity instead of hard-coded hardware claims.

Optimization Lab 07 recipes retain two internal-profiler repetitions with trace export and separate external-only Systems and Compute stages. Native stages must not request PyTorch trace export. The Compute template and executable profiling stage both select the existing projection range. Recipe changes require normal claim release and a fresh campaign; completed units and their evidence remain preserved.

The campaign agent owns repeated necessary recovery of Grafana, Nsight Systems/Compute native tools and streamer/viewer services, nsys/ncu profiling runtimes, browser sessions and task-owned forwards. No fixed recovery count or repeated approval applies within existing target authority. Restore exact-target loopback connections for all three services, including installed Nsight HTTP/TURN mappings and prior-session forwards, using persistent local processes and readiness observations after launch returns. Reconnect browser sessions and inspect visible responses to navigation, then exact dashboard/report content. Local transport recovery reuses access authority independently of remote service restart classification. When connection recovery is insufficient, restart only the affected authorized deployment/service through its installed owner, with explicit config/context/namespace and unchanged configuration, persistent data, credentials and private exposure. Retain durable originals and failed evidence; protect unrelated sessions and respect actual tool/access denials.

Treat nsys/ncu as installed profiling runtimes and scoped sessions/processes, not generic daemons. Inspect the actual executable, runtime, job and original diagnostic failure. Restore the existing prepared environment; gracefully stop only exact owned stuck sessions/jobs after reconciling uncertain intent and preserving outputs. Recheck readiness and actual capture/report content; a version response alone is insufficient. Do not weaken counter permissions, change resource limits, reset GPUs, disable unrelated monitoring or reinstall infrastructure under this recovery authority.

The controller makes terminal profile-stage failure terminal for that unit and binds dispatch identity to the campaign and stage. It cannot retry that stage in place. Preserve this deterministic path: finish unaffected campaign work and normal finalization/claim release (or the existing exact-owned cancellation path when needed), prove prior writers quiescent, then create a fresh campaign for only the affected lab/profile after fixing a proven tool/environment failure. Run the complete unchanged recipe, including prerequisite and unprofiled trials, with new job lineage and independently verified evidence. Preserve old failed evidence and completed units; never clear state, forge receipts, blindly resubmit an uncertain job, or retry correctness failures into success. Viewer/dashboard recovery alone resumes evidence without GPU replay. Adding an in-place retry state machine is rejected because it would require new attempt identities and changes to claims, verification and cleanup; restoring tools without a supported failed-unit path would leave the requested recovery incomplete.

The fixed controller/agent split and installed technologies remain unchanged: deterministic helpers own job identity, state and publication; the agent diagnoses tools and inspects native views. Keep the repair local and reversible across the source and matching installed skill, references, recovery evals, shared README/guide and changelog. Validate definition structure, supported existing controller behavior, links, course freshness and source/installed parity; live recovery and fresh model-quality behavior require separate observation. This instruction revision is implemented and statically verified; live tool recovery and model-quality behavior remain unverified. Earlier delivered campaign features and evidence below remain valid for their stated scopes.

Fundamentals Lab 03 requires host-copy mechanism proof before generic kernel admission, including reports with incidental kernels. Bind exact Systems stage/job/producer, explicit effective buffer size, workload interval and one GPU/process/context/stream. Independently verify all four ordered groups of 25 correlated logical copies, exact bytes and synchronization, then the final payload readback. Count native segments separately from logical copies and derive memory kinds from report-owned labels. Require matching_host_copy visual review for both configurations while preserving original hashes, warnings, correctness, publication and export. The source repair, focused regression checks and both-profile browser/export verification are complete; full-catalog execution remains in progress. Frozen course work and recipes remain unchanged.

Retain CUDA Lab 12 memcheck as an existing dependency stage with the same profile/executable before the three-trial launcher. Independent verification covers its zero-error output, all three originals and aggregate math. The course aggregate records and requires one consistent observed GPU name across trials; it does not claim H100 performance for another supported GPU. Reuse existing child result indices for complete dashboard coverage, with no synthetic aggregate JSON or new stage kind.

Treat campaign preflight as runtime qualification of the prepared environment. Resolve existing connection provenance privately, independently bind the exact cluster and service/backend identities, and verify one healthy results scrape with preserved labels, complete fresh GPU inventory, intended local write/read routing and actual later publication readback. Preserve user-selected local-only routing. Use current course validators for the invariants they cover; retain explicit independent observations for other prepared topologies rather than claim an incompatible setup verifier passed. FEAT-033 owns new setup discovery and v2 connection files and remains unchanged. Do not rerun setup solely to resume, convert old receipts, add a legacy branch or weaken runtime acceptance. Any separately authorized infrastructure repair remains outside the lab evidence trial. Record skill revisions separately when frozen course inputs are unchanged.

Document project-scoped Codex installation from `courses/` using `npx --yes skills add ./skills/run-labs -a codex --yes`, without `-g` or `--global`. The skill applies to courses and its subdirectories. The first `--yes` suppresses the npm package-download confirmation; the final `--yes` skips the skills wizard and bypasses the optional find-skills installation.

Resolve the enclosing course checkout from its sync script and course catalogs for source and project-installed skill layouts; installations outside a checkout require an explicit `--courses-root`.

Use a host agent skill at courses/skills/run-labs with deterministic Python helpers and reviewed structured recipes. A campaign freezes selection, source/recipe digests and private environment receipt; execution, verification, collection, browser review and export have separate checkpoints. Initialization acquires all selected claims under the control lock; resumed effects first reconcile exact ownership. Slurm dispatch records intent before submission and persists a parsable receipt; uncertain dispatch reconciles owned jobs without blind resubmission. Existing campaign launchers remain authoritative. One lab allocation runs at a time. Agent browser actions use headless Playwright against the installed native viewers with independent CLI content checks. Both viewer ports use loopback forwarding. Grafana imports retain UIDs and folders and use --overwrite for changed dashboards within the authorized course scope. Verify the requested dashboard identity and destination before importing; provisioned or foreign-owner metadata alone is neither a blocker nor a separate approval requirement. Reuse task authorization while retaining the installed CLI editability, ownership and concurrency checks. Record that file provisioning can later replace an API update, verify the effective imported definition before capture, and change provisioning sources only when that work is within scope. Preserve unrelated dashboards and do not use --attach to take over another catalog. Stable course/lab/profile/workspace publication labels replace prior selections. Public projections omit private infrastructure fields and include their own hashes. Stage public and private replacements under locks and recover a journal after interruption; preserve prior complete output until replacement commits. Persist a publication checkpoint before remote/local staging cleanup and mark export complete only after cleanup, allowing interrupted cleanup to resume without republishing. Course HTML links one deterministic external dashboards-and-results ZIP, shared with the course builder archive owner under FEAT-022, named `<slug>-lab-results.zip`; original runtime files are delivered through sync-labs.sh without a lab-kit ZIP. Workload profiles become small and large throughout current code, docs, dashboards and tests.

Introduce an internal evidence runner that loads and verifies the existing frozen campaign and delegates controller transitions to stage.py. A private, identity-bound adapter manifest pins prepared family verifier and publication helper bytes, arguments and output receipts; it never substitutes generic correctness checks for family contracts. Record runner and adapter fingerprints separately from frozen course inputs. Reconcile an existing publication intent or confirmed receipt before further effects and stop at an explicit visual-review gate. Keep serial allocation and the current collection, checksum and replacement owners unchanged. The exporter delegates deterministic archive assembly to the shared authoring owner under FEAT-022.

Use one campaign-owned headless browser process with separate in-memory contexts for Grafana, Systems and Compute. Serve a bounded allowlisted request protocol through an owner-only Unix socket; permit only configured loopback origins and private output paths. Resolve existing credentials in process memory once per service context. Do not expose CDP, evaluate caller JavaScript, persist browser state or capture login forms. Native navigation batches use inspected coordinates and explicit rendering waits, bind the exact report identity and retain visual checkpoints; overlapping Events View entries may be nested, so ordinal keypress counts alone cannot establish operation identity.

The Grafana adapter verifies the effective dashboard, profile, absolute window, generation, correctness and job identity before returning evidence. Capture only real, fully visible formatted table rows, at native scale, with tight readable crops and a bounded pagination fallback. Omit redundant overview images when numeric captures suffice; retain one for a zero-metric qualification. Validate the required zero-metric overview crop before adapter effects against the prepared native viewport. After final scrolling, wait for the current generation and both correctness rows to render fully inside the crop; retain their visible geometry with the screenshot observation. Measure all data cells against their clipping ancestors, including layouts where the row uses display:contents and has no box of its own. Reject blank, stale, incorrect and clipped controls even when earlier off-screen checks passed. Bind every numeric check to the image containing that row, reject conflicting or missing cases, and retain the existing complete-union 0.5 percent check. The runner never manufactures visual approval.

Advanced Lab 29 follows its documented UCX GPU-transfer mechanism rather than a CUDA-kernel requirement. Native evidence binds the observed initiator and target roles independently of process-file rank. Require the initiator's 550 warmup-plus-measurement write ranges and UCX put submission/completion events with exact profile-sized byte totals. Require GPU buffer preparation, CUDA/OS runtime activity and registration on both processes, plus target notification polling followed by the profile-sized device-to-host validation copy. Keep native warnings, hash-bound query proof, representative browser inspection and separate unprofiled correctness-checked measurements. Initialization-only capture cannot establish transfer evidence.

Advanced Lab 33 may leave one KV candidate replica idle. Require complete router selections and worker completions proving all 69 service requests belong to the active peer, zero inference work on the idle producer, eight distinct TP worker profiler starts, recorded stop events and completed capture acknowledgements and all eight GPU identities. Bind both reports and their native proofs to the exact job and original hashes. The active peer must retain matching inference kernels/NVTX and its representative browser view; an idle-only view cannot satisfy configuration coverage. Preserve failed evaluation evidence and reconcile completed exports before re-evaluation. Course inputs, GPU workload, report cardinality, capture warnings and comparison invariants stay unchanged.

#### Selected Option

One reusable agent skill and deterministic filesystem/controller helpers, using existing cluster tools and per-lab recipes. Keep agent reasoning for native GUI inspection and prerequisite resolution; make identities, job dispatch, checksums and publication deterministic.

#### Alternatives Considered

A generic two-command runner under-runs capstones and server experiments. Executing prose introduces ambiguous shell and optional exercises. Timestamped student directories accumulate duplicates. Adding a service or model framework is unnecessary.

#### Implementation Boundaries

Courses source, shared helpers, workload interfaces, recipes, tests, skill and generated artifacts. No new infrastructure installation, model downloads without prepared prerequisites, source mutation during skill runs, or changes to correctness requirements. H200 functional validation does not establish H100 performance. Inference Labs 11, 15, 20, 33 and 34 currently retain fixed server workloads for both profile labels; distinct size presets require a separate launcher design change.

#### Test-First Success Criteria

- TDD-001: Every executable catalog lab has one recipe and all selectors deduplicate deterministically.
- TDD-002: Repeated successful replacement has stable paths and obsolete owned files disappear; failure and interruption preserve or recover the last complete set.
- TDD-003: Resume never blindly resubmits, source drift is rejected, and malformed or mismatched evidence cannot publish.
- TDD-004: Session reuse isolates service authentication, rejects unsafe origins and outputs, and retains explicit visual review. Compact captures reject clipped, missing and conflicting numeric cases. Changed adapters and uncertain publication cannot silently replay. Installed source parity and fresh applicable browser use are reported separately from static tests.

#### Validation Plan

Run scoped controller tests, workload CLI tests, standalone helper parity, dashboard and HTML regeneration checks, all course validators and skill portability validation.

#### Test Plan

Use temporary filesystem transactions and fake transport boundaries for deterministic failure tests, plus actual command help and a fresh representative prepared-cluster trial.

#### Evaluation Plan

Verify selected native reports and dashboard numeric values with headless Playwright; distinguish all-catalog recipe coverage from live execution coverage. Retain explicit applicability and observed limitations in manifests.

#### Rollout And Rollback

Preserve pre-edit working bytes and unrelated dirty work. Introduce no old profile aliases. Replace only owned evidence after complete staging; recover interrupted two-destination publication from its journal. Existing historical archives remain untouched.

#### Done Definition

The skill and helpers implement the documented interface; local checks pass and representative live evidence is reported honestly. Full-catalog completion requires every selected recipe and applicable evidence lane to pass.

#### Implementation Evidence

The single-results-download revision is implemented. All six practical pages use separate labels and links for combined results and shared setup, without the duplicate introductory setup/dashboard paragraphs. Kit assembly and the six generated kit ZIPs are removed; all original source members remain. The wrapper help/status and source sync tests cover the resulting HTML/ZIP workflow. Older implementation evidence below describes prior revisions.

The zero-metric capture repair has 12 passing browser tests and 29 evidence-runner tests, including delayed remount, blank/stale/incorrect/clipped controls and invalid crops before verification or publication. Actual image acceptance remains a separate live review gate.

Inference Lab 10 selector repair follows exact original/native report inspection: the prior first-launch capture performed staged buffer writes inside measured generation, while Systems showed subsequent matrix launches. The guide, observability command and both recipe paths now select a matrix kernel in that same measured range. Both-profile regression cases fail before repair and pass afterward; 206 focused controller, collection, evidence-runner, vLLM capture and observability tests pass. The rejected diagnostic evidence is preserved; fresh target capture and browser/export acceptance remain separate gates.

Inference Lab 10 recipe repair adds in-process Systems flags and Compute-only host-memory allocation through a validated frozen stage environment. Both profiles preserve default clean process mode. The 23 failing regression cases pass after repair; 178 scoped controller, evidence-runner, collection and vLLM capture tests and Ruff pass. This is source validation; installed parity and fresh target execution remain separate gates.

The shared profiler explicitly requests demangled, unrenamed names and is synchronized into all six standalone courses. The affected inference/training source, observability metadata, both recipe representations and learner guides use the same operation ranges. Training Lab 06 baseline follows the guide default group size of eight, with its group-size-four comparison retained. Inference Lab 16 requires online Hub metadata access; a lab-scoped offline override preserves the prepared offline default for other labs.

Lab 15's profiling_runs and profilers entries now carry the same explicit measured-range and kernel selectors. The execution guide and both-profile controller regressions bind those values to the installed workflow.

The reviewed recipes set repetitions to three for Labs 09, 16 and 19, using the existing deterministic controller without an alternate execution path. Regression tests cover both profiles, complete trial identities, order reversal and unchanged workload arguments. The course README and execution protocol carry the same rule; Lab 09 changes only descriptive hardware metadata.

The Lab 07 reviewed recipe removes the incompatible trace-export flag from both native stages and carries the projection range through both Compute command surfaces. Existing controller, capture, correctness, publication and export owners remain unchanged.

Full tool-recovery follow-up: added one owning reference for Grafana service and Nsight native-tool/streamer deployment restart, all three exact-target loopback connections, visible browser navigation checks, preserved configuration/storage and nsys/ncu session/runtime recovery. The execution reference documents the existing fresh affected-lab/profile campaign path for proven terminal diagnostic-tool failure, with prior writers quiescent, normal claim release, complete unchanged recipes and preserved failure lineage. Updated core/environment/browser/runner links, four quality cases, two triggers, shared README/guide and changelog; refreshed eight changed/new installed files. Controller scripts, recipes and invocation metadata are unchanged.

Local-forward follow-up: separated authorized local transport recovery from remote-service mutation approval, added an explicit procedure for Grafana and both Nsight connections including prior-session exits, and required persistent local process/readiness checks before resuming evidence. Added two quality cases for exited forwards and real tool/foreign-listener restrictions. Refreshed five changed project-installed files and the shared README/guide; no helper scripts or campaign state changed.

Removed the single-recovery cap from the source skill and reconciled browser, runner and environment references. Added repeated service recovery, shared-session authority and retained-memory maintenance quality cases plus matching trigger boundaries. Refreshed only the six changed files in the matching project-installed skill. Updated the shared README, generated guide and Unreleased changelog; helper scripts, recipes, metadata, claims and export behavior remain unchanged.

CUDA capstone recipes now retain the supplied memcheck prerequisite. The course aggregate rejects missing, duplicate, unsupported or inconsistent GPU names and records the observed device in its scoped decision. Execution guidance keeps sanitizer/profiler diagnostics separate from three unprofiled child records and requires independent aggregate and publication coverage.

Prepared-runtime guidance now keeps independent current monitoring qualification separate from new setup discovery. It requires effective local write/read routing, scrape label preservation and complete fresh telemetry without converting old receipts or changing controller schemas. The optional workflow README and skill evaluation cases match this boundary.

The reusable evidence runner, persistent browser worker and compact Grafana capture are implemented and project-installed. The runner pins prepared family adapters, records effect intent, reuses confirmed publication and stops for explicit hash-bound visual review. Controller transitions check the expected action while holding the state lock, so a competing export cannot submit the next GPU unit. Isolated in-memory service contexts share one browser process through private serialized IPC. Native navigation uses bounded action batches and visual inspection; compact Inspector captures bind only visible numeric cases and paginate when needed. GPU scheduling, collection, hashing, replacement and ZIP ownership remain unchanged. Full-catalog execution remains in progress.

Advanced Lab 06 follows its documented vendor-owned capture contract: NVTX may be absent. The CE report must independently show all 56 distinct directed peer-copy paths, with positive integer event counts and bytes matching the profile buffer size; host initialization copies cannot satisfy this check. The SM report must contain the pinned vendor copy kernel. Kernel/NVTX field types, exact producer and report identity, hash-bound native proof, diagnostic warnings and representative browser review remain required. Kernel and NVTX checks remain the default outside explicit vendor exceptions.

Qualification receipts with no expected quantitative values require an explicit empty numeric-check list. They retain original correctness, dashboard selection/generation, visual review and applicable native-report requirements. Missing or malformed check lists and unknown metric selections remain invalid; measured labs still require every expected value.

The maintained and project-installed skill reuse campaign authorization for identified course dashboard updates. Provisioning or owner metadata alone no longer introduces an approval gate. The browser-evidence rule retains exact UID and folder scope, actual CLI ownership/editability/concurrency checks, and protection of unrelated dashboards. It records file-provisioner replacement risk and requires effective-definition verification before browser evidence. Trigger and output-quality definitions cover the authorized update and unrelated-owner boundary.

The controller locates the enclosing checkout from source and project-installed skill paths and retains an explicit override for external installations. The project-installed controller matches its maintained source.

The courses README documents the exact non-interactive local Codex installation
command, the `courses/` working directory, and the instruction to omit global
flags. It explains both `--yes` flags and skipping the optional `find-skills`
installation. The repository changelog records the documentation update.

Implemented courses/skills/run-labs with 110 reviewed recipes, union/deduplicated selectors, both workload profiles by default, a durable stage controller and identity-bound Slurm reconciliation. The agent workflow reuses prepared infrastructure, verifies original results and native reports, captures headless Playwright evidence and exports sanitized student artifacts. Owned private/public replacements use locks and a recovery journal; deterministic per-course ZIPs and manifest-owned HTML links expose the current evidence. Active interfaces, lessons, dashboards and standalone helper copies now use small/large without legacy aliases. Existing historical archives remain unchanged. Alignment repairs bind Lab 09 captures to the selected message-size ceiling and label Lab 12 two-node captures as the repeat variant. Claim acquisition and export cleanup now resume safely after interruption.

The Lab 29 validator now accepts empty CUDA-kernel lists only with role-specific NIXL transfer proof. Every capture configuration requires both roles; the runner derives the required visual-review flag from the same native-content validator. Other lab gates and publication transactions remain unchanged.

#### Verification Evidence

Current download revision: 338 distinct focused cases passed, with all 122 affected cases rerun after the final introductory cleanup. All seven native validators, HTML/ZIP freshness, helper parity and scoped lint passed. Three real wrapper runs on the final inputs, including two from another directory, produced identical hashes for all 15 generated outputs; failure-boundary tests preserve exit codes. Independent preservation confirms all six resources ZIPs unchanged, all 127 embedded source listings retained, and course markup identical outside the agreed introduction/heading changes. Final browser checks cover 18 desktop/mobile cases, six actual ZIP checksums and seven saved pages with JavaScript disabled. Generic skill-checker findings are unchanged from the task baseline and are not reported as passing. Source/installed evidence-reference parity and changed-scope code/security review passed. See docs/course-architecture-validation.md and current publication reviews for artifact hashes, visual checks and estimated hosting size. No live lab rerun, merge or deployment was performed. Earlier evidence below is historical.

Current campaign evidence, 2026-09-25: all 110 practical labs completed both profiles on the prepared H200 cluster, yielding 220 verified student exports with no missing profiles. The final audit independently joined completed controller stages, frozen source identities, original verification results, actual headless screenshot review receipts, public artifact hashes and exact ZIP bytes. Applicable native report contents were qualified independently before visual review and export. The last Inference Lab 34 profile retained all twelve native producer reports and three Grafana captures; native qualification included twelve positive checks and 108 corruption controls. Collection warnings remain visible and no complete-event-collection claim is made. CPU models remain modeled evidence, and unfavorable optimization results remain valid observations. These runs establish prepared-target experiment completion, not H100, clean-install or every optional-extension qualification. See profiling-validation.md for the course counts and evidence limits.

The earlier verification paragraphs below describe incremental source and partial-campaign checkpoints. Their full-catalog-pending statements are historical and do not override the current 220-export inventory.

The template-GEMM regression fails twice before the fix and passes for both direct and companion captures afterward; the focused profiling/controller/observability suites pass 334 tests. Source-operation equivalence checks cover all ten new annotation sites. Live Optimization Lab 19 completed both profiles and Lab 20 completed small with independently checked originals, native reports, actual reviewed PNGs and verified exports. Lab 20 large completed the workload but its old Compute capture failed: Systems correlated CUTLASS Kernel2 with an sgemm template argument, proving that short-name filtering excluded the operation. The failed unit remains preserved; a fresh affected-profile run and all future inference/training qualification remain pending.

Subsequent prepared H200 execution completed Optimization Labs 09, 10 and 12 in both profiles: thirty exact jobs, ten independently checked native reports and fifty actual visually reviewed Grafana/Nsight images. Public projection hashes, review joins, privacy checks and ZIP bytes pass. Lab 09 retains all independent runs and mixed or slower outcomes; Lab 10 preserves its differing shapes/precision and split-K native detail; Lab 12 proves allocation lifecycle state without an application speedup claim. The next Lab 14 small unit completed seventeen jobs but stopped at the incorrect publisher dtype invariant before acceptance or publication. Its originals remain preserved in the cancelled campaign; unaffected completed exports remain current. Focused source checks pass after the Lab 14 metadata and Lab 15 selector repair, with new live replay pending. Full-catalog completion remains unclaimed.

Independent-run alignment: six focused profile/recipe regressions failed before repair and pass afterward. All 140 controller, evidence-runner and observability tests pass. Scoped Python lint/format, Markdown checks, shared-tool parity, generated HTML freshness and all seven course validators pass. Reviewed changes preserve job ownership, one active allocation, result retention and publication safeguards. The completed Lab 07 campaign was cancelled only after all eight owned jobs were terminal and quiescent, preserving both exports and releasing only pending claims. New repeated acceptance jobs remain pending; source/static checks are not live proof.

Lab 07 source repair: three focused regressions reproduced incompatible modes or a missing projection selector before repair. All 134 controller, evidence-runner and observability tests pass, including both profile expansions and the real capture entrypoint with a fixture process. All seven course validators, generated HTML freshness, shared-tool parity, scoped Markdown and whitespace checks pass. Source and project-installed skill payloads match. Changed-scope code/security review found no blocking issue; the preexisting Ruff E731 in an unchanged test remains. The previous Optimization campaign was closed after five labs completed both profiles, retaining all completed jobs and exports. Fresh Lab 07 execution now passes both profiles on a prepared H200 target: eight completed jobs, four original PyTorch traces with 20 shape-checked and correlated iterations each, four independently qualified native reports, and six visually reviewed Grafana/Nsight images. Native Systems reports prove five warmup and twenty measured steps; Compute selects the first warmup projection and agrees with the internal traces on kernel and launch geometry. Each profile export has two sanitized original results, three actual images, a zero-metric summary and a manifest; hashes, privacy checks and ZIP bytes pass. Final finite-loss booleans do not establish reference-output equality, and profiler timings remain diagnostic. This is bounded Lab 07 live proof; full-catalog qualification remains incomplete.

Full tool-recovery verification: all 109 existing controller/evidence-runner tests and seven Node browser tests pass. Portable/Codex/Claude structure checks, local Markdown links, eval definitions, configured Markdown lint, generated HTML freshness, all seven course validators, shared-tool parity and paired-spec validation pass. The 27 source and installed payload files match; captured-byte comparison preserves scripts, recipes, metadata, prior 16 quality cases and unrelated spec records. Definitions now cover 16 triggers (10 positive, 6 negative) and 20 quality cases; this is STATIC_PASS, with no fresh native trigger/model-quality run. Scoped read-only code/security review found and resolved one stale recovery link and no remaining actionable issue. Current official Grafana restart/persistence and NVIDIA CLI/session/serialization docs informed the instructions. No live services, forwards or profiler jobs were operated for this source repair; actual recovery remains unverified.

Local-forward clarification: source and installed payloads match; comparison with captured working bytes preserves scripts, recipes, metadata, trigger CSV and the prior 14 quality cases. The two added cases bring quality definitions to 16; static validation, portable/Codex/Claude structure/frontmatter, relative links, configured Markdown lint, seven Node browser tests, generated-page checks, all seven course validators and shared-tool parity pass. Scoped code/security review confirms unchanged private target/access boundaries, fixed TURN mapping and foreign-process protection. Official kubectl documentation confirms forwarding ends with the selected pod and must be rerun. The other session refusal was not reproduced, no live forwards were launched, and fresh model-quality runs remain unverified. Prior Python regressions were not rerun because this follow-up changes only instructions and eval definitions.

Recovery-policy verification: portable, Codex and Claude static structure/frontmatter checks pass; the existing nested project-contract docs warning is retained. Canonical eval definitions cover 14 triggers (8 positive, 6 negative) and 14 quality cases, including three new recovery cases; this is STATIC_PASS only. All 109 existing controller/evidence-runner regressions and seven Node browser tests pass. Markdown lint, relative links, shared-guide freshness, all seven course validators and shared-tool parity pass. Working-byte comparison confirms the count rule changed while scripts, recipes and host metadata remain unchanged, and source/project-installed payloads match. Disposable skills CLI 1.5.26 discovery, Codex/Claude copies, repeat installation and isolation pass. Independent read-only code/security review found no blocking issue. NVIDIA Streamer restart and Kubernetes workload-restart/forward documentation were checked. Fresh trigger and comparative model-quality runs are UNAVAILABLE without isolated CLI authentication; no live service restart or lab execution was performed for this policy repair.

The capstone repair reproduced eight focused failures before implementation; 210 selected course/controller/evidence checks then passed, with one unrelated missing-PyTorch case excluded. Syntax, ShellCheck, Ruff, Markdown, generated HTML, course validation and skill structure pass. Read-only code/security review found no serious issue, and 26 source/installed files match after a successful project-scoped installation. This is source verification; fresh capstone target execution is separate.

The prepared-runtime clarification passes 64 focused controller and evidence-runner checks, canonical evaluation parsing and skill structural validation. Read-only source review found no serious guidance issue, and the project-scoped skill installation succeeded. This evidence covers the clarified source boundary, not new deployment discovery or full-catalog live acceptance.

The Lab 33 idle-replica boundary passes 82 focused controller/runner tests, including active-peer browser coverage, and eight counterexamples against retained originals. A completed two-node H200 LARGE run independently verifies all four jobs and native reports: three active reports retain eight-worker batched inference and CUDA joins; one KV replica has zero assigned/completed requests, eight profiler starts, seven recorded stop events and completed capture acknowledgements. The final verifier rehashed 44 original/native files and three installed annotation/availability sources and confirmed owned jobs were quiescent. Maintained and installed skill files match; focused Ruff, Markdown, generated HTML and course validators pass. This verifies the repaired evaluator on immutable completed originals; the unit's browser review/export and all-course completion remain separate campaign gates.

Four synthetic role/profile cases reproduced the old CUDA-kernel rejection. The focused controller and evidence-runner suites pass 58 checks, including altered transfer counts and bytes, missing target validation, wrong roles/producers, unchanged generic kernel checks and required NIXL visual review. The related controller, runner, vendor-evidence and profiling suites pass 297 checks with one skip. The 26 maintained and project-installed skill files match. A fresh H200 small-profile unit passed the installed verification, collection, browser review and export workflow: four native reports cover both roles in each configuration, all 550 payload-matched UCX puts and the targets' final validation copies. Three actual Grafana views match the unprofiled originals; two actual Systems views match the first measured UCX processing intervals and NIXL write annotations, selected by observed initiator role. Nine exported student files pass checksum, ZIP-content, privacy and generated-HTML checks. Native event-loss and scheduling warnings remain disclosed. This verifies the Lab 29 evidence boundary at the exercised profile; it does not establish complete all-course validation.

The runner update passes 67 Python checks across controller, runner and observability integration, plus seven Node browser checks. Regressions cover concurrent export transitions, canonical case labels, optional/asymmetric metrics, context isolation, bounded navigation, compact pagination, units and optional empty panels. Scoped Ruff, syntax, skill Markdown, observability generation and paired specification checks pass. README and changelog changes add no line-length findings; existing findings remain. Portable, Codex and Claude skill structure, actual disposable Skills CLI discovery, copied installations, repeat installation and isolation pass. Maintained and project-installed payloads match; installed dry-run and actual evidence assembly/export were exercised. Fresh native trigger behavior was not rerun because invocation metadata is unchanged; isolated model-quality evaluation remains unavailable.

A live installed Grafana worker reused this campaign's retained, hash-verified originals and captured two readable 960-by-360 Inspector images covering all four selected values, exact jobs and current generation. Both images passed explicit visual review; no workload, publication or export was repeated for this qualification. The same worker then opened and inspected the matching Systems report, selected the intended measured kernel and matched its duration, stream, launch geometry and projected NVTX ranges. Its 22-key selection request completed in about 1.4 seconds, including explicit render waits, instead of imposing 900 milliseconds after every key. The initial grouped open did not select a report and was rejected by visual review; inspected dialog navigation completed it. These observations establish the exercised Systems/Grafana paths, not overall campaign acceleration or Compute qualification. A live Compute check remains pending an applicable fresh campaign report; full-catalog completion is not claimed.

Four synthetic CE/SM receipt cases reproduced the unconditional NVTX rejection before repair. All 36 controller tests pass, covering both profile buffer sizes, missing or duplicate pairs, invalid counters, initialization-only SM captures, required GUI mechanism checks and the unchanged NVTX gate for other labs. A fresh H200 small-profile unit passed all eight original-result checks, both native reports, three repeated Grafana comparisons with 354 distinct numeric checks, 26 inspected screenshots and atomic export of 34 student files. The CE GUI shows a profile-sized peer copy and the SM GUI shows the vendor copy kernel; both displayed durations match the native trace. This verifies the repaired vendor evidence boundary; the large profile and full catalog remain in progress.

A synthetic zero-metric qualification receipt reproduced the prior unconditional numeric-check rejection before repair. Both metric-bearing and zero-metric evidence now pass roundtrip/export checks; missing, malformed and unknown checks remain rejected. All 30 controller tests, focused Ruff and Markdown checks, maintained/installed parity and unchanged campaign source/recipe fingerprints pass. Read-only risk review found no serious issue. A fresh two-node H200 qualification unit with no quantitative metrics passed independent original-result and native-report checks, actual headless Grafana and Systems review, and atomic PNG/CSV/JSON export. This verifies the zero-metric boundary; the full catalog campaign remains in progress.

The dashboard-authorization repair passes portable, Codex and Claude structure/frontmatter checks, the canonical nine-case trigger catalog, focused Markdown lint and paired spec validation. The four output-quality definitions include two new ownership boundary cases. Maintained and installed payloads match; actual disposable Skills CLI discovery, both copied installations, repeat installation and isolation pass. Read-only instruction and security review found no serious issue. Fresh native trigger behavior was not rerun because invocation metadata is unchanged; baseline model-quality evaluation is unavailable without isolated API credentials. These scoped checks do not establish full-catalog live lab completion.

Thirty controller tests pass, including source, Codex and Claude directory layouts, explicit override and missing-checkout failure. The previously failing installed all-course dry-run now selects all 110 labs and 220 profile units. This verifies checkout discovery, not live lab completion.

For the installation documentation update, the local skill path exists and
official Skills CLI source and npm documentation confirm the two `--yes`
flags and the skipped optional installation. Codex documentation confirms
project scope and ancestor-directory discovery. Scoped Markdown lint and
canonical spec validation pass. That earlier README-only checkpoint did not rebuild HTML. The current builder consumes the root courses README for the shared lab guide, so current README edits require regeneration and a freshness check. This documentation check does not establish a
fresh skill installation or native activation.

The complete local suite passes 1,548 tests; all seven course validators and generated HTML checks pass. Controller and transaction regressions exercise failed replacement, interrupted recovery, ownership, malformed evidence, source drift, ambiguous dispatch, cancellation and completed-stage resume. Nine added regression cases cover named-variant topology and workload consistency, interrupted claims with and without a conflicting campaign, cleanup failure before and after removal, and preservation of a newer claim. Original failures were reproduced before repair; follow-up read-only review found no remaining serious issue in the changed paths. Host-compiled CUDA argument tests verify canonical profile flags before device work. Skill structure/frontmatter and real npx discovery, copied-resource parity, repeat installation and isolation pass for Codex and Claude Code; fresh host trigger and baseline quality comparisons were not run.

Fresh GPU Fundamentals Lab 01 small completed four Slurm jobs on the authorized H200 substitute: two unprofiled trials and separate Systems/Compute captures. Independent correctness, native CLI contents and twelve rendered Grafana values passed. Four actual headless Playwright screenshots, two sanitized result JSON files, a CSV summary and a provenance manifest are exported under that lab/profile, with a separate results ZIP. Completed-campaign resume performed no work and ZIP regeneration preserved its checksum. Capture limitations are recorded in the manifest. The alignment repairs were tested locally without new live GPU runs. This qualifies the representative workflow, not all 110 recipes, the large workload or H100 performance; full-catalog live execution remains a separate campaign.

Subsequent prepared-target H200 verification completed CUDA Labs 11 through 13 for both profiles. The capstone retained its separate zero-error memcheck, all three counterbalanced unprofiled children, independently checked aggregate math and observed GPU identity, both original native reports, and all child metrics across two publication generations. Actual Grafana, Systems and Compute images were inspected and hash-bound before export; artifact hashes, sanitized contents and ZIP bytes matched. The qualification lab retained two fresh equivalent runs per profile and all three metric views. Original timing samples and output arrays were not retained, application integration remains pending, and full-catalog completion is not claimed.

Fundamentals Lab 03 source verification added mandatory host-copy admission and mechanism-specific browser approval. Twenty-three pre-repair failures establish the missing native mechanism check; the controller/evidence-runner suites pass 109 tests after repair. A separate process/thread binding negative control now passes. Original source-bound transfer rows and eight corruption controls pass independent qualification. The maintained and project-installed skill now complete Fundamentals Lab 03 in both workload profiles on a prepared H200 target. Eight exact jobs completed successfully; four original Systems reports independently prove all four ordered transfer modes, successful correlations, complete byte totals, synchronization and final readback. Original files, native query proofs and frozen source hashes were rechecked after jobs became quiescent. Four actual Grafana tables match all 32 displayed values, including verified SI display conversion; four actual Systems views match the selected measured copies and surrounding workload ranges. Both exports contain the two original results, four reviewed PNGs, a 16-row summary and manifest; checksums, review joins, privacy projections and ZIP bytes pass. This verifies the host-copy evidence repair, not full-catalog completion. Diagnostic warnings remain retained; per-mode full payloads and raw timing samples are absent, and no overlap or equal-work speedup is claimed.

<!-- /FEATURE: FEAT-034 -->

<!-- FEATURE: FEAT-035 reqs=REQ-002,REQ-015,REQ-016,REQ-017,REQ-018,REQ-020,REQ-022 status=ready delivery=implemented priority=P1 version=7 -->
### FEAT-035: Shared environment and lab execution guide

#### Requirements Covered

- REQ-002: Preserve standalone lessons and practical teaching with a shared setup entry.
- REQ-015: Synchronize the shared README and generated guide.
- REQ-016: Concise shared environment setup without a numbered Lab 00.
- REQ-017: Managed profiling, readiness and per-lab Grafana evidence.
- REQ-018: Preserve the ordinary and advanced hardware/runtime routes.
- REQ-020: Retain the advanced labs-only profile and distinct prerequisites.
- REQ-022: One concise learner guide with dedicated dashboard browsing for all practical courses.

#### Context Evidence

Before this change, the README combined learner operations and maintainer detail. Six setup documents were enforced by metadata, renderer and validators. Course captures are private and outside the browser viewer mount; container runners expected the superseded manual installer prefix. Dashboard imports change source configuration, whereas profiling installation requires an accepted matching generation.

#### Design Details

README.md is the sole authored shared guide with three H2 sections: How to set up the lab, How to run the labs, and Browsing Grafana and Nsight Profilers. Render lab-guide.html through the existing builder and freshness checks, link course/catalog pages to it, and synchronize both shared files plus the inline docs/grafana.png asset. Keep common Python/publishing setup in the shared guide. Move inference serving to its course README, CUDA builds to Lab 13, fabric tools to advanced Lab 01, MPI NCCL Tests to Lab 10, and vendor-specific qualifications to Labs 29-34. The advanced course README owns the joint vendor install because the existing installer requires all three stacks together. Move maintainer information to docs/maintaining-courses.md. Standalone kits remain executable packages; setup uses the shared guide and sync requires a Git clone.

Runtime preparation begins with the unchanged explicit-target sync-labs.sh command; it copies source and opens SSH but does not install packages or copy private monitoring configuration. Browsing begins with three separate explicit-context, loopback-only forwards. Grafana uses discovered service values; Nsight uses installer-selected namespace/service and paired HTTP/TURN ports. Follow with URLs, login guidance, Grafana selections and selected native-report export/inspection. Keep root-to-lab and generated-page anchors valid. This is a documentation/rendering change, with no runtime-script, stack, AI subsystem or live-target change.

Remove setup_guide metadata and the Lab 00 renderer/validator contract. Replace active setup referrals, preserving experiment numbers and explanations. Rename the synthetic readiness identity to environment_readiness across producer, recipe, publisher, inspector and dashboard together; retain distinct worker/GPU checks, without an alias or historical-result migration.

The workstation flow is create/render/deploy, profiling install with --interactive for initial credentials, grafana install --config ./config.yaml --target CLUSTER_TARGET --pushgateway, discovery-only course setup, then per-course directory imports and validation. Keep private services, local metrics, discovered datasource mapping and explicit folder selection. Before installation, explain result JSON, publication, scraping, storage and Grafana queries, with docs/grafana.png inline. Embed that existing PNG in the standalone generated guide with responsive sizing and accessible alternative text. Individual labs publish results and explain dashboards but do not reinstall them.

Retire the course manual profiler installer. Use managed activation and package-owned profiler roots, bound read-only into containers with support resources. Preserve COURSE_TOOLS for publishing/fabric helpers. Export explicitly selected report copies into a unique viewer directory, verify hashes and set read/traverse permissions on copies only. Retain workload-specific prerequisites and separate unprofiled acceptance from diagnostic capture. Direct Python lab commands and dependency checks use the restored COURSE_PYTHON interpreter; standard-library submission and validation tools use python3.

#### Selected Option

One authored README and one linked generated HTML guide. This supersedes the per-course Lab 00 document/rendering decisions in FEAT-026 and the per-lab dashboard import/manual installer instructions in FEAT-027; it preserves their teaching, monitoring and evidence safeguards.

#### Alternatives Considered

Keeping six setup guides retains duplication. Embedding shared setup into every textbook increases repeated reading and was not selected. Removing readiness loses environment verification. Broadly exposing private results is unnecessary; only selected copies enter viewer storage.

#### Implementation Boundaries

Courses source, helpers, container examples, metadata, tests, generated artifacts and documentation. No cxcli API change, cluster mutation, publication or commit. Preserve unrelated worktree edits.

#### Test-First Success Criteria

- TDD-001: Missing/stale shared guide, missing sync artifacts, invalid setup links or legacy setup metadata fail validation.
- TDD-002: Readiness duplicate-worker evidence remains rejected and numbered experiment identities remain unchanged.
- TDD-003: Managed profiler bindings include required resources and activation; private report originals are unchanged.

#### Validation Plan

Inspect complete source/HTML parity, shell syntax and documented command order; run focused tests, canonical-copy checks, all course validators and browser navigation checks.

#### Test Plan

Cover shared-guide rendering and link destinations, sync manifests, downloadable-kit metadata, readiness publication, managed container binding and existing profiling/observability regressions. Catalog ownership and migrated-guide tests select their intended GPU course identities independently of presentation order. Inference progression tests verify qualified prerequisites, launcher-owned startup/readiness/cleanup and both Lab 30 routes, while retaining the optional paper exercise and later Lab 15 campaign boundaries. Mocked prefix-cache and chunked-prefill launcher tests acknowledge recorded server startup through an explicit readiness signal, cover normal and delayed startup, distinguish health from metrics requests and retain exact trial ordering, counts and quality gates.

#### Evaluation Plan

Inspect desktop/mobile guide and course navigation. Record static/browser evidence separately from pending installed-tool, GPU, container and live cluster qualification.

#### Rollout And Rollback

Rebuild shared/catalog/course HTML together with the existing builder. Revert only this task's source/artifact delta if needed; do not alter deployed environments or historical results.

#### Done Definition

One concise shared guide replaces every numbered setup lab, all required setup/run/profile capabilities remain reachable, aligned helpers and source/generated validators pass, and live limitations are explicit.

#### Implementation Evidence

2026-09-23 concise-guide revision: the shared README now has setup, run and browsing sections. Runtime preparation starts with the unchanged sync/SSH script. Specialized commands moved to inference serving, CUDA Lab 13, fabric Lab 01, NCCL Tests Lab 10 and the advanced course joint vendor procedure; Dynamo model preparation lives in Lab 32 and is reused by Labs 33-34. Three explicit-target loopback forwarding commands precede Grafana and native-report browsing. Generated links resolve to actual course/lab fragments; the advanced page includes its course README so runtime setup is reachable. No sync/runtime behavior or cluster state changed.

2026-09-23 update: the README and catalog use general Performance Engineering branding. The shared guide explains benchmark publication and sampled telemetry, embeds the existing docs/grafana.png unchanged, and uses the cxcli Grafana/Pushgateway install command followed by discovery. The builder embeds validated PNG bytes with alternative text and responsive sizing. Synchronization includes the PNG alongside the README and standalone HTML. Maintainer guidance, cxcli consumer documentation and the repository changelog agree.

Implemented the two-section README and generated shared guide; removed all six setup documents and their metadata/rendering contract. Course/catalog links and synchronization include the shared guide. Environment readiness now uses one unnumbered identity across producers, recipes, dashboards and publication checks, with distinct-worker/GPU rejection retained. All 110 lab guides refer to shared setup and their assigned dashboards. Managed profiler discovery mounts complete package resources and activation read-only; selected-report export preserves private originals. Per-course runtimes include isolated inference mechanics/serving clients, CUDA builds and advanced fabric/vendor settings. Maintainer guidance moved to docs/maintaining-courses.md. Canonical helpers, generated pages, source skill references and changelog are aligned.

#### Verification Evidence

Current documentation reconciliation, 2026-09-25: the course READMEs, guides, version records and publication reviews now distinguish completed prepared-H200 experiments from H100 installation candidates. Workload labels select presets independently of baseline/candidate controls and can have identical effective parameters. Training guidance describes the supplied three-update in-memory checkpoint exercise, packing without a model loss, two-worker prefetch capacities, CPU batch readiness as a proxy, a compiled expression separately from captured eager updates, and the actual capstone matrix-expression comparison. Optional extensions remain explicit. Inference Lab 34 is a chunked-scheduling comparison rather than a prefix-cache experiment. Training Lab 31 changes only its metadata reference to Advanced Labs 19 and 21; AST comparison confirms no computational change.

All nine generated pages are current; all seven repository validators and canonical helper/dashboard checks pass. Focused documentation/content verification passed 333 tests with five skips. The final 27-case owned, isolated headless Chrome 153.0.8010.53 Playwright Test run passed at 1440, 390 and 320 pixels, covering fragments, keyboard navigation, applicable mobile TOCs and local scrollers, loaded images and enlarged-text reflow. Eighteen actual desktop/narrow captures were visually reviewed. The generic skill checker retains its documented local-link, embedded/footer-markup and special-profile findings; it is not claimed passed. Publication reviews bind each current HTML hash. These documentation checks are separate from the completed 220-profile live campaign recorded in FEAT-034.

Earlier verification paragraphs below retain their original scope and artifact identities.

The concise-guide revision passes 129 focused tests, including clean-shell vendor runtime restoration, private report-copy permissions, generated-guide parity and actual relocated-link destinations. All seven course validators, canonical helper parity, scoped Ruff and configured Markdown lint, whitespace checks and syntax checks for 73 documented Bash blocks pass. Browser checks at 1440, 390 and 320 pixels confirm the three guide sections, nine actual runtime-link navigations and no horizontal page overflow; the desktop browsing section was visually reviewed. Separate read-only code/security review found no serious issue. Live dashboard forwarding, dependency installation and GPU execution were not exercised.

2026-09-23 update: focused setup, guide, catalog, image, publishing and synchronization coverage is included in the 337 passing tests recorded under FEAT-033. Headless Chrome checks the guide title, decoded image dimensions, alternative text and absence of horizontal page overflow at 1440, 390 and 320 pixels, plus links from all seven course pages. Rendered diagram screenshots were visually inspected. Generated guide, catalog and all seven pages are current. No live cluster changes or external publication occurred.

Source validation on 2026-09-22: all seven course validators, canonical helper/dashboard parity and generated-page freshness checks pass. The available offline regression suite passed 1,406 tests, skipped 136 and retained three failures reproduced from the pre-change snapshot: two advanced-course tests assume a practical-only catalog slice, and one inference test expects older wording. Two modules and one separate test require unavailable PyTorch and were excluded explicitly. Changed Python lint/format, configured Markdown and ShellCheck pass. Browser checks cover the shared guide and navigation from the catalog and all seven courses at 1440, 390 and 320 pixels (27 checks); screenshots were reviewed and the guide has no page-width overflow. Read-only code/security review findings were repaired and confirmed closed. Fixtures exercise runtime restoration, exact link destinations and rejected traversal, complete profiler bindings, argument boundaries, ambient-PATH rejection and private report-copy permissions. No package installation, cluster deployment, native profiler/container execution or H100 qualification was performed; live behavior remains pending.

Explicit alignment follow-up on 2026-09-22 repaired direct Python documentation commands that bypassed the restored runtime and clarified shared-guide build help. Six real-shell interpreter-selection regressions failed before repair and passed afterward, including an interpreter path with spaces. The final affected suite passed 311 tests; all seven validators, source/helper/dashboard parity, configured lint/format, shell syntax and ShellCheck passed. Desktop/mobile navigation again passed 27 checks. Nested code/security review found no remaining material issue in the repaired surfaces. The broader offline run reproduced the same three unrelated pre-existing failures (1,406 passed, 136 skipped, one deselected); PyTorch-dependent and live-runtime coverage remain unavailable.

Troubleshooting follow-up on 2026-09-22 reproduced and repaired all three previously reported failures. Advanced-course tests now select their intended GPU courses by identity; the assertions remain effective with a reordered catalog and reject duplicate source ownership. Inference progression checks current qualified prerequisites, both launch routes and launcher-owned startup/readiness/cleanup; removing a required route still fails. Broader verification exposed two additional mock-readiness races: a fixed sleep could report success before fake server startup was recorded. Delayed-start fault injection reproduced all four affected success/mismatch cases failing. Explicit per-server readiness signals repair the fixtures, with health and metrics requests handled separately and trial counts, ordering and quality gates preserved. All 51 affected tests pass. The final available offline suite passes 1,419 tests, skips 136 and deselects one; the two PyTorch-dependent modules and seed test remain excluded because PyTorch is unavailable. All seven course validators, canonical helper/dashboard parity, changed Python lint/format, configured Markdown and final read-only code/security review pass. Production launchers and learner guidance are unchanged by these test repairs; no live cluster or GPU behavior was exercised.

<!-- /FEATURE: FEAT-035 -->

<!-- FEATURE: FEAT-036 reqs=REQ-017 status=ready delivery=verified priority=P1 version=2 -->
### FEAT-036: Worked GPU performance tool examples

#### Requirements Covered

- REQ-017: GPU profiling and per-lab Grafana evidence, including AC-011.

#### Context Evidence

The Fundamentals tools primer defines four tools but gives no per-tool visual example or NVTX marker/range code. Its renderer currently appends only one fixed measurement-loop figure.

#### Design Details

Expand only the Fundamentals primer with four original accessible SVG diagrams beside worked explanations: Systems host/device timeline, Compute kernel-report interpretation, NVTX point/range annotations, and Grafana selected results versus telemetry. Use explicitly synthetic values and short labels readable on mobile. Preserve the existing measurement loop and all practical lab identities and behavior. Show PyTorch NVTX mark and nested range context managers on one device-resident operation; explain host submission, explicit waiting, timeline correlation, and bounded range filtering. Add a brief retrieval/transfer check with feedback. Conclude with a connected workflow: baseline, NVTX labels in a diagnostic run, Systems timeline, Compute inspection when a kernel matters, and Grafana context around repeated unprofiled results.

Extend the shared renderer narrowly to embed declared course-local SVG images in the primer's How it works field. Reject unsafe paths, symlinks, active/external SVG content, duplicates and other placements. Keep generic Markdown image policy unchanged. Standalone validators check the declared diagram inventory, source bytes, location, and full prose/code parity. Synchronize generated validator copies and rebuild affected pages; runtime files are supplied by sync-labs.sh rather than lab-kit archives.

#### Selected Option

Original course-owned teaching diagrams and a small PyTorch snippet using the existing stack; no dependency or live profiling requirement for authoring.

#### Alternatives Considered

Real frontend captures require a qualified run and privacy review; original labeled diagrams expose the relationships without presenting invented measurements as real evidence.

#### Implementation Boundaries

Fundamentals teaching and references; shared rendering/validation and generated consumers only as needed. Preserve unrelated changes, runtime behavior, existing lesson/lab numbers and safe static publication.

#### Test-First Success Criteria

- TDD-001: Primer rendering accepts contextual local figures and preserves fenced code while rejecting unsafe, duplicate and misplaced images.
- TDD-002: Independent validation rejects missing or altered SVG/code content and wrong diagram placement.

#### Validation Plan

Check official vendor semantics, source/HTML parity, all affected standalone validators, focused renderer tests and changed-source lint. Run the bounded skill checker without weakening its known course-format differences.

#### Test Plan

Add focused negative controls for SVG input and rendered parity. Parse the sample without executing GPU work. Keep real lab execution separate.

#### Evaluation Plan

Review each worked conclusion and inspect integrated figures at desktop, 390px and 320px with owned isolated headless browser resources when available. Check navigation, local code scrolling and enlarged-text reflow.

#### Rollout And Rollback

Regenerate local HTML from canonical source; no external publication. Compare with private task-start snapshots to preserve unrelated edits.

#### Done Definition

Four contextual visuals and the NVTX example are complete, readable, statically validated and reviewed with remaining evidence gaps explicit.

#### Implementation Evidence

The concluding mental model now connects NVTX-labeled diagnostic phases to Systems timelines, optional Compute kernel inspection and Grafana context around repeated unprofiled results. Canonical prose and generated Fundamentals HTML are aligned; the prior worked-example implementation remains intact.

Implemented in the Fundamentals primer, four original `reference/diagrams/tools-*.svg` assets, shared Markdown renderer and standalone course validator. Added passive/local SVG admission, contextual placement and exact SVG/code parity checks; synchronized all six validator copies and regenerated embedded course kits. Updated the course README, official references, changelog and publication review.

#### Verification Evidence

For the connected mental-model clarification, read-only semantic review found no material issue. All seven validators, generated freshness/helper parity, configured Markdown and whitespace checks pass. The generic skill checker retains its five previous diagnostics. No new browser or GPU run was required for this prose-only clarification; earlier browser/runtime evidence below applies to the preceding artifact.

81 focused renderer/content/diagram tests and all seven course validators pass. Generated-page freshness and helper parity pass. Scoped lint/format and Markdown checks pass. Owned isolated headless Chrome checks pass at 1440, 390 and 320 pixels, with all four diagrams visually reviewed at desktop and 320px; publication review records artifact identity and evidence. Read-only code/security review found no blocking issue. The generic skill checker retains the same five task-start format diagnostics and is not claimed passing. Python snippet syntax and rendered parity are verified; GPU execution and performance are not claimed. FEAT-036 verification covers this bounded authoring contract; REQ-017 remains active for its wider obligations.

<!-- /FEATURE: FEAT-036 -->

<!-- FEATURE: FEAT-037 reqs=REQ-023 status=superseded delivery=not-started priority=P1 version=2 -->
### FEAT-037: Shared course presentation and preserved orientation

#### Requirements Covered

- REQ-023: Consistent course presentation without content loss.

#### Context Evidence

Superseded by FEAT-002 version 15, which owns the implemented shared-format
contract and current validation evidence. This record preserves the original
planning identity; its separate implementation route was not started.

Seven courses already embed one shared stylesheet. Profile renderers differ in
reference and support-section presentation; next steps are prose rather than
lists and conceptual lessons lack the latest local next-step section.

#### Design Details

Keep one shared CSS and renderer contract across conceptual, text-only and
labs-only profiles. Render numbered official sources and unordered next-step
options. Keep Glossary a top-level section and local glossaries after lesson
next steps. Remove mission/syllabus from the published support guides and TOC;
retain authoring inputs and relocate unique learner context before excluding
these sections. Preserve exact lesson/lab identities, content, diagrams,
source listings, practical prerequisites and safety constraints. Rebuild pages
from canonical Markdown; update renderer, standalone checks and focused tests
as one change without legacy paths.

#### Selected Option

Reuse shared renderer/style owners and canonical course files.

#### Alternatives Considered

CSS-only markers cannot establish semantic lists. Deleting planning files would
risk losing context. Independent per-course HTML edits would break source parity.

#### Implementation Boundaries

Course presentation, canonical prose, renderer, validators, focused tests and
related docs only. No lab execution, installed skills or external publication.

#### Test-First Success Criteria

Negative fixtures reject unnumbered references, paragraph-only next steps,
missing or misplaced glossary and forbidden visible planning sections. Baseline
comparisons preserve teaching and practical identities.

#### Validation Plan

Run all course validators, generated/source-helper parity and focused tests.

#### Test Plan

Exercise all three profiles and malformed section/list structures.

#### Evaluation Plan

Owned isolated headless Chrome checks common typography, headings, lists,
fragments and reflow at desktop, 390px and 320px with visual inspection.

#### Rollout And Rollback

Rebuild local outputs only; restore only task-owned edits if a regression occurs.

#### Done Definition

Every course follows the requested presentation contract and focused checks pass;
unavailable browser or runtime evidence remains explicit.

#### Implementation Evidence

Consolidated into FEAT-002 version 15; see its shared-format delivery evidence.

#### Verification Evidence

See FEAT-002 and each current PUBLICATION-REVIEW.md for source and browser evidence.

<!-- /FEATURE: FEAT-037 -->

<!-- maintain-project-specs:design:end -->
<!-- markdownlint-enable MD001 MD024 -->
