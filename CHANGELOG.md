# Changelog

This root changelog tracks repository-wide process, automation, and
documentation changes only. Project-specific release notes live in the owning
project folder.

## [Unreleased]

- Clarify the distinction between a completion run's trusted workflow revision
  and its tested merge result in the CI workflow and deployment documentation.

- Resolve merged results through identity-bound GraphQL evidence after GitHub
  removed the REST result field. Preserve fail-closed recovery, exact-result
  checkout and duplicate suppression for already-authorized merges.

- Record the reviewed broker deployment and verified repository protection;
  document GitHub's dependent branch-creation setting normalization and the
  configured Pages publication path. Keep live acceptance evidence separate.

- Unify ordinary and Dependabot PRs, including Docker, behind local review,
  safe repairs, fresh head/base review and CI, then explicit operator-dispatched
  protected merge. Remove the dependency metadata producer and rollout toggles;
  keep the numeric operator list as the only merge-specific variable.
- Require operator-dispatched positive-review intents in completion and CI
  checkout. Automatic events report CI or recover admitted completion only.
  Preserve bootstrap evidence and distinguish source, installed, CI and live
  acceptance; keep standalone review, preparation-only and publication ownership.

- Revalidate merge receipts inside each CI job before selecting the merged
  commit. Load the validator from the trusted workflow revision and keep Git
  fetch credentials process-local. Preserve ordinary PR and manual CI lanes.

- Use per-job built-in Actions tokens, a CI-only required aggregate, guarded
  direct merge and recoverable explicit result-CI/Pages publication. Stop
  for unsupported queues or workflow-file
  permissions. Remove custom-App setup without changing Agentic SDLC.

- Add a shared protected GitHub Actions merge broker for ordinary skill workflows
  and reviewed Dependabot updates, with local agent review, exact-head
  guards, explicit CI policy and no personal-token fallback. Preserve Dependabot
  schedules/ecosystems and the separate Agentic SDLC workflow.

- Fix release-session repository discovery to use GitHub's canonical endpoint
  without a trailing slash, preventing a false access failure during release intake.

- Simplify all eighteen PyTorch lessons for scanning: shorter explanations,
  focused examples and brief performance connections. Keep broadcasting to one
  matrix-plus-bias addition, remove unrelated API detours, and simplify model,
  transfer and batching code. Align diagrams, glossary, references and the
  generated reading course while preserving its lessons-only profile.

- Preserve reading-course subsection heading levels when rendering diagrams,
  so PyTorch lesson subsections use `h4` beneath lesson fields instead of `h6`.

- Simplify PyTorch indexing with positive definitions, explicit grid/row/column
  indices and short slicing/reshape examples. Label the selected diagram cell,
  teach the linear layer's feature-axis rule in lesson 9, and keep advanced
  reshape/layout details in lesson 6. Refresh glossary and generated teaching.

- Clarify all PyTorch reading lessons with definition-first explanations, exact
  API-name notation, concrete tensor-axis and random-sampling diagrams, and a
  Performance connection in every lesson. Expand the estimated route to three
  hours and eighteen lessons, including batching/vectorization and compilation.
  Preserve the lessons-only profile and align sources, diagrams and publication.
  Preserve bold API emphasis inside inline code in the shared course stylesheet.
  Distinguish dataset tuple samples from the loader's default list batches.

- Align all nine course packages and 110 lab guides with the current catalog.
  Reconcile active prerequisite and topic-ownership specs, distinguish supplied
  measurements from conceptual extensions, and complete taught glossary terms.
  Keep the lessons-only PyTorch course out of shared runtime discovery and lab
  directory preparation, with synchronized standalone helpers and regression tests.

- Add PyTorch for GPU Performance Engineering as a visual, lessons-only foundation
  immediately before GPU Fundamentals: sixteen concise lessons, sixteen original
  diagrams and nineteen commented examples in an estimated 105-minute reading
  route. Align the ten-resource catalog, course numbers, shared Lab Guide and
  all course menus; preserve existing labs and their identities.

- Review all eight course packages and 110 lab guides against their supplied
  implementations. Correct log/report paths, launcher arguments, workload names,
  optional-exercise and CUDA qualification claims; consolidate and expand taught
  glossary terms while preserving the distinct reading and labs-only profiles.

- Align synchronization documentation and fixtures with all eight packages,
  source verification and Python preparation. Cover local course removal while
  preserving destination-only course files and learner work.

- Use the same mint background and teal text for every catalog card number badge.

- Review all eight courses, the catalog and Lab Guide for consistent formatting
  and fresh builds. Correct the supporting-guide test to use production link
  mappings, refresh publication evidence, and distinguish passing responsive
  layout checks from remaining dense-diagram readability limits.

- Show decimal-MB sizes before each built or verified course HTML/ZIP path.
  Print one aligned publication summary after successful verification, with
  listed-output and other-file subtotals, total size, remaining capacity and
  limits. Remove largest-file reporting; Python `--no-summary` suppresses only
  the summary while retaining exact-byte budget enforcement.

