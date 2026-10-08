<!-- markdownlint-disable MD001 MD024 -->
<!-- maintain-project-specs:requirements:start schema=maintain-project-specs/requirements-v2 -->
# Project Requirements

<!-- REQUIREMENT: REQ-001 status=active priority=P0 type=feature -->
### REQ-001: GPU learning path, Slurm introduction and advanced fabric labs

#### User Story

Engineers need a Slurm and Soperator introduction, a shared performance-tools reference and a PyTorch tensor foundation before GPU foundations, optimization, LLM and CUDA-kernel specializations, and advanced communication labs.

#### Acceptance Criteria

- AC-001: The catalog, learning path, README reading route and all course/guide menus present ten resources in this order: Soperator, Lab Guide, GPU Performance Tools, PyTorch for GPU Performance Engineering, GPU Fundamentals, GPU Performance Optimization, LLM Training, LLM Inference, Custom CUDA Kernels, then Advanced Labs. Keep nine course packages; Lab Guide is the shared README-derived page, not a course package. Soperator is the first course because it teaches the Slurm concepts used throughout the GPU courses; it remains independently readable with basic Linux knowledge. GPU Performance Tools is prerequisite reading before lab-bearing courses and remains available as a reference during practice; it does not require GPU Fundamentals or a running cluster to read. The advanced course package is a labs-only route requiring two eight-H100 workers.
- AC-002: Each course is a new standalone package with one canonical implementation.
- AC-003: Fundamentals and Optimizations are prerequisites for the three specialized courses; neither LLM course is a prerequisite for Custom Kernels.
- AC-004: Each complete teaching topic and experiment has a course owner selected by its learning objective. Move misplaced content together with its labs, guides, diagrams, references and launch/test wiring; retain standalone setup helpers where each environment needs them.
- AC-005: Collection titles use Performance Engineering or GPU Optimization without H100 branding. Hardware-specific explanations and qualification requirements remain explicit.

#### Negative Criteria

- NC-001: No backward-compatibility layer, legacy alias, redirect, or cross-course runtime import is provided.

#### Validation Method

Inspect the nine-course catalog, five practical conceptual GPU packages, the text-only introduction, the reference-only tools course, the PyTorch lessons-only course and the advanced lab package.

#### Test Method

Run the shared catalog structure, prerequisite, ownership, and standalone-path tests.

#### Evaluation Method

Confirm each package is independently navigable, installable where applicable, and usable from its own root.

<!-- /REQUIREMENT: REQ-001 -->

<!-- REQUIREMENT: REQ-002 status=active priority=P0 type=quality -->
### REQ-002: Consistent practical and publication-safe teaching contract

#### User Story

Engineers need clear explanations, diagrams, runnable examples, interpretation guidance, and troubleshooting rather than survey-only material.

#### Acceptance Criteria

- AC-001: Every GPU course keeps maintainer-only mission and syllabus sources and provides detailed lessons, self-contained HTML, glossary, resources, versions, publication review, labs, Slurm launchers, validation, and cluster-smoke guidance. REQ-024 defines the reference-only performance tools course without labs or exercises. REQ-027 defines the visual PyTorch lessons-only course without labs or practice. REQ-020 defines an explicit labs-only profile without conceptual lessons. REQ-019 defines the explicit text-only profile; its no-lab, no-diagram scope supersedes GPU-specific requirements for that package only.
- AC-002: Except for the explicit reading-profile exceptions, every GPU lesson uses a concise conceptual title and the ordered sections Objective, How it works, Practice, Mental model, followed only by optional References. How it works integrates definitions, useful prerequisite connections, purpose and causal explanation, with at least one relevant diagram inside that section. The final Mental model summarizes already-explained concepts. The linked labs own hardware context, worked practice, trade-offs, evidence, interpretation, troubleshooting, answers and review.
- AC-003: Outside the explicitly diagram-free reading profiles, every GPU-course topology, execution, memory, scheduling, or parallelism concept has an accessible responsive inline diagram.
- AC-004: Public course claims use legitimate current official/vendor references collected at the end of each HTML course.
- AC-005: Do not publish source-coverage tables, course-history comparisons, previous-course names, migration notes, or historical learning records. Explain advanced topic limitations in the lessons themselves.
- AC-006: All HTML courses use the same navigation, typography, color
  palette, lesson formatting, diagram treatment, and supporting-guide layout.
  Use a light digital-textbook presentation with a sticky side-panel TOC,
  comfortable reading width, restrained callouts and accessible mobile layout.
  The top banner contains only the main course title and estimated guided hours.
- AC-008: Teaching-stage labels and diagram titles have no trailing period.
- AC-009: Every diagram has one primary inline home immediately after the specific
  paragraph, list, table or worked example that explains its topic, within the
  owning lesson field or lab section. Do not collect figures at the end of a
  broad section. Canonical Markdown declares each exact placement; missing,
  duplicate or wrong-home placements fail the build. Secondary uses link to
  that home; there is no separate end-of-course diagram gallery.
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
- AC-013: Catalog and course README sections or instructions intended for
  course maintainers carry a concise audience label. Keep learner setup,
  lab execution and shared guidance distinct from maintainer-only work.

- AC-014: All nine course packages share the same typography, spacing, readable TOC markers, section-label vocabulary and responsive reflow. Numbered lesson and lab identities remain stable. Soperator remains text-only and the advanced course remains labs-only; alignment must not fabricate missing lessons, labs or diagrams for those explicit profiles.

- AC-015: Standardization preserves complete explanations, worked examples, lesson/lab identities, executable behavior and evidence boundaries. Each conceptual Practice section states the activity at that stage, including previews and purposeful revisits; diagrams depict the taught relationship and Mental model introduces no new mechanism. Preserve the distinct text-only and labs-only profiles.

- AC-016: Every course glossary lists its abbreviations and terms alphabetically by displayed term, ignoring case and Markdown formatting. Glossary immediately precedes References in the course content and table of contents; References is the final content section before the license footer. Preserve every definition and existing glossary link target. Every course has exactly one top-level Glossary, sourced from GLOSSARY.md. Lessons and performance-tool guides contain no separate glossary sections. Consolidate their key terms and abbreviation expansions into the course glossary, merging repeated terms while preserving distinct definitions and contextual teaching. Any lesson References follow Mental model and end the lesson. The labs-only profile does not acquire conceptual lessons.

- AC-017: Learner pages and navigation omit Syllabus and Course mission sections and links. Preserve their unique audience, scope, readiness and completion guidance in course overviews and existing guides; retain authoring sources for maintainers. Each course has its own top-level Glossary section. Official references use numbered lists. Where to Go Next uses one bullet per complete onward-learning direction, with its explanation and caveats preserved. Every course has exactly one top-level Where to Go Next section, sourced from NEXT-STEPS.md, followed by exactly one Glossary and final Official references. Lessons and performance-tool guides contain neither appendix. Consolidation retains distinct optional learning directions, explanations and limits; simple next-lesson pointers may be removed when their purpose is already expressed by the destination objective and ordered TOC. Optional lesson References follow Mental model. All profiles share semantic heading levels, fonts, sizes and spacing for equivalent content.

- AC-018: All nine Course overviews use three short, course-specific paragraphs: purpose and outcomes; audience and prerequisites; practical scope and the next step. Retain brief hardware and safety boundaries, including Soperator's cluster-free reading and Advanced Labs' sixteen-GPU fabric requirement. Shared setup, submission, logs, workload profiles and access procedures belong in the shared Lab Guide. Preserve unique interpretation, qualification and completion guidance in the embedded course guide or owning lab before removing overview repetitions. Do not imply ordinary one-GPU labs require a separate cluster when qualified advanced hardware can be reused.

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
### REQ-003: GPU fundamentals coverage

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

- AC-005: The Work hierarchy subsection traces a concrete 2×3 by 3×2 matrix
  multiplication to four output elements, a 2×2-thread block in a one-block
  grid, one partial 32-lane warp and instruction issue on one SM. Show all four
  dot products, x-fastest thread numbering, four active and 28 unused lanes,
  and distinguish this chosen one-output-per-thread mapping from optimized
  tiled or cooperative matrix kernels. Place one complete accessible SVG
  beside the worked example inside that subsection. Its output panel labels x
  increasing across columns, y increasing down rows, and row-first indexing
  C[row, column] = C[y, x] for this chosen kernel mapping.

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
- AC-003: FP32 uses `rtol=1e-5, atol=1e-6`; FP16/BF16 uses `rtol=1e-2, atol=1e-2`; FP8 and quantized labs document recipe-specific tolerances. Lab 19 uses a per-reference magnitude-scaled absolute tolerance for its small BF16 gradients, with an exact-zero reference rule. Optimizations Lab 16 retains BF16 and checks both paths independently against an FP64 reference, adding an explicit bound for the composition's intermediate BF16 rounding. Its guide derives the bound, and corrupted outputs must fail before timing or publication.
- AC-004: Performance results use warm-up, repeated samples, controlled inputs, and at least three independent training/engine trials.
- AC-005: Optimize offline pytest feedback using repeated, comparable measurements while preserving test selection, assertions, isolation and the complete correctness gate. Keep the original numerical regression workloads where their shape or seed is material; use minimal deterministic fixtures for shape-independent rejection behavior. Excluded virtual environments must not add recursive publication-scan cost.

