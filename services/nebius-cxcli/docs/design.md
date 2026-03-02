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
- Generic render path for Terraform modules/resources and Flux Helm releases.
- Optional plugin boundaries for provider-specific runtime option lookups and validation.

## 3. Runtime Source Model

Primary source registry (repo root): `component_sources.yaml`

Sections:

- `infra.tf_modules[]`
  - `module`, `source`, `version`, `group`, `enable`
- `apps.helm_charts[]`
  - `name`, `repo`, `version`, `namespace`, `releasename`, `group`, `enable`

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
- `create` embeds only enabled component sources under `config.yaml.component_sources`.
- Config-based commands resolve sources from embedded `config.yaml.component_sources`.
- App chart defaults (`namespace`, `releasename`) can be edited in wizard mode or overridden in non-interactive mode with `--app-namespace` and `--app-releasename`.

## 4. Config Model

Runtime config root keys:

- `version`
- `client_info`
- `infra`
- `apps`
- `component_sources` (embedded snapshot)

Canonical model is dynamic:

- `infra.components[]`: `id`, `enabled`, `source`, `version`, `inputs`
- `apps.releases[]`: `id`, `section`, `enabled`, `values`
- Static nested component blocks are not accepted.

Commands operate from this dynamic model and keep source metadata self-contained per instance via `component_sources`.

## 5. Command Workflow

### `create <target_path>`

- Creates or reconciles one instance path and `config.yaml`.
- Wizard-first for identity and component prompts (unless `--no-interactive`).
- Uses source-driven infra/app entries.
- Resolves app dependencies from live Helm chart metadata (`Chart.yaml`) when available.

### `validate <config.yaml>`

- Core runtime/structural checks.

### `validate --strict <config.yaml>`

- Adds deployment-readiness checks:
  - placeholder rejection
  - chart source/dependency checks
  - module source and required-variable checks
  - provider-schema/resource checks when available

### `render <config.yaml>`

- Writes deterministic artifacts under `generated/infra` and `generated/flux`.

### `deploy <config.yaml>`

- Runs strict validate + render + terraform apply + flux apply.

### `bootstrap-ci <config.yaml>`

- Generates `.github/workflows/nebius-deployments.yml`.
- Optional CI auth/secret bootstrap.

### `auth bootstrap`

- Idempotently ensures CI service-account identity and role grants.
- Sync mode checks GitHub secrets and only creates keys when needed (or explicitly requested).

## 6. Idempotency Rules

- `create`: idempotent reconcile by default; `--force` is explicit reset.
- `validate`/`render`: deterministic and repeatable.
- `deploy`: convergent behavior expected from strict+render+apply sequence.
- `bootstrap-ci`: idempotent workflow file handling; `--force` only for overwrite.
- `auth bootstrap`: identity-first idempotent behavior; key creation is conditional/explicit.

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

- Generic Terraform provider block.
- Generic module blocks from enabled infra module entries.
- Generic provider resource blocks from dynamic provider components.
- Deterministic output files:
  - `generated/infra/terraform.tf`
  - `generated/infra/main.tf`
  - `generated/infra/terraform.auto.tfvars.json`

Flux render:

- Generic Helm source docs (`HelmRepository` or `GitRepository` for standalone chart sources).
- Generic HelmRelease docs from enabled app releases.
- Deterministic output under `generated/flux` + `kustomization.yaml`.

## 9. Auth and CI Bootstrap Model

`bootstrap-ci`:

- Generates workflow file.
- With auth bootstrap enabled, checks GitHub secret presence and only provisions/syncs missing secret material.

`auth bootstrap`:

- Ensures service account + role grants idempotently.
- Default sync mode:
  - no-op when required secrets already exist,
  - syncs only missing token when applicable,
  - creates fresh keys only when NEBIUS secret set is missing.
- Local mode (`--no-github-sync`):
  - identity-only by default,
  - `--create-keys` for explicit key provisioning.

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
- `src/nebius_cxcli/flux_render.py`: Flux render generation.
- `src/nebius_cxcli/render.py`: combined render orchestration.
- `src/nebius_cxcli/terraform_ops.py`: terraform command wrappers.
- `src/nebius_cxcli/flux_ops.py`: flux bootstrap/reconcile wrappers.
- `src/nebius_cxcli/discover_ops.py`: changed-config discovery (git and non-git modes).
- `src/nebius_cxcli/iam_bootstrap.py`: Nebius IAM bootstrap (identity + key material).
- `src/nebius_cxcli/github_secrets.py`: GitHub secret sync helpers.
- `src/nebius_cxcli/paths.py`: instance path resolution and alignment checks.
- `src/nebius_cxcli/inventory_ops.py`: inventory write/upload operations.
- `src/nebius_cxcli/notify_ops.py`: email notification operations.
- `component_sources.yaml`: repo-level starter source registry editable by operators.
- `<install-prefix>/nebius_cxcli/component_sources.yaml` (wheel data-file): bundled fallback source registry shipped inside wheel builds.