- Align run-labs with the five managed preparation scripts. Resolve actual
  recipe launchers, including optional containers, and validate existing
  runtime receipts before jobs. Keep campaign source copies and results isolated,
  reuse original installations without rewriting receipts, and report exact
  preparation commands for missing dependencies. Refresh the project-local skill
  through its documented npx installation path.

- Centralize dependency preparation across all eight courses and 110 labs in
  the Lab Guide, with a complete course/lab-number lookup. Remove duplicated
  setup commands and stale manual environment procedures while preserving
  hardware qualification, native job commands and source-edit experiments.

- Explain all five lab preparation scripts in a dedicated Lab Guide topic,
  including automated dependencies, model reuse, prerequisites and selection.
  Add nested sidebar links for guide steps and preparation groups, and align
  readiness instructions with native CUDA and serving runtimes.

- Split course preparation into five Python 3.12 commands: no-argument regular
  setup and explicit CUDA, communication, serving and Transformer Engine groups.
  Remove the old setup filename without an alias. Use native CUDA 13.3.0,
  vLLM 0.28.0 and AIPerf 0.12.0; retain optional container exercises.
  Select prerequisites before installation, share pinned model snapshots, scope
  fingerprints to relevant dependencies, and preserve specialized installations
  across regular reruns. Verify synchronized source before digest-gated retirement
  of old entrypoints; native jobs keep private writable caches and process cleanup.
  Preserve executable permissions when extracting native archives on shared
  filesystems without restoring archive ownership; fail on extraction errors.
  Keep vLLM's UUID-named RPC sockets on a short node-local path with private job
  permissions, while retaining per-job writable caches.

- Preserve course setup's APT simulation diagnostics in private phase logs,
  report the failed phase and exit code, and keep package-plan checks in the
  same isolated environment as installation. Explain repository version
  mismatches without silently downgrading installed libraries.
  Let Apptainer choose build privileges instead of forcing fakeroot for root.
  Prepare a private mount namespace for privileged jail setup, preserving the
  existing jail and other sessions while retaining Apptainer's isolation checks.
  Bound image compression memory and parallelism for smaller login containers.
  Place image-build scratch files in the private shared runtime cache to avoid
  exhausting the login pod's local storage allowance.

- Add automatic idempotent GPU-course setup: one no-argument command installs
  applicable runtimes and preserves results; native jobs restore private runtime
  records after reconnects. Bootstrap Python during normal sync/SSH, cache pinned
  containers/models, and keep GPU qualification in the labs.
  Isolate runtime activation from inherited Python overrides and managed paths
  belonging to a previously selected checkout.

- Replace the GPU Performance Tools metrics-path text sketch with a contextual
  diagram matching the shared course style, distinguishing VMAgent collection
  from Grafana queries and returned panel data.

- Audit all eight course publications: reject silently overwritten lesson fields,
  preserve fenced examples, strengthen passive-resource checks for reading
  courses, and align build/navigation documentation with the current catalog.

- Align native course execution: remove an unsupported distributed capture flag,
  retain semantic workload metadata, mark internal PyTorch profiling as diagnostic,
  stream historical report copies and clarify collection's exact-job scope.
- Replace GPU course execution/profiling wrappers with explicit per-lab native
  Slurm jobs and NVIDIA commands. Rename workload size to `--workload`, isolate
  new outputs by job ID, preserve existing evidence and add verified copy-only
  historical organization. Align run-labs recipes, recovery and exact-job collection.
- Add the five-lesson GPU Performance Tools reference course before Fundamentals,
  centralizing Slurm/Nsight flags, NVTX, PyTorch profiler and Grafana/VictoriaMetrics
  explanations without adding practical labs. Align its prerequisite reading route
  across the README, catalog, Lab Guide, course sidebars and practical syllabuses.

- Display course publication sizes and limits in decimal MB, retaining exact-byte
  enforcement and showing precise overflow amounts. Highlight build/check
  failures, including exceeded file, site and archive limits, in red on terminals;
  preserve plain redirected and `NO_COLOR` output.

- Remove the unused `sync-labs.sh --receipt` option and duplicate connection
  output from run-labs. Campaigns retain private connection settings and
  independently verified synchronization evidence, without compatibility aliases.
- Retry run-labs SSH transport failures only for queries,
  and recognize Slurm `BOOT_FAIL` and `DEADLINE` as terminal failures without
  cancelling terminal jobs or replaying failed units. Preserve failed evidence
  and release campaign claims after remaining work finishes.

- Repair course regression checks for native CPU submissions and Compute
  command rejection. Exercise the documented CPU launch through its real batch
  script with local scheduler doubles while preserving prepared-interpreter
  and workload-argument checks.

- Prepare all practical course directories once with `course_setup.py prepare`,
  and separate monitoring discovery into its explicit `monitoring` action.
  Explain every lab program and show one baseline `sbatch` in each Practice,
  including CPU-only submissions for introductory and cache-tiering labs and
  their variations, with GPU telemetry limited to optional CUDA runs. Replace learner
  profiler wrappers with visible native commands, retain maintainer automation,
  and expose worker/server capture arguments with bounded lifecycle handling.

