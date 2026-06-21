# Changelog

All notable changes to this chart are tracked here.

## [Unreleased]

- Aligned the mixed CPU+GPU worker examples and design guide with cxcli's
  shape-specific generated NodeSet names. The chart now has a mixed worker
  test fixture covering separate CPU/GPU NodeSets and generated shard
  `nodeSetRefs`, and it fails fast if stale cxcli worker helper inputs or the
  old `nodeGroupMapping` value are passed directly to Helm.
- Added explicit chart schema, validation, and tests for upstream Soperator
  ephemeral worker NodeSets. Ephemeral NodeSets now require non-negative integer
  `slurmConfig.suspendTime`, explicit non-negative `replicas`, and
  `initialNumberEphemeralNodes <= replicas`; explicit `replicas: 0` now renders
  as zero instead of falling back to one. The docs describe cxcli per-shard
  `worker_node_groups.<worker>.ephemeral_nodes.enabled=true` controls, global
  `worker_ephemeral_nodes.suspend_time_seconds`, and preserve the one Slurm
  worker pod to one Kubernetes worker VM scaling contract.
- Expanded the README and design guide with a dedicated Soperator autoscaling
  section covering fixed workers, infrastructure autoscaling, ephemeral
  NodeSets, `NodeSetPowerState`, `power-manager`, `suspendTime`, and the cxcli
  materialization boundary.
- Clarified the design guide's Soperator resource model: `slurmNodes.*`
  renders Slurm service roles inside `SlurmCluster`, `nodesets[]` renders
  worker `NodeSet` resources, structured partitions reference only NodeSet
  names, and `NodeConfigurator` remains the host-preparation and optional
  rebooter layer. The direct Helm placement example now uses chart-native
  `nodesets[].nodeSelector` and partition `nodeSetRefs` instead of implying
  worker NodeSets consume `k8sNodeFilters`.
- Made upstream `--latest` sync select the highest non-draft, non-prerelease
  SemVer release from GitHub releases instead of GitHub's mutable `Latest`
  marker, so maintenance-branch latest labels do not make automation reject an
  already-pinned newer release.

## [soperator-chart-v4.0.2-ps.1] - 2026-06-12

- Bumped Soperator upstream release to 4.0.2 and Helm chart package to
  4.0.2-ps.1.
- Made the upstream sync workflow and `publish-helm.sh --prep` seed a fallback
  changelog note when `[Unreleased]` is empty, so automated upstream bumps do
  not produce an empty release section.
- Clarified that parent chart package respins may keep unchanged
  Soperator-family child dependencies on their own package versions, and that
  child dependency repositories should move to OCI only after the child chart
  artifacts are registered, published, and pull-verified.
- Preserved same-release parent chart package respins during upstream sync and
  refreshed chart-facing version references for the current Soperator 4.0.2
  baseline.

## [soperator-chart-v4.0.1-ps.2] - 2026-06-12

- soperator helm chart release 4.0.1-ps.2

## [soperator-chart-v4.0.1-ps.1] - 2026-06-11

- Made the upstream sync `--report` changed-file summary use readable status
  labels such as `modified` and `new` instead of raw Git short-status codes.
- Fixed upstream sync Helm validation on clean GitHub runners by having
  `verify-upstream-soperator-sync.sh --sync` seed a temporary Helm repository
  config/cache from the parent chart's remote dependencies before running
  `helm dependency update` or `helm dependency build`, removing the need for
  ambient `helm repo add` state.
- Aligned Soperator example values and the Slurm memory validation message with
  the chart's upstream `3.0.5` image/defaults baseline.
- Hardened `verify-upstream-soperator-sync.sh --sync` so write mode requires a
  clean working tree, automatically creates a `sync-soperator-<release>` branch
  when run from the default branch, validates `yq` v4, provides macOS/Linux
  install hints for missing tools without installing packages automatically, and
  leaves the validated sync diff unstaged for local review while printing a
  changed-file summary when `--report` is used. The scheduled workflow still
  stages and commits automation PR changes after validation.
- Promoted Soperator CRDs from review-only hash tracking to exact upstream sync
  imports, so `--sync` refreshes `crds/` alongside the pinned operator image and
  catches upstream API schema additions such as `slurmConfig.suspendTime`.
