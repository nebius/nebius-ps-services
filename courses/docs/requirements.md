<!-- markdownlint-disable MD001 MD024 -->
<!-- maintain-project-specs:requirements:start schema=maintain-project-specs/requirements-v2 -->
# Project Requirements

<!-- REQUIREMENT: REQ-001 status=active priority=P0 type=feature -->
### REQ-001: Five-course NVIDIA H100 learning path

#### User Story

Engineers need five focused, independently usable courses that progress from GPU foundations through general optimization into LLM and CUDA-kernel specializations.

#### Acceptance Criteria

- AC-001: The catalog contains `gpu-fundamentals`, `gpu-optimizations`, `llm-training`, `llm-inference`, and `custom-cuda-kernels` in that order.
- AC-002: Each course is a new standalone package with one canonical implementation.
- AC-003: Fundamentals and Optimizations are prerequisites for the three specialized courses; neither LLM course is a prerequisite for Custom Kernels.
- AC-004: Each complete teaching topic and experiment has a course owner selected by its learning objective. Move misplaced content together with its labs, guides, diagrams, references and launch/test wiring; retain standalone setup helpers where each environment needs them.

#### Negative Criteria

- NC-001: No backward-compatibility layer, legacy alias, redirect, or cross-course runtime import is provided.

#### Validation Method

Inspect the root catalog, all five package roots, and the exact course-root inventory.

#### Test Method

Run the shared five-root structure, prerequisite, ownership, and standalone-path tests.

#### Evaluation Method

Confirm each package is independently navigable, installable where applicable, and usable from its own root.

<!-- /REQUIREMENT: REQ-001 -->

<!-- REQUIREMENT: REQ-002 status=active priority=P0 type=quality -->
### REQ-002: Consistent practical and publication-safe teaching contract

#### User Story

Engineers need clear explanations, diagrams, runnable examples, interpretation guidance, and troubleshooting rather than survey-only material.

#### Acceptance Criteria

- AC-001: Every course provides mission, syllabus, detailed lessons, self-contained HTML, glossary, resources, versions, publication review, labs, Slurm launchers, validation, and cluster-smoke guidance.
- AC-002: Every lesson uses a concise conceptual title and the ordered sections Objective, How it works, Practice labs and Mental model. How it works integrates definitions, useful prerequisite connections, purpose and causal explanation, with at least one relevant diagram inside that section. The final Mental model summarizes already-explained concepts. The linked labs own hardware context, worked practice, trade-offs, evidence, interpretation, troubleshooting, answers and review.
- AC-003: Every topology, execution, memory, scheduling, or parallelism concept has an accessible responsive inline diagram.
- AC-004: Public course claims use legitimate current official/vendor references collected at the end of each HTML course.
- AC-005: Do not publish source-coverage tables, course-history comparisons, previous-course names, migration notes, or historical learning records. Explain advanced topic limitations in the lessons themselves.
- AC-006: All five HTML courses use the same navigation, typography, color
  palette, lesson formatting, diagram treatment, and supporting-guide layout.
  Use a light digital-textbook presentation with a sticky side-panel TOC,
  comfortable reading width, restrained callouts and accessible mobile layout.
  The top banner contains only the main course title and estimated guided hours.
- AC-008: Teaching-stage labels and diagram titles have no trailing period.
- AC-009: Every diagram has one primary inline home immediately after its
  relevant lesson field or lab section. Secondary uses link to that home; there is no separate
  end-of-course diagram gallery.
- AC-010: Use a wider responsive content frame. Diagrams fit its full available
  width without a forced minimum width, clipping, or mandatory horizontal pan.
  Keep text explanations and diagram captions readable on narrow screens.
- AC-011: The sidebar TOC is the canonical course navigation; do not render
  repeated Back to contents links. Do not render expandable diagram-label
  transcripts. Retain accessible SVG titles/descriptions and visible captions.
