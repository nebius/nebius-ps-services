# GPU profiling and lab validation

## Completed H200 campaign: 2026-09-25

All 110 practical labs completed both workload profiles, producing **220 verified exports** across the six practical courses. Soperator remains a text-only course. The final all-catalog audit found no missing profiles and checked export inventories and hashes, exact ZIP contents, completed controller stages, original verification results and receipts for the visually reviewed headless-browser screenshots.

| Course | Labs | Verified profiles | Results |
| --- | ---: | ---: | --- |
| GPU Fundamentals | 11 | 22 | [Results](../gpu-fundamentals/reference/lab-results/) |
| GPU Optimizations | 14 | 28 | [Results](../gpu-optimizations/reference/lab-results/) |
| LLM Training | 16 | 32 | [Results](../llm-training/reference/lab-results/) |
| LLM Inference | 22 | 44 | [Results](../llm-inference/reference/lab-results/) |
| Custom CUDA Kernels | 13 | 26 | [Results](../custom-cuda-kernels/reference/lab-results/) |
| Advanced GPU Communication | 34 | 68 | [Results](../advanced-gpu-communication/reference/lab-results/) |

Each result retains its recorded source identity and prepared-environment evidence. Applicable native Nsight reports were independently checked and their real viewer screenshots reviewed; Grafana captures were reviewed against the corresponding local metrics. CPU-only and modeling exercises retain their declared limits. A successful verification is not a claim that every proposed optimization improved performance.

The observed tool/viewer pairs were Nsight Systems 2026.4.1 and Nsight Compute 2026.2.1. Metrics remained local to VictoriaMetrics/Grafana. The final Inference Lab 34 completed three counterbalanced pairs per profile with independent correctness and native-content checks. Its reports retain collection warnings, including incomplete NVTX/CUDA/OS collection warnings; acceptance of the required evidence does not establish that every event was captured.

These are prepared H200 experiment results, not clean-install qualification, H100 performance claims, or acceptance of every optional extension. Per-course `VERSIONS.md` files separate observed environments from installation candidates and remaining H100 checks. The [all-lab profiling audit](lab-profiling-audit.md) defines profiler applicability, dashboard assignments and source coverage.

## Historical integration checkpoints

The sections below retain their original source tests, partial campaign results, failures and pending gates. They describe those earlier checkpoints; the completed campaign above is the current result inventory. Historical failures are not rewritten as passing trials.

## Throughput unit repair: 2026-09-20

Live Grafana review found generic `ops/s` labels on measurements that count
tokens, samples or requests. Fourteen metric recipes across twelve dashboards
now use quantity-specific units, and their lab guides name the same quantities.
Grafana's count formatter preserves readable K/M scaling for tokens and samples;
request rates use its request-rate formatter. Published values, scale factors,
queries, experiment selections and dashboard identities are unchanged.
The shared result inspector prints human-readable throughput units instead of
Grafana format identifiers. All 53 focused profiling and observability tests
pass. Public render and ordinary-app apply installed the correction together
with four previously aligned capture descriptions; independent ConfigMap and
authenticated Grafana API checks matched all 75 dashboards in those three course
catalogs. Worker and viewer pod identities and resources remained unchanged.
Playwright matched 224 values across 14 actual comparisons on ten affected
dashboards. Direct review of 16 throughput-panel images confirmed readable
quantity-specific units. The remaining two affected dashboards still need their
completed workload comparisons; installation proof does not replace them.

## Chunked-prefill execution controls: 2026-09-20

The pinned engine changed two of four greedy responses between full and chunked
prefill even with batch-invariant execution and `TRITON_ATTN`. A controlled
replay changed only eager execution for both policies and preserved all four
response digests. Both server logs confirmed the same model revision, BF16,
attention backend and eight successful probe requests across the pair.

The launcher now selects batch-invariant execution, `TRITON_ATTN`, and
`--enforce-eager` for every correctness and AIPerf server. The original strict
digest gate remains. This isolates the repair to the compiled/graph execution
boundary; the exact internal compiler transform is not established. The full
three-pair course replay and matching profiler/dashboard qualification remain
pending. Timings from eager serving do not establish compiled-serving performance.

## Offline capture memory and streaming scope: 2026-09-20

The offline vLLM Compute capture completed warmup but exhausted its 64 GiB
host-memory allocation during kernel replay. A fresh trial changed only the
Slurm memory request to 256 GiB and completed successfully. The profiler's log
explicitly records device-memory backup in system memory. Lab 10 now requests
that allocation for its separate Compute command; the model, GPU count and
generation workload remain fixed. Report-content and viewer validation remain
separate from this resource repair.