- Aligned the parent `soperator` chart package version with the
  Soperator-family `<upstream>-ps.N` format. Upstream sync now sets the parent
  and child chart package versions to `<upstream>-ps.1`, and chart publish
  tooling accepts prerelease-suffixed SemVer tags such as
  `soperator-chart-v3.0.4-ps.1`.
- Added a design-doc inventory for the imported `slurm_scripts/` files,
  including their trigger contexts, scheduling model, and runtime purpose.
- Renamed the upstream Soperator sync lock group to `imports.scripts`, so the
  lock schema, report output, and `--scope scripts` terminology match.
- Made `upstream-soperator.lock.yaml` the single upstream-release source of
  truth: `verify-upstream-soperator-sync.sh --latest --sync` now updates the
  lock pin and derives chart appVersions, upstream parent dependency versions,
  child dependency versions, script imports, CRD imports, image values,
  review-only hashes, and Helm validation, while the scheduled workflow commits
  and opens an upstream-sync PR from an automation branch.
- Added a pre-mutation release-order guard so scheduled `--latest` sync fails
  clearly when the lock release is newer than GitHub's latest release.
- Hardened upstream sync verification so read-only checks validate child chart
  package versions, parent dependency pins, and `Chart.lock`, while scoped
  script/image runs stay read-only.
- Aligned the upstream sync script `--help` examples with the single-release
  sync flow, read-only scoped checks, and scheduled `--latest` PR workflow.
- Made the chart-owned Soperator webhook Certificate render
  `privateKey.rotationPolicy: Always` by default, matching cert-manager 1.18+
  behavior explicitly and avoiding default-change warnings.
- Clarified that `rebooter.enabled=true` enables the cluster-level
  NodeConfigurator reboot helper and RBAC for operator-triggered worker-node
  maintenance, but is not a per-NodeSet switch, install-time reboot, or
  chart-owned reboot schedule. The docs now also state that cxcli's normal
  wizard does not prompt this raw host-maintenance gate. They describe the
  upstream condition-driven, `NoExecute` taint-based drain path instead of
  implying a standalone reboot workflow, with examples of the maintenance and
  degraded-node condition chains that set `SlurmNodeDrain` and
  `SlurmNodeReboot`. They also clarify advanced production-maintenance mode:
  `NebiusMaintenanceScheduled=True` is graceful drain/node handoff while
  `SlurmNodeReboot=True` is the actual host reboot path after drain.
- Clarified that direct Helm installs from a source checkout must run
  `helm dependency build helm-charts/soperator` first, because the dependency
  archive cache under `charts/` is generated from `Chart.lock` and ignored by
  Git.
- Clarified that `slurmrestd` is primarily the Soperator / SConfigController
  control-plane REST API and optional secured integration surface, not the
  request path for normal Slurm CLI commands or ActiveChecks `srun` / `sbatch`
  scripts.
- Added typed `schedulingConfig.accountingStorageEnforce` and
  `schedulingConfig.enforcePartLimits` values. The chart now renders
  `AccountingStorageEnforce` and `EnforcePartLimits` through the same typed
  scheduling surface as preemption and priority weights, and hard-fails when
  those keys are duplicated in raw `customSlurmConfig`.
- Defaulted `rebooter.enabled=false` so direct Helm installs match the cxcli
  production contract; enabling the NodeConfigurator reboot helper remains an
  explicit operator decision. NodeConfigurator still renders a no-op
  `customContainer` by default so host-setup initContainers produce a valid
  DaemonSet when the rebooter is off.
- Added chart schema coverage for the NodeConfigurator `customContainer` and
  `rebooter` values so unsupported keys fail fast instead of being silently
  ignored by the template.
- Fixed the QOS reconcile hook Job placement rendering so referenced
  `k8sNodeFilterName` node selectors, affinity, and tolerations are inherited
  when the job does not override them, while explicit
  `qosConfiguration.job.tolerations` still flow through one path instead of
  rendering duplicate `tolerations` keys.
- Fixed the QOS reconcile script to pass `sacctmgr` update fields as Bash array
  arguments so values with spaces, such as account descriptions, are preserved.