- AC-012: Diagram labels, quantities and connector relationships accurately
  represent the adjacent lesson or lab explanation. Declare overview layouts explicitly rather
  than inferring semantics from titles; distinguish sequence, comparison,
  containment and overlap, and identify schematic or modeled quantities. For timelines,
  state the reading direction and explain in plain language when box heights
  and gaps do not represent elapsed time.
- AC-007: Keep only a short estimated-guided-hours label in the banner and
  concise course metadata. Do not expose time formulas, minute allocations,
  duration-calculation tables, or a guided-learning-plan section.

#### Negative Criteria

- NC-001: Course artifacts contain no secrets, private paths, private endpoints, customer data, private-source attribution, or unsupported performance claims.

#### Validation Method

Run course validators, public-safety scans, educational-completeness review, and desktop/mobile browser review.

#### Test Method

Test artifact presence, source-to-HTML parity, official-reference allowlists, diagram accessibility, links, and lab guide completeness.

#### Evaluation Method

Review each course as a learner and verify that every important concept leads to practical evidence or a documented deferral.

<!-- /REQUIREMENT: REQ-002 -->

<!-- REQUIREMENT: REQ-003 status=active priority=P0 type=feature -->
### REQ-003: H100 GPU fundamentals coverage

#### User Story

Engineers need an H100-centered mental model connecting the software stack, execution, memory, precision, topology, sharing, and health signals.

#### Acceptance Criteria

- AC-001: The course covers driver/runtime/toolkit/PTX/SASS/framework compatibility, H100 execution, SIMT, memory, timing, precision, Tensor Cores, roofline, and NCCL.
- AC-002: It adds practical compatibility, scheduling/tail, and read-only health evidence without reconfiguring the GPU.
- AC-003: Blackwell and later behavior is clearly comparative and not part of the runnable H100 contract.
- AC-004: Lesson 1 introduces an H100 SXM 80 GB overview containing only SMs,
  L2 and HBM before the single-SM diagram. Explain GPC/TPC/SM/SMSP containment,
  grid/block/warp/thread grouping, enabled hardware counts and resource-limited
  residency without equating threads with cores or launch size with concurrency.
  Distinguish the 32-block hardware ceiling from the two-block thread-capacity
  limit for 1,024-thread blocks, with a block-size comparison and explicit
  register/shared-memory qualifications. Define resident work through concrete
  SM placement, reserved registers/shared memory and a waiting-versus-executing
  warp example; distinguish residency from simultaneous instruction execution.

#### Negative Criteria

- NC-001: MIG, MPS, time-slicing, health, or topology lessons do not mutate cluster configuration.

#### Validation Method

Review the lessons and runnable exercises and run the offline validator plus the documented one- and two-node H100 smoke paths.

#### Test Method

Exercise compatibility reporting, scheduling/tail classification, health parsing, and NCCL launcher contracts.

#### Evaluation Method

Confirm learners can explain the observed bottleneck and system state from retained evidence.

<!-- /REQUIREMENT: REQ-003 -->

<!-- REQUIREMENT: REQ-004 status=active priority=P0 type=feature -->
### REQ-004: General PyTorch GPU optimization workflow

#### User Story

Engineers need an evidence-first optimization course that applies broadly without duplicating LLM- or kernel-specific ownership.

#### Acceptance Criteria

- AC-001: The course teaches baseline, limiter classification, evidence selection, single-factor change, and remeasurement.
- AC-002: Labs cover asynchronous timing, profiling, launch overhead, fusion, compilation, graphs, input, memory/layout, allocation, precision/shape, load balance, tails, and distributed overlap.
- AC-003: Activation checkpointing belongs to Training and attention/SDPA belongs to Inference.
- AC-004: A library-first decision gate precedes escalation to custom kernels.

#### Negative Criteria

- NC-001: The course does not retain duplicate domain-specific labs or imply that handwritten kernels are the default optimization path.

#### Validation Method

