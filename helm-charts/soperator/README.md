# soperator Helm chart

Umbrella chart for self-managed Nebius Soperator on MK8s.

This chart vendors the upstream Soperator 3.0.3 operator, CRDs, OpenKruise
dependency, MariaDB Operator dependency, SlurmCluster, NodeConfigurator,
NodeSet, and SFS storage templates into one installable chart. It intentionally
does not include upstream `soperator-fluxcd`; `nebius-cxcli` renders Flux. It
also does not install upstream `soperator-dcgm-exporter`; GPU telemetry stays
on the cxcli-managed NVIDIA GPU Operator DCGM Exporter path.

## Ownership boundary

- Terraform owns Nebius infrastructure: MK8s node groups, SFS filesystems, and
  optional VM-based NFS.
- Helm owns in-cluster resources: CRDs, RBAC, webhooks, storage PV/PVC/mount
  DaemonSets, `SlurmCluster`, `NodeConfigurator`, and `NodeSet`.
- `nebius-cxcli` binds the chart to an MK8s target. The chart app `instance_id`
  should match the MK8s target name, for example `soperator@cluster1`.

See [docs/design.md](docs/design.md) for the full architecture, node-role,
storage, Helm dependency, and cxcli wiring design.

## Upstream Release Contract

This chart is anchored to one Soperator release at a time. The pinned upstream
release is recorded in `upstream-soperator.lock.yaml`; `Chart.yaml.appVersion`
must match that release, and `Chart.yaml.version` uses the same release as its
base with a chart packaging suffix such as `3.0.3-ps.1`.

The `slurm_scripts/` directory is upstream-owned and must remain an exact copy
of `helm/slurm-cluster/slurm_scripts` from the locked Soperator release. The
chart verifier enforces that contract:

```bash
helm-charts/soperator/scripts/verify-upstream-soperator-sync.sh
```

To intentionally resync scripts after changing the lock to a new upstream
release:

```bash
helm-charts/soperator/scripts/verify-upstream-soperator-sync.sh --sync
```

The local chart templates, values, examples, docs, release tooling, and cxcli
wiring are not overwritten by upstream sync. Those files are this chart's
product layer, where we keep cxcli integration, Nebius infrastructure
boundaries, production profiles, and the local Kind/Minikube learning path.

The repository CI runs the same verifier on chart changes. A scheduled check
also compares the lock with the latest public Soperator release so release
drift is visible without fetching anything during Helm install.

## Features

- One installable umbrella chart for Soperator self-deployment.
- Pinned upstream Soperator operator and CRDs.
- Pinned OpenKruise dependency for Soperator-managed StatefulSets.
- Optional MariaDB Operator dependency for Slurm accounting.
- CPU-only, GPU-only, and mixed CPU+GPU Slurm worker layouts.
- Slurm partitions and node features through chart values.
- Nebius MK8s production path with SFS-backed jail and controller spool
  storage.
- Optional external NFS `/home` integration through chart values.
- cxcli-compatible values for MK8s target-scoped installs.
- Local one-node vanilla Kubernetes learning profile for Kind, Minikube, or
  Docker Desktop Kubernetes.

## Install

```bash
helm lint --strict helm-charts/soperator
helm template soperator helm-charts/soperator --namespace soperator
```

For a real deployment, set `slurmNodes.login.sshRootPublicKeys` to one or more
operator-approved public keys. The default and examples do not ship placeholder
SSH keys.

### Local Kind And Minikube

The chart can be installed on a local one-node vanilla Kubernetes cluster for
learning Soperator and basic Slurm behavior. Use the opt-in Helm values profile
at `examples/local-kind-values.yaml`.

This profile is a values file, not a separate chart. It is selected with
`-f examples/local-kind-values.yaml` and keeps all production defaults out of
the local learning path.

The local values profile sets:

- local host-path PVs instead of Nebius SFS.
- one CPU-only worker NodeSet.
- accounting and Slurm REST disabled.
- SConfigController scaled to `0`.
- Soperator admission webhooks disabled.
- `PlugStackConfig=/dev/null` so local `srun` works without the production
  chroot/pyxis SPANK plugin path.
- `enableHostUserNamespace: true` on the worker NodeSet for Docker
  Desktop/Kind compatibility.

```bash
helm install soperator-local . \
  -f examples/local-kind-values.yaml \
  --namespace soperator-local \
  --create-namespace \
  --wait \
  --timeout 15m

kubectl exec -n soperator-local login-0 -c sshd -- sinfo
kubectl exec -n soperator-local login-0 -c sshd -- srun -p cpu hostname
```