The streaming guide and course lesson now describe the implemented finite
closed-loop client and its median/p90 latency, chunk gaps and requests/s.
The earlier instruction to switch to open-loop overload was unsupported by
this launcher. Token throughput, p95 latency and SLO goodput belong to separately
declared measurements and are not implied by this client's results.

## Indexed dashboard ordering repair: 2026-09-20

Live NCCL comparisons displayed all expected values but ordered the case labels
lexically (`value-0`, `value-1`, `value-10`, ...). That distorted the visual message-size
sweep. The shared generator now extracts the positional index, sorts it numerically
after joining baseline and candidate, and removes the helper field from the chart.
Explicit numeric case labels use numeric sorting and retain string display labels.
Scalar panels, published labels, metric values and units remain unchanged.
Normal cxcli render/apply deployed the ordering change to 43 panels in 17
dashboards; independent checks confirmed the five affected ConfigMaps and all
99 contained dashboards in the installed Grafana API. Other application
resources remained unchanged. Playwright downloaded transformed panel data for
two real NCCL comparisons: all six charts retained their 288 original measured
values and ordered all 24 cases numerically. Browser screenshots confirm the
visible order. This verifies those comparisons; it does not establish runtime
qualification of every affected dashboard or H100 performance.

The 56-pair NVLink chart subsequently exposed overlapping axis labels despite
correct values and case order. Indexed charts now request Grafana's small
minimum label spacing (100 pixels), which skips crowded tick labels while
retaining every bar, tooltip and data row. The first live spacing replay exposed
a second issue: Grafana formatted skipped ticks with the shared missing-data
message, leaving the axis crowded. The case field now leaves those placeholders
blank, while numeric measurements retain their missing-data warning. Angled
labels also prevent clipping at the panel edge. After the browser-only
diagnostic, normal render/apply deployed the repair. Independent checks matched
all 99 affected-catalog dashboards to the installed API, including all 43 changed
panels. An unmodified browser replay displayed readable labels, retained all
56 original cases in the transformed CSV, and matched 430 NVLink/NCCL values.
Worker and profiler resources remained unchanged. This validates these views;
other affected dashboards still need their own workload comparisons.

## Vendor coordinator bind repair: 2026-09-20

The first NIXL trial stopped before benchmark traffic because etcd 3.6 rejected
the worker hostname in its client listener URL. The shared NIXL/Dynamo helper
now resolves the first worker's IPv4 address for binding and retains the
hostname for advertised client access and readiness. A regression fails on the
original command and passes on the corrected bind/advertise split. Fresh NIXL
runs reached the ready coordinator and completed both progress-thread modes.
Their UCX InfiniBand plugins separately exposed a userspace ABI mismatch, so
those runs do not qualify the intended InfiniBand transport. That runtime
prerequisite remains separate from the verified coordinator startup repair.

Fresh trials with matching UCX/RDMA userspace libraries pass both payload
profiles, three clean comparisons per profile and the original consistency
checks. The fixed transport configuration excludes TCP. Both endpoint reports
are inspected in the installed Systems viewer: each sender contains the fifty
warmup and five hundred measured UCX writes and completion events at the exact
payload size, while the passive receiver records setup without sender-side
submissions. Full UCX event tables match the original exports, and the installed
Grafana comparisons match the clean results. The vendor's RDMA-memory capability
warning is retained; these checks do not qualify that capability bit, peak
performance, cross-host clocks or complete trace collection.

## Bridge distributed launcher repair: 2026-09-20

The Bridge runtime inherited PyTorch from its prepared base, but its environment
selected a `torchrun` script that did not exist inside the Bridge virtualenv.
Imports and worker fabric checks passed before that launch failed. The course
now invokes `torch.distributed.run` through the selected Bridge Python, and the
installer checks the module's help command. A regression reproduces the missing
console script and verifies interpreter selection and argument preservation,
including paths with spaces. A fresh two-worker launch starts all sixteen
Python ranks through this entry point. After giving the private read-only
container fixture a writable `FLASHINFER_WORKSPACE_BASE`, all sixteen ranks
pass imports, CUDA/NCCL all-reduce and profiler-availability checks.
`XDG_CACHE_HOME` alone does not select FlashInfer's workspace. The fixture also
keeps container startup diagnostics on stderr so Python's stdout remains usable
for the course's run-ID generation. These fixture repairs are separate from the
public launcher repair; training progress is recorded below, while report
validation remains pending.

