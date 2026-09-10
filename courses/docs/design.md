<!-- markdownlint-disable MD001 MD024 -->
<!-- maintain-project-specs:design:start schema=maintain-project-specs/design-v2 -->
# Project Design

<!-- FEATURE: FEAT-001 reqs=REQ-001 status=ready delivery=implemented priority=P0 version=3 -->
### FEAT-001: Standalone course catalog

#### Requirements Covered

- REQ-001: Five-course NVIDIA H100 learning path.

#### Context Evidence

Five independent course roots support general GPU foundations and three specializations.

#### Design Details

Expose the five ordered roots and prerequisite links. Keep helpers course-local and avoid cross-course runtime imports.

#### Selected Option

Independent self-contained packages with one canonical path.

#### Alternatives Considered

Shared runtime dependencies would couple learner environments unnecessarily.

#### Implementation Boundaries

Courses only; no provisioning, publication or unrelated repository changes.

#### Test-First Success Criteria

- TDD-001: Exact five-root, prerequisite and independent-helper assertions pass.

#### Validation Plan

Check inventory, links, source parity and standalone validators.

#### Test Plan

Exercise course-local commands and domain ownership.

#### Evaluation Plan

Each course is navigable independently.

#### Rollout And Rollback

Atomic HTML generation; version control protects source changes.

#### Done Definition

Five roots and correct prerequisite links are present.

#### Implementation Evidence

The catalog exposes Fundamentals, Optimizations, Training, Inference and Custom CUDA.

#### Verification Evidence

Source inventory and ownership checks pass; target runtime evidence is separate.

<!-- /FEATURE: FEAT-001 -->

<!-- FEATURE: FEAT-002 reqs=REQ-002 status=ready delivery=implemented priority=P0 version=7 -->
### FEAT-002: Shared authoring and publication contract

#### Requirements Covered

- REQ-002: Consistent practical and publication-safe teaching contract.

#### Context Evidence

The packages contain long-form lessons, runnable labs and self-contained HTML.

#### Design Details

Use one renderer and shared light stylesheet, complete source listings, accessible SVGs, supporting guides and official end references. FEAT-011 owns the publication simplification; FEAT-021 defines the current lesson-to-lab boundary and unified Practice section.

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

The five courses contain 73 lessons, 94 numbered labs and 122 diagrams. FEAT-011 supplies the publication UI; FEAT-019 and FEAT-020 supply the networking and transfer additions, FEAT-021 defines the lesson/lab boundary, and FEAT-023 defines the current lesson presentation and diagram coverage.

#### Verification Evidence

Source checks pass; fresh visual and H100 qualification remain separate.

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

<!-- FEATURE: FEAT-005 reqs=REQ-006 status=ready delivery=implemented priority=P0 version=2 -->
### FEAT-005: Standalone LLM inference course

#### Requirements Covered

- REQ-006: Practical LLM inference optimization course.

#### Context Evidence

Inference separates mechanics and serving environments and owns engine workflows.

#### Design Details

Provide inference-owned labs for cache/scheduling/quantization/metrics/parallelism exercises, and provide gated engine profiles for vLLM, TensorRT-LLM/Triton, AIPerf, and advanced Dynamo material.

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

The standalone Inference package contains 24 numbered Python labs, separate
mechanics and serving manifests, and immutable-container launchers for vLLM,
TensorRT-LLM/Triton, AIPerf, and conditional Dynamo. Live chunked-prefill,
prefix-cache, speculative, parallelism, and capstone profiles use independent
restarts, correctness gates, and counterbalanced trials where applicable.

#### Verification Evidence

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

<!-- FEATURE: FEAT-021 reqs=REQ-002,REQ-009,REQ-010 status=ready delivery=implemented priority=P0 version=3 -->
### FEAT-021: Theory lessons linked to complete practical labs

#### Requirements Covered

- REQ-002: Consistent lesson and lab presentation.
- REQ-009: Complete conceptual teaching and preservation of useful explanations.
- REQ-010: Unified practical guides with explicit ownership and prerequisites.

#### Context Evidence

The current lessons repeat applied instructions already covered by their lab
guides. The accepted boundary moves all nine published fields from H100 focus
through Review into the owning practical guides; only Practice labs links stay
after lesson theory. Worked examples and execution become one Practice flow.

#### Design Details

Use the lesson presentation in FEAT-023: Objective, How it works, Practice labs
and final Mental model. Generate Practice labs links from explicit metadata.
Remove the nine applied fields from the
lesson schema and canonical lesson sources, without compatibility aliases.

Apply the same boundary inside How it works prose:
move lab-specific readiness checks, workload descriptions, commands and result
recipes into the owning guide, merging existing explanations rather than
appending duplicates. Keep conceptual definitions and useful hand-worked
reasoning in lessons. Update Theory preparation pointers when their detailed
explanation moves. The catalog-wide residual-prose revision is implemented. Read-only editorial
review verified preservation and corrected prerequisite pointers; full-page
browser review remains pending.

Map every moved explanation to a meaningful existing lab before removing it.
Place hardware/qualification context in Before you start; combine worked
reasoning, prediction and the existing commands in Practice; integrate evidence
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
No change to skill source is required: the explicit user-selected format takes
precedence over the reusable skill's default field names. The fixed stack has
no AI-agent or service architecture decision. This is a local reversible
content/schema refactor; apply ownership, preservation, input validation and
accessibility checks without an unrelated system-architecture redesign.

#### Test-First Success Criteria

- TDD-001: All 73 lessons use the new theory schema and exact Practice labs links;
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

