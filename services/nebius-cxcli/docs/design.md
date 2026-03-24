# nebius-cxcli Design

## Table of Contents

- [1. Goal](#1-goal)
- [2. Architecture Summary](#2-architecture-summary)
- [3. Runtime Source Model](#3-runtime-source-model)
- [4. Config Model](#4-config-model)
- [5. Command Workflow](#5-command-workflow)
- [6. Generator-side Commands](#6-generator-side-commands)
- [7. Customer-side Commands](#7-customer-side-commands)
- [8. Supporting Commands](#8-supporting-commands)
- [9. Idempotency Rules](#9-idempotency-rules)
- [10. Validation Model](#10-validation-model)
- [11. Render Model](#11-render-model)
- [12. Auth and CI Bootstrap Model](#12-auth-and-ci-bootstrap-model)
- [13. Vendor Scope](#13-vendor-scope)
- [14. Source Code Structure](#14-source-code-structure)

## 1. Goal

`nebius-cxcli` provides a single, repeatable operator workflow:

1. Create/reconcile one instance configuration (`config.yaml`).
2. Validate generator-side configuration safety/readiness.
3. Render deterministic Terraform and Flux artifacts.
4. Commit the rendered customer artifact bundle.
5. Deploy from the generated bundle and/or bootstrap CI automation.

The design target is source-driven runtime behavior with minimal fixed component logic in core command paths.

## 2. Architecture Summary

Core principles:

- `config.yaml` is the canonical render/reset contract.
- `generated/` is the deploy contract for customer repositories.
- Generator-side commands operate on `config.yaml`.
- Customer-side commands operate on `generated/`.
- Source-driven component discovery from `component_sources.yaml`.
- Runtime introspection for module/chart fields and chart dependencies.
- Wizard field model: infra inputs come from Terraform module variables, with Nebius API-backed option discovery inferred by field/path patterns.
- Generic render path for Terraform modules/resources and Flux Helm releases.
- Optional plugin boundaries for provider-specific runtime option lookups and validation.

## 3. Runtime Source Model

Primary source registry (repo root): `component_sources.yaml`

- `component_sources.yaml` is the repo-local developer catalog for working against checked-out local module paths.
- `component_sources.release.yaml` is the portable/release catalog template with Git module sources.

Sections:

- `cli.flux.version`
- `cli.terraform.version`
- `infra.tf_modules[]`
  - `module`, `source`, `version`, `group`, `enable`, optional `defaults`, optional `outputs`, optional `input`, optional `handoff`
- `apps.helm_charts[]`
  - `name`, `repo`, `version`, `namespace`, `releasename` (or `release-name`), `group`, `enable`, optional `defaults`, optional `outputs`, optional `input`
  - `repo` can be HTTP/S Helm repo base (must expose `index.yaml`), OCI (`oci://...`), or GitHub tree URL for git-hosted charts

Source validation requirements (`validate-sources`):

- Terraform module sources (`infra.tf_modules[]`):
  - `module` token must match runtime component id format (lowercase letters/digits/hyphens).
  - Local `source` may be relative or absolute.
  - Relative local paths are resolved from the active `component_sources.yaml` file location first.
  - Local `source` must resolve to an existing directory with at least one `*.tf` file.
  - Every module source is install-checked with `terraform init -backend=false`, so broken remote refs and missing git/auth access fail before deploy-time commands start.
  - Missing `main.tf` or `variables.tf` is warning-level (not hard-fail), because wizard variable discovery relies on module variable definitions.
  - Supported Terraform module source formats are only:
    - relative local path
    - absolute local path
    - Git repo source address such as `git::https://github.com/org/repo.git//modules/mk8s?ref=v1.2.3`
  - Plain `http://` or `https://` module URLs are rejected. Users must provide the Terraform Git source form instead.
  - Registry-style and `oci://` Terraform module sources are rejected.
  - `outputs.tf_outputs: true` auto-exports every Terraform output exposed by the module under the same alias name.
  - `outputs.terraform` exports or renames specific Terraform outputs.
  - `outputs.config` exports values from the component config row.
  - `outputs.static` exports literal YAML values.
  - Every Terraform-backed exported alias must map to a real Terraform output exposed by the source module.
  - Custom modules wired with `handoff` must reference one of those declared Terraform-backed aliases if they are used with local `deploy` or CI kubeconfig bootstrap flows.
- Helm chart sources (`apps.helm_charts[]`):
  - HTTP repo mode: `repo` is a Helm repository base URL; `index.yaml` must be readable; chart name and configured version must be present in index entries.
  - OCI mode: `repo` is a direct chart URL (`oci://.../<chart-name>`); OCI basename must match chart name; configured version must be semver tag.
  - GitHub tree mode is supported for git-hosted charts: `https://github.com/<owner>/<repo>/tree/<ref>/<chart-path>`.
  - Helm chart sources are fail-fast validated with `helm show chart`; missing Helm, bad refs, unreachable repos, and chart/version mismatches are hard failures.

Accepted Terraform module source examples:

- relative local path: `../../platform-infra/modules/mk8s`
- absolute local path: `/Users/alice/repos/platform-infra/modules/mk8s`
- Git repo source: `git::https://github.com/org/repo.git//modules/mk8s?ref=v1.2.3`

Local Terraform module sources are rendered as resolved local filesystem paths. If you need a pinned remote ref, declare an explicit `git::...?...ref=...` source instead of combining a local path with `version`.

Accepted runtime handoff example:

```yaml
cli:
  flux:
    version: v2.8.0
  terraform:
    version: 1.14.1

infra:
  tf_modules:
    - module: mk8s
      source: ../../platform-infra/modules/mk8s
      outputs:
        tf_outputs: true
        static:
          access: external
      handoff:
        cluster_id: cluster_id
        access: access
```

This is an infra-to-runtime orchestration contract.
The Terraform module declares exported aliases under `outputs`, and `handoff.cluster_id` / `handoff.access` point to the aliases the CLI should use for cluster access after apply.
Generic exported values do not replace the handoff block, because deploy/bootstrap still need an explicit runtime contract for kubeconfig setup.
Helm chart definitions stay cluster-agnostic; `deploy`/`flux bootstrap`/CI consume the handoff once and then run Flux/kubectl against that cluster.
Flux controller installation version for local `deploy` is configured in the same catalog under `cli.flux.version`.
Managed Terraform CLI download version is configured in the same catalog under `cli.terraform.version`.

Flux namespace architecture:

- `flux-system` is the shared Flux control namespace in this project.
- Flux controllers run in `flux-system`.
- Flux source objects such as `HelmRepository`, `GitRepository`, `OCIRepository`, and `Bucket` typically live in `flux-system` as shared inputs for one or more workloads.
- Namespaced consumer objects such as `HelmRelease` live in the target workload namespace and can reference a source object in `flux-system`.
- The resulting workload pods and services are created in the workload namespace, not in `flux-system`.
- A workload namespace does not require its own dedicated source object unless it truly consumes a different chart or repository source.

Generic component wiring model:

```yaml
infra:
  tf_modules:
    - module: mk8s
      source: ../../platform-infra/modules/mk8s
      outputs:
        tf_outputs: true
        static:
          access: external

apps:
  helm_charts:
    - name: demo-app
      repo: https://example.invalid/charts
      version: 1.0.0
      namespace: demo
      releasename: demo-app
      input:
        values.global.clusterId: mk8s.cluster_id
        values.global.clusterAccess: mk8s.access
```

Contract rules:

- Source-defined values live under `defaults`.
- Terraform module defaults must target `inputs.*`.
- Helm chart defaults must target `values.*`.
- Shared-derived defaults use `shared.<path>`.
- Producers declare exports under `outputs`.
- Consumers declare target paths under `input`.
- `input` is reserved for component-output references; literal values and shared-derived values must use `defaults`.
- References use `<component-id>.<output-alias>`.
- Both producer and consumer must be declared in `component_sources.yaml`.
- Component ids must be globally unique across `infra` and `apps`.
- `outputs.static` resolves immediately from the source catalog entry.
- `outputs.config` resolves immediately from the source component config row.
- Literal `defaults` seed starter config during `create` and apply as runtime fallback when the target field is missing.
- Shared-derived `defaults` resolve from top-level catalog `shared` values at validation/render/deploy time and are not copied into `config.yaml`.
- Terraform-backed outputs render as native Terraform module references for infra consumers.
- Terraform-backed outputs for app consumers resolve from Terraform state during `deploy` and `flux bootstrap`.
- Plain `render` can resolve Terraform-backed app bindings only when prior Terraform state already exists; otherwise it fails fast.

Resolution precedence:

1. CLI `--component-sources-file`
2. current working directory `./component_sources.yaml`
3. env `NEBIUS_CXCLI_COMPONENT_SOURCES_FILE`
4. user file `~/.config/nebius-cxcli/component_sources.yaml`
5. global file `/etc/nebius-cxcli/component_sources.yaml`
6. repo default `component_sources.yaml` (when present)
7. bundled package default (`nebius_cxcli/component_sources.yaml` packaged inside the install)

Catalog-selection policy:

- Automatic catalog resolution is a convenience default for interactive use.
- `validate` and `render` default to the `portable` render profile, so the normal generator path produces deployable artifacts rather than workstation-local Terraform module paths.
- Installed-package fallback is portable by default: when no external catalog override is present, the packaged `nebius_cxcli/component_sources.yaml` uses Git Terraform module sources.
- `--render-profile local-dev` is the explicit escape hatch for workstation testing against checked-out Terraform modules.
- `--component-sources-file` and `NEBIUS_CXCLI_COMPONENT_SOURCES_FILE` remain catalog-selection overrides, not the primary portable-vs-local switch.
- The checked-in developer and portable catalogs should stay semantically aligned except for Terraform module `source` values; local-vs-portable is a transport difference, not a feature-contract difference.
- Customer-side commands that operate on `generated/` should not require the original render environment's local source catalog in order to resolve Terraform module paths.

Instance self-containment:

- `--component-sources-file` is a global optional override for the active source catalog path.
- When omitted, nebius-cxcli resolves the default file name `component_sources.yaml` from the standard search order above.
- `component_sources.yaml` is source input for `create` and for config-based runtime validation.
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
- `component_sources.yaml` can declare per-component `defaults` so known Terraform inputs and Helm values are pre-seeded without interactive prompting.
- `component_sources.yaml` can declare top-level `shared` values, and `defaults` entries can reference them with `shared.<path>` so shared values flow into Terraform module inputs or Helm chart values without duplicating them in component config blocks.
- `shared` is catalog-only; `config.yaml` must not declare a root `shared` block.
- The shipped public catalogs should contain only non-sensitive shared defaults. Per-instance SSH public keys for jump-host modules belong in the private instance `config.yaml`, not in `component_sources.yaml`.
- The bundled `mk8s` source entry sets `defaults.inputs.mk8s_cluster_public_endpoint: true` because its cluster handoff contract is `access: external`. That keeps local kubeconfig handoff aligned with the cluster's public-endpoint selection for external access.
- The bundled `mk8s` source entry also sets `defaults.inputs.kube_network_service_cidrs: ["/20"]`. Nebius defaults omitted MK8s service CIDRs to `["/16"]`; on a single-pool `/16` subnet that can consume the entire pool and stall control-plane provisioning. `validate --strict` and `deploy` now preflight that case against the live subnet before Terraform apply.
- `component_sources.yaml` can also declare producer-side `outputs` exports and consumer-side `input` bindings so component outputs feed other component inputs without adding hardcoded wiring to the CLI.
- Optional module inputs are prompted when already set, when they are toggle fields, or when dependency-enabled prefixes are active (for example `gpu_enabled=true` enables `gpu_*` prompts).
- If a selected module has no catalog default for a required field, `create` prompts for it and stores it in the per-instance `config.yaml`. That is the canonical path for sensitive per-instance values such as jump-host `ssh_public_key`.
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

- `infra.components[]`: `id`, `enabled`, `inputs`
- `apps.charts[]`: `id`, `group`, `enabled`, `repo`, `version`, `namespace`, `release-name`, `values`
- Static nested component blocks are not accepted.

Commands operate from this dynamic model with infra source metadata resolved from the active `component_sources.yaml`, not pinned in `config.yaml`. New starter configs omit `infra.components[].source` and `infra.components[].version`.

## 5. Command Workflow

The command boundary is intentional:

- Generator-side commands operate on `config.yaml`.
- Customer-side commands operate on `generated/`.
- Customer CI is artifact-driven and should deploy only from `generated/**`.

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

- Writes deterministic artifacts under `generated/infra`, `generated/flux`, and `generated/inventory`.
- Writes `generated/nebius-cxcli-manifest.json`, which snapshots the runtime config and deployment metadata needed to operate on the generated bundle later.
- Warns before overwriting an existing generated bundle, because rerendering is the reset path back to the original `config.yaml` contract.
- When Terraform is available from `PATH` or the managed download path, attempts backend-ready `terraform init` to produce/update `.terraform.lock.hcl`.
- Automatically performs create-if-missing runtime auth bootstrap for backend-ready lockfile init.

### `validate-generated <generated-dir>`

- Validates an existing generated artifact bundle without rerendering it.
- Runs Terraform validation against `generated/infra`.
- Runs `kubectl kustomize` against `generated/flux` when apps are enabled.
- Optional `--portable` enforcement rejects generated bundles whose Terraform root still embeds local filesystem module sources.

### `deploy <generated-dir>`

- Deploys an existing generated bundle: terraform apply, inventory refresh, then local Flux apply.
- Ensures remote-state backend bucket exists before Terraform init/apply.
- Does not rerender from `config.yaml`.
- Uses `generated/nebius-cxcli-manifest.json` to recover the runtime config snapshot and deployment metadata.

### `bootstrap-ci <config.yaml>`

- Generates `.github/workflows/nebius-deployments.yml`.
- Re-running it automatically reconciles that CLI-managed workflow file to the latest template for the target repo/deployments path.
- Generated customer workflow is artifact-driven: it watches and deploys only `generated/**`.
- `config.yaml` remains in the customer repo as a manual render/reset contract and does not trigger customer CI deployment.
- The target `config.yaml` must already live inside the customer git repository because the workflow is written at that repo root.
- With default `--auth-bootstrap`, the command resolves the target GitHub repo from the checkout `origin` remote. `--github-repo` is only an explicit override for missing, non-GitHub, or remapped remotes.
- `--github-token-env` affects only auth bootstrap and environment secret sync. It is the escape hatch when the GitHub API token is not exposed as `GH_TOKEN`/`GITHUB_TOKEN`.
- When `--cli-ref` is omitted, the generated workflow defaults to `main` for development builds and `nebius-cxcli-v<version>` for stable tagged releases.
- `--cli-ref` is the explicit escape hatch when generator-side automation must pin the generated customer workflow to a specific nebius-cxcli branch, tag, or commit for PR validation.
- `--cli-ref` selects the `nebius-cxcli` source ref to install from `nebius-ps-services`; it does not select or mutate the branch of the customer target repo.
- Example: `nebius-cxcli bootstrap-ci /path/to/config.yaml --cli-ref <branch|tag|sha>`.
- Generated workflows also support a GitHub repo/org variable override `NEBIUS_CXCLI_REF`, which takes precedence over the generated default ref.
- The intended ref controls are generator-time pinning via `--cli-ref` and optional runtime override via the GitHub variable; editing the generated workflow YAML is not required for normal use.
- Optional CI auth/environment-secret bootstrap creates the GitHub Environment and syncs Environment Secrets, but does not manage GitHub repo/org variables.

### `auth` (flag-driven)

- `auth --create` creates runtime auth cache/profile only when missing.
- `auth --recreate` always rotates runtime auth material and rewrites cache.
- `auth --validate-profile` inspects cached runtime auth profile metadata/private key and verifies Nebius auth public key visibility.
- `auth --bootstrap-ci` syncs local runtime auth cache material into GitHub environment secrets.
- `auth --profile` and `auth --sdk-config-file` target Nebius SDK config resolution; they do not require the standalone `nebius` CLI binary.

## 6. Generator-side Commands

- `validate-sources`
  - Validates the active source catalog contract and backing Terraform/Helm sources.
- `validate <config.yaml>`
  - Validates the instance contract before rendering.
  - Defaults to `--render-profile portable`; `--render-profile local-dev` is available for local checked-out module workflows.
- `validate --strict <config.yaml>`
  - Adds deployment-readiness checks before rendering.
  - Uses the same `--render-profile {portable|local-dev}` contract.
- `render <config.yaml>`
  - Produces the canonical generated Terraform/Flux/inventory bundle.
  - Recreates the managed `generated/` bundle from a clean layout without stale files, while preserving bootstrap-owned `generated/flux/flux-system`.
  - Defaults to `--render-profile portable`; `--render-profile local-dev` is explicit and produces non-portable generated Terraform sources for local testing.
  - If the target `generated/` bundle already exists, rerender is treated as a reset:
    - interactive terminal: prompt before overwrite
    - non-interactive context: require `--force`

## 7. Customer-side Commands

- `validate-generated <generated-dir>`
  - Validates an already-rendered bundle without rerendering it.
  - CI and publish workflows should call `validate-generated --portable` before plan/apply.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation for Terraform validation (default enabled).
- `deploy <generated-dir>`
  - Full local deployment from the generated bundle: Terraform first, then inventory refresh for infra and apps artifacts, then Flux direct apply.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default enabled).
  - Does not run `bootstrap-ci` automatically, even when the generated bundle is inside a git repository; GitHub workflow/environment bootstrap stays an explicit generator-side action.
- `terraform apply <generated-dir>`
  - Infra-only apply from the generated Terraform bundle.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default enabled).
- `flux apply <generated-dir>`
  - Apps-only direct apply from the generated Flux bundle.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default enabled).
- `flux bootstrap <generated-dir>`
  - GitOps bootstrap/reconcile path from the generated Flux bundle.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default disabled).

## 8. Supporting Commands

- `create <target_path>`
  - Scaffolds or reconciles the instance `config.yaml` and generated skeleton.
- `bootstrap-ci <config.yaml>`
  - Generates or reconciles the customer workflow. The generated workflow watches and deploys only `generated/**`.
- `discover <target_path>`
  - Returns deployment-instance discovery payload for CI.
- `terraform plan <generated-dir>`
  - Infra-only plan from the generated Terraform bundle.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default enabled).
- `terraform unlock <generated-dir>`
  - Clears a stale remote Terraform state lock for a generated infra bundle.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default enabled).
  - `--force` overrides local safety checks and force-unlocks even when the lock owner is different or local processes are still active.
- `inventory write <generated-dir>`
  - Refreshes local inventory files from the generated bundle.
- `email <generated-dir>`
  - Sends inventory markdown via SMTP.
- `auth`
  - Manages runtime auth profiles and optional GitHub environment secret sync.

## 9. Idempotency Rules

- `create`: idempotent reconcile by default; `--force` is explicit reset.
- `validate`/`render`: deterministic and repeatable.
- `validate-generated`: deterministic for a given generated bundle.
- `deploy`: convergent behavior expected from apply/reconcile against a fixed generated bundle.
- `bootstrap-ci`: idempotent reconcile; reruns auto-update the CLI-managed customer workflow and re-check GitHub environment secret presence.
- `auth --create`: idempotent create-if-missing.
- `auth --recreate`: explicit rotation path.
- `auth --validate-profile`: read-only profile validation; safe to re-run.
- `auth --bootstrap-ci`: idempotent environment-secret upsert from local cache.
- `deploy` and other customer-side generated-bundle commands do not mutate GitHub CI state as a side effect.

## 10. Validation Model

Validation layers:

1. Structural/runtime config checks (`runtime_validation.py`).
2. Dynamic payload shape checks (`validate_dynamic_payload_structure`).
3. Strict checks in CLI for deployment readiness.
4. Optional plugin validation via `NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS`.

Plugin default:

- Default runtime validation plugins are disabled.
- Operators can enable custom/provider-specific rule packs explicitly.

## 11. Render Model

Infra render:

- Root-module Terraform layout with separated concerns:
  - `backend.tf`: authoritative remote-state backend config (root-owned; non-secret).
  - `versions.tf`: authoritative Terraform and provider constraints for generated root module.
  - `providers.tf`: provider configuration (child modules do not define provider blocks).
  - `variables.tf`: generated variable declarations for module arguments.
  - `main.tf`: module/resource orchestration only.
  - `outputs.tf`: generated root outputs required by higher-level CLI orchestration and declared component-output exports.
  - `terraform.auto.tfvars.json`: concrete values for generated variables.
- Generic module blocks from enabled infra module entries.
- Generic provider resource blocks from dynamic provider components.
- `render --render-profile portable` is the default and rewrites active local developer sources to the canonical portable catalog when a matching portable source exists.
- `render --render-profile local-dev` preserves resolved filesystem module paths for workstation testing and is intentionally non-portable.
- `component_sources.yaml` is the working-tree developer default for checked-out local Terraform module paths.
- `component_sources.release.yaml` is the portable/release override for CI or cross-machine generation.
- Build/package steps derive the bundled `nebius_cxcli/component_sources.yaml` fallback from `component_sources.release.yaml`, and release workflows rewrite its Terraform Git module refs from `?ref=main` to the current tag or commit before publishing it.
- Generator-side commands should use the render profile to choose portable vs local-dev output, and use `--component-sources-file` only when they need to override which catalog file is active.
- Deterministic output files:
  - `generated/nebius-cxcli-manifest.json`
  - `generated/infra/backend.tf`
  - `generated/infra/versions.tf`
  - `generated/infra/providers.tf`
  - `generated/infra/variables.tf`
  - `generated/infra/main.tf`
  - `generated/infra/outputs.tf`
  - `generated/infra/terraform.auto.tfvars.json`
  - `generated/infra/.terraform.lock.hcl` (generated by backend-enabled `terraform init` during CLI `render` when Terraform is available)
- Remote-state backend is distinct from app/object-storage components:
  - Bucket/key/endpoint settings are derived from `client_info` (`client_name`, `project_id`, `region_id`).
  - `infra.components[id=object-storage]` remains workload/application storage only.
- Before any Terraform init path (`render` lockfile init, `terraform plan`, `terraform apply`, `deploy`), CLI ensures the backend bucket exists via Nebius Storage API.
- Backend lock recovery is explicit: `terraform unlock <generated-dir>` inspects the remote `.tflock` object for the rendered backend and then uses Terraform `force-unlock` only when the lock appears stale. By default it refuses to unlock while local Terraform/deploy operations are still active or when the recorded lock owner differs from the current local identity.
- `terraform unlock` still requires `aws` CLI in `PATH`; Terraform itself may come from `PATH` or the managed Terraform download path.
- Local `deploy` validates the rendered Terraform root before apply, then if enabled charts and a `handoff`-enabled infra component are present it resolves the rendered cluster ID output and prepares kubeconfig before applying rendered Flux manifests.
- Customer-side commands operate on the rendered `generated/` bundle as the deploy contract and do not need the source catalog to recover local Terraform module paths from the original render machine.
- On non-CI local runs, that same cluster handoff also updates the user kubeconfig at `~/.kube/config` with a `nebius-cxcli` exec-based credential entry, so the target MK8s cluster is immediately usable with `kubectl` after `deploy`, `flux apply`, or `flux bootstrap` without a separate Nebius CLI install.
- Before `deploy`, `flux apply`, or `flux bootstrap` starts Flux work against a handed-off MK8s cluster, the CLI performs a fast node-readiness probe first and only enters a wait loop when the nodes are not `Ready` yet. Healthy existing clusters proceed to Flux work immediately.
- After that handoff, the local Flux phase keeps one continuous spinner alive and updates its message across cluster reachability, Flux API discovery, rendered-manifest apply, and the final rendered-resource readiness wait so the command remains visibly active during quiet kubectl/Flux setup work.
- In non-interactive environments, those same phase updates degrade to ordinary printed lines rather than transient spinner frames, so CI logs stay readable without requiring terminal animation support.
- `terraform plan` and `terraform apply` operate on the existing generated infra bundle rather than rerendering from `config.yaml`.
- `terraform apply` is a sequentially idempotent infra-only path for a given `generated/infra` bundle. Repeated runs converge through Terraform state; concurrent runs against the same backend are intentionally blocked by remote state locking.
- During long-running `terraform apply`, local `deploy` and `terraform apply` emit one merged status surface: Terraform apply transitions plus a light Nebius MK8s API snapshot. When an enabled `mk8s` component is present and Nebius SDK auth is available, the CLI polls Nebius MK8s API for cluster/node-group state; otherwise it falls back to an elapsed heartbeat for the API side.
- The merged status surface is formatted as a multi-line terminal block with separate TF and API sections so provider progress and Nebius API state are easy to distinguish during long creates.
- If Terraform apply fails, the CLI raises the Terraform failure as the canonical error and appends the last known merged Terraform/API status snapshot for context.
- If Terraform fails before it acquires the S3 backend lock, the CLI reports that as a backend lock failure, states that the run created nothing, and surfaces the lock owner/creation metadata Terraform returned. This avoids confusing a stale `.tflock` object with a cluster provisioning failure.
- If MK8s node-group status exposes `ERROR` events, the merged status block includes those alerts so likely quota/provisioning problems surface before Terraform exits. Known transient bootstrap warnings are downgraded to notes while the node group remains in provisioning.
- Generated Flux artifacts are treated as deploy truth. If enabled app charts bind values from Terraform-backed component outputs, operators must rerender after the required Terraform state exists before treating `generated/flux` as the final GitOps payload.
- If Flux controllers are missing, local `deploy` installs the core Flux controllers into the target cluster from the official Flux install manifest before applying rendered resources. This removes the `flux` CLI dependency from local `deploy`.
- The Flux install manifest version used by local `deploy` comes from `component_sources.yaml` `cli.flux.version`.
- After `kubectl apply -k generated/flux`, local `deploy` waits for the rendered Flux `source.toolkit` and `helm.toolkit` resources to become `Ready`, so a chart fetch/install failure does not get masked as a successful local deploy.
- During that Flux wait, local `deploy` and `flux apply` poll the rendered Flux resources from the cluster with `kubectl get -o json` and print a generic status block for the rendered `HelmRepository`, `GitRepository`, `HelmRelease`, and `Kustomization` objects. The status surface is resource-driven, not chart-specific.
- If all rendered workload resources are already `Ready` and only rendered Flux source objects remain pending without any `Ready` condition, the CLI stops waiting and completes with a note. That guardrail avoids false hangs on source-controller status gaps after a successful local apply.
- `deploy` and `flux apply` intentionally stay local direct-apply commands. They do not auto-bootstrap GitOps, because GitOps bootstrap has extra GitHub/Flux side effects. If the cluster is not bootstrapped yet, they now finish the local apply and print a warning with the exact `nebius-cxcli flux bootstrap <generated-dir>` follow-up command.
- `flux apply` reuses that same local app-deploy path without Terraform apply, which makes it the apps-only command for day-2 chart deployments after infra is already present.
- `flux apply` is also sequentially idempotent for a given `generated/flux` bundle: it applies the current rendered manifests, skips Flux controller installation when the controllers already exist, and waits for the rendered Flux resources to report `Ready`.
- `flux bootstrap` auto-downloads a managed Flux CLI binary from the official Flux GitHub release for the catalog-pinned `cli.flux.version` when `flux` is not already available in `PATH`. The binary is cached under the local nebius-cxcli cache and is not installed system-wide.
- `flux bootstrap` resolves the GitHub repo slug from `GITHUB_REPOSITORY` when present, otherwise it falls back to the local git `origin` remote.
- `flux bootstrap` uses the same handoff contract rather than hardcoding a specific Terraform output name in CI workflow logic.
- `flux bootstrap` only switches to reconcile mode when the cluster already contains both the core Flux controller deployments and the bootstrap Git objects `GitRepository/flux-system` plus `Kustomization/flux-system`. A cluster that only has Flux controllers from local `deploy`/`flux apply` is not treated as Git-bootstrapped yet.
- `flux bootstrap` is intentionally the GitOps path, not the direct-apply path. It assumes the rendered manifests are committed and pushed to the watched Git repository/path. `flux apply` is the local direct-apply path for immediate day-2 deployment before Git reconciliation is in place.
- Local kubeconfig persistence is skipped automatically in CI and can be disabled explicitly with `NEBIUS_CXCLI_PERSIST_LOCAL_KUBECONFIG=false`.
- `flux bootstrap` still depends on GitHub release availability when the managed Flux CLI download path is used.

Managed vs external local tooling:

- Auto-managed by the CLI when missing:
  - `terraform` for Terraform-backed validation, render lockfile generation, `terraform plan`, `terraform apply`, `terraform unlock`, and backend-backed Terraform output reads
  - `flux` for `flux bootstrap`
- Still external prerequisites:
  - `kubectl` for `deploy`, `flux apply`, `flux bootstrap`, and Flux readiness probes
  - `nebius` CLI for kubeconfig handoff against handoff-enabled cluster components
  - `helm` for strict Helm source validation
  - `aws` CLI for `terraform unlock` remote lock inspection

Flux render:

- Generic Helm source docs (`HelmRepository` HTTP/OCI or `GitRepository` for standalone chart sources).
- Inventory artifacts are part of the canonical generated output set as well:
  - `generated/inventory/inventory.md`
  - `generated/inventory/infra.json`
  - `generated/inventory/apps.json`
  - `generated/inventory/mk8s.json` only when MK8s is enabled
  - `generated/inventory/postgresql.json` only when Managed PostgreSQL is enabled
  - `generated/inventory/sfs.json` only when SFS is enabled
- `render`, `deploy`, `terraform apply`, `flux apply`, `flux bootstrap`, and `inventory write` refresh those inventory artifacts for the active instance.
- Those refreshes also delete stale disabled component inventory files.
- Explicit Namespace docs for chart target namespaces.
- Generic HelmRelease docs from enabled app releases.
- Deterministic flat output under `generated/flux`:
  - `helm-repositories.yaml`
  - `namespace-<namespace>.yaml`
  - `helmrelease-<group>-<release>.yaml`
  - `kustomization.yaml`
- Legacy nested Flux layout (`generated/flux/apps` and `generated/flux/sources`) is not supported.

## 12. Auth and CI Bootstrap Model

`bootstrap-ci`:

- Generates workflow file.
- Treats `.github/workflows/nebius-deployments.yml` as a CLI-managed file and automatically reconciles it to the latest generated contract on every rerun.
- Requires the target config path to be inside the customer git repository so the workflow can be written at the repo root.
- With auth bootstrap enabled, auto-detects the target GitHub repo from the checkout `origin` remote unless `--github-repo` overrides it.
- Fails before writing the workflow if full GitHub bootstrap prerequisites are missing.
- Derives GitHub environment name as `<client_name>-<project_id>`, ensures that environment exists, then checks/syncs missing environment secrets.
- Generated customer workflows validate with `nebius-cxcli validate-generated --portable` before Terraform plan/apply so non-portable local module paths are rejected in PRs and main-branch deploy runs.
- Generated customer workflows restore ignored `generated/infra/terraform.auto.tfvars.json` from `generated/nebius-cxcli-manifest.json` before Terraform plan/apply.
- Generated customer workflows do not install the standalone `nebius` CLI; MK8s kubeconfig handoff and token retrieval stay inside `nebius-cxcli` via the Nebius SDK.
- Generated customer workflows also keep the Python runtime version in one env var and write compact single-line discovery JSON to `GITHUB_OUTPUT` for stable matrix handoff.
- Does not manage GitHub repo/org variables; `NEBIUS_CXCLI_REF` remains an optional manual override consumed by the generated workflow.
- `generated/infra/terraform.auto.tfvars.json` remains ignored in private deployment repos; customer-side generated-bundle commands recreate it from `generated/nebius-cxcli-manifest.json` before Terraform plan/apply so CI does not depend on a committed tfvars file.

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

## 13. Vendor Scope

Current runtime implementation is Nebius-focused:

- Nebius SDK/API integration for auth/IAM and provider option lookups.
- Nebius-oriented defaults for provider/config behavior.

The component source model itself is Terraform-module + Helm-chart based, but this release does not claim full multi-vendor runtime support.

## 14. Runtime Versioning

- Installed wheels rely on package metadata for the published version.
- Source/editable checkouts prefer live `setuptools-scm` git state over a generated `_version.py` cache so local runtime behavior matches the current repo state, including dirty/dev suffixes.
- `publish-release.sh --publish X.Y.Z` creates the service tag locally, verifies that the tagged source checkout resolves `nebius_cxcli.__version__ == X.Y.Z`, and only then pushes the tag to trigger the release workflow.

## 15. Source Code Structure

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
- The `nebius-cxcli` GitHub release workflow publishes both the wheel and the raw portable catalog file so operators can download the editable source catalog directly from the release page with module refs already pinned to the published release tag.
- The repo CI and release workflows run the same local `make all` verification contract before wheel verification or release publication so GitHub Actions and local development stay on one lint/test/build path.

Primary automated test ownership:

- `tests/test_cli.py` and `tests/test_cli_command_coverage.py`: CLI command contract and workflow-generation behavior.
- `tests/test_component_sources.py`: component source precedence and validation rules, including `validate-sources` registry checks.
- `tests/test_github_secrets.py`: GitHub repo/environment secret helper behavior, including environment creation and environment-secret upsert orchestration.