Review topic ownership and run the general optimization labs and capstone evidence contract.

#### Test Method

Test timing controls, profiler payloads, tail classification, launcher safety, and single domain ownership.

#### Evaluation Method

Confirm each recommendation is traceable to measured evidence and a general performance limiter.

<!-- /REQUIREMENT: REQ-004 -->

<!-- REQUIREMENT: REQ-005 status=active priority=P0 type=feature -->
### REQ-005: Practical LLM training optimization course

#### User Story

Engineers need runnable H100 training material spanning correctness, memory, precision, data flow, parallelism, profiling, and recovery.

#### Acceptance Criteria

- AC-001: The course owns transformer training, loss masking, accumulation, DDP, FSDP2, LoRA/SFT, GRPO, activation checkpointing, mixed precision, and Transformer Engine FP8.
- AC-002: Labs add deterministic resume, sequence packing, selective recomputation, input starvation, fused operations/graphs, communication overlap, TP/PP/CP/EP mechanics, expert balance, MFU analysis, and a causal capstone.
- AC-003: Two-node results are labeled bounded mechanics and never presented as production-scale topology proof.

#### Negative Criteria

- NC-001: The course does not own serving engines, KV-cache optimization, or inference latency claims.

#### Validation Method

Run offline checks, isolated dependency checks, one-GPU training smoke, and bounded two-node DDP/FSDP2 exercises.

#### Test Method

Test numerical equivalence, resume state, padding/packing accounting, parallel payloads, and evidence records.

#### Evaluation Method

Compare loss behavior, step time, tokens per second, memory, communication, and MFU across controlled trials.

<!-- /REQUIREMENT: REQ-005 -->

<!-- REQUIREMENT: REQ-006 status=active priority=P0 type=feature -->
### REQ-006: Practical LLM inference optimization course

#### User Story

Engineers need an inference course that connects prefill/decode mechanics to memory, scheduling, serving engines, and user-facing latency metrics.

#### Acceptance Criteria

- AC-001: The course owns KV cache, prefill/decode, generation, vLLM, streaming, artifacts, sampling, bucketing, prefix caching, speculative decoding, and SDPA.
- AC-002: Labs add ISL/OSL analysis, TTFT and inter-token metrics, GQA/MQA sizing, paged KV, batching/chunked prefill, quantization, backend profiling, TP/EP mechanics, TensorRT-LLM/Triton, AIPerf, and a causal capstone.
- AC-003: Dynamo disaggregation and KV-aware routing are advanced conditional material, not core completion gates.
- AC-004: Mechanics and serving dependencies remain isolated.

#### Negative Criteria

- NC-001: The course does not claim unsupported RDMA, NVLink/NVSwitch, production disaggregation, quality, latency, or throughput results.

#### Validation Method

Run offline checks, separate environment checks, one-GPU mechanics/serving smoke, and bounded two-node serving exercises.

#### Test Method

Test cache sizing, metrics, scheduling, quantization comparison, client payloads, launcher safety, and engine profile gating.

#### Evaluation Method

Judge TTFT, ITL/TPOT, throughput, output-token rate, memory, concurrency, and quality from controlled workloads.

<!-- /REQUIREMENT: REQ-006 -->

<!-- REQUIREMENT: REQ-007 status=active priority=P0 type=feature -->
### REQ-007: CUDA C++ custom-kernel optimization course

#### User Story

Engineers need a safe, library-first path for deciding when custom H100 CUDA kernels are justified, then building, profiling and accepting them.

#### Acceptance Criteria

- AC-001: Learner examples are CUDA C++20/C++ and build with CMake targeting compute capability 9.0 by default.
- AC-002: Labs cover preflight, vector operations, fusion, transpose, reduction, stencil, divergence, resources, async pipelines, library epilogues, optional Hopper features, fused residual/RMSNorm, and a capstone.
- AC-003: Every optimization is checked against a trusted reference, sanitizer evidence, profiler evidence, and end-to-end behavior.
- AC-004: `sm_90a` and CUDA Tile C++ are opt-in, version-gated, and explicitly non-portable where applicable.
- AC-005: Define GPU kernels and host/device dispatch before customization; introduce maintained CUDA libraries, reusable host-facing APIs, distribution choices, support contracts and release verification. Publication guidance never implies an external release or an unimplemented package is already available.

