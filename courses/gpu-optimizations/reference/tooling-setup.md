# Diagnostic tooling reference

shared environment setup owns the shared installation and readiness checks for Systems, Compute, and private Grafana. Use the same qualified versions throughout the courses. Soperator supplies the existing DCGM collectors, VMAgent, and local VictoriaMetrics database; do not deploy another collector or database for these exercises.

PyTorch Profiler remains useful for operator dispatch in the advanced labs. Use the explicit external-only branch when collecting a separate Nsight trace, so two CUDA profilers do not compete for the same process. Profiling, sanitizers, and telemetry answer different questions; confirm an optimization with repeated unprofiled runs and the lab's correctness contract.

## What each tool can prove

| Tool | Appropriate evidence | What it cannot prove alone |
| --- | --- | --- |
| `nvidia-smi` | Device visibility, allocation, memory use, clocks, power, temperature, coarse activity | Kernel efficiency or a source-level bottleneck |
| DCGM | Health and interval activity across GPUs, nodes, ranks, and time | Which operator, kernel, or instruction caused the interval |
| DCGM Exporter | A protected, time-series view of selected DCGM fields for dashboards and alerts | A kernel root cause, even when an interval correlates with a slowdown |
| PyTorch Profiler | Operator CPU/CUDA time, call count, shapes, copies, allocations, and framework-to-kernel mapping | Whole-system scheduling outside its collection scope |
| NVTX | Semantic phase labels such as `forward`, `backward`, `prefill`, or `profile_region` | Any performance metric by itself |
| Nsight Systems | CPU gaps, CUDA APIs, kernels, copies, synchronization, NCCL, rank imbalance, and overlap | Arithmetic intensity or detailed kernel pipeline limits |
| Nsight Compute | One selected kernel's roofline position, throughput, memory traffic, scheduler, stalls, and occupancy | End-to-end latency or throughput by itself |
| `nvbandwidth` | Bandwidth or latency for a declared host/device or device/device copy path, size, direction, and concurrency | NCCL collective performance or application throughput |
| NCCL Tests | Correctness and algorithm/bus-bandwidth curves for a named collective and message-size sweep | Framework scheduling, overlap, useful model progress, or an application speedup |
| vLLM Bench | Startup, latency, throughput, or online-serving behavior for one versioned vLLM engine workload | A cross-engine standard or a kernel-level explanation |
| AIPerf | Request distribution, TTFT, ITL, end-to-end latency, request rate, and token throughput | Kernel root cause without server and profiler evidence |
| MLPerf | A comparable full-system result only when the official rules, scenario, dataset, quality target, audit, and checker are followed | That an informal course experiment is an MLPerf result |

For NCCL Tests, select the operation from the training or serving design rather
than running only all-reduce by habit. DDP commonly motivates an all-reduce;
FSDP-style sharding uses all-gather and reduce-scatter; tensor-parallel layouts
may use all-reduce, all-gather, or reduce-scatter; expert routing motivates
all-to-all. Sweep realistic message sizes and report both algorithm bandwidth
and bus bandwidth with the exact operation, rank count, topology, and build.
Then profile the real PyTorch workload, because a good synthetic curve does not
prove that the framework overlaps the same collective with useful compute.

Use `nvbandwidth` before NCCL Tests when the question is the underlying copy
path itself. Use NCCL Tests when the question is a collective. Use the PyTorch
or serving workload when the question is application behavior. Use an
engine-native client such as `vllm bench` for a focused vLLM study, an endpoint
load client for service-level latency and throughput, and MLPerf only when the
formal benchmark contract is actually being reproduced.

## Permissions and profiler coordination

Inside an allocated worker, `nsys status -e` reports capture prerequisites and
`ncu --list-sets` lists the installed collection sets. These are diagnostic
checks; shared environment setup's actual canary captures establish readiness for this runtime.

An `ERR_NVGPUCTRPERM`-style error is a recorded permission blocker. Escalate it
to the cluster owner; do not weaken host security. DCGM profiling fields and
developer profilers can compete for the same counters. DCGM pause and resume
are host-engine-wide operations, so only the site owner should coordinate them.
Profile one short, representative workload and preserve an unprofiled timing
run because profiling overhead invalidates acceptance timing.

## Evidence privacy

Profiler reports, exporter series, benchmark reports, and telemetry can contain host paths, command lines, device
identifiers, model metadata, or request content. Treat every raw artifact as
private even when the lab uses synthetic data. Follow
[evidence-security.md](evidence-security.md) before sharing any result.

## Apply the networking tools in lesson order

The advanced communication course owns the bounded networking experiments:
Lab 01 checks topology, Lab 09 measures the NCCL message-size curve and Lab 10
runs NCCL Tests. First map topology and selected transport, then read the message-size
curve, then change one job-local setting in a new process. Advanced Labs 12 and 13 own the
follow-up application scaling and overlap checks. NCCL Tests uses its own
MPI-enabled binary and launcher; the PyTorch labs use torchrun. The two runtimes
must record their loaded NCCL versions independently. Optional upstream tuning
reports and per-iteration columns are diagnostic extensions, not the standard
table accepted by the course parser.
