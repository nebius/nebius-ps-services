# GPU And Accelerator Troubleshooting Playbook

## Applicability

Use this NVIDIA guide for GPU allocation, initialization, execution, memory,
reset,
collective communication, topology, performance, or hardware-health failures.
Pin GPU model, firmware where visible, driver, CUDA, runtime, library, container
toolkit, device plugin or operator, and workload image versions.

## Version Fingerprint

Pin exact NVIDIA GPU model, MIG mode, firmware, driver, CUDA/runtime, libraries,
container toolkit, OS/kernel, topology, rank mapping and scheduler placement.

## Adaptive Decision Guide

Use only rows implicated by the failing path; record material unexamined boundaries.

<!-- markdownlint-disable MD013 -->

| Symptom | Competing explanations | Cheapest distinguishing evidence | Interpretation | Next branch |
| --- | --- | --- | --- | --- |
| GPU errors or low throughput | stale Xid / active device fault / starvation / transfer or collective bottleneck | boot/device identity, current counter deltas, workload timeline and matching topology | high allocation or a historical Xid does not prove current compute utilization/failure | separate host input, transfers, kernels and collectives before active tests |

<!-- markdownlint-enable MD013 -->

### Diagnostic Effects

Passive bounded inspection comes first. Active diagnostics remain coordinator-owned;
workers may not launch tests, captures, debug containers, stress or repairs.
Escalate only if the next observation can separate remaining explanations. Bound
time, output, target and effects; preserve evidence and original settings. Treat
production or unconfirmed targets as read-only without exact action authority.

### Verification

Replay the original operation from the same relevant identity and workload, compare
an unaffected control, and verify the implicated boundary independently. Invalidate
pre-change observations and state material unverified scope. A passing status
command cannot replace the workload oracle.

Follow device allocation → driver/runtime compatibility → application execution
→
collectives/fabric. For AMD use [amd.md](amd.md). CUDA minor compatibility is
conditional: PTX compilation, newer driver-dependent features and target architecture
can matter. Check the exact support matrix, not version-number proximity. Fabric
Manager/NVSwitch configuration is generation/platform-specific.

DCGM does not provide comprehensive hardware diagnosis or automatic repair. Some
levels include stress workloads; inventory visibility and a diagnostic pass have
bounded proof scope. NCCL failures may originate in networking, runtime/shared
memory, rank placement or host resources rather than the GPU. Compare scheduler
allocation and actual fabric locality for distributed workloads.

## Failing Path And Component Identity

Trace the path from scheduler or orchestrator resource declaration through node
inventory, device plugin or GRES, cgroup and device assignment, container
runtime, driver, CUDA or accelerator runtime, application framework, collective
library, PCIe or NVLink, NIC or RDMA fabric, and hardware. Record MIG or
partitioning, topology, persistence or reset state, and recent node operations.

## Component Verification

Verify expected device count and identity, driver and runtime compatibility,
device files and permissions, allocation visibility, cgroup access, GPU memory
and utilization, temperature and power, clock or throttle reasons, ECC and
retired pages, Xid events, reset state, PCIe and interconnect health, topology,
RDMA or NIC mapping, host resources, time sync, and affected versus unaffected
devices and nodes.

A successful inventory query proves management-path visibility, not kernel
execution, application correctness, collective communication, or hardware health.

## Implicated Logs And Evidence

Correlate job, pod, process, node, GPU UUID or index, MIG instance, rank, device,
Xid, fabric endpoint, and timestamp. Select relevant evidence from:

- application and framework logs, stack traces, core dumps, rank-specific
  stdout and stderr, and failure phase;
- scheduler or orchestrator allocation, device-plugin, operator, runtime, and
  container-toolkit logs;
- driver and kernel logs including NVIDIA Xid, NVRM, PCIe, IOMMU, reset, OOM,
  ECC, RAS, and device removal events;
- DCGM or equivalent health and diagnostic evidence, telemetry retention, and
  alert timing;
- NCCL or other collective-library evidence and InfiniBand, RDMA, NIC, switch,
  NVLink, and topology errors;
- maintenance, node replacement, firmware or driver changes, and power or
  thermal events.

Treat stale Xid events separately from incident-window events. A node reboot can
erase volatile evidence and reset the symptom without proving a cause.

## Diagnostic Branches

- **Resource not allocated or visible:** declared request, scheduler inventory,
  GRES or extended resource, plugin registration, node labels, cgroups, device
  files, runtime injection, MIG identity, and workload environment.
- **Initialization or compatibility:** driver, CUDA runtime and library ABI,
  image, device permissions, persistence or reset state, and application trace.
- **Out of memory:** allocation timeline, fragmentation, retained tensors or
  processes, per-rank behavior, input shape, framework allocator, cgroup and
  host memory, and retry behavior.
- **Xid, ECC, or reset:** exact device and time, kernel context, affected job,
  recurrence, topology, temperature and power, driver guidance, and hardware
  diagnostics. Do not reset or replace before preserving evidence.
- **Collective hang or performance:** rank timeline, topology, NCCL or library
  state, network or fabric counters, GPU progress, CPU affinity, NUMA, PCIe,
  link state, timeout origin, and one affected versus unaffected node set.
- **Performance regression:** equivalent workload and warmup, GPU occupancy,
  memory bandwidth, kernels, clocks and throttling, input pipeline, CPU, storage,
  topology, and known-good version comparison.

## Controlled Debug Escalation

Framework, CUDA, NCCL, driver, DCGM, or fabric debugging can be voluminous,
sensitive, and performance-changing. Scope by one job, rank, node, subsystem,
and bounded duration; define expected evidence and output; capture original
settings; warn about performance impact; redact payload and credentials; and
verify rollback. A full-resource diagnostic or ActiveCheck is a workload and
capacity mutation, not passive observation.

## Official Sources

- [CUDA compatibility limits](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html)
- [Fabric Manager](https://docs.nvidia.com/datacenter/tesla/fabric-manager-user-guide/index.html)
- [DCGM diagnostics and test levels](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/dcgm-diagnostics.html)

- [NVIDIA Xid errors](https://docs.nvidia.com/deploy/xid-errors/index.html)
- [NVIDIA DCGM documentation](https://docs.nvidia.com/datacenter/dcgm/latest/)
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/)
- [NCCL troubleshooting](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/troubleshooting.html)
- [Slurm generic resources](https://slurm.schedmd.com/gres.html)