- Make fresh-terminal monitoring verification restore its course checkout, and
  create the Advanced fabric-tools directory independently of publishing setup.
  Add regression checks for both command-first preparation paths.

- Simplify the shared Lab Guide into a command-first learner workflow with a
  small GPU execution check before full monitoring/profiling qualification.
  Show native Slurm submissions, private log preparation and exact-job result
  inspection across all 110 labs; retain explained helpers for publishing,
  qualification and automated campaigns. Consolidate each lab's shared
  prerequisite links and distinguish driver, toolkit and framework versions.

- Place every course diagram immediately after its topic-specific explanation in
  canonical Markdown. Require explicit placement for overview, detailed, lab and
  performance-workflow figures; reject missing or duplicate markers instead of
  collecting diagrams at the end of broad sections. Preserve existing figure
  identities, assets and text-only/labs-only course profiles.

- Refine the GPU Fundamentals matrix multiplication illustration with equal
  matrix cells, proportional type, a highlighted row–column dot product and
  bounded display width. Clarify dimensions, per-thread accumulation and the
  distinction between arithmetic expressions and emitted GPU instructions.
  Label the output matrix with x across columns, y down rows, and the chosen
  row-first mapping C[row, column] = C[y, x].

- Add a GPU Fundamentals matrix multiplication example and diagram tracing
  four outputs through a 2×2-thread block, a partial 32-lane warp and execution
  on one SM. Support exact lesson-figure placement using registered Markdown
  references, with checks for ownership, titles and duplicate placement.

- Rework the first four GPU Fundamentals performance-tool diagrams into
  landscape layouts that use the available page width, with left-to-right
  timelines and matching explanations, captions and accessible descriptions.

- Give all seven course overviews a consistent introduction, prerequisite and
  practical-scope pattern. Consolidate shared operations in the Lab Guide,
  preserve Advanced Labs' measurement and completion guidance in its course
  guide, and use cxcli-generated Grafana/Nsight connection and password commands.

- Simplify the shared Lab Guide introduction with hosting-neutral course links,
  and remove maintainer/build commentary and the repeated reading-format paragraph
  from its canonical README and generated HTML.

- Clarify the courses README catalog link and place its browser-edition link
  under lab setup. Keep the README as the shared Lab Guide source, move build
  details into maintainer documentation, and put attribution last without
  duplicating it in the generated guide.

- Lead the courses README with the GitHub Pages catalog. Introduce six courses,
  Advanced Labs and the shared Lab Guide as eight ordered resources, with the
  guide second after Soperator. Use the same order and current-page navigation
  across the catalog, learning path, seven course pages and Lab Guide, while
  preserving course content, prerequisites and downloads.

- Clarify that the Soperator course's shared jail is mounted by login, controller
  and worker Pods, including the controller's shared user information and Slurm
  configuration, with versioned public references.

- Preserve generated course summary CSV bytes in Git and recognize their CRLF
  records without relaxing other whitespace checks or changing evidence hashes.

- Align course regression fixtures with bounded Dynamo discovery and recognize
  the official Grafana documentation used by the profiling guide.

- Harden empty publication-root checks and separate ZIP member names from source
  filesystem paths; keep course-build templates and regressions synchronized.

- Rename course downloads to `*-lab-results.zip` without changing archive contents;
  enforce per-file and whole-site publication budgets during build/check. Add
  reusable course-build templates, explicit companion-link checks, and portable
  download guidance to the source `create-learning-course` skill.

- Split the course HTML builder into focused Python modules while preserving
  course content and styling. Publish one external dashboards-and-results ZIP
  per practical course, with separate results and setup-guide links; remove
  repeated setup and dashboard reminders from the lab introduction. Supply
  original lab scripts through sync-labs.sh without redundant lab-kit ZIPs.
  Align build-courses.sh help and output with HTML and ZIP checks; remove embedded download payloads
  and repetitive file links. Share deterministic archive assembly with the
  evidence exporter, validate all outputs before replacement, and reject
  unsupported Markdown and active SVG content.

- Shorten the Soperator course overview by consolidating repeated goals,
  prerequisites and text-only scope while retaining authorized-use limits.

- Standardize all seven course pages with shared heading typography, standalone
  glossary sections, numbered official references and bulleted next steps. Remove
  displayed mission and syllabus sections while preserving scope, readiness and
  completion guidance and distinct onward-learning options.

- Give every course exactly one Where to Go Next and one A–Z Glossary. Consolidate
  definitions from all lessons and performance-tool guides, preserve distinct meanings and first-use
  teaching, and reject duplicate glossary sections in authoring validation.
  Remove repeated lesson-level onward sections while preserving distinct optional
  study directions and limits. Keep optional lesson References last and course
  Glossary before Official references.

- Add worked Systems, Compute, NVTX and Grafana diagrams to GPU Fundamentals,
  with a short marker/range example, interpretation checks and explicitly
  synthetic values. Connect the tools through a baseline-to-diagnosis-to-validation
  mental model. Validate contextual SVG safety and complete code rendering.

- Align course guides and READMEs with the supplied scripts after completing all
  110 practical labs in both profiles. Record the 220 verified prepared-H200
  exports separately from H100 and clean-install qualification; clarify fixed
  workload presets, training controls and profiler evidence limits.