#### Negative Criteria

- NC-001: The course does not promise that handwritten kernels outperform mature libraries or require distributed execution.

#### Validation Method

Run source/HTML checks locally and CMake, CTest, Compute Sanitizer, Nsight, and performance exercises on one H100.

#### Test Method

Test edge shapes, error handling, FP32 and reduced-precision tolerances, build options, Slurm launchers, and embedded C++ parity.

#### Evaluation Method

Keep a custom kernel only when correctness, safety, kernel timing, and end-to-end evidence justify its maintenance cost.

<!-- /REQUIREMENT: REQ-007 -->

<!-- REQUIREMENT: REQ-008 status=active priority=P0 type=quality -->
### REQ-008: Separate evidence lanes and reproducible environments

#### User Story

Course maintainers and learners need to distinguish authored source, installed dependencies, runtime activation, and live H100 proof.

#### Acceptance Criteria

- AC-001: Publication status records source/static, installed environment, runtime activation, and live H100 completion independently.
- AC-002: Exact qualified versions or image digests are documented per course; PyTorch 2.14 is tried first and an affected course may retain 2.13 only with a recorded compatibility blocker.
- AC-003: FP32 uses `rtol=1e-5, atol=1e-6`; FP16/BF16 uses `rtol=1e-2, atol=1e-2`; FP8 and quantized labs document recipe-specific tolerances. Optimizations Lab 16 retains BF16 and checks both paths independently against an FP64 reference, adding an explicit bound for the composition's intermediate BF16 rounding. Its guide derives the bound, and corrupted outputs must fail before timing or publication.
- AC-004: Performance results use warm-up, repeated samples, controlled inputs, and at least three independent training/engine trials.
- AC-005: Optimize offline pytest feedback using repeated, comparable measurements while preserving test selection, assertions, isolation and the complete correctness gate. Keep the original numerical regression workloads where their shape or seed is material; use minimal deterministic fixtures for shape-independent rejection behavior. Excluded virtual environments must not add recursive publication-scan cost.

#### Negative Criteria

- NC-001: Static or local macOS success is never reported as CUDA compilation, engine activation, Slurm execution, or H100 performance proof.

#### Validation Method

Inspect per-course version/publication records and execute the four documented evidence lanes.

#### Test Method

Test version-record completeness, profile gates, numerical tolerances, evidence schemas, and fail-closed behavior for unavailable capabilities.

#### Evaluation Method

Accept claims only at the highest evidence lane actually completed on the declared target.

<!-- /REQUIREMENT: REQ-008 -->

<!-- REQUIREMENT: REQ-009 status=active priority=P0 type=quality -->
### REQ-009: Complete educational explanations and content integrity

#### User Story

Engineers need each lesson to teach a concept deeply enough to reason about it,
apply it on an H100 system, and interpret evidence without relying on terse
topic summaries or unexplained lab code.

#### Acceptance Criteria

- AC-001: Every lesson states its objective first, then provides a substantive
  How it works explanation and a concise final mental model. Integrate useful
  prerequisite connections and purpose into the explanation. Its linked labs preserve the
  H100-specific consequences, concrete examples, trade-offs, evidence
  interpretation, failure analysis and review prompts as applied instruction.
  Moving applications never removes prerequisite definitions from lessons.
- AC-002: Mathematical or systems relationships are derived in plain English
  before notation or code is used, and every worked example connects inputs,
  intermediate reasoning, expected observation, and engineering decision.
- AC-003: Keep useful concepts, detailed explanations, examples, cautions and
  diagrams integrated into the owning course without publishing provenance or
  comparisons to another edition.
