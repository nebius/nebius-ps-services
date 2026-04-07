# nebius-cxcli

`nebius-cxcli` is the Nebius customer experience CLI and an end-to-end automation workflow generator. From one per-project `config.yaml`, it renders a deployable customer artifact bundle: Terraform, Flux, inventory, and CI workflow artifacts.

After render, deployment should operate on the generated bundle. `config.yaml` remains the original render/reset contract, not the day-2 deployment surface.

The current implementation is provider-driven and source-configured for Nebius environments:

- Infra components come from Terraform module sources.
- App components come from Helm chart sources.
- Runtime options and dependency checks use live provider/chart metadata where available.
- Canonical project config model is dynamic: `infra.components[]` and `apps.charts[]`.

Architecture rationale:

- `config.yaml` is the operator-facing orchestration contract, while Terraform modules and Helm charts are the provisioning contracts.
- Terraform modules are used for infra because they provide desired-state planning, apply/destroy behavior, state/locking, reusable variable/output interfaces, and portable generated artifacts; the Nebius SDK is used for dynamic discovery, validation, status polling, and guard rails rather than replacing Terraform as the reconciler.
- Helm charts are used for apps because they preserve the native app deployment contract and keep workloads cluster-agnostic while Flux/Helm remain the runtime owners of app reconciliation.
- Component UX follows a progressive-enhancement model: generic Terraform modules and Helm charts should work with zero extra mapping, convention-friendly fields get better dynamic UX automatically, and optional `wizard_fields` metadata is reserved for advanced integration or ambiguous cases.
- Terraform output aliases and `handoff` aliases are treated as a stable interface once the CLI, generated manifests, deploy/bootstrap flows, or app bindings consume them. Renaming or changing one is a breaking contract change, not an internal refactor.

## Table of Contents