- Define NVIDIA Tools Extension Library (NVTX) in the GPU Fundamentals
  glossary and profiling guide, with its official reference and a distinction
  between annotations, synchronization and GPU timing.

- Clarify the GPU Fundamentals mental model: the CPU submits kernel launches
  and requests transfers, hardware moves data, and synchronization waits for
  required work without copying results back to the CPU.

- Validate zero-metric run-labs overview crops before adapter effects, and wait
  for the current generation and both correctness rows to render inside the
  final crop. Reject blank, stale and clipped qualification screenshots.

- Complete run-labs publication pairs for the inference and training capstones
  with two independent three-child groups, preserving both aggregates. Repeat
  each Training Lab 32 device configuration and retain only its CUDA Systems
  capture. Declare the five one-control Inference Lab 36 policy comparisons,
  with focused recipe-expansion checks in both profiles and aligned guides.

- Qualify writable, job-isolated module and compiler caches separately from
  immutable model assets in prepared course containers. Document the
  `HF_MODULES_CACHE` prerequisite for TensorRT-LLM configuration locks and
  distinguish SDK import effects from CPU-only diagnostics.

- Start Inference Lab 30's Triton server through the prepared image's one-rank
  MPI launcher inside its Slurm GPU task. Allow its backend worker to spawn
  within that allocation with MPI's `--oversubscribe`. Preserve loopback binding, bounded
  readiness and owned cleanup. Disable temporary configuration auto-completion
  to avoid reinitializing the same MPI rank; require equivalent explicit batch
  and response settings in the prepared repository. Cover successful requests, startup failure and
  invalid responses with local lifecycle fixtures.

- Check prepared container workspace admission after campaign sync and before
  freezing prerequisites or submitting jobs. Preserve image-specific Python
  search paths and verify required imports. Keep exact owned-root binding and
  prerequisite probes separate from lab execution, and align the Lab 30
  lifecycle test with both equivalent runs per protocol.

- Keep Inference Lab 35's Systems capture limited to its CUDA configuration.
  Repeat each CUDA, CPU and one-token CPU configuration independently for
  equivalent publication pairs, preserving all six unprofiled originals.

- Run Inference Lab 30 with two fresh equivalent qualification jobs per
  protocol and profile. Publish OpenAI and Triton pairs separately, retaining
  all four originals without treating bounded API probes as engine benchmarks.

- Select a measured matrix kernel for Inference Lab 10 Compute evidence,
  excluding staged request-buffer writes. Align the guide, observability
  command and both run-labs recipe paths while retaining in-process capture
  and its Compute-only host-memory allocation.

- Align Inference Lab 10 run-labs captures with the guide's in-process mode
  and Compute-only 256 GiB host-memory request. Validate and freeze the recipe's
  stage memory setting without applying it to clean measurements or other jobs.

- Document isolated Transformer Engine installation and qualification for Training
  Lab 22, including matched CUDA headers/NVRTC discovery and the unchanged small
  FP8 numerical gate before larger runs. Link setup from the shared course guide.

- Match Compute kernel filters against full demangled names with simplification
  disabled, so generic template kernels retain their GEMM identity. Keep NVTX
  and launch-count limits and verify both direct and companion captures.

- Add operation-scoped Compute ranges to the affected inference and training
  labs, excluding initialization while preserving clean workload semantics.
  Align Training Lab 06's recipe baseline with the guide's default group size
  and document Inference Lab 16's online metadata requirement.

- Select pipeline GEMMs for Optimization Labs 19 and 20 Compute captures,
  excluding weight initialization and Lab 20's input-fill kernel. Align the
  course commands and run-labs recipes in both profiles, document the default
  warmup capture boundary, and verify filter propagation through the launcher.

- Align Optimization Lab 14 publication with its declared FP32-to-BF16 mode
  comparison while preserving shape, input scaling and useful work. Keep dtype
  and logical I/O bytes invariant for repeated runs of the same mode. Select
  Lab 15's first measured uniform-grid probe for Compute, excluding input
  initialization and the one-block compilation probe, and clarify the limited
  sample count behind its reported percentiles.

- Run Optimization Labs 09, 16 and 19 with three independent jobs per
  configured variant and profile, retaining all results and reversing the
  middle repetition's order. Keep in-process rounds and native diagnostics
  separate from independent acceptance jobs. Lab 09 result metadata now
  leaves GPU model identity to the observed environment.

- Repair Optimization Lab 07 run-labs recipes so native captures use only
  external profiling, while the two PyTorch repetitions retain trace export.
  Select the projection range for Compute and document its first warmup GEMM;
  verify mode separation and launcher range propagation in focused tests.

- Extend run-labs recovery to Grafana services and nsys/ncu profiling runtimes,
  alongside both Nsight viewers/streamers and their loopback connections. Permit
  repeated scoped restarts without renewed approval, preserve configuration and
  persistent storage, and verify visible browser responses. Recover terminal
  diagnostic-tool failures through a fresh affected-lab/profile campaign with
  preserved failure history and unchanged acceptance; no in-place retry or
  replay of unaffected completed units.

