# MK8s GPU setup and operator guidance

Reviewed 2026-09-07. Pinned operator examples require a fresh compatibility check.
Proof pods are active diagnostics, not passive inspection or performance proof.

Use this reference when a task involves actually setting up, reviewing, or
debugging GPU-enabled Nebius Managed Service for Kubernetes clusters, especially
when the choice between Nebius driverful images and operator-managed host
setup affects GPU Operator, Network Operator, or GPUDirect RDMA behavior.

Primary vendor references:

- Nebius GPU setup:
  - <https://docs.nebius.com/kubernetes/gpu/set-up>
- Nebius InfiniBand / GPU cluster setup:
  - <https://docs.nebius.com/kubernetes/gpu/clusters>

## Decision workflow

1. Resolve the live Nebius compatibility inputs first.
   - Use the MK8s compatibility matrix for valid Kubernetes version, platform,
     OS, and `drivers_preset` combinations.
   - Use the live compute platform preset metadata to decide whether the exact
     selected preset allows GPU clustering / InfiniBand.
   - Use Capacity Dashboard only after that to rank which supported preset or
     fabric currently has capacity.
   - Treat single-GPU presets as Ethernet-only testing/dev shapes. The
     InfiniBand / GPUDirect-RDMA path is for cluster-compatible multi-GPU
     presets.

2. Choose the host-stack ownership model.
   - Nebius driverful image:
     - set `drivers_preset`
     - Nebius-managed node image owns the host GPU driver and CUDA userspace
     - verify whether this image also owns Container Toolkit and OFED before
       configuring operators; do not infer those components from the image label
   - Manual/operator-managed host stack:
     - omit `drivers_preset`
     - NVIDIA operators are responsible for installing the missing host pieces

3. Decide whether you need operators at all.
   - Default Nebius-image path:
     - if you only need the Nebius-provided GPU image and standard behavior,
       follow the Nebius docs first
   - Custom plugin or monitoring path:
     - follow the current Nebius custom-plugin/MIG guidance: omit `drivers_preset`
       when taking ownership of a modified device plugin or MIG configuration
     - do not assume adding an operator over a provider-managed stack is supported
   - Manual host-stack path:
     - GPU Operator is required for any cluster with GPU node groups that do
       not use the Nebius GPU image
   - Networking / GPUDirect path:
     - Network Operator is required when a non-Nebius-image GPU node group uses
       B200 or joins a GPU cluster for InfiniBand
     - in an explicitly selected driverful InfiniBand integration, Network Operator is also
       used to expose `rdma/shared_device` to pods even though host OFED stays
       image-owned
     - single-GPU presets stay on Ethernet and should not be described as an
       InfiniBand or GPUDirect-RDMA path

4. If both NVIDIA operators are needed, keep the order and ownership clean.
   - Install or reconcile Network Operator before GPU Operator.
   - Keep exactly one NFD owner.
   - If Network Operator is the intended NFD owner, disable GPU Operator NFD.

## Preinstalled-component ownership pattern

Nebius documents `--template-gpu-settings-drivers-preset <cuda...>` for an image
with GPU drivers and required components. Before integrating operators, verify
that the selected integration is supported and inspect the actual component
owners. Follow the documented custom-plugin/MIG path when taking over that work.

- Do not let an operator reinstall an image-owned GPU driver.
- Set `toolkit.enabled=false` only after verifying that the image owns and
  correctly configures the container runtime integration.
- Set `operator.ofedDriver.deploy=false` only after verifying an image-owned
  OFED stack for the exact selected GPU/fabric target.
- Keep exactly one NFD owner; disable GPU Operator NFD only when another
  verified owner supplies it.
- Expose `rdma/shared_device` through the selected supported device-plugin
  integration when workloads need it. Allocatable resources alone do not prove
  successful RDMA communication.

The driverful values templates express those ownership assumptions. They are
conditional examples, not evidence that every Nebius image has the same driver,
Container Toolkit, runtime configuration, kernel modules or OFED installation.
Privileged inspection follows `active-diagnostics.md` and requires exact scope.

## Operator-managed host-stack pattern

Use this path when you intentionally omit `drivers_preset`.

- GPU Operator is required.
- Network Operator is required for:
  - B200 operator-managed GPU node groups
  - any GPU-cluster / InfiniBand path