## Prefix-cache execution configuration: 2026-09-20

The first cache-disabled/enabled correctness pair disagreed even with batch
invariance enabled under the selected FlashAttention path. A controlled
attention-backend counterfactual using `TRITON_ATTN` produced identical digests
for all four unchanged greedy requests. Lab 20 now selects batch invariance and
that backend explicitly for both policies and both phases. Behavioral launcher
tests exercise the complete counterbalanced command sequence and confirm that
a digest mismatch prevents every measurement phase. The complete fresh-server
workflow now passes all three correctness pairs and all three measured pairs.
Independent inspection also confirms equality of all sixteen measured responses
per policy, actual server request counts and positive cache-hit counters only
for the enabled policy. Profiler captures and dashboard comparisons remain
separate qualification steps; these results do not establish an H100 speedup.

## Paired diagnostic capture guidance: 2026-09-20

Advanced Labs 30–33 compare two communication or serving settings, but their
guides previously showed only the baseline capture command. Both commands are
now explicit and must run sequentially with the same workload and reference
where applicable. The Dynamo disaggregation guide assigns cache-transfer
inspection to the disaggregated capture. These command and text changes do not
qualify a report that has not yet been collected and inspected.

## Bridge dataset configuration repair: 2026-09-20

With runtime imports and JIT compilation available, the reference training job
reached data-loader construction and the pinned Bridge runtime rejected its
unset loader type. Both lab recipes now explicitly select the sequential
`single` loader. Four regression cases cover both workload profiles and both
communication experiments, including identical dataset settings across each
comparison. A fresh sixteen-rank reference and the first overlap-off/on smoke
pair each complete all twenty-five training steps without skipped or non-finite
iterations. Independent inspection compares all 16,804,864 saved parameters
against the fixed reference and confirms the twenty measured timings against
the original TensorBoard scalars. This establishes the first clean overlap
pair; repeated trials, the larger workload, context parallelism, profiler
content and dashboard validation remain separate qualification steps.

## Fabric validation plugin repair: 2026-09-20

The host-memory RDMA bandwidth trial passed, but the CUDA-memory trial could not
load perftest's installed validation plugin. The shared installer now exports its
library directory in the fabric environment, and Lab 00 explicitly sources that
file before submission. Three regression cases cover unset, empty and existing
library paths, including a prefix with spaces and quotes. Fresh CUDA DMA-BUF
trials pass with the original payload-validation checks enabled. The PCI
prerequisite guard also matches the canonical helper and all standalone copies.
Installer source tests, qualified build fixtures, completed workload trials and
dashboard validation remain separate evidence. Fifteen fresh host/GPU bandwidth
and read-latency trials passed independent vendor-content checks. Three published
comparisons covered registration mode, transmit depth and read latency;
Playwright matched all 36 dashboard values, and screenshots confirmed their
units, selected jobs and experiment windows. This qualifies those comparisons,
not every optional registration backend or H100 performance.

## Chunked-prefill run identity repair: 2026-09-20

Live Lab 34 artifacts had different run IDs from their campaign directory.
The launcher generated its ID without exporting it, so each child client
generated another ID. The launcher now exports the validated ID before
starting either policy. A regression executes the real shell handoff and both
clients with fixture HTTP: generated IDs failed before the repair, and both
generated and caller-supplied IDs pass afterward. This restores shared
experiment identity; a fresh GPU replay is pending. The independently observed
cross-policy greedy-output mismatch remains unresolved and is not covered by
this repair.

## Node context legend repair: 2026-09-20

Live capstone dashboards exposed duplicate `GPU` legends on node CPU and memory
panels. Their queries return `instance`, while the shared panel helper applied
DCGM's `Hostname` and `gpu` template. The generator now selects the node-exporter
instance for these two node panels, preserving worker and local-GPU identity on
device panels. A catalog-wide regression checks both label contracts. This
restores the existing telemetry identity contract. The ordinary cxcli render
and Flux apply deployed all 116 corrected dashboards; Grafana's API returned
the expected 232 node legend fields without query or GPU-label changes.
Playwright inspection of both live capstone dashboards confirmed distinct node
legends, and all 36 published artifact values matched the saved lab results.
Worker resources and identities remained unchanged. This qualifies the legend
repair and these two comparisons, not every dashboard's workload results.

## Live telemetry label repair: 2026-09-19

