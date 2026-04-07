# nebius-cxcli Design

## Table of Contents

- [Goal](#goal)
- [Architecture Summary](#architecture-summary)
- [Why Terraform Modules And Helm Charts Are The Contracts](#why-terraform-modules-and-helm-charts-are-the-contracts)
- [Runtime Source Model](#runtime-source-model)
- [Config Model](#config-model)
- [Command Workflow](#command-workflow)
- [Generator-side Commands](#generator-side-commands)
- [Customer-side Commands](#customer-side-commands)
- [Supporting Commands](#supporting-commands)
- [Idempotency Rules](#idempotency-rules)
- [Validation Model](#validation-model)
- [Render Model](#render-model)
- [Auth and CI Bootstrap Model](#auth-and-ci-bootstrap-model)
- [Vendor Scope](#vendor-scope)
- [Runtime Versioning](#runtime-versioning)
- [Source Code Structure](#source-code-structure)

## Goal

`nebius-cxcli` provides a single, repeatable operator workflow:

1. Create/reconcile one project configuration (`config.yaml`).
2. Adjust source-driven component selection in an existing `config.yaml` over time.
3. Validate generator-side configuration safety/readiness.
4. Render deterministic Terraform and Flux artifacts.
5. Commit the rendered customer artifact bundle.
6. Deploy from the generated bundle and/or bootstrap CI automation.

The design target is source-driven runtime behavior with minimal fixed component logic in core command paths.

## Architecture Summary

Core principles:

- `config.yaml` is the canonical render/reset contract.
- `generated/` is the deploy contract for customer repositories.
- Generator-side commands operate on `config.yaml`.
- Customer-side commands operate on `generated/`.
- Source-driven component discovery from `component_sources.yaml`.
- Runtime introspection for module/chart fields and chart dependencies.
- Progressive-enhancement wizard model: infra inputs come from Terraform module variables and app inputs come from Helm values, convention-friendly fields get Nebius/chart-aware UX automatically, and optional `wizard_fields` metadata is reserved for advanced integration or ambiguity. Complex Terraform types stay native and are entered as YAML/JSON values instead of being flattened into string-only prompts.
- Generic render path for Terraform modules/resources and Flux Helm releases.
- Optional plugin boundaries for provider-specific runtime option lookups and validation.

## Why Terraform Modules And Helm Charts Are The Contracts

This architecture intentionally uses three different contract layers:

- `config.yaml` is the operator-facing orchestration contract.
- Terraform modules are the infra provisioning contract.
- Helm charts are the app provisioning contract.

The CLI does not use the Nebius SDK as the primary infrastructure reconciler because that would force `nebius-cxcli` itself to become the state engine, diff engine, destroy engine, and portability boundary. Terraform already provides those semantics well:

- desired-state planning and apply/destroy behavior
- state, locking, and drift-aware reconciliation
- reusable module interfaces built from variables and outputs
- portable generated artifacts that can run later in CI or another machine

That matters for this repo because the generator/runtime split is central to the design: the CLI renders a deterministic deployable bundle under `generated/`, then later deploy/apply/destroy commands operate on that bundle rather than reinterpreting `config.yaml` every time.

Terraform modules therefore serve as the canonical infrastructure contract for each reusable infra component. The CLI reads module variables to discover editable inputs, uses Nebius APIs only to enrich the operator experience with dynamic choices and validation, and then renders a plain Terraform root module as the deployable output.

Helm charts serve the same role for apps:

- they preserve the native application deployment contract instead of inventing a CLI-specific app schema
- they keep workload packaging cluster-agnostic
- they let Flux/Helm remain the runtime owner of app reconciliation

The Nebius SDK still has an important role, but it is intentionally narrower:

- validate tenant/project scope
- discover dynamic provider-backed field options
- perform readiness/status polling and other runtime checks
- support auth/bootstrap and provider-specific guard rails

That split keeps the CLI generic while still taking advantage of Nebius-specific APIs where they add operator value.

The operator experience follows the same layered design:

- zero-config support for generic modules and charts through runtime introspection
- better UX for convention-friendly fields where names are enough to infer provider-backed choices
- explicit metadata only for advanced integration or ambiguous cases, via optional `wizard_fields`

## Runtime Source Model

Primary source registry (repo root): `component_sources.yaml`

- `component_sources.yaml` is the single source of truth for component metadata and managed tool versions.

Sections:

- `cli.flux.version`
- `cli.terraform.version`
- `infra.tf_modules[]`
  - `module`, required `portable_source`, optional `local_source`, `version`, `group`, `enable`, optional `wizard_fields`, optional `defaults`, optional `outputs`, optional `input`, optional `handoff`
- `apps.helm_charts[]`
  - `name`, `repo`, `version`, `namespace`, `releasename`, `group`, `enable`, optional `wizard_fields`, optional `defaults`, optional `outputs`, optional `input`
  - `repo` can be HTTP/S Helm repo base (must expose `index.yaml`), OCI (`oci://...`), or GitHub tree URL for git-hosted charts

`wizard_fields` is intentionally optional. It is not a second required schema that users must maintain when they add a module or chart. The default path is still introspection-first:

- new Terraform modules work from their variables
- new Helm charts work from their chart metadata and `values.yaml`
- optional `wizard_fields` only adds explicit hints when the generic or convention-based path is not enough

Source profile contract:

- `portable` is the default and always resolves Terraform modules from `portable_source`.
- `local` prefers `local_source` and falls back to `portable_source` when `local_source` is blank.
- The active profile is chosen globally by `--source-profile`, then `NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE`, then default `portable`.
- The root CLI help should state that default explicitly so workstation users do not assume `local`.
- Metadata discovery is allowed to prefer a resolvable `local_source` even when the active profile is `portable`, so local/CI validation can inspect module outputs and variables without paying remote Git probe cost for every catalog entry.

Source validation requirements (`validate-sources`):

- Terraform module sources (`infra.tf_modules[]`):
  - `module` token must match runtime component id format (lowercase letters/digits/hyphens).
  - `portable_source` is required.
  - `local_source` is optional.
  - `validate-sources` validates whichever module source is active for the resolved source profile.
  - Active local filesystem sources may be relative or absolute.
  - Relative local paths are resolved from the active `component_sources.yaml` file location first.
  - Active local sources must resolve to an existing directory with at least one `*.tf` file.
  - Every module source is install-checked with `terraform init -backend=false`, so broken remote refs and missing git/auth access fail before deploy-time commands start.
  - Fast module-contract validation also runs against the resolved module directory:
    - missing `versions.tf`, missing `required_version`, and missing `required_providers` are hard failures
    - provider/backend blocks inside child modules are hard failures
    - missing canonical files such as `main.tf`, `variables.tf`, `outputs.tf`, `README.md`, or runnable `examples/` roots are warnings
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
  - Fast chart-contract validation also materializes the resolved chart and checks for `Chart.yaml`, `values.yaml`, `templates/`, and essential `Chart.yaml` metadata (`apiVersion`, `name`, `version`).

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
```

This is an infra-to-runtime orchestration contract.
The Terraform module declares exported aliases under `outputs`, and `handoff.cluster_id` / `handoff.access` point to the aliases the CLI should use for cluster access after apply.
`handoff.access` can resolve from explicit access tokens (`external`/`internal`, `public`/`private`) or from a boolean public-endpoint flag.
Generic exported values do not replace the handoff block, because deploy/bootstrap still need an explicit runtime contract for kubeconfig setup.
Helm chart definitions stay cluster-agnostic; `deploy`/`flux bootstrap`/CI consume the handoff once and then run Flux/kubectl against that cluster.
Flux controller installation version for local `deploy` is configured in the same catalog under `cli.flux.version`.
Managed Terraform CLI download version is configured in the same catalog under `cli.terraform.version`.

Module outputs and handoff aliases must be treated as a versioned interface.
In practice that means aliases such as `cluster_id` and `access` are not just internal module details once the CLI, generated manifest, app bindings, or deploy/bootstrap flows consume them.
Renaming, removing, or changing the meaning/type of one of those aliases is a breaking contract change for the component, even if the underlying Terraform module still applies successfully.
When a module evolves, either keep the exported aliases stable or introduce the change as an explicit contract/version change rather than silently reusing the old component identity with different output semantics.

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
      portable_source: git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=main
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
- `validate` and `render` default to the `portable` source profile, so the normal generator path produces deployable artifacts rather than workstation-local Terraform module paths.
- Installed-package fallback is portable by default: when no external catalog override is present, the packaged `nebius_cxcli/component_sources.yaml` uses Git Terraform module sources.
- `--source-profile local` is the explicit escape hatch for workstation testing against checked-out Terraform modules.
- `--component-sources-file` and `NEBIUS_CXCLI_COMPONENT_SOURCES_FILE` remain catalog-selection overrides, not the primary portable-vs-local switch.
- `component_sources.yaml` stays semantically identical across local and portable use because both source forms live in one file; profile choice only changes transport, not feature contract.
- Customer-side commands that operate on `generated/` should not require the original render environment's local source catalog in order to resolve Terraform module paths.

Instance self-containment:

- `--component-sources-file` is a global optional override for the active source catalog path.
- When omitted, nebius-cxcli resolves the default file name `component_sources.yaml` from the standard search order above.
- `component_sources.yaml` is source input for `create` and for config-based runtime validation.
- `create` component selection uses the full resolved `component_sources.yaml` catalog.
- `component list`/`component add`/`component remove` also use the full resolved `component_sources.yaml` catalog against an existing `config.yaml`.
- In `component_sources.yaml`, `enable` controls default selection state only.
- `create` persists only selected `infra.components[]` and `apps.charts[]` rows in `config.yaml`.
- `component add` preserves existing rows and values, appends new selected rows, and prompts only for newly added component fields.
- `component add` validates `component_sources.yaml` by default, matching `create`; `--no-validate-sources` is the explicit escape hatch.
- `component add` also revalidates the existing Nebius tenant/project scope before provider-backed field prompts so dynamic option failures surface clearly.
- `component remove` deletes selected rows only when the resulting config still satisfies component bindings and chart dependencies.
- `config.yaml` does not embed `component_sources`.
- Config-based commands resolve sources from the active `component_sources.yaml` resolution path.
- Canonical project path is `<deployments-root>/projects/<client-name>--<tenant-id>/<project-id>/config.yaml`.
- App chart defaults (`namespace`, `releasename`) can be edited in wizard mode or overridden in non-interactive mode with `--app-namespace` and `--app-releasename`.

Wizard field/option model:

- Infra input fields are discovered from module `variables.tf` (required and optional variables for source-backed modules).
- Required variables are prioritized during prompts and enforced by strict validation.
- Runtime-required infra inputs can be promoted above raw Terraform metadata when the CLI needs a stronger contract for a specific component.
- Prompt labels include Terraform type hints (for example `string`, `number`, `bool`) and `required` markers.
- `create` validates `tenant_id` and `project_id` via Nebius IAM APIs before optional wizard phases.
- Interactive `component add` and `component remove` use separate infra/apps selection prompts and an explicit confirmation before editing `config.yaml`.
- For source-backed modules, `inputs.parent_id`/`inputs.project_id` are pre-seeded from `client_info.nebius.project_id` when those variables are present.
- `component_sources.yaml` can declare per-component `defaults` so known Terraform inputs and Helm values are pre-seeded before prompting; literal defaults still appear in the interactive wizard as editable current values.
- `component_sources.yaml` can declare top-level `shared` values, and `defaults` entries can reference them with `shared.<path>` so shared values flow into Terraform module inputs or Helm chart values without duplicating them in component config blocks.
- `shared` is catalog-only; `config.yaml` must not declare a root `shared` block.
- The shipped public catalogs should contain only non-sensitive shared defaults. Project-scoped SSH public keys for jump-host modules belong in the private project `config.yaml`, not in `component_sources.yaml`.
- The bundled `mk8s` source entry sets `defaults.inputs.mk8s_cluster_public_endpoint: true`, and the handoff contract resolves endpoint access dynamically from that input. If operators switch the control plane to private-only, local app operations still work as long as the machine running `nebius-cxcli` already has private network reachability to the MK8s API endpoint.
- The bundled `mk8s` source entry also sets `defaults.inputs.kube_network_service_cidrs: ["/20"]`. Nebius defaults omitted MK8s service CIDRs to `["/16"]`; on a single-pool `/16` subnet that can consume the entire pool and stall control-plane provisioning. `validate --strict` and `deploy` now preflight that case against the live subnet before Terraform apply.
- The bundled `mk8s` source entry also sets `defaults.inputs.cpu_nodes_count: 2`, so the baseline CPU node-group size is visible in `config.yaml` and editable in the wizard instead of coming from an implicit Terraform module default.
- `component_sources.yaml` can also declare producer-side `outputs` exports and consumer-side `input` bindings so component outputs feed other component inputs without adding hardcoded wiring to the CLI.
- Interactive field prompting now offers all discoverable required and optional component fields for newly selected components.
- Required fields are labeled `required` and must receive a valid value before the wizard advances unless the operator stops the wizard.
- Optional fields are labeled `optional`; blank answers keep defaults/current values and leave the field implicit in `config.yaml` when the value still matches a virtual module/chart default.
- Fields grouped behind a sibling `<prefix>_enabled` toggle are prompted only when that toggle is true; enabling the toggle during the wizard appends the dependent fields later in the same run.
- Empty optional complex defaults such as `{}` and `[]` are presented with a blank prompt default plus explicit “blank keeps current empty map/list” text, instead of rendering those literals as inline prompt defaults.
- When Terraform module metadata falls back to local `variables.tf` parsing, multiline default values such as map/object literals must still be parsed as full defaults so the interactive wizard does not emit truncated prompt values.
- If a selected module has no catalog default for a required field, `create` prompts for it and stores it in the per-project `config.yaml`. That is the canonical path for sensitive per-project values such as jump-host `ssh_public_key`.
- Wizard option sources are inferred by field conventions and resolved live via Nebius APIs when available.
- When a live provider-backed option lookup fails, the CLI prints a field-specific warning immediately before prompting that field manually and explains whether blank input is still acceptable.
- Optional provider-backed fields accept blank/skip answers as “leave unset” without revalidating that blank value against the live option list.
- Built-in Nebius provider option sources include:
  - `mk8s_compatible_platforms`
  - `compute_platforms`
  - `compute_platform_presets`
  - `project_subnets`
  - `project_networks`
  - `tenant_projects`
  - `mk8s_control_plane_versions`
- Wizard stop token is `q`; on exit, remaining fields keep defaults.
- Stopping the wizard never discards the current project config edit. `create` and `component add` persist the current payload and warn only when required fields remain unresolved; if only optional fields are skipped, no warning is emitted.

## Config Model

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
- `notifications.email_enabled`
- `notifications.email`
- `notifications.email_enabled` is the single per-client enable/disable switch for inventory email delivery across local runs and CI.
- In `create`, leaving the optional notifications email blank writes `notifications.email_enabled: false` and `notifications.email: null`.

Legacy `client_info.env` and `client_info.cluster_name` are not supported.

Canonical model is dynamic:

- `infra.components[]`: `id`, `enabled`, `inputs`
- `apps.charts[]`: `id`, `group`, `enabled`, `repo`, `version`, `namespace`, `release-name`, `values`
- Source catalogs use `releasename`; project `config.yaml` uses `release-name`. Alias keys are intentionally unsupported.
- Static nested component blocks are not accepted.

Commands operate from this dynamic model with infra source metadata resolved from the active `component_sources.yaml`, not pinned in `config.yaml`. New starter configs omit `infra.components[].source` and `infra.components[].version`.

## Command Workflow

The command boundary is intentional:

- Generator-side commands operate on `config.yaml`.
- Customer-side commands operate on `generated/`.
- Customer CI is artifact-driven and should deploy only from `generated/**`.
- `create` owns project identity and initial scaffold creation from a deployments root.
- When `create` targets an already-existing project config, interactive mode warns and asks for confirmation before reconcile/update continues. `--force` remains the explicit reset path.
- Interactive `create` also emits an early notice when the deployments root already contains one or more project configs and asks whether to continue before the identity prompts start.
- If interactive `create` finds exactly one existing project config in the deployments root and no explicit identity flags were passed, it offers that config's `client_name`, `tenant_id`, and `project_id` as the prompt defaults instead of starting those fields blank.
- After `create` writes the resulting `config.yaml`, it runs the same non-strict runtime validation as `validate` by default; `--no-validate-config` is the explicit escape hatch.
- `component add`/`component remove` are the day-2 config-editing commands for an already existing `config.yaml`.
- Live Helm chart defaults remain implicit in the chart and are not persisted into `config.yaml`; the wizard may surface them as prompt defaults, but only explicit chart overrides are written.
- CLI help should label positional targets explicitly as `DEPLOYMENTS_ROOT`, `CONFIG_YAML`,
  `GENERATED_PATH`, or `COMPONENT_SOURCES_YAML` so operators can tell the expected path type
  from the first `--help` screen.
  `auth` is the exception: it has no positional path and may also run `--validate-profile`
  across all cached profiles when no project/config target is provided.

### `create <deployments-root>`

- Creates or reconciles one project path and `config.yaml`.
- Wizard-first for identity and component prompts (unless `--no-interactive`).
- Uses source-driven infra/app entries.
- Resolves app dependencies from live Helm chart metadata (`Chart.yaml`) when available.
- Resolves infra field options from live Nebius APIs where option sources are inferred.
- This is the bootstrap path because it owns project identity discovery/validation and initial directory creation.

### `component list <config.yaml>`

- Read-only inspection of the current project component state against the active source catalog.
- Reports enabled component instances and reusable catalog component types, split between infra modules and app charts.

### `component add <config.yaml> [component-id...]`

- Adds source-defined components to an existing project config without rerunning `create`.
- Component catalog entries are reusable types; each add creates a distinct enabled component instance with its own `instance_id`.
- Interactive mode prompts separately for infra and apps selections when component ids are omitted.
- Interactive mode confirms the add before editing `config.yaml`.
- Auto-resolves app chart dependencies from chart metadata before persisting the updated selection.
- Runs the field wizard only for newly added components; existing component values remain untouched.
- The field wizard offers all discoverable required and optional fields for each newly added component, keeping module/chart defaults virtual unless the operator overrides them.
- Accepts complex Terraform inputs such as lists/maps/objects as YAML/JSON prompt values so reusable modules do not need CLI-specific scalar shims.
- Validates the active source catalog by default before editing `config.yaml`, matching `create`.
- Reuses the existing project tenant/project scope and validates it non-interactively before provider-backed prompts, instead of silently downgrading dynamic Nebius lookups.
- Non-interactive mode accepts one or more explicit infra-module ids or app-chart ids, repeats of the same id to create more than one component instance, and `<component-id>@<instance-id>` when the caller wants to control the new instance id explicitly.
- Supports `--validate-sources` for a full catalog preflight.

### `component remove <config.yaml> [component-id...]`

- Removes enabled component rows from an existing project config without rerunning `create`.
- Interactive mode prompts separately for infra and apps selections when component ids are omitted.
- Interactive mode confirms the removal before editing `config.yaml`.
- When more than one instance of the same component type is enabled, non-interactive remove must target an exact `instance_id` or `<component-id>@<instance-id>`.
- Fails fast when the resulting config would break app dependencies or component input bindings.

### `validate <config.yaml>`

- Core runtime/structural checks.
- Runs as explicit phases: config/catalog load, active source validation, dependency validation, then Terraform module schema validation.
- Phase progress must stay visible in both TTY and non-TTY runs so long validation windows are not silent.

### `validate-sources [component_sources.yaml]`

- Validates `component_sources.yaml`, resolved Terraform module sources, and resolved Helm chart sources.
- Keeps the check fast: source resolution, catalog shape, child-module/chart layout, and CLI-facing surface validation only. It does not replace full `terraform validate` in example roots or `helm lint`.
- Accepts an optional positional catalog path in addition to the global `--component-sources-file` override.

### `validate --strict <config.yaml>`

- Adds deployment-readiness checks:
  - placeholder rejection
  - chart source/dependency checks
  - module source and required-variable checks
  - provider-schema/resource checks when available
- Reuses the common runtime-validation result instead of rerunning the full common validation stack again before strict-only checks.

### `render <config.yaml>`

- Writes deterministic artifacts under `generated/infra`, `generated/flux`, and `generated/inventory`.
- Writes `generated/nebius-cxcli-manifest.json`, which snapshots the runtime config and deployment metadata needed to operate on the generated bundle later.
- Warns before overwriting an existing generated bundle, because rerendering is the replace path back to the original `config.yaml` contract.
- Renders into a hidden sibling staging directory first and swaps it into `generated/` only after the replacement bundle is complete, so a failed rerender leaves the current bundle intact.
- When Terraform is available from `PATH` or the managed download path, attempts backend-disabled `terraform init -backend=false` to produce/update `.terraform.lock.hcl`.
- Removes transient `.terraform/` workdir state after lockfile generation so the canonical rendered bundle stays clean.

### `validate-generated <generated-path>`

- Validates an existing generated artifact bundle without rerendering it.
- Runs Terraform validation against `generated/infra`.
- Runs `kubectl kustomize` against `generated/flux` when apps are enabled.
- Optional `--portable` enforcement rejects generated bundles whose Terraform root still embeds local filesystem module sources.
- Reports visible validation phases for backend auth preparation, Terraform validation, Flux manifest validation, and optional portability enforcement.

### `deploy <generated-path>`

- Deploys an existing generated bundle as a reconcile/apply path: terraform apply, inventory refresh, then local Flux apply.
- Ensures remote-state backend bucket exists before Terraform init/apply.
- Does not rerender from `config.yaml`.
- Uses `generated/nebius-cxcli-manifest.json` to recover the runtime config snapshot and deployment metadata.
- Uses `deploy.status_watchers[]` from the generated manifest to decide which Nebius SDK pollers to run for infra status reporting. Those watcher specs are derived from `infra.tf_modules[].status` in the active catalog at render time.
- Each watcher spec resolves `parent_id` and `resource_name` from the enabled component's `inputs` payload in `config.yaml`, following the catalog-declared `status.parent_input` and `status.name_input` paths.
- Service-specific pollers must read the Nebius SDK response shape for that API directly, rather than assuming a generic `items[]` field, so in-progress resources remain visible during long-running applies.
- When an older generated manifest does not contain watcher metadata yet, `deploy` may rebuild watcher specs from the loaded runtime config plus the active local catalog as a fallback.
- Must stay idempotent for the same generated bundle, but should not change into a create-only mode that ignores drift or desired updates to already managed resources.
- Operators who need a non-mutating preview should use `terraform plan` against the same generated bundle before `deploy`.

### `destroy <generated-path>`

- Destroys an existing generated bundle as the destructive inverse of `deploy`: delete rendered Flux resources from the target cluster first when apps are enabled, then run Terraform destroy against the rendered infra bundle.
- Does not rerender from `config.yaml`.
- Uses `generated/nebius-cxcli-manifest.json` to recover the runtime config snapshot and deployment metadata.
- Uses the same generated manifest watcher specs/runtime auth/backends as the apply path.
- Rendered app teardown is best-effort. If deleting the rendered Flux resources fails, the CLI warns and still continues with Terraform destroy because the rendered infra bundle remains the authoritative teardown path.
- Requires explicit confirmation in interactive mode and `--yes` in non-interactive mode.
- Does not uninstall Flux controllers or mutate GitHub workflow/bootstrap state.

`object-storage` is modeled as one bucket per enabled component instance. That keeps `config.yaml`, the field wizard, and the Terraform module contract aligned on scalar inputs like `inputs.name`, `inputs.versioning_policy`, and `inputs.protect_from_destroy` while still allowing multiple buckets in one project through distinct `instance_id` values.

Modules that expose collection/object inputs, such as `mysterybox.secrets`, `ssh-jumphost.allowed_cidrs`, `wireguard-jumphost.clients`, or MK8s override objects, should keep those Terraform-native shapes. The CLI is responsible for prompting them as YAML/JSON and for failing early on known effectively-required inputs instead of flattening module contracts to fit a scalar-only wizard.

### `bootstrap-ci <config.yaml>`

- Generates `.github/workflows/nebius-deployments.yml`.
- Re-running it automatically reconciles that CLI-managed workflow file to the latest template for the target repo/deployments path.
- Generated customer workflow is artifact-driven: it watches and deploys only `generated/**`.
- `config.yaml` remains in the customer repo as a manual render/replace contract and does not trigger customer CI deployment.
- The target `config.yaml` must already live inside the customer git repository because the workflow is written at that repo root.
- The command resolves the target GitHub repo from the checkout `origin` remote. `--github-repo` is only an explicit override for missing, non-GitHub, or remapped remotes.
- `--github-token-env` controls the GitHub API token used for workflow/environment reconciliation, SMTP sync, and optional Nebius auth bootstrap.
- Every run reconciles local SMTP settings from `nebius-cxcli email --setup` into the matching GitHub Environment, including removal of stale GitHub SMTP settings when local SMTP is disabled.
- `--auth-bootstrap` controls only Nebius CI auth bootstrap/rotation. Disabling it does not disable workflow reconcile or SMTP reconcile.
- Because SMTP reconciliation happens on every run, `bootstrap-ci` still requires GitHub API access even when `--no-auth-bootstrap` is used.
- When `--cli-ref` is omitted, the generated workflow defaults to `main` for development builds and `nebius-cxcli-v<version>` for stable tagged releases.
- `--cli-ref` is the explicit escape hatch when generator-side automation must pin the generated customer workflow to a specific nebius-cxcli branch, tag, or commit for PR validation.
- `--cli-ref` selects the `nebius-cxcli` source ref to install from `nebius-ps-services`; it does not select or mutate the branch of the customer target repo.
- Example: `nebius-cxcli bootstrap-ci /path/to/config.yaml --cli-ref <branch|tag|sha>`.
- Generated workflows also support a GitHub repo/org variable override `NEBIUS_CXCLI_REF`, which takes precedence over the generated default ref.
- The intended ref controls are generator-time pinning via `--cli-ref` and optional runtime override via the GitHub variable; editing the generated workflow YAML is not required for normal use.
- Optional CI auth/environment-secret bootstrap creates the GitHub Environment and syncs Environment Secrets, but does not manage GitHub repo/org variables.
- Generated workflows always run the inventory email step after apply. `client_info.notifications.email_enabled` is the single send/no-send switch; when enabled but SMTP is not configured, the step warns and continues.

### `auth` (flag-driven)

- `auth --create` creates runtime auth cache/profile only when missing.
- `auth --recreate` always rotates runtime auth material and rewrites cache.
- `auth --validate-profile` inspects cached runtime auth profile metadata/private key and verifies Nebius auth public key visibility.
- `auth --bootstrap-ci` syncs local runtime auth cache material into GitHub environment secrets.
- `auth --profile` and `auth --sdk-config-file` target Nebius SDK config resolution; they do not require the standalone `nebius` CLI binary.

## Generator-side Commands

- `validate-sources`
  - Validates the active source catalog contract and backing Terraform/Helm sources.
- `validate <config.yaml>`
  - Validates the project config contract before rendering.
  - Defaults to source profile `portable`; `--source-profile local` is available for local checked-out module workflows.
- `validate --strict <config.yaml>`
  - Adds deployment-readiness checks before rendering.
- `render <config.yaml>`
  - Produces the canonical generated Terraform/Flux/inventory bundle.
  - Recreates the managed `generated/` bundle from a clean layout without stale files and removes any legacy `generated/flux/flux-system` subtree.
  - Stages the replacement bundle under a hidden sibling directory and swaps it into `generated/` only after the staged bundle is complete.
  - Defaults to source profile `portable`; `--source-profile local` is explicit and produces non-portable generated Terraform sources for local testing.
  - If the target `generated/` bundle already exists, rerender is treated as a replace action:
    - interactive terminal: prompt before overwrite
    - non-interactive context: require `--force`

## Customer-side Commands

- `validate-generated <generated-path>`
  - Validates an already-rendered bundle without rerendering it.
  - CI and publish workflows should call `validate-generated --portable` before plan/apply.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation for Terraform validation (default enabled).
- `deploy <generated-path>`
  - Full local deployment from the generated bundle: Terraform first, then inventory refresh for infra and apps artifacts, then Flux direct apply.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default enabled).
  - Does not run `flux bootstrap`; GitOps bootstrap/reconcile stays explicit through `flux bootstrap` or the generated CI apply workflow.
  - Does not run `bootstrap-ci` automatically, even when the generated bundle is inside a git repository; GitHub workflow/environment bootstrap stays an explicit generator-side action.
- `destroy <generated-path>`
  - Full local teardown from the generated bundle: rendered apps first, then Terraform destroy for infra.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default enabled).
  - Requires explicit confirmation or `--yes`.
- `terraform apply <generated-path>`
  - Infra-only apply from the generated Terraform bundle.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default enabled).
- `terraform destroy <generated-path>`
  - Infra-only destroy from the generated Terraform bundle.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default enabled).
  - Requires explicit confirmation or `--yes`.
- `flux apply <generated-path>`
  - Apps-only direct apply from the generated Flux bundle.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default enabled).
- `flux destroy <generated-path>`
  - Apps-only direct delete from the generated Flux bundle.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default enabled).
  - Requires explicit confirmation or `--yes`.
- `flux bootstrap <generated-path>`
  - GitOps bootstrap/reconcile path from the generated Flux bundle.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default disabled).

## Supporting Commands

- `component list <config.yaml>`
  - Shows enabled and available catalog components for the current project.
- `component add <config.yaml>`
  - Day-2 config mutation path for adding source-defined components to an existing project.
- `component remove <config.yaml>`
  - Day-2 config mutation path for safely removing enabled components from an existing project.
- `create <deployments-root>`
  - Scaffolds or reconciles the project `config.yaml` and generated skeleton.
  - If the deployments root already contains project configs, interactive mode asks whether to continue before it starts prompting for project identity. Both that early guard and the later exact-project reconcile confirmation now default to continue.
  - If exactly one existing project config is present and no explicit `--client-name` / `--tenant-id` / `--project-id` flags were supplied, interactive mode offers those identity values as the prompt defaults.
  - Runs non-strict runtime validation on the resulting `config.yaml` by default.
- `bootstrap-ci <config.yaml>`
  - Generates or reconciles the customer workflow. The generated workflow watches and deploys only `generated/**`.
- `discover <deployment-scope-dir>`
  - Returns deployment-project discovery payload for CI.
  - Accepts the deployments root or any narrower directory under it, including one project directory or `generated/`.
  - Scope filtering remains project-aware for both `--all` and changed-only mode, so a scoped `generated/` directory still maps back to that project `config.yaml`.
- `terraform plan <generated-path>`
  - Infra-only plan from the generated Terraform bundle.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default enabled).
- `terraform unlock <generated-path>`
  - Clears a stale remote Terraform state lock for a generated infra bundle.
  - `--auto-auth-bootstrap/--no-auto-auth-bootstrap` controls runtime auth creation (default enabled).
  - `--force` overrides local safety checks and force-unlocks even when the lock owner is different or local processes are still active.
- `inventory write <generated-path>`
  - Refreshes local inventory files from the generated bundle.
- `email [generated-path]`
  - Sends `inventory.md` via SMTP and fails if the rendered markdown file is missing.
  - Omits the positional path only when `--setup` is used.
  - Reads the recipient from `client_info.notifications.email` in the generated-bundle runtime config snapshot, not from any inventory artifact.
  - SMTP is opt-in. Local operators enable it with `nebius-cxcli email --setup`, which writes `~/.config/nebius-cxcli/email.yaml` with host/port/STARTTLS/from and optional username/password.
  - Per-client delivery is controlled by `client_info.notifications.email_enabled` in `config.yaml`.
  - If email is enabled but SMTP is not configured, the command warns and exits successfully instead of failing the deploy/email flow.
  - Runtime `SMTP_*` environment variables override the local email config when present.
  - Masks tenant/project identifiers in the email subject/body while leaving the local `inventory.md` artifact unchanged on disk.
- `auth`
  - Manages runtime auth profiles and optional GitHub environment secret sync.

## Idempotency Rules

- `create`: idempotent reconcile by default; `--force` is explicit reset.
- `create --force`: deterministic reset for the same inputs, but intentionally bypasses the safer default reconcile/update flow.
- `component list`: read-only; safe to repeat.
- `component add`: additive, not type-idempotent; repeating a component type creates another instance. Existing rows and values remain untouched unless a new instance is explicitly added.
- `component remove`: idempotent for already-absent components; removal is blocked when it would violate dependency contracts.
- `validate-sources`: read-only; safe to repeat.
- `validate`/`render`: deterministic and repeatable.
- `render --force`: same rendered output for the same config, but intentionally bypasses the interactive overwrite confirmation.
- `validate-generated`: deterministic for a given generated bundle.
- `discover`: read-only; safe to repeat.
- `deploy`: convergent behavior expected from apply/reconcile against a fixed generated bundle.
- `destroy`: sequentially convergent for a fixed generated bundle, but intentionally destructive and confirmation-gated.
- `terraform plan`: read-only; safe to repeat.
- `terraform apply`: sequentially convergent for a fixed generated bundle; Terraform backend locking intentionally prevents concurrent mutation against the same state.
- `terraform destroy`: sequentially convergent for a fixed generated bundle, but intentionally destructive and confirmation-gated.
- `terraform unlock`: operationally idempotent; once a stale lock is cleared, reruns report that no lock is present.
- `flux apply`: sequentially convergent for a fixed generated bundle.
- `flux destroy`: sequentially convergent for a fixed generated bundle, but intentionally destructive and confirmation-gated.
- `flux bootstrap`: bootstrap once, reconcile on rerun when the cluster is already GitOps-bootstrapped.
- `inventory write`: deterministic local rewrite for a fixed generated bundle.
- `email --setup`: local SMTP-config reconcile; repeating the same answers leaves the same config on disk.
- `email`: intentionally not idempotent because each successful run sends another message.
- `bootstrap-ci`: idempotent reconcile; reruns auto-update the CLI-managed customer workflow and re-check GitHub environment secret presence.
- `auth --create`: idempotent create-if-missing.
- `auth --recreate`: explicit rotation path.
- `auth --validate-profile`: read-only profile validation; safe to re-run.
- `auth --bootstrap-ci`: idempotent environment-secret upsert from local cache.
- `deploy` and other customer-side generated-bundle commands do not mutate GitHub CI state as a side effect.

## Validation Model

Validation layers:

1. Structural/runtime config checks (`runtime_validation.py`).
2. Dynamic payload shape checks (`validate_dynamic_payload_structure`).
3. Strict checks in CLI for deployment readiness.
4. Optional plugin validation via `NEBIUS_CXCLI_RUNTIME_VALIDATION_PLUGINS`.

Plugin default:

- Default runtime validation plugins are disabled.
- Operators can enable custom/provider-specific rule packs explicitly.

## Render Model

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
- `render` with source profile `portable` is the default and rewrites active local developer sources to `portable_source` when a matching portable source exists.
- `render` with source profile `local` preserves resolved filesystem module paths for workstation testing and is intentionally non-portable.
- `component_sources.yaml` is the only checked-in catalog; build/package steps strip `local_source` when bundling the portable wheel catalog.
- Release workflows rewrite internal `portable_source` refs from `?ref=main` to the current tag or commit before publishing.
- Generator-side commands use the global source profile to choose portable vs local output, and use `--component-sources-file` only when they need to override which catalog file is active.
- Deterministic output files:
  - `generated/nebius-cxcli-manifest.json`
  - `generated/infra/backend.tf`
  - `generated/infra/versions.tf`
  - `generated/infra/providers.tf`
  - `generated/infra/variables.tf`
  - `generated/infra/main.tf`
  - `generated/infra/outputs.tf`
  - `generated/infra/terraform.auto.tfvars.json`
  - `generated/infra/.terraform.lock.hcl` (generated by backend-disabled `terraform init -backend=false` during CLI `render` when Terraform is available)
- Remote-state backend is distinct from app/object-storage components:
  - Bucket/key/endpoint settings are derived from `client_info` (`client_name`, `project_id`, `region_id`).
  - `infra.components[id=object-storage]` remains workload/application storage only.
- Before backend-enabled Terraform init paths (`validate-generated`, `terraform plan`, `terraform apply`, `deploy`), CLI ensures the backend bucket exists via Nebius Storage API.
- Backend lock recovery is explicit: `terraform unlock <generated-dir>` inspects the remote `.tflock` object for the rendered backend and then uses Terraform `force-unlock` only when the lock appears stale. By default it refuses to unlock while local Terraform/deploy operations are still active or when the recorded lock owner differs from the current local identity.
- `terraform unlock` still requires `aws` CLI in `PATH`; Terraform itself may come from `PATH` or the managed Terraform download path.
- Local `deploy` validates the rendered Terraform root before apply, then if enabled charts and a `handoff`-enabled infra component are present it resolves the rendered cluster ID output and prepares kubeconfig before applying rendered Flux manifests.
- Customer-side commands operate on the rendered `generated/` bundle as the deploy contract and do not need the source catalog to recover local Terraform module paths from the original render machine.
- On non-CI local runs, that same cluster handoff also updates the user kubeconfig at `~/.kube/config` with a `nebius-cxcli` exec-based credential entry, so the target MK8s cluster is immediately usable with `kubectl` after `deploy`, `flux apply`, or `flux bootstrap` without a separate Nebius CLI install.
- Only `deploy`, `flux apply`, and `flux bootstrap` persist that local kubeconfig handoff. `destroy` and `flux destroy` use only a temporary kubeconfig because they need cluster access for rendered app teardown but should not switch the operator's local current-context as a side effect.
- The bundled MK8s handoff no longer hardcodes public access. It resolves the endpoint choice from `inputs.mk8s_cluster_public_endpoint`, so the CLI selects the private API endpoint automatically when the cluster is configured private-only.
- Private-endpoint handoff is supported, but reachability is still an environment concern. `nebius-cxcli` fails early with a targeted message when `kubectl` cannot reach a private control-plane endpoint; operators must provide that path through their own VPN, routed private network, tunnel, subnet router, or an in-network runner.
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
- GitOps safety comes from publishing one final watched-path snapshot, not from tearing Flux down. Normal updates should rerender locally, review the `generated/` diff, and push a single commit; do not publish an intermediate manifest-deletion commit and do not routinely unbootstrap/rebootstrap Flux to replace rendered artifacts.
- Local kubeconfig persistence is skipped automatically in CI and can be disabled explicitly with `NEBIUS_CXCLI_PERSIST_LOCAL_KUBECONFIG=false`.
- `flux bootstrap` still depends on GitHub release availability when the managed Flux CLI download path is used.

Managed vs external local tooling:

- Auto-managed by the CLI when missing:
  - `terraform` for Terraform-backed validation, render lockfile generation, `terraform plan`, `terraform apply`, `terraform unlock`, and backend-backed Terraform output reads
  - `flux` for `flux bootstrap`
- Still external prerequisites:
  - `kubectl` for `deploy`, `destroy`, `flux apply`, `flux destroy`, `flux bootstrap`, and Flux readiness probes
  - Nebius SDK auth for kubeconfig handoff against handoff-enabled cluster components; the standalone `nebius` CLI is only an optional auth-token fallback, not a runtime dependency for cluster API handoff
  - `helm` for strict Helm source validation
  - `aws` CLI for `terraform unlock` remote lock inspection

Flux render:

- Generic Helm source docs (`HelmRepository` HTTP/OCI or `GitRepository` for standalone chart sources).
- Inventory artifacts are part of the canonical generated output set as well:
  - `generated/inventory/inventory.md`
- `inventory.md` is the human-readable inventory and the body used by the `email` command.
- `render`, `deploy`, `terraform apply`, `flux apply`, `flux bootstrap`, and `inventory write` refresh those inventory artifacts for the active project.
- Those refreshes also delete stale legacy inventory JSON files.
- Explicit Namespace docs for chart target namespaces.
- Generic HelmRelease docs from enabled app releases.
- Deterministic flat output under `generated/flux`:
  - `helm-repositories.yaml`
  - `namespace-<namespace>.yaml`
  - `helmrelease-<group>-<release>.yaml`
  - `kustomization.yaml`
- Legacy nested Flux layout (`generated/flux/apps` and `generated/flux/sources`) is not supported.

## Auth and CI Bootstrap Model

`bootstrap-ci`:

- Generates workflow file.
- Treats `.github/workflows/nebius-deployments.yml` as a CLI-managed file and automatically reconciles it to the latest generated contract on every rerun.
- Requires the target config path to be inside the customer git repository so the workflow can be written at the repo root.
- Auto-detects the target GitHub repo from the checkout `origin` remote unless `--github-repo` overrides it.
- Fails before writing the workflow if GitHub reconciliation prerequisites are missing.
- Derives GitHub environment name as `<client_name>-<project_id>`, ensures that environment exists, then reconciles SMTP settings on every run and optionally Nebius CI auth secrets when `--auth-bootstrap` is enabled.
- Generated customer workflows validate with `nebius-cxcli validate-generated --portable` before Terraform plan/apply so non-portable local module paths are rejected in PRs and main-branch deploy runs.
- Generated customer workflows rely on the same generated-bundle CLI commands, which recreate ignored `generated/infra/terraform.auto.tfvars.json` from `generated/nebius-cxcli-manifest.json` before Terraform runs.
- Generated customer workflows do not install the standalone `nebius` CLI; MK8s kubeconfig handoff and token retrieval stay inside `nebius-cxcli` via the Nebius SDK.
- Generated customer workflows install `kubectl` directly from upstream Kubernetes release binaries instead of `azure/setup-kubectl`, avoiding GitHub Actions Node runtime deprecation coupling.
- Generated customer workflows also keep the Python runtime version in one env var and write compact single-line discovery JSON to `GITHUB_OUTPUT` for stable matrix handoff.
- Does not manage GitHub repo/org variables; `NEBIUS_CXCLI_REF` remains an optional manual override consumed by the generated workflow.
- `generated/infra/terraform.auto.tfvars.json` remains ignored in private deployment repos; generated-bundle CLI commands recreate it from `generated/nebius-cxcli-manifest.json` before Terraform runs so CI does not depend on a committed tfvars file or duplicate that restore logic in workflow YAML.

`auth`:

- Reads `~/.config/nebius-cxcli/<client_name>-<project-id>/runtime-auth.json`.
- `--create`: creates runtime auth profile if cache is missing; otherwise no rotation.
- `--recreate`: always rotates keys and refreshes cached material.
- `--validate-profile`: checks local private key presence and verifies auth public key visibility via Nebius IAM API.
  When no project/config target is provided, it validates every cached runtime auth profile.
- `--bootstrap-ci`: syncs local cached auth material into GitHub environment secrets (`<client_name>-<project_id>`); requires existing local cache material.

Terraform runtime auth:

- Generated `providers.tf` uses direct Nebius provider service-account fields and `module_name`.
- Runtime auth material is passed to Terraform via `TF_VAR_*` rather than provider `_env` fields.
- Runtime auto-bootstrap uses dedicated service account name `nebius-cxcli-tf-sa`.
- Auto-bootstrapped runtime auth material is cached under `~/.config/nebius-cxcli/<client_name>-<project-id>/`.
- Terraform backend path requires AWS-compatible Object Storage keys (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`); runtime auth cache provides them automatically when bootstrapped.

## Vendor Scope

Current runtime implementation is Nebius-focused:

- Nebius SDK/API integration for auth/IAM and provider option lookups.
- Nebius-oriented defaults for provider/config behavior.

The component source model itself is Terraform-module + Helm-chart based, but this release does not claim full multi-vendor runtime support.

## Runtime Versioning

- Installed wheels rely on package metadata for the published version.
- Source/editable checkouts prefer live SCM state over a generated `_version.py` cache: they use `setuptools-scm` when available and fall back to `git describe` when it is not, so local runtime behavior still tracks the current repo state even in minimal release-shell environments.
- `publish-release.sh --prep X.Y.Z` fails before editing `CHANGELOG.md` if the target tag already exists locally or on `origin`, so duplicate release-prep runs stop before producing a redundant changelog commit.
- `publish-release.sh --prep X.Y.Z` is otherwise idempotent while the target tag remains unreleased: once `Unreleased` is empty, reruns leave `CHANGELOG.md` and `HEAD` unchanged.
- `publish-release.sh --publish X.Y.Z` creates the service tag locally, verifies that the tagged source checkout resolves `nebius_cxcli.__version__ == X.Y.Z`, and only then pushes the tag to trigger the release workflow.

## Source Code Structure

- `setup.py`: wheel-build hook that bundles the portable `component_sources.yaml` view and rewrites internal release refs for published artifacts.
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
- `src/nebius_cxcli/paths.py`: project path resolution and alignment checks.
- `src/nebius_cxcli/inventory_ops.py`: inventory write operations.
- `src/nebius_cxcli/notify_ops.py`: email notification operations.
- `component_sources.yaml`: repo-level starter source registry editable by operators.
- `<install-prefix>/nebius_cxcli/component_sources.yaml` (wheel data-file): bundled fallback source registry shipped inside wheel builds.
- The `nebius-cxcli` GitHub release workflow publishes both the wheel and the raw portable catalog file so operators can download the editable source catalog directly from the release page with module refs already pinned to the published release tag.
- The repo CI and release workflows run the same local `make all` verification contract before wheel verification or release publication so GitHub Actions and local development stay on one lint/test/build path.
- After `make all`, those workflows also run `validate-sources component_sources.yaml` against the active portable catalog so real Terraform-module and Helm-chart source contracts are checked in automation, not only in unit tests.
- Post-`make all` workflow verification uses the repo `.venv/bin/python` for `nebius_cxcli.release_catalog` commands so wheel/catalog checks import the checked-out editable package reliably under GitHub Actions.

Primary automated test ownership:

- `tests/test_cli.py` and `tests/test_cli_command_coverage.py`: CLI command contract and workflow-generation behavior.
- `tests/test_component_sources.py`: component source precedence and validation rules, including `validate-sources` registry checks.
- `tests/test_github_secrets.py`: GitHub repo/environment secret helper behavior, including environment creation and environment-secret upsert orchestration.
- `tests/test_setup_build.py`: setup/build packaging contract, with CI build env isolated so source selection and release-ref rewrite precedence stay deterministic under GitHub Actions.