- Raised the default QOS reconcile Job active deadline to cover the chart's
  accounting-pod readiness wait plus the in-pod SlurmDBD readiness wait on slow
  startup paths.
- Removed the default Slurm `PluginDir` override because Slurm 25.11 fails
  startup when any configured plugin directory is absent. Image-specific plugin
  paths now stay image-owned unless an operator explicitly sets
  `customSlurmConfig`.
- Clarified Soperator-family README usage guidance with an umbrella dependency
  table and explicit child-chart purpose, enablement, and standalone-use notes.
- Kept the parent ActiveChecks integration safe for production training by
  defaulting `soperator-activechecks.waitForChecks.enabled=false` alongside the
  disabled ActiveChecks, checks-controller, and Soperator DCGM child chart gates.
- Disabled the MariaDB dependency chart's alternate cert-controller by default
  because the parent chart already uses cert-manager for the MariaDB Operator
  webhook certificate in the combined Soperator release.
- Added an optional declarative `qosConfiguration` block. When
  `qosConfiguration.enabled: true`, a Helm post-install / post-upgrade
  hook Job reconciles accounts, QOS objects, and user/account
  associations through `sacctmgr` against the running accounting pod
  (idempotent, ttl-cleaned). The Job uses `alpine/k8s:1.33.5` for Bash plus
  kubectl, streams the reconcile script into the accounting pod with
  `kubectl exec -i`, and grants pod watch access for `kubectl wait`, so it does
  not need to mount the munge key or know the SlurmDBD endpoint. QOS preemption
  relationships are now applied in a second pass after all QOS objects exist,
  matching Slurm's `sacctmgr` validation order. Disabled by default; not
  supported on Managed Soperator (the Job cannot run in the operator namespace).
- Added large-cluster tuning surfaces typed in values:
  `partitionConfiguration.includeFile` appends a `Include=<path>` line to
  `customSlurmConfig` so operators or customers can hand-edit a partition
  file mounted into the controller outside the chart-managed list, and
  `controllerManager.manager.kubeClient.{qps,burst}` are emitted as
  `KUBE_API_QPS` / `KUBE_API_BURST` env vars on the Soperator manager for
  busy mk8s control-plane scenarios from the Big Cluster PoC.
- Added an opaque `nodesets[].topologyLabels` pass-through. The chart
  concatenates these entries onto `nodeConfig.features` so topology
  labels (rack, SU, fabric) become job-targetable Slurm features. The
  field is the chart-side hook for cxcli's GB300/NVL topology profile.
- Documented operational tuning of the slurmctld liveness probe and
  resource requests as standard typed value overrides in design.md,
  replacing the pattern of patching the operator's ConfigMap directly
  reported in support escalations.
- Added a typed Slurm scheduling and preemption surface. The new top-level
  `schedulingConfig` block models `preemptType`, `preemptMode`,
  `preemptParameters`, `jobRequeue`, `schedulerType`, `schedulerParameters`,
  `priorityType`, and the `priorityWeights.age` / `assoc` / `fairshare` /
  `partition` / `jobSize` / `qos` / `tres` knobs;
  these are rendered as Slurm.conf lines and appended to `customSlurmConfig`
  at template time.
- Added a typed per-partition `policy` block under
  `partitionConfiguration.partitions[]`. Supported fields: `priorityTier`,
  `preemptMode`, `default`, `hidden`, `state`, `maxTime`, `defaultTime`,
  `defMemPerNode`, `defMemPerCPU`, `defMemPerGPU`, `defCpuPerGPU`,
  `overSubscribe`, `allowAccounts`, `allowQos`, `denyAccounts`, `denyQos`. The
  free-form `config` string remains available for unmodeled Slurm.conf tokens
  and is appended after the typed tokens.
- Added hard-fail render validation when a typed key is also present in the
  matching raw escape hatch: `schedulingConfig.<field>` overlapping with
  `customSlurmConfig`, and `partitions[].policy.<field>` overlapping with the
  same partition's `config` string. The validator names the conflicting key.