All five courses use theory followed by Practice labs links, practical guides
own the complete applied material, and current static/editorial checks pass.
Outstanding browser or target evidence remains explicit.

#### Implementation Evidence

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

<!-- FEATURE: FEAT-022 reqs=REQ-013 status=ready delivery=implemented priority=P1 version=1 -->
### FEAT-022: Branch-published Nebius learning website

#### Requirements Covered

- REQ-013: Nebius course website and navigation.

#### Context Evidence

Five self-contained course pages and canonical course metadata exist. The repository is Apache-2.0 licensed and has no course-specific license. At task start, the website catalog, root HTML entry and Pages publishing configuration were absent.

#### Design Details

Extend the local builder with a catalog renderer and embedded editorial stylesheet. Derive titles, hours, ordering and link destinations from canonical course metadata and the catalog registry; author concise summaries and outcomes alongside the renderer. Present a hero, prerequisite route, two foundation cards and three specialization cards. Add a catalog link and native HTML course switcher before each course's lesson contents. Keep the current course marked and link directly to its four siblings. Each course metadata file carries a stable slug; standalone validators use that identity independently of the checkout folder name.

The compact footer states: Copyright 2026 Nebius B.V.; provided free of charge for learning and education; licensed under Apache License 2.0. Preserve third-party licensing. Embed the unmodified repository license in a collapsed license section and link locally to it. Resource embedding remains mandatory; navigation exceptions are limited to declared course routes inside the navigation block.

A small root welcome page links to the catalog and repository. Add root `.nojekyll` and configure branch-based Pages from `main` `/` after the reviewed change merges. No custom workflow or runtime dependency is required.

#### Selected Option

Generate the course website locally and commit its HTML; serve the complete selected branch root through GitHub Pages.

#### Alternatives Considered

A handwritten catalog would duplicate metadata. A custom deployment workflow is unnecessary for the selected whole-root publication. Noncommercial content terms were considered and rejected in favor of the existing Apache-2.0 license and a free educational offering statement.

#### Implementation Boundaries

Website rendering, explicit course navigation, validators, documentation and authorized Pages settings only. Preserve course teaching, lab logic, dependencies, existing copyright notices and branch protection. Standalone validators must not require sibling directories or the enclosing repository license file.

#### Test-First Success Criteria

- TDD-001: The catalog remains current with metadata and rejects missing or stale committed output.
- TDD-002: All course navigation edges work; unknown destinations, misplaced relative links and external resources remain rejected.
- TDD-003: The embedded license matches its canonical source and the attribution introduces no additional usage restriction.

#### Validation Plan

Run focused tests, the shared pytest suite, all standalone validators and alignment. Review desktop/mobile and keyboard behavior locally, then verify deployed content and the Pages source revision independently.

#### Test Plan

Add metadata, prerequisite, link graph, current-course marker, license parity and negative publication controls. Preserve existing course-content and presentation coverage.

#### Evaluation Plan

The learner can choose a course, understand its prerequisites, switch directly to another course and read the licensing terms without loading external resources.

#### Rollout And Rollback

Reuse the current branch and preserve its history. Merge through the required approving review, then enable HTTPS Pages from `main` `/`. Verify live routes before declaring publication complete. Revert website defects through the normal reviewed branch process.

#### Done Definition

The catalog, root welcome page, cross-course navigation and attribution are implemented and validated; the intended revision is independently confirmed on the live Pages site.

#### Implementation Evidence

Implemented the catalog renderer and embedded stylesheet, root welcome page and .nojekyll, course switchers, complete embedded Apache license and attribution, canonical metadata slugs, scoped navigation validation, publication regression tests and authoring documentation.

#### Verification Evidence

All 740 offline pytest tests passed. After the final favicon edit and rebuild, 78 focused publication/content tests, generated-source parity and all five standalone validators passed. Tests cover renamed standalone course copies, every local route, stale/missing catalog output, metadata escaping, navigation restrictions and license parity. Ruff, formatting, configured Markdown lint and whitespace checks passed. Browser checks covered desktop, tablet, 390px and 320px layouts; all five courses, root and catalog routes; keyboard switching and visible focus; embedded licensing; no horizontal overflow or external loaded resources. The final browser console had no errors. Existing GitHub CodeQL checks passed on the website implementation commit. Pages configuration and live deployed-revision verification remain pending the required approving review and merge.

<!-- /FEATURE: FEAT-022 -->

<!-- FEATURE: FEAT-023 reqs=REQ-002,REQ-009 status=ready delivery=implemented priority=P0 version=1 -->
### FEAT-023: Coherent conceptual lessons with integrated diagrams

#### Requirements Covered

- REQ-002: Consistent lesson structure and meaningful diagrams.
- REQ-009: Conceptual titles, complete causal teaching and terminology.

#### Context Evidence

The 73 lessons contain useful explanations but repeat template labels and
frequently enumerate components in their titles. Some summaries introduce
new concepts and some lessons lack a diagram at their explanatory home.

#### Design Details

Use exactly Objective, How it works, Practice labs and Mental model in that
order. Start How it works with the central definition; integrate prerequisite
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

Courses only. Preserve pre-existing work, all 94 lab identities and executable
behavior. No dependencies, installations, skill-source edits or publication.

#### Test-First Success Criteria

- TDD-001: All 73 lessons use the exact ordered fields; retired fields fail.
- TDD-002: Every How it works contains an accessible core diagram; missing or
  externally placed diagrams fail even when another lesson has extra figures.
- TDD-003: Full canonical prose parity, title/link consistency and unchanged
  executable sources hold throughout the five courses.

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

<!-- maintain-project-specs:design:end -->
<!-- markdownlint-enable MD001 MD024 -->
