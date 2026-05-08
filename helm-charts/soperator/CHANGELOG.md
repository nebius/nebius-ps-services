# Changelog

All notable changes to this chart are tracked here.

## [Unreleased]

- Added the initial cxcli-managed Soperator umbrella chart with vendored
  upstream 3.0.3 CRDs/templates, pinned OpenKruise dependency, Nebius SFS
  storage glue, optional external NFS values, and example values for GPU,
  accounting, NFS, and multi-NodeSet deployments.
- Added `upstream-soperator.lock.yaml`, a release-sync verifier, and CI checks
  so upstream-owned `slurm_scripts/` stays an exact copy of the pinned public
  Soperator release.
- Set `Chart.yaml.appVersion` to the pinned upstream Soperator release and
  `Chart.yaml.version` to the same release base with a chart packaging suffix
  (`3.0.3-ps.1`).
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
- Kept upstream Flux and DCGM Exporter charts out of this chart so Flux remains
  cxcli-owned and DCGM remains managed by the NVIDIA GPU Operator path.
- Enabled structured Slurm partitions by default so static NodeSets render
  explicit `NodeName`/`NodeSet` entries before worker pods register with
  `slurmctld`.
- Enabled chart-managed MariaDB accounting and Slurm REST by default for the
  SConfig reconciliation path.
- Aligned default node filters with the cxcli Soperator profile: populate-jail
  runs on the CPU-only `system` pool, accounting runs on the CPU-only
  `accounting` pool, and NFS remains an external VM-backed storage option rather
  than an MK8s node group.
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
- Added an opt-in local Kind/Minikube learning values profile that uses local
  host-path PVs instead of Nebius SFS, disables accounting and Soperator
  admission webhooks, and keeps the default Nebius SFS path unchanged.
- Made the storage mount DaemonSet image configurable and gated Soperator
  webhook configuration rendering on `certManager.enabled`.
- Rendered the parent SlurmCluster annotation on chart-managed NodeSets so the
  local profile can run with Soperator admission webhooks disabled.
- Updated the local learning profile to disable the production SPANK plug stack
  and scale SConfigController to zero, which keeps a no-accounting one-node
  Docker Desktop/Kind cluster free of local REST and plugin-path failures.
- Expanded the README with a feature summary and clearer wording that the local
  Kind/Minikube/Docker Desktop install path is a Helm values profile selected
  with `-f examples/local-kind-values.yaml`.
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