- AC-006: Training and inference capstone aggregation requires explicit uninstrumented provenance and rejects artifacts excluding acceptance timing. Lab 19 validates BF16 gradients relative to each reference bucket magnitude and reaches rank-wide correctness consensus before timing.

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

- AC-014: All lesson titles describe their central subject rather than listing technologies or implementation components. Syllabus, lesson links and generated navigation use the same title. How it works includes at least one accessible diagram that explains the lesson's core mechanism, with explicit arrow/containment/comparison meaning and adjacent prose. Mental model closes the teaching explanation and introduces no unexplained concept; optional References follow.

- AC-013: Each course ends with an optional self-study section without a displayed research-review date introducing current technologies and advanced concepts through concise plain-English descriptions, a concrete study question, official public references, and explicit hardware/maturity boundaries. Distinguish recent developments from established advanced ideas; researched documentation does not qualify dependencies or target execution. Preserve required lessons, labs and guided hours.

- AC-015: Canonical lesson fields use Objective, How it works, Practice, Mental model consistently in prose, rendered headings and authoring validation. Syllabi and supporting guides name and link each current practical owner; retired lab locations are not presented as runnable instructions.

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
  Takeaways and next step. Practice gives concise, self-contained commands and one deliberate comparison.
  Worked reasoning belongs in Concepts and code path. Lab 00 is the setup-only
  exception, with three ordered deployment, connection and sync steps.
- AC-002: Explanations are specific to the supplied implementation. Explain
  component responsibilities, data/state flow and synchronization for complex
  labs without a line-by-line code commentary.
- AC-003: One authoritative human-readable title preserves technology
  capitalization. The source filename's lab number, heading, TOC, source
  disclosure and lesson links agree. Each executable lab has explicit relevant lessons; setup Lab 00 precedes the
  lesson route and has no executable source association.
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
  serving as its only home. Guides explain needed concepts locally without Theory preparation reading
  lists or mandatory lesson/document detours; previews are distinguished from
  execution in the syllabus, and required labs do not depend
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

Learners need an attractive central course catalog, direct navigation between the nine courses, and clear Nebius attribution and licensing on a publicly browsable website.

#### Acceptance Criteria