- Make run-labs recreate exited Grafana and Nsight loopback forwards under
  existing access authorization, including after a prior campaign session ends.
  Separate local reconnection from remote-service restart approval and verify
  process lifetime, target identity and readiness before resuming evidence.

- Allow repeated necessary Nsight native-tool, streamer/viewer, browser-session
  and task-owned forward recovery during run-labs campaigns, without a fixed
  count or repeated approval within existing target authority. Keep each action
  scoped and observed, preserve reports and completed jobs, and protect unrelated
  sessions and services.

- Align GPU Optimization guides with the launcher's configured NVTX range
  and kernel filters. Clarify that Lab 01 captures the CUDA-event GEMM,
  separately from initialization and host-timer loops.

- Verify Fundamentals Lab 03 through its complete host-copy mechanism, including
  copy-only Systems reports. Bind both configurations to exact jobs, payloads,
  correlated transfers, synchronization and final readback; require copy-specific
  browser review and preserve report/proof hashes and export gates.

- Retain the CUDA capstone guide's separate memcheck prerequisite in run-labs,
  with independent three-trial aggregate checks and observed GPU identity.

- Distinguish run-labs prepared-runtime monitoring checks from new setup
  discovery. Preserve exact target and backend identity, healthy results scraping,
  complete GPU telemetry, local-only routing and publication readback without
  converting older setup receipts or rerunning installation merely to resume.

- Shorten the shared course README by moving specialized runtime and vendor
  preparation to owning course/lab guides. Start preparation with sync-labs.sh
  and add dedicated Grafana, Nsight Compute and Nsight Systems connection and
  browsing instructions, retaining explicit cluster targeting and private reports.

- Use hardware-neutral Performance Engineering course branding and illustrate
  the Grafana measurement flow in the shared guide. Install Grafana and
  Pushgateway through cxcli; replace course catalog/resource generation with
  private connection discovery and native scrape verification, removing legacy
  installer flags and receipts.

- Verify idle KV-routing replicas with complete zero-assignment records and
  eight GPU-worker profiler starts with recorded stop events and completed
  capture acknowledgements. Retain the active peer's kernel,
  annotation and browser evidence instead of rejecting a valid idle capture.

- Wait for Dynamo frontend model discovery after worker readiness, preserving
  one bounded deadline, the exact model gate, and immediate failure on an
  unexpected model or failed owned process.

- Pass the selected Dynamo runtime's executable directory to serving children
  so FlashInfer can find its installed Ninja compiler driver. Preserve inherited
  tool paths and cover symlinked interpreters and paths containing spaces.

- Document bounded native viewer memory release between completed run-labs
  reviews using Nsight Streamer's normal tool restart. Preserve browser sessions,
  original reports and explicit visual checks; use the same campaign recovery
  policy after capture failures.

- Size run-labs artifact-copy deadlines from the remote inventory, bounded from
  30 minutes to four hours. Preserve completed copies for collection resume and
  retain all original files, independent checksums and existing export behavior.

- Repair course regression tests to select GPU courses independently of catalog
  order and check the current launcher-owned engine qualification workflow.
  Synchronize inference launcher fixtures with actual fake-server readiness and
  cover delayed startup without weakening command or quality-gate assertions.

- Centralize all six practical courses' setup and execution in the two-section
  courses README and generated shared guide. Remove numbered setup labs, retain
  unnumbered environment readiness, import dashboards during setup, and use
  cxcli-managed profiling with private report exports and container bindings.
  Preserve course-specific runtimes and move maintainer guidance out of the
  learner entry point. Direct Python exercises and dependency checks use the
  restored course interpreter; build help includes the shared guide.

- Update all practical-course dashboard instructions to the current cxcli
  import/validate commands with explicit course-folder and data-source UIDs.
  Preserve initial monitoring render/apply and remove the obsolete per-lab
  catalog-attachment flow. Correct remaining workload-profile names and
  supported H100/H200 prerequisite guidance.

- Reuse a source-owned course evidence runner with pinned prepared adapters,
  persistent isolated browser sessions and compact Grafana table captures.
  Guide native viewer searches with independent event values and explicit
  checks of rendered selections and file-dialog state.
  Keep required Compute metrics together by hiding optional section prose,
  while retaining profiler warnings and rule output.
  Capture every view of a published comparison before publishing the next
  comparison, preserving the exact generation shown in Grafana.
  Preserve explicit visual review, complete numeric/native coverage, serial
  Slurm allocation and the existing result replacement and ZIP workflow.

- Verify Advanced Lab 06 native CE peer copies and SM copy kernels without
  requiring vendor NVTX annotations. Retain complete directed-pair coverage,
  buffer-size checks, report provenance and native browser evidence.

- Verify Advanced Lab 29's native UCX transfers without requiring CUDA kernels.
  Require all fixed-work writes, target notification and validation activity,
  both observed process roles, and matching native browser review. Preserve
  unprofiled measurements and the kernel requirements of other labs.

- Accept explicit empty numeric-check lists for course qualification evidence
  with no quantitative metrics, while preserving rendered correctness checks
  and complete numeric coverage for measured labs.