- Documented the typed scheduling and preemption surfaces, the typed-vs-raw
  conflict rules, and the operational patterns (partition+preemption-only,
  QOS+fairshare, large-cluster `schedulerParameters` tuning) in
  `docs/design.md`.
- Added a safer parent-chart Enroot cleanup override through local-owned
  `local_slurm_scripts/cleanup_enroot.sh`, matching both old
  `pyxis_<jobid>...` and image-derived `pyxis_<image>.sqsh_<jobid>` names while
  keeping exact upstream script files untouched.
- Documented the opt-in cxcli QoS/preemption profile contract for Soperator:
  Slurm config and partitions are chart values, and self-managed clusters can
  reconcile SlurmDBD accounts, associations, QOS objects, and QOS preemption
  relationships through the opt-in `qosConfiguration` hook; Managed Soperator
  targets still coordinate those changes through the managed-service path.
- Expanded the design guide with a core Soperator architecture map covering
  custom resources, runtime roles, dependency chart responsibilities, and the
  standard Nebius SFS sharing model for production and onboarded clusters.
- Aligned the bundled Nebius production profile with the typed MK8s
  node-group inventory: the default GPU profile now uses the logical `worker`
  NodeSet alongside `system`, `controller`, `login`, and `accounting`, while
  direct Helm installs can still define any valid NodeSet layout.
- Documented chart-native placement for existing MK8s clusters through
  `k8sNodeFilters[]`, `slurmNodes.*.k8sNodeFilterName`, `nodesets[]`,
  `storage.*`, and `partitionConfiguration` values.
- Documented how chart-owned system helpers such as the Soperator manager,
  checks controller, and MariaDB operator can inherit a selected `system` node
  affinity.
- Taught cxcli-generated Nebius GPU-image installs to disable the Soperator
  DCGM job-mapping exporter's toolkit validation init wait.
- Fixed the storage mount helper scripts so failed virtiofs or glusterfs
  mounts are logged as failures and retried instead of being reported as
  successful mounts.
- Expanded NodeConfigurator rebooter RBAC for pod watches and pod evictions,
  and documented that rebooter tolerations must cover tainted worker nodes.
- Changed the bundled OpenKruise manager default to one replica so direct and
  cxcli-rendered Soperator installs can fit smaller Kubernetes clusters by
  default; larger HA installs can still override the replica count.
- Disabled Slurm topology by default so worker initialization does not wait for
  Soperator `tier-*` node labels on generic clusters; production overlays can
  still set `slurmConfig.topologyPlugin` and the matching topology label
  prefix explicitly.
- Added a cxcli-owned `values.topologyProfile` contract: `disabled` remains
  the generic default, while `nebius-tiered-tree-v1` explicitly enables
  `topology/tree` with `topology.nebius.com/tier-*` label discovery.
- Clarified the topology policy: the five-role Nebius production shape is role
  separation, while Slurm topology is a worker-locality optimization for fresh
  production deployments with accurate tier labels; generic and existing
  clusters should keep topology disabled until labels are prepared and
  verified.
- Documented that adding SFS attachments to existing MK8s node groups during
  cxcli onboarding is disruptive because Managed Kubernetes rolls node-template
  updates by replacing, cordoning, draining, and deleting nodes.
- Updated cxcli profile values to keep an internal `hidden` ActiveChecks
  partition alongside visible shape partitions and to avoid topology and
  node-health initial ActiveCheck runs when topology is disabled.
- Default bundled ActiveCheck resources to use a `hostUsers: true` PodTemplate
  so k8sJob checks run on MK8s runtimes without Kubernetes user namespaces.
- Granted the bundled checks controller read access to `PodTemplate` resources
  required by ActiveCheck `podTemplateNameRef`.
- Added a CPU-only ActiveChecks partition override so the `srun` readiness
  probe can target the rendered `cpu` partition instead of the upstream
  `hidden` default.
- Disabled GPU and prepull-dependent ActiveChecks in CPU-only examples and
  profiles so enabled checks do not wait on checks that are not rendered.
- Added default controller, login, and accounting `k8sNodeFilters` tolerations
  so Slurm control-plane pods can schedule on dedicated tainted service nodes.
- Changed the default SConfigController UID/GID to root so jailed Slurm config
  sync can write into the root-owned populated jail `/etc` tree.
