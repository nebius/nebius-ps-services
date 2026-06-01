# soperator Helm chart

Umbrella chart for self-managed Nebius Soperator on MK8s.

This chart vendors the upstream Soperator 3.0.4 operator, CRDs, OpenKruise
dependency, MariaDB Operator dependency, SlurmCluster, NodeConfigurator,
NodeSet, and SFS storage templates into one installable chart. It intentionally
does not include upstream `soperator-fluxcd`; `nebius-cxcli` renders Flux.
Optional upstream Soperator-family features such as Slack notifications, active
checks, K8up backup schedules, and the Soperator DCGM job-mapping exporter are
packaged as disabled-by-default child chart dependencies.
Their source folders stay as sibling charts, while this parent chart exposes
only the integration values that matter for the combined Soperator install.

## Table Of Contents

- [Architecture And Ownership](#architecture-and-ownership)
- [Feature Summary](#feature-summary)
- [Bundled Child Charts](#bundled-child-charts)
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
- One-replica OpenKruise manager default for small-cluster portability; set
  `kruise.manager.replicas` higher when the target has enough system capacity.
- Slurm topology is disabled by default for generic-cluster portability; enable
  `slurmConfig.topologyPlugin` only when worker nodes expose matching
  `<controllerManager.manager.env.topologyLabelPrefix>/tier-*` labels.
- cxcli exposes the same topology decision as `values.topologyProfile`; the
  Nebius production opt-in is `nebius-tiered-tree-v1`.
- The five-role Nebius production shape (`system`, `controller`, `login`,
  `accounting`, `worker`) is role separation. Slurm topology is separate: it is
  a worker-placement optimization for distributed jobs that benefit from
  physical or InfiniBand fabric locality.
- For fresh Nebius production MK8s deployments, use the production topology
  profile only when worker nodes are prepared with accurate
  `topology.nebius.com/tier-*` labels. Generic clusters and already-installed
  MK8s clusters should keep topology disabled unless the operator has prepared
  and verified equivalent labels.
- Nebius MK8s production path with SFS-backed jail and controller spool
  storage.
- Optional external NFS `/home` integration through chart values.
- NodeConfigurator rebooter RBAC for cluster-wide pod watches and taint-based
  worker-node drain/reboot checks.
- cxcli-compatible values for MK8s target-scoped installs.
- Disabled-by-default child chart dependencies for Slack notifications, active
  checks, K8up jail backup schedules, and Soperator DCGM job-mapping telemetry.
- The default GPU telemetry path remains the cxcli-managed NVIDIA GPU Operator
  DCGM Exporter. Enable the Soperator DCGM child chart only when per-job Slurm
  labels are required.

## Bundled Child Charts

The parent chart keeps the core Soperator install in one release while exposing
optional integrations through dependency gates:

| Dependency | Gate | What It Does | Use It For |
| --- | --- | --- | --- |
| `kruise` | `kruise.installOperator` | Installs OpenKruise for Soperator-managed StatefulSets. | Required unless OpenKruise is already installed and managed outside this release. |
| `mariadb-operator` | `mariadb-operator.installOperator` | Installs the MariaDB Operator used by chart-managed Slurm accounting. The parent chart uses cert-manager for its webhook certificate and disables the dependency chart's alternate cert-controller by default. | Accounting-enabled clusters that do not already provide the operator. |
| `soperator-checks` | `soperator-checks.enabled` | Deploys the Soperator checks controller. | Required before `ActiveCheck` resources can run. cxcli production profiles keep it disabled unless ActiveChecks are explicitly enabled. |
| `soperator-activechecks` | `soperator-activechecks.enabled` | Deploys Soperator `ActiveCheck` custom resources. | Runtime benchmark/diagnostic checks, including Slurm and NCCL-oriented checks. Keep disabled on production training clusters unless running a maintenance window. |
| `soperator-notifier` | `soperator-notifier.enabled` | Renders VictoriaMetrics alerting resources for Slack job notifications. | Optional Slurm job notifications to Slack through a precreated or cxcli-managed webhook Secret. |
| `soperator-backup-config` | `soperator-backup-config.enabled` | Renders a K8up `Schedule` for jail backups. | Optional Soperator jail backups to Object Storage. |
| `k8up` | `soperator-backup-config.enabled` | Installs the K8up controller dependency for backup schedules. | Pulled in only when jail backups are enabled. |
| `soperator-dcgm-exporter` | `soperator-dcgm-exporter.enabled` | Deploys DCGM Exporter with the Soperator job-mapping directory. | Optional per-job Slurm/DCGM labels. cxcli production profiles keep it disabled by default; avoid duplicate scraping with the GPU Operator exporter. |

Each sibling chart keeps its own README for standalone prerequisites, values,
and validation commands. The parent values file carries only integration
overrides needed for the combined Soperator install.

For production training clusters, keep the performance-impacting child gates
off by default:

- `soperator-activechecks.enabled=false`
- `soperator-activechecks.waitForChecks.enabled=false`
- `soperator-checks.enabled=false`
- `soperator-notifier.enabled=false`
- `soperator-backup-config.enabled=false`
- `soperator-dcgm-exporter.enabled=false`

Enable ActiveChecks only for benchmark/diagnostic clusters or maintenance
windows, because they can schedule Slurm CUDA, NCCL, GPU stress, RDMA, and
maintenance jobs. Enable the Soperator DCGM exporter only when Slurm per-job
DCGM labels are required; the normal cxcli telemetry path uses the NVIDIA GPU
Operator DCGM exporter plus the Nebius Observability Agent.

SSSD and rebooter are not dependency charts. They are in-chart service gates
and also default off: `slurmNodes.sssd.enabled=false`,
`nodesets[].sssd.enabled=false`, and `rebooter.enabled=false`. The
rebooter gate enables the NodeConfigurator reboot helper and RBAC for
operator-triggered worker-node drain/handoff or reboot maintenance. cxcli's
normal wizard does not prompt this raw host-maintenance gate; set it
deliberately in Helm values or `config.yaml` only when Soperator-managed node
maintenance is wanted. It is not
a per-NodeSet switch, does not reboot nodes at install time, and does not create
a reboot schedule by itself. The upstream 3.0.4 helper acts after `SlurmNodeDrain` or
`SlurmNodeReboot` is set on the Kubernetes Node and drains by cordoning the node
plus adding a `NoExecute` taint. NodeConfigurator still renders a no-op
`customContainer` by default so its host setup initContainers have a valid
DaemonSet container when the rebooter is off. Example condition flow:
`NebiusMaintenanceScheduled=True` becomes
`SoperatorChecksNodeMaintenance=True`, then `SlurmNodeDrain=True`; a degraded
Slurm reason such as `Kill task failed` or
`[compute_maintenance] node reboot process` becomes
`SoperatorChecksNodeDegraded=True`, then `SlurmNodeReboot=True`.
Advanced production-maintenance mode is for operators who deliberately enable
both `soperator-checks.enabled=true` and `rebooter.enabled=true`. It has two
intents. `NebiusMaintenanceScheduled=True` means graceful maintenance drain and
node handoff; it drains Slurm workers and lets the rebooter cordon and
`NoExecute`-drain Kubernetes pods, but it does not call host `reboot now` by
itself. `SlurmNodeReboot=True` is the actual Soperator host reboot path after
drain. Prefer the Soperator degraded-node flow that creates this condition from
a Slurm reboot/degraded reason; direct external writes to `SlurmNodeReboot=True`
must happen only after Slurm workloads are already drained.

## Production Install

For production, prefer the target-specific values rendered by `nebius-cxcli`.
Direct Helm installs are still supported when you manage the target cluster
labels, storage, and values yourself.

Before installing with direct Helm, users must have:

- chart dependencies resolved from `Chart.lock`:
  `helm dependency build helm-charts/soperator`.
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
- The default controller and accounting filters also tolerate matching
  `slurm.nebius.ai/nodeset-name` `NoSchedule` taints for dedicated service
  nodes.
- A CPU/service node that matches the default `no-gpu` filter:
  `nebius.com/gpu NotIn ["true"]`.
- GPU worker nodes labeled `slurm.nebius.ai/nodeset-name=worker`.
- Nodes that mount the shared jail labeled `slurm.nebius.ai/jail=true`.
- SFS/Filestore device `jail` mounted through the chart at `/mnt/jail`.
- SFS/Filestore device `controller-spool` mounted through the chart at
  `/mnt/controller-spool`.
- Worker capacity for the default `worker` values: one worker pod that
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
helm dependency build helm-charts/soperator
helm install soperator helm-charts/soperator \
  --namespace soperator \
  --create-namespace \
  --set-file "slurmNodes.login.sshRootPublicKeys[0]=$HOME/.ssh/id_ed25519.pub"
```

For direct Helm installs that need explicit production settings, start with
`examples/production-core-values.yaml` or one of the scenario examples, then
edit it to match your cluster:

```bash
helm dependency build helm-charts/soperator
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
  login, accounting, exporter, REST, and populateJail. The default controller,
  login, and accounting filters tolerate the matching dedicated role taints.
- `sConfigController.runAsUid` / `runAsGid`: defaults to `0` so the controller
  can write Slurm config files into the root-owned populated jail.
- `slurmNodes.rest.enabled`: renders `slurmrestd`, the Slurm REST API service.
  In this chart it is primarily a control-plane API for Soperator /
  SConfigController reconciliation and possible secured API integrations. It is
  not the path used by normal Slurm CLI commands such as `srun`, `sbatch`,
  `squeue`, or `scontrol`, and it is not the path used by the ActiveChecks
  `srun` / `sbatch` scripts; those use the native Slurm client path from the
  login or check-job environment.
- `nodesets[]`: Slurm worker NodeSets such as `worker`, `worker-cpu`,
  `worker-gpu`, or hardware-specific NodeSets.
- `partitionConfiguration`: Slurm partitions and their NodeSet mappings.
  Each partition supports a typed `policy` block (`priorityTier`,
  `preemptMode`, `default`, `hidden`, `state`, `maxTime`, `defaultTime`,
  `defMemPerNode` / `defMemPerCPU` / `defMemPerGPU` / `defCpuPerGPU`,
  `overSubscribe`, `allowAccounts`, `allowQos`, `denyAccounts`, `denyQos`)
  and a free-form `config` escape hatch. Typed tokens render first, then
  `config` is appended. `partitionConfiguration.includeFile` adds a single
  `Include=<path>` line for partition files mounted into the controller
  outside the chart-managed list.
- `schedulingConfig`: typed Slurm scheduling and preemption surface
  (`preemptType`, `accountingStorageEnforce`, `enforcePartLimits`,
  `preemptMode`, `preemptParameters`, `jobRequeue`, `schedulerType`,
  `schedulerParameters`, `priorityType`, and
  `priorityWeights.age` / `assoc` / `fairshare` / `partition` / `jobSize` /
  `qos` / `tres`). Each non-null entry renders as a Slurm.conf line appended
  to `customSlurmConfig`.
- `qosConfiguration`: optional declarative QOS lifecycle. When
  `enabled: true`, a post-install / post-upgrade Helm hook Job reconciles
  accounts, QOS objects, and associations through `sacctmgr` against the
  accounting pod (idempotent, ttl-cleaned). The default hook image is
  `alpine/k8s:1.33.5` because the driver needs Bash plus kubectl. Disabled by
  default and not supported on Managed Soperator.
- `customSlurmConfig`: free-form escape hatch for Slurm.conf tokens not
  modeled in `schedulingConfig`. Setting the same key in both `schedulingConfig`
  and `customSlurmConfig` fails the template render to prevent silent
  overrides. Configure matching SlurmDBD accounts, associations, and QoS
  objects (typically through `qosConfiguration`) before enabling enforcement.
  Keep `PluginDir` unset unless the image-specific plugin directory is known:
  Slurm fails startup when any path in `PluginDir` is absent.
- `controllerManager.manager.kubeClient.{qps,burst}`: optional Kubernetes
  client tuning for the Soperator manager on large clusters (2k-5k nodes).
  Empty by default; non-empty values are emitted as `KUBE_API_QPS` /
  `KUBE_API_BURST` env vars on the manager container.
- `nodesets[].topologyLabels`: optional list of additional Slurm features
  per NodeSet derived from topology (rack, SU, fabric). Appended to
  `nodeConfig.features` so jobs can target them with `--constraint=`. Used
  by cxcli's `nebius-nvl-rack-v1` topology profile for GB300/NVL clusters.
- `nodeGroupMapping` is a cxcli-authored convenience value for mapping
  Soperator roles to existing MK8s node groups. Helm itself schedules from
  `k8sNodeFilters[]`, `nodesets[]`, `storage.*`, `rebooter.tolerations`, and
  `partitionConfiguration`. cxcli also applies the mapped `system` filter to
  chart-owned helper deployments such as the Soperator manager, checks
  controller, and MariaDB operator.
- `volume.*` and `storage.*`: jail, controller spool, optional accounting
  storage, and mount placement.
- `slurmNodes.login.sshRootPublicKeys`: public keys for login SSH access.
  Prefer Helm `--set-file`.
- `certManager.enabled`: Soperator admission webhook certificates. Requires
  cert-manager CRDs when enabled. The chart defaults
  `certManager.privateKey.rotationPolicy` to `Always`, matching cert-manager
  1.18+ private-key rotation behavior explicitly.
- `mariadb-operator.webhook.cert.certManager.enabled`: MariaDB Operator webhook
  certificates. This is enabled by default, so
  `mariadb-operator.certController.enabled` stays `false` unless you
  deliberately run the dependency chart without cert-manager.
- `slurmConfig.topologyPlugin` / `slurmConfig.topologyParam` plus
  `controllerManager.manager.env.topologyLabelPrefix`: enable only after
  worker nodes have accurate tier labels. Manual pre-labeling can work, but
  incorrect or stale labels can produce poor placement, worker initialization
  waits, or multi-node jobs placed across incompatible domains.
- `soperator-checks.enabled`, `soperator-activechecks.enabled`,
  `soperator-notifier.enabled`, `soperator-backup-config.enabled`,
  and `soperator-dcgm-exporter.enabled`: optional child chart gates. Keep these
  off unless the deployment needs that feature; this parent values file
  intentionally carries only integration overrides, not full copies of each
  child chart's defaults.
- `soperator-activechecks.waitForChecks.enabled`: keep disabled for production
  training so the Helm release is not blocked on benchmark or diagnostic
  checks. Enable it only when an ActiveChecks run is intentionally part of the
  install/maintenance workflow.
- `soperator-dcgm-exporter.validateToolkit`: keep enabled for GPU Operator
  toolkit-managed hosts; set to `false` for Nebius GPU-image hosts where the
  host NVIDIA runtime stack is already present.
- `soperator-notifier.enabled`: renders Slack job notifications through a
  Slack App incoming webhook as documented by Slack's
  [incoming webhook guide](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/).
  Never put the webhook URL in values or Git. Direct Helm users create the
  Secret referenced by `soperator-notifier.slack.existingSecret` /
  `existingSecretKey` before install. cxcli users either provide the URL at
  deploy time or set `soperator-notifier.slack.webhookSource: mysterybox` with
  an existing Nebius MysteryBox Secret ID (`mbsec-...`); cxcli then renders an
  ExternalSecret and uses the MysteryBox primary version.
- `soperator-backup-config.enabled`: installs the optional K8up dependency in
  the Soperator release namespace and renders the jail backup `Schedule`.
- `soperator-activechecks.srunReadyPartition`: Slurm partition used by the
  ActiveChecks `srun` readiness probe. cxcli profiles keep an internal
  `hidden` partition for upstream ActiveChecks; CPU-only examples and profiles
  can still set this to `cpu` when they want the readiness probe to target the
  visible CPU partition.
- `soperator-activechecks.checks.enroot-cleanup.slurmJobSpec.sbatchScriptFile`:
  scheduled cleanup script for job-scoped Pyxis/Enroot containers. The default
  points to local-owned `local_scripts/enroot-cleanup.sh`, recognizes both
  `pyxis_<jobid>...` and image-derived `pyxis_<image>.sqsh_<jobid>` names, and
  avoids deleting arbitrary `pyxis_*` containers. Use `sbatchScript` only for
  one-off inline overrides.
- `slurmScripts.builtIn.cleanup_enroot.sh.customContentFile`: job prolog/epilog
  cleanup override rendered into the Soperator scripts ConfigMap. The default
  points to local-owned `local_slurm_scripts/cleanup_enroot.sh` and removes
  only Enroot containers matching the current `SLURM_JOB_ID`. Use
  `customContent` only for one-off inline overrides.
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
helm dependency build helm-charts/soperator
helm lint --strict --with-subcharts helm-charts/soperator
helm template soperator helm-charts/soperator --namespace soperator >/tmp/soperator.yaml
for file in helm-charts/soperator/examples/*-values.yaml; do
  helm template soperator helm-charts/soperator \
    --namespace soperator -f "$file" >/dev/null
done
```

`helm dependency build` reconstructs `helm-charts/soperator/charts/` from
`Chart.lock`. The dependency archive cache is intentionally ignored by Git, but
it is still included in packaged chart releases.

## Chart Release And OCI Publish

The chart package version follows the upstream Soperator release with a Nebius
package suffix. `Chart.yaml.appVersion` stays pinned to the upstream Soperator
release recorded in `upstream-soperator.lock.yaml`, while
`Chart.yaml.version` uses `<upstream>-ps.N`.

```yaml
version: 3.0.4-ps.1
appVersion: "3.0.4"
```

Release flow:

1. Add the user-facing release notes under `CHANGELOG.md` `## [Unreleased]`.
2. Run `./publish-helm.sh --prep X.Y.Z-ps.N` from this chart directory.
3. Merge the generated release-prep commit to `main`.
4. Run `./publish-helm.sh --publish X.Y.Z-ps.N` from `main`.
5. The `soperator-chart-vX.Y.Z-ps.N` tag triggers
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
  --version X.Y.Z-ps.N
```

## Upstream Release Contract

This chart is anchored to one Soperator release at a time. The pinned upstream
release is recorded in `upstream-soperator.lock.yaml`; the sync script derives
`Chart.yaml.version`, `Chart.yaml.appVersion`, upstream annotations, child-chart
versions, dependency pins, script imports, image values, and review-only hashes
from that lock. Upstream sync sets the parent and Soperator-family child chart
package versions to `<upstream>-ps.1`; follow-up parent-chart package respins
may use `<upstream>-ps.N`.

The lock describes upstream tracking in four groups:

- script imports, such as Slurm scripts and ActiveChecks scripts.
- chart `appVersion` tracking for the parent and Soperator-family child charts.
- image values tracked against upstream chart values.
- review-only upstream logic hashes for templates, CRDs, dashboards,
  custom ConfigMaps, and storage classes.

The verifier enforces that contract without writing files:

```bash
helm-charts/soperator/scripts/verify-upstream-soperator-sync.sh --scope all --report
```

### Syncing To A New Upstream Release

Use this flow when upstream Soperator publishes a newer Helm chart release:

1. From any clean working tree, run the latest-release sync:

   ```bash
   helm-charts/soperator/scripts/verify-upstream-soperator-sync.sh --latest --sync --report
   ```

   `--latest --sync` updates the lock pin to GitHub latest, derives all
   upstream-owned version, dependency, script, image, and review-hash changes,
   and commits the resulting sync as one local commit. Use plain `--sync` only
   when you want to refresh the release already pinned in the lock; it does not
   query GitHub latest.
   `--report` prints detailed per-import status and the sync commit file list
   before committing.

2. Open the PR. The PR is the human approval gate for script diffs, image
   changes, dependency movement, and review-only hash changes.

`scripts/verify-upstream-soperator-sync.sh --check-latest` remains a read-only
status check. It is not required before sync.

Expected sync-owned changes can include:

- `upstream-soperator.lock.yaml` `tag`, `commit`, image values, and review-only
  hashes.
- parent `Chart.yaml` package version, upstream annotations, upstream dependency
  versions, and child-chart dependency versions.
- Soperator-family child chart `version` and `appVersion` fields.
- `Chart.lock` when dependency metadata changed.
- approved upstream script imports under `slurm_scripts/` and
  `soperator-activechecks/scripts/`.
- tracked image values in the values files listed in the lock.

`--sync` refuses to write from a dirty working tree. When run from `main`,
`master`, or the repository default branch, it creates a clean feature branch
named `sync-soperator-<release>` before mutating files. It refreshes the lock
`tag` and resolved `commit`, copies approved script imports, updates tracked
image values, updates parent and child chart `appVersion` and `<upstream>-ps.1`
package versions, refreshes upstream parent dependency versions, regenerates
dependency metadata when needed, runs Helm dependency, lint, and template
validation, and commits the resulting diff as one local commit. Local runs do
not push or create the PR.
Scoped `--scope scripts` and `--scope images` runs are read-only checks only;
write-mode sync always uses the full upstream release contract.

Write mode requires `yq` v4 and Helm; read-only CI verification does not.
Missing-tool errors include macOS and Linux install hints, but the sync script
does not install packages automatically.

The local chart templates, examples, docs, release tooling, and cxcli
wiring are not overwritten by upstream sync. Those files are this chart's
product layer, where we keep cxcli integration, Nebius infrastructure
boundaries, production profiles, and chart-specific examples. Image sync is
limited to explicit value paths listed in the lock, and the verifier refuses
to write image values into files that are not declared in `local_owned_paths`.
If this fork needs a temporary hotfix image, update the lock in the same PR so
the intentional divergence is visible and reviewed.

The repository CI runs the same verifier on chart changes. A daily scheduled
workflow creates or updates an upstream-sync feature branch and PR when GitHub
has a newer public Soperator release; local runs update and commit the current
feature branch and leave PR creation to the user.
The scheduled sync compares release versions before writing files and fails
early when the lock release is newer than GitHub's latest release, which helps
catch lock typos without mutating chart files.

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