- [Features](#features)
- [Runtime Metadata](#runtime-metadata)
- [Recommended Workflow](#recommended-workflow)
- [Releases](#releases)
- [Commands](#commands)
  - [Generator-side Commands](#generator-side-commands)
  - [Customer-side Commands](#customer-side-commands)
  - [Supporting Commands](#supporting-commands)
- [Auth Workflow](#auth-workflow)
- [Examples](#examples)
- [Development](#development)
- [Security Notes](#security-notes)

## Features

- `config.yaml` is the canonical render/reset contract per project.
- `generated/` is the deploy contract for customer repositories.
- Source-driven component model from `component_sources.yaml`.
- `create` scaffolds or reconciles project config idempotently.
- Re-running `create` against an existing project stays in reconcile/update mode by default; interactive runs now warn and ask for confirmation before continuing, while `--force` remains the explicit reset path.
- Interactive `create` now also prints an early notice when the deployments root already contains project configs, then asks whether to continue before it starts prompting for project identity.
- When interactive `create` finds exactly one existing project config in the deployments root and no explicit identity flags were passed, it offers that config's `client_name`, `tenant_id`, and `project_id` as prompt defaults.
- `create` writes dynamic component state (`infra.components[]`, `apps.charts[]`).
- `create` keeps only selected components/charts in `config.yaml` (unselected entries are omitted).
- `component list`, `component add`, and `component remove` are the day-2 config-editing surface for both infra modules and app charts in existing projects.
- `component_sources.yaml` defines reusable component types; `config.yaml` stores enabled component instances with unique `instance_id` values, so the same type can be added more than once.
- `component add` preserves existing values, resolves app chart dependencies, and only prompts for newly added component instance fields.
- Live Helm chart default values are treated as prompt-time defaults only. `config.yaml` stores explicit chart overrides, not the chart's full default values tree.
- `component add` validates `component_sources.yaml` by default and supports `--no-validate-sources` when you explicitly want to skip that preflight.
- `component add` also validates the existing `tenant_id`/`project_id` scope before provider-backed wizard fields run, so Nebius-backed dynamic options fail clearly instead of silently degrading.
- `component remove` blocks changes that would leave unresolved component bindings or dependency breakage in `config.yaml`.
- `create` validates `component_sources.yaml` by default (`--no-validate-sources` to skip).
- `create` also runs the non-strict `validate` pass against the resulting `config.yaml` by default (`--no-validate-config` to skip).
- `create`, `render`, and `bootstrap-ci` auto-manage a deployments-root `.gitignore` block when target path is inside a git repo (keeps `config.yaml` and deployable generated artifacts versioned while ignoring Terraform transient/runtime files and generated tfvars).
- App dependency resolution from Helm `Chart.yaml` metadata.
- Interactive wizard supports `q` to stop optional phases/field prompting.
- `create` and `component add` still save the edited `config.yaml` when the wizard is stopped with `q`; they warn only when required fields are still missing and stay quiet when only optional fields remain at defaults.
- `create` validates `tenant_id`/`project_id` against Nebius IAM APIs before continuing.
- Infra field options are resolved dynamically from Nebius APIs where supported.
- Flux output is flat under `generated/flux` (no `apps/` or `sources/` subdirectories).
- `validate` runtime checks, plus `validate --strict` deployment-readiness checks.
- `render` writes deterministic Terraform, Flux, inventory, and `generated/nebius-cxcli-manifest.json`.
- `render` now stages a full replacement bundle under a hidden sibling directory and swaps it into `generated/` only after the new bundle is complete.
- Rerender still recreates the managed generated bundle from a clean layout and removes stale or legacy content under `generated/`, including an old `generated/flux/flux-system` subtree.
- `render` warns before overwriting existing generated artifacts, so rerendering is still an explicit replace action driven from the original `config.yaml` contract.
- Generated-bundle CLI commands recreate ignored `generated/infra/terraform.auto.tfvars.json` from the committed manifest before Terraform runs, so deployable repos and generated workflows do not need to version that sensitive duplicate file.
- `deploy`, `destroy`, `terraform plan/apply/destroy/unlock`, `flux apply/bootstrap/destroy`, `inventory write`, and `email` all operate on an existing generated bundle instead of reading `config.yaml`.
- `terraform apply`, `terraform destroy`, `flux apply`, `flux destroy`, `deploy`, and `destroy` are designed for sequential reruns against the same generated bundle; destroy commands remain explicitly destructive and require confirmation or `--yes`.
- `bootstrap-ci` generates or reconciles the customer CI workflow, always reconciles GitHub email settings from local `email --setup`, and optionally bootstraps/syncs Nebius CI auth secrets.
- `discover` outputs deployment-project discovery JSON with `config`, `generated`, `config_changed`, `generated_changed`, and `github_environment`.

## Runtime Metadata

Primary source file (repo root):

- `component_sources.yaml` is the single source of truth for component and managed-tool metadata.

Schema:

- `cli.flux.version`: Flux controller install version used by local `deploy` when controllers are missing and by managed `flux bootstrap` CLI download
- `cli.terraform.version`: Terraform CLI version used by the managed Terraform download path
- `infra.tf_modules[]`: `module`, required `portable_source`, optional `local_source`, `version`, `group`, `enable`, optional `wizard_fields`, optional `defaults`, optional `outputs`, optional `input`, optional `handoff`
- `apps.helm_charts[]`: `name`, `repo`, `version`, `namespace`, `releasename`, `group`, `enable`, optional `wizard_fields`, optional `defaults`, optional `outputs`, optional `input`

`wizard_fields` is optional catalog metadata for advanced wizard behavior. Most modules should rely on Terraform variable or Helm values introspection alone; use `wizard_fields` only when you need to disambiguate provider-backed choices or add an explicit override beyond naming conventions.

Source profile selection:

- `portable` is the default. It always uses `portable_source`.
- `local` prefers `local_source` and falls back to `portable_source` when `local_source` is unset.
- Choose the active profile with the global CLI flag `--source-profile {portable|local}`. When omitted, the CLI defaults to `portable`.
- Or set `NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE={portable|local}`.
- For schema/output introspection, nebius-cxcli prefers a resolvable `local_source` when one exists, even while the active profile is `portable`. This keeps workstation/CI validation fast without changing the emitted portable module source addresses.

`apps.helm_charts[].repo` supports:

- HTTP/S Helm repositories (must serve `index.yaml`)
- OCI chart repositories (`oci://...`)
- GitHub tree URLs for charts stored in git (`https://github.com/<owner>/<repo>/tree/<ref>/<chart-path>`)

Source requirements enforced by `validate-sources`:

- Terraform modules (`infra.tf_modules[]`):
  - `module` must be lowercase letters/digits/hyphens.
  - `portable_source` is required.
  - `local_source` is optional.
  - `validate-sources` validates the module source resolved by the active source profile.
  - `local_source` may use a relative path (`./...`, `../...`, `../../...`) or an absolute filesystem path.
  - Relative local paths are resolved from the active `component_sources.yaml` file location first.
  - The active local filesystem source must resolve to an existing directory.
  - Directory must contain at least one `*.tf` file.
  - Every module source is install-checked with `terraform init -backend=false` during validation, so broken remote refs, missing auth, and nested module source failures fail fast.
  - `validate-sources` also runs fast CLI-contract checks against the resolved module directory:
    - fails when `versions.tf` is missing `required_version` or `required_providers`
    - fails when child modules configure backend or provider blocks
    - warns when canonical files such as `main.tf`, `variables.tf`, `outputs.tf`, `README.md`, or runnable `examples/` roots are missing
  - Supported Terraform module source formats are only:
    - relative local path
    - absolute local path
    - Git repo source address such as `git::https://github.com/org/repo.git//modules/mk8s?ref=v1.2.3`
  - Local Terraform module sources are rendered as resolved local filesystem paths. If you need a portable or pinned remote ref, declare it in `portable_source`.
  - Plain `http://` or `https://` module URLs are rejected. Use the Terraform Git source format instead.
  - Registry-style and `oci://` Terraform module sources are rejected.
  - `outputs.tf_outputs: true` auto-exports every Terraform output exposed by the module under the same alias name.
  - `outputs.terraform` exports or renames specific Terraform outputs (`<alias>: <terraform-output-name>`).
  - `outputs.config` exports values from the component config row (`<alias>: <component-path>`).
  - `outputs.static` exports literal YAML values (`<alias>: <literal-value>`).
  - Every exported Terraform-backed alias must map to a real Terraform output exposed by the source module.
  - If you provide a custom module behind a `handoff`-enabled component and plan to use `deploy` or CI kubeconfig bootstrap, that handoff must reference one of those declared Terraform-backed aliases.
- Helm charts (`apps.helm_charts[]`):
  - HTTP repo format: `repo` must be a Helm repo base URL, `repo/index.yaml` must be readable, chart must exist in `entries`, and configured version must exist.
  - OCI format: `repo` must be direct OCI chart ref (`oci://.../<chart-name>`), basename must match chart `name`, and configured version must be a semantic version tag.
  - GitHub tree format: `repo` may point at a chart directory in git (`https://github.com/<owner>/<repo>/tree/<ref>/<chart-path>`). Helm validates the chart from that path directly.
  - Helm chart sources are fail-fast validated with `helm show chart`; missing Helm, unreachable repos, bad refs, missing charts, and version mismatches are hard failures.
  - `validate-sources` also materializes the resolved chart and checks the CLI-facing chart contract:
    - fails when `Chart.yaml`, `values.yaml`, or `templates/` are missing
    - fails when `Chart.yaml` is missing `apiVersion`, `name`, or `version`
    - warns when the chart is not on canonical Helm v2 metadata or when `README.md` is missing

Resolution precedence:

1. `--component-sources-file`
2. current working directory `./component_sources.yaml`
3. `NEBIUS_CXCLI_COMPONENT_SOURCES_FILE`
4. `~/.config/nebius-cxcli/component_sources.yaml`
5. `/etc/nebius-cxcli/component_sources.yaml`
6. repo default `component_sources.yaml` (when present)
7. bundled package default (`nebius_cxcli/component_sources.yaml` packaged inside the install)

`--component-sources-file` is a global optional override for the active source catalog path.  
When omitted, nebius-cxcli resolves the default file name `component_sources.yaml` from the standard search order above.  
`component_sources.yaml` is the full source catalog for `create` component selection and runtime source-backed validation.  
`enable: true|false` controls only default checkbox state in the wizard.  
`config.yaml` does not embed `component_sources`; source resolution uses the resolved `component_sources.yaml` path.

Source profile precedence:

1. `--source-profile`
2. `NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE`
3. default `portable`

Supported `--component-sources-file` examples:

- Relative file in the current directory: `nebius-cxcli --component-sources-file ./component_sources.yaml validate-sources`
- Positional file in the current directory: `nebius-cxcli validate-sources ./component_sources.yaml`
- Relative file elsewhere: `nebius-cxcli --component-sources-file ../../shared/component_sources.yaml validate-sources`
- Absolute file: `nebius-cxcli --component-sources-file /Users/alice/catalogs/component_sources.yaml validate-sources`
- Environment override: `NEBIUS_CXCLI_COMPONENT_SOURCES_FILE=/Users/alice/catalogs/component_sources.yaml nebius-cxcli validate-sources`

Supported source-profile examples:

- Local workstation mode for one command: `nebius-cxcli --source-profile local validate /path/to/config.yaml`
- Local workstation mode via env var: `NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE=local nebius-cxcli render /path/to/config.yaml`

Supported source entry examples inside `component_sources.yaml`:

```yaml
cli:
  flux:
    version: v2.8.0
  terraform:
    version: 1.14.1

infra:
  tf_modules:
    - module: mk8s
      portable_source: git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=main
      local_source: ../../platform-infra/modules/mk8s
      defaults:
        inputs.cpu_nodes_count: 2
      outputs:
        tf_outputs: true
        config:
          access: inputs.mk8s_cluster_public_endpoint
      handoff:
        cluster_id: cluster_id
        access: access
    - module: shared-vpc
      portable_source: git::https://github.com/example/platform-infra.git//modules/shared-vpc?ref=v1.2.3

apps:
  helm_charts:
    - name: external-dns
      repo: https://kubernetes-sigs.github.io/external-dns/
      version: 1.18.0
    - name: gateway-helm
      repo: oci://docker.io/envoyproxy/gateway-helm
      version: 1.4.2
    - name: n8n
      repo: https://github.com/example/charts/tree/main/charts/n8n
      version: 1.2.3
```

`cli.flux.version` is the catalog-controlled Flux controller version for local `deploy` and the managed Flux CLI download path.  
`cli.terraform.version` is the catalog-controlled Terraform CLI version for the managed Terraform download path.  
To upgrade either managed tool version, bump the value in the active `component_sources.yaml`.

Portable build/release behavior:

- `component_sources.yaml` is the only checked-in catalog.
- Build/package steps bundle a portable view of that catalog into the wheel by stripping `local_source`.
- CI/release workflows rewrite internal `portable_source` refs from `?ref=main` to the current commit or tag before publishing wheel or catalog assets.

Recommended workflow:

- Automatic catalog resolution is a convenience default, not a portability guarantee.
- `validate` and `render` default to the `portable` source profile, which emits deployable Terraform module sources suitable for CI and other machines.
- Installed-package fallback is portable by default: when no repo-local/user/global override is present, the packaged `nebius_cxcli/component_sources.yaml` uses Git Terraform module sources.
- Use `--source-profile local` or `NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE=local` when you intentionally want generated Terraform to point at checked-out local module paths for workstation testing.
- Use `--component-sources-file` or `NEBIUS_CXCLI_COMPONENT_SOURCES_FILE` only when you need to override which catalog file is active; it is not the primary portable-vs-local switch.
- Customer-side commands that operate on `generated/` do not need the source catalog to resolve Terraform module paths from the original render environment.

Typical usage:

```bash
# Local development against checked-out Terraform modules
nebius-cxcli --source-profile local validate --strict /path/to/config.yaml
nebius-cxcli --source-profile local render /path/to/config.yaml

# Portable generation for CI / another repository / another machine
nebius-cxcli render /path/to/config.yaml
```

Managed vs external local tools:

- Auto-managed by `nebius-cxcli` when missing:
  - `terraform` for Terraform-backed validation, render lockfile generation, `terraform plan`, `terraform apply`, `terraform unlock`, and backend-backed Terraform output lookups
  - `flux` for `flux bootstrap`
- Still external prerequisites:
  - `kubectl` for `deploy`, `flux apply`, `flux bootstrap`, and Flux readiness checks
  - `helm` for strict Helm source validation in `validate-sources`
  - `aws` CLI for `terraform unlock` remote lock inspection

The CLI checks those external prerequisites when the relevant command path needs them and fails fast with guidance if they are missing.

`outputs` declares producer-side exports.  
Supported export keys are `tf_outputs`, `terraform`, `config`, and `static`.  
`defaults` declares source-defined target values. Literal values are seeded into component config at `create` time and reused at runtime when the target path is missing. Shared-derived values use `shared.<path>` and resolve from top-level `shared` in the active source catalog.  
For Terraform modules, `defaults` targets must start with `inputs.`. For Helm charts, they must start with `values.`.  
`input` declares consumer-side target paths wired from `<component-id>.<output-alias>`.  
`input` is reserved for component-output references only. Use `defaults` for literal values or shared-derived values.  
Shared-derived defaults are managed by the source catalog and must not be duplicated explicitly in `config.yaml`.
Do not declare `shared` in `config.yaml`; shared values are catalog-only and configs with a root `shared` key are rejected.

`handoff` is an infra-to-runtime orchestration contract.  
It is declared once on the Terraform module source entry, not on every Helm chart.  
`handoff.cluster_id` must reference a declared Terraform-backed `outputs` alias.  
`handoff.access` must reference a declared config/static `outputs` alias that resolves to either an explicit access token (`external`/`internal`, `public`/`private`) or a boolean public-endpoint flag.  
Generic exported values do not replace `handoff`; local deploy/bootstrap still needs an explicit runtime contract for kubeconfig setup.  
When enabled charts are deployed, the CLI uses that contract to read the rendered Terraform root output and prepare kubeconfig before Flux/kubectl work starts.

For `apps.helm_charts[]`, `namespace` and `releasename` are defaults:

- interactive create wizard prompts them for enabled apps
- non-interactive create can override with:
  - `--app-namespace <app-id>=<namespace>`
  - `--app-releasename <app-id>=<release-name>`

Runtime config shape:

- `client_info`: `client_name`, `nebius.{tenant_id,project_id,region_id}`, `notifications.{email_enabled,email}`
- `client_info.notifications.email_enabled` is the single per-client gate for inventory email delivery across local runs and CI. Keep it `true` when this client should receive inventory email, and set it to `false` when this specific client should not receive mail.
- In `create`, leaving the optional notifications email blank writes `client_info.notifications.email_enabled: false` and `client_info.notifications.email: null`.
- `client_info` does not include legacy `env` or `cluster_name` fields.
- `infra.components[]`: `id`, `enabled`, `inputs`
- `apps.charts[]`: `id`, `group`, `enabled`, `repo`, `version`, `namespace`, `release-name`, `values`
- Source catalogs use `releasename`; project `config.yaml` uses `release-name`. Aliases such as `release-name` in `component_sources.yaml` or `release_name` in `config.yaml` are not supported.
- Static nested component configs (`infra.<component>.enabled`, `apps.<group>.<chart>.enabled`) are not supported.
- Canonical project path: `<deployments-root>/projects/<client-name>--<tenant-id>/<project-id>/config.yaml`

Infra module source selection comes from the active `component_sources.yaml`. `config.yaml` does not need to pin `infra.components[].source` or `infra.components[].version`.
New starter configs omit those fields entirely.

Flux render output (canonical):

- `generated/flux/helm-repositories.yaml`
- `generated/flux/namespace-<namespace>.yaml`
- `generated/flux/helmrelease-<group>-<release>.yaml`
- `generated/flux/kustomization.yaml`
- Legacy nested Flux layout (`generated/flux/apps`, `generated/flux/sources`) is not supported.

Inventory render output:

- `generated/nebius-cxcli-manifest.json`
- `generated/inventory/inventory.md`
- `inventory.md` is the human-readable inventory and the email body source for `nebius-cxcli email`.
- `render`, `deploy`, `terraform apply`, `flux apply`, `flux bootstrap`, and `inventory write` refresh inventory artifacts for the active project instead of leaving the starter placeholder behind.
- Those refreshes also delete stale legacy inventory JSON files so the directory reflects the current generated contract.

Terraform render output (canonical):

- `generated/infra/backend.tf`
- `generated/infra/versions.tf`
- `generated/infra/providers.tf`
- `generated/infra/variables.tf`
- `generated/infra/main.tf`
- `generated/infra/outputs.tf`
- `generated/infra/terraform.auto.tfvars.json`
- `generated/infra/.terraform.lock.hcl` (generated during `render` when Terraform is available from `PATH` or the managed download path and backend-disabled init succeeds; transient `.terraform/` workdir state is removed afterward)
- Local Terraform module sources are rendered as resolved filesystem paths. Use an explicit `git::...//subdir?ref=...` source in `component_sources.yaml` when you want a portable or pinned remote module reference.
- Terraform remote state is managed separately from app/object-storage components: backend bucket settings are derived from `client_info` (`client_name` + `project_id` + `region_id`), not from `infra.components[id=object-storage].inputs`.
- Backend locking uses Terraform S3 lockfile mode (`use_lockfile = true`) and Nebius Object Storage endpoint (`https://storage.<region>.nebius.cloud`).
- If a deploy/apply is canceled while Terraform is waiting on or holding the backend lock, the remote `.tflock` object can remain behind. In that case the next apply fails before creating any resources; the CLI now reports that explicitly and includes the lock owner/creation time from Terraform's lock metadata.
- `nebius-cxcli terraform unlock <generated-dir>` is the recovery path for that case. It inspects the remote `.tflock`, refuses by default when local Terraform/deploy processes are still active or the lock owner is from another machine/user, and then uses Terraform's own `force-unlock` only when the lock looks stale. Do not run it as routine cleanup.
- `terraform unlock` requires `aws` CLI in `PATH`. Terraform itself can come from `PATH` or from the managed Terraform download path.
- Inventory artifacts are local-only outputs under `generated/inventory`; they are not uploaded to Object Storage by the CLI.

Wizard field behavior:

- Infra input field names are discovered dynamically from Terraform module variables (required and optional).
- Interactive `create` and `component add` offer all discoverable required and optional component fields for newly selected components.
- Required fields are prompted first, are labeled `required`, and must receive a valid value before the wizard advances unless the operator stops the wizard with `q`.
- Optional fields are labeled `optional`; pressing Enter keeps the current/default value and leaves the field unset in `config.yaml` when the value is still only a virtual default.
- Prompt labels include Terraform input type hints (for example `string`, `number`, `bool`) plus `required` or `optional`.
- Collection/object Terraform inputs (`list(...)`, `map(...)`, `object(...)`, `tuple(...)`) are entered as YAML/JSON values in the wizard instead of being flattened into string-only prompts.
- Terraform module defaults and Helm chart defaults can be shown as prompt defaults without being copied into `config.yaml`; they remain virtual until the operator explicitly overrides them.
- Literal defaults from `component_sources.yaml` are still shown in the wizard as editable current values instead of being hidden once pre-seeded into the component block.
- Empty optional YAML/JSON defaults such as `{}` and `[]` are rendered as blank-input prompts with explicit “blank keeps current empty map/list” guidance instead of awkward literal default tokens.
- Multiline Terraform defaults discovered from module `variables.tf` files, including map/object defaults, are parsed as full values in wizard mode instead of being truncated to the first line.
- Source-backed infra `inputs.parent_id`/`inputs.project_id` default to `client_info.nebius.project_id` when those variables exist.
- `component_sources.yaml` can declare top-level `shared` values and shared-derived `defaults` so components read shared values from the active source catalog instead of duplicating them under component `inputs` or chart `values`.
- The bundled `mk8s` catalog entry defaults `inputs.mk8s_cluster_public_endpoint: true`, and the handoff contract now resolves access dynamically from that input. If you switch the control plane to private-only, local app operations still work, but only from a machine that already has private network reachability to the MK8s API endpoint.
- The bundled `mk8s` catalog entry also defaults `inputs.kube_network_service_cidrs: ["/20"]`. Nebius treats an omitted MK8s service CIDR as `["/16"]`; on a single-pool `/16` subnet that can consume the whole pool and leave no address space for control-plane allocations, which looks like a long `PROVISIONING` stall.
- The bundled `mk8s` catalog entry also defaults `inputs.cpu_nodes_count: 2`. That keeps the baseline cluster footprint explicit in `config.yaml` and editable in the wizard instead of relying on a hidden Terraform module default for CPU node-group size.
- Fields behind a sibling `<prefix>_enabled` toggle, such as MK8s GPU settings behind `gpu_enabled`, stay hidden until that toggle is true.
- Provider-backed option lists are inferred by field patterns and resolved live from Nebius APIs when available.
- If live provider choices are unavailable for a field, the CLI prints a field-specific warning immediately before that prompt and explains whether the next manual-input prompt is required or can be skipped with Enter.
- Optional provider-backed fields now accept blank/skip answers as “leave unset” without revalidating that blank value against the live option list.
- Helm chart default values discovered from the live chart are not copied into `config.yaml`; the app wizard can show them as prompt defaults, but only explicit overrides are written back.
- Current built-in provider option sources include:
  - `mk8s_compatible_platforms` (for mk8s platform fields)
  - `compute_platforms`
  - `compute_platform_presets`
  - `project_subnets`
  - `project_networks`
  - `tenant_projects`
  - `mk8s_control_plane_versions`
- When live provider options are unavailable, the wizard falls back to manual input.

Shared-derived default example:

```yaml
# component_sources.yaml
shared:
  admin_ssh:
    user_name: ubuntu

infra:
  tf_modules:
    - module: wireguard-jumphost
      portable_source: git::https://github.com/example/platform-infra.git//modules/wireguard-jumphost?ref=v1.2.3
      local_source: ../../platform-infra/modules/wireguard-jumphost
      enable: false
      defaults:
        inputs.ssh_user_name: shared.admin_ssh.user_name
```

```yaml
# config.yaml
infra:
  components:
    - id: wireguard-jumphost
      enabled: true
      inputs:
        parent_id: project-123
        ssh_public_key: ssh-ed25519 AAAA... admin@example
```

With that default, `wireguard-jumphost` receives `ssh_user_name` from the active `component_sources.yaml` `shared.admin_ssh.user_name` value during validation/render.  
`ssh_public_key` is intentionally per-project input and should be stored only in the private project `config.yaml`, not in the shipped source catalog.  
Do not duplicate shared-derived default targets under `infra.components[].inputs` or `apps.charts[].values`; strict validation rejects explicit values for catalog-managed targets.

Catalog defaults example:

```yaml
# component_sources.yaml
infra:
  tf_modules:
    - module: mk8s
      portable_source: git::https://github.com/example/platform-infra.git//modules/mk8s?ref=v1.2.3
      local_source: ../../platform-infra/modules/mk8s
      defaults:
        inputs.cluster_name: demo-cluster
        inputs.cpu_nodes_count: 3

apps:
  helm_charts:
    - name: demo-app
      repo: https://example.invalid/charts
      version: 1.0.0
      namespace: demo
      releasename: demo-app
      defaults:
        values.replicaCount: 2
        values.image.tag: stable
```

With that contract, `create` seeds those values into the starter `config.yaml`, the wizard skips prompting for those target paths, and runtime commands still use them as fallback when the field is omitted from the config.

Component output binding example:

```yaml
# component_sources.yaml
infra:
  tf_modules:
    - module: mk8s
      portable_source: git::https://github.com/example/platform-infra.git//modules/mk8s?ref=v1.2.3
      local_source: ../../platform-infra/modules/mk8s
      outputs:
        tf_outputs: true
        config:
          access: inputs.mk8s_cluster_public_endpoint

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

This contract is fully catalog-driven:

- producers declare exported aliases under `outputs`
- consumers declare target paths under `input`
- both producer and consumer must exist in `component_sources.yaml`
- source component ids must be globally unique across `infra` and `apps`

Resolution model:

- `outputs.static` resolves immediately from the source catalog entry.
- `outputs.config` resolves immediately from the source component config row.
- Terraform-backed outputs render as native `module.<producer>.<output>` references for infra consumers.
- Terraform-backed outputs for app consumers resolve from Terraform state. `deploy` and `flux bootstrap` handle that automatically after Terraform outputs exist.
- Plain `render` resolves Terraform-backed app bindings only when prior Terraform state already exists. Otherwise it fails fast with guidance instead of emitting partial values.

## Recommended Workflow

1. `nebius-cxcli create <deployments-root>`
2. Optional day-2 config edits on the existing project:
   - `nebius-cxcli component list <config.yaml>`
   - `nebius-cxcli component add <config.yaml>`
   - `nebius-cxcli component remove <config.yaml>`
3. Edit the project `config.yaml` with real values.
4. Optional extra readiness gate: `nebius-cxcli validate --strict <config.yaml>`
5. `nebius-cxcli render <config.yaml>`
6. Commit the project `config.yaml` and the deployable `generated/` bundle to the customer private repo.
7. Deploy from the generated bundle:
   - `nebius-cxcli deploy <generated-dir>`
   - `nebius-cxcli terraform apply <generated-dir>`
   - `nebius-cxcli flux apply <generated-dir>`
   - CI workflow deploys from `generated/`, not from `config.yaml`
8. Optional CI setup:
   - `nebius-cxcli bootstrap-ci <config.yaml>`
   - The generated customer workflow watches `generated/**` only. Editing `config.yaml` in the customer repo does not trigger CI deploys; rerendering from `config.yaml` is a manual replace action.

`create` is idempotent by default. Re-running the same project identity reconciles selections and preserves existing values. Use `create --force` only when you want reset/overwrite behavior.

`create` is the bootstrap path from the deployments root because it owns project identity (`client_name`, `tenant_id`, `project_id`, `region_id`) and initial scaffold creation. Once `config.yaml` already exists, use `component list/add/remove` against that file for day-2 component selection changes. Those commands keep the current identity and existing values intact, and `render` remains the full reconcile step back into `generated/`.

In the customer private repo, keep both:

- `config.yaml` as the original render/replace contract
- `generated/` as the deploy contract used by day-2 operations and CI

Rerendering from `config.yaml` is still supported, but it is a manual replace action. The CLI now renders into a hidden sibling staging directory and only swaps it into `generated/` after the new bundle is complete, so a failed rerender leaves the current bundle untouched. The replacement still removes stale or legacy content under `generated/`, including an old `generated/flux/flux-system` subtree. In an interactive terminal, `render` prompts for confirmation before overwrite. In non-interactive contexts, rerender requires `--force`.

For Flux/GitOps, the important safety boundary is Git history, not the local render directory swap. The recommended workflow is: rerender locally, validate/review the new `generated/` diff, then commit and push one final snapshot of the watched path. Do not push an intermediate commit that removes manifests from the watched Git path, and do not routinely unbootstrap/rebootstrap Flux just to replace rendered artifacts.

Sensitive per-project values such as jump-host SSH public keys belong in the private project `config.yaml`, not in the shipped public `component_sources.yaml`.

Local `deploy`/`flux bootstrap` behavior when apps + a handoff-enabled cluster component are enabled:

- `deploy` now runs `terraform validate` after render and before apply.
- When Terraform is not already in `PATH`, `deploy`, `terraform plan`, `terraform apply`, `terraform unlock`, and backend-backed Terraform output lookups use a managed Terraform CLI download pinned by `component_sources.yaml` `cli.terraform.version`. The binary is cached under the local nebius-cxcli cache and is not installed system-wide.
- During long-running `terraform apply`, `deploy` and `terraform apply` print one merged status surface: Terraform apply transitions plus a light Nebius MK8s API snapshot. When an enabled `mk8s` component is present and Nebius SDK auth is available, the CLI polls Nebius MK8s API for cluster/node-group status; otherwise it falls back to a simple elapsed heartbeat for the API side.
- The merged status surface is rendered as a multi-line block with distinct TF and API sections so Terraform progress and Nebius resource state are visually separate in the terminal.
- If Terraform apply fails, the CLI exits with the Terraform error as the canonical failure and appends the last known merged Terraform/API status snapshot.
- Remote state lock failures are called out separately: the CLI explains that Terraform never acquired the backend lock, so the run created nothing, and points at the stale `.tflock` object metadata when Terraform provides it.
- When Nebius MK8s node-group status reports `ERROR` events, the merged status block includes those alerts. Known transient bootstrap warnings such as waiting for ProviderID registration or temporary `Ready=False` node conditions are shown as notes instead of alerts while the node group is still provisioning.
- After apply, `deploy` reads the rendered Terraform output declared by `handoff.cluster_id` and configures a temporary kubeconfig before applying Flux manifests.
- `handoff.access` is config-driven for the bundled `mk8s` component, so the CLI automatically selects the public or private control-plane endpoint based on `inputs.mk8s_cluster_public_endpoint` instead of assuming public access.
- On non-CI local runs, that same cluster handoff also updates the user kubeconfig at `~/.kube/config` with a `nebius-cxcli` exec-based credential entry, so `kubectl` can be used against the target cluster after `deploy`, `flux apply`, or `flux bootstrap` without installing a separate Nebius CLI.
- `destroy` and `flux destroy` still use the same MK8s handoff contract for temporary cluster access when app resources must be removed first, but they do not persist or switch the user's local `~/.kube/config`.
- When the selected handoff endpoint is private, `deploy`, `flux apply`, `flux bootstrap`, `destroy`, and `flux destroy` require the current machine to already have a private network path to the MK8s API. The CLI does not hardcode or auto-provision that path; customer environments can satisfy it with VPNs, routed private networks, subnet routers, SSH/WireGuard tunnels, or by running the command from an in-network runner.
- Before `deploy`, `flux apply`, or `flux bootstrap` starts Flux work against a handed-off MK8s cluster, the CLI first checks Kubernetes node readiness and only waits when the nodes are not `Ready` yet. When the cluster is already healthy, it proceeds to Flux work immediately instead of presenting that probe as a wait.
- Once the cluster handoff is ready, the local Flux phase now keeps one continuous spinner alive and updates its message through cluster reachability, Flux API discovery, rendered manifest apply, and the final rendered-resource readiness wait so the command does not go visually idle between phases.
- In non-interactive logs such as GitHub Actions, those same phase updates fall back to stable printed lines instead of transient spinner frames, so CI logs remain readable and do not depend on TTY animation support.
- Generated Flux artifacts are treated as the deploy truth. If an app chart depends on Terraform-backed component outputs, you must rerender after the needed Terraform state exists before treating `generated/flux` as the final GitOps payload.
- Flux render writes explicit Namespace manifests for chart target namespaces before namespaced `HelmRelease` resources, so local `kubectl apply -k generated/flux` does not fail with `namespaces "<name>" not found`.
- Flux uses a split namespace model in this project: shared Flux control-plane and source objects such as `HelmRepository` / `GitRepository` typically live in `flux-system`, while the actual `HelmRelease` and workload pods live in their target app namespace. A workload namespace does not need its own dedicated source object unless it truly uses a different chart or repo source.
- If Flux controllers are missing, `deploy` installs the core Flux controllers into the target cluster automatically using the official Flux install manifest. `flux` CLI is not required for local `deploy`.
- The install manifest version used by local `deploy` comes from `component_sources.yaml` `cli.flux.version`.
- After `kubectl apply -k generated/flux`, `deploy` waits for the rendered Flux `source.toolkit` and `helm.toolkit` resources to report `Ready`, so local deploy does not exit before chart source fetch or Helm reconciliation has actually succeeded.
- If Flux controllers had to be installed during `deploy`, the CLI also waits for the required Flux CRD-backed APIs to become discoverable before applying the rendered Flux bundle. This avoids transient `the server could not find the requested resource` races immediately after controller install.
- While that Flux wait is in progress, `deploy` and `flux apply` poll the rendered Flux resources from the cluster with `kubectl get -o json` and print a generic status block showing which `HelmRepository`, `GitRepository`, `HelmRelease`, or `Kustomization` objects are still progressing. This is chart-agnostic and does not hardcode a specific release name.
- If all rendered workload resources are already `Ready` and only rendered Flux source objects remain pending without publishing a `Ready` condition, the CLI stops waiting and completes with a note instead of hanging until the full timeout. This avoids false hangs on source-controller status edge cases after a successful local app apply.
- `deploy` and `flux apply` are intentionally local direct-apply paths. They do not bootstrap GitOps automatically, because that would require implicit GitHub/Flux bootstrap side effects. If the cluster is not bootstrapped yet, the CLI now finishes the local apply and prints a warning with the exact `nebius-cxcli flux bootstrap <generated-dir>` follow-up command.
- `flux apply` uses that same local app-deploy path without running Terraform apply, so it is the apps-only command for day-2 chart deploys after infra already exists.
- `terraform apply` is safe to rerun sequentially with the same `generated/infra`: it validates the existing generated infra bundle and then relies on Terraform state convergence. It is not safe to run concurrently against the same backend state; Terraform remote locking is the protection there.
- `flux apply` is safe to rerun sequentially with the same `generated/flux`: it applies the existing rendered manifests, skips Flux controller installation when controllers are already present, and waits for the rendered Flux resources to become `Ready`.
- `flux bootstrap` auto-downloads a managed Flux CLI binary from the official Flux GitHub release for the catalog-pinned `cli.flux.version` when `flux` is not already in `PATH`. The binary is cached under the local nebius-cxcli cache and is not installed system-wide.
- `flux bootstrap` resolves the GitHub repo slug from `GITHUB_REPOSITORY` when present, otherwise it falls back to the local git `origin` remote.
- `flux bootstrap` uses the same handoff contract instead of hardcoding `mk8s_cluster_id` in CI workflow glue.
- `flux bootstrap` only takes the reconcile path when the cluster already has both Flux controllers and the bootstrap Git objects `GitRepository/flux-system` plus `Kustomization/flux-system`. If controllers exist but those bootstrap objects do not, the CLI runs a real `flux bootstrap github ...` instead of a reconcile that would fail.
- `flux bootstrap` is the GitOps path. It expects the rendered manifests to be committed and pushed to the watched GitHub repo/path before or immediately after bootstrap. If you want an immediate local cluster apply without depending on Git content yet, use `flux apply`.
- Local kubeconfig persistence can be disabled explicitly with `NEBIUS_CXCLI_PERSIST_LOCAL_KUBECONFIG=false`. In CI it is skipped automatically.
- `deploy`, `destroy`, `flux apply`, and `flux destroy` require `kubectl` when they operate on rendered app resources.
- Nebius API/runtime auth interactions use the Nebius SDK. A standalone `nebius` CLI install is not required.
- `flux bootstrap` still needs network access to GitHub releases when the managed Flux CLI download path is used.
- The managed Terraform download path needs network access to HashiCorp releases when Terraform is not already in `PATH`.
- If your Nebius SDK config already has auth, `deploy` can reuse that SDK config. Otherwise rerun with runtime auth material available, for example `--auto-auth-bootstrap`.

## Releases

Use the release flow in three steps:

1. Prepare the changelog on your working branch with `./publish-release.sh --prep X.Y.Z`.
2. Merge that branch to `main`.
3. From a clean, synced `main`, create and push the release tag with `./publish-release.sh --publish X.Y.Z`.

`--prep` requires a strictly clean worktree, including untracked files, so the release-prep commit stays isolated.
On a brand-new local release branch, `--prep` now pushes with `git push --set-upstream origin <branch>` automatically, so you do not need to publish the branch manually before the prep step succeeds.
`--prep` also fails before editing `CHANGELOG.md` if the target tag already exists locally or on `origin`, so duplicate release-prep runs for an already-published version stop immediately.
`--prep` is idempotent while the target tag does not already exist. You can run it multiple times for the same unreleased version; once `## [Unreleased]` is empty, reruns leave `CHANGELOG.md` and `HEAD` unchanged.
`--publish` fails locally before tagging if the target changelog section is missing or empty.

The publish step creates the annotated tag `nebius-cxcli-vX.Y.Z`. That tag triggers the repository workflow at `.github/workflows/nebius-cxcli-release.yml`, which reruns the same local `make all` verification contract, runs `validate-sources component_sources.yaml` against the real portable catalog, verifies that the wheel version matches the tag, verifies that the bundled fallback `component_sources.yaml` is present inside the wheel, and publishes the GitHub Release from the tagged commit.
Those post-`make all` workflow checks use the repo `.venv/bin/python` created by that contract so `nebius_cxcli.release_catalog` imports the editable service package reliably under GitHub Actions.

In source/editable checkouts, runtime version resolution prefers live SCM state over a generated `_version.py` cache: it uses `setuptools-scm` when available and falls back to `git describe` when it is not. The local `./publish-release.sh --publish X.Y.Z` flow also verifies that the tagged source checkout resolves `nebius-cxcli.__version__ == X.Y.Z` before it pushes the release tag.

Release assets for `nebius-cxcli` now include:

- the wheel artifact
- the raw release catalog as a direct editable download, with its Terraform Git module refs pinned to the published release tag

## Commands

Idempotency guide:

- Read-only commands are safe to repeat: `validate-sources`, `validate`, `validate-generated`, `discover`, `terraform plan`, and `auth --validate-profile`.
- Reconcile/apply commands are sequentially idempotent or convergent for the same target: `create` (default mode), `render`, `deploy`, `terraform apply`, `flux apply`, `flux bootstrap`, `inventory write`, `bootstrap-ci`, `auth --create`, and `auth --bootstrap-ci`.
- Destructive commands are sequentially convergent for the same target but intentionally remove resources: `destroy`, `terraform destroy`, and `flux destroy`. They require confirmation or `--yes`.
- Explicit additive or side-effecting commands are intentionally not idempotent: `component add` creates another component instance on repeat, `auth --recreate` rotates auth material, and `email` sends another message on each run.
- `create --force` and `render --force` are still deterministic with the same inputs, but they are explicit overwrite/reset modes rather than the safer default reconcile flow.
- `terraform unlock` is operationally safe to repeat: once the lock is cleared, reruns report that no lock is present.

Global options:

- `--version`
- `--component-sources-file <path>`
- `--source-profile {portable|local}`

### Generator-side Commands

```bash
nebius-cxcli validate-sources
nebius-cxcli validate /path/to/config.yaml
nebius-cxcli validate --strict /path/to/config.yaml
nebius-cxcli render /path/to/config.yaml
```

- `validate-sources`
  - Validates the active `component_sources.yaml` catalog: Terraform module sources, Helm chart sources, catalog contract shape, and fast source-structure checks for CLI-friendly Terraform modules and Helm charts.
  - Accepts an optional positional catalog path, for example `nebius-cxcli validate-sources ./component_sources.yaml`.
- `validate <config.yaml>`
  - Validates the project config contract and runtime shape without the stricter deployment-readiness checks.
  - Runs phased validation with visible progress: config/catalog load, active source checks, dependency checks, then Terraform module input/schema checks.
  - Defaults to the global source profile `portable`, so validation fails when the requested render contract would rely on non-portable local Terraform module paths.
- `validate --strict <config.yaml>`
  - Adds stricter deployment-readiness checks on top of `validate`, including source-backed and runtime-backed checks used before rendering.
  - Keeps the same visible phase reporting and then adds strict readiness plus MK8s preflight phases.
- `render <config.yaml>`
  - Generates the deployable bundle under `generated/`, refreshes inventory, and writes `generated/nebius-cxcli-manifest.json`.
  - Rerender now stages the new bundle under a hidden sibling directory and swaps it into `generated/` only after the replacement bundle is complete.
  - The replacement recreates the managed generated bundle from a clean canonical layout without stale files from earlier renders and removes any legacy `generated/flux/flux-system` subtree.
  - Defaults to the global source profile `portable`, which rewrites active local module sources to their portable Git equivalents when available.
  - Use `--source-profile local` only for workstation testing against checked-out local Terraform modules; those generated artifacts are intentionally non-portable.
  - Use `--component-sources-file` or `NEBIUS_CXCLI_COMPONENT_SOURCES_FILE` only when you need to select a non-default catalog file.
  - If `generated/` already contains files, `render` prompts before overwrite in an interactive terminal.
  - In non-interactive contexts, use `nebius-cxcli render --force <config.yaml>` to confirm the replacement explicitly.

### Customer-side Commands

```bash
nebius-cxcli validate-generated /path/to/generated
nebius-cxcli deploy /path/to/generated
nebius-cxcli destroy /path/to/generated --yes
nebius-cxcli terraform apply /path/to/generated
nebius-cxcli terraform destroy /path/to/generated --yes
nebius-cxcli flux apply /path/to/generated
nebius-cxcli flux destroy /path/to/generated --yes
nebius-cxcli flux bootstrap /path/to/generated
```

- `validate-generated <generated-dir>`
  - Validates an existing generated bundle without rerendering it. Runs `terraform validate` against `generated/infra` and `kubectl kustomize` against `generated/flux` when apps exist.
  - Reports visible phases for backend auth/bootstrap, Terraform validation, Flux manifest validation, and optional portability enforcement.
  - Add `--portable` in CI or pre-commit checks to reject generated Terraform bundles that still embed local filesystem module paths.
  - Uses the generated bundle as the deploy contract; it does not need the original render machine's local module paths.
- `deploy <generated-dir>`
  - Full local reconcile from the generated bundle: Terraform apply first, then inventory refresh for both infra and apps artifacts, then Flux apply. If GitOps bootstrap is not configured yet, the CLI warns and prints the follow-up `flux bootstrap` command.
  - `deploy` is idempotent in the Terraform/Flux sense: rerunning the same generated bundle converges to no-op, but it is not a create-only path. Existing managed infrastructure or workloads can be updated when the generated bundle differs from live state.
  - Use `nebius-cxcli terraform plan <generated-dir>` first when you need a non-mutating preview of the next reconcile.
  - Nebius API status polling for infra is catalog-driven per Terraform module. The generated manifest snapshots enabled module watcher specs, and `deploy`/`terraform apply` fall back to the active catalog when older generated bundles do not have that metadata yet.
  - Each watcher resolves its `parent_id` and `resource_name` from the enabled component row in `config.yaml`, using the catalog's `status.parent_input` and `status.name_input` paths. For example, `mk8s` reads `inputs.parent_id` plus `inputs.cluster_name`, `managed-postgresql` reads `inputs.parent_id` plus `inputs.name`, and `object-storage` reads `inputs.parent_id` plus `inputs.name`.
  - Status output now reads Nebius service-native response fields for MSP PostgreSQL, SFS, and object-storage watchers, so in-progress resources are reported instead of being shown as "not visible yet".
  - `deploy` does not run `flux bootstrap`; use `flux bootstrap` itself or the generated CI apply workflow when you want GitOps bootstrap/reconcile.
  - `deploy` does not run `bootstrap-ci` automatically, even when the bundle lives inside a git repository. GitHub workflow/environment bootstrap stays an explicit generator-side step.
- `destroy <generated-dir>`
  - Full local teardown from the generated bundle: delete rendered Flux resources from the target cluster first when apps are enabled, then run Terraform destroy against the rendered infra bundle.
  - `destroy` is the destructive inverse of `deploy`. It operates only on the existing generated bundle, does not rerender from `config.yaml`, and does not uninstall Flux controllers or mutate GitHub CI/bootstrap state.
  - Rendered app teardown is best-effort. If deleting the rendered Flux resources fails, the CLI warns and still continues with Terraform destroy because the rendered infra bundle is the authoritative teardown path.
  - The command requires explicit confirmation in interactive mode and `--yes` in non-interactive mode.
  - If you only want the infra teardown, use `terraform destroy`. If you only want the rendered app teardown, use `flux destroy`.
- `terraform apply <generated-dir>`
  - Infra-only apply from the generated Terraform bundle. Safe to rerun sequentially for convergence, and does not depend on resolving the original source catalog's module paths.
- `terraform destroy <generated-dir>`
  - Infra-only destroy from the generated Terraform bundle. Destructive by intent, requires confirmation or `--yes`, and reuses the same generated-bundle runtime auth/backend/status machinery as `terraform apply`.
- `flux apply <generated-dir>`
  - Apps-only direct apply from the generated Flux bundle. Safe to rerun sequentially for day-2 reconciliation. If GitOps bootstrap is not configured yet, the CLI warns and prints the follow-up `flux bootstrap` command.
- `flux destroy <generated-dir>`
  - Apps-only direct delete from the generated Flux bundle using the same rendered manifests that `flux apply` manages. Destructive by intent and requires confirmation or `--yes`.
- `flux bootstrap <generated-dir>`
  - GitOps bootstrap/reconcile path from the generated Flux bundle. Use this when the cluster should watch the Git repo/path with Flux.
  - Normal day-2 updates should replace `generated/` locally, then commit and push one final watched-path snapshot. Do not unbootstrap/rebootstrap Flux just to roll out a new rendered bundle.

### Supporting Commands

```bash
nebius-cxcli component list /path/to/config.yaml
nebius-cxcli component add /path/to/config.yaml
nebius-cxcli component remove /path/to/config.yaml
nebius-cxcli create /path/to/deployments-root
nebius-cxcli bootstrap-ci /path/to/config.yaml
nebius-cxcli discover /path/to/deployments-root
nebius-cxcli terraform plan /path/to/generated
nebius-cxcli terraform destroy /path/to/generated --yes
nebius-cxcli terraform unlock /path/to/generated
nebius-cxcli flux destroy /path/to/generated --yes
nebius-cxcli inventory write /path/to/generated
nebius-cxcli destroy /path/to/generated --yes
nebius-cxcli email /path/to/generated
nebius-cxcli auth --project-config /path/to/config.yaml --validate-profile
```

- Positional target quick map:
  - `create`: pass the deployments root directory.
  - `discover`: pass the deployments root or any narrower directory under it, including one project directory or `generated/`.
  - `component`, `validate`, `render`, `bootstrap-ci`: pass the project `config.yaml`.
  - `validate-generated`, `deploy`, `destroy`, `terraform *`, `flux *`, `inventory write`, `email`: pass `generated/`, one of its subdirectories, or a file under that tree as accepted by the command.
  - `validate-sources`: optional explicit `component_sources.yaml` path.
  - `auth`: no positional path; use `--project-config <config.yaml>` or `--project-id`, or omit both with `--validate-profile` to inspect all cached profiles.

- `component list <config.yaml>`
  - Shows enabled and available catalog entries for the current project, split between infra modules and app charts.
  - Read-only inspection command for deciding the next add/remove action against the current `config.yaml`.
- `component add <config.yaml>`
  - Adds source-defined infra module rows or app chart rows to an existing project config without recreating the project scaffold.
  - Catalog entries are reusable component types. Each add creates a new enabled component instance with its own `instance_id`, so you can add `mk8s`, `managed-postgresql`, `object-storage`, or app charts multiple times in one project.
  - Interactive mode prompts separately for infra and apps, confirms the selection, auto-resolves app chart dependencies, and then runs the field wizard only for the newly added components.
  - That field wizard offers all discoverable required and optional fields for each new component, including editable literal catalog defaults. Required fields must be filled before advancing; optional blanks stay implicit when they still match module/chart defaults.
  - Source validation runs by default, mirroring `create`. Use `--no-validate-sources` only when you intentionally want to skip catalog preflight.
  - The command revalidates the existing Nebius tenant/project scope before provider-backed field prompts, so missing SDK credentials or inaccessible scope are surfaced as explicit errors.
  - Complex Terraform inputs such as `allowed_cidrs`, `clients`, `secrets`, and `mk8s_*_overrides` are edited as YAML/JSON values in the wizard.
  - Non-interactive mode accepts component ids directly, for example `nebius-cxcli component add /path/to/config.yaml managed-postgresql object-storage gateway-helm --no-interactive`.
  - Repeating the same component id adds another instance. You can also request an explicit instance id with `<component-id>@<instance-id>`, for example `object-storage@logs-bucket`.
  - `object-storage` now represents one bucket per enabled module instance and requires `inputs.name`.
  - Existing component values are preserved. After the edit, run `validate` and `render` again.
- `component remove <config.yaml>`
  - Removes enabled infra module rows or app chart rows from an existing config.
  - Interactive mode prompts separately for infra and apps and asks for confirmation before editing.
  - When multiple instances of the same component type are enabled, remove by exact `instance_id` or `<component-id>@<instance-id>`.
  - The command fails fast when the removal would leave unresolved app dependencies or component input bindings.
- `create <deployments-root>`
  - Scaffolds or reconciles the project `config.yaml` and generated-folder skeleton.
  - In interactive mode, `create` prints an early notice when the deployments root already contains project configs and offers a stop/continue prompt before any project-identity prompts appear. Both that early guard and the later exact-project reconcile confirmation now default to continue.
  - If exactly one existing project config is present and no explicit `--client-name` / `--tenant-id` / `--project-id` flags were supplied, interactive `create` offers those identity values as the prompt defaults.
  - When the target project config already exists, interactive `create` warns and asks for confirmation before reconcile/update continues.
  - After writing the resulting `config.yaml`, `create` runs the same non-strict runtime validation as `validate` by default. Use `--no-validate-config` only when you intentionally want to skip that post-write check.
  - In interactive mode, `q` can stop the wizard at any point. The command still writes the current project config and warns only when required fields remain unresolved.
  - For selected components, the field wizard offers all discoverable required and optional fields, including editable literal catalog defaults. Required blanks are rejected immediately; optional blanks keep defaults implicit when possible.
- `bootstrap-ci <config.yaml>`
  - Generates or reconciles the customer GitHub Actions workflow, always reconciles GitHub email settings from local `email --setup`, and optionally bootstraps/syncs the required Nebius CI auth secrets. The generated workflow watches and deploys only `generated/**`.
  - The workflow file is CLI-managed. Re-running `bootstrap-ci` automatically reconciles `.github/workflows/nebius-deployments.yml` to the latest generated contract and is idempotent when no drift exists.
  - Generated workflows validate changed bundles with `nebius-cxcli validate-generated --portable` before Terraform plan/apply.
  - Generated workflows rely on the same generated-bundle CLI commands, which recreate ignored `generated/infra/terraform.auto.tfvars.json` from `generated/nebius-cxcli-manifest.json` before Terraform runs.
  - Generated workflows do not install the standalone `nebius` CLI. MK8s kubeconfig handoff and token retrieval stay inside `nebius-cxcli` via the Nebius SDK.
  - Generated workflows install `kubectl` directly from upstream Kubernetes release binaries instead of `azure/setup-kubectl`, so they are not coupled to GitHub Actions Node runtime deprecations.
  - Generated workflows also keep the Python version in one env var and emit compact single-line discovery JSON into `GITHUB_OUTPUT` so matrix handoff stays deterministic.
  - The target `config.yaml` must already live inside the customer git repository because the workflow is written at that repo root under `.github/workflows/`.
  - `--auth-bootstrap` is already enabled by default. Re-running `bootstrap-ci` always reconciles the managed workflow plus GitHub email settings, and with default `--auth-bootstrap` also reconciles Nebius CI auth secrets. Use `--no-auth-bootstrap` only when you intentionally want to skip Nebius CI auth bootstrap/rotation.
  - The GitHub Environment contract consists of these required secrets: `NEBIUS_SA_ID`, `NEBIUS_AUTH_PUBLIC_KEY_ID`, `NEBIUS_AUTH_PRIVATE_KEY_PEM`, `NEBIUS_S3_ACCESS_KEY_ID`, `NEBIUS_S3_SECRET_ACCESS_KEY`, and `FLUX_GITHUB_TOKEN`.
  - Local SMTP setup is operator-managed with `nebius-cxcli email --setup`, which writes `~/.config/nebius-cxcli/email.yaml`.
  - Every `bootstrap-ci` run reconciles that local SMTP config into the GitHub Environment: `SMTP_HOST`, `SMTP_PORT`, `SMTP_STARTTLS`, and optional `SMTP_FROM` are environment variables; optional `SMTP_USERNAME` and `SMTP_PASSWORD` are environment secrets. If local SMTP is removed, `bootstrap-ci` removes stale GitHub SMTP settings too.
  - The generated workflow always runs the email step after apply. `client_info.notifications.email_enabled` is the single send/no-send switch. If it is `true` but SMTP is not configured, the step warns and continues instead of failing the deployment.
  - The command auto-detects the target GitHub repo from that checkout's `origin` remote. Use `--github-repo <owner/repo>` only as an explicit override when the remote is missing, non-GitHub, or not the repo you want to manage.
  - `--github-token-env <ENV>` controls the GitHub API token used for workflow/environment reconciliation, SMTP sync, and optional Nebius auth bootstrap. Use it when the token is stored in a non-default environment variable instead of `GH_TOKEN`/`GITHUB_TOKEN`.
  - Because SMTP reconciliation happens on every run, `bootstrap-ci` needs GitHub API access even with `--no-auth-bootstrap`.
  - When `--cli-ref` is omitted, generated workflows default to `main` for development builds and to `nebius-cxcli-v<version>` for stable tagged releases.
  - Use `--cli-ref <branch|tag|sha>` when you want `bootstrap-ci` to bake a specific default install ref into the generated workflow.
  - `--cli-ref` is the `nebius-cxcli` source ref for the generated workflow to install from `nebius-ps-services`; it is not the branch of the target customer repo.
  - Example: `nebius-cxcli bootstrap-ci /path/to/config.yaml --cli-ref feature/my-branch`
  - Generated workflows also honor an optional GitHub repo/org variable `NEBIUS_CXCLI_REF`; when set, it overrides the generated default ref without editing the workflow file.
  - Customers do not need to set `NEBIUS_CXCLI_REF` before each run. Set it only when you want to override the baked workflow default, and leave it in place until you want a different ref.
  - `bootstrap-ci` creates the GitHub Environment, reconciles SMTP settings on every run, and with `--auth-bootstrap` also syncs Nebius CI auth secrets. It does not create GitHub repo/org variables; `NEBIUS_CXCLI_REF` remains an optional manual override.
- `discover <deployment-scope-dir>`
  - Returns changed deployment projects for CI matrix generation.
  - Accepts the deployments root or any narrower directory under it, including one project directory or `generated/`.
  - Scope filtering is project-aware: both `--all` and normal changed-file discovery still resolve the matching project when the scope is a project subdirectory such as `generated/`.
- `validate-generated <generated-path>`
  - Validates an existing rendered bundle from `generated/`, one of its subdirectories, or a file under that tree.
- `terraform plan <generated-path>`
  - Infra-only plan from the generated Terraform bundle.
- `terraform destroy <generated-path>`
  - Destroys the generated Terraform bundle in place after an explicit confirmation or `--yes`.
- `terraform unlock <generated-path>`
  - Inspects and clears a stale remote Terraform state lock for the generated infra bundle.
- `flux destroy <generated-path>`
  - Deletes the rendered Flux resources from the target cluster after an explicit confirmation or `--yes`.
- `destroy <generated-path>`
  - Deletes rendered apps first and then destroys the rendered infra bundle after an explicit confirmation or `--yes`.
- `inventory write <generated-path>`
  - Refreshes local non-sensitive inventory files from the generated bundle.
- `email [generated-path]`
  - Sends only `generated/inventory/inventory.md` to `client_info.notifications.email` via SMTP and fails fast if that file is missing.
  - Omit the path only when using `--setup`.
  - The recipient email comes from the generated-bundle runtime config snapshot in `generated/nebius-cxcli-manifest.json`, not from the inventory artifacts.
  - SMTP is disabled by default. Run `nebius-cxcli email --setup` to create, update, or remove local SMTP settings under `~/.config/nebius-cxcli/email.yaml`.
  - Local email config stores host/port/STARTTLS/from and optional username/password. Runtime `SMTP_HOST`, `SMTP_PORT`, `SMTP_STARTTLS`, `SMTP_FROM`, `SMTP_USERNAME`, and `SMTP_PASSWORD` still override those local values when set.
  - Per-client send/no-send stays in `config.yaml`: `client_info.notifications.email_enabled: true|false`.
  - When `client_info.notifications.email_enabled` is `true` but SMTP is missing, the command warns and exits successfully instead of failing the deploy/email workflow.
  - The email path masks tenant and project identifiers in the subject/body down to their last 4 characters; the on-disk `inventory.md` stays unchanged.
- `auth`
  - Manages runtime auth profiles and optional GitHub environment secret sync.

Common command flags:

- `component add`:
  `--no-interactive`, `--validate-sources/--no-validate-sources`
- `component remove`:
  `--no-interactive`
- `create`:
  `--client-name`, `--tenant-id`, `--project-id`, `--region-id`, `--email`, `--infra`, `--app`, `--app-namespace`, `--app-releasename`, `--validate-sources/--no-validate-sources`, `--no-interactive`, `--force`
- `bootstrap-ci`:
  `--auth-bootstrap/--no-auth-bootstrap`, `--github-repo`, `--github-token-env`, `--cli-ref`
- `validate`: `--strict`
- Global source-selection for config-based commands: `--source-profile`, `NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE`
- `validate-generated`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, `--portable`
- `render`: `--force`
- `deploy`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`
- `destroy`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, `--yes`
- `discover`: `--all`
- `terraform plan`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`
- `terraform apply`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`
- `terraform destroy`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, `--yes`
- `terraform unlock`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, `--force`
- `flux apply`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`
- `flux destroy`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, `--yes`
- `flux bootstrap`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`
- `auth`:
  `--project-id`, `--project-config`, `--client-name`, `--profile`, `--endpoint`, `--sdk-config-file`, `--github-repo`, `--github-token-env`, `--validate-profile`, `--create`, `--recreate`, `--bootstrap-ci`

## Auth Workflow

Terraform runtime auth behavior:

- Generated `providers.tf` uses direct provider fields (`service_account.account_id/public_key_id/private_key_file`) and sets `module_name`.
- Generated `backend.tf` stores only non-secret backend location/settings; credentials are supplied by environment/runtime profile.
- Runtime values are passed through Terraform variables (`TF_VAR_*`) instead of provider `_env` indirection.
- Local runtime auth can be auto-bootstrapped with a dedicated service account name: `nebius-cxcli-tf-sa`.
- Auto-bootstrapped runtime auth material is cached under `~/.config/nebius-cxcli/<client_name>-<project-id>/` to avoid creating new key material every run.
- The auth key flow is authorized-key based: the CLI generates keypair material, uploads the public key for the service account, and stores private key material locally for Terraform runtime use.
- Terraform backend init uses AWS-compatible Object Storage keys (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`); the runtime auth profile cache auto-populates these for local runs.
- `render` generates `.terraform.lock.hcl` with backend-disabled `terraform init -backend=false` and removes transient `.terraform/` workdir state afterward, so render does not need runtime auth/bootstrap side effects just to pin providers.

`auth` behavior:

- `--create`:
  - Creates runtime auth profile only when local cache is missing.
  - If cache exists, it warns and does not rotate keys.
- `--recreate`:
  - Always rotates runtime auth profile material and refreshes cache + Nebius authorized key.
- `--validate-profile`:
  - Validates cached runtime auth profile state:
    - metadata file exists under `~/.config/nebius-cxcli/<client_name>-<project-id>/runtime-auth.json`
    - private key file exists locally
    - configured `auth_public_key_id` is still readable from Nebius IAM
  - When `--project-id` and `--project-config` are both omitted, validates every cached runtime auth profile under `~/.config/nebius-cxcli/`.
- `--bootstrap-ci`:
  - Syncs local runtime auth profile secrets to GitHub environment secrets.
  - GitHub environment name is `<client_name>-<project_id>`.
  - Requires existing local runtime auth profile (create first if missing).

`bootstrap-ci <config.yaml>` remains the full CI workflow bootstrap command and can still perform complete CI auth bootstrap/sync for that config. The generated customer workflow is artifact-driven: it watches and deploys only `generated/**`. Re-running the command automatically reconciles the CLI-managed workflow file to the latest template, always reconciles local SMTP settings into the matching GitHub Environment, and uses `--github-repo` only as an explicit override when repo auto-detection is wrong or unavailable.

`deploy <generated-dir>` is intentionally separate from `bootstrap-ci <config.yaml>`. Local/customer-side deploy commands operate only on the committed generated bundle and runtime auth material; they do not create or update GitHub workflows, GitHub environments, or CI secrets automatically.

Generated workflow CLI ref:

- The generated customer workflow installs `nebius-cxcli` from the ref resolved into its `NEBIUS_CXCLI_REF` workflow env.
- `bootstrap-ci --cli-ref <branch|tag|sha>` changes the default ref written into the workflow YAML.
- That ref points to the `nebius-cxcli` source in `nebius-ps-services`, not to the checked-out branch of the customer target repo.
- Example: `nebius-cxcli bootstrap-ci /path/to/config.yaml --cli-ref nebius-cxcli-v0.1.0`
- `bootstrap-ci` does not create the optional `NEBIUS_CXCLI_REF` GitHub repo/org variable; create that override manually only when you want to supersede the generated default ref without regenerating the workflow.
- `NEBIUS_CXCLI_REF: main` means the workflow installs the latest code from the repository main branch on each run. That is appropriate during active development before a release is cut.
- Customers do not need to set `NEBIUS_CXCLI_REF` before every workflow run. It is a persistent GitHub variable override, not a per-run input.
- To override the generated default later without editing the workflow YAML, create a GitHub repo/org variable `NEBIUS_CXCLI_REF`, for example:

```yaml
NEBIUS_CXCLI_REF: nebius-cxcli-v0.1.0
```

- Use a release tag or commit SHA in customer repos when you want reproducible CI behavior and do not want workflow runs to pick up new `main` branch changes automatically.

`deploy <generated-dir>` (default `--auto-auth-bootstrap`) uses the same runtime auth creation core as `auth --create` when auth material is missing.

This keeps repeated runs safe by default while still allowing explicit rotation.

## Examples

```bash
# Generator side: create and render artifacts
# Interactive create (default wizard mode)
nebius-cxcli create /path/to/deployments-root

# Day-2 config edits against an existing project
nebius-cxcli component list /path/to/config.yaml

# Interactive add of a new Terraform-module-backed infra component
nebius-cxcli component add /path/to/config.yaml

# Non-interactive add/remove
nebius-cxcli component add /path/to/config.yaml managed-postgresql --no-interactive
nebius-cxcli component add /path/to/config.yaml managed-postgresql object-storage@logs-bucket --no-interactive
nebius-cxcli component add /path/to/config.yaml gateway-helm --no-interactive
nebius-cxcli component remove /path/to/config.yaml managed-postgresql@managed-postgresql-2 --no-interactive
nebius-cxcli component remove /path/to/config.yaml gateway-helm --no-interactive

# Non-interactive create/reconcile
nebius-cxcli create /path/to/deployments-root \
  --client-name client-a \
  --tenant-id tenant-123 \
  --project-id project-123 \
  --infra mk8s \
  --app n8n \
  --app-namespace n8n=automation \
  --app-releasename n8n=workflow-core \
  --no-interactive

# Validate and render
nebius-cxcli validate --strict /path/to/config.yaml
nebius-cxcli render /path/to/config.yaml

# Local render against checked-out Terraform modules
nebius-cxcli --source-profile local validate --strict /path/to/config.yaml
nebius-cxcli --source-profile local render /path/to/config.yaml

# Customer side: validate and deploy generated bundle
# Validate generated bundle before deploy
nebius-cxcli validate-generated --portable /path/to/generated

# Local deploy from generated artifacts
nebius-cxcli deploy /path/to/generated

# Local destroy from generated artifacts
nebius-cxcli destroy /path/to/generated --yes

# Infra only
nebius-cxcli terraform plan /path/to/generated
nebius-cxcli terraform apply /path/to/generated
nebius-cxcli terraform destroy /path/to/generated --yes

# Safe to rerun sequentially after artifact changes or partial failures
nebius-cxcli terraform apply /path/to/generated

# If `terraform` is not in PATH, nebius-cxcli downloads the catalog-pinned
# Terraform CLI into its local cache automatically before these commands run.
nebius-cxcli terraform apply /path/to/generated

# Apps only: direct local apply to the target cluster
nebius-cxcli flux apply /path/to/generated
nebius-cxcli flux destroy /path/to/generated --yes

# Safe to rerun sequentially for day-2 chart reconciliation
nebius-cxcli flux apply /path/to/generated

# Apps only: GitOps bootstrap/reconcile path
nebius-cxcli flux bootstrap /path/to/generated

# If `flux` is not in PATH, nebius-cxcli downloads the catalog-pinned
# Flux CLI into its local cache automatically before bootstrapping.
nebius-cxcli flux bootstrap /path/to/generated

# Supporting: bootstrap generated-only customer CI workflow
nebius-cxcli bootstrap-ci /path/to/config.yaml

# Create local runtime auth profile (no rotation if already present)
nebius-cxcli auth --project-id project-123 --client-name client-a --create

# Force runtime auth profile rotation
nebius-cxcli auth --project-id project-123 --client-name client-a --recreate

# Validate runtime auth profile
nebius-cxcli auth --project-config /path/to/config.yaml --validate-profile

# Sync local auth profile to GitHub environment secrets
nebius-cxcli auth --project-config /path/to/config.yaml --bootstrap-ci --github-repo owner/repo
```

## Development

Python: `3.12+`

```bash
make venv
make lint
make all
```

Useful checks:

```bash
python -m nebius_cxcli --help
python -m nebius_cxcli create --help
python -m nebius_cxcli auth --help
```

`make lint` is the same Ruff gate used by the `nebius-cxcli-ci` workflow, so import ordering and loop-closure lint failures should be fixed locally before pushing.

Test suite focus:

- `tests/test_setup_build.py` isolates ambient CI build env vars so setup/build source-selection and ref-rewrite behavior are verified deterministically.
- `tests/test_cli.py` and `tests/test_cli_command_coverage.py` cover the command contract, including `bootstrap-ci`, global source-profile behavior, and generated-bundle validation paths.
- `tests/test_component_sources.py` covers source-catalog loading and `validate-sources` registry validation rules.
- `tests/test_github_secrets.py` covers GitHub repo/environment secret helper routing and environment-secret upsert orchestration.

Runtime plugin env knobs:

- `NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS`: comma-separated `module.path:function` plugins.
  - Default: none (core structural/runtime checks only).
- `NEBIUS_CXCLI_PROVIDER_OPTION_PLUGINS`: optional provider-option lookup plugins.
- `NEBIUS_CXCLI_STRICT_PROVIDER_OPTION_CHECKS=1`: enable live option membership checks in strict mode.

## Security Notes

- Keep deployment repositories private.
- Never commit credentials or secret values.
- The shipped catalogs avoid tenant/admin-specific key material. Project-scoped SSH public keys belong in the private deployment repo `config.yaml`.
- `config.yaml` is the canonical render/reset contract and should be versioned in the private deployment repo.
- `generated/` is the deploy contract and should also be versioned, except for ignored runtime/transient files.
- Managed deployments `.gitignore` keeps generated Terraform runtime files and generated tfvars out of git, but does not ignore `config.yaml` or deployable generated manifests.
- Keep `generated/infra/terraform.auto.tfvars.json` ignored even in a private repo: it is a generated, sensitive duplicate of values already present in `config.yaml`.
- Generated-bundle CLI commands such as `validate-generated`, `terraform plan/apply`, and `deploy` recreate `generated/infra/terraform.auto.tfvars.json` from `generated/nebius-cxcli-manifest.json` before Terraform runs, and generated workflows use those same commands instead of carrying separate inline restore logic.
- GitHub sync requires a token with permission to write GitHub environment secrets.
- Key rotation is explicit with `auth --recreate` and automatic in deploy only when runtime auth bootstrap is needed.
