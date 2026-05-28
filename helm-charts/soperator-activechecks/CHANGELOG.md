# Changelog

## [Unreleased]

- Added render-time validation for ActiveCheck script file references so
  missing `sbatchScriptFile`, `scriptFile`, or `pythonScriptFile` values fail
  Helm rendering with the exact check path.
- Clarified when to use the chart standalone versus through the parent
  Soperator umbrella chart and cxcli production profiles.
- Added `slurmJobSpec.sbatchScript` inline script rendering for Slurm-job
  ActiveChecks, and moved the default `enroot-cleanup` override to
  local-owned `local_scripts/enroot-cleanup.sh` so image-derived Pyxis names
  are handled without editing imported upstream scripts.
- Render a shared `PodTemplate` with `hostUsers: true` and attach ActiveChecks
  through `podTemplateNameRef` so k8sJob checks run on MK8s runtimes without
  Kubernetes user namespaces.
- Updated the pinned Soperator ActiveCheck chart import for upstream release
  3.0.4 and packaged it as a parent-chart child dependency.
- Kept ActiveCheck script files as exact upstream imports and moved namespace
  and `slurmClusterRefName` adaptation into the script render helper.
- Added `srunReadyPartition` so CPU-only clusters can point the
  `wait-for-soperatorchecks-srun-ready` probe at their rendered partition.
- Added render-time validation so enabled ActiveChecks cannot depend on
  disabled or unknown checks.
- Moved the post-install wait image behind `waitForChecks.image`.
