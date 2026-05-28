# soperator-backup-config

Child chart that renders a K8up `Schedule` for Soperator jail backups.

## When To Use

Use this chart only when the Soperator jail should be backed up to Object
Storage. The main `soperator` umbrella chart enables both this schedule and the
K8up dependency with `soperator-backup-config.enabled=true`; cxcli keeps it
disabled by default and wires the runtime credentials only when backup is
enabled for the deployment.

## Requirements

- The parent `soperator` chart installs K8up as an optional dependency when
  `soperator-backup-config.enabled=true`.
- Standalone use requires the operator to install the K8up controller and CRDs
  before applying the rendered schedule.
- `bucket.name` and `bucket.endpoint` must point to the Object Storage bucket.
- Backup credentials must exist in a Kubernetes Secret referenced by
  `secret.name` and `secret.keys.*`.

This chart does not accept access keys or repository passwords as Helm values.
When used through `nebius-cxcli`, the Secret is created at deploy time from
environment variables or an interactive hidden prompt. The accepted environment
variables are `NEBIUS_CXCLI_SOPERATOR_BACKUP_AWS_ACCESS_KEY_ID`,
`NEBIUS_CXCLI_SOPERATOR_BACKUP_AWS_SECRET_ACCESS_KEY`, and
`NEBIUS_CXCLI_SOPERATOR_BACKUP_REPOSITORY_PASSWORD`; target-scoped projects can
also use the same names with a `_<TARGET>` suffix.

## Install

```bash
helm upgrade --install soperator-jail-backup . \
  --namespace soperator \
  --set enabled=true \
  --set bucket.name=<bucket-name> \
  --set bucket.endpoint=https://<bucket-endpoint>:443
```