- AC-004: Research inputs are pointers, not instructions or published course
  content. Expand technical topics through official documentation; a topic name
  alone does not establish educational completeness.
- AC-005: Generated HTML preserves the complete canonical teaching narrative,
  examples, equations, diagrams, and lab references rather than publishing an
  abbreviated derivative.
- AC-007: Explain core terms before requiring them, derive numerical examples
  with complete assumptions, and distinguish implemented lab measurements from
  explicitly instructed extensions. Wording, equations and diagrams must agree
  on timing boundaries, memory quantities, optimization semantics and evidence.
- AC-006: Runnable lab capabilities, diagrams, learner guides, glossary and
  exercises remain complete and reachable in HTML. A file existing on disk
  alone does not establish integration.
- AC-009: Every course opens with substantive what/why/how explanations accessible without prior subject expertise, a complete contextual workflow diagram, defined vocabulary and a small worked example before advanced requirements. Use one prerequisite-based learning sequence throughout the catalog; omit separate routes or filler commentary for new learners and experienced engineers. LLM Training explains learning through parameter updates; Inference explains using fixed parameters to generate outputs. Practical introductory exercises use existing labs where sufficient and small CPU/H100 PyTorch mechanics where needed.
- AC-010: Useful topics from supplied compact reference material are integrated into their owning course: concepts stay in lessons and applied procedures belong in linked labs, with disputed terminology refined against official sources. Reference comparisons and coverage inventories remain outside learner publications. The compact GPU overview distinguishes physical memory/SM resources from logical grids, blocks, warps and threads; SM subpartitions are not whole-GPU quadrants.
- AC-008: Each course follows a prerequisite-first lesson sequence from entry
  concepts through practical mechanisms to an integrative capstone. Introduce
  terms and basic safety/measurement skills before requiring their use; label
  previews, later revisits and optional advanced branches explicitly. The
  syllabus names each ordered lesson, its competency and practical activity.

- AC-011: Consolidate repeated full topics or experiments that teach the same objective without adding a distinct competency. Preserve useful refreshers, previews, deeper applications, controls and capstone assessments, explicitly explaining the added purpose. Carry every unique explanation, safety check and practical capability into the retained owner before removing a duplicate.

- AC-012: After the initial Objective, every lesson begins How it works with a plain-English definition before applications, use cases or optimization advice. Apply definition-before-application at each new topic within lessons, guides and optional study. Explain what it acts on, its components, causal steps and result; expand unfamiliar abbreviations such as Parallel Thread Execution (PTX) at first meaningful use, without requiring expansions of common CPU/GPU terms. Connect prerequisites in prose without standalone Prerequisite bridge, Recall or Why it matters fields. Preserve substantive course-entry explanations, mechanisms, examples and labs; a glossary link, acronym expansion or statement of benefits alone does not suffice.

- AC-014: All lesson titles describe their central subject rather than listing technologies or implementation components. Syllabus, lesson links and generated navigation use the same title. How it works includes at least one accessible diagram that explains the lesson's core mechanism, with explicit arrow/containment/comparison meaning and adjacent prose. Mental model comes last and introduces no unexplained concept.

- AC-013: Each course ends with an optional self-study section without a displayed research-review date introducing current technologies and advanced concepts through concise plain-English descriptions, a concrete study question, official public references, and explicit hardware/maturity boundaries. Distinguish recent developments from established advanced ideas; researched documentation does not qualify dependencies or target execution. Preserve required lessons, labs and guided hours.

#### Negative Criteria

- NC-001: A checklist, one-sentence field, diagram, or lab
  file alone does not satisfy the explanation requirement.
- NC-002: Presentation cleanup must not remove useful educational context,
  practical examples or safety qualifications.

#### Validation Method