- AC-001: A self-contained light editorial catalog at `courses/index.html` introduces nine courses and Lab Guide as ten numbered entries in REQ-001 order, each with a concise introduction and a working relative link. All ten catalog card number badges use the same mint background (#edf6f0) and teal text (#206757), without course-specific color overrides. Preserve source-derived course titles, hours, prerequisites and outcomes; the guide has no invented hours or course metadata. The courses README prominently links to the published catalog with the visible text Explore the courses and labels its relative `index.html` link Browse the courses catalog. Keep hosting-provider names out of the visible introduction.
- AC-002: The learning path starts with Soperator and the shared Lab Guide, then GPU Performance Tools and PyTorch for GPU Performance Engineering before Fundamentals and Optimization followed by three independent specializations and Advanced Labs. Every course and the guide has the same ordered ten-entry menu, a separate catalog backlink and exactly one current-page marker. Resolve destinations relative to each page; retain current-course identity and label the current guide appropriately. The tools reference prepares learners for the native commands used in labs; the three specialization prerequisites remain Fundamentals and Optimization.
- AC-003: The catalog and courses carry a readable small-print Nebius B.V. copyright, free educational resource statement and Apache-2.0 license link with the complete license embedded. Preserve third-party notices; do not impose noncommercial or resale restrictions.
- AC-004: The existing Python builder owns the generated catalog and course pages. The executable `courses/build-courses.sh` entry point rebuilds the shared guide, catalog, every registered course and each combined results ZIP, then checks HTML/ZIP source parity. Its help and status messages describe both output types. It works independently of the caller's directory, including checkout paths containing spaces, preserves builder failures, and provides help without requiring Python. Repeated runs with unchanged inputs produce identical HTML and ZIP content; file modification times may change. The wrapper separates checking from building with a cyan heading; current results are green and stale or missing output diagnostics and build/preflight failures are red on terminals. Check results retain stdout/stderr routing and stop at the first failed page. Redirected streams, `TERM=dumb` and any defined `NO_COLOR` use plain text. Rendering retains embedded reading resources, keyboard navigation, readable responsive layouts and standalone course validation.
- AC-005: A minimal repository welcome page links to the catalog and GitHub. GitHub Pages publishes all eligible repository files from `main` `/` with `.nojekyll`, HTTPS and no custom workflow file.
- AC-006: Preserve all nine courses' narrative, code listings, CSS, diagrams, teaching images, identifiers and reading behavior. Keep reading assets embedded; externalize only downloads. Each practical course has a Practical labs section with a Download results subsection. Separate labels from the links beneath them: Download all lab results: links through Grafana dashboards, Small and Large results to the sole external lab-results ZIP; Setup the lab environment: links through Lab setup guide to ../lab-guide.html. Do not generate or publish lab-kit ZIPs; original scripts, launchers, dependency files and displayed source remain intact and sync-labs.sh supplies cluster files. The download introduction replaces the redundant shared-setup/online-guide paragraph and introductory dashboard reminder; retain lab-specific dashboard teaching; prerequisite sections use one shared Lab Guide link. Soperator, GPU Performance Tools and PyTorch for GPU Performance Engineering have no lab downloads. Lab results contain grafana-dashboards/, `small/<lab>/` and `large/<lab>/`, with original manifest-owned bytes and no nested ZIPs. Preserve existing dashboard anchors and replace repetitive download/file lists with concise pointers.
- AC-007: Keep the stdlib Python CLI and static HTML architecture. Separate metadata, Markdown, asset validation, rendering and build orchestration into focused modules. One deterministic archive assembler serves the builder and evidence exporter. Validate the complete selected output set before replacing files; replacements are individually atomic. Check mode writes nothing and verifies both HTML and ZIP bytes.
- AC-008: Reject unsupported nested Markdown lists and single emphasis outside code. Every Markdown destination has an explicit rendered-link or source-scoped plain-text outcome; unknown destinations fail. Apply one passive SVG policy to all authored embedded SVGs.
- AC-009: Rename the six combined downloads to `<slug>-lab-results.zip` with identical contents and no aliases. Remove obsolete filename retirement logic; preserve canonical result and dashboard files. Verify all nine course contents and styles.
- AC-010: Normal build and read-only check preflight the complete repository-root publication inventory plus planned outputs before replacement, with exact caps of 104857600 bytes per file and 1000000000 bytes total. Report sizes, limits and headroom in decimal MB (1 MB = 1,000,000 bytes), with two decimal places and thousands separators; overflow diagnostics show excess amounts with six decimal places so one-byte violations remain visible. Keep exact integer-byte comparisons, fail on incomplete inventory or overflow, and never silently drop content. File, site and archive-limit failures use the same stream-specific red terminal diagnostics in build and check. Archive export enforces the same per-file cap under its existing transaction.
- AC-011: Every successful built/current HTML or ZIP line displays its exact serialized byte length in an aligned decimal-MB column before the path. After successful processing, print an aligned publication summary with listed-output and other-file subtotals, estimated site size, site limit, remaining capacity and per-file limit; omit largest-file reporting. The shell wrapper prints this summary exactly once at the end after verification, replacing its trailing success sentence. The Python builder supports --no-summary to suppress only that summary; preflight enforcement and failure diagnostics remain active. Selected-course subtotals cover only that run's planned outputs. Totals use exact bytes before independent display rounding, which may differ by 0.01 MB. Failed builds/checks emit no success summary.

#### Negative Criteria

- NC-001: No JavaScript framework, external runtime asset, new course prerequisite, arbitrary relative-link permission or license replacement is introduced.
- NC-002: Local checks do not establish live publication. Respect protected-branch review and verify the actual deployed revision and public routes.

#### Validation Method

Run generated-source parity, the standalone validators, metadata/navigation/license checks, desktop/mobile browser checks and authoritative GitHub Pages plus public HTTP verification.

#### Test Method

Exercise stale and missing catalog output, metadata changes, all navigation edges, current-course identity, disallowed links/resources and license-copy parity.

#### Evaluation Method

Navigate from the repository welcome page through the catalog and between all nine courses using pointer and keyboard. Assess small-screen layout and readable attribution independently of static checks.

<!-- /REQUIREMENT: REQ-013 -->

<!-- REQUIREMENT: REQ-014 status=active priority=P0 type=quality -->
### REQ-014: Authoritative technical terminology throughout the catalog

#### User Story

Learners need terminology that transfers directly from every lesson and lab to
NVIDIA documentation and the official documentation of the frameworks they use.

#### Acceptance Criteria

- AC-001: Review all lessons and all lab guides in the five courses against the
  relevant current NVIDIA references. Correct invented, ambiguous or inaccurate
  technical wording in context, including connected diagrams, glossaries and
  lab descriptions.
- AC-002: Use NVIDIA names and meanings for CUDA execution, memory, timing,
  architecture, profiling, communication and NVIDIA libraries. Preserve exact
  framework/API names and use their owning official documentation where NVIDIA
  does not define the concept.
- AC-003: Define technical terms before relying on them. Clearly identify
  descriptive measurement labels, analogies and synthetic models as such;
  ordinary explanatory prose must not imply an undocumented GPU mechanism.
- AC-004: Explain CPU timers, CUDA events and synchronization explicitly when
  describing measured operations. Distinguish host submission, device execution,
  data transfer and result completion, and distinguish memory residency from
  thread-block residency.
- AC-005: Preserve learning depth, existing lesson/lab identities, executable
  behavior and evidence limits. Rebuild all HTML from canonical sources and
  verify complete narrative/source parity and terminology consistency.

#### Negative Criteria

- NC-001: Do not perform blind global substitutions, copy vendor prose, invent
  acronym expansions, rename API/result identifiers merely to edit prose, or
  imply NVIDIA ownership of framework-specific terminology.
- NC-002: This review does not authorize package upgrades, live GPU jobs,
  infrastructure changes or external publication, and does not establish runtime
  performance or independent expert approval.

#### Validation Method

Read every lesson and guide semantically, compare technical definitions with
specific official sources, and inspect changed prose and diagram labels together.

#### Test Method

Run catalog and standalone validators, generated-source parity, existing
editorial/source checks and permitted visual inspection of changed artifacts.

#### Evaluation Method

A learner can identify the documented concept and explain the actual operations
performed and measured without learning an invented technical vocabulary.

<!-- /REQUIREMENT: REQ-014 -->

<!-- REQUIREMENT: REQ-015 status=satisfied priority=P1 type=feature -->
### REQ-015: Incremental local course sync to a Slurm login node

#### User Story

Learners with a local Git clone need one command to copy and refresh all course
labs and their supporting files, discover the login endpoint when needed, and
enter an interactive shell in the synced remote directory.

#### Acceptance Criteria

- AC-001: Run `sync-labs.sh` locally with an optional DNS hostname, IPv4/IPv6
  address or SSH alias. Bare targets select `root`; an explicit `user@target`
  selects that user. The selected account takes precedence over SSH configuration `User`.
  SSH owns name resolution and authentication; port and identity overrides are
  available. Preflight, transfer and interactive SSH use the same selected account.
- AC-002: Discover course directories by `reference/course.json` and `COURSE.md`,
  including reading-only packages without labs. Preserve their source folder names
  and full supporting source packages plus the catalog, shared Lab Guide, README
  and preparation helpers under remote `~/courses/`. A destination option selects another
  safe direct child of remote home. Invocation works from any working directory.
- AC-003: Transfer current tracked and non-ignored untracked working files,
  including uncommitted edits. Exclude generated environments and outputs using
  Git ignore rules. Size/time checks skip unchanged contents in one rsync
  transfer across all courses. Separately hash selected source files into a local
  verification manifest and verify destination content, executable status and safe
  link identity after each real transfer, before the interactive handoff.
- AC-004: Local source wins for matching files. Preserve remote-only files,
  including results and locally deleted source counterparts or entire course
  folders. Exclude locally removed courses even if Git still tracks their files.
  Only known unmodified old setup entrypoints may be retired after verified
  synchronization, under REQ-025. Dry-run does not
  modify the remote destination, including on first use.
- AC-005: Preserve runtime relative paths, executable permissions and timestamps.
  Validate inputs and dependencies, report failures nonzero, clean temporary
  state and print clear summaries and course-root navigation examples.
- AC-006: Repeating a completed sync with unchanged local and remote state
  transfers no file contents and preserves destination bytes, permissions and
  modification times. After a partial transfer, rerunning converges to the
  selected local sources while retaining remote-only files.
- AC-007: With no target, discover one login LoadBalancer Service using the current
  Kubernetes context, namespace `soperator` and label
  `app.kubernetes.io/component=login`. Require one distinct external ingress IP
  or hostname and one TCP Service port; an explicit `--port` overrides that port.
  Exclude internal/headless Services, fail on missing or ambiguous results and
  list candidates for an explicit-target rerun. Explicit targets never require
  or invoke Kubernetes. Bound API requests and retain Kubernetes configuration.
- AC-008: Every successful normal sync opens foreground interactive SSH in the
  selected destination, ensuring Python 3.12 and venv support through the existing
  bootstrap before entering the account's login shell. Earlier failures never
  open that shell; SSH exit status becomes the script status. Normal runs without
  a terminal fail before syncing. Dry runs remain noninteractive previews and
  never open a shell. Clean temporary transfer state before handing off terminal
  and signal ownership to SSH.

- AC-009: Sync produces no connection-receipt file and exposes no receipt option. Reject removed options before external access, without aliases or compatibility paths. Automation uses --sync-only and owns its connection settings and independent campaign synchronization proof. The script requires local Python 3 for its transfer manifest and remote Python 3 for post-transfer source verification; --sync-only skips interpreter preparation and the interactive shell. Preserve explicit/discovered SSH port precedence and native SSH configuration when no port override is selected.

#### Negative Criteria

- NC-001: Do not delete destination-only files outside REQ-025's narrow
  verified-entrypoint retirement, weaken SSH host verification, copy Git internals,
  install lab runtimes, provision infrastructure or submit Slurm jobs. Normal
  interactive handoff may ensure Python 3.12 and venv support; dry-run and
  sync-only perform no installation. Do not introduce a sync database or
  compatibility shims.
- NC-002: Local transfer tests do not establish live cluster or GPU readiness.

#### Validation Method

Check Bash syntax, ShellCheck, command help, source/package selection, paired
spec validity and README examples.

#### Test Method

Use disposable Git repositories, real rsync and a controlled SSH transport for
initial, unchanged, changed, excluded and interrupted transfers; compare settled
destination snapshots across repeats and verify metadata-only convergence, target
forms, quoting, source discovery, destination guards and dry-run preservation.
Use controlled kubectl output and a real kubectl client against a local fake API
for discovery, plus pseudo-terminals to verify interactive handoff, shell location,
exit status, cleanup and Ctrl+C ownership. Test nonterminal rejection separately.

#### Evaluation Method

A learner runs one local command and navigates to the corresponding remote
course root with every helper needed by the existing lab and Slurm commands.
Check discovery, terminal handoff and shell location using isolated transports;
keep actual cluster access separate from local acceptance.

<!-- /REQUIREMENT: REQ-015 -->

<!-- REQUIREMENT: REQ-016 status=active priority=P1 type=quality -->
### REQ-016: Self-contained lab flow and concise environment setup

#### User Story

Learners need to stay in one lab while building the knowledge needed to run it,
and prepare the shared environment once before starting practical work.

#### Acceptance Criteria

- AC-001: All lab guides retain a concise purpose immediately after the title.
  Remove Theory preparation labels and reading lists; retain useful definitions
  and scope qualifications in the opening or local concept explanation.
- AC-002: Practice contains short task instructions and supported commands,
  without lesson/README detours or repeated environment setup. Preserve worked
  calculations, interpretation, checks and extensions in their relevant sections.
- AC-003: The shared courses README owns environment setup for all six practical
  courses, linked from their pages without a numbered setup lab. It covers two Soperator worker nodes with one GPU each, the
  nebius-cxcli create/render/deploy sequence, external Kubernetes credentials,
  and running ./sync-labs.sh locally from the cloned courses directory. The advanced
  fabric route requires two eight-H100 workers with InfiniBand. Base labs may
  reuse that cluster through one-GPU Slurm allocations; cluster telemetry still
  verifies all sixteen devices, independently of the one-GPU workload boundary.
- AC-004: Setup describes Soperator accurately, preserves the supplied public
  references and explains placeholders and sync/SSH prerequisites concisely.
  It contains bounded tool/readiness checks, but no optimization experiment or theory lesson.
- AC-005: Existing platform and compiler verification remains available in
  dedicated numbered verification labs, with source, identity, syllabus,
  metadata, launchers and tests aligned. Setup renders as prose without an
  unrelated executable source. Full source/HTML parity remains enforced.

#### Negative Criteria

- NC-001: Do not delete distinct useful teaching or weaken correctness checks
  to shorten the reading path. Live installation and verification follow only the explicitly designated target and authorized actions.

#### Validation Method

Review all revised guides, exact setup commands against official sources and
local implementations, then inspect rendered lab flow and source parity.

#### Test Method

Run guide/metadata, setup presence/order/parity, renamed source identity and
course-sequence checks; rebuild all textbooks and run catalog validators.

#### Evaluation Method

A learner can read the purpose, understand the local explanation and run the
lab without following a mandatory reading detour; basic setup ends with a tiny GPU execution check; deeper tool and dashboard qualification precedes use of those capabilities.

<!-- /REQUIREMENT: REQ-016 -->

<!-- REQUIREMENT: REQ-017 status=active priority=P1 type=feature -->
### REQ-017: GPU profiling and per-lab Grafana evidence

#### User Story

Learners need an executable, evidence-led GPU optimization workflow across all six practical courses in the nine-course catalog.

#### Acceptance Criteria

- AC-001: All executable labs have a versioned dashboard, supported local capture and comparison recipes, guided one-variable tuning and independent investigation, with explicit CPU/model/qualification exceptions.
- AC-002: The shared README contains cluster creation, connection/copy, cxcli-managed Nsight installation, cxcli-managed Grafana and Pushgateway installation and unnumbered environment readiness checks. Explain the measurement-to-dashboard flow with the existing inline diagram, distinguishing immutable benchmark results from sampled telemetry. The GPU Performance Tools reference precedes experiments without renumbering practical lessons or labs.
- AC-003: Publish selected baseline/candidate numeric summaries after timing through one serialized PUT per fixed workspace/course/lab/profile; validate equivalent workloads and exact-generation readback, with bounded labels and separate publication failures.
- AC-004: Initial private monitoring setup uses `nebius-cxcli grafana install --config ./config.yaml --target CLUSTER_TARGET --pushgateway`. Course setup only discovers and verifies the installed services and writes private connection files; it never creates a catalog or installs infrastructure. Shared setup imports every selected course dashboard directory using the current immediate cxcli import command with explicit config, target, existing course-folder UID and an explicitly selected datasource in the interactive import wizard; automation supplies its prepared datasource UID. Inspect the rendered dashboard separately. Sampled telemetry does not replace benchmark timers or establish exclusive job attribution.
- AC-005: Qualify the actual installed Nsight versions, shared worker/container availability, private access and both workers through executed canaries and matching report inspection. All executable labs and applicable documented variants require independent live workload, profiler-content and Grafana verification before an all-lab qualification claim; static checks, an installed binary and a nonempty report are insufficient.
- AC-006: Audit all 110 executable labs for an applicable Nsight Systems recipe reaching the actual CUDA worker, distributed rank or GPU server. Record explicit reasons for CPU-model, artifact-inspection, protocol-only and RDMA wire-timing exceptions; CUDA correctness labs and optional CUDA exercises must not be excluded merely by their teaching category. Bind each lab to its own Grafana JSON, mapped measurements and relevant telemetry, and validate guide/capture/dashboard parity.

- AC-007: Lab 19 provides an unnumbered single-H100 Compute companion for one selected local backward kernel, with exact companion admission and diagnostic-only artifacts. Its distributed workload retains Systems capture; the advanced course keeps 34 numbered labs.

- AC-010: Discover exactly one accepted cxcli-owned private Grafana and Pushgateway on the explicit target. Resolve the installed local metrics datasource and native VMAgent scrape job from current rendered and live state. Reject ambiguous, foreign, stale or unready owners and unavailable local metrics before writing connection files. No course-owned infrastructure, legacy installer flags or receipt compatibility path is retained.
- AC-011: In GPU Performance Tools, the reference lessons give Nsight Systems, Nsight Compute, NVTX and Grafana a worked example with an original contextual diagram or permitted frontend image. Define NVTX markers and ranges with a concise inline PyTorch example, show timeline interpretation and range-based kernel selection, and explain annotation versus synchronization and timing. Label synthetic values and preserve separate diagnostic and unprofiled evidence. The concluding mental model connects the tools into one investigation, from an unprofiled baseline through NVTX-labeled diagnosis to checking a measured change. The first four tool diagrams use landscape layouts with left-to-right timelines or reading order, readable labels and matching visible explanations and accessible descriptions.

#### Negative Criteria

- NC-001: Do not claim GPU performance from simulations, profile overhead, stale data or unsupported attribution. Preserve unrelated changes and existing cluster ownership.

#### Validation Method

Inspect source, recipes, dashboards, setup and supported command flow against the approved design.

#### Test Method

Run focused contract, failure-path, publisher, dashboard, renderer and integration tests.

#### Evaluation Method

Separate offline source checks from installed-tool, browser and representative two-H100 live evidence. Record unavailable live checks explicitly.

<!-- /REQUIREMENT: REQ-017 -->

<!-- REQUIREMENT: REQ-018 status=active priority=P1 type=feature -->
### REQ-018: Simple setup and a separate GPU-fabric learning route

#### User Story

Learners need clear setup commands, organized outputs, and distributed experiments
whose hardware can exercise the intended GPU and network communication paths.

#### Acceptance Criteria

- AC-001: Use CLUSTER_CONFIG and CLUSTER_TARGET consistently, define COURSE_TOOLS
  before use, and install Grafana and Pushgateway with the single cxcli Grafana
  install command. Discover private connections using an explicit kubeconfig and
  context; preserve the existing database and one native results scrape. Laptop
  and worker environment files contain only their required connection settings.
- AC-002: Course submissions create private `results/<lab>/logs` before sbatch,
  bind an absolute working directory and separate job output/error paths, and
  preserve job IDs and workload arguments. Do not move unrelated existing logs.
- AC-003: The base route requires one full H100 per local workload and may use
  two one-H100 workers or reuse the two eight-H100 fabric workers with explicit
  one-GPU Slurm allocations. Its TCP/IP results do not qualify GPU-fabric tuning.
  Move all existing real distributed labs into the seventh advanced course (REQ-020),
  preserving identities, implementations, diagrams, guides and runtime assets.
- AC-004: The advanced route requires a Soperator cluster
  with two eight-H100 workers, intra-node NVLink/NVSwitch and an active shared
  InfiniBand fabric. Verify actual topology and transport before measurement;
  retain bounded two-rank mechanics where generalization changes the exercise.
- AC-005: Supply executable advanced GPU topology, peer bandwidth, NCCL collective,
  InfiniBand/GPUDirect RDMA, distributed PyTorch/Nsight profiling, training and
  inference investigations with measured artifacts, correctness evidence, one
  tuning control, dashboards and independent follow-up questions.
- AC-007: The same lab code admits observed full SM90 H100 and H200
  devices for functional single-GPU and fabric tests while retaining allocation,
  MIG, topology and correctness checks. Results identify the observed GPU family;
  H200 measurements never qualify H100-specific performance claims. Course teaching text remains centered on H100; unsupported devices and mixed-family
  fabric allocations fail, and no hardware-selection flag is required.
- AC-006: Align lesson groups, exact titles, TOCs, syllabuses, metadata, source
  kits, setup, references, tests and generated HTML across the affected courses.
  Keep CPU arithmetic/simulation work in its appropriate base lesson.

#### Negative Criteria

- NC-001: Do not infer NVLink across nodes, GPUDirect RDMA from NET/IB alone,
  hardware readiness from a preset name, or a speedup from changed workloads.
- NC-002: No live provisioning, driver/fabric changes, service exposure or cost
  is authorized by course authoring. Preserve unrelated working-tree changes.

#### Validation Method

Audit preservation and hardware/lesson ownership; verify current upstream tool
interfaces and local cxcli catalog resolution before changing examples.

#### Test Method

Test submission argv/privacy before Slurm launch, environment selection, hardware
rejection, rank placement, correctness and result mappings with local fixtures.
Rebuild and validate all source kits, dashboards and browser navigation.

#### Evaluation Method

Keep source/CPU/browser evidence separate from actual two-by-eight-H100 runs.
Require readable rank traces, qualified GPU-memory registration, interpreted
comparisons and matched useful work before claiming fabric optimization.

<!-- /REQUIREMENT: REQ-018 -->

<!-- REQUIREMENT: REQ-019 status=satisfied priority=P1 type=feature -->
### REQ-019: Concise text-only Soperator course

#### User Story

Engineers using the GPU courses need to understand Slurm, its Soperator deployment
on Kubernetes, and the client commands used to request and inspect work.

#### Acceptance Criteria

- AC-001: Provide the first standalone course named exactly Soperator: A Nebius Slurm
  cluster running on Kubernetes, with slug soperator, six concise lessons and
  no executable labs, setup guide, diagrams, runtime kit or dashboards.
- AC-002: Define Slurm using SchedMD sources, then explain login, controller and
  worker roles before Soperator's SlurmCluster, SlurmNodes services and worker
  NodeSets. Distinguish Kubernetes pod placement from Slurm job scheduling. Explain
  that login, controller and worker Pods mount the shared jail, while login
  sessions and worker jobs use it as their user environment and the controller
  accesses shared user information and Slurm configuration.
- AC-003: Most teaching covers practical client use: allocations, steps, tasks,
  resource requests, files and logs, sbatch/salloc/srun, GPU visibility,
  distributed launcher arithmetic, queue inspection, accounting and cancellation.
  Re-author reference concepts using public official sources and synthetic examples.
- AC-004: Lessons use Objective, How it works, Practice, Mental model,
  followed only by optional References. Practice
  is an in-text comprehension check with feedback, not an executable assignment.
  Definitions precede use; worked examples explain results and limitations.
- AC-005: Canonical metadata, syllabus, TOC, glossary, references, catalog,
  switchers, synchronization, README and generated HTML agree. Text-only
  validation preserves the five GPU courses' stronger runtime/diagram contracts.
- AC-006: Keep the Course overview brief and specific to Soperator: state the
  learning outcome, entry knowledge, reading route and text-only scope once.
  Retain authorized-use limits; keep detailed readiness checks and version
  context in their existing owning sections instead of repeating them here.

#### Negative Criteria

- NC-001: No private source details, copied confidential prose, credentials,
  real deployment identifiers, live execution or implied runtime qualification.
- NC-002: No duplication of cluster installation or GPU optimization labs, and
  no forced GPU prerequisites for reading the Slurm introduction.

#### Validation Method

Verify command semantics and component boundaries against public SchedMD,
versioned Soperator source and PyTorch documentation; review all lesson prose.

#### Test Method

Exercise separate text metadata validation, full prose parity, links, stale
builds, six-course routing and synchronization without a labs directory. Run
focused GPU regressions and isolated desktop/mobile browser checks.

#### Evaluation Method

A reader can choose a launch command, calculate process/resource counts, explain
where execution occurs, and distinguish pending, failed and completed work from
text examples alone. Live commands are examples, not claimed cluster results.

<!-- /REQUIREMENT: REQ-019 -->

<!-- REQUIREMENT: REQ-020 status=active priority=P1 type=feature -->
### REQ-020: Advanced GPU communication laboratory course

#### User Story

Engineers need one complete practical course for diagnosing and tuning GPU
communication, large-training throughput and latency-sensitive distributed
inference on a two-node, sixteen-H100 Soperator cluster.

#### Acceptance Criteria

- AC-001: Add the seventh course named exactly Advanced Labs: Multi-GPUs Multi-Nodes communication optimization, with slug advanced-gpu-communication. It links to the shared setup guide and contains practical labs, without separate conceptual lessons; definitions and worked explanations live beside experiments.
- AC-002: Move all 25 existing advanced activities (18 distributed labs and seven fabric/training/inference labs) with their complete code, guides, evidence recipes, dashboards and runtime support. Preserve distinct capabilities and bounded two-rank mechanics. Remove obsolete practical associations from earlier courses; retain useful conceptual teaching and route advanced practice to its sole owner.
- AC-003: Require two workers with eight H100 GPUs each, verified intra-node NVLink/NVSwitch and inter-node InfiniBand. Setup prepares shared tools once, retains private monitoring and per-lab logs, and separates user-space qualification from administrator-owned fabric configuration.
- AC-004: Add purposeful executable investigations for small-message collective latency, RDMA latency, NIC/rail selection, NIXL transfers, Megatron communication overlap and hierarchical context parallelism, Dynamo prefill/decode separation and cache-aware routing, and inference goodput. Use current official interfaces with candidate versus installed versus qualified versions distinguished.
- AC-005: Each activity provides complete local prerequisites, actual baseline/candidate commands, one-variable tuning, correctness/validity checks, interpretable timing/throughput/latency evidence, relevant Nsight/PyTorch annotations or explicit inapplicability, a course-owned Grafana dashboard and independent follow-up. Preserve equal useful work and distinguish application latency, collective latency, link bandwidth and sampled telemetry.
- AC-006: Integrate seven-course catalog/navigation, source kits, synchronization, metadata, titles, TOCs, references and validators. A dedicated labs-only profile enforces complete guides and source parity without weakening conceptual or text-only course contracts.
- AC-007: Dynamo installation builds its pinned NIXL and NIXL-EP components
  together against an explicit owner-qualified UCX prefix for that runtime.
  Reject a missing EP component or bundled duplicate UCX libraries before
  installation. Keep the separate NIXLBench environment isolated, and require
  allocated-worker construction, collective and full-serving checks before
  claiming native compatibility.
- AC-008: Lab 34 compares concurrency on the same two-worker deployment using
  128 identical transmitted requests and fixed, server-verified generated-token
  work. Exact response text is diagnostic, not a performance prerequisite.
  Reject failed/incomplete requests, missing server usage, unequal inputs or
  token work, and artifacts predating this contract. Preserve the separate
  output-equivalence contracts of Labs 32 and 33.

#### Negative Criteria

- NC-001: No duplicate lab implementations, cross-course runtime imports, compatibility redirects, invented measurements, guaranteed speedups or implied production-scale qualification.
- NC-002: Authoring does not provision hardware, install dependencies, expose services, change fabric/driver policy, run paid targets or publish. H100-incompatible Blackwell features are not presented as runnable H100 optimizations.

#### Validation Method

Audit every moved capability and new vendor interface; review teaching,
comparison validity, topology evidence and public-safe artifacts.

#### Test Method

Use CPU fixtures for launch/resource contracts, parser/output/error paths,
comparison equivalence, ownership and dashboard/source parity. Build all courses
and check desktop/mobile navigation in an isolated browser.

#### Evaluation Method

Require learners to defend a throughput or latency tradeoff using matched
workloads, correctness evidence and traces. Report static/browser checks
separately from installed runtimes and actual sixteen-H100 measurements.

<!-- /REQUIREMENT: REQ-020 -->

<!-- REQUIREMENT: REQ-021 status=active priority=P1 type=feature -->
### REQ-021: Repeatable course campaigns and student evidence

#### User Story

Course maintainers need one reusable run-labs skill to execute selected labs on a prepared cluster and provide students with convenient comparable results.

#### Acceptance Criteria

- AC-001: Select one or multiple labs, courses, or all six practical courses; exclude setup and duplicate external links. Both small and large workloads are default. Replace old workload names throughout active interfaces with no aliases; retain actual hardware names.
- AC-002: Freeze reviewed recipes, dependencies, parameters, source and target identities. Preserve specialized launchers, repeated trials, correctness gates and declared profiler applicability. Never execute extracted lesson prose.
- AC-003: Execute, verify, collect original raw artifacts and capture applicable real Grafana, Nsight Systems and Compute views with headless Playwright. Verify native report contents independently; screenshots alone are insufficient. A KV-routing replica with zero assigned requests requires complete zero-assignment/completion proof and eight worker profiler starts, recorded stop events and completed capture acknowledgements; its active peer retains normal inference and browser evidence.
- AC-004: Export sanitized PNG, CSV and JSON under each course reference/lab-results/lab/profile, with actual GPU and workload provenance, stable filenames and no explanatory student reports. Offer one external per-course resources ZIP containing Grafana dashboards and both small and large result profiles, without nested archives. The course page offers this single download and a shared setup-guide link; deliver runtime source files through sync-labs.sh, without a lab-kit ZIP.
- AC-005: A fresh successful run replaces only owned private and public results for its lab/profile, without accumulating duplicates. Stage and journal replacement; failed runs preserve the previous complete set. Resume does not resubmit completed jobs; ambiguous submission must reconcile before retry.
- AC-006: Reuse prepared services and stable dashboard identities, import updates with overwrite, scope cancellation and cleanup to owned jobs and files. The campaign request authorizes updates to its identified course dashboards after UID, folder and task-scope checks; provisioned or foreign-owner metadata alone must not block execution or require repeated approval. Preserve actual CLI permission, ownership and concurrency checks and unrelated dashboards. Do not install Lab00 or automatically change course source or acceptance.
- AC-007: Document Codex installation from the courses directory with `npx --yes skills add ./skills/run-labs -a codex --yes`, without global scope, so the skill is available in courses and its subdirectories. Skip installation prompts and the optional find-skills offer without installing that extra skill.
- AC-008: Reuse a source-owned evidence runner and isolated in-memory browser sessions across a campaign. Keep family-specific independent verification, exact publication identity, complete numeric coverage, explicit visual review and existing export transactions. Compact actual Grafana captures must bind only fully visible values to each image; paginate when needed. Runner upgrades do not invalidate unchanged frozen course inputs and recipes. No GPU/evidence pipelining is part of the runner change; download packaging follows the separate REQ-013 authoring contract.
- AC-009: Admit prepared monitoring from independent current runtime observations bound to the explicit target and existing connection provenance. Verify private services, exactly one healthy results scrape with label preservation, complete fresh GPU telemetry, the intended local metrics write/read path and later exact publication readback. Setup discovery and new connection receipts retain REQ-017's current owner contract; a resumed prepared campaign neither requires setup rerun solely because installation metadata differs nor converts or accepts obsolete setup receipt schemas. Preserve an explicit local-only routing requirement and report source/setup/runtime evidence separately.

- AC-010: CUDA capstone execution retains the guide-required separate memcheck before three fresh trials. Independently verify zero sanitizer errors, all child records and aggregate math; summaries bind the actual consistent supported GPU identity instead of hardcoding H100.

- AC-011: Fundamentals Lab 03 native acceptance verifies all ordered host-copy modes, exact effective payload sizes, correlated submissions/completions, synchronization and final readback, including valid reports without CUDA kernels. Require actual copy views for both configurations; incidental kernels do not bypass this proof.

- AC-012: A lab run or resume authorizes repeated necessary recovery of its identified Grafana service, Nsight Systems/Compute native tools and streamer/viewer services, nsys/ncu profiling runtimes, browser sessions and task-owned SSH/loopback forwards, without a fixed attempt count or repeated approval within existing target authority. Restore the exact three service connections, retaining Nsight HTTP/TURN mappings, and verify visible responses to browser navigation. If connection recovery is insufficient, restart the affected authorized deployment or service while preserving configuration, credentials and persistent storage. Protect unrelated sessions and services; remote mutations on production or unconfirmed targets still require exact live-action authority. Restore exited local forwards, including prior-session forwards, under existing access authorization without a new service-restart approval; verify exact target, local port ownership, durable process lifetime and readiness. Bound and inspect each recovery, retain failed evidence and revalidate original report and dashboard contents. Recover nsys/ncu through their existing installed runtime and exact owned sessions/jobs, without changing acceptance or bypassing permissions. A terminal diagnostic-tool failure requires preserved failure history, terminal/quiescent prior jobs and normal claim release, then a fresh campaign scoped to the affected lab/profile using its complete unchanged recipe; it must not rewrite a failed stage, replay an uncertain dispatch or rerun unaffected completed units.

- AC-013: Observational job queries retry SSH transport status 255 and timeouts for at most three total attempts, retaining the 60-second per-attempt timeout. Mutating operations and reconciliation remain single-attempt; other exit statuses, malformed responses and application errors fail immediately.
- AC-014: BOOT_FAIL and DEADLINE are terminal job failures: persist failed stage/unit evidence, continue other eligible units, finalize the campaign as failed and release its claims when work ends. Cancellation does not send scancel for those already-terminal states. Keep the transmitted remote helper self-contained and source/installed skill payloads aligned.

#### Negative Criteria

- NC-001: Dry-run catalog coverage, file presence, API success, invented screenshots or diagnostic timings cannot establish live acceptance.
- NC-002: Public artifacts contain no credentials, private connection details or raw infrastructure logs. Unrelated files and prior unmanaged archives remain untouched.

#### Validation Method

Validate all selector combinations and catalog coverage, canonical workload interfaces, source parity, evidence schemas, browser receipts and replacement ownership.

#### Test Method

Exercise invalid selection, source drift, submission ambiguity, failed and interrupted replacement, repeated success, resume and cancellation, malformed evidence and sanitized exports. Run existing course checks and fresh representative cluster trials.

#### Evaluation Method

A maintainer can rerun a selected lab and students find exactly one current evidence set per workload; reported completion distinguishes source checks from observed live evidence.

<!-- /REQUIREMENT: REQ-021 -->

<!-- REQUIREMENT: REQ-022 status=active priority=P1 type=feature -->
### REQ-022: One shared setup and lab execution guide

#### User Story

Learners prepare the environment once and then connect, run, profile and inspect labs through one concise entry point.

#### Acceptance Criteria

- AC-001: courses/README.md has four main sections in order: How to set up the lab, Lab Preparation Scripts, How to run the labs, and Browsing Grafana and Nsight Profilers. Explain each automated preparation group and shared prerequisites in the preparation topic; keep detailed experiment-specific hardware and vendor qualification in the owning course or lab, with direct links from the shared guide. The sidebar includes ordered top-level and subsection links. Useful maintainer material lives separately. The README and generated Lab Guide introduction omit the maintainer-guide link, lab-kit/build commentary and the paragraph beginning The seven courses share one reading format; retain the course route, results ZIP explanation and source-sync instruction. Place Read this guide online immediately under How to set up the lab, linking to the published lab-guide.html and explaining that it contains the same instructions as the README. The README ends with the Nebius B.V. free educational material and Apache License 2.0 attribution; nothing follows it.
- AC-002: Generate lab-guide.html from the README with the visible title Lab Guide, link it as the second catalog/menu entry, share the eight-entry navigation with every course, and include README.md and lab-guide.html in course synchronization. Keep README.md as the single instructional source. Omit its browser-edition pointer and closing attribution from the generated article body; use the standard HTML footer once with complete license and third-party notices. Remove all six Lab 00 documents, setup_guide metadata and special rendering; preserve executable lab identities and self-contained teaching.
- AC-003: Show create/render/deploy followed by cxcli soperator profiling install before course monitoring changes and dashboard imports. Explain first-time interactive credentials. Retain private monitoring, explicit target, interactive datasource selection and per-course folder selection; import directories during setup instead of each experiment.
- AC-004: Use environment_readiness consistently for the unnumbered checks and dashboard. Preserve independent-worker/GPU checks without a legacy alias or automatic migration of historical results.
- AC-005: Use cxcli-managed profiling in worker and container environments, preserving private originals and exporting only selected reports for browser viewing. Keep per-course dependencies, CUDA builds and advanced fabric/vendor qualification.
- AC-006: Runtime preparation starts with the existing workstation sync-labs.sh handoff. Explain that it synchronizes source and opens SSH, while dependency installation and private monitoring-environment transfer remain separate. The run section covers course selection, submission, logs/results, separate diagnostic capture and publication. Experiment-specific reasoning remains local.
- AC-007: The browsing section gives `nebius-cxcli grafana show --config "$CLUSTER_CONFIG" --target "$CLUSTER_TARGET"` and `nebius-cxcli soperator profiling show "$CLUSTER_CONFIG" --target "$CLUSTER_TARGET"`. Learners run the printed explicit-target, loopback forwarding commands in separate terminals, retrieve passwords using the printed commands and open the printed URLs. Preserve both Nsight HTTP/TURN mappings, the installer-selected Nsight username (admin by default), Grafana's printed username and private report originals. Initial installation remains separate from routine access; do not maintain duplicate service, Secret, port or URL recipes in the guide.

- AC-008: Teach a command-first learner path with explicit Python 3.12, current-context verification and no-argument sync-labs.sh discovery. Use portable placeholders and explain each command's effect. Basic readiness uses an explicitly allocated GPU and the existing compatibility-stack lab; it does not claim profiling, container, distributed or monitoring qualification. Retain full qualification before the relevant evidence claims and preserve automated run-labs contracts.
- AC-009: Learners submit jobs with native sbatch and inspect the exact job's logs and printed result paths. The no-argument regular-lab-setup.py command and selected specialized preparation commands create and validate private result, log and profile directories before submissions; standalone copies discover their course root automatically. Reject symlinked, foreign-owned or non-private existing directories without changing them. Remove repeated directory-preparation blocks and learner-facing umask commands from lab instructions. Preserve working directory, job identity, workload arguments and per-lab output/error paths. Use native sbatch dispatch for automation and retain inspection utilities; REQ-024 replaces generic execution wrappers.
- AC-010: Each lab's Before you start contains one shared Lab Guide link and no assigned-dashboard link; retain distinct prerequisites and dashboard interpretation. Concisely introduce every supplied Python or CUDA lab program by its actual purpose, actions, correctness checks and output immediately before its canonical Practice command. Explain the retained monitoring discovery, publication, capture and qualification helpers where used. Move detailed campaign instructions to the existing run-labs documentation.
- AC-011: Explain that driver capability, framework CUDA build and compiler toolkit versions need compatibility, not equality. Never recommend generic CUDA/driver upgrades in the Soperator learner flow. Make supported Ubuntu Python and Apptainer installation conditional administrator preparation; actual worker/container checks remain necessary. Keep fresh-terminal variables explicit, credential reuse accurate, full-project Grafana deployment effects visible and obsolete lab-kit instructions absent.
- AC-012: Every executable lab guide has exactly one baseline sbatch invocation in Practice, with capture explicitly disabled. CPU-only labs use a CPU launcher without GPU requests. Preserve distinct comparisons and required builds outside that launch block. Lesson Practice remains links and teaching context.
- AC-013: Teach unprofiled execution, Systems investigation, selected Compute analysis where applicable, and unprofiled verification. Learner diagnostics use visible native srun, nsys, ncu and report-inspection commands. Coordinated multi-node services retain native sbatch allocation with explicit profiler argv passed to each worker. Distributed and server recipes reach actual ranks/servers with private distinct reports, bounded execution and cleanup. Keep diagnostic labeling, capture applicability and maintainer automation; do not hide learner profiling inside Python wrappers.
- AC-014: The internal directory-preparation function is standard-library-only and idempotent, performs no infrastructure or dependency installation itself, and is available in synchronized course trees. Monitoring discovery remains the separate explicit regular-lab-setup.py monitoring action with its existing authority checks.

#### Negative Criteria

- NC-001: No cloud execution, public deployment, credential disclosure or unrelated dirty-work changes are part of authoring.
- NC-002: No repeated Lab 00, manual profiler-installation alternative, legacy identity alias or weakened experiment/readiness acceptance checks.

#### Validation Method

Review command order and course-specific prerequisites against executable contracts; inspect shared and course HTML navigation at desktop/mobile widths.

#### Test Method

Exercise shared-guide parity, metadata and kit contracts, sync manifest, readiness identity and duplicate-worker rejection, container profiler discovery, profiling/monitoring regressions and all course validators.

#### Evaluation Method

A learner follows one setup guide, reaches a numbered lab, submits a clean run and a separate diagnostic, and can locate its evidence. Static verification does not establish live GPU qualification.

<!-- /REQUIREMENT: REQ-022 -->

<!-- REQUIREMENT: REQ-023 status=superseded priority=P1 type=quality -->
### REQ-023: Consistent course presentation without content loss

#### User Story

Superseded by REQ-002 AC-014 through AC-017, which consolidate this presentation
contract with the existing authoring and preservation requirements. Retained
here to preserve its stable identity and original intent.

Learners need the same recognizable typography and section patterns across all
seven courses while retaining each subject's explanations and practical context.

#### Acceptance Criteria

- AC-001: Course titles, topic headings, body text and common sections use the
  shared stylesheet and semantic hierarchy across all course profiles.
- AC-002: Official references are numbered; Where to Go Next uses bullets for
  distinct options at course and lesson level. Each conceptual lesson places
  Where to Go Next after Mental model and before its independent Glossary.
- AC-003: Remove learner-facing Syllabus and Course mission sections and TOC
  entries. Preserve authoring sources and relocate unique audience,
  prerequisites, readiness, outcomes and safety context into the orientation
  or owning teaching. Do not discard distinct explanations, labs or diagrams.
- AC-004: Course Glossary is its own top-level section. Preserve local glossary
  definitions and alphabetical ordering. Keep references last and retain the
  intentional text-only and labs-only course profiles.

#### Negative Criteria

- NC-001: No lab implementation, infrastructure, installed skill, publication,
  dependency installation or unrelated dirty-work change is required.
- NC-002: Do not infer live lab or educational outcome proof from format checks.

#### Validation Method

Inspect every course's canonical and rendered presentation and compare retained
teaching with the working-byte baseline. Verify shared typography in the browser.

#### Test Method

Run focused format regressions, all course validators, source/helper and HTML
parity checks, and owned isolated desktop/mobile browser assertions.

#### Evaluation Method

Review complete rendered headings, references, next steps and representative
layouts at desktop, 390px and 320px; report source, browser and runtime separately.

<!-- /REQUIREMENT: REQ-023 -->

<!-- REQUIREMENT: REQ-024 status=satisfied priority=P1 type=feature -->
### REQ-024: Native lab execution, workload naming and shared profiling reference

#### User Story

Students can read the Slurm and NVIDIA commands they run, select workload size explicitly, and learn profiling tools once without losing existing results or automated evidence collection.

#### Acceptance Criteria

- AC-001: All 110 labs use explicit per-lab native batch jobs for normal execution and supported profiler modes. Student and campaign paths share these jobs; remove generic Python execution/submission wrappers after migrating all consumers. Preserve distributed rank, server lifecycle, CUDA postprocessing, watchdog, cleanup and diagnostic evidence guarantees.
- AC-002: Workload CLI is --workload small|large, with unchanged lab defaults; run-labs accepts small|large|both and defaults to both. COURSE_WORKLOAD is the workload environment variable. No old flag/environment aliases. Preserve existing serialized evidence schemas and actual profiling terminology.
- AC-003: New output belongs to results/<lab>/jobs/<job-id> with results, profiles, logs and artifacts children; scheduler logs remain results/<lab>/logs/<job-id>.out/.err. Create private scheduler directories before submission, reject unsafe paths/restarts/collisions, disable requeue, and collect only exact dispatched jobs.
- AC-004: Preserve all original results, reports, screenshots, receipts, archives and published paths byte-for-byte. Historical organization makes verified copies only, with attribution/checksum manifests and collision detection. Uncertain attribution stays unresolved; history never supplies fresh evidence.
- AC-005: Place GPU Performance Tools before GPU Fundamentals as preparation for lab-bearing courses: five concise reference lessons covering evidence selection/Slurm, Systems/NVTX, Compute, PyTorch profiler, and Grafana/VictoriaMetrics. No labs or exercises. Move existing primers and contextual diagrams, explain every demonstrated flag centrally, and retain lab-specific commands locally. Grafana queries its datasource; exporters/publishers and VMAgent collect/forward metrics.
- AC-006: Align canonical skills/run-labs source, references, recipes, tests and installed payload. Preserve scheduling, dependency, durable dispatch/reconciliation, owned cancellation, recovery, result identity/cardinality and evidence integrity. Reject incompatible saved executable plans without rewriting historical evidence.
- AC-007: Lab 08 identifies embedded PyTorch versus external diagnostics and post-warmup measured NVTX ranges; no profiled timing is presented as clean performance evidence. Static and live qualification remain distinct.

#### Negative Criteria

- NC-001: No compatibility wrappers, historical file rewrites, automatic result replacement, live infrastructure changes or publication.

#### Validation Method

Inspect native jobs, shared tool teaching, canonical/installed skill parity, exact-job collection and preservation hashes.

#### Test Method

Exercise workload parsing, native commands with scheduler/profiler doubles, timeout/report failures, distributed/server boundaries, reading-profile exclusions and copy-only historical organization; run course validators and generated parity.

#### Evaluation Method

Students can explain each process and locate its evidence; automated campaigns execute the same reviewed jobs without hidden profiler dispatch.

<!-- /REQUIREMENT: REQ-024 -->

<!-- REQUIREMENT: REQ-025 status=satisfied priority=P1 type=feature -->
### REQ-025: Five idempotent lab preparation commands

#### User Story

After source synchronization and SSH, a learner runs python3.12 "$HOME/courses/tools/regular-lab-setup.py" without arguments for regular labs and explicitly prepares expensive specialized labs when needed.

#### Acceptance Criteria

- AC-001: Python 3.12 scripts discover the synchronized catalog or their standalone course. Normal sync/SSH ensures Python 3.12; dry-run and sync-only do not install dependencies.
- AC-002: Partition all 110 default labs into regular (79), CUDA (13), communication (10), serving (7), and Transformer Engine (1). Each specialized script supports --lab, --launcher, --all and read-only --plan; no arguments list supported labs without installation. --all excludes optional variants. Regular retains explicit monitoring and organize-history actions through an importable internal module.
- AC-003: Select dependency closure before system prerequisites. Regular installs ordinary isolated Python environments, publication tools and the shared pinned Qwen2.5-0.5B snapshot, without container images, CUDA toolkit or specialized builds. Invoke Apptainer and jail namespace preparation only for selected container work.
- AC-004: CUDA uses managed native CUDA 13.3.0 and compiles selected labs without replacing cluster drivers or system CUDA. Serving uses isolated native vLLM 0.28.0 with vendor-matched dependencies and native AIPerf 0.12.0. Communication selects only required fabric, NCCL, NIXL, Bridge or Dynamo components; Transformer Engine has an isolated runtime and native dependencies. Keep TensorRT-LLM, Dynamo container preflight and CUDA container teaching explicitly selectable.
- AC-005: Share identical pinned model snapshots. Only speculative serving downloads 1.5B; only relevant communication labs download 8B. Artifact audit obtains metadata without downloading weights.
- AC-006: One shared installation engine, lock, cache and receipt store validates completed generations, resumes interrupted work and atomically publishes selected runtimes. Scope component/runtime fingerprints to relevant inputs. Regular runs preserve specialized records and installations; unrelated source changes do not invalidate them. Preserve all existing results, logs and complete generations.
- AC-007: Preparation groups differ from hardware eligibility. Inspect configured Slurm capacity without allocations; unsupported hardware reports explicit skips, unknown hardware stays unverified, and real installation/safety failures return nonzero. Setup submits no jobs and performs no GPU qualification, model warmup or persistent service startup.
- AC-008: Native sbatch, srun, nsys and ncu stay visible. Jobs load isolated declared runtimes and never install dependencies; missing/stale runtimes report the exact preparation command. Preserve launcher arguments, profiling, process cleanup, library isolation and private per-job writable caches.
- AC-009: Remove course_setup.py with no compatibility alias. Update imports, help, errors, tests, sync inventories, all six standalone copies, README, lab teaching and generated HTML/archives. After verified synchronization retire only digest-identified old distributed entrypoints; preserve modified files, symlinks, runtime state and evidence.
- AC-010: Complete the five full commands sequentially, then repeat the unchanged sequence, for two successful runs per script on the authorized non-production target. Independently verify receipts, package versions, generations, activation and historical preservation. Report first/repeat wall times, failed/repair time, skips, existing-cache disclosure and total validation time; runtime smokes are separate from preparation timing.
- AC-011: The README-derived Lab Guide has a dedicated Lab Preparation Scripts topic explaining each script's learner purpose, lab scope, automated installations, prerequisites, selection commands, shared model reuse and installation-versus-qualification boundary. Its sidebar exposes all top-level topics and authored subsections in body order, including each preparation group. Review the entire guide for native/default versus optional-container consistency and retain visible Slurm/profiler commands.
- AC-012: Across all eight courses and 110 lab guides, dependency preparation has one shared Lab Guide referral per course entry or lab prerequisite section, without duplicated installation commands or competing manual setup procedures. The guide maps each available course/lab number to its preparation group and explains optional selections. Preserve lab-specific hardware/data prerequisites, runtime checks, native execution and intentional build experiments. Current learner teaching and runbooks must not direct users to retired setup entrypoints.
- AC-013: The source-owned run-labs skill resolves every selected recipe launcher, including optional variants, to its preparation group. Campaign copies reuse an explicitly bound prepared installation without copying or rewriting runtime receipts. Independently validate selected managed runtimes before dispatch, report exact owning preparation commands when missing, preserve isolated job evidence and never install dependencies during a campaign. Refresh and verify the project-local skill through the documented npx installer.

#### Negative Criteria

- NC-001: No compatibility wrappers, unrelated dirty-work changes, historical evidence replacement, platform upgrades, automatic Git commit or external publication.
- NC-002: Do not equate installation with hardware qualification, hide real errors as skips, or label cached timings pristine cold installs.

#### Validation Method

Inspect complete 110-lab and launcher bindings, dependency closure, native runtime activation, transfer verification and targeted retirement. Keep source, installed and live evidence distinct.

#### Test Method

Exercise exact groups, optional variants, read-only plans, selective packages, fingerprints, shared snapshots, interruption, locking, malicious paths, protected packages, activation isolation and result preservation. Run focused tests, validators, helper and generated parity before the frozen live trial.

#### Evaluation Method

Freeze final source identity, target resources/quiescence, existing installation checkpoint, packages and preservation baseline. Run regular, CUDA --all, communication --all, serving --all and Transformer Engine --all twice unchanged. Failed/interrupted attempts do not count; repair the causal owner and replay affected acceptance against final source. Bounded native smokes qualify only observed hardware.

<!-- /REQUIREMENT: REQ-025 -->

<!-- REQUIREMENT: REQ-026 status=active priority=P1 type=feature -->
### REQ-026: Portable setup in privileged Soperator login jails

#### User Story

A cluster administrator uses explicitly selected container preparation on current and future jail-based Soperator clusters without manually repairing each build session.

#### Acceptance Criteria

- AC-001: On supported Ubuntu Linux with an already privileged root session, container preparation makes the existing jail the root of its own private mount namespace before image installation; native preparation does not require this transition. Preserve root, working directory, shared paths and the parent session's mount state.
- AC-002: Require existing namespace capabilities; grant no privileges and change no SSH daemon, platform configuration, host mount namespace or Apptainer isolation guard. Unsupported constrained jails fail with actionable prerequisites.
- AC-003: Close transient namespace/root descriptors and detach the old root before installation. Execute no subprocess, load external code or write files while the process temporarily sees the namespace root. Any incomplete transition terminates the process.
- AC-004: Deliver the adaptation through canonical and standalone setup helpers. Selected container runs preserve results/logs and reuse packages, component generations and runtime receipts. Bound image compression memory and parallelism rather than relying on host-sized defaults in a smaller login container. Keep image-build scratch files on the private managed course filesystem instead of pod-local temporary storage.

#### Negative Criteria

- NC-001: Do not claim universal support for untested future platform versions or policies. GPU execution remains a separate lab qualification.

#### Validation Method

Review namespace ownership and failure boundaries against Linux and installed Soperator source. Keep source tests, bounded namespace proof and full setup acceptance separate.

#### Test Method

Test ordinary-root behavior, jail transitions, missing capabilities, failed syscalls, identity mismatches and descriptor cleanup. Verify parent mount/path identities around a bounded Apptainer reproducer, then run full setup twice after final source deployment.

#### Evaluation Method

A fresh SSH session completes setup and a second fresh invocation reuses completed state with no historical data changes.

<!-- /REQUIREMENT: REQ-026 -->

<!-- REQUIREMENT: REQ-027 status=satisfied priority=P1 type=feature -->
### REQ-027: Visual PyTorch foundation for GPU performance engineering

#### User Story

Students who can read basic Python need to recognize tensor work, memory movement and completion boundaries before reading GPU Fundamentals and performance lab code.

#### Acceptance Criteria

- AC-001: Provide a standalone course titled PyTorch for GPU Performance Engineering directly before GPU Fundamentals in all catalog, guide and course navigation surfaces. Renumber reader resources consistently while preserving existing lab identities and technical prerequisites.
- AC-002: Provide an estimated three-hour, eighteen-lesson reading route with definition-first explanations, contextual small diagrams and commented worked examples. Every lesson has an observable objective and a substantive Performance connection. Teach tensor attributes, shapes, indexing, broadcasting, arithmetic, reductions, matrix multiplication, layout, dtype, device, modules, autograd, inference, transfers, memory and correct timing before an integrated code-reading example.
- AC-003: Use a lessons-only profile with Objective, How it works and Mental model. No labs, Practice sections, exercises, setup scripts, execution dependencies or result archives. Reading requires only basic Python, not a running GPU or cluster. Shared lab discovery and preparation skip this reading profile and create no job or result directories for it.
- AC-004: Use original passive inline SVGs with consistent shared fonts, readable labels and deliberate shapes; preserve visible explanations and inspect desktop, 390px and 320px layouts. Keep complete canonical Markdown, source metadata, syllabus, glossary and generated self-contained HTML in parity.
- AC-005: Support technical claims with current official PyTorch references. Treat supplied topic material as reference data; simplify useful lab ideas into examples without copying operational instructions or manufacturing measurements.
- AC-006: Distinguish exact PyTorch API names from application examples throughout explanations, tables, captions and diagrams. API names use bold monospace; example variables and values use ordinary monospace; application-assigned axis meanings and general concepts use plain text. Headings and callout labels may remain bold. Explain the convention at entry; preserve normal executable code formatting.
- AC-007: Define dimensions as indexed axes with sizes and application-assigned meaning; distinguish axis index, size and meaning with concrete 2-by-3-by-4 grids. Explain shape, dtype, device and element count separately, and constructor shape arguments independently of rand/randn distributions. Explicitly label batch/position/feature as an example interpretation. Remove the requested opening Tools-nearby referral while preserving relevant optional references.
- AC-008: Retain essential explanations and correctness constraints, consolidate genuine duplication, and include focused batching/vectorization and compilation/fusion lessons. Teach equivalent-work correctness, memory/latency tradeoffs, compilation overhead and evidence boundaries without promised speedups.
- AC-009: Define concepts affirmatively through what they are and how they work. Define index and its plural indices before notation, and map each coordinate to a grid, row or column with zero-based positions. Keep lesson 2 indexing, slicing and reshaping examples short and grounded in the same grids. Teach the linear layer's feature-axis contract in lesson 9; teach inferred reshape sizes and axis reordering in lesson 6. Preserve technical constraints with clear conditions and consequences.
- AC-010: Make every lesson easy to scan with a direct definition, a useful diagram and short examples that each teach one relationship. Define APIs before using them; omit advanced detours that do not serve the lesson objective. Broadcasting uses one matrix-plus-bias example without expand/repeat or an extra row-offset case. Keep performance connections brief and concrete; preserve necessary numerical, storage and completion constraints.

#### Negative Criteria

- NC-001: No installation, live GPU execution, publication, compatibility alias or advanced kernel/distributed-training syllabus is required by this reading course.

#### Validation Method

Inspect full teaching and diagram semantics, exact catalog order, lesson identities, safe resources and source/render parity.

#### Test Method

Run focused reading-profile and catalog regressions, all native course validators, shared build/freshness, syntax checks of examples and isolated responsive browser assertions.

#### Evaluation Method

Read the route as a Python-literate beginner: example comments and diagram labels must expose shapes, values, memory implications and what remains a performance hypothesis. The duration is an estimate, not a learner-outcome measurement.

<!-- /REQUIREMENT: REQ-027 -->

<!-- maintain-project-specs:requirements:end -->
<!-- markdownlint-enable MD001 MD024 -->
