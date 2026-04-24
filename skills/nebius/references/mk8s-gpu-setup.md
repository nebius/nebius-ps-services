# MK8s GPU setup and operator guidance

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
     - on the validated Nebius path, the host also already has NVIDIA Container
       Toolkit configured and the GPU-cluster image includes the OFED path
   - Manual/operator-managed host stack:
     - omit `drivers_preset`
     - NVIDIA operators are responsible for installing the missing host pieces

3. Decide whether you need operators at all.
   - Default Nebius-image path:
     - if you only need the Nebius-provided GPU image and standard behavior,
       follow the Nebius docs first
   - Custom plugin or monitoring path:
     - install GPU Operator when you need Kubernetes-side GPU management such as
       device plugin or DCGM handling
   - Manual host-stack path:
     - GPU Operator is required for any cluster with GPU node groups that do
       not use the Nebius GPU image
   - Networking / GPUDirect path:
     - Network Operator is required when a non-Nebius-image GPU node group uses
       B200/B200A or joins a GPU cluster for InfiniBand
     - in the cxcli bundled driverful InfiniBand path, Network Operator is also
       used to expose `rdma/shared_device` to pods even though host OFED stays
       image-owned
     - single-GPU presets stay on Ethernet and should not be described as an
       InfiniBand or GPUDirect-RDMA path

4. If both NVIDIA operators are needed, keep the order and ownership clean.
   - Install or reconcile Network Operator before GPU Operator.
   - Keep exactly one NFD owner.
   - If Network Operator is the intended NFD owner, disable GPU Operator NFD.

## Validated Nebius driverful-image pattern

This is the most important Nebius-specific distinction.

- Nebius docs use `--template-gpu-settings-drivers-preset <cuda...>` to request
  a boot disk image that already contains the GPU drivers and other required
  components.
- In the validated Nebius driverful path:
  - GPU Operator must not reinstall the host GPU driver
  - GPU Operator must not reinstall the NVIDIA Container Toolkit runtime
  - Network Operator must not deploy the OFED driver container
  - for GPU-cluster / InfiniBand shapes, Network Operator still needs to expose
    RDMA resources to pods

Practical consequences for automation:

- GPU Operator:
  - `driver.enabled=false`
  - `toolkit.enabled=false`
  - if Network Operator is present, `nfd.enabled=false`
- Network Operator:
  - `operator.ofedDriver.deploy=false`
  - enable NFD only once
  - enable Mellanox NodeFeatureRules when Network Operator owns NFD
  - expose `rdma/shared_device` for InfiniBand-capable nodes when pods must
    request RDMA resources

In the validated Nebius driverful GPU-node image, the live host had:

- NVIDIA GPU driver
- CUDA packages
- NVIDIA Container Toolkit configured as the default containerd runtime
- `nvidia_peermem` loaded
- Mellanox / OFED kernel modules present on the InfiniBand-capable GPU node

Treat those host-side observations as image-specific facts that should be
verified on the actual cluster, not as a timeless universal guarantee.

## Operator-managed host-stack pattern

Use this path when you intentionally omit `drivers_preset`.

- GPU Operator is required.
- Network Operator is required for:
  - B200/B200A operator-managed GPU node groups
  - any GPU-cluster / InfiniBand path
- If both operators are installed:
  - install Network Operator first
  - disable GPU Operator NFD so only one NFD instance is active
- Nebius docs currently pin the operator-managed B300 GPU Operator path to:
  - `driver.version=580.95.05`
- In the current cxcli contract for operator-managed stacks:
  - `driver.enabled=true`
  - `toolkit.enabled=true`
  - `nfd.enabled=false` on GPU Operator whenever Network Operator is the
    intended NFD owner
  - `operator.ofedDriver.deploy=true` on Network Operator
  - for GPU-cluster / InfiniBand shapes, Network Operator also patches
    `NicClusterPolicy` so pods can request `rdma/shared_device`

This path is fundamentally different from the Nebius driverful path. Do not mix
them casually inside one node-group design.

## Current nebius-cxcli catalog behavior

When the task is explicitly about this repository rather than generic Nebius
operations, the active policy contract is:

- GPU Operator:
  - auto-enabled for GPU-enabled MK8s clusters
  - leaves host GPU driver and NVIDIA Container Toolkit untouched on
    `gpu_stack_source: nebius_image`
  - installs the host GPU driver and NVIDIA Container Toolkit on
    `gpu_stack_source: operator_managed`
  - pins `driver.version=580.95.05` only for the Nebius B300 operator-managed path
- Network Operator:
  - auto-enabled for `gpu_cluster_enabled=true`
  - auto-enabled for operator-managed B200/B200A shapes even without InfiniBand
  - keeps `operator.ofedDriver.deploy=false` on the Nebius driverful-image path
  - keeps `operator.ofedDriver.deploy=true` on the operator-managed path
- Single-GPU preset handling:
  - treated as Ethernet-only testing/dev capacity, not as GPU-cluster capacity
  - does not surface `infiniband_fabric` as a valid setting
  - if operators manually leave NCCL validation enabled on such shapes, cxcli
    warns that the test would use Ethernet/TCPIP and is not representative of
    production distributed training
- NFD ownership:
  - on GPU-cluster / InfiniBand shapes, Network Operator is the single NFD owner
  - on operator-managed B200/B200A shapes where Network Operator is auto-enabled for
    RDMA plumbing, GPU Operator NFD also stays disabled

Treat `component_sources.yaml` in `services/nebius-cxcli` as the repository's
authoritative implementation of those rules.

## GPUDirect RDMA notes

- Current NVIDIA docs default to the DMA-BUF path for GPUDirect RDMA. Do not
  assume the legacy `nvidia-peermem` module is required on every cluster.
- If Mellanox OFED is managed directly on the host, `driver.rdma.useHostMofed`
  is the GPU Operator switch for that ownership model.
- If you intentionally need the legacy `nvidia-peermem` path instead of
  DMA-BUF, that is when `driver.rdma.enabled=true` becomes relevant.
- On the validated Nebius driverful-image host we inspected, `nvidia_peermem`
  was already loaded. Treat that as a current-image observation, not a generic
  universal requirement.

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
- `assets/gpu/inspect-driverful-host.sh`
- `assets/gpu/proof-rdma-gpu-pod.yaml`
