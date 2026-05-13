# soperator Helm chart

Umbrella chart for self-managed Nebius Soperator on MK8s.

This chart vendors the upstream Soperator 3.0.3 operator, CRDs, OpenKruise
dependency, MariaDB Operator dependency, SlurmCluster, NodeConfigurator,
NodeSet, and SFS storage templates into one installable chart. It intentionally
does not include upstream `soperator-fluxcd`; `nebius-cxcli` renders Flux.
Optional companion features such as Slack notifications, active checks, K8up
backup schedules, the Soperator DCGM job-mapping exporter, and in-cluster NFS
live in separate charts so the core Slurm install stays focused.

## Table Of Contents

- [Architecture And Ownership](#architecture-and-ownership)
- [Feature Summary](#feature-summary)
- [Production Install](#production-install)
- [Configuration](#configuration)
- [Examples](#examples)
- [Validation](#validation)
- [Chart Release And OCI Publish](#chart-release-and-oci-publish)
- [Upstream Release Contract](#upstream-release-contract)
- [Local Kubernetes Learning Profile](#local-kubernetes-learning-profile)

## Architecture And Ownership

- Terraform owns Nebius infrastructure: MK8s node groups, SFS filesystems, and
  optional VM-based NFS.
- Helm owns in-cluster resources: CRDs, RBAC, webhooks, storage PV/PVC/mount
  DaemonSets, `SlurmCluster`, `NodeConfigurator`, and `NodeSet`.
- `nebius-cxcli` binds the chart to an MK8s target. The chart app `instance_id`
  should match the MK8s target name, for example `soperator@cluster1`.

`clusterName` becomes the `SlurmCluster` and `NodeConfigurator` name and is
also used as the prefix for generated RBAC, ConfigMap, and PriorityClass names.
Use a lowercase DNS-label value up to 38 characters, starting with a letter, so
the generated suffixed Kubernetes names remain valid.

See [docs/design.md](docs/design.md) for the full architecture, node-role,
storage, Helm dependency, and cxcli wiring design.

## Feature Summary

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
- Separate optional companion charts for Slack notifications, active checks,
  K8up jail backup schedules, Soperator DCGM job-mapping telemetry, and
  in-cluster NFS.
- The default GPU telemetry path remains the cxcli-managed NVIDIA GPU Operator
  DCGM Exporter. Enable the Soperator DCGM companion only when per-job Slurm
  labels are required.

## Production Install

For production, prefer the target-specific values rendered by `nebius-cxcli`.
Direct Helm installs are still supported when you manage the target cluster
labels, storage, and values yourself.

Before installing with direct Helm, users must have:

- cert-manager installed when `certManager.enabled=true`.
- access to the pinned container images from the target cluster.
- at least one SSH public key passed to
  `slurmNodes.login.sshRootPublicKeys`.
- Kubernetes node labels, storage names, and worker resources that match the
  values being installed.

If you install with no values overlay, the default values expect:

- Service node labels: nodes exist for
  `slurm.nebius.ai/nodeset-name=system`, `controller`, `login`, and
  `accounting`.
- A CPU/service node that matches the default `no-gpu` filter:
  `nebius.com/gpu NotIn ["true"]`.
- GPU worker nodes labeled `slurm.nebius.ai/nodeset-name=worker-gpu`.
- Nodes that mount the shared jail labeled `slurm.nebius.ai/jail=true`.
- SFS/Filestore device `jail` mounted through the chart at `/mnt/jail`.
- SFS/Filestore device `controller-spool` mounted through the chart at
  `/mnt/controller-spool`.
- Worker capacity for the default `worker-gpu` values: one worker pod that
  requests `8` GPUs, `64` CPU, `512Gi` memory, and `50Gi` ephemeral storage.

Check the labels before using the defaults directly:

```bash
kubectl get nodes \
  -L slurm.nebius.ai/nodeset-name,slurm.nebius.ai/jail,nebius.com/gpu
```

If your cluster uses different labels, storage device names, storage paths, or
worker shapes, change the matching values. Helm does not ignore labels; it
renders selectors from values such as `k8sNodeFilters[]` and
`nodesets[].nodeSelector`.

```bash
helm install soperator helm-charts/soperator \
  --namespace soperator \
  --create-namespace \
  --set-file "slurmNodes.login.sshRootPublicKeys[0]=$HOME/.ssh/id_ed25519.pub"
```

For direct Helm installs that need explicit production settings, start with
`examples/production-core-values.yaml` or one of the scenario examples, then
edit it to match your cluster:

```bash
helm install soperator helm-charts/soperator \
  --namespace soperator \
  --create-namespace \
  -f helm-charts/soperator/examples/production-core-values.yaml \
  --set-file "slurmNodes.login.sshRootPublicKeys[0]=$HOME/.ssh/id_ed25519.pub"
```

For concepts behind these settings, see
[docs/design.md](docs/design.md).

## Configuration

Common direct Helm settings:

- `clusterName`: rendered `SlurmCluster` name and generated resource prefix.
  Keep the Helm release name cluster-unique too, because operator RBAC and
  webhook objects are cluster-scoped and include the release name.
- `clusterType`: `gpu` when any worker NodeSet uses GPUs; otherwise `cpu`.
- `k8sNodeFilters[]`: node selection for non-worker roles such as controller,
  login, accounting, exporter, REST, and populateJail.
- `nodesets[]`: Slurm worker NodeSets such as `worker-cpu`, `worker-gpu`, or
  hardware-specific NodeSets.
- `partitionConfiguration`: Slurm partitions and their NodeSet mappings.
- `volume.*` and `storage.*`: jail, controller spool, optional accounting
  storage, and mount placement.
- `slurmNodes.login.sshRootPublicKeys`: public keys for login SSH access.
  Prefer Helm `--set-file`.
- `certManager.enabled`: Soperator admission webhook certificates. Requires
  cert-manager CRDs when enabled.
- `uninstallCleanup.image`: `kubectl` image used by the pre-delete cleanup
  hook. Override in restricted-egress clusters.

## Examples

Values examples live under `examples/`:

- `minimal-gpu-values.yaml`
- `cpu-only-values.yaml`
- `mixed-cpu-gpu-values.yaml`
- `production-core-values.yaml`
- `accounting-enabled-values.yaml`
- `external-nfs-enabled-values.yaml`
- `multi-worker-nodeset-values.yaml`
- `partition-features-values.yaml`
- `local-k8s-one-node-cluster-values.yaml` for local learning only

## Validation

```bash
helm lint --strict helm-charts/soperator
helm template soperator helm-charts/soperator --namespace soperator >/tmp/soperator.yaml
for file in helm-charts/soperator/examples/*-values.yaml; do
  helm template soperator helm-charts/soperator \
    --namespace soperator -f "$file" >/dev/null
done
```

## Chart Release And OCI Publish

The chart has its own SemVer package version in `Chart.yaml.version`.
`Chart.yaml.appVersion` stays pinned to the upstream Soperator release recorded
in `upstream-soperator.lock.yaml`.

```yaml
version: 0.1.0
appVersion: "3.0.3"
```

Release flow:

1. Add the user-facing release notes under `CHANGELOG.md` `## [Unreleased]`.
2. Run `./publish-helm.sh --prep X.Y.Z` from this chart directory.
3. Merge the generated release-prep commit to `main`.
4. Run `./publish-helm.sh --publish X.Y.Z` from `main`.
5. The `soperator-chart-vX.Y.Z` tag triggers
   `.github/workflows/helm-chart-publish.yml`.

The shared publish workflow reads `.github/helm-chart-publish.json` to map the
`soperator-chart` tag prefix to this chart.

The workflow publishes the package to a Nebius OCI registry path shaped like:

```text
oci://cr.<region>.nebius.cloud/<registry-short-id>/charts/soperator
```

Configure the shared GitHub environment `nb-image-chart-publish` with the Nebius
registry and service-account variable names used by image and chart publish
workflows:

- Variables:
  `NB_TENANT_ID`, `NB_PROJECT_ID`, `NB_REGION_ID`, `NB_REGISTRY_ID`,
  `NB_SERVICE_ACCOUNT_ID`,
  `NB_SERVICE_ACCOUNT_PUBLIC_KEY_ID`
- Optional variables:
  `NB_REGISTRY_NAME`
- Secret:
  `NB_SERVICE_ACCOUNT_PRIVATE_KEY`

`NB_REGISTRY_ID` must be the full registry id, for example
`registry-<registry-short-id>`.

Only the push path uses Nebius authentication. The workflow logs in with the
service-account secret before `helm push`, then verifies public pull with a
fresh unauthenticated Helm registry config. Helm pushes to the repository root
`.../charts`; Helm derives the final chart repository name and tag from the
packaged chart, matching the [Helm OCI registry contract](https://helm.sh/docs/topics/registries/#the-push-subcommand).
Public pull example:

```bash
helm pull oci://cr.<region>.nebius.cloud/<registry-short-id>/charts/soperator \
  --version X.Y.Z
```

## Upstream Release Contract

This chart is anchored to one Soperator release at a time. The pinned upstream
release is recorded in `upstream-soperator.lock.yaml`; `Chart.yaml.appVersion`
must match that release. `Chart.yaml.version` is this chart package's own
SemVer and is bumped only by the chart release flow.

The lock describes upstream imports in three groups:

- exact file imports, such as Slurm scripts and ActiveChecks scripts.
- image values tracked against upstream chart values.
- review-only upstream logic hashes for templates, CRDs, dashboards,
  custom ConfigMaps, and storage classes.

The verifier enforces that contract without writing files:

```bash
helm-charts/soperator/scripts/verify-upstream-soperator-sync.sh --scope all --report
```

To intentionally resync approved exact imports after changing the lock to a new
upstream release:

```bash
helm-charts/soperator/scripts/verify-upstream-soperator-sync.sh \
  --scope scripts --sync --report
```

To check only tracked image values:

```bash
helm-charts/soperator/scripts/verify-upstream-soperator-sync.sh --scope images --report
```

Image value sync uses `yq` only when it needs to write changed values; read-only
CI verification does not require `yq`.

The local chart templates, values, examples, docs, release tooling, and cxcli
wiring are not overwritten by exact file sync. Those files are this chart's
product layer, where we keep cxcli integration, Nebius infrastructure
boundaries, production profiles, and chart-specific examples. Image sync is
limited to explicit value paths listed in the lock, and the verifier refuses
to write image values into files that are not declared in `local_owned_paths`.
If this fork needs a temporary hotfix image, update the lock in the same PR so
the intentional divergence is visible and reviewed.

The repository CI runs the same verifier on chart changes. A daily scheduled
check also compares the lock with the latest public Soperator release so
release drift is visible without fetching anything during Helm install.

## Local Kubernetes Learning Profile

This section is intentionally last because the chart is primarily used for
production-grade Nebius MK8s deployments. The local profile is for learning
Soperator and basic Slurm behavior on a local vanilla Kubernetes cluster. Kind,
Minikube, and Docker Desktop Kubernetes are common examples.

Use the opt-in one-node values profile at
`examples/local-k8s-one-node-cluster-values.yaml`. This is a values file, not a
separate chart, and it keeps production defaults out of the local learning path.

The local values profile sets:

- Kubernetes `local` PVs backed by node-local paths instead of Nebius SFS.
- a required `slurm.nebius.ai/local-storage=true` node label for the local
  storage node and all Slurm pods that mount those PVs.
- one CPU-only worker NodeSet.
- accounting and Slurm REST disabled.
- SConfigController scaled to `0`.
- Soperator admission webhooks disabled.
- `PlugStackConfig=/dev/null` so local `srun` works without the production
  chroot/pyxis SPANK plugin path.
- `enableHostUserNamespace: true` on the worker NodeSet for Docker
  Desktop/Kind compatibility.

Before installing the local profile, choose the node that owns the local jail
and controller-spool paths:

```bash
kubectl label node <local-storage-node> \
  slurm.nebius.ai/local-storage=true \
  --overwrite
```

```bash
helm install soperator-local . \
  -f examples/local-k8s-one-node-cluster-values.yaml \
  --namespace soperator-local \
  --create-namespace \
  --wait \
  --timeout 15m

kubectl exec -n soperator-local login-0 -c sshd -- sinfo
kubectl exec -n soperator-local login-0 -c sshd -- srun -p cpu hostname
```

This profile is named one-node because its default storage is node-local. It can
be installed into a multi-node local Kubernetes cluster, but the Slurm pods that
mount the local jail and controller spool must stay on the single labeled
storage node. Use a real shared RWX backend such as NFS for multi-node local
worker testing.

This is not the production path and it is not enabled by default. A local
cluster still has to meet the Soperator runtime requirements and must be able
to pull the pinned Soperator images. Keep Nebius and cxcli deployments on the
default SFS-backed values.

Docker Desktop on macOS can run Kubernetes through a Kind node with a registry
mirror. If pod events show `short read` or `unexpected EOF` while pulling
Nebius images, bypass the mirror for the Nebius registry inside the local node:

```bash
NODE_CONTAINER="${NODE_CONTAINER:-desktop-control-plane}"
tmpfile="$(mktemp)"
trap 'rm -f "$tmpfile"' EXIT

printf '%s\n' \
  'server = "https://cr.eu-north1.nebius.cloud"' \
  '' \
  '[host."https://cr.eu-north1.nebius.cloud"]' \
  '  capabilities = ["pull", "resolve"]' \
  > "$tmpfile"

docker exec -i "$NODE_CONTAINER" sh -lc '
  install -d -m 755 /etc/containerd/certs.d/cr.eu-north1.nebius.cloud
  cat > /etc/containerd/certs.d/cr.eu-north1.nebius.cloud/hosts.toml
' < "$tmpfile"
```

Repeat the command for each local node container that may pull Soperator images.
This is a local-cluster runtime tweak; it is lost when the local cluster is
recreated and it does not replace image pull credentials for private images.