A live Soperator exporter supplied sixteen GPU-utilization series with
`Hostname` and `gpu` labels and no `UUID` label. Grouping by the absent UUID
collapsed those series into one; the dashboard UUID selector had no values.
The canonical generator now selects a worker and its local GPU index, and
monitoring counts fresh, nonempty worker/index pairs. The corrected query
returns sixteen distinct devices on the same target. This repairs telemetry
identity; course hardware admission and workload qualification remain separate.

The available target uses H200 GPUs. The original guards rejected those devices;
the user approved functional validation on this target while retaining H100
teaching text. The same code now admits full SM90 H100 and H200 devices and
records their observed family, retaining allocation, MIG and fabric checks.
H200 results do not qualify H100-specific performance claims.

Installed Systems 2026.4.1 and Compute 2026.2.1 now qualify through executed
canaries instead of equality with manual-installer candidate versions. Native
CUDA canaries passed on both workers. A fresh two-worker PyTorch replay also
passed exact kernel-symbol, counter and NVTX checks. Its initial comparison
failed because the tools simplify display names differently; comparing their
raw mangled symbols fixes that mismatch without weakening kernel identity.
Viewing these actual readiness reports remains separate from the earlier
independent fixture viewer proof.

A read-only CUDA container exposed a second readiness parsing defect: Compute
printed section-directory warnings before its CSV header. The report contained
the expected kernel and a positive duration, but the parser treated a warning as
the header. Readiness now identifies the raw counter table and rejects malformed
capture rows while retaining the full warning output. A fresh CUDA replay on
both workers passed matching kernel-symbol, NVTX and positive-counter checks
with the repaired parser; browser inspection of those reports remains separate.

An independent native CUDA fixture captured six kernel launches in Systems and
one selected launch in Compute. Authenticated Playwright sessions opened both
reports: Systems displayed the expected `validation_kernel` NVTX event, and
Compute displayed that kernel's H200 counters. Systems reported one collected
NVTX event, matching the fixture, alongside a warning that NVTX collection may
be incomplete and an informational software-tracing fallback. Compute warned
about unlocked clocks. This proves basic capture and viewing, not complete
course traces or performance results. Browser input readiness, case-sensitive
path entry and ownership of the private fixture directory were corrected in
the validation harness; they are not lab-code fixes.

All six documented inference Lab 36 CPU-model variants passed their ownership
and capacity checks. CPU function checks also passed the five Lab 27 allocator
invariants and Lab 28's equal-work and request-completion checks. Those function
checks do not qualify the full H100-gated command recipes or result publication.

The dashboard deployment exposed the client-side apply annotation limit in
cxcli. Its ordinary Flux apply now uses server-side apply with kubectl's default
field manager, preserving migration of existing client-side ownership without
forcing conflicts. A live server dry run accepted an enlarged existing
dashboard and omitted its oversized annotation; a custom-manager control
correctly conflicted. The repaired command created six ConfigMaps containing
all 116 dashboards. Grafana then required separate Flux recovery after its
earlier missing-ConfigMap rollout had stalled. That recovery is intervention,
not proof of an unattended end-to-end deployment repair.

All 116 deployed dashboards loaded in Playwright with 1,601 query responses,
no query errors, no blocking dialogs and the expected GPU selectors. Workload
result panels remain unqualified until the corresponding artifacts are published.

The initial live baseline campaign executed all eleven fundamentals and fourteen
optimization labs. It exposed an H100-only health correctness check, stale
capstone timing paths and a bottleneck-lab identity that included case/mode.
The health repair passed a fresh job. Corrected capstone mappings consumed the
existing live artifact successfully and now keep CUDA-event and wall-clock
timings separate. All eight bottleneck case/mode combinations now pass live workload and result inspection. Separate profiler and dashboard qualification continues.

At the initial telemetry checkpoint, the changed surface passed 191 focused tests
and all seven course validators. The apply change passes ten focused CLI checks and 55 ordinary-app
checks. Source checks, tool-fixture capture, dashboard rendering and actual
per-lab workload qualification remain separate evidence.

## Existing Grafana and runtime checks: 2026-09-19

Setup now extends an accepted private Grafana through its original catalog and
normal cxcli render/apply. It binds live and generated ownership, validates the
source against frozen render inputs, preserves existing credentials and data
sources, embeds verified dashboard JSON for relocation, and adds four owned
bridge objects. Actual rendering preserved all prior Grafana values. The normal
apply completed successfully; after operator reconciliation, independent checks
confirmed one healthy results scrape and sixteen fresh GPU series. Numeric
result publication and complete per-lab dashboard proof remain in progress.