- Updated the upstream Soperator lock, chart appVersion, tracked images, and
  review-only sync hashes to public Soperator release 3.0.4.
- Folded the mirrored upstream Soperator-family child charts into the
  parent chart as disabled-by-default `file://../...` dependencies while
  keeping their source folders as sibling charts.
- Removed the in-cluster `soperator-nfs-server` child dependency and source
  chart from this repository; production shared storage should use Nebius SFS,
  with VM-backed NFS kept outside this chart as an explicit non-HA
  compatibility path.
- Documented the Slack App incoming-webhook setup for the bundled
  `soperator-notifier` child chart, including cxcli's deploy-time and
  MysteryBox-backed Secret sources.
- Stopped tracking generated dependency archives under `charts/`; they are
  rebuilt from `Chart.lock` and still included in packaged chart releases.
- Authenticated the scheduled upstream latest-release check with the GitHub
  Actions token when available so CI does not depend on unauthenticated API
  quota.
- Documented the shared `nb-image-chart-publish` GitHub environment used by
  the chart publish workflow and clarified that only chart pushes require
  authentication.
- Added `publish-helm.sh` and a shared `helm-chart-publish` workflow
  for publishing this chart to a Nebius OCI registry.
- Added chart package publishing with Nebius `-ps.N` SemVer suffixes while
  keeping `Chart.yaml.appVersion` pinned to the upstream Soperator release.
- Added fail-fast validation for duplicate NodeSet names, structured partitions
  that reference missing NodeSets, empty structured partition lists, and
  malformed exporter accounting lookback durations.
- Fixed NodeSet flat image fallback for digest-pinned image references and
  defaulted per-NodeSet image dictionaries without `pullPolicy` to
  `IfNotPresent`.
- Trimmed `examples/minimal-gpu-values.yaml` so it only carries the real
  minimal GPU overrides instead of duplicating the default worker NodeSet.
- Hardened the upstream-import verifier so image sync can write only to
  declared local-owned files, and declared all current Soperator-family chart
  product files in the lock.
- Fixed the pre-delete cleanup hook to wait for Soperator-created Kruise
  StatefulSets by `clusterName`, matching upstream Soperator labels, while
  keeping Helm-rendered CR deletion scoped to the release label.
- Documented that Helm release names must be cluster-unique because the
  operator RBAC and webhook objects are cluster-scoped.
- Added optional child-chart documentation for active checks, K8up jail
  backups, and Soperator DCGM job-mapping telemetry while keeping the main
  chart focused on core Soperator resources.
- Changed the upstream Soperator release-drift workflow from weekly to daily
  while keeping it read-only.
- Expanded the upstream sync verifier into an upstream-import contract that
  tracks exact script imports, image value imports, and review-only upstream
  logic hashes for covered and intentionally replaced Soperator charts.
- Made `helm-charts/soperator-activechecks/scripts` an exact upstream import by
  moving local login-service hostname adaptation into the ActiveChecks render
  helper.
- Documented the separate `soperator-notifier` child-chart source boundary so
  the main Soperator chart remains focused on core Slurm/Soperator resources
  and does not store Slack webhook URLs in values.
- Added the initial cxcli-managed Soperator umbrella chart with vendored
  upstream 3.0.3 CRDs/templates, pinned OpenKruise dependency, Nebius SFS
  storage glue, optional external NFS values, and example values for GPU,
  accounting, NFS, and multi-NodeSet deployments.
- Added `upstream-soperator.lock.yaml`, a release-sync verifier, and CI checks
  so upstream-owned imports stay aligned with the pinned public Soperator
  release.
- Set `Chart.yaml.appVersion` to the pinned upstream Soperator release.
- Aligned default Slurm scripts and plug stack values with the locked
  Soperator 3.0.3 release by removing unreleased `main` script references from
  the chart.
- Rewrote `docs/design.md` as a simpler operator guide with short
  `SlurmCluster`, `NodeSet`, and `NodeConfigurator` examples, plus clearer
  explanations of where the matching chart values are defined.
- Documented the jail label, worker NodeSet label, Kubernetes scheduling flow,
  and CPU-only/GPU-only/mixed worker profile design.