This is not the production path and it is not enabled by default. A local
cluster still has to meet the Soperator runtime requirements and must be able
to pull the pinned Soperator images. Keep Nebius and cxcli deployments on the
default SFS-backed values.

Docker Desktop on macOS can run Kubernetes through a Kind node with a registry
mirror. If pod events show `short read` or `unexpected EOF` while pulling
Nebius images, bypass the mirror for the Nebius registry inside the local node:

```bash
cat >/tmp/nebius-registry-hosts.toml <<'EOF'
server = "https://cr.eu-north1.nebius.cloud"

[host."https://cr.eu-north1.nebius.cloud"]
  capabilities = ["pull", "resolve"]
EOF

docker cp /tmp/nebius-registry-hosts.toml \
  desktop-control-plane:/tmp/nebius-registry-hosts.toml
docker exec desktop-control-plane sh -lc \
  'mkdir -p /etc/containerd/certs.d/cr.eu-north1.nebius.cloud &&
   cp /tmp/nebius-registry-hosts.toml \
      /etc/containerd/certs.d/cr.eu-north1.nebius.cloud/hosts.toml'
```

## Slurm defaults

The default chart values use structured partitions so Soperator writes explicit
`NodeName` and `NodeSet` entries into `slurm.conf` before worker pods register
with `slurmctld`. Accounting, MariaDB Operator, and Slurm REST are enabled by
default because SConfig reconciliation talks to the Slurm REST API. The chart
also appends the Slurm plugin directory used by the pinned images and mounts
the generated `slurm-scripts` ConfigMap into worker NodeSets so the default
health check, prolog, and epilog paths resolve at runtime.

For GPU NodeSets, the chart derives the Slurm `Gres=gpu:<count>` node setting
from `slurmd.resources.gpu` when GPU is enabled and `nodeConfig.static` does not
already include an explicit `Gres=` value. This keeps Kubernetes GPU requests
and Slurm GPU scheduling aligned without duplicating the GPU count in values.

The MariaDB Operator dependency is controlled and configured under the
`mariadb-operator` values key, matching the dependency name in `Chart.yaml`.
The separate `slurmNodes.accounting.mariadbOperator` key is the Soperator CRD
field for the MariaDB custom resource used by Slurm accounting.

## Supported Worker Scenarios

This chart supports three Slurm worker layouts. In all cases, each Soperator
`NodeSet` should describe one homogeneous worker shape, and Slurm partitions
should point at the matching `NodeSet` names.

| Scenario | Worker NodeSets | Partitions |
| --- | --- | --- |
| CPU-only workers | `worker-cpu` | `cpu` |
| GPU-only workers | `worker-gpu` | `gpu` |
| Mixed CPU+GPU workers | `worker-cpu`, `worker-gpu` | `cpu`, `gpu` |

Typical use:

- CPU-only workers: general batch, preprocessing, CPU services, and development
  queues.
- GPU-only workers: dedicated accelerator clusters for training or GPU batch
  work.
- Mixed CPU+GPU workers: one Slurm cluster with separate CPU and GPU queues.

### CPU-Only Workers

Use a CPU-only worker cluster when no Slurm worker pod needs GPU resources.
Set `clusterType: cpu`, define CPU worker `nodesets[]` with `gpu.enabled:
false`, and map a `cpu` partition to those NodeSets.

CPU-only workers do not need the NVIDIA GPU Operator, DCGM Exporter, or Network
Operator. The core Slurm services still use CPU MK8s node groups for
controller, login, accounting, and system workloads.

Example values: `examples/cpu-only-values.yaml`.

### GPU-Only Workers

Use a GPU-only worker cluster when all Slurm worker jobs should land on GPU
nodes. Set `clusterType: gpu`, define GPU worker `nodesets[]` with
`gpu.enabled: true`, request GPUs under `slurmd.resources.gpu`, and map the
default partition to the GPU NodeSet.

The chart derives `Gres=gpu:<count>` from `slurmd.resources.gpu`, so Slurm jobs
can request GPUs with commands such as:

```bash
srun -p gpu --gres=gpu:1 nvidia-smi -L
```

For GPU MK8s targets, `nebius-cxcli` owns NVIDIA GPU Operator enablement and
keeps DCGM Exporter on that path. When the target uses GPU fabric or
InfiniBand, `nebius-cxcli` also enables Network Operator.

Example values: `examples/minimal-gpu-values.yaml` and
`examples/production-core-values.yaml`.

### Mixed CPU+GPU Workers

Use a mixed worker cluster when one Slurm cluster needs both CPU queues and GPU
queues. Do not create one mixed-hardware NodeSet. Define separate homogeneous
worker NodeSets such as:

```yaml
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
```

Then map partitions explicitly:

