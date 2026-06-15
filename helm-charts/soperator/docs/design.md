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

## Table Of Contents

- [Installation Boundary](#installation-boundary)
- [Direct Helm Default Readiness](#direct-helm-default-readiness)
- [Big Picture Flow](#big-picture-flow)
- [Core Concepts And Architecture](#core-concepts-and-architecture)
  - [Control And Reconcile Model](#control-and-reconcile-model)
  - [Runtime Components And Roles](#runtime-components-and-roles)
  - [Dependency Chart Responsibilities](#dependency-chart-responsibilities)
  - [Standard SFS Filesystems](#standard-sfs-filesystems)
  - [Placement And Sharing Rules](#placement-and-sharing-rules)
- [Runtime Kubernetes Objects](#runtime-kubernetes-objects)
  - [Soperator Operator](#soperator-operator)
  - [Custom Resource Definitions](#custom-resource-definitions)
  - [RBAC](#rbac)
  - [Admission Webhooks](#admission-webhooks)
- [Soperator Custom Resources](#soperator-custom-resources)
  - [1. SlurmCluster](#1-slurmcluster)
  - [2. NodeSet](#2-nodeset)
  - [3. NodeConfigurator](#3-nodeconfigurator)
- [Storage Design](#storage-design)
  - [1. Nebius Infrastructure Storage](#1-nebius-infrastructure-storage)
  - [2. Kubernetes Storage Glue](#2-kubernetes-storage-glue)
  - [3. Slurm Volumes](#3-slurm-volumes)
- [Slurm Role Model](#slurm-role-model)
  - [System](#system)
  - [Controller](#controller)
  - [Login](#login)
  - [Accounting](#accounting)
  - [Exporter](#exporter)
  - [REST](#rest)
  - [Worker](#worker)
- [Worker And Partition Design](#worker-and-partition-design)
  - [Supported Worker Scenarios](#supported-worker-scenarios)
    - [CPU-Only Workers](#cpu-only-workers)
    - [GPU-Only Workers](#gpu-only-workers)
    - [Mixed CPU+GPU Workers](#mixed-cpugpu-workers)
  - [Slurm Partitions And Features](#slurm-partitions-and-features)
    - [Partition Examples](#partition-examples)
    - [Feature Examples](#feature-examples)
  - [cxcli Profile Design](#cxcli-profile-design)
  - [Scheduling And Preemption](#scheduling-and-preemption)
  - [Validation Rules](#validation-rules)
- [Production Operations](#production-operations)
  - [Uninstall Model](#uninstall-model)
  - [Persistent Slurm Config](#persistent-slurm-config)
  - [Scaling Workers](#scaling-workers)
  - [Immutable NodeSet Storage](#immutable-nodeset-storage)
  - [Accounting And QoS](#accounting-and-qos)
  - [Enroot And Pyxis Cleanup](#enroot-and-pyxis-cleanup)
  - [Slurm Scripts Inventory](#slurm-scripts-inventory)
  - [Memory Defaults](#memory-defaults)
  - [GPU Driver Capabilities](#gpu-driver-capabilities)
  - [AppArmor And User Namespaces](#apparmor-and-user-namespaces)
- [Helm Dependencies](#helm-dependencies)
  - [OpenKruise](#openkruise)
  - [MariaDB Operator](#mariadb-operator)
  - [Optional Soperator-Family Child Charts](#optional-soperator-family-child-charts)
- [cxcli Integration](#cxcli-integration)
- [Install Flow](#install-flow)
- [Change Guide](#change-guide)
- [Optional Child Charts](#optional-child-charts)
- [Explicit Non-Goals](#explicit-non-goals)
- [Chart Release And OCI Publish](#chart-release-and-oci-publish)
- [Upstream Release Contract](#upstream-release-contract)
- [Reference Sources](#reference-sources)
- [Useful Render Commands](#useful-render-commands)

## Installation Boundary

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

The default chart values are sized for small-cluster portability first. For
example, `kruise.manager.replicas` defaults to `1` so OpenKruise does not block
single-node or small CPU-node installs, and
`slurmConfig.topologyPlugin` defaults to empty so worker init does not wait for
Soperator tier-label discovery on generic clusters. Production overlays can
raise the replica count and enable topology with a provider-specific
`controllerManager.manager.env.topologyLabelPrefix` when the target has
matching `tier-*` labels.

cxcli models that choice as `values.topologyProfile`. The default profile is
`disabled`; the `nebius-tiered-tree-v1` profile explicitly sets
`slurmConfig.topologyPlugin=topology/tree`,
`slurmConfig.topologyParam=SwitchAsNodeRank`, and
`controllerManager.manager.env.topologyLabelPrefix=topology.nebius.com`. This is
separate from the five-role production node-group shape: `system`,
`controller`, `login`, `accounting`, and `worker` can be created without
turning on topology, and topology should be enabled only when worker nodes
actually expose the matching `topology.nebius.com/tier-*` labels.

The distinction matters:

- The five-role production shape is operational placement: Soperator system
  pods, Slurm controllers, login pods, accounting pods, and Slurm workers land
  on the intended Kubernetes node groups.
- Slurm topology is worker locality: it gives Slurm a physical or fabric
  hierarchy for worker nodes so distributed jobs can prefer topologically close
  nodes.

Fresh Nebius production MK8s plus Soperator deployments should enable
`nebius-tiered-tree-v1` only when the provisioning flow also prepares accurate
`topology.nebius.com/tier-*` worker labels. Generic Kubernetes clusters,
arbitrary existing clusters, and already-installed Nebius MK8s clusters should
leave topology disabled until operators deliberately label and verify the
worker nodes. Manual pre-labeling before a direct Helm install is fine when the
labels are complete and truthful; misleading labels are worse than no topology
because Slurm can wait for unavailable topology data or schedule distributed
jobs across the wrong fabric domains.

Topology can help NCCL/MPI workloads because it improves Slurm's node-placement
input, not because it changes NCCL itself. Nebius documents
[topology-aware NCCL AllReduce tests](https://docs.nebius.com/compute/clusters/gpu/topology)
with gains up to 20% depending on cluster size, while Slurm's
[topology guide](https://slurm.schedmd.com/topology.html) frames
`topology/tree` as a way to minimize contention on hierarchical networks.
Production validation should still run NCCL tests on the rendered cluster
because topology benefits are workload and placement dependent.

It does not create Nebius infrastructure. MK8s clusters, MK8s node groups, SFS
filesystems, and optional NFS VMs are Terraform-owned.

`clusterName` is the Kubernetes object name for the rendered `SlurmCluster`
and `NodeConfigurator`, and it prefixes generated RBAC, ConfigMap, and
PriorityClass names. Keep it as a lowercase DNS-label value up to 38
characters, starting with a letter, so the longest generated suffix still fits
Kubernetes 63-character label-style resource names.

## Direct Helm Default Readiness

The README direct Helm command first runs `helm dependency build` so the local
checkout has dependency archives reconstructed from `Chart.lock`, then installs
`values.yaml` as-is plus the supplied SSH public key. That means the target
cluster must already match the selectors, storage names, and worker capacity in
those values before Helm install starts.

This is not a separate product mode. It is the "install exactly these values"
path for operators who already prepared the cluster manually. If the cluster
uses different labels, storage device names, paths, or worker shapes, change
the values to match the cluster. Helm renders the selectors from values; it
does not ignore labels.

Default role selectors:

- `populateJail` uses `populateJail.k8sNodeFilterName: system`, which points
  to `slurm.nebius.ai/nodeset-name=system`.
- Controller, REST, and SConfigController use
  `k8sNodeFilterName: controller`, which points to
  `slurm.nebius.ai/nodeset-name=controller` and tolerates the matching
  `NoSchedule` taint.
- Login uses `k8sNodeFilterName: login`, which points to
  `slurm.nebius.ai/nodeset-name=login`.
- Accounting uses `k8sNodeFilterName: accounting`, which points to
  `slurm.nebius.ai/nodeset-name=accounting` and tolerates the matching
  `NoSchedule` taint.
- Exporter uses `k8sNodeFilterName: no-gpu`, which selects nodes where
  `nebius.com/gpu` is not `true`.
- The default GPU worker uses `nodesets[].nodeSelector`, which points to
  `slurm.nebius.ai/nodeset-name=worker`.

Default storage expectations:

- The shared jail uses SFS/Filestore device `jail`, host path `/mnt/jail`, and
  PVC `jail-pvc`.
- Nodes that should mount the jail must match
  `storage.jail.matchExpressions`, which defaults to
  `slurm.nebius.ai/jail=true`.
- The Slurm controller spool uses SFS/Filestore device `controller-spool`, host
  path `/mnt/controller-spool`, and PVC `controller-spool-pvc`.
- Controller pods mount both storages: the jail for the shared Slurm runtime
  filesystem and controller spool for `slurmctld` state/spool data.

Default worker shape expectations:

- The default worker NodeSet is `worker`.
- `nodesets[].slurmd.resources` describes the `slurmd` worker pod resources,
  not the cloud VM shape itself.
- The default `slurmd` container requests `8` GPUs, `64` CPU, `512Gi` memory,
  and `50Gi` ephemeral storage. This is a conservative 8-GPU production slice:
  8 CPU and 64Gi memory per GPU, below the full Nebius 8-GPU H100/H200/B200
  node capacity so system components and sidecars retain headroom.
- The sizing is intentionally below Nebius 8-GPU platform presets such as
  H100/H200 `8gpu-128vcpu-1600gb`, B200 `8gpu-160vcpu-1792gb`, and B300
  `8gpu-192vcpu-2768gb`. See
  [Nebius VM platform presets](https://docs.nebius.com/compute/virtual-machines/types).
- The Kubernetes node must have enough allocatable capacity for that pod plus
  MUNGE and system overhead, or the values must be reduced.
- `nodesets[].nodeConfig.static` describes the Slurm node topology advertised
  to Slurm. It is a separate field from the Kubernetes CPU request, but the
  default keeps both aligned at 64 CPUs for the 8-GPU worker.

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

## Core Concepts And Architecture

The chart has three layers:

1. Helm renders Kubernetes objects, CRDs, RBAC, storage glue, and Soperator
   custom resources.
2. The Soperator manager reconciles those custom resources into Slurm runtime
   objects.
3. Slurm daemons run on the Kubernetes node groups selected by chart values and
   cxcli role mapping.

The important boundary is that Helm does not create Nebius infrastructure. MK8s
node groups and SFS filesystems come from Terraform or from an existing
cluster. Helm only consumes the labels, taints, PV/PVC names, mount tags, and
storage paths that describe those resources.

### Control And Reconcile Model

The parent chart renders these Soperator custom resources:

- `SlurmCluster`: the root cluster specification. It declares the cluster name,
  Slurm configuration, role placement filters, shared volume sources,
  `populateJail`, controller/login/accounting/REST/exporter settings,
  SConfigController settings, and Slurm partitions.
- `NodeSet`: a homogeneous Slurm worker definition. A NodeSet defines
  `slurmd`, MUNGE, GPU settings, resources, Slurm node features, GRES config,
  worker volumes, node selectors, and tolerations. One logical NodeSet can be
  backed by one or many MK8s node groups when those node groups share the same
  worker label.
- `NodeConfigurator`: the host-preparation and rebooter resource. It supports
  node-level preparation and safe eviction/reboot workflows used by Soperator
  runtime operations.

The Soperator manager watches these resources and creates the lower-level
runtime objects. Users should normally edit chart values or cxcli profile data,
not the generated child objects directly.

### Runtime Components And Roles

Soperator uses role placement rather than one flat node pool.

| Component | What It Does | Normal Placement |
| --- | --- | --- |
| Soperator manager | Kubernetes controller that watches `SlurmCluster`, `NodeSet`, and `NodeConfigurator` and reconciles Slurm runtime resources. | `system` CPU nodes in cxcli production profiles; configurable through `controllerManager.*`. |
| `system` role | Operational placement role, not a Slurm daemon. It hosts `populateJail` and can host chart/system helpers such as the Soperator manager, checks controller, and MariaDB operator webhooks. | CPU system node group. |
| Controller | Runs `slurmctld` and controller MUNGE. Owns Slurm scheduling/control-plane state. | CPU controller node group. |
| Worker | Runs `slurmd` and worker MUNGE through one or more `NodeSet` resources. Executes user jobs. | GPU node groups for GPU clusters; CPU worker node groups for CPU clusters. |
| Login | Runs SSH login pods and login-side MUNGE so users can submit and inspect jobs. | CPU login node group. |
| Accounting | Runs `slurmdbd` and accounting MUNGE. Integrates with MariaDB for job/accounting history. | CPU accounting node group. |
| REST | Runs `slurmrestd`. It is mainly a control-plane API used by SConfigController and can also serve Slurm API clients when exposed and secured by the operator. | CPU controller node group by default. |
| SConfigController | Writes and reconciles generated Slurm config in the populated jail and triggers Slurm reconfiguration through REST. | CPU controller node group by default. |
| Checks | Optional `soperator-checks` controller and `soperator-activechecks` jobs for readiness, health, GPU, NCCL, and maintenance checks. | System/control CPU nodes for controllers; checks may submit Slurm jobs to worker partitions. |
| MariaDB | Database used by Slurm accounting when chart-managed accounting is enabled. The MariaDB Operator owns the database CR; Soperator references it from the accounting spec. | Operator/webhooks on system CPU nodes in cxcli profiles; database workload belongs to accounting. |
| Kruise | OpenKruise controllers and CRDs used by Soperator workload patterns and lifecycle behavior. | System/control CPU nodes unless overridden by subchart values. |

The chart also renders an exporter role. The Slurm exporter is a Slurm metrics
collector and is separate from the optional DCGM exporter child chart.

### Dependency Chart Responsibilities

`Chart.yaml` packages dependency charts so a Soperator install has one parent
release while feature behavior remains value-driven.

The always-available operator dependencies are gated by
`kruise.installOperator` and `mariadb-operator.installOperator`. Optional
Soperator-family features are gated by their dependency names:
`soperator-checks.enabled`, `soperator-activechecks.enabled`,
`soperator-notifier.enabled`, `soperator-backup-config.enabled`, and
`soperator-dcgm-exporter.enabled`. Parent values keep those optional feature
gates disabled by default for production training installs.

- `kruise`: installs OpenKruise CRDs and controllers required by Soperator's
  workload and lifecycle model. It is enabled by `kruise.installOperator`.
- `mariadb-operator`: installs the MariaDB Operator CRDs, controller, and
  webhook used when `slurmNodes.accounting.mariadbOperator.enabled=true`.
  The parent chart defaults the webhook certificate path to cert-manager and
  disables the dependency chart's alternate cert-controller so the combined
  release has one certificate authority path.
- `soperator-checks`: installs the checks controller that reconciles
  Soperator check resources and can drain or report unhealthy Slurm nodes based
  on configured reactions.
- `soperator-activechecks`: installs pinned ActiveCheck definitions and
  PodTemplates for Slurm and Kubernetes checks, including NCCL, CUDA, DCGM,
  memory, SSH, topology wait, and jail-management checks.
- `soperator-dcgm-exporter`: installs a DCGM exporter wired to the Soperator
  Slurm job-mapping directory so GPU metrics can be related back to Slurm jobs.
- `soperator-notifier`: installs the notification path for Slurm job-state
  alerts through VictoriaMetrics Alertmanager and an existing Slack webhook
  Secret.
- `soperator-backup-config`: installs K8up backup schedules for the jail when
  object-storage backup is enabled.
- `k8up`: installs the K8up controller as a dependency of
  `soperator-backup-config`; it is enabled only when jail backup is enabled.

`cert-manager` is a prerequisite app in cxcli-managed installs, not a subchart
of this parent chart. The chart renders cert-manager `Issuer` and
`Certificate` objects for Soperator webhooks when `certManager.enabled=true`,
so cert-manager CRDs and controller must exist first.

### Standard SFS Filesystems

The Nebius production profile uses SFS for the shared Slurm filesystems:

| Filesystem | Mount Tag | Host Path | Main Consumers |
| --- | --- | --- | --- |
| `jail` | `jail` | `/mnt/jail` | `populateJail`, controller, login, accounting, and workers. |
| `controller-spool` | `controller-spool` | `/mnt/controller-spool` | Slurm controller pods and `slurmctld` state/spool. |
| `accounting` | `accounting` | `/mnt/accounting` | Accounting/database persistence when the production profile enables accounting storage. |

A mount tag is the Nebius SFS device name attached to the MK8s node group. It
is not the final pod mount by itself. The flow is:

1. Terraform attaches the SFS filesystem to the MK8s node-group template with a
   mount tag such as `jail`.
2. The chart's privileged mount DaemonSet uses that tag to mount the filesystem
   on the Kubernetes host path, such as `/mnt/jail`.
3. The chart renders PV/PVC objects that point at the host path.
4. Soperator mounts the PVC into Slurm pods through `SlurmCluster.volumeSources`
   or `NodeSet.slurmd.volumes`.

Direct Helm values enable the jail and controller-spool storage glue by
default. cxcli production profiles also seed an `accounting` SFS filesystem so
the accounting layer can use dedicated persistent storage when that profile
enables it.

### Placement And Sharing Rules

In the complete cxcli production profile, the five MK8s role groups share SFS
like this:

| MK8s Node Group | Soperator Role(s) | SFS Attached |
| --- | --- | --- |
| `system` | `populateJail`, Soperator manager, checks controller, MariaDB operator helpers | `jail` |
| `controller` | Slurm controller, REST, SConfigController | `jail`, `controller-spool` |
| `login` | Login pods | `jail` |
| `accounting` | `slurmdbd`, accounting MUNGE, MariaDB database workload | `jail`, `accounting` |
| `worker` or worker shards | Worker `NodeSet` pods and user jobs | `jail` |

In an onboarded or compact cluster, several Soperator roles may map to the same
CPU node group. In that case the node group must attach the union of the SFS
filesystems required by those roles. For example:

| Existing Node Group Mapping | Required SFS Attachments |
| --- | --- |
| GPU workers mapped to `worker` | `jail` |
| CPU nodes mapped to `system`, `controller`, `login`, and `accounting` | `jail`, `controller-spool`, `accounting` |

The chart still schedules by Kubernetes labels and filters. Sharing a node
group does not collapse the Slurm roles; it only means the same hosts satisfy
multiple role selectors and therefore need all storage attachments required by
those selected roles.

## Runtime Kubernetes Objects

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

This chart vendors the upstream Soperator CRD bundle under:

- `crds/slurmcluster-crd.yaml`

The vendored CRDs are exact upstream imports from the release pinned in
`upstream-soperator.lock.yaml`. Keeping them byte-for-byte aligned with the
operator image avoids API-schema drift when upstream adds fields, defaults, or
validation.

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

The chart expects cert-manager and its CRDs to be present through the wider
cxcli app flow before install. When `certManager.enabled=false`, the operator
starts with webhooks disabled and this chart does not render the Soperator
webhook configurations. The default remains `true` for Nebius/cxcli
deployments.

## Soperator Custom Resources

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

The default `k8sNodeFilters` are only for non-worker Slurm components and
CPU-only service placement: `system`, `controller`, `login`, `accounting`, and
`no-gpu`. The controller, login, and accounting filters include matching
tolerations for dedicated tainted service nodes. Worker `NodeSet` resources schedule
through their own `nodeSelector` and tolerations, including GPU workers.

The default SConfigController UID/GID is `0` because it writes generated Slurm
configuration into the populated jail, whose `/etc` tree is root-owned.

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
        name: soperator-slurm-scripts
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

Our default logical worker NodeSet is `worker`. cxcli can create many MK8s
node-group shards behind it with canonical node-group keys generated from the selected profile.
Those host pools share this Kubernetes label:

```yaml
slurm.nebius.ai/nodeset-name: worker
```

The `NodeSet` uses that label to schedule worker pods onto the right hosts.
That default does not mean the chart only supports GPU workers. CPU-only and
mixed clusters use the same pattern with different NodeSet names:

```text
CPU-only:        worker-cpu -> slurm.nebius.ai/nodeset-name=worker-cpu
GPU-only:        worker -> slurm.nebius.ai/nodeset-name=worker
Mixed CPU+GPU:   worker-cpu and worker-gpu, each with its own label
H100/H200 split: worker-h100 and worker-h200, each with its own label
```

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
  name: worker
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
    static: Boards=1 SocketsPerBoard=1 CoresPerSocket=64 ThreadsPerCore=1
    dynamic: InstanceId={{ .PodName }}
    gresConfig:
      - AutoDetect=nvidia

  slurmd:
    image:
      repository: cr.eu-north1.nebius.cloud/soperator/worker_slurmd
      tag: 4.0.2-slurm25.11.3
    resources:
      cpu: "64"
      memory: 512Gi
      gpu: 8
    volumes:
      spool:
        emptyDir: {}
      jail:
        persistentVolumeClaim:
          claimName: jail-pvc

    customVolumeMounts:
      - name: slurm-scripts
        mountPath: /opt/slurm_scripts/
        volumeSource:
          configMap:
            name: soperator-slurm-scripts
            defaultMode: 493
      - name: slurm-scripts-jail
        mountPath: /mnt/jail.upper/opt/slurm_scripts/
        volumeSource:
          configMap:
            name: soperator-slurm-scripts
            defaultMode: 493

  munge:
    image:
      repository: cr.eu-north1.nebius.cloud/soperator/munge
      tag: 4.0.2-slurm25.11.3

  nodeSelector:
    slurm.nebius.ai/nodeset-name: worker
  tolerations:
    - key: nvidia.com/gpu
      operator: Exists
      effect: NoSchedule
```

The worker `slurm-scripts` mounts are template-owned so values files do not have
to copy the same ConfigMap mount block into every NodeSet. The
`slurm-scripts` and `slurm-scripts-jail` mount names are reserved; use a
different name for additional
`nodesets[].slurmd.volumes.customVolumeMounts` entries.

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

The MK8s Terraform module receives this label as typed node-group data from
cxcli profile materialization or direct Terraform input:

```hcl
node_groups = {
  worker = {
    node_count = 100
    gpu        = true
    node_labels = {
      "slurm.nebius.ai/nodeset-name" = "worker"
      "slurm.nebius.ai/jail"         = "true"
    }
    sfs_filesystem_keys = ["jail"]
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
slurm.nebius.ai/nodeset-name: worker
```

It maps MK8s host pools to a logical Soperator `NodeSet`.

Several MK8s node groups can share the same logical NodeSet label:

```text
worker-0 -> slurm.nebius.ai/nodeset-name=worker
worker-1 -> slurm.nebius.ai/nodeset-name=worker
worker-2 -> slurm.nebius.ai/nodeset-name=worker
```

The rendered `NodeSet` uses this label through `spec.nodeSelector`:

```yaml
apiVersion: slurm.nebius.ai/v1alpha1
kind: NodeSet
metadata:
  name: worker
spec:
  replicas: 256
  nodeSelector:
    slurm.nebius.ai/nodeset-name: worker
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
such as the rebooter. The chart keeps a no-op `customContainer` enabled by
default so those initContainers have a valid long-running DaemonSet container.
The rebooter stays disabled by default; enable `rebooter.enabled=true` only when
the cluster should let NodeConfigurator run the worker-node reboot helper for
operator-triggered drain/handoff or reboot maintenance. cxcli's normal wizard
does not prompt this raw host-maintenance gate; set it deliberately in Helm
values or `config.yaml` only when Soperator-managed node maintenance is wanted.
This is a cluster-level
NodeConfigurator switch, not a per-NodeSet setting, and it does not reboot nodes
during chart install. The chart does not create a reboot schedule or CronJob by
itself.

In this chart it is used for:

- host-level sysctl settings needed by the Slurm/GPU workload path.
- optional custom host preparation container.
- optional rebooter helper.

The bundled rebooter RBAC includes cluster-scoped pod watch access and a
`pods/eviction` create grant, but the upstream rebooter path drains by
marking the node unschedulable, adding a `NoExecute` taint, and then checking
that non-DaemonSet pods without matching tolerations have left the node. When
worker nodes are tainted, `rebooter.tolerations` must cover the same taints as
the worker `NodeSet` tolerations so NodeConfigurator can run on every Slurm
worker host. Those tolerations affect helper placement only; a separate
Soperator/operator maintenance flow must set the node's `SlurmNodeDrain` or
`SlurmNodeReboot` condition before drain/handoff or reboot work starts. These are
Kubernetes Node status conditions written by the Soperator checks controllers:
maintenance starts from a condition such as `NebiusMaintenanceScheduled=True`,
becomes `SoperatorChecksNodeMaintenance=True` after Slurm workers drain, and
then becomes `SlurmNodeDrain=True`; reboot starts from a degraded Slurm reason
such as `Kill task failed` or `[compute_maintenance] node reboot process`,
becomes `SoperatorChecksNodeDegraded=True`, and then becomes
`SlurmNodeReboot=True`.
Advanced production-maintenance mode is an explicit operator opt-in to both
`soperator-checks.enabled=true` and `rebooter.enabled=true`. It has two
separate runtime intents. `NebiusMaintenanceScheduled=True` is graceful
maintenance drain and node handoff: Soperator drains Slurm workers, the rebooter
cordons and `NoExecute`-drains Kubernetes pods, and the checks controller can
delete the Kubernetes Node object for the maintenance platform. It does not call
the host `reboot now` path by itself. `SlurmNodeReboot=True` is the actual
Soperator host reboot path after drain. Prefer the upstream degraded-node path
that creates this condition from a Slurm reboot/degraded reason; direct external
writes to `SlurmNodeReboot=True` must happen only after Slurm workloads are
already drained.

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
    enabled: true
    image:
      repository: cr.eu-north1.nebius.cloud/soperator/busybox
      tag: latest
    command: ["/bin/sh", "-c", "trap : TERM INT; sleep infinity & wait"]

  rebooter:
    enabled: false
    evictionMethod: evict
    image:
      repository: cr.eu-north1.nebius.cloud/soperator/rebooter
      tag: 4.0.2

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

## Storage Design

The chart uses three storage layers.

### 1. Nebius Infrastructure Storage

Terraform creates and attaches:

- SFS `jail`.
- SFS `controller-spool`.
- optional SFS `accounting`.
- optional jail submount filesystems.
- optional VM-based NFS server.

cxcli attaches SFS filesystems to MK8s node groups from profile data. Profiles
set `sfs_filesystem_keys`, for example `["jail", "controller-spool"]`, when one
CPU node group intentionally hosts multiple Slurm roles. The bundled production
profile also treats `jail: true` as a convenience signal for the jail filesystem,
but role labels such as `workload: controller` or `workload: accounting` do not
implicitly attach SFS unless the profile declares the matching filesystem keys.
For already-created MK8s clusters, adding these SFS attachments to existing node
groups is disruptive. Nebius documents
[node-group template updates](https://docs.nebius.com/kubernetes/node-groups/manage#deployment-strategy-and-quotas)
as a rolling update: create a replacement node, cordon the old node, drain the
old node, and delete it. That can evict pods, restart Slurm workers, and
interrupt active jobs. cxcli onboarding should therefore treat SFS attachment
remediation as maintenance-window work and keep pure app/chart adoption
separate when the cluster is already running workloads.

Local Kubernetes learning clusters such as Kind, Minikube, or Docker Desktop
Kubernetes must not use Nebius SFS. For that case only, the chart supports
`volume.jail.type: local` and `volume.controllerSpool.type: local`, which
render Kubernetes `local` PVs backed by node-local paths instead of SFS mount
commands. This is an opt-in development profile; the Nebius production path
stays SFS-backed.

Kubernetes local PVs are node-local storage, not a shared filesystem. The local
one-node values profile therefore selects one labeled storage node with
`slurm.nebius.ai/local-storage=true` and schedules the local Slurm pods that
mount the jail or controller spool onto that node. A multi-node local
Kubernetes cluster can still be used for learning, but multi-node Slurm worker
tests need a real shared RWX backend such as NFS.

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
vanilla Kubernetes cluster. It is not the production Nebius path. Production
values keep accounting, Slurm REST, SConfigController, SFS-backed jail storage,
and the production SPANK plug stack enabled.

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

## Slurm Role Model

### System

`system` is an MK8s node-group role from the cxcli default profile.

It is CPU-only. The chart uses it for `populateJail`, which initializes the
shared jail filesystem.

`populateJail` is a one-time initialization job for the shared jail root
filesystem. It copies the default jail content from the pinned populate-jail
image, or from `populateJail.jailSnapshotVolume` when that override is set. It
must run on a node that can mount the jail storage before login and worker pods
depend on that filesystem.

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

The controller mounts both default shared storage volumes:

- `jail`, so controller-side Slurm runtime paths and scripts are consistent
  with login and worker pods.
- `controller-spool`, so `slurmctld` has persistent controller state/spool
  storage separate from the shared jail.

Chart value:

```yaml
slurmNodes:
  controller:
    k8sNodeFilterName: controller
```

The `slurmctld` liveness probe and resource requests are part of the same
typed value path and are intentionally configurable. Operators on busy
clusters where the scheduler ping cycle takes longer than the default
probe period (see Slack `#slurm-support` incident reports) should raise
`slurmNodes.controller.slurmctld.livenessProbe.failureThreshold` or
`periodSeconds` through normal value overrides instead of patching the
operator's ConfigMap, which would be reconciled away.

```yaml
slurmNodes:
  controller:
    slurmctld:
      livenessProbe:
        httpGet:
          path: /livez
          port: 6817
        initialDelaySeconds: 30
        periodSeconds: 60
        failureThreshold: 5
        timeoutSeconds: 10
      resources:
        cpu: "4000m"
        memory: "16Gi"
        ephemeralStorage: "100Gi"
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
placeholder customer keys in this repository. Helm values must contain public
key strings, not local filesystem paths. Use Helm `--set-file` when the source
key lives in a local file such as `$HOME/.ssh/id_ed25519.pub`.

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

### Exporter

`exporter` exposes Slurm metrics for Prometheus-compatible monitoring.

It is a metrics collector for Slurm state. It is not the GPU DCGM exporter.
The Slurm exporter reads Slurm/controller or accounting data and exposes
Prometheus-style metrics for Slurm jobs, nodes, and scheduler state.

It is CPU-only by default and uses the `no-gpu` node filter unless a values
overlay moves it elsewhere. Container resources and probes belong under
`slurmNodes.exporter.exporterContainer`.

Chart value:

```yaml
slurmNodes:
  exporter:
    enabled: true
    k8sNodeFilterName: no-gpu
    exporterContainer:
      resources:
        cpu: "250m"
        memory: "256Mi"
        ephemeralStorage: "500Mi"
```

### REST

`rest` runs `slurmrestd` for SConfig reconciliation and Slurm API access.

It is enabled by default and normally runs with the controller role because it
talks to the Slurm control plane.

In this chart, REST is primarily the API surface used by SConfigController to
reconcile Slurm configuration and accounting-related state. It can also serve
Slurm API clients when the cluster exposes and secures that path, but it should
be treated as a control-plane service, not a worker component.

`slurmrestd` is not the request path for normal Slurm CLI commands. Users and
automation that run `srun`, `sbatch`, `squeue`, `scontrol`, or similar Slurm
commands from login pods use the native Slurm client path to the controller
and, where applicable, accounting services. The ActiveChecks `srun` and
`sbatch` scripts follow the same native Slurm client behavior from their
check-job environment; they do not submit through the REST API.

Chart value:

```yaml
slurmNodes:
  rest:
    enabled: true
    k8sNodeFilterName: controller
```

### Worker

`worker` is the default Slurm worker NodeSet.

It runs `slurmd` and MUNGE. The default worker requests GPUs and mounts the
jail, but worker NodeSets are configurable. CPU-only clusters normally use
`worker-cpu`; mixed clusters use both `worker-cpu` and `worker-gpu`; GPU
clusters with multiple hardware classes can use names such as `worker-h100`
and `worker-h200`.

Chart value:

```yaml
nodesets:
  - name: worker
    gpu:
      enabled: true
    nodeSelector:
      slurm.nebius.ai/nodeset-name: worker
```

Terraform can create one or more MK8s host pools behind that logical NodeSet.
For example:

```text
worker-0 -> label slurm.nebius.ai/nodeset-name=worker
worker-1 -> label slurm.nebius.ai/nodeset-name=worker
worker-2 -> label slurm.nebius.ai/nodeset-name=worker
```

Slurm sees one logical worker group. Kubernetes may use many cloud node groups
to host it.

## Worker And Partition Design

### Supported Worker Scenarios

The chart supports three Slurm worker layouts:

| Scenario | Worker NodeSets | Slurm partitions | Cluster type |
| --- | --- | --- | --- |
| CPU-only workers | `worker-cpu` | `cpu` | `cpu` |
| GPU-only workers | `worker` | `gpu` | `gpu` |
| Mixed CPU+GPU workers | `worker-cpu`, `worker-gpu` | `cpu`, `gpu` | `gpu` |

The chart values model multiple worker `NodeSet` objects because
`templates/nodesets/nodeset.yaml` renders every entry in `nodesets[]`.

The stable rule is: one Soperator `NodeSet` represents one homogeneous Slurm
worker type. Do not use one `NodeSet` for different hardware shapes. If the
cluster has CPU and GPU workers, create separate NodeSets and separate
partitions. If the cluster has multiple GPU shapes, such as H100 and H200,
create separate GPU NodeSets and either one shared GPU partition or one
partition per shape, depending on how users should request capacity.

`nodesets[].slurmd.resources` is the Kubernetes resource request for each
worker pod's `slurmd` container. It is not the Nebius VM preset. The MK8s node
shape must have enough allocatable CPU, memory, GPUs, and ephemeral storage for
the rendered worker pod plus sidecars and system overhead. Separately,
`nodesets[].nodeConfig.static` describes the Slurm topology advertised to
Slurm, such as sockets, cores, and threads.

Common NodeSet names:

```text
worker-cpu      -> CPU worker nodes, no GPU resource request
worker          -> default GPU worker nodes
worker-h100     -> H100 GPU worker nodes
worker-h200     -> H200 GPU worker nodes
worker-gpu-ib   -> GPU workers with InfiniBand fabric
```

#### CPU-Only Workers

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
      static: Boards=1 SocketsPerBoard=1 CoresPerSocket=64 ThreadsPerCore=1
      dynamic: InstanceId={{ .PodName }}
    slurmd:
      resources:
        cpu: "64"
        memory: 512Gi
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
  "worker-cpu" = {
    node_count = 100
    gpu        = false
    node_labels = {
      "slurm.nebius.ai/nodeset-name" = "worker-cpu"
      "slurm.nebius.ai/jail"         = "true"
    }
    sfs_filesystem_keys = ["jail"]
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

#### GPU-Only Workers

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
        - worker
      config: Default=YES MaxTime=INFINITE State=UP

nodesets:
  - name: worker
    gpu:
      enabled: true
      nvidia:
        gdrCopyEnabled: true
    nodeConfig:
      features:
        - gpu
        - cuda
      static: Boards=1 SocketsPerBoard=1 CoresPerSocket=64 ThreadsPerCore=1
      dynamic: InstanceId={{ .PodName }}
      gresConfig:
        - AutoDetect=nvidia
    slurmd:
      resources:
        cpu: "64"
        memory: 512Gi
        gpu: 8
      volumes:
        spool:
          emptyDir: {}
        jail:
          persistentVolumeClaim:
            claimName: jail-pvc
    nodeSelector:
      slurm.nebius.ai/nodeset-name: worker
    tolerations:
      - key: nvidia.com/gpu
        operator: Exists
        effect: NoSchedule
```

The chart derives Slurm `Gres=gpu:<count>` from `slurmd.resources.gpu` when
`gpu.enabled=true` and `nodeConfig.static` does not already include an explicit
`Gres=` value. In the example above, the rendered NodeSet static config becomes:

```text
Boards=1 SocketsPerBoard=1 CoresPerSocket=64 ThreadsPerCore=1 Gres=gpu:8
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

#### Mixed CPU+GPU Workers

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

### Slurm Partitions And Features

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
- Structured partitions support `name`, `isAll`, `nodeSetRefs`, `policy`, and
  `config`. The typed `policy` block holds Slurm partition settings such as
  `Default`, `MaxTime`, `State`, `PriorityTier`, `AllowAccounts`, `AllowQos`,
  and `OverSubscribe` as typed fields. The free-form `config` string is an
  escape hatch for any Slurm.conf partition token not modeled in `policy`.
  At render time the chart joins typed `policy` tokens first, then appends
  `config` verbatim. Setting the same key in both `policy` and `config`
  fails the template render (see [Scheduling And Preemption](#scheduling-and-preemption)).
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

#### Partition Examples

Use shape partitions when the queue should target a hardware class:

| Partition | NodeSet refs | Typical purpose |
| --- | --- | --- |
| `cpu` | `worker-cpu` | CPU-only jobs |
| `gpu` | `worker`, `worker-h100`, `worker-a100` | Generic GPU jobs |
| `h100` | `worker-h100` | NVIDIA H100 jobs |
| `highmem` | `worker-highmem` | Large-memory CPU jobs |
| `infiniband` | `worker-gpu-ib` | RDMA-capable jobs |

Use policy partitions when the queue should target a scheduling rule:

| Partition | NodeSet refs | Typical policy |
| --- | --- | --- |
| `debug` | selected worker NodeSets | Short jobs with low `MaxTime` |
| `long` | selected worker NodeSets | Long-running jobs with longer `MaxTime` |

Example using the typed `policy` block (preferred):

```yaml
partitionConfiguration:
  configType: structured
  partitions:
    - name: gpu
      nodeSetRefs:
        - worker-h100
        - worker-a100
      policy:
        default: true
        state: UP
        maxTime: INFINITE
        priorityTier: 10
    - name: h100
      nodeSetRefs:
        - worker-h100
      policy:
        default: false
        state: UP
        maxTime: INFINITE
        priorityTier: 20
    - name: debug
      nodeSetRefs:
        - worker-h100
        - worker-a100
      policy:
        default: false
        state: UP
        maxTime: "00:30:00"
        priorityTier: 100
```

The free-form `config` string remains available for tokens not modeled in
`policy` (for example `MaxNodes=` or vendor-specific options); typed tokens
are emitted first, then `config` is appended.

#### Feature Examples

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

#### cxcli Profile Design

cxcli should expose Soperator worker shape as catalog data, not Python
hardcoding. The profile set should look like this:

```text
nebius-cpu-v1    -> system, controller, login, accounting, worker-cpu
nebius-gpu-v1    -> system, controller, login, accounting, worker
nebius-mixed-v1  -> system, controller, login, accounting, worker-cpu, worker-gpu
```

Each worker profile entry should own its own sizing fields. CPU and GPU workers
must not share shortcut-derived MK8s inputs.

For an existing MK8s cluster, cxcli uses the same profile data as a role map
instead of assuming the physical node groups are named after the Soperator
roles. The persisted app value is:

```yaml
values:
  nodeGroupMapping:
    system: [cpu-a]
    controller: [cpu-a]
    login: [cpu-a]
    accounting: [cpu-a]
    worker: [h100]
```

The renderer converts that map into chart-native values: role
`k8sNodeFilters[]` select `nebius.com/node-group`, worker `nodesets[]` select
the mapped worker groups, storage `matchExpressions` select the same mapped
node groups for jail, controller-spool, and accounting mounts, and partitions
reference the generated NodeSet names. The mapped `system` filter also feeds
the Soperator manager, checks controller, and MariaDB operator affinities so
chart-owned helper pods stay on system/CPU nodes.
The map is a cxcli convenience layer; direct Helm users can set
`k8sNodeFilters[]`, `nodesets[]`, `storage.*`, and
`partitionConfiguration` directly.

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
- Helm `k8sNodeFilters[]` for service pods only; worker pods use NodeSet
  selectors and tolerations.

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

#### Scheduling And Preemption

Slurm scheduling, accounting enforcement, preemption, and per-partition policy
are configured through typed value surfaces that the chart owns and translates
into Slurm.conf content at template time. The Soperator CRD types only a small
subset of Slurm.conf scheduling keys (`priorityWeightAge`,
`priorityWeightFairshare`, `priorityWeightQOS`, `priorityWeightTRES`, under
`slurmNodes.accounting.slurmConfig`). Other modeled Slurm.conf keys are emitted
into `customSlurmConfig`; this chart wraps that escape hatch behind structured
values so cxcli and operators do not concatenate Slurm.conf strings by hand.

The public references for these fields are Slurm's scheduling, preemption, and
accounting manuals. This chart documents the subset it models below so
operators can keep scheduling policy in values instead of copying raw
Slurm.conf snippets.

##### Global Scheduling Surface

The top-level `schedulingConfig` block defines cluster-wide scheduling and
preemption keys. Each field is optional; absent keys keep Slurm's built-in
defaults.

```yaml
schedulingConfig:
  preemptType: preempt/partition_prio
  accountingStorageEnforce:
    - associations
    - limits
    - qos
  enforcePartLimits: ANY
  preemptMode: REQUEUE
  preemptParameters:
    - send_user_signal
  jobRequeue: 1
  schedulerType: sched/backfill
  schedulerParameters:
    - sched_min_interval=2000000
    - bf_max_job_test=2000
  priorityType: priority/multifactor
  priorityWeights:
    fairshare: 100
    partition: 1000
    jobSize: 500
    qos: 1000
```

| Chart value | Slurm.conf key | Notes |
| --- | --- | --- |
| `preemptType` | `PreemptType` | One of `preempt/partition_prio`, `preempt/qos`, `preempt/none` |
| `accountingStorageEnforce` | `AccountingStorageEnforce` | List; comma-joined. Common QOS/fairshare enforcement uses `associations,limits,qos` |
| `enforcePartLimits` | `EnforcePartLimits` | `ALL`, `ANY`, or `NO` submit-time partition-limit behavior |
| `preemptMode` | `PreemptMode` | One or more of `OFF`, `CANCEL`, `REQUEUE`, `SUSPEND`, `GANG`, comma-joined |
| `preemptParameters` | `PreemptParameters` | List; emitted as comma-joined string |
| `jobRequeue` | `JobRequeue` | `0` or `1`; required for `PreemptMode=REQUEUE` to apply without `--requeue` |
| `schedulerType` | `SchedulerType` | Typical `sched/backfill` |
| `schedulerParameters` | `SchedulerParameters` | List; comma-joined. Used for big-cluster tuning |
| `priorityType` | `PriorityType` | Typical `priority/multifactor` |
| `priorityWeights.age` | `PriorityWeightAge` | Multifactor priority weight |
| `priorityWeights.assoc` | `PriorityWeightAssoc` | Multifactor priority weight |
| `priorityWeights.fairshare` | `PriorityWeightFairshare` | Multifactor priority weight; requires Slurm accounting |
| `priorityWeights.partition` | `PriorityWeightPartition` | Multifactor priority weight |
| `priorityWeights.jobSize` | `PriorityWeightJobSize` | Multifactor priority weight |
| `priorityWeights.qos` | `PriorityWeightQOS` | Multifactor priority weight; only affects QOS priority when non-zero |
| `priorityWeights.tres` | `PriorityWeightTRES` | TRES priority weight string, for example `gres/gpu=1000,cpu=100` |

Do not also put the same `PriorityWeight*` key in `customSlurmConfig`; the chart
fails the render when a typed scheduling key overlaps the raw escape hatch.

##### Per-Partition Policy Surface

Each partition supports a typed `policy` block alongside the free-form
`config` escape hatch:

```yaml
partitionConfiguration:
  configType: structured
  partitions:
    - name: research
      nodeSetRefs:
        - worker-gpu
      policy:
        default: true
        state: UP
        maxTime: "8:00:00"
        defaultTime: "02:00:00"
        priorityTier: 10
        preemptMode: REQUEUE
        allowAccounts:
          - research
        allowQos:
          - normal
          - high
        overSubscribe: "NO"
```

The renderer joins typed `policy` tokens first, then appends the free-form
`config` value (if any). Supported typed fields:

| Chart value | Slurm.conf token | Notes |
| --- | --- | --- |
| `default` | `Default=YES/NO` | Boolean |
| `hidden` | `Hidden=YES/NO` | Boolean |
| `state` | `State` | `UP`, `DOWN`, `DRAIN`, `INACTIVE` |
| `maxTime` | `MaxTime` | Slurm time spec |
| `defaultTime` | `DefaultTime` | Slurm time spec |
| `priorityTier` | `PriorityTier` | Integer |
| `preemptMode` | `PreemptMode` | Partition-level override |
| `defMemPerNode` | `DefMemPerNode` | Integer (MB) |
| `defMemPerCPU` | `DefMemPerCPU` | Integer (MB); mutually exclusive with `defMemPerNode` |
| `defMemPerGPU` | `DefMemPerGPU` | Integer (MB) |
| `defCpuPerGPU` | `DefCpuPerGPU` | Integer |
| `overSubscribe` | `OverSubscribe` | `NO`, `YES`, `EXCLUSIVE`, or `FORCE[:N]` |
| `allowAccounts` | `AllowAccounts` | List; comma-joined |
| `allowQos` | `AllowQos` | List; comma-joined |
| `denyAccounts` | `DenyAccounts` | List; comma-joined |
| `denyQos` | `DenyQos` | List; comma-joined |

Any Slurm.conf token not modeled here can still be provided through the
partition's `config` string. The chart emits typed tokens first, then
appends `config` verbatim. For example:

```yaml
- name: research
  policy:
    priorityTier: 10
  config: "MaxNodes=128 OverTimeLimit=10"
```

renders as `PriorityTier=10 MaxNodes=128 OverTimeLimit=10`.

##### Overlap Detection

Setting the same Slurm.conf key in both the typed surface and the free-form
raw string fails the template render with an actionable error. This applies
to two layers:

- `schedulingConfig.<field>` vs. raw `customSlurmConfig` content. The
  validator scans `customSlurmConfig` for `^\s*<Key>=` on any line and
  rejects the render when a typed field is also set.
- `partitionConfiguration.partitions[].policy.<field>` vs. the same
  partition's `config` string. The validator scans the partition's `config`
  for the corresponding token and rejects the render on collision.

The intent is to make the typed surface the single source of truth for
modeled keys, while keeping the raw escape hatch available for unmodeled
ones. The chart does not silently merge; users must pick one source per
key.

##### Practical Patterns

Two well-known production patterns map directly onto these surfaces:

- **Partition + preemption only** (deterministic scheduling). Set
  `schedulingConfig.preemptType: preempt/partition_prio`, give each
  partition a distinct `policy.priorityTier`, and set
  `schedulingConfig.preemptMode` plus per-partition `policy.preemptMode`
  overrides. Keep `priorityWeight*` zeroed so partition tier drives the
  decision.
- **QOS + fairshare** (multi-tenant fairness). Set
  `schedulingConfig.preemptType: preempt/qos`, set non-zero
  `schedulingConfig.priorityWeights.fairshare` / `qos` / `age` values when
  those priority factors should matter, and reference QOS names from each
  partition's `policy.allowQos` list. For self-managed clusters, enable
  `qosConfiguration` to reconcile the matching accounts, QOS objects,
  associations, fairshare values, and QOS preemption relationships through
  `sacctmgr`. For Managed Soperator, keep `qosConfiguration.enabled=false`
  because the chart hook cannot run in the managed operator namespace.

##### Large-Cluster Tuning Notes

For 2k-5k node deployments, three orthogonal tuning surfaces matter and
they all live in the chart's typed values rather than in raw config
strings or out-of-band patches:

- `schedulingConfig.schedulerParameters` is the durable knob for slurmctld
  scheduler performance. Common entries:
  - `sched_min_interval=<microseconds>` — minimum interval between
    scheduling cycles.
  - `bf_max_job_test=<count>` — backfill scheduler test depth.
  - `bf_continue` — keep backfilling across cycles.
  - `max_depend_depth=<n>` — limit dependency-chain depth.
  Values are workload-specific. The chart treats this field as opaque
  list-of-strings input; cxcli profiles or per-cluster value overrides
  should populate it based on the cluster's measured behavior on the
  Slurm controller dashboard rather than chart defaults.
- `controllerManager.manager.kubeClient.{qps,burst}` tunes the operator's
  Kubernetes API client. The Big Cluster PoC findings showed that
  client-go default rate limits throttle the manager on busy mk8s control
  planes. These keys are emitted as `KUBE_API_QPS` and `KUBE_API_BURST`
  environment variables on the operator container; leave them empty on
  small clusters so client-go defaults apply.
- `partitionConfiguration.includeFile` appends a single `Include=<path>`
  line to `customSlurmConfig`. Use this to point Slurm at a partition
  file mounted into the controller pod by external IaC. This lets a
  customer with sufficient access edit their own partitions outside the
  chart-managed list without re-rendering the chart, which is the
  pattern requested by the support customer in `#slurm-support`. The
  chart does not create the included file or its volume; that is an
  operator responsibility (typically via a ConfigMap or external SFS
  mount referenced under the controller's volume layout).

Worker-storage tuning, controller storage class selection, MariaDB
sizing, and HelmRelease timeouts are covered through their existing
typed value paths under `slurmNodes.controller.*`,
`slurmNodes.accounting.mariadbOperator.storage.*`, and FluxCD or other
deployment-tool config respectively, and are intentionally left out of
this chart's surface.

#### Validation Rules

The product should fail fast when these mappings are inconsistent:

- Every Helm `nodesets[].name` has at least one MK8s node group labeled
  `slurm.nebius.ai/nodeset-name=<name>`.
- Every Soperator role or worker that mounts the jail has an MK8s node group
  with `jail = true`, which becomes `slurm.nebius.ai/jail=true`.
- CPU-only profiles set `clusterType: cpu` and do not enable GPU platform apps.
- cxcli profiles keep an internal `hidden` partition for upstream ActiveChecks
  while still rendering visible shape partitions such as `cpu` and `gpu`.
  CPU-only profiles can set `soperator-activechecks.srunReadyPartition` to the
  visible `cpu` partition for the readiness probe.
- GPU-only and mixed profiles set `clusterType: gpu` and enable the cxcli GPU
  Operator path.
- GPU NodeSets set `gpu.enabled=true`, request `slurmd.resources.gpu`, tolerate
  the GPU node taint, and render Slurm `Gres=gpu:<count>` from that GPU request
  unless `nodeConfig.static` already provides an explicit `Gres=` value.
- CPU NodeSets set `gpu.enabled=false`, do not request `slurmd.resources.gpu`,
  and do not tolerate the GPU taint unless there is a deliberate reason.
- Structured partitions use `nodeSetRefs` for heterogeneous worker groups
  instead of relying on one broad `isAll` partition.

## Production Operations

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

### Uninstall Model

The chart owns Soperator CRs and also installs the OpenKruise dependency by
default. OpenKruise's pre-delete finalizer refuses to uninstall while Advanced
StatefulSets still exist. Because Soperator creates those Advanced StatefulSets
from the chart-owned `SlurmCluster` and `NodeSet` CRs, this chart renders an
earlier `pre-delete` hook that deletes the chart-owned Soperator CRs and waits
for their OpenKruise child workloads to disappear before the OpenKruise hook
runs.

The hook is controlled by `uninstallCleanup`. It uses a `kubectl`-capable image,
deletes only Soperator CRs labeled with the current Helm release, and waits for
Advanced StatefulSets labeled with the Soperator `clusterName` in the release
namespace. Helm still leaves CRDs behind after uninstall, so CRD removal is a
separate cluster-admin cleanup step.

Set `uninstallCleanup.image` to an approved internal mirror when the cluster
cannot pull the default public `alpine/k8s` image.

### Persistent Slurm Config

Modeled scheduling, accounting enforcement, and preemption keys belong in
`schedulingConfig`. Keep `customSlurmConfig` for Slurm.conf keys the chart does
not model:

```yaml
schedulingConfig:
  preemptType: preempt/qos
  accountingStorageEnforce:
    - associations
    - limits
    - qos
  enforcePartLimits: ANY
  preemptMode: REQUEUE
  preemptParameters: [send_user_signal]
  priorityWeights:
    fairshare: 100
    qos: 1000
```

Partition-line config belongs in `partitionConfiguration`. Use the structured
typed `policy` block when the token is modeled, and reserve raw `config` or
`rawConfig` for unmodeled Slurm partition tokens:

```yaml
partitionConfiguration:
  configType: structured
  partitions:
    - name: high
      nodeSetRefs: [worker-gpu]
      policy:
        default: false
        state: UP
        maxTime: INFINITE
        priorityTier: 20
        allowQos: [high]
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

This chart can provision the QoS lifecycle declaratively through the
`qosConfiguration` block. The block defines accounts, QoS objects, and
user/account associations as typed values; a Helm hook Job runs after install
and upgrade and reconciles them through `sacctmgr` against the live SlurmDBD.
The reconcile script is idempotent and reuses the accounting pod's munge
authentication by streaming the script into that pod via `kubectl exec -i`, so
the Job does not need to mount the munge key, depend on `kubectl cp`/`tar`, or
know the SlurmDBD endpoint directly. The default Job image is `alpine/k8s:1.33.5`,
which provides both Bash and kubectl. The default active deadline is longer
than the accounting-pod readiness wait plus the in-pod SlurmDBD readiness wait,
so slow startup can fail from the explicit readiness checks rather than the
Job deadline racing them. Reconcile order is fixed:
accounts -> QoS base fields -> QoS preemption relationships -> associations.
The separate preemption pass matters because Slurm rejects `Preempt=<qos>`
references until every referenced QoS object exists.

```yaml
schedulingConfig:
  preemptType: preempt/qos
  accountingStorageEnforce:
    - associations
    - limits
    - qos
  enforcePartLimits: ANY
  preemptMode: REQUEUE
  preemptParameters: [send_user_signal]
  jobRequeue: 1
  priorityWeights:
    fairshare: 100
    qos: 1000

qosConfiguration:
  enabled: true
  accounts:
    - name: research
      organization: ai-team
  qos:
    - name: high
      priority: 1000
      maxJobs: 200
      maxWallSeconds: 7200
      preempt: [normal, low]
    - name: normal
      priority: 100
    - name: low
      priority: 10
  associations:
    - user: alice
      account: research
      defaultQos: high
      qos: [normal, high]

partitionConfiguration:
  configType: structured
  partitions:
    - name: gpu
      nodeSetRefs: [worker-gpu]
      policy:
        default: true
        state: UP
        maxTime: INFINITE
        priorityTier: 30
        allowQos: [high, normal, low]
```

The Job is disabled by default (`qosConfiguration.enabled: false`) so the
chart's "no surprise side effects" contract continues to hold. Enabling it is
a deliberate opt-in for environments where IaC ownership of SlurmDBD content
is acceptable. Managed Soperator on the Nebius AI Cloud Console does not
expose the operator namespace; the reconcile Job cannot run there, and QOS
changes must go through Nebius support. For those targets keep
`qosConfiguration.enabled` at the default and treat the typed surface as a
documented schema only.

`AllowQos` and per-partition QoS restrictions live in the typed partition
policy (`partitions[].policy.allowQos` / `policy.denyQos`) and are emitted
into Slurm.conf alongside the other partition tokens. See
[Scheduling And Preemption](#scheduling-and-preemption) for the full mapping.

### Enroot And Pyxis Cleanup

The upstream cleanup scripts are kept as byte-for-byte script imports on disk.
The parent chart overrides `cleanup_enroot.sh` through
`slurmScripts.builtIn.*.customContentFile`, pointing at local-owned
`local_slurm_scripts/cleanup_enroot.sh`. The Slurm job hooks remove only
containers for the current job. The match covers both older
`pyxis_<jobid>...` names and newer image-derived names that end with `_<jobid>`,
such as `pyxis_<image>.sqsh_<jobid>`.

The scheduled `enroot-cleanup` ActiveCheck points
`slurmJobSpec.sbatchScriptFile` at local-owned
`local_scripts/enroot-cleanup.sh` and removes only job-shaped Pyxis names. It is
not a broad `pyxis_*` deletion policy because persistent named containers are a
site-specific operational choice.

### Slurm Scripts Inventory

The files under `slurm_scripts/` are rendered into the Slurm scripts ConfigMap
and mounted at `/opt/slurm_scripts/` and `/mnt/jail.upper/opt/slurm_scripts/`.
They are not install-time one-shot jobs and they are not always-running
processes. Slurm starts them as short-lived hooks:

- `prolog.sh`: before each job on each allocated node.
- `epilog.sh`: after each job on each allocated node.
- `hc_program.sh`: periodically through Slurm `HealthCheckProgram`; the GPU
  default runs every 120 seconds.

Rows that list a `.json` sidecar include both the executable and the same-name
metadata file, such as `alloc_gpus_busy.drain.sh.json`. The sidecar is not
executed directly; Helm renders it into
`checks.json`, which `check_runner.py` reads to decide context, filtering, and
node drain or undrain behavior.

| File | Runtime | Used for |
| --- | --- | --- |
| `check_runner.py` | Short-lived runner launched by the prolog, epilog, or health-check wrapper. | Loads `checks.json`, filters checks by context, GPU platform, job allocation, and node state, runs matching commands, and applies configured drain, undrain, comment, or uncomment actions. |
| `prolog.sh` | Job-start hook from `slurmConfig.prolog`; runs before each job on each allocated node. | Sets `CHECKS_CONTEXT=prolog` and starts `check_runner.py` for pre-job checks and setup. |
| `epilog.sh` | Job-finish hook from `slurmConfig.epilog`; runs after each job on each allocated node. | Sets `CHECKS_CONTEXT=epilog` and starts `check_runner.py` for post-job cleanup and checks. |
| `hc_program.sh` | Slurm-scheduled health-check hook; GPU default is every 120 seconds. | Sets `CHECKS_CONTEXT=hc_program` and starts `check_runner.py` for periodic node recovery and health checks. |
| `alloc_gpus_busy.drain.sh` / `.json` | Job-start check for GPU jobs. | Drains and requeues when an allocated GPU already has unmanaged compute processes. |
| `alloc_gpus_busy.undrain.sh` / `.json` | Periodic health-check recovery for drained GPU nodes. | Undrains a node once no GPU compute processes remain. |
| `alloc_mem_used.drain.sh` / `.json` | Job-start check for all jobs. | Drains and requeues when the job's requested memory exceeds available node memory. |
| `alloc_mem_used.undrain.sh` / `.json` | Periodic health-check recovery for drained nodes. | Undrains a node when available memory is back above the node real-memory threshold. |
| `boot_disk_full.sh` / `.json` | Job-start and periodic health check. | Drains when root disk usage is above 80%; periodic checks can resume the node after cleanup. |
| `chmod_enroot_layers.sh` / `.json` | Job-start and job-finish maintenance. | Keeps cached Enroot image layers under `/mnt/jail/mnt/image-storage` readable and writable. |
| `cleanup_enroot.sh` / `.json` | Job-start and job-finish cleanup in the jail, but this chart overrides the executable by default. | The upstream file removes Pyxis/Enroot containers for the current job. The parent chart renders local-owned `local_slurm_scripts/cleanup_enroot.sh` instead. |
| `cleanup_scratch_data.sh` / `.json` | Disabled by metadata (`contexts: ["none"]`) unless the check config is changed. | Optional scratch cleanup helper for `/mnt/jail/scratch`. |
| `drop_page_cache.sh` / `.json` | Job-finish cleanup. | Runs `sync` and drops Linux page cache after a job. |
| `drop_posix_shmem.sh` / `.json` | Job-finish cleanup for full-GPU jobs. | Clears `/mnt/jail/dev/shm` after GPU jobs while skipping CPU-only and partial-GPU allocations. |
| `gpu_health_check.py` / `.json` | Job-start, job-finish, and periodic GPU health check for H100, H200, B200, and B300 nodes. | Runs the Nebius `health-checker`; failures drain the node with the first failed check in the reason. |
| `job_tmpfs_delete.sh` / `.json` | Job-finish cleanup. | Deletes `/mnt/jail/mnt/memory/job_$SLURM_JOB_ID`. |
| `job_tmpfs_delete_leftover.sh` / `.json` | Periodic health-check cleanup. | Deletes stale job tmpfs directories after confirming the job is no longer running. |
| `job_tmpfs_recreate.sh` / `.json` | Job-start setup. | Creates or clears `/mnt/jail/mnt/memory/job_$SLURM_JOB_ID` before the job starts. |
| `map_job_dcgm.sh` / `.json` | Job-start setup for GPU jobs. | Writes job IDs under `/var/run/nebius/slurm` so DCGM GPU metrics can be attributed to Slurm jobs. |
| `nvme_raid_health.sh` / `.json` | Disabled by default; if enabled, runs as a periodic health check. | Checks NVMe-backed RAID arrays, mount read/write behavior, and recent NVMe-related `dmesg` errors; failures drain the node. |
| `unmap_job_dcgm.sh` / `.json` | Job-finish cleanup for GPU jobs. | Removes the DCGM job mapping files written by `map_job_dcgm.sh`. |

### Memory Defaults

The Soperator 4.0.2 CRD defaults `slurmConfig.defMemPerNode` to `0`. Slurm
does not allow `DefMemPerCPU` and `DefMemPerNode` together, so the chart fails
rendering when `customSlurmConfig` contains `DefMemPerCPU`.

For GPU-only worker partitions, use a GPU-based memory default instead:

```yaml
slurmConfig:
  defCpuPerGPU: 8

customSlurmConfig: |
  DefMemPerGPU=131072
```

This keeps the memory policy compatible with the Soperator CRD default while
still aligning GPU jobs to a CPU and memory ratio.

### GPU Driver Capabilities

GPU worker NodeSets set container driver capabilities through
`nodesets[].slurmd.customEnv`:

```yaml
nodesets:
  - name: worker
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

## Helm Dependencies

`Chart.yaml` declares the always-available operator dependencies plus optional
Soperator-family child charts. The child chart source folders remain as
siblings under `helm-charts/`; this parent chart consumes them through
`file://../...` dependencies and exposes only curated integration overrides in
the parent `values.yaml`.

The sibling chart directories are the source of truth. Archives under
`helm-charts/soperator/charts/` are generated dependency artifacts from
`helm dependency build` / packaging. When validating a dirty checkout, prefer a
disposable copy for dependency rebuilds so local archive churn does not obscure
the actual chart, values, or documentation changes under review.

### OpenKruise

Values key:

```yaml
kruise:
  installOperator: true
```

OpenKruise provides controllers and CRDs used by Soperator workload and
lifecycle patterns. It is a runtime dependency of the operator path, not a
Slurm role. Keep it installed before the Soperator-managed workloads are
created, and keep `uninstallCleanup.enabled=true` unless an operator has a
separate cleanup process for Soperator-created Kruise resources.

### MariaDB Operator

Values key:

```yaml
mariadb-operator:
  installOperator: true
```

This installs the MariaDB Operator CRDs, controller, and webhook. The
Soperator `SlurmCluster` then uses
`slurmNodes.accounting.mariadbOperator.enabled=true` to ask Soperator to create
and wire the accounting database used by `slurmdbd`.

There are two separate toggles on purpose:

- `mariadb-operator.installOperator`: installs the Kubernetes operator.
- `slurmNodes.accounting.mariadbOperator.enabled`: enables the Soperator
  accounting database integration in the SlurmCluster spec.

### Optional Soperator-Family Child Charts

These dependencies are disabled by default and enabled only through their
dependency-name value keys:

- `soperator-checks.enabled`
- `soperator-activechecks.enabled`
- `soperator-notifier.enabled`
- `soperator-backup-config.enabled`
- `soperator-dcgm-exporter.enabled`

The child charts keep their full defaults in their own folders. The parent
values file carries only integration defaults that matter for the combined
install, such as stable `fullnameOverride` values, runtime Secret references,
and active-check safety toggles. For production training, the parent and cxcli
profiles keep `soperator-activechecks.enabled=false`,
`soperator-activechecks.waitForChecks.enabled=false`,
`soperator-checks.enabled=false`, and `soperator-dcgm-exporter.enabled=false`.
cxcli profiles keep an internal `hidden` partition for upstream ActiveChecks and can override
`soperator-activechecks.srunReadyPartition` to a visible partition such as
`cpu` when that is the better readiness target.

Responsibilities:

- `soperator-checks` runs the checks controller and optional node-health
  automation.
- `soperator-activechecks` defines the actual check workloads and dependencies
  between them.
- `soperator-notifier` owns Slack notification Alertmanager wiring and expects
  the webhook URL to come from an existing runtime Secret.
- `soperator-backup-config` owns K8up backup schedules for the jail and expects
  object-storage credentials to come from an existing runtime Secret.
- `k8up` is pulled in only when `soperator-backup-config.enabled=true`, because
  the K8up controller is needed only for jail backup.
- `soperator-dcgm-exporter` owns optional Slurm job-mapped GPU metrics and is
  separate from the NVIDIA GPU Operator's default DCGM telemetry path.

## cxcli Integration

The cxcli Soperator profile lives in:

- `services/nebius-cxcli/component_cli_settings.yaml`
- path: `components.apps.soperator.cli.soperator_nodesets_profile`

The default profile is `nebius-gpu-v1`.

It seeds MK8s `node_groups` as data:

- `system`
- `controller`
- `login`
- `accounting`
- `worker` for the default one-group production profile
- `worker-0`, `worker-1`, and so on only when the profile is explicitly sharded

The Terraform `mk8s` module does not hardcode those names. It accepts a generic
`node_groups` map and creates one MK8s node group per enabled map entry.

The same profile also seeds SFS filesystems:

- `jail`
- `controller-spool`
- `accounting`

The Terraform `sfs` module does not hardcode those names either. It accepts a
generic `filesystems` map.

## Install Flow

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

## Change Guide

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
- host preparation and optional rebooter behavior: `hostNetwork`,
  `customContainer`, `initContainers`, and `rebooter`. Rebooter ServiceAccount,
  Role, and binding resources render only when `rebooter.enabled=true` and
  `rebooter.generateRBAC=true`; no chart-owned schedule is rendered for
  reboots.

## Optional Child Charts

The core templates stay limited to Soperator and Slurm resources. Optional
in-cluster features are sibling chart sources packaged as child dependencies of
this parent chart:

- `soperator-notifier`: Slack job-state notifications through
  VictoriaMetrics Alertmanager. Slack delivery uses a Slack App incoming
  webhook as described in
  <https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/>.
  Webhook URLs stay in runtime Kubernetes Secrets. Direct Helm users precreate
  the referenced Secret; cxcli users can either provide the URL at deploy time
  or point the notifier at an existing Nebius MysteryBox Secret ID and let ESO
  sync the primary version into Kubernetes.
- `soperator-checks`: controller for Soperator `ActiveCheck` resources.
- `soperator-activechecks`: pinned upstream ActiveCheck definitions. The
  target SlurmCluster name is `slurmClusterRefName`; cxcli derives that value
  and the login-node SSH check count from the matching Soperator app row.
  CPU-only deployments should set `srunReadyPartition` to a rendered partition
  such as `cpu` when ActiveChecks are explicitly enabled for diagnostics.
- `soperator-backup-config`: K8up `Schedule` for jail backups to Object
  Storage. Credentials are referenced through an existing Secret. The parent
  chart installs K8up as an optional dependency when
  `soperator-backup-config.enabled=true`, in the same release namespace.
- `soperator-dcgm-exporter`: NVIDIA DCGM exporter configured with the
  Soperator Slurm job-mapping directory. This is optional; the default GPU
  telemetry path remains the NVIDIA GPU Operator DCGM exporter. For Nebius
  GPU-image hosts, cxcli sets `validateToolkit=false` because the host NVIDIA
  runtime stack is already present.

### Upstream Helm Chart Coverage

The upstream `nebius/soperator/helm` tree is handled by this product through
four decisions:

- Main chart: `soperator`, `soperator-crds`, `slurm-cluster`,
  `slurm-cluster-storage`, `nodesets`, and `nodeconfigurator`.
- Optional child dependencies: `soperator-checks`, `soperator-activechecks`,
  `soperator-backup-config`, `k8up`, `soperator-dcgm-exporter`, and
  `soperator-notifier`.
- cxcli-owned orchestration: `soperator-fluxcd` and
  `soperator-fluxcd-bootstrap` are intentionally replaced by cxcli render and
  deploy ordering.
- Excluded upstream chart: `nfs-server`. Production shared storage should use
  Nebius SFS. The Terraform-owned VM NFS module remains available only as a
  non-HA compatibility bridge when explicit NFS semantics are required.

Three upstream charts are tracked as review-only imports instead of installed:

- `soperator-monitoring-dashboards`: cxcli owns Grafana dashboard import
  through generated Grafana values and dashboard ConfigMaps. Installing the
  upstream chart directly would require a Grafana sidecar contract that cxcli
  does not currently use.
- `soperator-custom-configmaps`: these ConfigMaps are only useful when a
  profile wires the matching Slurm/NodeSet references. Rendering them by
  default would create unused node config ConfigMaps.
- `storageclasses`: production storage is Terraform-owned SFS or VM NFS, and
  the local learning path uses chart-local storage. Creating generic cluster
  StorageClasses from this chart would cross the infra boundary.

The verifier hashes those review-only upstream paths so a future upstream
change is visible during a release bump without silently changing the cxcli
contract.

## Explicit Non-Goals

- It does not create MK8s clusters or node groups.
- It does not create SFS filesystems.
- It does not create the NFS VM.
- It does not install upstream `soperator-fluxcd`.
- It does not enable Slack notifications, active checks, backup schedules,
  or Soperator DCGM job-mapping telemetry by default; opt into the matching
  child chart value only when that feature is required. Keep
  `soperator-activechecks.enabled=false`,
  `soperator-activechecks.waitForChecks.enabled=false`,
  `soperator-checks.enabled=false`, `soperator-notifier.enabled=false`,
  `soperator-backup-config.enabled=false`, and
  `soperator-dcgm-exporter.enabled=false` for production training defaults.
- It does not enable SSSD identity integration or the NodeConfigurator rebooter
  by default; keep `slurmNodes.sssd.enabled=false`,
  `nodesets[].sssd.enabled=false`, and `rebooter.enabled=false` unless those
  service paths are intentionally configured. cxcli does not prompt the raw
  rebooter gate in its normal wizard.
- It does not block production Helm installs on ActiveChecks by default:
  `soperator-activechecks.waitForChecks.enabled` stays `false` unless a
  benchmark, diagnostic, or maintenance workflow intentionally enables it.
- It does not own the full observability stack.

Default GPU telemetry stays on the cxcli-managed NVIDIA GPU Operator DCGM
Exporter path.

## Chart Release And OCI Publish

The chart package version follows the upstream Soperator release with a Nebius
package suffix. `Chart.yaml.version` uses `<upstream>-ps.N`, while
`Chart.yaml.appVersion` is the upstream Soperator release and must match
`upstream-soperator.lock.yaml`.

```yaml
version: X.Y.Z-ps.N
appVersion: "X.Y.Z"
```

The suffix lets this repository publish package respins without implying that
Nebius Soperator itself released a different upstream version.

The parent chart package version is independent from the Soperator-family child
chart package versions. A parent-only chart respin can use a later `-ps.N`
suffix than unchanged child dependencies. The required invariant is that each
parent dependency pin matches the referenced child chart's own
`Chart.yaml.version`, and `Chart.lock` matches the parent dependency repository
and version exactly.

Release prep and publish are intentionally local and explicit:

1. Add release notes under `CHANGELOG.md` `## [Unreleased]`.
2. Run `./publish-helm.sh --prep X.Y.Z-ps.N` to move notes into a dated release
   section, update `Chart.yaml.version`, validate the chart, commit, and push
   the branch.
3. Merge to `main`.
4. Run `./publish-helm.sh --publish X.Y.Z-ps.N` from `main` to create and push
   the `soperator-chart-vX.Y.Z-ps.N` tag.
5. The tag starts `.github/workflows/helm-chart-publish.yml`, which reads
   `.github/helm-chart-publish.json`, packages the chart, pushes it to Nebius
   OCI, verifies anonymous pull, and writes a publish manifest artifact.

If `[Unreleased]` is empty, the scheduled upstream sync workflow and
`publish-helm.sh --prep` seed a fallback chart-bump note before release prep
moves the section into the dated release entry.

Only the push path uses Nebius authentication. The post-publish pull check uses
a fresh unauthenticated Helm registry config because published chart pulls are
intended to be public.

The workflow pushes to the OCI repository root, for example
`oci://cr.<region>.nebius.cloud/<registry-short-id>/charts`. Helm derives the
final `soperator` repository and `X.Y.Z-ps.N` tag from the packaged chart,
matching the [Helm OCI registry contract](https://helm.sh/docs/topics/registries/#the-push-subcommand).

Soperator-family child charts remain sibling `file://../...` dependencies until
they have their own chart-publish catalog entries and the exact child versions
are published and anonymously pullable from OCI. Only after that should the
parent dependency repositories move to the OCI repository root, with dependency
`name` and `version` selecting the exact child artifact.

## Upstream Release Contract

This chart is anchored to one public Nebius Soperator release at a time. The
authority is `upstream-soperator.lock.yaml`.

The lock records:

- upstream repository.
- upstream release and tag.
- resolved upstream tag commit.
- upstream-owned script imports copied into this repository.
- upstream-owned CRD imports copied into this repository.
- chart `appVersion` tracking for the parent and Soperator-family child charts.
- image value imports checked against upstream chart values.
- review-only upstream logic hashes for templates, dashboards, custom
  ConfigMaps, and storage classes.
- the local-owned paths that script sync must not overwrite and image sync must
  explicitly target.
- a daily CI sync path that opens a feature-branch PR when the public upstream
  release advances.

Versioning uses two fields on purpose:

```yaml
version: X.Y.Z-ps.N
appVersion: "X.Y.Z"
```

`appVersion` is the upstream Soperator release and is derived from the lock by
the sync script. `version` is this repository's Helm chart package version; a
new upstream release sync sets it to `<upstream>-ps.1`, while same-release sync
preserves an explicit parent-chart package respin such as `<upstream>-ps.2`.

The upstream-owned exact imports are:

- `helm/slurm-cluster/slurm_scripts` to
  `helm-charts/soperator/slurm_scripts`.
- `helm/soperator-activechecks/scripts` to
  `helm-charts/soperator-activechecks/scripts`.
- `helm/soperator/crds` to `helm-charts/soperator/crds`.

ActiveChecks keeps the upstream script files byte-for-byte. The chart applies
the local SlurmCluster name and namespace at render time in the helper that
embeds those scripts, so script sync does not carry local patches.
Configured ActiveCheck script file references fail Helm rendering when the
referenced file is not packaged with the chart, which keeps parent chart and
cxcli renders from silently producing empty check payloads.

Full upstream sync owns only derived upstream-tracking surfaces:

- `Chart.yaml.appVersion` and upstream annotations.
- parent `Chart.yaml.version` as `<upstream>-ps.1`.
- upstream parent chart dependency versions and repositories that also exist in
  the local parent chart.
- Soperator-family child chart `appVersion` and `<upstream>-ps.1` package
  versions.
- parent dependency versions for those child charts.
- `Chart.lock` when dependency metadata changes.
- approved script imports.
- approved CRD imports.
- explicit image value paths listed under `imports.images`.
- review-only upstream hashes in the lock.

It does not copy or overwrite local product-layer sources:

- `README.md`
- `CHANGELOG.md`
- `values.schema.json`
- `scripts/`
- `templates/`
- `examples/`
- `docs/`

Those files are the local product layer. They contain the simplified values
interface, MK8s/SFS/NFS ownership boundary, cxcli wiring, local learning
profile, production guardrails, and the worker/partition design.

The Helm dependency archive cache at `helm-charts/soperator/charts/` is
generated from `Chart.lock` by `helm dependency build`. It is ignored by Git,
but it is intentionally not excluded from chart packages because packaged
releases need the resolved dependency archives.

Image value sync is narrower: it may update only the value paths listed under
`imports.images`, and the target file must be declared in
`local_owned_paths`. If a maintainer needs to carry a patched image that differs
from upstream, update the lock in the same PR so the verifier failure becomes
an explicit review decision rather than hidden drift.

Review-only hashes are updated by `--sync` so the PR records the upstream
template, dashboard, custom ConfigMap, or storage class movement without
copying those upstream files into this chart.

Validate the lock and upstream tracking with:

```bash
scripts/verify-upstream-soperator-sync.sh --scope all --report
```

The report labels this lock group as `script`, matching `imports.scripts`.

To intentionally move to a newer Soperator release:

1. From any clean working tree, run
   `scripts/verify-upstream-soperator-sync.sh --latest --sync --report`.
2. Review and test the unstaged diff locally.
3. Stage and commit the reviewed sync changes.
4. Open the PR. The PR is the human approval gate for copied scripts, CRD
   schemas, image values, dependency versions, and review-only hash changes.

The script refuses local `--sync` from a dirty working tree. When run from
`main`, `master`, or the repository default branch, it creates a clean
`sync-soperator-<release>` feature branch before mutating files. It refreshes
the lock `tag` and resolved `commit`, updates derived chart metadata, copies
approved scripts and CRDs, updates tracked image values, updates review-only
hashes, regenerates dependency metadata when chart versions change, runs Helm
dependency, lint, and template validation under a temporary Helm repository
config/cache, and leaves the resulting diff unstaged for local review and
testing. The temporary Helm config is populated from the parent chart's remote
dependency repositories, so local and CI sync runs do not require ambient
`helm repo add` state. Write mode requires the full `all` scope plus `yq` v4
and Helm; scoped `scripts`, `crds`, or `images` runs are read-only verification
only. With `--report`, it also prints the changed-file list with readable
status labels after sync validation.
Missing-tool errors include macOS and Linux install hints, but the script does
not install packages automatically. Local runs do not stage, commit, push, or
create the PR.

The scheduled GitHub workflow runs the same sync with `--latest`, stages and
commits the validated result, pushes it to `automation/soperator-upstream-sync`,
and creates or updates a PR for human approval.
`scripts/verify-upstream-soperator-sync.sh --check-latest`
remains a read-only local or CI check and is not required before sync.
Before a scheduled `--latest` sync writes files, the script compares the lock
release with the highest non-draft, non-prerelease SemVer release published in
GitHub releases. If the lock is newer than every such release, the workflow
fails with a clear typo/stale-metadata message instead of mutating chart files.

## Reference Sources

This design follows the public Slurm, Kubernetes, and Nebius Soperator
projects:

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