- Remove the `run-labs` blanket blocker for provisioned or foreign-owner
  dashboard metadata. Reuse campaign authorization for identified course
  dashboards, preserving UID/folder scope and CLI checks, and record the
  possibility of later replacement by file provisioning.

- Resolve the course checkout from source and project-installed `run-labs`
  layouts. Give Inference Lab 30 an owned OpenAI server launcher that uses the
  prepared immutable vLLM image and cached model within the client's allocation,
  with bounded readiness and cleanup; preserve its separate Triton route.

- Document project-scoped `run-labs` installation from `courses/`, with a
  non-interactive Codex command that skips the optional `find-skills` offer,
  and guidance to omit global installation flags.

- Clarify course lab submission from the Slurm login node, execution on allocated
  compute/GPU nodes, and the remote per-course results path, log creation timing,
  and owner-only directory permissions. Present environment setup and syncing
  before the lab execution instructions. Place the optional `run-labs` AI agent
  workflow near the end and explain its use for result comparison, with
  Ownership and license as the final README section.

- Put Soperator first across the course catalog, learning path, navigation and
  READMEs, followed by GPU Fundamentals, GPU Performance Optimization, LLM Training,
  LLM Inference, Custom CUDA Kernels and Advanced Labs. Preserve technical
  prerequisites and existing lesson/lab identities.

- Add `courses/build-courses.sh` to rebuild the catalog and every course, then
  verify generated HTML against its sources, from any working directory.
  Separate checking with a cyan heading and green current/red failed statuses,
  retaining plain redirected output and stopping at the first failed check.

- Disable Nsight's extra per-process CUDA-profiler-stop flush for owned Dynamo server captures, retaining collection-end flushing and the acknowledged controls/report-content checks. Allow up to 600 seconds for stop acknowledgments so LARGE report serialization can finish before engine cleanup, while retaining the 90-second start timeout and failing on a missing acknowledgment.

- Align advanced lab guidance, investigation paragraphs, and recipe questions with Grafana worker/GPU-index selectors and bounded distributed-report browsing; distinguish short-workload telemetry sampling from kernel evidence.

- Bound Dynamo CUDA captures with acknowledged worker profiler controls after readiness and before engine shutdown. Retain both reports while servers remain alive, then use bounded forwarded interrupts for cleanup. Missing reports fail the lab; request-phase GPU content and profiler warnings require separate review.

- Enable vLLM batch invariance and explicitly select the RMSNorm custom operation so compilation preserves its batch-invariant CUDA dispatch. Select FlashAttention 2 to avoid the nondeterministic Hopper FA3 path and disable the nondeterministic fused all-reduce/RMSNorm pass for both Dynamo workers in every serving layout, retaining compilation and CUDA graphs. Keep seeded requests and strict output equivalence fixed across concurrency and routing changes; document the reproducibility/performance trade-off.

- Match the training and inference readiness publication invariants to their recorded world size and distinct host count. Reject changed or missing placement fields and align both lab guides.

- Name the DDP/FSDP peak-memory panels by quantity so their titles remain accurate when Grafana scales the converted byte values.

- Select the goodput lab's AIPerf tokenizer by its pinned repository ID and revision in the complete offline cache. Reject an unrelated snapshot and preserve the parent environment; align setup and wrapper requirements with AIPerf 0.12's cache resolver.

- Retain Dynamo client request epoch bounds and the measured cohort window after warmup, while keeping latency on a monotonic clock. Align Labs 32–33's profiler correlation instructions and test that wall-clock offsets cannot change the reported latency.
- Disable unused dataset attention-mask generation in the Megatron Bridge labs. The causal attention backend already supplies masking; avoiding dense prefetched masks prevents long-sequence DataLoader shared-memory exhaustion while preserving the seeded workload.

- Build Advanced Lab 00's Dynamo NIXL and NIXL-EP package together against its prepared native UCX/RDMA stack. Check both extensions, reject bundled UCX references, verify installed hashes and preserve runtime package versions; keep standalone NIXLBench isolated and retain full serving qualification as a separate gate.

- Correct token, sample and request throughput units across twelve course dashboards and their lab guides. Render Grafana count units as readable measurement labels in the shared result inspector.

- Align the transpose lab's worked edge case and lesson practice with its supplied rectangular shapes; the smoke executable uses 1003×1020.

- Fix the chunked-prefill comparison to use batch-invariant eager execution and a fixed attention backend for both policies and phases. Preserve strict output equivalence and align the lesson, guide and README with the measured execution mode.

- Document additional host memory for the offline vLLM Compute capture after a resource-only replay resolved its memory failure. Align the streaming guide and course lesson with the implemented closed-loop client, median/p90 latency and request-throughput metrics.

- Fix the prefix-cache campaign's server configuration to batch-invariant execution with `TRITON_ATTN` across both policies and phases. Preserve the exact greedy-output gate and add launcher coverage proving failed equivalence prevents measurement; align the lesson and lab guide.

- Add both diagnostic capture commands to the Bridge and Dynamo comparison guides and align their observability questions. Distinguish disaggregated cache-transfer evidence from aggregated serving.

