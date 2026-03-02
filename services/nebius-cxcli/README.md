# nebius-cxcli

`nebius-cxcli` is the Nebius customer experience CLI and an end-to-end automation workflow generator. From one canonical per-instance `config.yaml`, it generates Terraform, Flux, and CI workflow artifacts.

The current implementation is provider-driven and source-configured for Nebius environments:

- Infra components come from Terraform module sources.
- App components come from Helm chart sources.
- Runtime options and dependency checks use live provider/chart metadata where available.
- Canonical instance model is dynamic: `infra.components[]` and `apps.releases[]`.

## Table of Contents

- [Features](#features)
- [Runtime Metadata](#runtime-metadata)
- [Recommended Workflow](#recommended-workflow)
- [Commands](#commands)
- [Auth Bootstrap Workflow](#auth-bootstrap-workflow)
- [Examples](#examples)
- [Development](#development)
- [Security Notes](#security-notes)

## Features

- Single canonical `config.yaml` per instance.
- Source-driven component model from `component_sources.yaml`.
- `create` scaffolds or reconciles instance config idempotently.
- `create` writes dynamic/self-contained component state (`infra.components[]`, `apps.releases[]`) and embeds selected source snapshot under `component_sources`.
- App dependency resolution from Helm `Chart.yaml` metadata.
- `validate` runtime checks, plus `validate --strict` deployment-readiness checks.
- Generic `render` path for Terraform + Flux generation.
- `deploy` runs strict validate + render + terraform apply + Flux apply.
- `bootstrap-ci` generates CI workflow and can bootstrap/sync CI secrets.
- `discover` outputs config discovery JSON: changed `config.yaml` files in git mode, full scan outside git.

## Runtime Metadata

Primary source file (repo root):

- `component_sources.yaml`

Schema:

- `infra.tf_modules[]`: `module`, `source`, `version`, `group`, `enable`
- `apps.helm_charts[]`: `name`, `repo`, `version`, `namespace`, `releasename`, `group`, `enable`

Resolution precedence:

1. `--component-sources-file`
2. current working directory `./component_sources.yaml`
3. `NEBIUS_CXCLI_COMPONENT_SOURCES_FILE`
4. `~/.config/nebius-cxcli/component_sources.yaml`
5. `/etc/nebius-cxcli/component_sources.yaml`
6. repo default `component_sources.yaml` (when present)
7. bundled package default (`<install-prefix>/nebius_cxcli/component_sources.yaml` from wheel data-files)

`component_sources.yaml` is starter-only metadata for `create`.  
When `create` writes an instance `config.yaml`, it embeds only enabled component sources under `component_sources`.  
Config-based commands (`validate`, `render`, `deploy`, `bootstrap-ci`) resolve sources from embedded `config.yaml.component_sources`.

For `apps.helm_charts[]`, `namespace` and `releasename` are defaults:

- interactive create wizard prompts them for enabled apps
- non-interactive create can override with:
  - `--app-namespace <app-id>=<namespace>`
  - `--app-releasename <app-id>=<release-name>`

Runtime config shape:

- `infra.components[]`: `id`, `enabled`, `source`, `version`, `inputs`
- `apps.releases[]`: `id`, `section`, `enabled`, `values`
- Static nested component configs (`infra.<component>.enabled`, `apps.<section>.<release>.enabled`) are not supported.

## Recommended Workflow

1. `nebius-cxcli create <deployments-root>`
2. Edit the generated instance `config.yaml` with real values.
3. `nebius-cxcli validate --strict <config.yaml>`
4. Optional local generation/deploy:
   - `nebius-cxcli render <config.yaml>`
   - `nebius-cxcli deploy <config.yaml>`
5. Optional CI setup:
   - `nebius-cxcli bootstrap-ci <config.yaml>`

`create` is idempotent by default. Re-running the same instance identity reconciles selections and preserves existing values. Use `create --force` only when you want reset/overwrite behavior.

## Commands

| Command | Purpose | Git required |
| --- | --- | --- |
| `create <target_path>` | Scaffold/reconcile instance config and generated folder skeleton | No |
| `validate <config.yaml>` | Runtime validation | No |
| `validate --strict <config.yaml>` | Runtime + strict deployment-readiness checks | No |
| `render <config.yaml>` | Generate Terraform and Flux artifacts under `generated/` | No |
| `deploy <config.yaml>` | Strict validate + render + terraform apply + Flux apply | No |
| `bootstrap-ci <config.yaml>` | Generate customer workflow; optional auth/secret bootstrap | Yes |
| `discover <target_path>` | Changed-config detection in git mode, full scan outside git | No |
| `auth bootstrap` | Idempotent CI identity/secret bootstrap helper | Optional (only for GitHub sync) |

## Auth Bootstrap Workflow

`auth bootstrap` behavior is idempotent-first:

- Always ensures service account + role grants.
- Default mode (`--github-sync`, `--no-create-keys`):
  - Checks required GitHub secrets.
  - If NEBIUS secrets are missing, it creates fresh keys and syncs them.
  - If only `FLUX_GITHUB_TOKEN` is missing, it syncs that token only.
  - If all required secrets exist, it performs no secret changes.
- `--no-github-sync` keeps it local:
  - Without `--create-keys`: identity-only (no key rotation).
  - With `--create-keys`: generates fresh keys; optional `--private-key-out` writes auth private key.

This keeps repeated runs safe by default while still allowing explicit rotation.

## Examples

```bash
# Interactive create (default wizard mode)
nebius-cxcli create /path/to/deployments-root

# Non-interactive create/reconcile
nebius-cxcli create /path/to/deployments-root \
  --client-name client-a \
  --tenant-id tenant-123 \
  --env prod \
  --cluster-name cluster-a \
  --project-id project-123 \
  --infra mk8s,object-storage \
  --app n8n \
  --app-namespace n8n=automation \
  --app-releasename n8n=workflow-core \
  --no-interactive

# Validate and render
nebius-cxcli validate --strict /path/to/config.yaml
nebius-cxcli render /path/to/config.yaml

# Local deploy
nebius-cxcli deploy /path/to/config.yaml

# CI workflow bootstrap
nebius-cxcli bootstrap-ci /path/to/config.yaml

# Auth bootstrap (identity + GitHub secret sync)
nebius-cxcli auth bootstrap --instance-config /path/to/config.yaml

# Auth bootstrap identity-only (no secret sync, no key rotation)
nebius-cxcli auth bootstrap --project-id project-123 --no-github-sync

# Explicit key creation in local/no-github-sync mode
nebius-cxcli auth bootstrap --project-id project-123 --no-github-sync --create-keys --private-key-out ./ci-auth.pem
```

## Development

Python: `3.12+`

```bash
make venv
make all
```

Useful checks:

```bash
python -m nebius_cxcli --help
python -m nebius_cxcli create --help
python -m nebius_cxcli auth bootstrap --help
```

Runtime plugin env knobs:

- `NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS`: comma-separated `module.path:function` plugins.
  - Default: none (core structural/runtime checks only).
- `NEBIUS_CXCLI_PROVIDER_OPTION_PLUGINS`: optional provider-option lookup plugins.
- `NEBIUS_CXCLI_STRICT_PROVIDER_OPTION_CHECKS=1`: enable live option membership checks in strict mode.

## Security Notes

- Keep deployment repositories private.
- Never commit credentials or secret values.
- GitHub sync requires a token with permission to write repo Actions secrets.
- Key rotation is explicit in local mode (`--create-keys`) and automatic only when sync needs missing NEBIUS secrets.
