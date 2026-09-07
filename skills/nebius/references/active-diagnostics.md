# Active diagnostic boundaries

Reviewed 2026-09-07. Classify the effect, not the command's name.

| Action | Effect and evidence limit |
| --- | --- |
| Scoped API get/list | Control-plane observation; may be incomplete/stale |
| kubectl get on selected resources | Cluster observation; requires exact context and scope |
| kubectl exec or host inspection | Command execution; assess state/security impact before calling it read-only |
| Privileged pod or host-root mount | Cluster mutation plus host privilege; never passive inspection |
| GPU/RDMA proof pod | Pod creation, GPU allocation and command execution; device exposure only |
| NCCL/storage/network benchmark | Resource consumption and possibly data/network writes; benchmark-specific proof |
| Synthetic log/metric/trace | Telemetry write; ingestion proof only after independent query |

The former privileged host helper is removed. For an authorized host diagnostic:
freeze cluster, node and commands; prefer already available non-privileged
evidence. If privileged access is essential, use the platform's approved
workflow with explicitly authorized namespace, pinned image digest, exact node,
minimal mounts/access and bounded runtime. A read-only host mount does not make
a privileged pod non-intervening. Do not mount host `/` writable. Record the exact
created UID and clean up only that owned object under the authorized cleanup
scope; never delete a pre-existing pod with a colliding name.

`assets/gpu/proof-rdma-gpu-pod.yaml` is an explicitly active, bounded example.
Resolve its image placeholder to a compatible digest, validate the node/preset
supports the requested GPU/RDMA resources, choose the namespace, and create only
with authority. Retain the API-returned generated name and UID. A Succeeded pod
with visible devices proves scheduling/device exposure; it does not prove
GPUDirect traffic, fabric topology correctness, NCCL throughput or production
training readiness. Verify those with separately declared workload criteria.

For live product validation, freeze expected behavior before the trial. Any
out-of-workflow mutation that performs or pre-satisfies a product step intervenes
in that trial. Keep setup, recovery and independent verification evidence
separate; recovery authority never repairs contaminated product evidence.