- Select the sequential dataloader explicitly in both Bridge training labs, fixing the pinned runtime's rejection of an unset loader type. Keep the seeded dataset identical across communication variants and align both lab guides.

- Launch Bridge's distributed module through its selected Python interpreter, supporting inherited base PyTorch without assuming a venv-local `torchrun` script. Add launcher readiness and argument-preservation regression coverage; align setup guidance.

- Resolve the allocated worker's IP for NIXL/Dynamo's etcd listener, preserving hostname-based client discovery. Add a regression for etcd's bind-address validation and align Lab 00 prerequisites.

- Space and angle dense indexed Grafana axis labels without dropping measurements. Leave skipped case ticks blank while preserving missing-data warnings for measurements; keep every case available through tooltips and the transformed data inspector.

- Export perftest's installed library directory from the shared fabric environment and source it in Advanced Lab 00, enabling Lab 07's CUDA data-validation plugin. Align the PCI prerequisite check across all standalone helper copies.

- Sort indexed Grafana comparison cases numerically after joining the two slots, preserving artifact order for message-size and directed-pair sweeps without changing published labels, values or units.

- Retain Advanced Lab 10's profiler output per rank and combine completed streams before strict parsing, preventing concurrent Nsight progress text from corrupting the NCCL benchmark table. Preserve failures for missing logs, size limits and runtime warnings.

- Bind Advanced Lab 10's NCCL Tests child to visible CUDA device zero and permit step-local GPU peer access within its Slurm allocation. This prevents invalid MPI device selection and cgroup rejection of CUDA IPC imports. Align the guide and one-/two-node launch regressions.

- Bound Racecheck's analysis workers by the Slurm CPU allocation and limit queued launches to two, preventing tracking-memory exhaustion without filtering the CUDA lab's kernels or hazard checks. Keep sanitizer timing separate from performance evidence.

- Reject non-text Triton generation responses in Inference Lab 30. Align its guide with the response check, backend dependency qualification, and protocol-only troubleshooting scope.

- Clarify Advanced Lab 14's PyTorch trace: preallocated tensors may produce no allocation events, while operator shapes and CUDA activity remain available for framework attribution.

- Keep Advanced Lab 24's custom result directory under `results/` so its documented inspection command finds the completed batch-one experiment.

- Correct Inference Lab 15's concurrency comparison to use the streaming launcher's supported positional arguments, with fixed model, request count and revision. Distinguish the separate AIPerf campaign's fixed concurrency.

- Align prefix-cache guidance with its separate fresh-server correctness and cohort-measurement phases and matched policy pairs. Describe KV-tiering results as deterministic policy comparisons rather than timing repetitions, and keep its execution and troubleshooting guidance specific to the CPU model.

- Correct shared lab publication guidance: baseline/candidate slots may differ by one declared control; unchanged controls represent repeated qualification. Preserve lab-specific policy pairing and modeled-result limits.

- Export the chunked-prefill launcher's validated run ID so both policy clients, server processes and the campaign directory share one experiment identity. Add generated-ID and caller-supplied-ID handoff regressions. Clarify that each policy runs three fresh-server correctness probes and three fresh-server AIPerf trials.

- Identify node CPU and memory series by their node-exporter instance in every course dashboard, preserving worker and local-GPU labels on device panels. Correct the capstone guides' publication sentence boundary.

- Correct Inference Labs 33 and 34's profiler guidance to use native AIPerf exports for latency and throughput, while their Grafana panels show paired greedy-equivalence evidence. Align the recipes, guides and course README.

- Correct advanced Lab 00's fabric prerequisites: include PCI development headers and `libpci`, remove the stale Boost requirement, and check PCI compilation/linking before cloning tools or creating a partial build. Respect owner-supplied compiler and library flags.

- Direct CUDA, optimization, communication-vendor and Dynamo Systems launches now discard process environment metadata and exclude inherited remote Debuginfod downloads, matching the shared profiler while preserving workload inputs and local symbol resolution.

- Name NIXL Lab 29's recorded hash `launcher_sha256` to reflect the selected command file, and distinguish that wrapper identity from the qualified native benchmark build.

- Explain host capacity, worker container limits and Slurm allocations separately in the Soperator course, including why job flags cannot enlarge the worker's Kubernetes resource budget.

- Clarify Inference Lab 30's server lifecycle: its Triton launcher owns startup and cleanup, while the HTTP preflight has no server GPU capture contract. Keep the guide, profiler metadata and dashboard explanation consistent, and document the exact server-log directory and submitted-job results check.

- Inference Lab 30's optional AIPerf handoff uses an executable help command and the Lab 15 identity that owns its logs and captures. The launcher prints its native export directory, and the guide distinguishes those exports from the streaming client's dashboard results.

- Advanced NCCL guides, profiling preflight help and optimization-course handoffs use the current lab identities and rank counts; Lab 10 selects NCCL Tests variants through the launcher's positional argument.

- Offline vLLM profiling explicitly keeps the engine in-process and selects measured generation, so parent NVTX filters can capture its GPU launches without changing clean throughput defaults.

