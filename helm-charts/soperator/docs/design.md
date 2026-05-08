# Soperator Helm Chart Design

This document explains this chart from the ground up. It is intentionally
written as an operator guide, not as an implementation dump.

The short version:

- Terraform creates Nebius infrastructure.
- Helm installs Kubernetes and Soperator resources inside the MK8s cluster.
- Soperator turns `SlurmCluster`, `NodeSet`, and `NodeConfigurator` custom
  resources into a working Slurm cluster.
- `nebius-cxcli` connects Terraform outputs to Helm values and gives users a
  guided deployment flow.

## Source Of Truth

This design follows the public Slurm and Nebius Soperator projects:

- [Slurm documentation](https://slurm.schedmd.com/documentation.html)
- [Slurm overview](https://slurm.schedmd.com/overview.html)
- [Slurm accounting](https://slurm.schedmd.com/accounting.html)
- [Slurm GRES/GPU scheduling](https://slurm.schedmd.com/gres.html)
- [Slurm dynamic nodes](https://slurm.schedmd.com/dynamic_nodes.html)
- [Slurm `slurm.conf` NodeSet and partition configuration](https://slurm.schedmd.com/slurm.conf.html)
- [Kubernetes labels and selectors](https://kubernetes.io/docs/concepts/overview/working-with-objects/labels/)
- [Kubernetes node selection](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/)
- [Nebius Soperator](https://github.com/nebius/soperator)
- [Soperator architecture](https://github.com/nebius/soperator/blob/main/docs/architecture.md)
- [Soperator self-deploy](https://github.com/nebius/soperator/blob/main/docs/self-deploy.md)

## Upstream Release Contract

This chart is anchored to one public Nebius Soperator release at a time. The
authority is `upstream-soperator.lock.yaml`.

The lock records:

- upstream repository.
- upstream release and tag.
- resolved upstream tag commit.
- this chart package version.
- the upstream-owned path copied into this chart.
- the local-owned paths that sync must not overwrite.

Versioning uses two fields on purpose:

```yaml
version: 3.0.3-ps.1
appVersion: "3.0.3"
```

`appVersion` is the upstream Soperator release. It should match the lock
exactly. `version` is the Helm chart package version. It uses the same upstream
release as its base, then adds a packaging suffix such as `-ps.1`. This keeps
the release relationship obvious while still allowing chart-only fixes,
documentation fixes, cxcli wiring fixes, or example updates without pretending
Nebius Soperator itself released a new version.

Only `slurm_scripts/` is upstream-owned. It must be an exact copy of
`helm/slurm-cluster/slurm_scripts` from the locked Soperator release. The chart
sync does not own or overwrite:

- `Chart.yaml`
- `Chart.lock`
- `README.md`
- `CHANGELOG.md`
- `values.yaml`
- `values.schema.json`
- `scripts/`
- `templates/`
- `examples/`
- `docs/`

Those files are the local product layer. They contain the simplified values
interface, MK8s/SFS/NFS ownership boundary, cxcli wiring, local learning
profile, production guardrails, and the worker/partition design.

Validate the lock and script copy with:

```bash
scripts/verify-upstream-soperator-sync.sh
```

To intentionally move to a newer Soperator release:

1. Update `upstream-soperator.lock.yaml`.
2. Update `Chart.yaml.appVersion`, chart annotations, image tags, and
   `Chart.yaml.version`.
3. Run `scripts/verify-upstream-soperator-sync.sh --sync`.
4. Review the script diff and any Soperator CRD/schema changes.
5. Run Helm render tests for the default chart and all examples.

## What This Chart Installs

The chart installs the in-cluster side of a Slurm on Kubernetes deployment:

- Soperator operator.
- Soperator Custom Resource Definitions.
- RBAC permissions.
- Admission webhooks.
- `SlurmCluster` custom resource.
- `NodeSet` custom resources.
- `NodeConfigurator` custom resource.
- PVs, PVCs, and mount DaemonSets for storage integration.
- Slurm scripts, health checks, prolog scripts, and epilog scripts.
- OpenKruise and MariaDB Operator subcharts.

It does not create Nebius infrastructure. MK8s clusters, MK8s node groups, SFS
filesystems, and optional NFS VMs are Terraform-owned.

## Big Picture Flow

```text
Terraform
  creates MK8s node groups, SFS filesystems, optional NFS VM

nebius-cxcli
  writes Terraform inputs and Helm values for one MK8s target

Helm / Flux
  installs this chart into the target MK8s cluster

Soperator
  watches SlurmCluster, NodeSet, and NodeConfigurator resources

Slurm
  runs controller, login, accounting, REST, and worker daemons
```

## The Main Kubernetes Objects

### Soperator Operator

The Soperator operator is the controller that does the work.

In this chart it is rendered from:

- `templates/soperator/deployment.yaml`
- `templates/soperator/*-rbac.yaml`
- `templates/soperator/*webhook*.yaml`
- `values.yaml` under `controllerManager`

The operator watches Soperator custom resources and creates or updates the
Kubernetes objects needed for Slurm.

### Custom Resource Definitions

CRDs teach Kubernetes about Soperator resource types.

This chart vendors the Slurm CRD under:

- `crds/slurmcluster-crd.yaml`

The vendored CRDs keep schema validation but omit generated description text.
That keeps Helm release storage under the Kubernetes Secret size limit.

Helm installs files in `crds/` before normal templates. Treat CRD changes as
upgrade-sensitive work because CRD behavior is different from ordinary Helm
resources.

### RBAC

RBAC gives the operator and Slurm helper controllers the permissions they need.

This chart renders RBAC for:

- the Soperator manager.
- the Slurm controller power-management path.
- the SConfig controller.
- the Slurm exporter.
- NodeConfigurator and rebooter helpers.

RBAC files live under:

- `templates/soperator/`
- `templates/slurm-cluster/`
- `templates/nodeconfigurator/`

### Admission Webhooks

Admission webhooks validate or mutate Soperator resources when Kubernetes
accepts them.

This chart renders:

- `templates/soperator/mutating-webhook-configuration.yaml`
- `templates/soperator/validating-webhook-configuration.yaml`
- webhook service and certificate objects.

The chart expects cert-manager to be present through the wider cxcli app flow.
When `certManager.enabled=false`, the operator starts with webhooks disabled
and this chart does not render the Soperator webhook configurations. The
default remains `true` for Nebius/cxcli deployments.

## The Three Important Soperator CRs

Soperator is driven by three resource kinds in this chart.

### 1. SlurmCluster

`SlurmCluster` is the main cluster definition.

It tells Soperator:

- what the Slurm cluster is called.
- which Slurm daemons to run.
- where controller, login, accounting, REST, and exporter pods should run.
- which volumes exist.
- how partitions should be generated.
- which scripts and health checks Slurm should use.

Rendered from:

- `templates/slurm-cluster/slurm-cluster-cr.yaml`

Configured mostly from:

- `clusterName`
- `clusterType`
- `partitionConfiguration`
- `k8sNodeFilters`
- `volumeSources`
- `populateJail`
- `slurmConfig`
- `slurmNodes`
- `sConfigController`
- `externalNfs`

Short example of the rendered shape:

```yaml
apiVersion: slurm.nebius.ai/v1
kind: SlurmCluster
metadata:
  name: soperator
  namespace: soperator
spec:
  clusterType: gpu
  maintenance: "none"

  partitionConfiguration:
    configType: structured
    partitions:
      - name: main
        isAll: true
        config: Default=YES MaxTime=INFINITE State=UP

  k8sNodeFilters:
    - name: controller
      affinity:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
              - matchExpressions:
                  - key: slurm.nebius.ai/nodeset-name
                    operator: In
                    values: ["controller"]

    - name: login
      affinity:
        nodeAffinity:
          requiredDuringSchedulingIgnoredDuringExecution:
            nodeSelectorTerms:
              - matchExpressions:
                  - key: slurm.nebius.ai/nodeset-name
                    operator: In
                    values: ["login"]

  volumeSources:
    - name: slurm-scripts
      configMap:
        name: slurm-scripts
        defaultMode: 0755
    - name: jail
      persistentVolumeClaim:
        claimName: jail-pvc
        readOnly: false
    - name: controller-spool
      persistentVolumeClaim:
        claimName: controller-spool-pvc
        readOnly: false

  populateJail:
    image: cr.eu-north1.nebius.cloud/soperator/populate_jail:...
    k8sNodeFilterName: system
    overwrite: false

  slurmNodes:
    controller:
      k8sNodeFilterName: controller
      slurmctld:
        port: 6817
      volumes:
        spool:
          volumeSourceName: controller-spool
        jail:
          volumeSourceName: jail

    login:
      size: 1
      k8sNodeFilterName: login
      sshd:
        port: 22
      sshRootPublicKeys: []
      volumes:
        jail:
          volumeSourceName: jail

    accounting:
      enabled: true
      k8sNodeFilterName: accounting
      slurmdbd:
        port: 6819
      mariadbOperator:
        enabled: true

    rest:
      enabled: true
      k8sNodeFilterName: controller
      rest:
        port: 6820
```

This is not a full manifest. Use this command to see the exact rendered object:

```bash
helm template soperator . --namespace soperator \
  --show-only templates/slurm-cluster/slurm-cluster-cr.yaml
```

### 2. NodeSet

`NodeSet` defines Slurm worker nodes.

A `NodeSet` is not the same thing as an MK8s node group:

- MK8s node group: cloud host pool created by Terraform.
- Soperator `NodeSet`: in-cluster worker definition created by Helm.

Our default logical worker NodeSet is `worker-gpu`. cxcli can create many MK8s
node-group shards behind it, such as `worker-gpu-0`, `worker-gpu-1`, and so on.
Those host pools share this Kubernetes label:

```yaml
slurm.nebius.ai/nodeset-name: worker-gpu
```

The `NodeSet` uses that label to schedule worker pods onto the right hosts.

Rendered from:

- `templates/nodesets/nodeset.yaml`

Configured from:

- `nodesets[]`
- `images.slurmd`
- `images.munge`
- `volumeSources`
- `externalNfs`

Short example of the rendered shape:

```yaml
apiVersion: slurm.nebius.ai/v1alpha1
kind: NodeSet
metadata:
  name: worker-gpu
  namespace: soperator
spec:
  replicas: 1
  maxUnavailable: 1

  gpu:
    enabled: true
    nvidia:
      gdrCopyEnabled: true

  nodeConfig:
    features:
      - gpu
      - cuda
    static: Boards=1 SocketsPerBoard=1 CoresPerSocket=8 ThreadsPerCore=1
    dynamic: InstanceId={{ .PodName }}
    gresConfig:
      - AutoDetect=nvidia

  slurmd:
    image:
      repository: cr.eu-north1.nebius.cloud/soperator/worker_slurmd
      tag: 3.0.3-slurm25.11.3
    resources:
      cpu: "4"
      memory: 16Gi
      gpu: 8
    volumes:
      spool:
        emptyDir: {}
      jail:
        persistentVolumeClaim:
          claimName: jail-pvc

  munge:
    image:
      repository: cr.eu-north1.nebius.cloud/soperator/munge
      tag: 3.0.3-slurm25.11.3

  nodeSelector:
    slurm.nebius.ai/nodeset-name: worker-gpu
  tolerations:
    - key: nvidia.com/gpu
      operator: Exists
      effect: NoSchedule
```

Render the exact object with:

```bash
helm template soperator . --namespace soperator \
  --show-only templates/nodesets/nodeset.yaml
```

#### Jail And Scheduling Labels

There are two separate label concepts that are easy to mix up.

##### Jail Label

The jail label is a Kubernetes node label:

```yaml
slurm.nebius.ai/jail: "true"
```

It means the Nebius SFS jail filesystem is attached to that MK8s node group and
the chart is allowed to mount `/mnt/jail` on those Kubernetes hosts.

The MK8s Terraform module adds this label from generic `node_groups` data when
the group has `jail = true`:

```hcl
node_groups = {
  "worker-gpu-0" = {
    nodeset_name     = "worker-gpu"
    workload         = "worker"
    fixed_node_count = 100
    gpu              = true
    jail             = true
  }
}
```

The chart uses the jail label in two places:

- `templates/slurm-cluster-storage/jail-mount-daemonset.yaml`
- `templates/slurm-cluster-storage/jail-pv.yaml`

Default chart values:

```yaml
storage:
  jail:
    matchExpressions:
      - key: slurm.nebius.ai/jail
        operator: In
        values:
          - "true"
```

That makes the jail mount DaemonSet run only on hosts that should have the jail,
and it makes the local PV bind only to nodes that expose `/mnt/jail`.

The jail label is not a Slurm partition label. It is only the Kubernetes storage
placement guardrail for the shared root filesystem.

##### NodeSet Label

The NodeSet label is also a Kubernetes node label:

```yaml
slurm.nebius.ai/nodeset-name: worker-gpu
```

It maps MK8s host pools to a logical Soperator `NodeSet`.

Several MK8s node groups can share the same logical NodeSet label:

```text
worker-gpu-0 -> slurm.nebius.ai/nodeset-name=worker-gpu
worker-gpu-1 -> slurm.nebius.ai/nodeset-name=worker-gpu
worker-gpu-2 -> slurm.nebius.ai/nodeset-name=worker-gpu
```

The rendered `NodeSet` uses this label through `spec.nodeSelector`:

```yaml
apiVersion: slurm.nebius.ai/v1alpha1
kind: NodeSet
metadata:
  name: worker-gpu
spec:
  replicas: 256
  nodeSelector:
    slurm.nebius.ai/nodeset-name: worker-gpu
  tolerations:
    - key: nvidia.com/gpu
      operator: Exists
      effect: NoSchedule
```

Kubernetes then schedules the worker pods only onto nodes that have that label.
For GPU worker pools, the matching toleration is also required because GPU host
pools are tainted to keep non-GPU pods away from accelerator nodes.

##### Worker Scheduling Flow

The full flow is:

1. cxcli selects a Soperator profile for one MK8s target.
2. cxcli writes generic Terraform `mk8s.inputs.node_groups`.
3. Terraform creates MK8s node groups and applies labels such as
   `slurm.nebius.ai/nodeset-name`, `slurm.nebius.ai/jail`, and `nebius.com/gpu`.
4. Helm renders the jail PV and jail mount DaemonSet with node affinity matching
   `slurm.nebius.ai/jail=true`.
5. Helm renders one or more Soperator `NodeSet` resources.
6. Soperator creates worker pods for each `NodeSet`.
7. Kubernetes schedules those pods using the `NodeSet` `nodeSelector`,
   affinity, taints, and tolerations.
8. Each worker pod starts `slurmd` and registers with `slurmctld`.
9. Slurm assigns jobs to the registered nodes through partitions and
   `NodeSet` references.

The important rule is that storage placement and worker placement are separate:

- `slurm.nebius.ai/jail=true` answers: "Can this host mount the shared jail?"
- `slurm.nebius.ai/nodeset-name=<name>` answers: "Which Soperator worker or
  Slurm system role should land here?"

### 3. NodeConfigurator

`NodeConfigurator` prepares Kubernetes hosts for Slurm worker behavior.

In plain terms, it is the host setup layer. It can run privileged init
containers, configure host networking behavior, and enable Soperator helpers
such as the rebooter.

In this chart it is used for:

- host-level sysctl settings needed by the Slurm/GPU workload path.
- optional custom host preparation container.
- the rebooter helper.

Rendered from:

- `templates/nodeconfigurator/nodeconfigurator-cr.yaml`

Configured from:

- `hostNetwork`
- `customContainer`
- `rebooter`
- `initContainers`

Short example of the rendered shape:

```yaml
apiVersion: slurm.nebius.ai/v1alpha1
kind: NodeConfigurator
metadata:
  name: soperator-nodeconfigurator
  namespace: soperator
spec:
  hostNetwork: true

  customContainer:
    enabled: false
    image:
      repository: cr.eu-north1.nebius.cloud/soperator/busybox
      tag: latest

  rebooter:
    enabled: true
    evictionMethod: evict
    image:
      repository: cr.eu-north1.nebius.cloud/soperator/rebooter
      tag: 3.0.3

  initContainers:
    - name: node-sysctl-params
      securityContext:
        privileged: true
      command:
        - /bin/sh
        - -c
        - |
          sysctl -w kernel.unprivileged_userns_clone=1
          sysctl -w net.core.rmem_max=536870912
```

Render the exact object with:

```bash
helm template soperator . --namespace soperator \
  --show-only templates/nodeconfigurator/nodeconfigurator-cr.yaml
```

## Slurm Roles In This Chart

### System

`system` is an MK8s node-group role from the cxcli default profile.

It is CPU-only. The chart uses it for `populateJail`, which initializes the
shared jail filesystem.

Chart value:

```yaml
populateJail:
  k8sNodeFilterName: system
```

### Controller

`controller` is where the Slurm control plane runs.

It is CPU-only. It runs:

- `slurmctld`
- controller MUNGE
- SConfig controller
- Slurm REST by default

Chart value:

```yaml
slurmNodes:
  controller:
    k8sNodeFilterName: controller
```

### Login

`login` is where users connect with SSH.

It is CPU-only and mounts the jail, so users see the same shared environment
that jobs see on workers.

Chart value:

```yaml
slurmNodes:
  login:
    k8sNodeFilterName: login
    sshRootPublicKeys: []
```

Real deployments must provide SSH public keys. Do not put private keys or
placeholder customer keys in this repository.

### Accounting

`accounting` runs Slurm accounting.

It is CPU-only. It runs:

- `slurmdbd`
- accounting MUNGE
- MariaDB custom resource when chart-managed accounting is enabled

Chart value:

```yaml
slurmNodes:
  accounting:
    enabled: true
    k8sNodeFilterName: accounting
    mariadbOperator:
      enabled: true
```

There are two related MariaDB settings:

- `mariadb-operator.installOperator`: installs the MariaDB Operator subchart.
- `slurmNodes.accounting.mariadbOperator.enabled`: tells Soperator to use a
  MariaDB custom resource for Slurm accounting.

### Worker

`worker-gpu` is the default Slurm worker NodeSet.

It runs `slurmd` and MUNGE. It requests GPUs and mounts the jail.

Chart value:

```yaml
nodesets:
  - name: worker-gpu
    gpu:
      enabled: true
    nodeSelector:
      slurm.nebius.ai/nodeset-name: worker-gpu
```

Terraform can create one or more MK8s host pools behind that logical NodeSet.
For example:

```text
worker-gpu-0 -> label slurm.nebius.ai/nodeset-name=worker-gpu
worker-gpu-1 -> label slurm.nebius.ai/nodeset-name=worker-gpu
worker-gpu-2 -> label slurm.nebius.ai/nodeset-name=worker-gpu
```

Slurm sees one logical worker group. Kubernetes may use many cloud node groups
to host it.

## Supported Worker Scenarios

The chart supports three Slurm worker layouts:

| Scenario | Worker NodeSets | Slurm partitions | Cluster type |
| --- | --- | --- | --- |
| CPU-only workers | `worker-cpu` | `cpu` | `cpu` |
| GPU-only workers | `worker-gpu` | `gpu` | `gpu` |
| Mixed CPU+GPU workers | `worker-cpu`, `worker-gpu` | `cpu`, `gpu` | `gpu` |

The chart values model multiple worker `NodeSet` objects because
`templates/nodesets/nodeset.yaml` renders every entry in `nodesets[]`.

The stable rule is: one Soperator `NodeSet` represents one homogeneous Slurm
worker type. Do not use one `NodeSet` for different hardware shapes. If the
cluster has CPU and GPU workers, create separate NodeSets and separate
partitions. If the cluster has multiple GPU shapes, such as H100 and H200,
create separate GPU NodeSets and either one shared GPU partition or one
partition per shape, depending on how users should request capacity.

Common NodeSet names:

```text
worker-cpu      -> CPU worker nodes, no GPU resource request
worker-gpu      -> default GPU worker nodes
worker-h100     -> H100 GPU worker nodes
worker-h200     -> H200 GPU worker nodes
worker-gpu-ib   -> GPU workers with InfiniBand fabric
```

### CPU-Only Workers

Use a CPU-only worker Slurm cluster when no worker job needs GPU resources.
This is the simplest Slurm worker shape and should not enable GPU platform
apps.

Required design:

- `clusterType: cpu`
- one or more CPU worker `nodesets[]`
- `nodesets[].gpu.enabled: false`
- no `slurmd.resources.gpu`
- no GPU taint toleration on worker pods
- a `cpu` partition whose `nodeSetRefs` point to CPU worker NodeSets

Key chart values:

```yaml
clusterType: cpu

partitionConfiguration:
  configType: structured
  partitions:
    - name: cpu
      nodeSetRefs:
        - worker-cpu
      config: Default=YES MaxTime=INFINITE State=UP

nodesets:
  - name: worker-cpu
    gpu:
      enabled: false
    nodeConfig:
      features:
        - cpu
      static: Boards=1 SocketsPerBoard=1 CoresPerSocket=8 ThreadsPerCore=1
      dynamic: InstanceId={{ .PodName }}
    slurmd:
      resources:
        cpu: "8"
        memory: 64Gi
      volumes:
        spool:
          emptyDir: {}
        jail:
          persistentVolumeClaim:
            claimName: jail-pvc
    nodeSelector:
      slurm.nebius.ai/nodeset-name: worker-cpu
```

Key MK8s node group data:

```hcl
node_groups = {
  "worker-cpu-0" = {
    nodeset_name     = "worker-cpu"
    workload         = "worker"
    fixed_node_count = 100
    gpu              = false
    jail             = true
  }
}
```

CPU-only targets must not auto-enable the NVIDIA GPU Operator, DCGM exporter, or
Network Operator.

Expected scheduling behavior:

```bash
srun -p cpu -N1 hostname
```

Slurm should place the job only on nodes from the CPU partition.

### GPU-Only Workers

Use a GPU-only worker Slurm cluster when every worker job is expected to run on
GPU-backed nodes.

This is the current default cxcli profile: `nebius-gpu-v1`.

Required design:

- `clusterType: gpu`
- one or more GPU worker `nodesets[]`
- `nodesets[].gpu.enabled: true`
- `slurmd.resources.gpu` set to the GPU count per worker pod
- GPU taint toleration on worker pods when MK8s GPU nodes are tainted
- a `gpu` partition whose `nodeSetRefs` point to GPU worker NodeSets

Key chart values:

```yaml
clusterType: gpu

partitionConfiguration:
  configType: structured
  partitions:
    - name: gpu
      nodeSetRefs:
        - worker-gpu
      config: Default=YES MaxTime=INFINITE State=UP

nodesets:
  - name: worker-gpu
    gpu:
      enabled: true
      nvidia:
        gdrCopyEnabled: true
    nodeConfig:
      features:
        - gpu
        - cuda
      static: Boards=1 SocketsPerBoard=1 CoresPerSocket=8 ThreadsPerCore=1
      dynamic: InstanceId={{ .PodName }}
      gresConfig:
        - AutoDetect=nvidia
    slurmd:
      resources:
        cpu: "8"
        memory: 64Gi
        gpu: 8
      volumes:
        spool:
          emptyDir: {}
        jail:
          persistentVolumeClaim:
            claimName: jail-pvc
    nodeSelector:
      slurm.nebius.ai/nodeset-name: worker-gpu
    tolerations:
      - key: nvidia.com/gpu
        operator: Exists
        effect: NoSchedule
```

The chart derives Slurm `Gres=gpu:<count>` from `slurmd.resources.gpu` when
`gpu.enabled=true` and `nodeConfig.static` does not already include an explicit
`Gres=` value. In the example above, the rendered NodeSet static config becomes:

```text
Boards=1 SocketsPerBoard=1 CoresPerSocket=8 ThreadsPerCore=1 Gres=gpu:8
```

GPU targets should auto-enable the cxcli-managed NVIDIA GPU Operator path. DCGM
Exporter remains part of that path, not this chart. If the selected GPU platform
uses InfiniBand or GPU fabric, cxcli should also auto-enable Network Operator.

Expected scheduling behavior:

```bash
srun -p gpu --gres=gpu:1 -N1 nvidia-smi -L
```

Slurm should place the job only on GPU partition nodes and account for the
requested GPU GRES.

### Mixed CPU+GPU Workers

Use a mixed CPU+GPU worker Slurm cluster when one Slurm control plane should
serve both general CPU jobs and accelerated jobs.

Do not create a mixed-hardware `NodeSet`. The chart-side shape is two or more
homogeneous worker `NodeSet` objects:

```yaml
clusterType: gpu

partitionConfiguration:
  configType: structured
  partitions:
    - name: cpu
      nodeSetRefs:
        - worker-cpu
      config: Default=YES MaxTime=INFINITE State=UP PriorityTier=5
    - name: gpu
      nodeSetRefs:
        - worker-gpu
      config: Default=NO MaxTime=INFINITE State=UP PriorityTier=10

nodesets:
  - name: worker-cpu
    gpu:
      enabled: false
    nodeSelector:
      slurm.nebius.ai/nodeset-name: worker-cpu

  - name: worker-gpu
    gpu:
      enabled: true
    slurmd:
      resources:
        gpu: 8
    nodeSelector:
      slurm.nebius.ai/nodeset-name: worker-gpu
    tolerations:
      - key: nvidia.com/gpu
        operator: Exists
        effect: NoSchedule
```

`clusterType` should be `gpu` when any worker NodeSet has GPUs. That keeps GPU
health-check and GPU Slurm settings available for the GPU side of the cluster.

Required design:

- CPU and GPU workers are separate `NodeSet` objects.
- CPU and GPU workers have different MK8s node selectors.
- CPU and GPU workers map to separate Slurm partitions.
- GPU workers request Kubernetes GPUs and render Slurm `Gres=gpu:<count>`.
- CPU workers do not request GPUs and do not tolerate GPU taints unless there is
  a deliberate operational reason.

Expected scheduling behavior:

```bash
srun -p cpu -N1 hostname
srun -p gpu --gres=gpu:1 -N1 nvidia-smi -L
```

Live validation confirmed this shape on MK8s with two CPU workers and two H100
GPU workers in the same Slurm cluster:

- Kubernetes had four Ready nodes, labeled as two `worker-cpu` nodes and two
  `worker-gpu` nodes.
- Soperator reported `SlurmCluster` `Available`.
- Both `worker-cpu` and `worker-gpu` NodeSets reached `Ready`.
- `sinfo` showed `cpu*` and `gpu` partitions.
- `scontrol show nodes` showed GPU workers with `Gres=gpu:8` and
  `CfgTRES=...,gres/gpu=8`.
- CPU and GPU `srun` smoke jobs succeeded from the login pod.

## Slurm Partitions And Features

Slurm has two related but separate concepts:

- A partition is a queue over a set of nodes. Slurm permits the same nodes to
  appear in more than one partition, which is useful for policy queues such as
  `debug` and `long`.
- A feature is a node label used by job constraints. Users request it with
  commands such as `sbatch --constraint=h100` or
  `srun -p gpu --constraint=h100 ...`.

Soperator exposes both concepts directly, but they are configured in different
resources:

- Partitions are configured on the `SlurmCluster`, not on the `NodeSet`.
  Chart values under `partitionConfiguration` render to
  `SlurmCluster.spec.partitionConfiguration`.
- Structured partitions support `name`, `isAll`, `nodeSetRefs`, and `config`.
  The `config` string is where Slurm partition settings such as `Default`,
  `MaxTime`, `State`, `PriorityTier`, and `AllowGroups` belong.
- Features are configured on each worker `NodeSet`. Chart values under
  `nodesets[].nodeConfig.features` render to
  `NodeSet.spec.nodeConfig.features`.
- The connection between the two is the NodeSet name. A partition uses
  `nodeSetRefs` to include one or more NodeSets, and Slurm uses each included
  NodeSet's features for job constraints.
- `NodeConfigurator` does not define partitions or Slurm node features. It
  prepares Kubernetes hosts for Slurm worker behavior.

This chart follows that CRD model. It does not create a chart-specific
`partition.features` field because Slurm and Soperator do not model features
that way.

### Partition Examples

Use shape partitions when the queue should target a hardware class:

| Partition | NodeSet refs | Typical purpose |
| --- | --- | --- |
| `cpu` | `worker-cpu` | CPU-only jobs |
| `gpu` | `worker-gpu`, `worker-h100`, `worker-a100` | Generic GPU jobs |
| `h100` | `worker-h100` | NVIDIA H100 jobs |
| `highmem` | `worker-highmem` | Large-memory CPU jobs |
| `infiniband` | `worker-gpu-ib` | RDMA-capable jobs |

Use policy partitions when the queue should target a scheduling rule:

| Partition | NodeSet refs | Typical policy |
| --- | --- | --- |
| `debug` | selected worker NodeSets | Short jobs with low `MaxTime` |
| `long` | selected worker NodeSets | Long-running jobs with longer `MaxTime` |

Example:

```yaml
partitionConfiguration:
  configType: structured
  partitions:
    - name: gpu
      nodeSetRefs:
        - worker-h100
        - worker-a100
      config: Default=YES MaxTime=INFINITE State=UP PriorityTier=10
    - name: h100
      nodeSetRefs:
        - worker-h100
      config: Default=NO MaxTime=INFINITE State=UP PriorityTier=20
    - name: debug
      nodeSetRefs:
        - worker-h100
        - worker-a100
      config: Default=NO MaxTime=00:30:00 State=UP PriorityTier=100
```

### Feature Examples

Features belong on the NodeSet that represents the matching homogeneous worker
shape:

```yaml
nodesets:
  - name: worker-h100
    gpu:
      enabled: true
    nodeConfig:
      features:
        - gpu
        - cuda
        - h100
        - infiniband

  - name: worker-a100
    gpu:
      enabled: true
    nodeConfig:
      features:
        - gpu
        - cuda
        - a100
```

The user-facing Slurm behavior is then:

```bash
sbatch -p gpu --constraint=h100 train.sh
srun -p gpu --constraint=h100 --gres=gpu:1 nvidia-smi -L
srun -p h100 --gres=gpu:8 nvidia-smi -L
srun -p debug -N1 hostname
```

The `gpu` partition can point at both H100 and A100 NodeSets. A user can request
`--constraint=h100` inside the generic `gpu` partition, or use a dedicated
`h100` partition if the platform wants that queue to be explicit.
`--constraint=h100` filters eligible nodes by Slurm feature; `--gres=gpu:1`
requests one GPU from the selected node.

### cxcli Profile Design

cxcli should expose Soperator worker shape as catalog data, not Python
hardcoding. The profile set should look like this:

```text
nebius-cpu-v1    -> system, controller, login, accounting, worker-cpu
nebius-gpu-v1    -> system, controller, login, accounting, worker-gpu
nebius-mixed-v1  -> system, controller, login, accounting, worker-cpu, worker-gpu
```

Each worker profile entry should own its own sizing fields. CPU and GPU workers
must not share a single `gpu_node_groups` input.

Conceptual profile data:

```yaml
worker_nodesets:
  - name: worker-cpu
    nodeset_name: worker-cpu
    node_group_key_prefix: worker-cpu
    workload: worker
    gpu: false
    jail: true
    default_total_nodes: 4
    default_nodes_per_group: 4
    max_nodes_per_group: 100

  - name: worker-gpu
    nodeset_name: worker-gpu
    node_group_key_prefix: worker-gpu
    workload: worker
    gpu: true
    jail: true
    default_total_nodes: 1
    default_nodes_per_group: 1
    max_nodes_per_group: 100
```

The selected profile should materialize into:

- Terraform `mk8s.inputs.node_groups`.
- Terraform `sfs.inputs.filesystems`.
- Helm `nodesets[]`.
- Helm `partitionConfiguration.partitions[]`.
- Helm `k8sNodeFilters[]`.

cxcli also exposes a small partition profile selector. The default
`shape-default` leaves the shape partitions from the selected nodesets profile.
The `with-debug-long` profile adds policy partitions:

```yaml
values:
  partitionProfile: with-debug-long
```

That value is materialized into `SlurmCluster.spec.partitionConfiguration`, so
the persisted Soperator custom resource contains real Slurm partition data. It
does not invent another in-cluster resource or require Terraform changes.

Terraform stays generic. It should never contain fixed Soperator names such as
`worker-gpu`, `worker-cpu`, `controller`, or `login` in module logic. Those
names belong to cxcli profile data and direct Terraform caller input.

### Validation Rules

The product should fail fast when these mappings are inconsistent:

- Every Helm `nodesets[].name` has at least one MK8s node group labeled
  `slurm.nebius.ai/nodeset-name=<name>`.
- Every Soperator role or worker that mounts the jail has an MK8s node group
  with `jail = true`, which becomes `slurm.nebius.ai/jail=true`.
- CPU-only profiles set `clusterType: cpu` and do not enable GPU platform apps.
- GPU-only and mixed profiles set `clusterType: gpu` and enable the cxcli GPU
  Operator path.
- GPU NodeSets set `gpu.enabled=true`, request `slurmd.resources.gpu`, tolerate
  the GPU node taint, and render Slurm `Gres=gpu:<count>` from that GPU request
  unless `nodeConfig.static` already provides an explicit `Gres=` value.
- CPU NodeSets set `gpu.enabled=false`, do not request `slurmd.resources.gpu`,
  and do not tolerate the GPU taint unless there is a deliberate reason.
- Structured partitions use `nodeSetRefs` for heterogeneous worker groups
  instead of relying on one broad `isAll` partition.

## Production Operations Model

The chart is designed so day-2 changes go through the same durable inputs used
for install:

- Nebius infrastructure changes go through Terraform and cxcli infra
  components.
- In-cluster Soperator changes go through Helm values and the rendered
  `SlurmCluster` or `NodeSet` resources.
- Generated files inside pods, generated ConfigMaps, and OpenKruise
  StatefulSets are not the source of truth.

This model addresses the main production failure patterns seen in Soperator
clusters: manual Slurm config drift, MK8s node group scaling without matching
NodeSet replicas, immutable worker storage changes, and accounting/QoS changes
that are applied before SlurmDBD contains the required associations.

### Persistent Slurm Config

Global Slurm config belongs in chart values:

```yaml
customSlurmConfig: |
  PluginDir=/usr/lib/x86_64-linux-gnu/slurm
  AccountingStorageEnforce=associations,limits,qos
  EnforcePartLimits=Any
```

Partition-line config belongs in `partitionConfiguration`:

```yaml
partitionConfiguration:
  configType: custom
  rawConfig:
    - >-
      PartitionName=high Nodes=ALL Default=NO MaxTime=INFINITE State=UP
      PriorityTier=20 AllowQos=high
```

The result is persisted on `SlurmCluster.spec.customSlurmConfig` and
`SlurmCluster.spec.partitionConfiguration`. Users should verify both the custom
resource and the live Slurm view:

```bash
kubectl -n soperator get slurmcluster soperator -o yaml
kubectl -n soperator exec login-0 -c sshd -- scontrol show config
kubectl -n soperator exec login-0 -c sshd -- scontrol show partition high
```

### Scaling Workers

Worker scaling has two separate desired states:

- MK8s node group size: Kubernetes host capacity.
- Soperator `NodeSet.spec.replicas`: Slurm worker pod count.

Both must be changed together. Scaling only the MK8s node group creates unused
Kubernetes capacity. Scaling only the OpenKruise StatefulSet is temporary and
can be undone by Soperator. In cxcli, the Soperator profile should update the
MK8s node group count and the matching `nodesets[].replicas` value in the same
rendered config.

### Immutable NodeSet Storage

Soperator uses OpenKruise StatefulSets for worker pods. Fields that become
volume-claim-template data are not safe day-2 mutation points. For existing
production NodeSets, avoid in-place changes to storage class, disk type,
worker image storage, and claim-template-like fields.

The safe design is replacement by shape:

1. Create a new homogeneous NodeSet with the desired storage shape.
2. Add or move Slurm partitions to the new NodeSet.
3. Drain or complete jobs on the old NodeSet.
4. Remove the old NodeSet after workload migration.

### Accounting And QoS

Enabling SlurmDBD enforcement is more than a `slurm.conf` line. Before setting
`AccountingStorageEnforce=associations,limits,qos`, the cluster must already
have the required accounts, user associations, QoS objects, and service-user
associations for active checks or automation. Otherwise jobs or health checks
can be rejected by design.

Partition QoS restrictions are part of the partition line:

```yaml
partitionConfiguration:
  configType: custom
  rawConfig:
    - "PartitionName=gpu Nodes=ALL Default=YES MaxTime=INFINITE State=UP AllowQos=gpu"
```

### Memory Defaults

The Soperator 3.0.3 CRD defaults `slurmConfig.defMemPerNode` to `0`. Slurm
does not allow `DefMemPerCPU` and `DefMemPerNode` together, so the chart fails
rendering when `customSlurmConfig` contains `DefMemPerCPU`.

For GPU-only worker partitions, use a GPU-based memory default instead:

```yaml
slurmConfig:
  defCpuPerGPU: 8

customSlurmConfig: |
  PluginDir=/usr/lib/x86_64-linux-gnu/slurm
  DefMemPerGPU=131072
```

This keeps the memory policy compatible with the Soperator CRD default while
still aligning GPU jobs to a CPU and memory ratio.

### GPU Driver Capabilities

GPU worker NodeSets set container driver capabilities through
`nodesets[].slurmd.customEnv`:

```yaml
nodesets:
  - name: worker-gpu
    slurmd:
      customEnv:
        - name: NVIDIA_DRIVER_CAPABILITIES
          value: compute,graphics,utility,video
```

The default cxcli profile uses `compute,graphics,utility,video`. Keep this as a
NodeSet-level value because different worker shapes can have different
container requirements. For example, a pure training partition may use a
narrower set, while rendering or visualization workers may need `graphics`.

### AppArmor And User Namespaces

AppArmor profiles are configurable in values for login, controller,
accounting, and worker containers. Changing a profile is an operational change
that can restart affected pods.

Rootless container behavior should be tested through Slurm jobs, not direct SSH
into the login or worker jail. The SSH jail path can block user namespace
creation even when the same operation works from a Slurm job path:

```bash
srun -p gpu --gres=gpu:1 --pty bash
unshare --user echo test
```

Direct SSH into workers is also not equivalent to `srun` or `sbatch` for CUDA
library environment handling. Production GPU workloads should be validated via
Slurm jobs and their container environment, not by direct worker SSH.

## How Storage Connects

The chart uses three storage layers.

### 1. Nebius Infrastructure Storage

Terraform creates and attaches:

- SFS `jail`.
- SFS `controller-spool`.
- optional SFS `accounting`.
- optional jail submount filesystems.
- optional VM-based NFS server.

cxcli attaches SFS filesystems to MK8s node groups from profile data. The common
path is label-driven: `jail: true` attaches the jail filesystem, `workload:
controller` attaches `controller-spool`, and `workload: accounting` attaches
the accounting filesystem. Compact or custom profiles can also set
`sfs_filesystem_keys`, for example `["jail", "controller-spool"]`, when one CPU
node group intentionally hosts multiple Slurm roles.

Local Kind or Minikube learning clusters must not use Nebius SFS. For that
case only, the chart supports `volume.jail.type: local` and
`volume.controllerSpool.type: local`, which render local host-path PVs instead
of SFS mount commands. This is an opt-in development profile; the Nebius
production path stays SFS-backed.

The local profile intentionally keeps the Slurm control plane small:

- accounting is disabled.
- Slurm REST is disabled because upstream Soperator only reconciles REST when
  accounting has a database enabled.
- SConfigController is scaled to zero so it does not repeatedly call a REST
  service that does not exist in the no-accounting profile.
- `customSlurmConfig` sets `PlugStackConfig=/dev/null` so basic CPU `srun`
  works on local ARM and x86 clusters without requiring the production
  chroot/pyxis SPANK plugin path.

That local profile is for learning the Soperator and Slurm object model on a
vanilla one-node Kubernetes cluster. It is not the production Nebius path.
Production values keep accounting, Slurm REST, SConfigController, SFS-backed
jail storage, and the production SPANK plug stack enabled.

### 2. Kubernetes Storage Glue

Helm renders:

- PVs.
- PVCs.
- mount DaemonSets.
- mount scripts ConfigMap.

Storage templates live under:

- `templates/slurm-cluster-storage/`

### 3. Slurm Volumes

`SlurmCluster.spec.volumeSources` tells Soperator which volumes Slurm pods can
mount.

Default chart values:

```yaml
volumeSources:
  - name: controller-spool
    persistentVolumeClaim:
      claimName: controller-spool-pvc
      readOnly: false

  - name: jail
    persistentVolumeClaim:
      claimName: jail-pvc
      readOnly: false
```

If external NFS is enabled, the chart adds another `volumeSources` entry:

```yaml
externalNfs:
  enabled: true
  server: 10.0.0.10
  path: /srv/nfs/home
  mountPath: /home
```

NFS is a VM-based Terraform component. It is not an MK8s node group.

## Helm Dependencies

`Chart.yaml` declares two dependencies.

### OpenKruise

Values key:

```yaml
kruise:
  installOperator: true
```

OpenKruise provides controllers used by Soperator workload patterns.

### MariaDB Operator

Values key:

```yaml
mariadb-operator:
  installOperator: true
```

This installs the MariaDB Operator. The Soperator `SlurmCluster` then uses
`slurmNodes.accounting.mariadbOperator` to create the accounting database.

## How cxcli Wires The Infra And Chart

The cxcli Soperator profile lives in:

- `services/nebius-cxcli/component_cli_settings.yaml`
- path: `components.apps.soperator.cli.soperator_nodesets_profile`

The default profile is `nebius-gpu-v1`.

It seeds MK8s `node_groups` as data:

- `system`
- `controller`
- `login`
- `accounting`
- `worker-gpu-0`, `worker-gpu-1`, and so on

The Terraform `mk8s` module does not hardcode those names. It accepts a generic
`node_groups` map and creates one MK8s node group per enabled map entry.

The same profile also seeds SFS filesystems:

- `jail`
- `controller-spool`
- `accounting`

The Terraform `sfs` module does not hardcode those names either. It accepts a
generic `filesystems` map.

## What Happens During Install

1. User selects Soperator in cxcli.
2. cxcli writes MK8s node groups, SFS filesystems, and chart values.
3. Terraform creates Nebius infrastructure.
4. cxcli reads Terraform outputs.
5. cxcli renders Flux/Helm manifests.
6. Helm installs this chart.
7. Kubernetes registers Soperator CRDs.
8. Soperator operator starts.
9. Soperator sees `SlurmCluster`, `NodeSet`, and `NodeConfigurator`.
10. Soperator creates Slurm controller, login, accounting, REST, and workers.
11. `populateJail` initializes the jail.
12. Worker pods register with `slurmctld`.
13. Users SSH to login nodes and submit jobs with Slurm commands.

## What To Change For A New Shape

Change infrastructure shape in Terraform or cxcli profile data:

- MK8s node groups: `mk8s.inputs.node_groups`
- SFS filesystems: `sfs.inputs.filesystems`
- NFS VM: `nfs.inputs.*`

Change in-cluster Slurm behavior in Helm values:

- Slurm controller/login/accounting/REST: `slurmNodes`
- workers: `nodesets`
- partitions: `partitionConfiguration`
- storage mounts in Slurm pods: `volumeSources`
- storage glue: `volume`, `storage`, and `externalNfs`
- scripts: `slurmScripts`
- host preparation: `NodeConfigurator` values

## Things This Chart Intentionally Does Not Do

- It does not create MK8s clusters or node groups.
- It does not create SFS filesystems.
- It does not create the NFS VM.
- It does not install upstream `soperator-fluxcd`.
- It does not install upstream `soperator-dcgm-exporter`.
- It does not own the full observability stack.

GPU telemetry stays on the cxcli-managed NVIDIA GPU Operator DCGM Exporter
path.

## Useful Render Commands

Render everything:

```bash
helm template soperator . --namespace soperator >/tmp/soperator.yaml
```

Render only the main Slurm cluster object:

```bash
helm template soperator . --namespace soperator \
  --show-only templates/slurm-cluster/slurm-cluster-cr.yaml
```

Render only worker NodeSets:

```bash
helm template soperator . --namespace soperator \
  --show-only templates/nodesets/nodeset.yaml
```

Render only NodeConfigurator:

```bash
helm template soperator . --namespace soperator \
  --show-only templates/nodeconfigurator/nodeconfigurator-cr.yaml
```

Validate common examples:

```bash
helm lint --strict .

for file in examples/*-values.yaml; do
  helm template soperator . --namespace soperator -f "$file" >/dev/null
done
```
