# Publication review

Status: Public-safe reviewed. Static course review and browser-rendered layout
QA are complete; live cluster execution remains pending.

## Public-safety review

- The release contains only generic public course content and no non-public source metadata or environment-specific identifiers.
- Examples use synthetic inputs, generic environment variables, and repository-local files.
- External references are public official NVIDIA, PyTorch, Slurm, vLLM, and MLCommons documentation or project pages.
- Results, logs, profiler reports, environments, and caches are ignored.
- Runtime artifacts are classified as private by default; learner guidance
  requires a separately reviewed, sanitized summary before external sharing.
- Python bytecode and tool caches are excluded from and rejected by the
  portable course tree because they can retain local filesystem paths.
- The public topic map records coverage without retaining source names or non-public metadata.

## Technical review contract

- Every comparison declares workload equivalence and a correctness gate.
- Techniques are presented as conditional hypotheses, not guaranteed speedups.
- Profiler counters are paired with scope and interpretation limits.
- The HTML embeds canonical source for all labs, checked by `tools/validate_course.py`.
- Single-device and distributed launch semantics are distinct and explicit.
- The five primary bottleneck classes are separated from cross-cutting transfer and synchronization mechanisms and from capacity feasibility.
- Profiler acceptance uses unprofiled timing; instrumented Nsight durations are explanatory evidence only.
- The normal validator performs static checks and never executes lab or
  launcher source from a reviewed revision.
- The direct PyTorch requirement is not represented as an immutable lock;
  live execution requires a cluster-approved hash lock or image digest.

## Evidence still pending

Until the cluster is available, the project has no live evidence for:

- H100 device, CUDA, and PyTorch compatibility.
- The approved platform-specific dependency lock or immutable image digest.
- Site permission and availability for Nsight Systems or Nsight Compute.
- Compiler, graph-capture, SDPA, and checkpointing behavior under the pinned stack.
- NCCL initialization, topology, collective timing, or two-node scaling.
- CUDA allocator statistics and compute/communication overlap behavior on the pinned stack.
- Site-enabled DCGM/DCGM Exporter fields, `nvbandwidth` paths, NCCL Tests results, and serving-load-generator or standards-suite evidence.
- Nsight Systems and Nsight Compute artifacts for the four practical profiler cases.
- The numerical and performance results shown by the lab configurations.

The revised course was served from a temporary loopback HTTP server and reviewed
in the in-app browser at a 1280-pixel desktop width and a 390-pixel narrow
viewport. The review covered the simplified header, five-class bottleneck map,
tool-scope ladder, PyTorch/NVTX attribution, Nsight Systems examples, roofline
figure, tool tables, Lab 14, source listings, references, and footer. Desktop
rendering had no page overflow. At narrow width, diagrams, tables, and source
listings remained inside their own horizontal scrollers, and the browser emitted
no warnings or errors.

The validator separately checked the exact title, four-part hierarchy, 19
lesson headings and contents links, 15 complete lab cards, 16 embedded source
listings, 29 sequential official references, accessible SVG titles and arrow
markers, and canonical-source parity.

Run [reference/cluster-smoke-test.md](reference/cluster-smoke-test.md) and preserve its evidence before marking the course live-verified.