- Clarified that the chart supports CPU-only workers, GPU-only workers, and
  mixed CPU+GPU workers, with separate homogeneous NodeSets and partition
  mappings for each scenario.
- Documented Slurm partition and feature design, including `debug`/`long`
  policy partitions and hardware features such as `h100`, `highmem`, and
  `infiniband` on NodeSet `nodeConfig.features`.
- Added CPU-only and mixed CPU/GPU values examples. Mixed examples use separate
  `worker-cpu` and `worker-gpu` NodeSets plus explicit Slurm partitions.
- Added a partition/features values example that shows shape partitions,
  policy partitions, and NodeSet features together.
- Documented the matching cxcli mixed-worker partition profile for H100 /
  InfiniBand feature partitions.
- Kept upstream Flux and DCGM Exporter charts out of the main chart so Flux
  remains cxcli-owned and default DCGM telemetry remains managed by the NVIDIA
  GPU Operator path.
- Enabled structured Slurm partitions by default so static NodeSets render
  explicit `NodeName`/`NodeSet` entries before worker pods register with
  `slurmctld`.
- Enabled chart-managed MariaDB accounting and Slurm REST by default for the
  SConfig reconciliation path.
- Aligned default node filters with the cxcli Soperator profile: populate-jail
  runs on the CPU-only `system` pool, accounting runs on the CPU-only
  `accounting` pool, and NFS remains an external VM-backed storage option rather
  than an MK8s node group.
- Removed the unused default `gpu` Kubernetes node filter because worker
  NodeSets schedule through their own selectors, and changed monitor scrape
  timeouts from `28s` to `20s` for the default `30s` interval.
- Moved the MariaDB Operator dependency toggle under `mariadb-operator` so the
  condition and subchart values use one dependency-name-aligned key.
- Added worker `slurm-scripts` mounts so `srun`, health checks, prolog, and
  epilog can run from the default chart values without relying on a chart-owned
  Slurm plugin-directory override.
- Derived Slurm GPU `Gres=gpu:<count>` from GPU NodeSet
  `slurmd.resources.gpu` when no explicit `nodeConfig.static` `Gres=` value is
  supplied, so GPU partitions support `--gres=gpu:*` requests without
  duplicating the GPU count in values.
- Stripped generated description text from the vendored Soperator CRDs so Helm
  release storage stays below the Kubernetes Secret size limit while preserving
  CRD schema behavior.
- Added an opt-in one-node local Kubernetes learning values profile that uses
  Kubernetes local PVs instead of Nebius SFS, disables accounting and Soperator
  admission webhooks, and keeps the default Nebius SFS path unchanged.
- Made the storage mount DaemonSet image configurable and gated Soperator
  webhook configuration rendering on `certManager.enabled`.
- Rendered the parent SlurmCluster annotation on chart-managed NodeSets so the
  local profile can run with Soperator admission webhooks disabled.
- Updated the local learning profile to disable the production SPANK plug stack
  and scale SConfigController to zero, which keeps a no-accounting local
  Kubernetes cluster free of local REST and plugin-path failures.
- Expanded the README with a feature summary and clearer wording that the local
  Kubernetes install path is a Helm values profile selected
  with `-f examples/local-k8s-one-node-cluster-values.yaml`.
- Added production operations guardrails for persistent Slurm config, worker
  scaling, immutable NodeSet storage, accounting/QoS, memory defaults,
  AppArmor/user namespaces, storage, and observability ownership.
- Added fail-fast validation that rejects `DefMemPerCPU` in
  `customSlurmConfig` because the Soperator 3.0.3 CRD defaults
  `defMemPerNode=0`.
- Fixed `SlurmCluster` rendering so custom `healthCheckConfig` overrides the
  GPU default instead of producing a duplicate YAML key.
- Aligned GPU worker examples and the cxcli Soperator profile on
  `NVIDIA_DRIVER_CAPABILITIES=compute,graphics,utility,video` while leaving the
  value overrideable per NodeSet.
- Aligned the README examples list with all shipped values profiles, including
  the partition/features profile.
