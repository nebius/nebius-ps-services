# soperator-activechecks

Companion chart that deploys `ActiveCheck` custom resources for an existing
Soperator Slurm cluster.

## When To Use

Use this chart when a Soperator cluster should create and run Soperator
`ActiveCheck` resources. The main `soperator` umbrella chart enables it with
`soperator-activechecks.enabled=true`; cxcli production profiles keep it
disabled by default and should enable it only for benchmark/diagnostic clusters
or maintenance windows. Standalone installs are useful only after the target
`SlurmCluster` and `soperator-checks` controller already exist.
The parent `soperator` chart and cxcli production profiles override
`waitForChecks.enabled=false`; this standalone chart's `true` default is for
direct ActiveChecks installs that intentionally block on check completion.

## Requirements

- Soperator CRDs and the target `SlurmCluster` must already exist.
- `soperator-checks` must be installed before these checks are expected to run.
- The chart defaults target `slurmClusterRefName=soperator`.
- Set `slurmClusterRefName` to the target `SlurmCluster.metadata.name`; the
  ActiveCheck refs, login-node SSH scripts, and Slurm config Secret/ConfigMap
  references are derived from it.
- Files under `scripts/` are exact imports from the pinned upstream Soperator
  release. Cluster and namespace adaptation is applied while rendering the
  scripts into `ActiveCheck` resources. A check can set
  `slurmJobSpec.sbatchScriptFile` to render a local-owned script outside
  `scripts/`, or `slurmJobSpec.sbatchScript` for a one-off inline script,
  without editing the imported script files.
- `slurmJobSpec.sbatchScriptFile`, `k8sJobSpec.scriptFile`, and
  `k8sJobSpec.pythonScriptFile` must point to files packaged in this chart.
  Helm rendering fails fast when one of those configured paths is missing.
- `srunReadyPartition` selects the partition used by the
  `wait-for-soperatorchecks-srun-ready` probe. The default remains upstream's
  `hidden`; set it to the active CPU-only partition when no hidden partition is
  rendered.
- The default `enroot-cleanup` check points to local-owned
  `local_scripts/enroot-cleanup.sh` so it recognizes both `pyxis_<jobid>...`
  and image-derived `pyxis_<image>.sqsh_<jobid>` names while avoiding broad
  `pyxis_*` deletion.
- By default the chart renders a shared `PodTemplate` with `hostUsers: true`
  and attaches ActiveChecks through `podTemplateNameRef`. This avoids requiring
  Kubernetes user namespace support for k8sJob checks on MK8s.

## Install

```bash
helm upgrade --install soperator-activechecks . \
  --namespace soperator \
  --set slurmClusterRefName=soperator
```

Set `waitForChecks.enabled=false` when you want GitOps to create the checks
without blocking the Helm release on run-after-creation checks.