Perform a lesson-by-lesson editorial review against current official sources
and the current learning objectives. Review all lab guides, glossary entries,
syllabi, supporting runbooks and diagram labels for clear grammar, consistent
terminology and formatting, precise units and agreement with the supplied code.
Inspect topic introductions within those materials as well as lesson openings:
before its first application, a learner must be able to explain what the new
concept is and how its essential parts relate.

For each How it works section, follow the causal sequence from its starting condition to
its result. Check that the prose identifies the acting components, explains
dependencies and completion, and introduces the quantities used in examples.
Review diagram arrows and captions against that same sequence.

#### Test Method

Run structural depth, teaching-component, content-integrity, source-to-HTML
parity, and five-course consistency tests, followed by desktop and narrow-screen
browser review.

#### Evaluation Method

Review topic introductions throughout every course and its practical guides.
Confirm a learner can explain what each newly taught technique is before its
application, then why it exists, how it changes execution or memory behavior,
when it helps, when it does not, what evidence to collect, and how to decide
the next action. Treat semantic review separately from structural checks.

<!-- /REQUIREMENT: REQ-009 -->
<!-- REQUIREMENT: REQ-010 status=active priority=P0 type=quality -->
### REQ-010: Lab-specific practical teaching guides

#### User Story

Engineers need to understand each lab's purpose, technology, code structure,
experiment, outputs and transferable lesson before running or adapting it.

#### Acceptance Criteria

- AC-001: Every numbered lab has a substantive introduction immediately after
  its title, followed by Before you start, Concepts and code path, Practice,
  Check your results, Investigate the behavior, If something goes wrong, and
  Takeaways and next step. Practice naturally combines a reasoned example,
  prediction, supplied baseline procedure and deliberate variation; it does
  not repeat separate Worked example and Practice blocks.
- AC-002: Explanations are specific to the supplied implementation. Explain
  component responsibilities, data/state flow and synchronization for complex
  labs without a line-by-line code commentary.
- AC-003: One authoritative human-readable title preserves technology
  capitalization. The source filename's lab number, heading, TOC, source
  disclosure and lesson links agree. Each lab has explicit relevant lessons.
- AC-004: Replace generic Prediction/Evidence/Interpretation/Troubleshooting
  cards with consistent, practical authored guidance in all five courses.
- AC-005: Run instructions name actual supported modes, inputs, prerequisites,
  launchers and result fields. Distinguish supplied behavior, learner
  extensions, simulations, operator mechanics and real-engine experiments.
- AC-006: State what correctness is actually checked and what remains
  unproven. Expected results do not promise unmeasured speedups, scalability,
  numerical equivalence or deployment readiness.
- AC-007: Canonical Markdown guides and complete source listings are embedded
  in the standalone HTML without abbreviation. Missing, orphaned, duplicated
  or mismatched guides and invalid lesson associations fail validation.
- AC-008: Preserve all useful lessons, labs, walkthroughs, inline diagrams and
  privacy/environment boundaries. Numbering gaps do not require aliases or
  compatibility files.
- AC-009: Before a lab is executed, its required techniques and technologies
  are taught in an owning lesson or an explicitly declared prerequisite
  course. That theory explains what each technique is, what problem it serves
  and how to apply it, including the relevant operational and numerical
  constraints. A lab-guide definition supplements this teaching rather than
  serving as its only home. Every guide identifies its theory preparation;
  previews are distinguished from execution, and required labs do not depend
  on a later or optional lesson without an explicit prerequisite route.
- AC-010: Applied lesson material from H100 focus through Review has an explicit
  owning lab. Consolidate only actual duplication with that guide; retain
  distinct calculations, qualifications, controls and advanced extensions.
  Shared labs separate initial practice from later prerequisite-gated revisits.
  Lessons include exact linked lab titles before their final Mental model, with no parallel applied
  sections. Conceptual figures remain with theory; applied figures move with
  their lab context, with one explicit primary home and working cross-links.

#### Negative Criteria

