# nebius-cxcli

`nebius-cxcli` is the Nebius customer experience CLI and an end-to-end automation workflow generator. From one canonical per-instance `config.yaml`, it generates Terraform, Flux, and CI workflow artifacts.

The current implementation is provider-driven and source-configured for Nebius environments:

- Infra components come from Terraform module sources.
- App components come from Helm chart sources.
- Runtime options and dependency checks use live provider/chart metadata where available.
- Canonical instance model is dynamic: `infra.components[]` and `apps.charts[]`.

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
- `create` writes dynamic component state (`infra.components[]`, `apps.charts[]`).
- `create` keeps only selected components/charts in `config.yaml` (unselected entries are omitted).
- `create` validates `component_sources.yaml` by default (`--no-validate-sources` to skip).
- `create` auto-manages a deployments-root `.gitignore` block when target path is inside a git repo (covers generated Flux/Terraform artifacts across all instances).
- App dependency resolution from Helm `Chart.yaml` metadata.
- Interactive wizard supports `q` to stop optional phases/field prompting.
- `create` validates `tenant_id`/`project_id` against Nebius IAM APIs before continuing.
- Infra field options are resolved dynamically from Nebius APIs where supported.
- Flux output is flat under `generated/flux` (no `apps/` or `sources/` subdirectories).
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

`apps.helm_charts[].repo` supports:

- HTTP/S Helm repositories (must serve `index.yaml`)
- OCI chart repositories (`oci://...`)

Source requirements enforced by `validate-sources`:

- Terraform modules (`infra.tf_modules[]`):
  - `module` must be lowercase letters/digits/hyphens.
  - Local `source` must resolve to an existing directory.
  - Directory must contain at least one `*.tf` file.
  - Missing `main.tf` or `variables.tf` is reported as warning (wizard field discovery depends on variables).
  - Remote sources (`git::`, `http(s)://`, `oci://`) are accepted but local structure checks are skipped with warning.
- Helm charts (`apps.helm_charts[]`):
  - HTTP repo format: `repo` must be a Helm repo base URL, `repo/index.yaml` must be readable, chart must exist in `entries`, and configured version must exist.
  - OCI format: `repo` must be direct OCI chart ref (`oci://.../<chart-name>`), basename must match chart `name`, and configured version must be a semantic version tag.
  - For both HTTP and OCI, Helm metadata (`Chart.yaml`) is checked when available: chart name and resolved version must match configured values.

Resolution precedence:

1. `--component-sources-file`
2. current working directory `./component_sources.yaml`
3. `NEBIUS_CXCLI_COMPONENT_SOURCES_FILE`
4. `~/.config/nebius-cxcli/component_sources.yaml`
5. `/etc/nebius-cxcli/component_sources.yaml`
6. repo default `component_sources.yaml` (when present)
7. bundled package default (`<install-prefix>/nebius_cxcli/component_sources.yaml` from wheel data-files)

`component_sources.yaml` is the full source catalog for `create` component selection.  
`enable: true|false` controls only default checkbox state in the wizard.  
`config.yaml` does not embed `component_sources`; source resolution uses the resolved `component_sources.yaml` path.

For `apps.helm_charts[]`, `namespace` and `releasename` are defaults:

- interactive create wizard prompts them for enabled apps
- non-interactive create can override with:
  - `--app-namespace <app-id>=<namespace>`
  - `--app-releasename <app-id>=<release-name>`

Runtime config shape:

- `client_info`: `client_name`, `nebius.{tenant_id,project_id,region_id}`, `notifications.{inventory_markdown,email}`
- `client_info` does not include legacy `env` or `cluster_name` fields.
- `infra.components[]`: `id`, `enabled`, `source`, `version`, `inputs`
- `apps.charts[]`: `id`, `group`, `enabled`, `repo`, `version`, `namespace`, `release-name`, `values`
- Static nested component configs (`infra.<component>.enabled`, `apps.<group>.<chart>.enabled`) are not supported.
- Canonical instance path: `<deployments-root>/instances/<client-name>--<tenant-id>/<project-id>/config.yaml`

Flux render output (canonical):

- `generated/flux/helm-repositories.yaml`
- `generated/flux/helmrelease-<group>-<release>.yaml`
- `generated/flux/kustomization.yaml`
- Legacy nested Flux layout (`generated/flux/apps`, `generated/flux/sources`) is not supported.

Terraform render output (canonical):