```yaml
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
```

CPU jobs can use `srun -p cpu ...`; GPU jobs can use
`srun -p gpu --gres=gpu:1 ...`. The live validation for this chart confirmed a
mixed MK8s deployment with two CPU workers and two H100 GPU workers, with both
Slurm partitions available and `--gres=gpu:*` jobs working from the login pod.

Example values: `examples/mixed-cpu-gpu-values.yaml`.

## Partitions And Features

Slurm partitions are queues over one or more NodeSets. Slurm features are labels
on nodes. In this chart, that maps directly to the Soperator CRDs:

- partitions: `partitionConfiguration.partitions[].nodeSetRefs`
- features: `nodesets[].nodeConfig.features`

Do not model `features` as a partition field. If a GPU partition can run on H100
and A100 nodes, create separate homogeneous NodeSets and attach the hardware
feature to each NodeSet:

```yaml
partitionConfiguration:
  configType: structured
  partitions:
    - name: gpu
      nodeSetRefs:
        - worker-h100
        - worker-a100
    - name: h100
      nodeSetRefs:
        - worker-h100

nodesets:
  - name: worker-h100
    nodeConfig:
      features:
        - gpu
        - cuda
        - h100
  - name: worker-a100
    nodeConfig:
      features:
        - gpu
        - cuda
        - a100
```

Users can then choose either a partition (`-p h100`) or a generic partition plus
a feature constraint (`-p gpu --constraint=h100`). Common partition names are
`cpu`, `gpu`, `h100`, `highmem`, `infiniband`, `debug`, and `long`; the exact
set should match the NodeSets and policies in the cluster.

`nebius-cxcli` exposes this through Soperator profiles. The nodesets profile
chooses the worker shape (`nebius-cpu-v1`, `nebius-gpu-v1`, or
`nebius-mixed-v1`). The optional partition profile `with-debug-long` adds
policy partitions for short debug jobs and long-running jobs on top of the
shape partitions.

Example values: `examples/partition-features-values.yaml`.

## MK8s Node Mapping

Soperator `NodeSet` objects are in-cluster Slurm worker definitions. Nebius
MK8s node groups are Kubernetes host pools. Keep those layers separate:

- CPU-only MK8s node groups: `system`, `controller`, `login`, and
  `accounting`.
- GPU MK8s worker node groups: shard one logical Slurm worker NodeSet, for
  example `worker-gpu-0`, `worker-gpu-1`, all labeled
  `slurm.nebius.ai/nodeset-name=worker-gpu`.
- Chart worker NodeSets: use the logical NodeSet name such as `worker-gpu`, not
  the shard names.
- The jail label `slurm.nebius.ai/jail=true` is separate from the worker
  NodeSet label. It drives jail PV and mount DaemonSet placement for all Slurm
  roles that need the shared root filesystem.