- NC-001: Renaming the same generic sentences or generating explanations from
  filenames alone does not satisfy the lab-specific teaching requirement.
- NC-002: Synthetic recurrent-operator measurements must not be named or
  interpreted as language-model TTFT, TPOT, ITL or output-token throughput.

#### Validation Method

Audit every numbered source and guide, run static/source-parity and link
checks, and independently review the changed teaching and measurement bounds.
Trace each lab's actual computation, measurement, validation and coordination
techniques to theory available before its first execution in the syllabus.

#### Test Method

Exercise guide inventory/schema, identities, section completeness, full
narrative embedding, explicit lesson mappings, command syntax/help, negative
metadata cases and synthetic metric boundaries.

#### Evaluation Method

A learner can explain why the lab exists, how its main components cooperate,
what to run, what success and failure look like, what to investigate, and how
to transfer the technique to another workload.

<!-- /REQUIREMENT: REQ-010 -->

<!-- REQUIREMENT: REQ-011 status=active priority=P1 type=feature -->
### REQ-011: GPU networking foundations and controlled communication tuning

#### User Story

Engineers need to understand how NVIDIA GPU interconnects, network fabrics and
communication software cooperate before measuring and tuning multi-node work.

#### Acceptance Criteria

- AC-001: Fundamentals explains NVLink, NVSwitch, NCCL, RDMA, InfiniBand, RoCE and GPUDirect RDMA in plain English, with contextual diagrams distinguishing software, links, fabrics and direct-memory paths. New networking material is theory; existing collective labs remain intact.
- AC-002: Optimizations teaches topology and transport qualification before NCCL measurement/tuning, then application scaling and overlap. Lesson order, syllabus, sidebar TOC and lab assignments agree.
- AC-003: Python/PyTorch communication experiments and an externally built NVIDIA NCCL Tests exercise support the learner-managed two-node Slurm target, with message-size curves, correctness, repeated runs, metric interpretation and one-factor job-local tuning.
- AC-004: Explain algorithmic and normalized bus bandwidth, microsecond units, in-place/out-of-place results, runtime versions and the evidence needed to claim RDMA or GPUDirect RDMA.
- AC-005: Use official NVIDIA documentation, accessible contextual diagrams, complete lab guides, safe launchers, source/HTML parity and independent static versus live evidence.

#### Negative Criteria

- NC-001: No NIC, switch, driver, subnet-manager, firewall, Slurm or systemwide NCCL configuration changes. No universal tuning values or unmeasured speedup claims.
- NC-002: Two one-GPU nodes do not prove intra-node NVLink/NVSwitch or production-scale behavior. Missing supported MPI/RDMA/GDR prerequisites fail clearly or remain conditional.
- NC-003: Preserve existing useful lessons and labs; do not add cross-course runtime imports or learner-authored C++ outside Custom Kernels. NCCL Tests is an external vendor benchmark, not a new C++ learner implementation.

#### Validation Method

Research official definitions and version-sensitive flags; inspect lesson ordering,
diagram semantics, artifact mappings and regenerated HTML.

#### Test Method

Test parser validity and correctness rejection, fresh-process tuning settings,
safe launch arguments, CPU help/import behavior, lab/TOC parity and full validators.

#### Evaluation Method

Learners explain the actual path, run three independent comparisons, interpret
size-dependent results and validate any retained change against application time.
H100, Slurm, MPI and fabric activation remain target-run acceptance gates.

<!-- /REQUIREMENT: REQ-011 -->

<!-- REQUIREMENT: REQ-012 status=active priority=P1 type=feature -->
### REQ-012: Profile and optimize complete data-transfer paths

#### User Story

Engineers need complete experiments for transfer bottlenecks, communication
trade-offs and reusable inference state, integrated with their owning courses.

#### Acceptance Criteria