- NCCL sweep guidance sets Soperator's job-level SPANK log control for clean timing runs, because the plugin can override `NCCL_DEBUG` at the worker step.

- Course capture helpers reject missing or empty Nsight reports even when the profiler exits successfully, while preserving profiler failure codes.

- Course profiling instructions distinguish first-launch collection checks from representative workload kernels selected through Systems and `COURSE_PROFILE_KERNEL`.

- Distributed lab helpers bind NCCL to the local GPU before the first barrier, preventing collective stalls when global rank differs from the local device index.

- Course dashboards resolve selected timestamps at wall time before converting to milliseconds, avoiding empty experiment-date panels.

- Offline vLLM guidance compares both eager-flag settings across repeated trials instead of requesting an impossible third boolean value.

- GPU course Systems captures retain local symbol resolution while excluding inherited remote Debuginfod downloads that stalled report completion.

- Systems captures discard process environments instead of embedding potentially sensitive variables in profiler reports.

- Profiler readiness preserves Compute warnings while locating the raw counter table, avoiding a false empty-report result in read-only containers.
- Advanced communication setup explicitly installs NumPy, required by PyTorch object collectives when gathering distributed evidence.
- Course dashboards apply units only to measured values, preserve numeric case labels, and show selected-result metadata without infrastructure-label clutter. Optimization Lab 07 now keeps external Nsight captures separate from its internal PyTorch trace.
- Python course setup selects its own cuDNN sublibraries before profiling to avoid loading incompatible host copies.
- Course setup can extend an accepted private Grafana with an owned results bridge while preserving its existing catalog and data sources; setup receipts resolve namespaces and browser access.

### Changed

- Capstone aggregators accept the supported observed GPU families, preserve actual hardware in their scoped claims, and continue rejecting mixed trial environments.

- Unified H100/H200 runtime admission with observed GPU identity,
  retaining H100 teaching text and full-device/fabric checks. Base labs may
  reuse sixteen-GPU clusters; monitoring verifies their complete device inventory.
  Profiler readiness records installed versions and requires matching canary
  kernels, NVTX ranges and counters.

- Corrected course GPU dashboards and monitoring readiness to identify devices
  by worker and GPU index, matching Soperator telemetry that omits UUID labels.
  Keep sampled telemetry and live hardware qualification separate.

- Standardized all seven courses with contextual Practice instructions, explicit
  previews and revisits, clearer advanced definitions and worked examples, and
  five concept-specific diagrams. Preserve lesson/lab identities, executable
  sources and dashboard assignments; validate authored Practice context and
  exact guide ownership in generated and standalone courses.

- Audited all 110 executable GPU-course labs for Nsight Systems and assigned
  Grafana dashboards. Added 15 worker, rank, vendor and server capture recipes,
  repaired the moved NCCL launcher, documented 11 profiling exceptions, and
  removed unrelated GPU telemetry from CPU-only dashboards.

- Aligned all seven learning courses with canonical titles, direct lesson and lab
  contents links, consistent Practice subheadings and accessible mobile reflow.
  Corrected references to moved advanced labs and connected the inference
  curriculum to its Dynamo activities.

- Added Git-root Codex and Claude skill marketplace catalogs pointing to the
  existing `skills/` project, with native manifests and installation guidance.

- Added a static repository welcome page and a seven-course learning catalog,
  including the text-only Soperator introduction and dedicated advanced
  multi-GPU communication labs, with direct course switching
  and embedded Apache-2.0 notices. Course synchronization includes packages
  without labs. Prepared
  GitHub Pages publication from the root of `main` using `.nojekyll`, without
  a custom deployment workflow; documented initial activation and deployment
  verification. Course generation and checks remain local.
- Added path-scoped CI for the reusable project-spec lifecycle and its ordinary
  session, Task Implementer, Agentic SDLC, hook installer, and project
  instruction adapters.
- Updated the root README description from Nebius Public Services to Nebius
  Platform Services and aligned it with the repository's reusable AI/ML
  deployment building-block focus.
- Helm chart publication validation now lints charts with subcharts after
  resolving chart dependencies and can run chart-specific strict lint and
  smoke render combinations.
- Helm chart publication now seeds a temporary Helm repository config from
  remote `Chart.yaml` dependencies before building locked dependencies on
  clean runners.
- Retired the downstream Soperator Helm chart family, its upstream verifier
  workflow, and its Helm publication entry now that cxcli consumes official
  upstream Soperator release artifacts directly.
- Reorganized the root README and changelog around monorepo ownership: root
  docs now describe repository layout and cross-project policy, while
  project-specific release notes live in project-local changelogs. The root
  README now avoids enumerating growing project names or every project-local
  changelog link.
- Added `skills/CHANGELOG.md` and moved reusable-skill release notes out of the
  root changelog.
- Expanded the repo-level Dependabot auto-merge policy so
  Dependabot-authored semver `uv` and `pip` dependency bumps can be
  auto-approved and auto-merged when every changed file is limited to Python
  dependency manifests or lockfiles, while source-code edits and other
  non-dependency file changes remain ineligible.
