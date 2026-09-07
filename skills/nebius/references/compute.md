# Compute: virtual machines and Kubernetes

Reviewed 2026-09-07. Public APIs: `compute.v1`, `mk8s.v1`. Discover project
platforms/presets and the live MK8s compatibility matrix; API version alone does
not establish region availability or service maturity. Use the catalog's source
links and regional availability checks before provisioning.

## Choose the resource

- CPU VMs suit services, bastions and general compute; GPU VMs suit workloads
  needing direct host control. Container VMs retain VM networking/storage and
  lifecycle responsibilities.
- Preemptible VMs/node groups require interruption-tolerant jobs and durable
  checkpoints. They are not a transparent replacement for long-running stateful work.
- GPU clusters provide supported InfiniBand placement. Validate the exact
  preset's clustering capability before selecting a fabric; capacity ranking
  does not authorize unsupported combinations.
- Managed Kubernetes owns the control plane and node-group lifecycle. Plan
  CPU system capacity separately from GPU workers. Soperator/Serverless AI
  selection is described in `ai-service-integration.md`.

## Preflight and lifecycle

1. Freeze project/region, platform/preset, image/version and optional fabric.
   Confirm compute, disk, VM count and address quotas across tenant/project,
   reservation terms and actual capacity. Reservations and quota are different.
2. Check selected subnet/network/security group, boot disk/image architecture,
   service account and storage parentage. Decide explicitly which disks survive
   VM deletion. Never place credentials in cloud-init or labels.
3. Build requests with `assets/storage/volumes.py`,
   `assets/compute/virtual_machine.py` or `assets/compute/kubernetes.py`.
   The builders never choose a live platform or reserve capacity.
4. Submit only within the authorized workflow using
   `assets/sdk/provision.py`. Wait for the operation, verify resource identity,
   requested settings and service-specific readiness, then verify the workload.
5. Before stop/start, maintenance, upgrade or deletion, evaluate workload
   disruption, ephemeral storage loss, attachment retention and controller
   ownership. Do not automatically delete an errored VM as a diagnostic step.

## VM plus disk example

The following is an authorized-call example, not a script to run implicitly.
`context` contains previously verified IDs and the caller's explicit selections.
`cloud_init` contains only public keys/non-secret configuration.

```python
from compute.virtual_machine import instance_request
from storage.volumes import disk_request
from sdk.provision import create_and_verify
from nebius.api.nebius.compute.v1 import (
    DiskServiceClient, DiskStatus, GetDiskRequest,
    InstanceServiceClient, InstanceStatus, GetInstanceRequest,
)

disk = create_and_verify(
    DiskServiceClient(sdk), disk_request(**context["disk"]), GetDiskRequest,
    ready=lambda value: value.status.state == DiskStatus.State.READY,
)
vm_args = dict(context["vm"], disk_id=disk.resource_id)
vm = create_and_verify(
    InstanceServiceClient(sdk), instance_request(**vm_args), GetInstanceRequest,
    ready=lambda value: value.status.state == InstanceStatus.InstanceState.RUNNING,
)
```

These checks establish provider state, not SSH trust, cloud-init completion,
application health or GPU performance. Verify those separately without
bypassing their owning workflow. Retain standalone disk IDs on VM failure.

## Kubernetes operations

Use an explicit service CIDR that does not overlap workload/private ranges.
For node groups, pass a complete NodeTemplate and a version validated against
the control plane. Fixed count and autoscaling are distinct modes. Estimate
maximum autoscaling demand plus rolling-update surge and node/pod address usage.
Load `mk8s-compatibility.md` and `mk8s-gpu-setup.md` before GPU changes.

For upgrades, verify supported version steps, node compatibility, disruption
budgets and spare capacity. Size reduction is a disruptive option requiring
separate authority, not a default quota workaround. Control-plane readiness,
node Ready status, schedulable GPU/RDMA resources and successful workloads
are separate postconditions. Inspect cluster/node metadata using
`scripts/inspect_kubernetes.py`; it does not read kubeconfig or run kubectl.

## Official references

- [VM types](https://docs.nebius.com/compute/virtual-machines/types)
- [VM lifecycle](https://docs.nebius.com/compute/virtual-machines/lifecycle)
- [Preemptible VMs](https://docs.nebius.com/compute/virtual-machines/preemptible)
- [Images](https://docs.nebius.com/compute/storage/boot-disk-images)
- [Capacity reservations](https://docs.nebius.com/compute/virtual-machines/reservations)
- [Kubernetes management](https://docs.nebius.com/kubernetes/clusters/manage)
- [Node groups](https://docs.nebius.com/kubernetes/node-groups/manage)
- [Version upgrades](https://docs.nebius.com/kubernetes/manage-versions)