- Worker NodeSets should be homogeneous. The design supports `worker-cpu` and
  `worker-gpu` as separate NodeSets for mixed CPU/GPU clusters; see
  [docs/design.md](docs/design.md#cpu-gpu-and-mixed-worker-design).
- NFS is not an MK8s node group in this chart. Use the VM-based Terraform `nfs`
  component when a separate `/home` export is required.

## Storage

The chart expects Nebius SFS filesystems to exist and be attached to the
appropriate MK8s node groups by Terraform:

- `jail`
- `controller-spool`
- optional `accounting`
- optional jail submount filesystems

Kubernetes storage glue for those filesystems is chart-owned. When `nebius-cxcli`
renders this chart and finds an enabled sibling `nfs` infra component for the
same target, it fills `externalNfs.server` and `externalNfs.path` from Terraform
outputs. For direct Helm installs, set:

```yaml
externalNfs:
  enabled: true
  server: 10.0.0.10
  path: /srv/nfs/home
  mountPath: /home
```

For local learning clusters only, set `volume.jail.type: local` and
`volume.controllerSpool.type: local` so the chart renders local host-path PVs
instead of SFS/Filestore mount DaemonSets. This keeps local installs away from
Nebius SFS while leaving the default Nebius storage path unchanged.

## Production Operations Guardrails

Use chart values and cxcli config as the source of truth. Manual edits inside
pods, generated ConfigMaps, or OpenKruise StatefulSets are temporary and can be
overwritten by Soperator, Helm, Flux, or the next cxcli render.

### Persistent Slurm Configuration

Put global `slurm.conf` additions under `customSlurmConfig` and partition-line
changes under `partitionConfiguration`. Use `configType: custom` when a
partition needs raw Slurm fields such as `AllowQos`:

```yaml
customSlurmConfig: |
  PluginDir=/usr/lib/x86_64-linux-gnu/slurm
  AccountingStorageEnforce=associations,limits,qos
  EnforcePartLimits=Any

partitionConfiguration:
  configType: custom
  rawConfig:
    - >-
      PartitionName=high Nodes=ALL Default=NO MaxTime=INFINITE State=UP
      PriorityTier=20 AllowQos=high
```

Verify the persisted path, not only the live file:

```bash
kubectl -n soperator get slurmcluster soperator -o yaml
kubectl -n soperator exec login-0 -c sshd -- scontrol show config
kubectl -n soperator exec login-0 -c sshd -- scontrol show partition high
```

### Scaling Workers

Scale both layers together:

- Terraform/cxcli MK8s node group size for Kubernetes capacity.
- Helm/cxcli `nodesets[].replicas` for the Soperator `NodeSet` desired size.

Do not use `kubectl scale statefulsets.apps.kruise.io ...` as a permanent
change. It can be useful only as a short-lived recovery action because the
Soperator `NodeSet` and `SlurmCluster` specs remain the durable desired state.

### NodeSet Storage Changes

Treat NodeSet volume identity as create-time configuration. After a NodeSet is
created, do not change storage class, disk type, claim-template-like fields, or
the worker image storage layout in place. OpenKruise rejects some
volume-claim-template mutations, leaving Slurm worker pods and desired counts
out of sync. For production changes, create a new homogeneous NodeSet, map the
right partition to it, drain workloads, and then remove the old NodeSet.

### Accounting, QoS, And Partitions

`AccountingStorageEnforce=associations,limits,qos` is production-safe only when
all expected users, service users, accounts, associations, and QoS entries
already exist in SlurmDBD. This includes service users used by active checks or
automation. Apply `AllowQos` on the relevant partition through
`partitionConfiguration`, then verify with `sacctmgr`, `scontrol show config`,
and `scontrol show partition`.

### Memory Defaults

This chart fails rendering if `customSlurmConfig` contains `DefMemPerCPU`.
Soperator 3.0.3 defaults `slurmConfig.defMemPerNode=0`, and Slurm treats
`DefMemPerCPU` and `DefMemPerNode` as mutually exclusive. For GPU-only
partitions, use `DefMemPerGPU` with `DefCpuPerGPU` instead:

```yaml
slurmConfig:
  defCpuPerGPU: 8

customSlurmConfig: |
  PluginDir=/usr/lib/x86_64-linux-gnu/slurm
  DefMemPerGPU=131072
```

### GPU Driver Capabilities

GPU NodeSets can set `NVIDIA_DRIVER_CAPABILITIES` under
`nodesets[].slurmd.customEnv`. The default GPU values and cxcli profile expose
`compute,graphics,utility,video` so CUDA, graphics, utility, and video driver
libraries are available to GPU workloads. Override that environment variable
per NodeSet when a cluster needs a narrower or broader capability set.

### AppArmor, User Namespaces, And Worker SSH

The chart exposes AppArmor profile fields for login, controller, accounting,
and worker containers. Changing those profiles is a rolling operation for the
affected pods.

Do not use direct SSH into worker/login jail sessions as the correctness test
for rootless container behavior. User namespace creation can fail from the SSH
jail path even when it works from a Slurm job path. Test rootless/container
workloads through Slurm:

```bash
srun -p gpu --gres=gpu:1 --pty bash
unshare --user echo test
```

Direct worker SSH sessions can also differ from `srun` and `sbatch` sessions in
environment setup such as `LD_LIBRARY_PATH`. Production CUDA and container
workloads should be validated through Slurm jobs.

### Storage And Observability

Keep jail, `/home`, and jail submounts explicit. Monitor shared filesystem
capacity and latency before treating slow submit phases as Slurm scheduler
issues. For GPU clusters, DCGM Exporter remains owned by the cxcli-managed
NVIDIA GPU Operator path, and remote-write or observability changes should live
in the owning observability values rather than ad-hoc `kubectl patch` commands.

## Examples

Values examples live under `examples/`:

- `minimal-gpu-values.yaml`
- `cpu-only-values.yaml`
- `mixed-cpu-gpu-values.yaml`
- `production-core-values.yaml`
- `accounting-enabled-values.yaml`
- `external-nfs-enabled-values.yaml`
- `multi-worker-nodeset-values.yaml`
- `local-kind-values.yaml`

## Validation

```bash
helm lint --strict helm-charts/soperator
helm template soperator helm-charts/soperator --namespace soperator >/tmp/soperator.yaml
for file in helm-charts/soperator/examples/*-values.yaml; do
  helm template soperator helm-charts/soperator \
    --namespace soperator -f "$file" >/dev/null
done
```