- Added chart-local Git ignore rules and tightened `.helmignore` so local
  packaging/lint artifacts and the chart changelog stay out of packaged chart
  bytes.
- Fixed NodeSet image fallback so per-NodeSet `slurmd`, `munge`, and `sssd`
  images can be omitted while using the flat top-level `images.*` defaults.
- Scoped generated `slurm-scripts`, `mount-scripts`, and exporter ServiceAccount
  names to `clusterName`, and moved default worker script mounts into the
  NodeSet template.
- Corrected built-in Slurm component PriorityClass ordering so controller,
  login, accounting, exporter, REST, SConfigController, populate-jail, and
  MariaDB priorities are higher than worker priority.
- Added fail-fast validation for contradictory partition config, duplicate or
  unknown Kubernetes node filter references, and tighter schema coverage for
  maintenance mode, partition config type, Kubernetes-safe `clusterName`,
  NodeSet resources, NodeSet volumes, and `DefMemPerCPU`.
- Documented the supported SSH public-key workflow with Helm `--set-file` and
  trimmed trailing newlines from rendered login public keys so common `.pub`
  files can be used directly without storing a local path in values.
- Aligned production examples and NodeConfigurator RBAC with cluster-scoped
  naming by removing an unused worker filter and using `clusterName` for the
  NodeConfigurator ClusterRole/ClusterRoleBinding names.
- Removed unused leftover SlurmCluster helper files that no template invoked.
- Added a chart pre-delete cleanup hook so Helm uninstall deletes chart-owned
  Soperator CRs and waits for OpenKruise Advanced StatefulSets before the
  OpenKruise subchart finalizer runs.
- Documented the cert-manager prerequisite for the default webhook path and
  changed the pinned operator manager image pull policy to `IfNotPresent`.
- Renamed the local install documentation to local Kubernetes cluster wording
  and made the local profile select a labeled local storage node instead of
  implying the node-local jail PV is shared across local nodes.
- Updated the Docker Desktop registry-bypass example to stream the containerd
  `hosts.toml` file directly into the local node container instead of relying
  on an intermediate `docker cp`.
- Moved the local Kubernetes learning install instructions to the end of the
  README so the main install and feature sections stay focused on the
  production-grade Nebius MK8s path.
- Moved the maintainer-focused upstream release contract toward the end of the
  README, after validation and before the local learning appendix.
- Clarified mixed CPU/GPU worker wording to say Slurm partitions instead of
  ambiguous queues.
- Reorganized the README into a production-first flow with a structured table
  of contents, clearer architecture/install/defaults sections, and grouped
  worker and partition guidance.
- Reorganized `docs/design.md` with a structured table of contents and a
  reader path that moves from installation boundaries to runtime objects,
  storage, worker design, operations, cxcli wiring, and maintainer references.
- Simplified the README SSH public-key install example by passing the common
  `$HOME/.ssh/id_ed25519.pub` path directly to Helm `--set-file`.
- Clarified the README distinction between the minimal direct Helm install
  command and `examples/production-core-values.yaml` as an explicit production
  values overlay.
- Reorganized `values.yaml` comments into clearer chart API sections, moved
  Slurm exporter resources to the rendered `exporterContainer` values block,
  and documented exporter/REST roles in the design guide.
- Aligned example values files with consistent purpose comments, explicit
  cluster types, SSH public-key guidance, and explicit GPU partitions in the
  GPU-only overlays.
- Forced NodeSet PriorityClass values to render as decimal integers, reserved
  chart-managed worker script mount names, scoped pre-delete Kruise waits to
  the current Helm release, propagated `IfNotPresent` on flat image fallbacks,
  normalized exporter `ephemeralStorage`, documented `slurmNodes.sssd.image`,
  and widened SSH public-key validation for OpenSSH FIDO2 key types.
- Documented the exact node-label, storage, and default GPU worker-shape
  assumptions required when using the direct Helm install command without a
  values overlay.
- Simplified the README install guidance into a settings-focused direct Helm
  checklist and moved role/concept explanations into the design guide.
- Raised the default 8-GPU worker resources to `64` CPU, `512Gi` memory, and
  `50Gi` ephemeral storage, and aligned GPU examples and Slurm topology with
  that production-oriented default.
