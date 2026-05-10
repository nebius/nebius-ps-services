# soperator-activechecks

Companion chart that deploys `ActiveCheck` custom resources for an existing
Soperator Slurm cluster.

## Requirements

- Soperator CRDs and the target `SlurmCluster` must already exist.
- `soperator-checks` must be installed before these checks are expected to run.
- The chart defaults target `slurmClusterRefName=soperator`.
- Set `slurmClusterRefName` to the target `SlurmCluster.metadata.name`; the
  ActiveCheck refs, login-node SSH scripts, and Slurm config Secret/ConfigMap
  references are derived from it.
- Files under `scripts/` are exact imports from the pinned upstream Soperator
  release. Cluster and namespace adaptation is applied while rendering the
  scripts into `ActiveCheck` resources.

## Install

```bash
helm upgrade --install soperator-activechecks . \
  --namespace soperator \
  --set slurmClusterRefName=soperator
```

Set `waitForChecks.enabled=false` when you want GitOps to create the checks
without blocking the Helm release on run-after-creation checks.