- AC-001: Compare reference topics with all five courses and preserve existing useful coverage. Verify technical claims against current NVIDIA documentation and framework API documentation; use original explanations without inline source comparisons or universal speedups.
- AC-002: Optimizations teaches NVTX timeline interpretation, bounded H2D copy/compute overlap, and D2H output workers, pinned-buffer reuse, completion events, backpressure and final drain through runnable experiments.
- AC-003: Training provides real DDP bucket sizing and FP16/BF16/PowerSGD hook experiments, an uncompressed global-batch reference, observed bucket sizes, numerical error and short training trajectories. Convergence and target speed remain separate gates.
- AC-004: Inference teaches KV retention, expiry, eviction, restore versus recompute and GDS boundaries with a deterministic CPU policy lab. Modeled costs never become measured engine latency or storage throughput.
- AC-005: Each addition has a complete seven-section guide, contextual diagram, lesson and syllabus integration, private result output, supported commands, and complete generated HTML/source parity. Shared publication and standalone validators also conform to the course skill's bounded HTML/source contract.

#### Negative Criteria

- NC-001: No transcript copies, vendor-specific benchmark promises, compatibility shims, dependency installation, fabric/storage administration, or external publication.
- NC-002: GPU buffers cannot be overwritten before their consumers complete; host consumers cannot read incomplete D2H results. Profiling duration is not acceptance timing.

#### Validation Method

Review official technical documentation, all-course ownership, teaching semantics,
buffer and collective dependencies, and source-to-publication integration.

#### Test Method

Exercise boundary/error cases, buffer ownership and drain, DDP reference and hook
contracts, cache policy invariants, supported CLI help, and all course validators.

#### Evaluation Method

Require equivalent work, independently repeated target trials and scoped evidence.
Keep source/CPU, installed environment, CUDA/NCCL/GDS, live H100 and browser lanes separate.

<!-- /REQUIREMENT: REQ-012 -->

<!-- REQUIREMENT: REQ-013 status=active priority=P1 type=feature -->
### REQ-013: Nebius course website and navigation

#### User Story

Learners need an attractive central course catalog, direct navigation between the five courses, and clear Nebius attribution and licensing on a publicly browsable website.

#### Acceptance Criteria

- AC-001: A self-contained light editorial catalog at `courses/index.html` introduces the five courses in canonical order with source-derived titles and guided hours, prerequisites, outcomes and working relative links.
- AC-002: The learning path shows Fundamentals then Optimization followed by three independent specializations. Every course has a catalog link, four direct sibling links and one current-course marker above its lesson contents.
- AC-003: The catalog and courses carry a readable small-print Nebius B.V. copyright, free educational resource statement and Apache-2.0 license link with the complete license embedded. Preserve third-party notices; do not impose noncommercial or resale restrictions.
- AC-004: The existing local build and check commands own the generated catalog and course pages. They retain embedded resources, keyboard navigation, readable responsive layouts and standalone course validation.
- AC-005: A minimal repository welcome page links to the catalog and GitHub. GitHub Pages publishes all eligible repository files from `main` `/` with `.nojekyll`, HTTPS and no custom workflow file.

#### Negative Criteria

- NC-001: No JavaScript framework, external runtime asset, new course prerequisite, arbitrary relative-link permission or license replacement is introduced.
- NC-002: Local checks do not establish live publication. Respect protected-branch review and verify the actual deployed revision and public routes.

#### Validation Method

Run generated-source parity, the standalone validators, metadata/navigation/license checks, desktop/mobile browser checks and authoritative GitHub Pages plus public HTTP verification.

#### Test Method

Exercise stale and missing catalog output, metadata changes, all navigation edges, current-course identity, disallowed links/resources and license-copy parity.

#### Evaluation Method

Navigate from the repository welcome page through the catalog and between all five courses using pointer and keyboard. Assess small-screen layout and readable attribution independently of static checks.

<!-- /REQUIREMENT: REQ-013 -->

<!-- maintain-project-specs:requirements:end -->
<!-- markdownlint-enable MD001 MD024 -->
