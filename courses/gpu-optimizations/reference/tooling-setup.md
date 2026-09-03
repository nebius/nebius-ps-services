# Diagnostic tooling setup

Use this checklist from the allocated compute-node environment. A command that
exists on the login node but disappears inside a Slurm job is not available to
the lab. The official installation and user guides are collected in
[RESOURCES.md](../RESOURCES.md).

## Start with the site-owned path

Prefer the cluster's supported module, container, or base image. Driver tools,
hardware-counter permissions, CUPTI, DCGM, and profiler versions interact with
the host driver and security policy. A learner-local package must not replace
or weaken those controls.

Run the inventory first:

```bash
sbatch slurm/tooling_preflight.sbatch
```

Record the PyTorch, CUDA, driver, Nsight, and DCGM versions from the Slurm
output. Also record which commands are missing. The preflight deliberately
suppresses hostnames, executable paths, GPU UUIDs, and raw DCGM discovery.
Installation is complete only when the command is visible inside the same
allocation used by the labs.

## Installation and ownership map

| Tool | Installation path | Verify in the Slurm job | Owner |
| --- | --- | --- | --- |
| `nvidia-smi` | Installed with the NVIDIA driver | `nvidia-smi` | Cluster administrator |
| PyTorch Profiler | Included in the pinned PyTorch CUDA environment | `python -c 'import torch; print(torch.profiler)'` | Course environment owner |
| NVTX from PyTorch | Included as `torch.cuda.nvtx` in the PyTorch CUDA environment | `python -c 'import torch; print(torch.cuda.nvtx)'` | Course environment owner |
| Nsight Systems | Prefer a site module or CUDA Toolkit; NVIDIA also publishes Linux CLI-only packages such as `nsight-systems-cli` | `nsys --version` and `nsys status -e` | Cluster administrator |
| Nsight Compute | Prefer the CUDA Toolkit or NVIDIA standalone package; the CLI is `ncu` | `ncu --version`, `ncu --list-sets`, `ncu --list-sections`; confirm every set/section used by a lab | Cluster administrator |
| Roofline analysis | No separate package; it is an Nsight Compute section set and report view | Confirm that `roofline` appears in `ncu --list-sets` | Nsight Compute owner |
| DCGM | Install the DCGM 4 package that matches the CUDA user-mode driver major version; the host engine and field policy are site services | `dcgmi --version`, `dcgmi discovery -l`, `dcgmi profile --list --entity-id gpu:0` | Cluster administrator |
| DCGM Exporter | Deploy the NVIDIA exporter against the site-owned DCGM host engine, normally through the cluster monitoring stack | `dcgm-exporter --help`; verify the protected Prometheus target through the site monitoring path | Cluster administrator / monitoring owner |
| `nvbandwidth` | Build or install NVIDIA's versioned utility in a cluster-approved tools image; retain its commit or release identity | `nvbandwidth --help` | Cluster administrator / performance tools owner |
| NCCL Tests | Build the NVIDIA test executables against the same NCCL and MPI/runtime family used by the cluster | `all_reduce_perf --help`, `all_gather_perf --help`, `reduce_scatter_perf --help`, `alltoall_perf --help` | Cluster administrator / communication owner |
| vLLM Bench | Included with a compatible, pinned vLLM environment; keep it separate from the training environment | `vllm bench --help` | Serving environment owner |
| GenAI-Perf | Use an exact cluster-approved version or a hash-locked requirements file in an isolated client environment; the endpoint must already be running | `genai-perf --help` | Benchmark client owner |
| AIPerf | For new NVIDIA generative-AI benchmarks, use an exact approved version or a hash-locked requirements file in an isolated client environment | `aiperf --help` | Benchmark client owner |
| MLPerf | Reproduce only with the official benchmark rules, approved implementation, dataset, quality target, and submission scenario | Verify the selected suite's official checker and result format | Benchmark program owner |

GenAI-Perf is being phased out by its official project in favor of AIPerf.
This course explains GenAI-Perf so that existing workflows remain readable,
but new benchmark automation should begin with AIPerf. Pin whichever client is
used, preserve its configuration and artifact schema, and never compare runs
whose metric definitions differ silently.

Use only an official package index or a cluster-approved mirror. Do not use an
unqualified `pip install` on a managed cluster. Record the exact resolved
version and retain the approved lock or hash file with the private run record.

## Administrator-owned package examples

These are provisioning examples, not learner lab commands:

```bash
# After the NVIDIA developer-tools repository is configured for the host OS:
NSYS_PACKAGE_VERSION='replace-with-approved-version'
sudo apt-get install --yes --no-install-recommends \
  "nsight-systems-cli=${NSYS_PACKAGE_VERSION}"

# DCGM 4 package name uses the CUDA user-mode driver major version.
# Replace this example with the version supported by the site's driver stack.
CUDA_VERSION_MAJOR=13
DCGM_PACKAGE_VERSION='replace-with-approved-version'
sudo apt-get install --yes --no-install-recommends \
  "datacenter-gpu-manager-4-cuda${CUDA_VERSION_MAJOR}=${DCGM_PACKAGE_VERSION}"
```

Nsight Compute may arrive with the CUDA Toolkit or a standalone NVIDIA
installer. Package names and supported distributions change, so the cluster
owner must use the current official installation guide rather than copying an
old versioned package name. Learners should not run these administrator
commands on a managed cluster.

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
| GenAI-Perf or AIPerf | Request distribution, TTFT, ITL, end-to-end latency, request rate, and token throughput | Kernel root cause without server and profiler evidence |
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
