# soperator-backup-config

Companion chart that renders a K8up `Schedule` for Soperator jail backups.

## Requirements

- K8up CRDs must be installed before this schedule is applied.
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
