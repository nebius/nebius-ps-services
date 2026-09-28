# Advanced Labs: Multi-GPUs Multi-Nodes communication optimization

[Open the complete course](index.html) · [Lab route](SYLLABUS.md) · [Setup](../README.md#how-to-set-up-the-lab) · [Environment candidates](VERSIONS.md)

Course seven contains **34 executable labs**, plus [shared environment setup](../README.md#how-to-set-up-the-lab). It owns the 25 distributed activities previously spread across four courses and adds nine experiments on network latency, adapter selection, NIXL, Megatron Bridge, hierarchical context parallelism, Dynamo and AIPerf goodput.

Use two eight-H100 Soperator workers with NVLink/NVSwitch and InfiniBand. The two one-GPU TCP workers used for the earlier local labs are insufficient for fabric qualification. Sixteen GPUs are allocated; two-rank mechanics deliberately use fewer participating GPUs. Every guide identifies the relevant evidence and a one-variable comparison.

Readiness Labs 03 and 04 publish the recorded world size and distinct host
count as comparison invariants. Both selected runs must preserve the two-rank,
two-host placement and pass the NCCL correctness checks.

NIXL and Dynamo's job-owned etcd coordinator binds the first worker's resolved
IPv4 address and advertises its hostname. Verify worker name resolution before the vendor installation below.

Dynamo's installer builds NIXL 1.3.2 and NIXL-EP together against the prepared
runtime's CUDA-aware UCX, selected by `DYNAMO_UCX_PREFIX`. The native package
checks preserve runtime versions and reject the bundled UCX dependency pattern
that can introduce a second library stack. This build remains separate from
standalone NIXLBench; both-worker readiness and full serving checks are required.

The serving launcher prepends the selected Dynamo interpreter's directory to
its child processes' `PATH`, preserving the inherited compiler and tool paths.
This lets FlashInfer find the prepared runtime's Ninja executable during kernel
compilation without requiring the submission shell to activate that venv.
After both workers become healthy, the launcher waits up to 600 seconds for
the frontend's asynchronous model discovery to list exactly `course-model`.
An empty list remains pending; an unexpected model or failed owned process
fails immediately. The private job log records the successful discovery check.

Dynamo workers enable vLLM batch invariance and the RMSNorm custom operation,
select FlashAttention 2, and disable
the nondeterministic fused all-reduce/RMSNorm compilation pass in every serving
lab. The pinned Hopper FlashAttention 3 path can violate batch invariance.
Explicit RMSNorm selection preserves its batch-invariant CUDA implementation
when compilation would otherwise select the native implementation.
Compilation and CUDA graphs remain enabled. Keep these settings fixed across concurrency and
routing comparisons; these settings can affect throughput. Labs 32 and 33
require matching outputs. Lab 34 instead compares identical transmitted requests
and fixed, server-verified generated-token work; answer text may vary. Missing
usage, failed requests and artifacts from its earlier contract are rejected.

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

Grafana GPU selectors use the worker hostname and its local GPU index. Match
those labels to the allocation record; UUIDs remain useful for physical-device
identity but are not dashboard selectors. Short small runs can fall between
telemetry samples, so a low utilization sample does not establish idle kernels.
For distributed traces, check every rank through exported statistics and inspect
representative reports from both workers. Open large reports in small groups.

Dynamo Labs 32 and 33 retain each measured request's Unix nanosecond start/end
timestamps in private `requests.json`, plus the measured cohort bounds in
`measurement-window.json`. Warmups precede that window. Latency still uses a
monotonic clock; correlate server traces only after checking worker clock
alignment, and keep instrumented timings separate from clean comparisons.

Lab 34 selects AIPerf's tokenizer by the pinned repository ID and revision,
using the complete Lab 32 cache snapshot in offline mode. Keep the snapshot's
metadata as well as its model files; runtime wrappers must pass the client's
`HF_HUB_CACHE`, `HF_HUB_OFFLINE` and `TRANSFORMERS_OFFLINE` settings through.

For NIXL, retain both the recorded launcher identity and the qualified native
executable's build identity; the supplied launcher is a library-path wrapper.

Bridge's distributed launcher invokes `torch.distributed.run` through
`COURSE_BRIDGE_PYTHON`. This also supports the prepared base's inherited PyTorch
installation, which need not create a `torchrun` executable in the Bridge venv.
Both training labs explicitly select its sequential `single` dataloader and
keep the seeded synthetic dataset fixed across communication variants.
The attention backend supplies causal masking. Dataset mask generation is
disabled as well as mask transfer, avoiding an unused quadratic mask in each
prefetched sample at the longer sequence length.

The fabric build in Lab 01 requires PCI development headers and `libpci` in addition
to the CUDA and RDMA development stack. The installer checks compilation and
linking before creating a partial build; see [Lab 01](reference/labs/01_fabric_topology.md) for
package names and owner-supplied compiler/library paths.
Source the generated fabric environment before submission so perftest can find
its installed CUDA data-validation plugin while preserving owner library paths.

DDP and FSDP memory dashboards convert the artifact’s MiB values to bytes; Grafana selects the displayed byte unit.

Every submission uses `tools/submit_lab.py`, which prepares private per-lab logs. Shared Nsight tools, private Grafana and publication helpers are installed once in shared environment setup. JSON artifacts remain authoritative; selected comparison metrics are a replaceable cache. `small` and `large` are workload-size profiles.

Keep custom output directories under `results/`, as in Lab 24's batch-one
example, so `tools/inspect_results.py` can find completed artifacts by job ID.

Lab 14's separate PyTorch trace shows framework operations, input shapes and
CUDA activity. Its preallocated tensors may produce no allocation events;
the trace is not a measurement of total GPU memory use.

Lab 10 selects its NCCL Tests variant as the positional argument after
`slurm/nccl_tests.sbatch`, for example `default` or `socket`.
Its runner sets `NCCL_TESTS_DEVICE=0` only for the benchmark child, matching
Slurm's one-GPU-per-task visibility. Inherited device overrides are rejected;
the MPI local rank does not identify a device inside that restricted view.
The step also uses `--gres-flags=allow-task-sharing` so tasks can exchange
GPU memory with peers inside the same allocation while retaining their
one-device CUDA binding. This does not grant access to another job's GPUs.
Profiled runs retain separate stdout/stderr files per rank beside the private
log, then combine them after `srun` exits. This prevents concurrent profiler
progress messages from corrupting the strict benchmark-table parser.

Install the complete `requirements.txt` in Python 3.12 or newer. NumPy is required
by PyTorch object collectives used to gather distributed evidence.
Distributed helpers bind NCCL to each rank's local GPU before the first barrier.
This matters when two-rank mechanics leave all eight GPUs visible on each worker:
global rank is not the local device index.

The new NVIDIA environments are isolated qualification candidates. Source validation does not prove sixteen-GPU runtime compatibility, network transport selection or performance. See [Publication review](PUBLICATION-REVIEW.md) for evidence lanes.

```bash
python3 tools/validate_course.py
```

The self-contained HTML embeds the full lab guides, source listings, dashboard downloads and a complete lab kit. It contains no separate conceptual lessons. Estimated guided time is 64 hours, excluding provisioning, queues and independent investigations.

Nsight Systems capture commands and report views are assigned per lab in `reference/observability.json` and repeated in each lab guide. Shared setup imports each course dashboard directory once; CPU-only and protocol-only results omit unrelated GPU telemetry. Explicit profiling exceptions explain which evidence to use instead. Keep captures separate from the unprofiled result pair. Labs 30–33 show separate diagnostic commands for both comparison settings; run them sequentially and retain both sets of reports.

[Lab 19](reference/labs/19_gradient_overlap.md) checks gradients relative to
each BF16 reference bucket's magnitude and reaches rank-wide correctness
consensus before timing. Its unnumbered Compute companion isolates one local
backward kernel on one H100 without collectives. Use its fixed NCU launcher
for diagnostic counters and Systems for the distributed schedule. The course
still has 34 numbered labs; native Compute qualification remains pending.

## Runtime preparation

After the shared Python/publishing setup, work from
`~/courses/advanced-gpu-communication`. Prepare fabric tools in
[Lab 01](reference/labs/01_fabric_topology.md) and MPI-enabled NCCL Tests in
[Lab 10](reference/labs/10_nccl_tests_report.md). Source
`"$COURSE_TOOLS/fabric/environment.sh"` before fabric submissions.

### Vendor runtime preparation

The existing installer prepares Bridge, NIXLBench, Dynamo and AIPerf together;
all prerequisites below are required even when preparing only one vendor lab.
Run it once, then reuse the installed environments.

For Labs 29–34, prepare isolated vendor environments once. The owner supplies Python 3.12, UV, CUDA 13 and a compatible driver (the generic CUDA 13 floor is 580.00.03), RDMA libraries, and a Bridge base interpreter with the training stack corresponding to NVIDIA PyTorch 26.06-py3. Bridge also needs its CUDA compiler and locked Transformer Engine build dependencies. Do not use an empty stock-Python environment as the Bridge base.

Dynamo needs CUDA 13 compiler tools and CUDA-aware UCX/RDMA development libraries selected by `DYNAMO_UCX_PREFIX`. Its joint NIXL/NIXL-EP build must use that same native stack.

For NIXLBench, the owner prepares Meson/Ninja, GCC 11+, CMake 3.20+, CUDA headers, GFlags, OpenMP, Asio, tomlplusplus, etcd-cpp-api with gRPC/Protobuf, a shared etcd executable, and a separate CUDA-aware UCX 1.22.0 installation (source commit `8a6b06fb880accbb933a79cda893883872c68d9d`). The install script checks CUDA and etcd features rather than accepting a partial build.

NIXL and Dynamo start a private, job-owned etcd process on the first allocated
worker. Worker names must resolve to their reachable IPv4 addresses. The helper
binds that worker's resolved IP, as required by etcd, and advertises its hostname
to clients. Readiness failure stops the experiment before benchmark traffic.

```bash
export BRIDGE_BASE_PYTHON='<absolute owner-prepared Python 3.12 path>'
export UCX_PREFIX='<absolute isolated UCX 1.22.0 prefix>'
export DYNAMO_UCX_PREFIX='<absolute UCX prefix matching the Dynamo CUDA/RDMA runtime>'
export COURSE_ETCD='<absolute shared etcd executable>'
bash env/install-vendor-candidates.sh
source env/vendor-environment.sh
```

The vendor scripts pin versions and record private receipts. Source `env/vendor-environment.sh` before network/serving labs to restore the base runtime after Bridge work. Keep standalone NIXL UCX separate from Dynamo's prepared stack. Candidate installation is not live qualification.

Save the vendor prerequisites without overwriting the existing Python settings:

```bash
declare -p COURSE_ETCD UCX_PREFIX DYNAMO_UCX_PREFIX >> "$HOME/courses/.runtime/$COURSE.sh"
```

Prepare the model in [Lab 32](reference/labs/32_dynamo_disaggregation.md) before
Dynamo Labs 32–34. Source `env/vendor-environment.sh` in each vendor submission shell.

## Learning order

Complete shared environment setup once. Keep these identities in filenames, dashboards and evidence. Mechanics labs intentionally reserve the fabric while using only the ranks their model requires.

### Qualify placement and fabric

- [Lab 01: Verify the GPU fabric and rank placement](reference/labs/01_fabric_topology.md)
- [Lab 02: Verify the two-node H100 platform](reference/labs/02_collective_readiness.md)
- [Lab 03: Verify the distributed training allocation](reference/labs/03_training_readiness.md)
- [Lab 04: Verify the two-node inference mechanics platform](reference/labs/04_inference_readiness.md)
- [Lab 05: Establish the optimization platform contract](reference/labs/05_transport_readiness.md)
- [Lab 06: Compare copy engines and SM peer traffic](reference/labs/06_nvlink_bandwidth.md)
- [Lab 07: Validate host and GPU memory over InfiniBand](reference/labs/07_rdma_bandwidth.md)

### Understand collectives and rank timelines

- [Lab 08: Measure a two-node NCCL all-reduce](reference/labs/08_distributed_collectives.md)
- [Lab 09: Measure a two-node NCCL message-size curve](reference/labs/09_nccl_transport_sweep.md)
- [Lab 10: Run and interpret NVIDIA NCCL Tests on Slurm](reference/labs/10_nccl_tests_report.md)
- [Lab 11: Compare flat and hierarchical all-reduce](reference/labs/11_collective_layout.md)
- [Lab 12: Compare one GPU with two-node DDP](reference/labs/12_distributed_scaling.md)
- [Lab 13: Test whether communication can overlap independent compute](reference/labs/13_collective_overlap.md)
- [Lab 14: Correlate PyTorch and Systems traces across ranks](reference/labs/14_distributed_profiling.md)

### Train with distributed state and communication

- [Lab 15: Train replicated models with DDP](reference/labs/15_ddp_train.md)
- [Lab 16: Observe FSDP2 sharded training state](reference/labs/16_fsdp2_train.md)
- [Lab 17: Trace expert routing, gradients, and grouped work](reference/labs/17_training_expert_parallel.md)
- [Lab 18: Partition a linear layer and verify its backward pass](reference/labs/18_training_tensor_parallel.md)
- [Lab 19: Reduce gradients when they become ready](reference/labs/19_gradient_overlap.md)
- [Lab 20: Follow pipeline and context partitions through backward](reference/labs/20_parallelism_mechanics.md)
- [Lab 21: Tune real DDP buckets and communication hooks](reference/labs/21_ddp_buckets.md)
- [Lab 22: Tune microbatching at fixed global batch](reference/labs/22_fabric_training.md)

### Inspect inference parallelism

- [Lab 23: Route inference tokens to expert owners](reference/labs/23_inference_expert_parallel.md)
- [Lab 24: Compare inference tensor-partition communication patterns](reference/labs/24_inference_tensor_parallel.md)
- [Lab 25: Compare request replicas with tensor-parallel decoding](reference/labs/25_fabric_inference.md)

### Isolate network latency and transport

- [Lab 26: Separate collective latency from payload throughput](reference/labs/26_collective_latency.md)
- [Lab 27: Measure GPU-memory RDMA read completion latency](reference/labs/27_rdma_latency.md)
- [Lab 28: Test NCCL network-adapter selection](reference/labs/28_nic_selection.md)
- [Lab 29: Measure NIXL GPU transfer progress](reference/labs/29_nixl_transfer.md)

### Apply current NVIDIA training and serving techniques

- [Lab 30: Overlap Megatron gradient communication](reference/labs/30_megatron_overlap.md)
- [Lab 31: Place hierarchical context-parallel communication](reference/labs/31_context_parallel.md)
- [Lab 32: Compare aggregated and disaggregated Dynamo serving](reference/labs/32_dynamo_disaggregation.md)
- [Lab 33: Test KV-cache-aware request routing](reference/labs/33_dynamo_routing.md)
- [Lab 34: Tune serving goodput under latency objectives](reference/labs/34_serving_goodput.md)

### Final investigation

Choose a training or inference workload family. Write a hypothesis before the candidate run, retain one changed control, and repeat both variants in reversed order. Explain why a bandwidth result can disagree with a latency result. Use a later trace to distinguish a communication tail from a delayed producer, then decide whether to keep the change. Include correctness, variation and a limitation; a negative result can pass.