- If both operators are installed:
  - install Network Operator first
  - disable GPU Operator NFD so only one NFD instance is active
- Nebius docs currently pin the operator-managed B300 GPU Operator path to:
  - `driver.version=580.95.05`
- For an explicitly selected operator-managed stack:
  - `driver.enabled=true`
  - `toolkit.enabled=true`
  - `nfd.enabled=false` on GPU Operator whenever Network Operator is the
    intended NFD owner
  - `operator.ofedDriver.deploy=true` on Network Operator
  - for GPU-cluster / InfiniBand shapes, Network Operator also patches
    `NicClusterPolicy` so pods can request `rdma/shared_device`

This path is fundamentally different from the Nebius driverful path. Do not mix
them casually inside one node-group design.

## GPUDirect RDMA notes

- Current NVIDIA docs default to the DMA-BUF path for GPUDirect RDMA. Do not
  assume the legacy `nvidia-peermem` module is required on every cluster.
- If Mellanox OFED is managed directly on the host, `driver.rdma.useHostMofed`
  is the GPU Operator switch for that ownership model.
- If you intentionally need the legacy `nvidia-peermem` path instead of
  DMA-BUF, that is when `driver.rdma.enabled=true` becomes relevant.
- Inspect the selected image and running driver stack before choosing either
  path; a module observed on one image is not a requirement for another.

## Readiness and proof workflow

For live cluster review, do not stop at a green control plane.

1. Check operator policy state.
   - `ClusterPolicy` for GPU Operator
   - `NicClusterPolicy.status.appliedStates` for Network Operator

2. Check scheduler-visible node resources.
   - `nvidia.com/gpu` on Ready GPU nodes
   - `rdma/shared_device` or the configured RDMA resource on Ready GPU nodes
     when the task needs GPUDirect RDMA or pod-facing RDMA access

3. Check NFD and label outcomes.
   - GPU-capable nodes should carry `feature.node.kubernetes.io/pci-10de.present=true`
   - Mellanox-capable nodes should carry
     `feature.node.kubernetes.io/pci-15b3.present=true`

4. Run a proof pod when needed.
   - Request both `nvidia.com/gpu` and `rdma/shared_device`
   - verify the pod starts
   - verify `/dev/infiniband` exists inside the pod

5. For driverful images, inspect the host only when the ownership boundary is
   part of the question.
   - confirm NVIDIA Container Toolkit is installed and configured
   - confirm the expected GPU and Mellanox modules are loaded
   - confirm RDMA devices are visible from the host when InfiniBand is part of
     the design

## Common pitfalls

- `toolkit.enabled` in GPU Operator refers to the NVIDIA Container Toolkit
  runtime, not the CUDA Toolkit.
- Do not use `nvidia.com/gpu.deploy.operands=true` as a required deployment
  switch. The documented suppression label is `nvidia.com/gpu.deploy.operands=false`.
- Do not decide GPU clustering from platform name alone; use the selected
  preset's live `allow_gpu_clustering` metadata.
- Do not treat a fabric-scoped Capacity Dashboard row for a single-GPU preset
  as proof that the preset supports InfiniBand.
- Single-GPU presets are useful for testing and basic validation, but they are
  not representative NCCL / distributed-training performance environments.
- A ready `NicClusterPolicy` is not proof that pods can consume RDMA. Check the
  actual allocatable resource on the node.
- Do not blanket-set `driver.rdma.enabled=true` just because GPUDirect RDMA is
  desired. That setting is for the legacy `nvidia-peermem` path, not the
  default DMA-BUF path.
- Do not assume you can change GPU platform, preset, or GPU cluster in place on
  an existing node group. Nebius docs say to create a new node group instead.

## Skill assets for this workflow

- `assets/gpu/gpu-operator-driverful-values.yaml`
- `assets/gpu/network-operator-driverful-values.yaml`
- `assets/gpu/nicclusterpolicy-driverful-rdma-shared.yaml`
- `assets/gpu/gpu-operator-manual-values.yaml`
- `assets/gpu/network-operator-manual-values.yaml`
- `assets/gpu/nicclusterpolicy-manual-rdma-shared.yaml`
- `assets/gpu/check-cluster-readiness.sh`
- `references/active-diagnostics.md`
- `assets/gpu/proof-rdma-gpu-pod.yaml`