A real fundamentals comparison published generation 1 and produced numeric
Grafana queries. Browser inspection exposed measurement units applied to numeric
case labels and date formatting applied to metadata labels. The generator now
scopes units to measured values and limits metadata tables to slots, cases and
values. Normal deployment preserved the other application resources. Fifty-one
published comparisons now pass browser checks against 830 expected numeric
values, selection generations and metadata fields, without query errors or NaN
values. This is a subset of the full lab matrix. Optimization Lab 07's generated
capture commands also combined mutually exclusive internal and external
profiling flags; corrected Systems and Compute commands both completed.

Six later comparisons exposed empty experiment-date panels despite valid
published timestamps. A same-request query comparison isolated the placement
of the wall-time modifier: applying it to the metric selector before converting
seconds to milliseconds returned both selected values. The generator now uses
that ordering. Normal cxcli deployment changed only the six course dashboard
ConfigMaps. All thirty-nine comparisons passed fresh Playwright checks, and
the six affected comparisons also passed with a historical telemetry window.

The first thirty LLM baseline jobs yielded twenty-nine successful, inspectable
results; the FP8 lab requires a separate Transformer Engine runtime. One Systems
capture exposed cuDNN sublibrary resolution against host copies. Selecting the
isolated runtime's cuDNN directory made the same capture complete. Python setup
now explicitly selects that directory, following NVIDIA's runtime library-path
requirements; container runtime qualification remains separate. See the
[NVIDIA cuDNN runtime guidance](https://docs.nvidia.com/deeplearning/cudnn/installation/latest/build-run-cudnn.html).

The first advanced topology run passed both workers' fabric checks but failed
when PyTorch's object collective converted a tensor to NumPy. The course
requirements omitted that dependency. NumPy is now explicit in the Python
3.12+ environment; a fresh sixteen-rank run with the same PyTorch version passed
execution and result inspection. Remaining advanced workloads and captures are
separate validation steps.

Two inference workloads completed their computations while Systems continued
waiting for symbol processing. Changing process-wait mode did not help. Fresh
captures with inherited Debuginfod downloads disabled completed both workloads
while retaining local symbol resolution. The capture helper and readiness now
exclude that optional network dependency. Systems also discards the process
environment from saved reports. Fresh course-launcher replays completed both
workloads and passed independent kernel and NVTX content inspection. This does
not qualify the remaining per-lab viewer checks.

A two-rank transport sweep passed fabric checks but stalled after its first
barrier. A bounded three-way replay isolated NCCL device selection: the unbound
barrier-first sequence stalled, while explicit local-device binding and an
unbound collective-first control both completed correctly. Shared initializers
now bind the local GPU at process-group creation. A fresh course-launched
two-rank sweep completed with exact results at all 18 tested message sizes;
fresh sixteen-rank layout and two-rank scaling runs also passed result inspection.
The sweep separately excluded its timing artifact, so its performance comparison
is not yet qualified.

The latest full offline suite passed 1,343 tests. The setup guides pass their
existing authored-length and requirement checks.
The existing-Grafana and monitoring regression suite passes 36 tests. These checks
do not establish unrun CUDA, fabric, serving or vendor-library qualification.

## Source-only launcher verification

The chunked-prefill launcher now brackets both correctness probes and AIPerf
trials with server-capture start and stop calls. A local Bash regression executes
the actual campaign branches with fixture commands: both AIPerf variants failed
before the repair, and all four phase/variant combinations pass afterward.
The focused profiling and launcher suite passes 228 tests. These checks establish
command ordering; actual vLLM/Nsight captures still require live qualification.

## Remaining-review repairs: 2026-09-18

Both capstone aggregators now require explicit Boolean
`experiment.instrumented: false` and reject records that exclude acceptance
timing. Invalid inputs fail before output; clean campaigns retain their
existing decisions. Negative controls reproduced the missing admission before
the repair, and all 86 focused capstone checks pass afterward.

Advanced Lab 19 compares FP32 gradients with absolute tolerance proportional
to each BF16 reference bucket's maximum magnitude, including an exact-zero
rule. A rank-wide MIN verdict precedes warmup and timing. CPU autograd checks
cover the actual 8 MiB bucket size, small valid perturbations, zeroed, inverted
and scaled corruptions, malformed buckets and one failing rank.

Its unnumbered single-H100 Compute companion shares the local bucket/loss
arithmetic, checks an independent BF16-cast-preserving derivative and captures
one selected backward kernel. The canonical profiler admits only its exact
Python/script prefix and declared range, rejects multi-rank execution, and
uses process-scoped NVTX, kernel replay and normal workload completion.
Actual Bash/Slurm/NCU fixture dispatch tests cover private receipts, workload
failure, empty/missing reports and command-policy bypass attempts. The kit
includes both companion and helper without changing the 34 numbered labs.

The initial combined focused run passed 360 tests. All seven validators,
shared-helper parity, dashboard parity, configured Markdown, changed Python
lint/format, Bash syntax and ShellCheck pass. A nested read-only code/security
review found no blocking source issue and independently checked helper and
embedded-kit parity.

The final available offline suite passed **1,252 tests**. The nine functions in
`tests/test_result_publication.py` were explicitly excluded because the
existing validation environment lacks the declared `prometheus-client`
dependency. No dependency was installed; those publication checks remain an
explicit validation gap.

These are CPU and fixture checks. A nonempty `.ncu-rep` alone does not prove
that the intended backward kernel was captured. Qualification still requires
the actual H100 reference check and inspection of a matching kernel report;
distributed overlap still requires correlated Systems traces. No native
runtime, live cluster, dependency installation or publication was performed.

## Implemented behavior

- Every practical course links to the shared README setup and run guide.
  Environment readiness is unnumbered; each course retains its tools lesson.
  Existing lesson and executable-lab identifiers remain stable.
- Every lab has a local measurement recipe, a dashboard JSON asset, exact
  a guided comparison and an independent investigation. Shared setup imports
  each course's dashboard directory through cxcli once.
  CPU models and qualification exercises identify their profiling limits.
- Completed-job inspection prints the measured values and exact artifact paths.
  Immutable JSON remains authoritative; publishing never occurs inside timing.
- Opt-in NVTX and separate Systems/Compute capture preserve clean measurement
  paths. Distributed reports use rank-specific identities; serving capture
  targets the GPU server while the client measures requests.
- The publisher checks workload invariants, declared controls, correctness,
  numerical equivalence where required, runtime identity and profile. It sends
  both selected slots together, serializes writers, rejects stale selections
  and requires fresh generation confirmation from the metrics backend.
- The private catalog reuses verified Soperator monitoring identities. It adds
  one CPU Pushgateway and one selected VMServiceScrape through Grafana
  `extraObjects`, without a public Gateway or another database.
- cxcli supports guarded ordinary application updates on Soperator, internal
  datasources without cloud authorization headers, and temporary loopback
  Grafana access. Protected state, target ownership, accepted generation and
  pending-operation checks remain enforced.

## Advanced-route revision: 2026-09-18

The ordinary Grafana component command now uses the private catalog selected once
by Lab 00. `CLUSTER_CONFIG`, `CLUSTER_TARGET` and `COURSE_TOOLS` are explicit.
Setup receipts are isolated by deployment and target. The shell guard checks the
catalog on every invocation, including after a failed environment load or a
change of directory. A local regression reproduced the failed-load bypass before
repair and now rejects it without invoking the CLI.

All submissions use a shared helper that creates private per-lab output/error
paths before Slurm runs. The launchers and their help agree with this interface;
existing home-directory logs remain untouched. All 18 existing distributed labs
move into advanced lesson groups while retaining their original IDs. Bounded
two-rank mechanics reserve full workers but keep their original algorithms.
New eight-/sixteen-rank launchers share a run identity and preserve rank-specific
captures.

Seven new executable labs add topology qualification, all 56 directed peer
copies, validated bidirectional host/GPU RDMA, collective hierarchy, distributed
PyTorch/Systems traces, fixed-global-batch DDP, and replicated/sharded generation.
The inference guide also supplies full sixteen-GPU vLLM layouts and server capture.
The educational projection model is not a production LLM benchmark; HTTP content
chunks from the streaming client are not individual token timing.

| Current check | Evidence and boundary |
| --- | --- |
| Courses suite | 965 passed in the full local run plus two loopback fixtures with socket permission (967 total); the final setup follow-up passed 45 tests. CPU and fake-service fixtures only. |
| Dashboard and package contracts | 101 lab dashboards plus five readiness dashboards; all five standalone validators, source/ZIP parity, units, datasource bindings, bounded query cases and conservative ConfigMap sizes pass. |
| New failure controls | Private log-path validation, missing/shadowed catalogs, failed-source continuation, MIG/restricted device visibility, peer matrix, active IB, rank placement, vendor validation logs and fixed-work numerical checks. |
| Vendor interfaces | Reviewed pinned NVIDIA nvbandwidth, NVIDIA NCCL Tests and linux-rdma/perftest source; user-space builds and real GPU-buffer registration remain unqualified. |
| Lint and review | Python formatting, ShellCheck and configured Markdown checks pass. Changed Python has no introduced Ruff findings; 21 existing test-style findings remain, versus 33 on the same starting files. Code, catalog/privacy boundaries, commands, hardware routing and numerical references were reviewed. |
| Browser | 15 artifact-bound Playwright Test checks passed in owned headless Chrome 153.0.8010.48 at 1440, 390 and 320 px. Advanced order, fragments, keyboard, downloads and kit membership pass. Five new diagrams were inspected. Setup and new lesson headings wrap with enlarged text; existing whole-page enlarged-text limits remain separate. |
| Skill checker | The same eight existing format findings occur on starting and final pages; exact executable-source membership and embedded bytes pass. |

No cxcli service source was changed in this revision. Its previously recorded
application-only deployment tests below are historical evidence, not a new live
or installed-CLI qualification. The qualification candidates and open live gates
remain explicit.

## Prior source and browser evidence

Validated on 2026-09-17 using local fixtures; no cloud resource was changed.

| Check | Result and boundary |
| --- | --- |
| Full courses suite | 947 tests passed across the full run and two loopback-fixture reruns with socket permission. The pinned CPU PyTorch runtime remains in use. |
| Focused cxcli suite | The implementation pass had 588 passing tests. The follow-up alignment passed 457 focused tests across onboarding, ordinary apps, application dispatch, deployment state/planning, bundle transactions, catalogs, observability and Grafana. |
| Dashboard contracts | All 99 JSON assets regenerate identically; UIDs, datasource bindings, missing-data descriptions, units and conservative ConfigMap size limits pass. |
| Publisher failure paths | Mismatches, concurrent selection, delayed retries, cache loss, failed ingestion, republishing and fresh generation readback pass with fixtures. |
| Application-only boundary | The public loader/dispatch test accepts an ordinary app update with infrastructure drift while Terraform and Slurm-maintenance entry points remain uncalled. Protected changes are rejected. |
| Private Grafana | Loopback forwarding cleanup, internal datasource authentication, credential boundaries and public-Gateway/private-Service distinction pass with fixtures. |
| Course packaging | All five HTML builds, template validators, dashboard downloads, embedded ZIP content and standalone shared-helper parity pass. |
| Browser checks | All 15 Playwright Test cases pass in owned isolated headless Chrome 153.0.8010.48 at 1440, 390 and 320 px: normal layout, fragments, keyboard TOC, command scrolling, dashboard downloads and standalone kits. Setup and the new primer fit 200% root text at 320 px. This does not test Grafana's live rendering. |
| Shell and whitespace | Syntax and ShellCheck pass for all 25 changed shell/Slurm files; scoped diff whitespace checks pass. |
| Python lint | cxcli changed Python files pass. Course changes introduce no Ruff findings; existing findings in previously authored code remain outside this change. |
| Installed course-skill checker | Eight existing format findings remain identical to the starting pages; no new finding remains. Source-list membership and embedded bytes pass. |

The follow-up alignment repaired three reproduced defects:

- Selected-result queries now evaluate at wall time, independently of an absolute
  historical dashboard window. All 99 dashboards retain historical GPU/node
  telemetry and current selected summaries; 94 guides and five primers explain
  that distinction. Ingestion after experiment end no longer places summaries
  outside their query time. This uses the existing VictoriaMetrics backend's
  [MetricsQL time modifier](https://docs.victoriametrics.com/victoriametrics/metricsql/),
  with wall-clock `now()` confirmed in the
  [1.114.0 implementation](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.114.0/app/vmselect/promql/transform.go).
  Source query contracts pass; actual query-engine and Grafana execution remain
  unverified. An isolated local engine download stalled and was stopped.
- Inference Lab 09 accepts its declared one-token versus eight-token workload
  study while retaining model, prompt and request invariants. Token/decode counts
  may differ only when the declared token control changes.
- Soperator onboarding publishes the ordinary-app baseline only after successful
  registration and binds it to the captured accepted generation. Regressions
  exercise real local generation capture, registration and baseline validation,
  plus failed registration and concurrent source edits, using a fake backend.

The alignment pass covered entry-point wiring, code review, security boundaries,
documentation and generated assets. It repaired incomplete CUDA-result
acceptance, percentile-field parsing, missing comparison invariants and result
inspection. Offline fixtures establish those source contracts; they do not
establish installed-wheel behavior, GPU correctness or deployed service health.

The broader publication gates remain open: whole-page overflow with 200% root
text is unchanged from the starting pages, and the eight existing installed
checker findings are unresolved. The final diagram has a registered home and
accessible figure markup; its introductory lesson does not renumber other
lessons. Per-course publication reviews record artifact hashes and browser
evidence. Screenshots/traces were retained privately and owned browser resources
were closed.

## Live qualification still required

cxcli owns the paired Nsight Systems and Nsight Compute installations and
browser viewers. Follow the [shared setup guide](../README.md#how-to-set-up-the-lab)
and complete environment readiness before declaring the environment ready:

1. Identify each designated non-production cluster config and target. Verify the
   base pair of one-H100 workers and the advanced pair of eight-H100 workers
   separately, including release-owned monitoring and exactly one results scrape.
2. Verify both profiler candidates on workers and each course container/runtime,
   including counter permissions and matching browser viewers. Record exact
   managed versions, driver/runtime identities and readable reports.
3. Apply/import the dashboards through cxcli, inspect private Grafana, and
   publish readiness results from distinct workers and GPUs. Confirm fresh
   metrics and publication generation through VictoriaMetrics.
4. Complete Fundamentals Lab 01 on the base cluster. On the advanced cluster,
   qualify Optimizations Labs 22–26, Training Lab 35 and Inference Lab 38, then
   representative distributed, training, inference-serving and CUDA labs. Retain unprofiled baseline/candidate JSON,
   correctness checks, diagnostic reports and the selected dashboard evidence.
5. Verify a repeated dashboard update against the deployed target while
   infrastructure drift exists, preserving the ordinary application boundary.

Acceptance requires interpretable measurements and valid comparisons. A
speedup is not required, and empty or stale data is never a readiness pass.

## Repeat source checks

```bash
python3 -m venv .venv-validation
.venv-validation/bin/pip install -r requirements-validation.txt
PYTHONDONTWRITEBYTECODE=1 .venv-validation/bin/python -m pytest tests -q -p no:cacheprovider
python3 tools/sync_course_tools.py --check
python3 tools/build_observability.py --check
python3 tools/build_course_html.py
```

Run these commands from `courses`. Tests that start local HTTP fixtures need
loopback networking permission. Live validation uses shared environment readiness
and each lab's recipes; it must not be replaced by this source test command.

### Representative Compute kernel selection

The first FP8 Compute report selected a random-initialization kernel inside
`lab_workload`. Its positive counters and matching Systems symbol prove collection,
not FP8 arithmetic behavior. Guides now require selecting an operation-relevant
kernel from Systems with `COURSE_PROFILE_KERNEL` before interpreting counters.
First-launch content checks remain distinct from representative-kernel qualification.

A live offline vLLM Compute trial exited zero after reporting that no kernels were
profiled and produced no report. The shared capture helper now requires a nonempty
report for every successful Systems or Compute run, records exit code 2 for a
missing or empty report, and preserves existing nonzero profiler exit codes.
Regression cases cover both tools, absent/empty/nonempty reports and failed
profiler processes. This repairs false success reporting; the vLLM process/NVTX
selection and representative-kernel qualification remain separate runtime checks.

The live NCCL sweep correctly rejected timing artifacts after the installed
Soperator SPANK plugin replaced the submitted `NCCL_DEBUG=WARN` with `INFO`.
An allocation-bound probe localized that change to `srun`, before Python imports.
The documented plugin control `SNCCLD_LOG_LEVEL=WARN` preserved warning-level
logging through `srun`, Bash and torchrun in a fresh probe. Lab 09 now explains
both diagnostic and timing submission settings; the prior verbose trials remain
excluded from acceptance timing.

Offline vLLM captures now explicitly select `--in-process` before engine initialization and annotate measured generation with `vllm_generate`. A controlled live probe established that moving the engine into the parent process permits NVTX-scoped Compute collection; its initialization kernel was diagnostic evidence only. The repaired recipe excludes construction and warmup, preserves the clean default process mode, and rejects a capture without the declared process setting. CPU regressions cover process selection, warmup exclusion, request accounting and early rejection. Fresh matching Systems/Compute operation reports remain required.
