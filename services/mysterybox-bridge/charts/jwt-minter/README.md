# jwt-minter chart (optional scaffold)

This chart is an optional extension for teams that need a dedicated JWT-minter
component between ESO and backend auth providers.

`nebius-cxcli` does **not** require this chart for the default MysteryBox flow.

## What this scaffold includes

- CronJob with production defaults:
  - `concurrencyPolicy: Forbid`
  - deadlines/history limits/backoff/TTL settings
- ServiceAccount + Role + RoleBinding for Secret write operations.
- Pod/container security context baseline.
- Values validation via `values.schema.json`.

## Important scope

The CronJob command is intentionally a placeholder; you must add real token-mint
and Secret upsert logic for your environment.

## Install (disabled by default)

```bash
helm upgrade --install jwt-minter ./charts/jwt-minter \
  --namespace external-secrets \
  --create-namespace \
  --set enabled=true
```