- `generated/infra/versions.tf`
- `generated/infra/providers.tf`
- `generated/infra/variables.tf`
- `generated/infra/main.tf`
- `generated/infra/terraform.auto.tfvars.json`
- `generated/infra/.terraform.lock.hcl` (generated during `render` when `terraform` is available)
- Source-backed local module paths (`platform-infra/modules/*`) are canonicalized to git module sources with `?ref=...` for portability.

Wizard field behavior:

- Infra input field names are discovered dynamically from Terraform module variables (required and optional).
- Wizard prompts required Terraform variables first (plus existing optional values and dependency-enabled option sets).
- Prompt labels include Terraform input type hints (for example `string`, `number`, `bool`) and `required` markers.
- Source-backed infra `inputs.parent_id`/`inputs.project_id` default to `client_info.nebius.project_id` when those variables exist.
- `infra.ssh_public_key` defaults from local `~/.ssh/*.pub` when available, and module `inputs.ssh_public_key` inherits that value.
- Provider-backed option lists are inferred by field patterns and resolved live from Nebius APIs when available.
- Current built-in provider option sources include:
  - `mk8s_compatible_platforms` (for mk8s platform fields)
  - `compute_platforms`
  - `compute_platform_presets`
  - `project_subnets`
  - `project_networks`
  - `tenant_projects`
  - `mk8s_control_plane_versions`
- When live provider options are unavailable, the wizard falls back to manual input.

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

Global options:

- `--version`
- `--component-sources-file <path>`

| Command | Purpose | Git required |
| --- | --- | --- |
| `create <target_path>` | Scaffold/reconcile instance config and generated folder skeleton | No |
| `validate-sources` | Validate `component_sources.yaml` module/chart sources | No |
| `bootstrap-ci <config.yaml>` | Generate customer workflow; optional auth/secret bootstrap | Yes |
| `validate <config.yaml>` | Runtime validation | No |
| `validate --strict <config.yaml>` | Runtime + strict deployment-readiness checks | No |
| `render <config.yaml>` | Generate Terraform and Flux artifacts under `generated/` | No |
| `deploy <config.yaml>` | Strict validate + render + terraform apply + Flux apply | No |
| `discover <target_path>` | Changed-config detection in git mode, full scan outside git | No |
| `email <config.yaml>` | Send inventory markdown to `client_info.notifications.email` via SMTP env vars | No |
| `terraform plan <config.yaml>` | Run Terraform init + plan in `generated/infra` | No |
| `terraform apply <config.yaml>` | Run Terraform init + apply in `generated/infra` | No |
| `flux bootstrap <config.yaml>` | Bootstrap Flux if missing; otherwise reconcile | No |
| `inventory write <config.yaml>` | Write local non-sensitive inventory files | No |
| `inventory upload <config.yaml>` | Upload inventory artifacts to Nebius Object Storage | No |
| `auth bootstrap` | Idempotent CI identity/secret bootstrap helper | Optional (only for GitHub sync) |

Common command flags:

- `create`:
  `--client-name`, `--tenant-id`, `--project-id`, `--region-id`, `--email`, `--infra`, `--app`, `--app-namespace`, `--app-releasename`, `--validate-sources/--no-validate-sources`, `--no-interactive`, `--force`
- `bootstrap-ci`:
  `--force`, `--auth-bootstrap/--no-auth-bootstrap`, `--github-repo`, `--github-token-env`
- `validate`: `--strict`
- `deploy`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`
- `discover`: `--all`
- `terraform plan`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`
- `terraform apply`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`
- `flux bootstrap`: `--auto-auth-bootstrap`
- `auth bootstrap`:
  `--project-id`, `--instance-config`, `--service-account-name`, `--service-account-description`, `--role-id`, `--auth-key-description`, `--access-key-description`, `--profile`, `--endpoint`, `--sdk-config-file`, `--private-key-out`, `--create-keys/--no-create-keys`, `--json`, `--github-sync/--no-github-sync`, `--github-repo`, `--github-token-env`, `--github-set-flux-token/--no-github-set-flux-token`

## Auth Bootstrap Workflow

Terraform runtime auth behavior:

- Generated `providers.tf` uses direct provider fields (`service_account.account_id/public_key_id/private_key_file`) and sets `module_name`.
- Runtime values are passed through Terraform variables (`TF_VAR_*`) instead of provider `_env` indirection.
- Local runtime auth can be auto-bootstrapped with a dedicated service account name: `nebius-cxcli-tf-sa`.
- Auto-bootstrapped runtime auth material is cached under `~/.config/nebius-cxcli/runtime-auth/<project-id>/` to avoid creating new key material every run.
- Terraform runtime no longer requires `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`.

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
