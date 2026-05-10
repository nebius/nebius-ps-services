# Changelog

All notable changes to this chart are tracked here.

## [Unreleased]

- Documented the shared `nb-image-chart-publish` GitHub environment used by
  the chart publish workflow and clarified that only chart pushes require
  authentication.
- Added `publish-helm.sh` and a shared `helm-chart-publish` workflow
  for publishing this chart to a Nebius OCI registry.
- Split chart package versioning from the upstream Soperator release:
  `Chart.yaml.version` is now this chart's SemVer, while
  `Chart.yaml.appVersion` remains pinned to the upstream Soperator release.
- Added fail-fast validation for duplicate NodeSet names, structured partitions
  that reference missing NodeSets, empty structured partition lists, and
  malformed exporter accounting lookback durations.
- Fixed NodeSet flat image fallback for digest-pinned image references and
  defaulted per-NodeSet image dictionaries without `pullPolicy` to
  `IfNotPresent`.
- Trimmed `examples/minimal-gpu-values.yaml` so it only carries the real
  minimal GPU overrides instead of duplicating the default worker NodeSet.
- Hardened the upstream-import verifier so image sync can write only to
  declared local-owned files, and declared all current Soperator companion
  chart product files in the lock.
- Fixed the pre-delete cleanup hook to wait for Soperator-created Kruise
  StatefulSets by `clusterName`, matching upstream Soperator labels, while
  keeping Helm-rendered CR deletion scoped to the release label.
- Documented that Helm release names must be cluster-unique because the
  operator RBAC and webhook objects are cluster-scoped.
- Added optional companion-chart documentation for active checks, K8up jail
  backups, Soperator DCGM job-mapping telemetry, and in-cluster NFS while
  keeping the main chart focused on core Soperator resources.
- Changed the upstream Soperator release-drift workflow from weekly to daily
  while keeping it read-only.
- Expanded the upstream sync verifier into an upstream-import contract that
  tracks exact script imports, image value imports, and review-only upstream
  logic hashes for covered and intentionally replaced Soperator charts.
- Made `helm-charts/soperator-activechecks/scripts` an exact upstream import by
  moving local login-service hostname adaptation into the ActiveChecks render
  helper.
- Documented the new separate `soperator-notifier` companion chart boundary so
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
- Added the pinned-image Slurm plugin directory override and worker
  `slurm-scripts` mounts so `srun`, health checks, prolog, and epilog can run
  from the default chart values.
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
