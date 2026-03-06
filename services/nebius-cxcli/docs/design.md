# nebius-cxcli Design

## Table of Contents

- [1. Goal](#1-goal)
- [2. Architecture Summary](#2-architecture-summary)
- [3. Runtime Source Model](#3-runtime-source-model)
- [4. Config Model](#4-config-model)
- [5. Command Workflow](#5-command-workflow)
- [6. Idempotency Rules](#6-idempotency-rules)
- [7. Validation Model](#7-validation-model)
- [8. Render Model](#8-render-model)
- [9. Auth and CI Bootstrap Model](#9-auth-and-ci-bootstrap-model)
- [10. Vendor Scope](#10-vendor-scope)
- [11. Source Code Structure](#11-source-code-structure)

## 1. Goal

`nebius-cxcli` provides a single, repeatable operator workflow:

1. Create/reconcile one instance configuration (`config.yaml`).
2. Validate configuration safety/readiness.
3. Render deterministic Terraform and Flux artifacts.
4. Optionally deploy locally and/or bootstrap CI automation.

The design target is source-driven runtime behavior with minimal fixed component logic in core command paths.

## 2. Architecture Summary

Core principles:

- Single canonical instance file: `config.yaml`.
- Source-driven component discovery from `component_sources.yaml`.
- Runtime introspection for module/chart fields and chart dependencies.
- Wizard field model: infra inputs come from Terraform module variables, with Nebius API-backed option discovery inferred by field/path patterns.
- Generic render path for Terraform modules/resources and Flux Helm releases.
- Optional plugin boundaries for provider-specific runtime option lookups and validation.

## 3. Runtime Source Model

Primary source registry (repo root): `component_sources.yaml`

Sections:

- `infra.tf_modules[]`
  - `module`, `source`, `version`, `group`, `enable`
- `apps.helm_charts[]`
  - `name`, `repo`, `version`, `namespace`, `releasename` (or `release-name`), `group`, `enable`
  - `repo` can be HTTP/S Helm repo base (must expose `index.yaml`) or OCI (`oci://...`)

Source validation requirements (`validate-sources`):

- Terraform module sources (`infra.tf_modules[]`):
  - `module` token must match runtime component id format (lowercase letters/digits/hyphens).
  - Local `source` must resolve to an existing directory with at least one `*.tf` file.
  - Missing `main.tf` or `variables.tf` is warning-level (not hard-fail), because wizard variable discovery relies on module variable definitions.
  - Remote sources (`git::`, `http(s)://`, `oci://`) are accepted, but local file-structure checks are skipped with warning.
- Helm chart sources (`apps.helm_charts[]`):
  - HTTP repo mode: `repo` is a Helm repository base URL; `index.yaml` must be readable; chart name and configured version must be present in index entries.
  - OCI mode: `repo` is a direct chart URL (`oci://.../<chart-name>`); OCI basename must match chart name; configured version must be semver tag.
  - For both modes, live Helm metadata checks (when Helm is available) validate `Chart.yaml` name and version against configured source fields.

Resolution precedence:

1. CLI `--component-sources-file`
2. current working directory `./component_sources.yaml`
3. env `NEBIUS_CXCLI_COMPONENT_SOURCES_FILE`
4. user file `~/.config/nebius-cxcli/component_sources.yaml`
5. global file `/etc/nebius-cxcli/component_sources.yaml`
6. repo default `component_sources.yaml` (when present)
7. bundled package default (`<install-prefix>/nebius_cxcli/component_sources.yaml` from wheel data-files)

Instance self-containment:

- `component_sources.yaml` is starter-only input for `create`.
- `create` component selection uses the full resolved `component_sources.yaml` catalog.
- In `component_sources.yaml`, `enable` controls default selection state only.
- `create` persists only selected `infra.components[]` and `apps.charts[]` rows in `config.yaml`.
- `config.yaml` does not embed `component_sources`.
- Config-based commands resolve sources from the active `component_sources.yaml` resolution path.
- Canonical instance path is `<deployments-root>/instances/<client-name>--<tenant-id>/<project-id>/config.yaml`.
- App chart defaults (`namespace`, `releasename`) can be edited in wizard mode or overridden in non-interactive mode with `--app-namespace` and `--app-releasename`.

Wizard field/option model:

- Infra input fields are discovered from module `variables.tf` (required and optional variables for source-backed modules).
- Required variables are prioritized during prompts and enforced by strict validation.
- Prompt labels include Terraform type hints (for example `string`, `number`, `bool`) and `required` markers.
- `create` validates `tenant_id` and `project_id` via Nebius IAM APIs before optional wizard phases.
- For source-backed modules, `inputs.parent_id`/`inputs.project_id` are pre-seeded from `client_info.nebius.project_id` when those variables are present.
- Shared `infra.ssh_public_key` is auto-seeded from local `~/.ssh/*.pub` when available, and source-backed module `inputs.ssh_public_key` inherits it.
- Optional module inputs are prompted when already set, when they are toggle fields, or when dependency-enabled prefixes are active (for example `gpu_enabled=true` enables `gpu_*` prompts).
- Wizard option sources are inferred by field conventions and resolved live via Nebius APIs when available.
- Built-in Nebius provider option sources include:
  - `mk8s_compatible_platforms`
  - `compute_platforms`
  - `compute_platform_presets`
  - `project_subnets`
  - `project_networks`
  - `tenant_projects`
  - `mk8s_control_plane_versions`
- Wizard stop token is `q`; on exit, remaining fields keep defaults.

## 4. Config Model

Runtime config root keys:

- `version`
- `client_info`
- `infra`
- `apps`

Canonical `client_info` keys:

- `client_name`
- `nebius.tenant_id`
- `nebius.project_id`
- `nebius.region_id`
- `notifications.inventory_markdown`
- `notifications.email`

Legacy `client_info.env` and `client_info.cluster_name` are not supported.

Canonical model is dynamic:

- `infra.components[]`: `id`, `enabled`, `source`, `version`, `inputs`
- `apps.charts[]`: `id`, `group`, `enabled`, `repo`, `version`, `namespace`, `release-name`, `values`
- Static nested component blocks are not accepted.

Commands operate from this dynamic model with source metadata coming from `component_sources.yaml`.

## 5. Command Workflow

### `create <target_path>`

- Creates or reconciles one instance path and `config.yaml`.
- Wizard-first for identity and component prompts (unless `--no-interactive`).
- Uses source-driven infra/app entries.
- Resolves app dependencies from live Helm chart metadata (`Chart.yaml`) when available.
- Resolves infra field options from live Nebius APIs where option sources are inferred.

### `validate <config.yaml>`

- Core runtime/structural checks.

### `validate-sources`

- Validates `component_sources.yaml` (Terraform module paths and Helm source definitions).

### `validate --strict <config.yaml>`

- Adds deployment-readiness checks:
  - placeholder rejection
  - chart source/dependency checks
  - module source and required-variable checks
  - provider-schema/resource checks when available

### `render <config.yaml>`

- Writes deterministic artifacts under `generated/infra` and `generated/flux`.
- When Terraform is installed, attempts backend-ready `terraform init` to produce/update `.terraform.lock.hcl`.
- Automatically performs create-if-missing runtime auth bootstrap for backend-ready lockfile init.

### `deploy <config.yaml>`

- Runs strict validate + render + terraform apply + flux apply.
- Ensures remote-state backend bucket exists before Terraform init/apply.

### `bootstrap-ci <config.yaml>`

- Generates `.github/workflows/nebius-deployments.yml`.
- Optional CI auth/environment-secret bootstrap.

### `auth` (flag-driven)

- `auth --create` creates runtime auth cache/profile only when missing.
- `auth --recreate` always rotates runtime auth material and rewrites cache.
- `auth --validate-profile` inspects cached runtime auth profile metadata/private key and verifies Nebius auth public key visibility.
- `auth --bootstrap-ci` syncs local runtime auth cache material into GitHub environment secrets.

## 6. Idempotency Rules

- `create`: idempotent reconcile by default; `--force` is explicit reset.
- `validate`/`render`: deterministic and repeatable.
- `deploy`: convergent behavior expected from strict+render+apply sequence.
- `bootstrap-ci`: idempotent workflow file handling; `--force` only for overwrite.
- `auth --create`: idempotent create-if-missing.
- `auth --recreate`: explicit rotation path.
- `auth --validate-profile`: read-only profile validation; safe to re-run.
- `auth --bootstrap-ci`: idempotent environment-secret upsert from local cache.

## 7. Validation Model

Validation layers:

1. Structural/runtime config checks (`runtime_validation.py`).
2. Dynamic payload shape checks (`validate_dynamic_payload_structure`).
3. Strict checks in CLI for deployment readiness.
4. Optional plugin validation via `NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS`.

Plugin default:

- Default runtime validation plugins are disabled.
- Operators can enable custom/provider-specific rule packs explicitly.

## 8. Render Model

Infra render:

- Root-module Terraform layout with separated concerns:
  - `backend.tf`: authoritative remote-state backend config (root-owned; non-secret).
  - `versions.tf`: authoritative Terraform and provider constraints for generated root module.
  - `providers.tf`: provider configuration (child modules do not define provider blocks).
  - `variables.tf`: generated variable declarations for module arguments.
  - `main.tf`: module/resource orchestration only.
  - `terraform.auto.tfvars.json`: concrete values for generated variables.
- Generic module blocks from enabled infra module entries.
- Generic provider resource blocks from dynamic provider components.
- Source-backed local module paths under `platform-infra/modules/*` are canonicalized to git module sources with `?ref=...` for portability in customer repositories.
- Deterministic output files:
  - `generated/infra/backend.tf`
  - `generated/infra/versions.tf`
  - `generated/infra/providers.tf`
  - `generated/infra/variables.tf`
  - `generated/infra/main.tf`
  - `generated/infra/terraform.auto.tfvars.json`
  - `generated/infra/.terraform.lock.hcl` (generated by backend-enabled `terraform init` during CLI `render` when Terraform is available)
- Remote-state backend is distinct from app/object-storage components:
  - Bucket/key/endpoint settings are derived from `client_info` (`client_name`, `project_id`, `region_id`).
  - `infra.components[id=object-storage]` remains workload/application storage only.
- Before any Terraform init path (`render` lockfile init, `terraform plan`, `terraform apply`, `deploy`), CLI ensures the backend bucket exists via Nebius Storage API.

Flux render:

- Generic Helm source docs (`HelmRepository` HTTP/OCI or `GitRepository` for standalone chart sources).
- Generic HelmRelease docs from enabled app releases.
- Deterministic flat output under `generated/flux`:
  - `helm-repositories.yaml`
  - `helmrelease-<group>-<release>.yaml`
  - `kustomization.yaml`
- Legacy nested Flux layout (`generated/flux/apps` and `generated/flux/sources`) is not supported.

## 9. Auth and CI Bootstrap Model

`bootstrap-ci`:

- Generates workflow file.
- With auth bootstrap enabled, derives GitHub environment name as `<client_name>-<project_id>`, ensures that environment exists, then checks/syncs missing environment secrets.

`auth`:

- Reads `~/.config/nebius-cxcli/<client_name>-<project-id>/runtime-auth.json`.
- `--create`: creates runtime auth profile if cache is missing; otherwise no rotation.
- `--recreate`: always rotates keys and refreshes cached material.
- `--validate-profile`: checks local private key presence and verifies auth public key visibility via Nebius IAM API.
- `--bootstrap-ci`: syncs local cached auth material into GitHub environment secrets (`<client_name>-<project_id>`); requires existing local cache material.

Terraform runtime auth:

- Generated `providers.tf` uses direct Nebius provider service-account fields and `module_name`.
- Runtime auth material is passed to Terraform via `TF_VAR_*` rather than provider `_env` fields.
- Runtime auto-bootstrap uses dedicated service account name `nebius-cxcli-tf-sa`.
- Auto-bootstrapped runtime auth material is cached under `~/.config/nebius-cxcli/<client_name>-<project-id>/`.
- Terraform backend path requires AWS-compatible Object Storage keys (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`); runtime auth cache provides them automatically when bootstrapped.

## 10. Vendor Scope

Current runtime implementation is Nebius-focused:

- Nebius SDK/API integration for auth/IAM and provider option lookups.
- Nebius-oriented defaults for provider/config behavior.

The component source model itself is Terraform-module + Helm-chart based, but this release does not claim full multi-vendor runtime support.

## 11. Source Code Structure

- `src/nebius_cxcli/cli.py`: CLI entrypoints, orchestration, strict checks, command behavior.
- `src/nebius_cxcli/component_sources.py`: source registry loading + precedence resolution.
- `src/nebius_cxcli/components.py`: runtime component entry generation and dependency helpers.
- `src/nebius_cxcli/config_template.py`: starter `config.yaml` generation from runtime entries.
- `src/nebius_cxcli/config_model.py`: runtime/dynamic shape conversion.
- `src/nebius_cxcli/config_loader.py`: file loading + runtime validation normalization.
- `src/nebius_cxcli/runtime_validation.py`: core runtime validation.
- `src/nebius_cxcli/runtime_plugin_validation.py`: optional validation plugin loader.
- `src/nebius_cxcli/runtime_component_validation.py`: optional component rule plugin (not default-loaded).
- `src/nebius_cxcli/runtime_introspection.py`: module/chart introspection helpers.
- `src/nebius_cxcli/provider_options.py`: provider-backed field option lookup.
- `src/nebius_cxcli/provider_components.py`: provider schema discovery/matching helpers.
- `src/nebius_cxcli/infra_render.py`: Terraform render generation.
- `src/nebius_cxcli/terraform_backend.py`: Terraform remote-state backend derivation/rendering + bucket bootstrap.
- `src/nebius_cxcli/flux_render.py`: Flux render generation.
- `src/nebius_cxcli/render.py`: combined render orchestration.
- `src/nebius_cxcli/terraform_ops.py`: terraform command wrappers.
- `src/nebius_cxcli/flux_ops.py`: flux bootstrap/reconcile wrappers.
- `src/nebius_cxcli/discover_ops.py`: changed-config discovery (git and non-git modes).
- `src/nebius_cxcli/iam_bootstrap.py`: Nebius IAM bootstrap (identity + key material).
- `src/nebius_cxcli/github_secrets.py`: GitHub repo/environment secret sync helpers.
- `src/nebius_cxcli/paths.py`: instance path resolution and alignment checks.
- `src/nebius_cxcli/inventory_ops.py`: inventory write operations.
- `src/nebius_cxcli/notify_ops.py`: email notification operations.
- `component_sources.yaml`: repo-level starter source registry editable by operators.
- `<install-prefix>/nebius_cxcli/component_sources.yaml` (wheel data-file): bundled fallback source registry shipped inside wheel builds.
