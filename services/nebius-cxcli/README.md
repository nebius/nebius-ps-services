# nebius-cxcli

`nebius-cxcli` is the Nebius customer experience CLI and an end-to-end automation workflow generator. From one per-project `config.yaml`, it renders a deployable customer artifact bundle: Terraform, Flux, inventory, and CI workflow artifacts.

After render, deployment still operates on the generated bundle. `deploy` takes
`config.yaml`, resolves sibling `generated/`, and uses the generated manifest as
the authoritative deploy contract so source changes after render do not silently
change what gets applied.

## Quick Start Guide

```bash
nebius-cxcli --version
nebius-cxcli --help
nebius-cxcli create <target-path>
nebius-cxcli quota-check <config.yaml>
nebius-cxcli quota-request <config.yaml>
nebius-cxcli render <config.yaml>
nebius-cxcli deploy <config.yaml>
nebius-cxcli bootstrap-ci <config.yaml>
```

- `nebius-cxcli --version`: print the installed CLI version.
- `nebius-cxcli --help`: show the command surface and path contracts.
- `nebius-cxcli create <target-path>`: create one tenant/project folder scaffold under a deployments root; reruns overwrite only after confirmation.
- `nebius-cxcli quota-check <config.yaml>`: run a live Nebius quota/capacity assessment for the enabled infra components in one project config.
- `nebius-cxcli quota-request <config.yaml>`: plan and attempt direct quota allowance requests for the confirmed live shortages in one project config, while still printing unresolved coverage gaps and any manual follow-up that remains.
- `nebius-cxcli render <config.yaml>`: turn one project config into a deployable `generated/` bundle.
- `nebius-cxcli deploy <config.yaml>`: resolve the sibling rendered bundle and apply it to Nebius and the target cluster.
- `nebius-cxcli bootstrap-ci <config.yaml>`: generate or reconcile the customer CI workflow for that project.

The current implementation is provider-driven and source-configured for Nebius environments:

- Infra components come from Terraform module sources.
- App components come from Helm chart sources.
- Runtime options and dependency checks use live provider/chart metadata where available.
- Canonical project config model is dynamic: `infra.components[]` and `apps.charts[]`.

Architecture rationale:

- `config.yaml` is the operator-facing orchestration contract, while Terraform modules and Helm charts are the provisioning contracts.
- Terraform modules are used for infra because they provide desired-state planning, apply/destroy behavior, state/locking, reusable variable/output interfaces, and portable generated artifacts; the Nebius SDK is used for dynamic discovery, validation, status polling, and guard rails rather than replacing Terraform as the reconciler.
- Helm charts are used for apps because they preserve the native app deployment contract and keep workloads cluster-agnostic while Flux/Helm remain the runtime owners of app reconciliation.
- Component UX follows a progressive-enhancement model: generic Terraform modules and Helm charts should work with zero extra mapping, and optional `wizard` metadata is reserved for explicit Nebius-backed field choices or other advanced integration.
- Terraform outputs consumed by app bindings or built-in deploy/bootstrap behavior are treated as a stable interface. Renaming or changing one is a breaking contract change, not an internal refactor.

## Table of Contents

- [Quick Start Guide](#quick-start-guide)
- [Features](#features)
- [Runtime Metadata](#runtime-metadata)
- [component_sources.yaml Reference](#component_sourcesyaml-reference)
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
- `create` is the bootstrap path for one tenant/project folder with `config.yaml` plus the generated-folder scaffold under a deployments root.
- Re-running `create` against an existing resolved `tenant_id/project_id` target no longer reconciles existing component state. Interactive runs warn and ask for overwrite confirmation; non-interactive reruns require `--force`.
- Interactive `create` now resolves `tenant_id`/`project_id` before any overwrite warning. Reusing an existing resolved project still requires explicit confirmation, while choosing a different new project under the same deployments root does not trigger a pre-warning.
- Interactive `create` treats project identity as new by default: unless you explicitly pass `--tenant-id` / `--project-id`, the wizard starts those prompts blank even when the deployments root already contains other projects.
- `create` writes dynamic component state (`infra.components[]`, `apps.charts[]`).
- When `create` overwrites an existing `tenant_id/project_id` target, it recreates that one resolved tenant/project folder from scratch, restarts client-info prompts from the normal create defaults, and rebuilds infra/apps selections plus component values from the current create inputs.
- `component list`, `component add`, and `component remove` are the day-2 config-editing surface for both infra modules and app charts in existing projects.
- `component_sources.yaml` defines reusable component types; `config.yaml` stores enabled component instances with unique `instance_id` values, so the same type can be added more than once.
- `component add` preserves existing values, resolves app chart dependencies, and only prompts for newly added component instance fields.
- Live Helm chart default values are treated as prompt-time defaults only. `config.yaml` stores explicit chart overrides, not the chart's full default values tree.
- `component add` validates `component_sources.yaml` by default and supports `--no-validate-sources` when you explicitly want to skip that preflight.
- `component add` also validates the existing `tenant_id`/`project_id` scope before provider-backed wizard fields run, so Nebius-backed dynamic options fail clearly instead of silently degrading.
- `component remove` blocks changes that would leave unresolved component bindings or dependency breakage in `config.yaml`.
- `create` validates `component_sources.yaml` by default (`--no-validate-sources` to skip).
- `create` also runs the non-strict `validate` pass against the resulting `config.yaml` by default (`--no-validate-config` to skip).
- `create` now performs a best-effort live Nebius quota assessment for bundled infra components and warns when current tenant/project quota is already insufficient, without blocking the config workflow.
- `create`, `render`, and `bootstrap-ci` auto-manage a deployments-root `.gitignore` block when target path is inside a git repo (keeps `config.yaml` and deployable generated artifacts versioned while ignoring Terraform transient/runtime files and generated tfvars only; it does not try to become a generic developer `.gitignore` for things like coverage files or packaged chart archives).
- App dependency resolution from Helm `Chart.yaml` metadata.
- Interactive wizard phase prompts still use `q` to stop the wizard, while answering `n` to a component-level `Configure '<component>' component fields now?` prompt skips only that component and continues with the remaining selected components. Field prompts use `q` to go back: flat Terraform module-input prompts revisit the previous prompt, nested value/object prompts back out of the current prompt-prefix branch, and `qq` stops the wizard entirely.
- `create` and `component add` still save the edited `config.yaml` when the wizard is stopped; they warn only when required fields are still missing and stay quiet when only optional fields remain at defaults.
- `create` validates `tenant_id`/`project_id` against Nebius IAM APIs before continuing.
- Infra field options are resolved dynamically from Nebius APIs where supported.
- Flux output is flat under `generated/flux` (no `apps/` or `sources/` subdirectories).
- `validate` runtime checks plus a warning-only live quota phase, and `validate --strict` deployment-readiness checks that fail on confirmed live quota insufficiency.
- `render` writes deterministic Terraform, Flux, inventory, and `generated/nebius-cxcli-manifest.json`.
- `render` now stages a full replacement bundle under a hidden sibling directory and swaps it into `generated/` only after the new bundle is complete.
- `render` rechecks live Nebius quota before promoting the staged bundle, persists the quota report into `generated/nebius-cxcli-manifest.json`, and still completes with warnings when quota is insufficient or only partially known.
- Rerender still recreates the managed generated bundle from a clean layout and removes stale or legacy content under `generated/`, including an old `generated/flux/flux-system` subtree.
- `render` warns before overwriting existing generated artifacts, so rerendering is still an explicit replace action driven from the original `config.yaml` contract.
- Generated-bundle CLI commands recreate ignored `generated/infra/terraform.auto.tfvars.json` from the committed manifest before Terraform runs, so deployable repos and generated workflows do not need to version that sensitive duplicate file.
- `deploy`, `destroy`, `report write`, and `email` now take project `config.yaml` as the CLI entrypoint, resolve sibling `generated/` automatically, and still operate on the rendered bundle/runtime snapshot rather than rereading post-render source edits. Lower-level `validate-generated`, `terraform *`, and `flux *` commands continue to target `generated/` directly.
- `deploy` rechecks live Nebius quota from the generated bundle snapshot and fails fast before Terraform apply when the required quota is still insufficient.
- `deploy` also keeps the MK8s terminal-error fail-fast path scoped to the current run: fresh node-group API errors still abort early, but stale old errors from a previously failed node-group instance do not block Terraform replacement from starting.
- `terraform apply`, `terraform destroy`, `flux apply`, `flux destroy`, `deploy`, and `destroy` are designed for sequential reruns against the same generated bundle; destroy commands remain explicitly destructive and require confirmation or `--yes`.
- `bootstrap-ci` generates or reconciles the customer CI workflow, always reconciles GitHub email settings from local `email --setup`, and optionally bootstraps/syncs Nebius CI auth secrets.
- `discover` outputs deployment-project discovery JSON with `config`, `generated`, `config_changed`, `generated_changed`, and `github_environment`.

## Runtime Metadata

Primary source file (repo root):

- `component_sources.yaml` is the single source of truth for component and managed-tool metadata.

Schema:

- `cli.flux.version`: Flux controller install version used by local `deploy` when controllers are missing and by managed `flux bootstrap` CLI download
- `cli.flux.release_timeout`: global default Flux `HelmRelease.spec.timeout` for rendered app releases when a chart does not set `release.timeout`
- `cli.terraform.version`: Terraform CLI version used by the managed Terraform download path
- `components.infra.<component-id>`: `source.portable`, optional `source.local`, optional `ui`, optional `status`, optional `defaults`, optional component-local `cli`, optional `wizard_profile`, optional `wizard`, optional `input`
- `components.apps.<component-id>`: optional `source.portable`, optional `source.local`, optional `ui`, optional `release`, optional `defaults`, optional component-local `cli`, optional `input`

## component_sources.yaml Reference

`component_sources.yaml` uses a strict schema. Unsupported keys are rejected at load time rather than silently ignored.

Minimal structure:

```yaml
cli:
  terraform:
    version: 1.14.1
  flux:
    version: v2.8.0
    release_timeout: 5m
shared:
  admin_ssh:
    user_name: ubuntu
    public_key: ~/.ssh/id_ed25519.pub

components:
  infra:
    mk8s:
      source:
        portable: git::https://github.com/org/repo.git//modules/mk8s?ref=v1.2.3
      defaults:
        inputs.gpu_stack_source: nebius_image
      cli:
        gpu:
          image_preferences:
            preferred_gpu_stack_presets: [cuda13.0, cuda12.8, cuda12.4, cuda12]
            preferred_os: [ubuntu24.04, ubuntu22.04]
          validations:
            operator_readiness:
              enabled_by_default: true
              timeout: 10m
            gpu_visibility:
              enabled_by_default: true
              namespace: gpu-validation
              image: nvcr.io/nvidia/k8s/cuda-sample:vectoradd-cuda11.7.1-ubuntu20.04
              timeout: 5m
              max_nodes: 3
        boot_disk_defaults:
          cpu:
            default_type: NETWORK_SSD
            rules:
              - max_vcpu: 8
                max_memory_gib: 32
                size_gib: 64
              - max_vcpu: 32
                max_memory_gib: 128
                size_gib: 93
          gpu:
            default_type: NETWORK_SSD
            rules:
              - max_gpu: 1
                max_vcpu: 32
                max_memory_gib: 384
                size_gib: 256
              - min_gpu: 8
                max_gpu: 8
                size_gib: 1023
    vm:
      source:
        portable: git::https://github.com/org/repo.git//modules/vm?ref=v1.2.3
      cli:
        image_preferences:
          preferred_cpu_image_families: [ubuntu24.04-driverless, ubuntu22.04-driverless]
          preferred_gpu_image_families: [ubuntu24.04-cuda13.0, ubuntu24.04-cuda12]

  apps:
    nvidia-network-operator:
      source:
        portable:
          repo: oci://example.invalid/network-operator
          chart: network-operator
          version: 1.0.0
      release:
        namespace: nvidia-network-operator
        name: network-operator
      cli:
        mk8s_gpu_policy:
          role: network_operator
          rules:
            - gpu_cluster_enabled: true
              auto_enable: true
    nvidia-gpu-operator:
      source:
        portable:
          repo: oci://example.invalid/gpu-operator
          chart: gpu-operator
          version: 1.0.0
      release:
        namespace: nvidia-gpu-operator
        name: gpu-operator
      cli:
        mk8s_gpu_policy:
          role: gpu_operator
          rules:
            - auto_enable: true
            - gpu_stack_source: nebius_image
              defaults:
                values.driver.enabled: false
          install_after: [nvidia-network-operator]
```

Field guide:

- Root blocks:
  - `cli`: managed tool versions bundled into the catalog contract.
  - `shared`: reusable shared values. Today the supported shape is `shared.admin_ssh.{user_name,public_key}`. `public_key` accepts either an inline `ssh-rsa` / `ssh-ed25519` public key or a readable local `.pub` file path such as `~/.ssh/id_ed25519.pub`.
  - `components`: source registry split into `infra` and `apps`.
- `components.infra.<component-id>`:
  - `<component-id>` must use lowercase letters, digits, and hyphens.
  - `source.portable`: required portable Terraform module source.
  - `source.local`: optional workstation-local Terraform module path used by the `local` source profile.
  - `ui.title`, `ui.group`, `ui.enabled`: display metadata and default wizard checkbox state.
  - `status`: optional Nebius deployment-status watcher metadata. When present, `status.kind` is required, `status.parent_input` defaults to `parent_id`, and `status.name_input` defaults to `name`.
  - `defaults`: target-path map for seeded or fallback values. Infra defaults must target `inputs.*`.
  - `cli`: optional cxcli-owned policy for that component. The bundled `mk8s` component uses `cli.gpu.*` for MK8s GPU image preferences and deploy-time validation defaults, and `cli.boot_disk_defaults.<cpu|gpu>` for cxcli-owned explicit boot-disk defaults. cxcli resolves the selected preset's live resources (vCPU, RAM, GPU count), matches the first ordered `rules` entry whose bounds fit that shape, and materializes explicit `inputs.<cpu|gpu>_nodes_boot_disk_*` values from the matched size plus disk type. If no rule matches, cxcli falls back to a heuristic so quota checks still have a deterministic size. Explicit first-class inputs or `template.boot_disk` overrides remain authoritative. The bundled `vm` component uses `cli.image_preferences.*` for VM public-image ordering.
  - `wizard_profile`: optional built-in shorthand that expands to a tested `wizard` mapping for that exact infra component id.
  - `wizard`: optional prompt metadata keyed by target field path such as `inputs.cpu_nodes_platform`.
    - Set `prompt: false` on an optional field when it should stay available for manual `config.yaml` editing but should be suppressed from the interactive wizard.
  - Terraform module outputs are exported automatically under normalized output names such as `cluster_id` and can be consumed from other components through `input` bindings.
  - `input`: consumer-side binding map. Values must use `<component-id>.<output-alias>` or `<component-id>@<instance-id>.<output-alias>`.
- `components.apps.<component-id>`:
  - `source.portable`: optional portable Helm source mapping. Supports HTTP/S chart repos, `oci://` repos, and GitHub tree URLs.
  - `source.local`: optional developer-local chart mapping with `path`.
  - `source.portable.chart`: optional chart name. Defaults to the component id when omitted. When it differs, runtime validation, dependency lookup, and Flux rendering use the configured chart basename instead of the app id.
  - `source.portable.version`: optional chart version.
  - `ui.title`, `ui.group`, `ui.enabled`: display metadata and default wizard checkbox state.
  - `release.namespace`, `release.name`: default Helm namespace and release name used during `create`.
  - `release.timeout`: optional Flux `HelmRelease.spec.timeout` duration such as `10m` or `12m30s`. When omitted, the chart inherits `cli.flux.release_timeout`.
  - `defaults`: unconditional target-path map for chart values. App defaults must target `values.*`.
  - `cli.mk8s_gpu_policy`: optional MK8s GPU automation contract for that app entry. `role` declares what operator role the chart plays, `install_after` adds Flux `dependsOn` ordering edges between app releases, `rules` is the conditional policy list, and optional `default_sets` / `post_render_patch_sets` let the catalog name reusable value overlays and post-render patch bundles once. Each `rules[]` item can set `auto_enable: true` to let cxcli auto-select the app for a matching MK8s GPU context and/or define conditional `defaults` / `post_render_patches` directly or reference shared sets with `defaults_from` / `post_render_patches_from`. Top-level app `defaults` remain unconditional; the rule-level fields are the conditional version of the same mechanism.
  - `wizard`: optional prompt metadata keyed by chart value path such as `values.image.tag`.
  - `input`: same binding syntax as infra, but target paths should land under `values.*`.

Wizard shorthand and wiring:

- `wizard_profile` is the short form for built-in component-specific wizard wiring. It expands to a built-in `wizard` mapping at catalog-load time.
- `wizard` is the explicit escape hatch when you need full field-by-field control.
- If both are set on the same infra component, the `wizard_profile` fields load first and explicit `wizard` entries override or extend them.
- Built-in `wizard_profile` names are one-to-one with infra component ids. When set, the profile name must exactly match the component id.
- `wizard_profile` still does not create Terraform variables. It only predefines how the CLI should populate existing module fields.
- Use `wizard_profile` or `wizard` only when normal Terraform/Helm introspection is not enough or when you want guided choices instead of plain free-text entry.
- Fields that are just ordinary inputs with no guided choices do not need either `wizard_profile` or `wizard`.

Implementation note:

- Built-in infra `wizard_profile` definitions are currently centralized in [src/nebius_cxcli/wizard_profiles.py](src/nebius_cxcli/wizard_profiles.py). They are not split into one Python file per component today.
- Bundled infra runtime validation selection is centralized in [src/nebius_cxcli/validation_profiles.py](src/nebius_cxcli/validation_profiles.py). It is code-owned internal metadata, not a supported public `component_sources.yaml` field.
- Central onboarding guidance for new Nebius Terraform modules lives in [../../skills/onboard-nbs-cxcli/SKILL.md](../../skills/onboard-nbs-cxcli/SKILL.md). Use it when a module needs to be added to `component_sources.yaml` and you need to decide whether onboarding also requires wizard/provider/status/validation/handoff code changes.

Built-in wizard profiles:

- `mk8s`: subnet lookup plus MK8s platform/preset chaining, live GPU driver-preset choices keyed by the selected GPU platform and Kubernetes version, and InfiniBand fabric choices keyed by the selected GPU platform and region.
- `managed-postgresql`: VPC network lookup plus static `tier` choices.
- `vm`: subnet lookup plus live compute platform/preset chaining, live Nebius public image-family choices keyed by the selected platform and region, static public-IP mode choices, and optional InfiniBand fabric choices for GPU-cluster VM shapes.
- `wireguard-jumphost`: subnet lookup plus live compute platform/preset chaining for the WireGuard jump-host module.
- `ssh-jumphost`: subnet lookup plus live compute platform/preset chaining for the SSH jump-host module.
- `object-storage`: static choices for `versioning_policy` and `object_audit_logging`.

Bundled infra component alignment:

- `mk8s` uses `wizard_profile: mk8s` because its subnet, platform, preset, GPU driver-preset, and optional `infiniband_fabric` fields need guided choices.
- `managed-postgresql` uses `wizard_profile: managed-postgresql` because `network_id` is Nebius-backed and `tier` is intentionally guided as a fixed choice.
- `vm` uses `wizard_profile: vm` because `subnet_id`, `platform`, `preset`, `source_image_family`, `public_ip_mode`, and optional GPU-cluster fabric choices should come from guided catalog wiring instead of raw manual entry.
- `wireguard-jumphost` and `ssh-jumphost` use their matching `wizard_profile` names because `subnet_id`, `platform`, and `preset` should come from live project discovery.
- `object-storage` uses `wizard_profile: object-storage` because `versioning_policy` and `object_audit_logging` are intentionally guided as fixed choices.
- `sfs` and `mysterybox` currently omit `wizard_profile` and `wizard`; they rely on ordinary Terraform variable introspection today.
- App components do not support `wizard_profile`; they rely on Helm metadata plus optional explicit `wizard` entries when a chart value needs guided choices.

Bundled MK8s GPU app policy:

- MK8s GPU software defaults are policy-driven in code and source-driven in the catalog.
- The bundled catalog keeps chart source selection, release metadata, default Helm values, activation rules, validation images, thresholds, and timeouts in `component_sources.yaml`, while the CLI only evaluates those rules against the selected MK8s context.
- The same `source.portable` / `source.local` contract now applies to first-party Helm charts as well as Terraform modules.
- The canonical GPU role is `nvidia-gpu-operator` for both Nebius-image and manual node groups. On Nebius-managed images the CLI materializes Helm values that disable the driver, the NVIDIA Container Toolkit runtime (`values.toolkit.enabled`), and the Nebius `NVIDIADriver` CRD path while relying on the chart defaults that keep the device plugin and DCGM exporter enabled. The catalog now keeps only the Nebius-specific operator deltas instead of restating live chart defaults.
- When the selected MK8s shape enables GPU clustering / InfiniBand, or when a manual B200/B200A node group requires RDMA plumbing, the CLI auto-enables `nvidia-network-operator`, renders a Flux `dependsOn` edge so the network operator reconciles before the GPU operator, suppresses GPU Operator's own NFD so the bundled stack keeps only one NFD instance, and renders an explicit `NicClusterPolicy` patch for `rdma/shared_device` on the InfiniBand path instead of relying on chart defaults. The bundled catalog now keeps that RDMA patch and the driverful NFD overlay as reusable named policy sets so the versioned plugin tag and selector details stay catalog-owned without being repeated inline in multiple rules.
- GPU Visibility test is enabled by default for GPU-backed MK8s deploys, but it remains intentionally workload-based instead of stopping at a node `allocatable` check: by default it runs the CUDA sample on at most 3 Ready GPU nodes, reports live pod phase progress, bulk-cleans the validation pods afterward, and now also saves the underlying device-plugin allocatable snapshot in the report for comparison. That keeps `nvidia.com/gpu` advertisement visible without treating it as proof that a CUDA workload actually runs.
- NCCL test is enabled by default only for MK8s GPU-cluster shapes, not for every GPU-enabled MK8s cluster. Its workload manifest comes from the first-party `helm-charts/nccl-test` chart with both `source.local` and `source.portable` catalog entries pinned to `oci://cr.<region>.nebius.cloud/<registry-short-id>/charts/nccl-test --version 0.2.7`; the shared image/tag plus the pragmatic benchmark defaults are now sourced directly from the chart's own `values.yaml`, and the app entry keeps only the B200-only `-mca coll ^hcoll` overlay because the official Nebius B200 NCCL example includes that flag while the H100/H200 example does not. A nearby NVIDIA signal is that HPC-X release notes / known issues mark HCOLL unsupported on GB200/GB300; that does not directly prove the same rule for B200, but it is another reason to keep the B200 override scoped in the catalog instead of promoting it to a shared chart default. See: [Nebius NCCL guide](https://docs.nebius.com/kubernetes/gpu/nccl-test), [NVIDIA HPC-X General Support](https://docs.nvidia.com/networking/display/hpcvx225/hpc-x-general-support), and [NVIDIA HPC-X Known Issues](https://docs.nvidia.com/networking/display/hpcxv2251/known-issues). `nebius-cxcli` also keeps the Kubeflow Training Operator as a transient NCCL prerequisite pinned in the catalog and installs/removes it on demand, and the saved GPU validation reports are intentionally compact ordered JSON: practical summary fields stay up front, success cases omit noisy raw logs, and failures keep only the relevant log excerpts.

What `wizard` is doing:

- `wizard` does not create Terraform variables or Helm values. Those fields already come from the Terraform module or Helm chart contract.
- `wizard.<field>.options` is the wiring layer that tells `nebius-cxcli` how to populate one existing field from a guided option source. That source may be a live Nebius API lookup or a static choice list.
- In other words, the Terraform input path stays the operator-facing destination, and the `wizard` metadata tells the CLI where to fetch valid choices for that destination.
- Without that metadata, the field can still exist and be prompted as a normal string/bool/number field, but the CLI will not know which Nebius-backed lookup to run for it.

What `status` is doing:

- `status` is not a Terraform input and it is not rendered into the module itself.
- It is catalog metadata for Nebius deployment-status polling during commands such as `deploy` and `terraform apply`.
- If an infra component wants Nebius status polling, declare `status.kind` explicitly.
- Use `status.parent_input` and `status.name_input` only when the resource is identified by input names other than the defaults `parent_id` and `name`.
- `status.name_input` may resolve either one scalar resource name or a collection of objects that each contain a nested `name`; in the latter case the CLI expands one component row into one watcher spec per resolved resource name.
- The bundled `mk8s` component is the scalar example: `status.kind: nebius.mk8s.cluster` declares the Nebius resource type, and `status.name_input: cluster_name` tells the watcher which Terraform input contains the actual cluster name.
- The bundled `mysterybox` component is the collection example: `status.kind: nebius.mysterybox.secret` with `status.name_input: secrets` expands one component row into one watcher per configured secret name.
- Supported bundled watcher kinds currently include `nebius.mk8s.cluster`, `nebius.msp.postgresql.cluster`, `nebius.compute.filesystem`, `nebius.compute.instance`, `nebius.mysterybox.secret`, and `nebius.storage.bucket`.
- Fail-fast behavior is service-native: MK8s watchers inspect live node-group events, while the PostgreSQL/filesystem/compute-instance/MysteryBox/object-storage watchers combine live resource state with the latest terminal Nebius operation status for that resource.

Example:

```yaml
wizard_profile: mk8s
```

That shorthand expands to the equivalent wiring for the built-in MK8s flow, including:

- `inputs.subnet_id` from the Nebius `project_subnets` lookup
- `inputs.k8s_version` from the Nebius MK8s control-plane version lookup, with the first live version auto-selected into the wizard/config unless you override it
- `inputs.cpu_nodes_platform` and `inputs.gpu_nodes_platform` from the MK8s compatibility lookup intersected with the selected project's live compute-platform inventory
- `inputs.cpu_nodes_preset` and `inputs.gpu_nodes_preset` from the compute-preset lookup chained off the selected platform
- `inputs.cpu_nodes_os`, `inputs.gpu_stack_preset`, and `inputs.gpu_nodes_os` materialized from the live MK8s compatibility matrix using the catalog preference order
- `inputs.infiniband_fabric` is prompted only after `inputs.gpu_nodes_preset`, and only when the chosen preset's live SDK metadata says that GPU clustering is supported for that shape

Profile-plus-override example:

```yaml
wizard_profile: mk8s
wizard:
  inputs.subnet_id:
    options:
      from: project_subnets
      filter_regex: "^vpcsubnet-"
```

That keeps the rest of the built-in `mk8s` profile unchanged and replaces only the `inputs.subnet_id` field wiring with the explicit override.

Explicit `wizard` example:

```yaml
wizard:
  inputs.cpu_nodes_platform:
    options:
      from: mk8s_compatible_platforms
      prefix: cpu-

  inputs.cpu_nodes_preset:
    options:
      from: compute_platform_presets
      depends_on: inputs.cpu_nodes_platform
```

How the explicit example works:

- `inputs.cpu_nodes_platform` is still a Terraform module input.
- `from: mk8s_compatible_platforms` tells the CLI to call the Nebius-backed compatibility lookup for that field.
- When `client_info.nebius.project_id` is available, that lookup keeps only platform names that are both MK8s-compatible for the chosen control-plane version and present in the selected project's live compute-platform inventory.
- `prefix: cpu-` keeps only CPU platform names from that compatible/project-scoped result set. This is a plain prefix filter, not regex.
- `inputs.cpu_nodes_preset` is another Terraform module input.
- `from: compute_platform_presets` tells the CLI to query Nebius for presets of a selected compute platform.
- `depends_on: inputs.cpu_nodes_platform` means the preset lookup uses the operator's chosen `cpu_nodes_platform` value as input to the next API call.
- That makes the second field a chained lookup: first choose a compatible platform, then choose one of the presets available for that exact platform.
- Chained provider-backed fields are prompted only after the dependency field has a concrete value. If the operator skips `inputs.gpu_nodes_platform`, `inputs.gpu_nodes_preset` is not prompted yet instead of falling back to a misleading manual-entry warning.

Regex and pattern behavior:

- `wizard.<field>.options.filter_regex` is the only regex-capable field in `component_sources.yaml`.
- `filter_regex` is compiled as a Python regular expression and applied to provider-returned option values with regex `search`, not exact-match.
- The same `filter_regex` is used both for displayed wizard choices and for strict provider-backed manual-entry validation, so operators cannot type a value that the catalog-level filter was meant to exclude.
- `wizard.<field>.options.prefix` is a plain literal prefix helper for provider lookups. It is not regex.
- `wizard.<field>.options.depends_on` is a plain field-path reference such as `inputs.cpu_nodes_platform`. It is not regex.
- `wizard.<field>.options.auto_select_single: true` tells the wizard to preselect a live provider value when exactly one compatible option exists and the field is currently unset.
- `wizard.<field>.options.auto_select_first: true` tells the wizard to preselect the first live provider value after catalog preference ordering when the field is currently unset.
- `wizard.<field>.options.args` passes provider-specific lookup arguments through directly; the shorthand helpers `prefix` and `depends_on` are merged into that args mapping during catalog load.
- `wizard.<field>.options.skip_prompt_if_no_choices: true` suppresses an optional provider-backed prompt when the live lookup succeeds but returns no valid choices for the current shape.
- Component ids and instance selectors are validated against the repo's lowercase letters/digits/hyphens naming rules.
- `cli.flux.version` must look like `v2.8.0`; `cli.flux.release_timeout` and `release.timeout` must be Go-style durations such as `5m` or `12m30s`; `cli.terraform.version` must look like `1.14.1`.

Wizard option keys:

- `from`: provider-option source name such as `mk8s_compatible_platforms` or `tenant_projects`
- `prefix`: optional literal prefix filter passed into provider lookups
- `depends_on`: optional sibling field path used to drive provider lookup args
- `args`: optional provider-specific argument mapping; use this for extra lookup inputs beyond the `prefix` / `depends_on` shorthands
- `filter_regex`: optional regex post-filter for returned option values
- `auto_select_single`: optional boolean for provider-backed fields; when true, the wizard preselects the one live compatible value if the lookup resolves to exactly one option
- `auto_select_first`: optional boolean for provider-backed fields; when true, the wizard materializes the first live compatible value after provider-side sorting
- `skip_prompt_if_no_choices`: optional boolean for provider-backed optional fields; when true, the wizard skips the prompt entirely if the live lookup returns no valid choices and no current value is set

Reference syntax:

- `defaults` shared-value reference: `shared.admin_ssh.user_name`
- `input` binding without instance selector: `mk8s.cluster_id`
- `input` binding with explicit instance selector: `mk8s@cluster-a.cluster_id`

`wizard_profile` and `wizard` are optional catalog metadata for advanced wizard behavior. Most modules should rely on Terraform variable or Helm values introspection alone; use `wizard_profile` only when the bundled component-specific profile already exists for that same component id, or use `wizard` when a field needs explicit Nebius-backed choices or another catalog-defined override.

Built-in cluster handoff:

- Cluster handoff for kubeconfig/bootstrap is no longer declared in `component_sources.yaml`.
- The bundled `mk8s` component has a code-owned built-in cluster handoff contract.
- That built-in contract reads the Terraform output `cluster_id` and derives endpoint access from `inputs.mk8s_cluster_public_endpoint`.
- This keeps the YAML schema smaller while preserving the same deploy/Flux/bootstrap behavior.

`input` is the consumer-side binding block for both infra and apps:

- `input.<target-path>: <component-id>.<terraform-output>`
- `input.<target-path>: <component-id>@<instance-id>.<terraform-output>`

Source profile selection:

- `portable` is the default. It always uses `source.portable`.
- `local` prefers `source.local` and falls back to `source.portable` when `source.local` is unset.
- Choose the active profile with the global CLI flag `--source-profile {portable|local}`. When omitted, the CLI defaults to `portable`.
- Or set `NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE={portable|local}`.
- For schema/output introspection, nebius-cxcli prefers a resolvable `source.local` when one exists, even while the active profile is `portable`. This keeps workstation/CI validation fast without changing the emitted portable module source addresses.

`components.apps.<id>.source.portable.repo` supports:

- HTTP/S Helm repositories (must serve `index.yaml`)
- OCI chart repositories (`oci://...`)
- GitHub tree URLs for charts stored in git (`https://github.com/<owner>/<repo>/tree/<ref>/<chart-path>`)

Source requirements enforced by `validate-sources`:

- Terraform components (`components.infra.<id>`):
  - `<component-id>` must be lowercase letters/digits/hyphens.
  - `source.portable` is required.
  - `source.local` is optional.
  - `validate-sources` validates the module source resolved by the active source profile.
  - `source.local` may use a relative path (`./...`, `../...`, `../../...`) or an absolute filesystem path.
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
  - Local Terraform module sources are rendered as resolved local filesystem paths. If you need a portable or pinned remote ref, declare it in `source.portable`.
  - Plain `http://` or `https://` module URLs are rejected. Use the Terraform Git source format instead.
  - Registry-style and `oci://` Terraform module sources are rejected.
  - All Terraform outputs exposed by the module are exported automatically under their normalized names.
  - If you provide a custom module for the bundled `mk8s` component and plan to use `deploy` or CI kubeconfig bootstrap, that module must still expose the Terraform output `cluster_id`.
- App charts (`components.apps.<id>`):
  - `source.portable` is the release-ready chart source.
  - `source.local` is optional and is intended for developer-local chart work.
  - HTTP repo format: `source.portable.repo` must be a Helm repo base URL, `repo/index.yaml` must be readable, chart must exist in `entries`, and configured version must exist.
  - OCI format: `source.portable.repo` must be an OCI repo prefix (`oci://...`), and `source.portable.chart` supplies the chart name.
  - Git-hosted chart format: `source.portable.repo` may be a GitHub tree URL, and `source.portable.chart` supplies the logical chart name.
  - Local chart format: `source.local.path` must resolve to an existing chart directory when the active source profile is `local`.
  - Portable build/release verification strips `source.local` and fails if an app chart still has no usable `source.portable`.
  - GitHub tree format: `source.portable.repo` may point at a chart directory in git (`https://github.com/<owner>/<repo>/tree/<ref>/<chart-path>`). Helm validates the chart from that path directly.
  - Helm chart sources are fail-fast validated with `helm show chart`; missing Helm, unreachable repos, bad refs, missing charts, and version mismatches are hard failures.
  - Set `NEBIUS_CXCLI_HELM_TIMEOUT_SECONDS` to raise the Helm validation timeout for slow OCI registries or chart sources without changing the catalog.
  - `validate-sources` also materializes the resolved chart and checks the CLI-facing chart contract:
    - fails when `Chart.yaml`, `values.yaml`, or `templates/` are missing
    - fails when `Chart.yaml` is missing `apiVersion`, `name`, or `version`
    - warns when the chart is not on canonical Helm v2 metadata or when `README.md` is missing
  - App `defaults` seed `values.*` when missing.
  - `release.namespace` and `release.name` are the default Helm namespace and release name used by `create`.

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
`enabled: true|false` under `ui` controls only default checkbox state in the wizard.  
`config.yaml` does not embed `component_sources`; source resolution uses the resolved `component_sources.yaml` path.

Source profile precedence:

1. `--source-profile`
2. `NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE`
3. default `portable`

Supported `--component-sources-file` examples:

- Relative file in the current directory: `nebius-cxcli --component-sources-file ./component_sources.yaml validate-sources`
- Positional file in the current directory: `nebius-cxcli validate-sources ./component_sources.yaml`
- Relative file elsewhere: `nebius-cxcli --component-sources-file ../../shared/component_sources.yaml validate-sources`
- Home-relative file: `nebius-cxcli --component-sources-file ~/catalogs/component_sources.yaml validate-sources`
- Environment override: `NEBIUS_CXCLI_COMPONENT_SOURCES_FILE=~/catalogs/component_sources.yaml nebius-cxcli validate-sources`

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

components:
  infra:
    mk8s:
      source:
        portable: git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=main
        local: ../../platform-infra/modules/mk8s
      ui:
        title: Managed Kubernetes baseline cluster
        group: Compute
        enabled: true
      status:
        kind: nebius.mk8s.cluster
        name_input: cluster_name
      defaults:
        inputs.cpu_nodes_count: 2
        inputs.mk8s_cluster_public_endpoint: true
      wizard:
        inputs.cpu_nodes_platform:
          options:
            from: mk8s_compatible_platforms
            prefix: cpu-

  apps:
    external-dns:
      source:
        portable:
          repo: https://kubernetes-sigs.github.io/external-dns
          chart: external-dns
          version: 1.18.0
      release:
        namespace: external-dns
        name: external-dns

    gateway-helm:
      source:
        portable:
          repo: oci://docker.io/envoyproxy
          chart: gateway-helm
          version: 1.7.0
      release:
        namespace: envoy-gateway-system
        name: envoy-gateway
```

`cli.flux.version` is the catalog-controlled Flux controller version for local `deploy` and the managed Flux CLI download path.  
`cli.flux.release_timeout` is the catalog-controlled default Flux `HelmRelease.spec.timeout` used when an app chart does not set `release.timeout`.  
`cli.terraform.version` is the catalog-controlled Terraform CLI version for the managed Terraform download path.  
The bundled default is `5m`, which matches the upstream Helm/Flux default action timeout. To change one global app-install timeout policy or either managed tool version, bump the value in the active `component_sources.yaml`.

Portable build/release behavior:

- `component_sources.yaml` is the only checked-in catalog.
- Build/package steps bundle a portable view of that catalog into the wheel by stripping `source.local`.
- Any app chart that still lacks `source.portable` is intentionally local-only and will fail portable release verification until a portable chart source is published.
- CI/release workflows rewrite internal `source.portable` refs from `?ref=main` to the current commit or tag before publishing wheel or catalog assets.

Recommended workflow:

- Automatic catalog resolution is a convenience default, not a portability guarantee.
- `validate` and `render` default to the `portable` source profile, which emits deployable Terraform module sources suitable for CI and other machines.
- Installed-package fallback is portable by default: when no repo-local/user/global override is present, the packaged `nebius_cxcli/component_sources.yaml` uses Git Terraform module sources.
- Use `--source-profile local` or `NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE=local` when you intentionally want generated Terraform to point at checked-out local module paths for workstation testing.
- Use `--component-sources-file` or `NEBIUS_CXCLI_COMPONENT_SOURCES_FILE` only when you need to override which catalog file is active; it is not the primary portable-vs-local switch.
- Generated-bundle commands do not need the source catalog to resolve Terraform module paths from the original render environment.

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

`defaults` declares source-defined target values. Literal values are seeded into component config at `create` time and reused at runtime when the target path is missing. Shared-derived values use `shared.<path>` in `component_sources.yaml`, and `create`/`component add` materialize those resolved values into the per-project component rows so `config.yaml` carries the effective operator contract. `render`/`validate` do not backfill those shared-derived values later; if a selected row is missing one, the config is invalid and must be fixed explicitly.
For Terraform components, `defaults` targets must start with `inputs.`. For app charts, they must start with `values.`.
`input` declares consumer-side target paths wired from `<component-id>.<terraform-output>` or `<component-id>@<instance-id>.<terraform-output>`.
`input` is reserved for component-output references only. Use `defaults` for literal values or shared-derived values.
Unqualified refs resolve only when exactly one enabled instance of the source component type matches; qualify the ref with `@<instance-id>` when multiple instances of the same type are enabled.
The one intentional exception is `shared.admin_ssh.public_key`: when a private active catalog sets it and a selected infra module declares `ssh_public_key`, `create` and `component add` resolve that value locally if needed and seed the normalized inline key into the project `config.yaml` so the customer repo stays self-contained.
Do not declare `shared` in `config.yaml`; shared values are catalog-only and configs with a root `shared` key are rejected.

The bundled `mk8s` component has a built-in cluster handoff contract.  
It is not declared in `component_sources.yaml`.  
When enabled charts are deployed, the CLI reads the rendered Terraform output `cluster_id` and derives endpoint access from `inputs.mk8s_cluster_public_endpoint` before Flux/kubectl work starts.

For app source entries, `release.namespace` and `release.name` are defaults:

- interactive create wizard prompts them for enabled apps
- non-interactive create can override with:
  - `--app-namespace <app-id>=<namespace>`
  - `--app-releasename <app-id>=<release-name>`

Runtime config shape:

- `client_info`: `client_name`, `nebius.{tenant_id,project_id,region_id}`, `notifications.{email_enabled,email}`
- `client_info.notifications.email_enabled` is the single per-client gate for deploy-report email delivery across local runs and CI. Keep it `true` when this client should receive the deploy report email, and set it to `false` when this specific client should not receive mail.
- In `create`, leaving the optional notifications email blank writes `client_info.notifications.email_enabled: false` and `client_info.notifications.email: null`.
- `client_info` does not include legacy `env` or `cluster_name` fields.
- `infra.components[]`: `id`, `enabled`, `inputs`
- `apps.charts[]`: `id`, `group`, `enabled`, `repo`, `version`, `namespace`, `release-name`, `values`
- Source catalogs use `release.name`; project `config.yaml` uses `release-name`. Alias keys are intentionally unsupported.
- Static nested component configs (`infra.<component>.enabled`, `apps.<group>.<chart>.enabled`) are not supported.
- Canonical project path: `<deployments-root>/<tenant-id>/<project-id>/config.yaml`

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
- The generated manifest includes the render-time quota report alongside the runtime config snapshot and deploy metadata, so later bundle commands can explain quota-related failures without rerendering first.
- `generated/inventory/deploy-report.md`
- `deploy-report.md` is the single human-readable customer report. It combines the project inventory with a `Validations` section, and `nebius-cxcli email` sends that same file.
- The generated report is emitted without trailing blank lines so customer-repo Markdown linting stays clean.
- `render`, `deploy`, `terraform apply`, `flux apply`, `flux bootstrap`, and `report write` refresh that report artifact for the active project instead of leaving the starter placeholder behind.
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
- `nebius-cxcli terraform unlock <generated-dir>` is the explicit manual recovery path for that case. It inspects the remote `.tflock`, refuses by default when local Terraform/deploy processes are still active or the lock owner is from another machine/user, and then uses Terraform's own `force-unlock` only when the lock looks stale. Do not run it as routine cleanup.
- `destroy` and `terraform destroy` can use that same guarded stale-lock recovery automatically inside an already-confirmed destroy flow: if Terraform destroy fails before acquiring the backend lock and the existing local-owner safety checks pass, the CLI clears the stale lock and retries destroy once instead of making you run a separate unlock command first.
- `terraform unlock` requires `aws` CLI in `PATH`. Terraform itself can come from `PATH` or from the managed Terraform download path.
- Inventory artifacts are local-only outputs under `generated/inventory`; they are not uploaded to Object Storage by the CLI.

Wizard field behavior:

- Infra input field names are discovered dynamically from Terraform module variables (required and optional).
- Interactive `create` and `component add` offer all discoverable required and optional component fields for newly selected components.
- Infra component field phases default to `y`; app chart field phases default to `n`, because chart overrides are usually optional and Helm/chart defaults still apply unless you choose to edit them.
- Required fields are prompted first, are labeled `required`, and must receive a valid value before the wizard advances unless the operator backs out or stops the wizard.
- Optional fields are labeled `optional`; pressing Enter keeps the current/default value and leaves the field unset in `config.yaml` when the value is still only a virtual default.
- Prompt labels include Terraform input type hints (for example `string`, `number`, `bool`) plus `required` or `optional`.
- Collection/object Terraform inputs (`list(...)`, `map(...)`, `object(...)`, `tuple(...)`) are entered as single-line YAML/JSON values in the wizard instead of being flattened into string-only prompts.
- Terraform module defaults and Helm chart defaults can be shown as prompt defaults without being copied into `config.yaml`; they remain virtual until the operator explicitly overrides them.
- Literal defaults from `component_sources.yaml` are still shown in the wizard as editable current values instead of being hidden once pre-seeded into the component block.
- Declared `component_sources.yaml` `wizard` paths under `inputs.*` or `values.*` remain valid even when the target key is not yet materialized in the current payload; the wizard now prompts those fields directly instead of printing a spurious “path not found in config payload” warning.
- `wizard.<field>.prompt: false` suppresses optional fields from the interactive wizard while leaving the field in the underlying Terraform/Helm contract for manual config editing.
- Empty optional YAML/JSON defaults such as `{}` and `[]` are rendered as blank-input prompts with explicit “blank keeps current empty map/list” guidance instead of awkward literal default tokens.
- Empty top-level app `values: {}` blocks no longer trigger a generic whole-map prompt; the wizard only prompts concrete chart value leaves that already exist, come from chart defaults, or are declared explicitly in `wizard`.
- Multiline Terraform defaults discovered from module `variables.tf` files, including map/object defaults, are parsed as full values in wizard mode instead of being truncated to the first line.
- Source-backed infra `inputs.parent_id`/`inputs.project_id` default to `client_info.nebius.project_id` when those variables exist.
- `component_sources.yaml` can declare top-level `shared` values and shared-derived `defaults` so components read shared values from the active source catalog instead of duplicating them under component `inputs` or chart `values`.
- The bundled `mk8s` catalog entry defaults `inputs.mk8s_cluster_public_endpoint: true`, and the built-in MK8s cluster handoff derives access dynamically from that input. If you switch the control plane to private-only, local app operations still work, but only from a machine that already has private network reachability to the MK8s API endpoint.
- The bundled `mk8s` catalog entry also defaults `inputs.kube_network_service_cidrs: ["/20"]`. Nebius treats an omitted MK8s service CIDR as `["/16"]`; on a single-pool `/16` subnet that can consume the whole pool and leave no address space for control-plane allocations, which looks like a long `PROVISIONING` stall.
- The bundled `mk8s` catalog entry also defaults `inputs.cpu_nodes_count: 2`. That keeps the baseline cluster footprint explicit in `config.yaml` and editable in the wizard instead of relying on a hidden Terraform module default for CPU node-group size.
- The bundled `mk8s` catalog entry now uses `wizard_profile: mk8s`, which wires `inputs.subnet_id` to the live `project_subnets` provider, wires `inputs.k8s_version` to the MK8s control-plane version lookup, wires MK8s platform/preset prompts to project-scoped Nebius lookups, and materializes `inputs.cpu_nodes_os`, `inputs.gpu_stack_preset`, and `inputs.gpu_nodes_os` from the live MK8s compatibility matrix using the catalog preference order.
- That same bundled `mk8s` profile still suppresses the advanced passthrough maps `inputs.mk8s_cluster_overrides`, `inputs.mk8s_cpu_node_group_overrides`, and `inputs.mk8s_gpu_node_group_overrides`, but it now surfaces first-class MK8s boot-disk choices in the interactive flow once the effective node-group shape is known. cxcli pre-fills the boot-disk size from the first matching ordered size rule for the selected preset resources, falls back to a heuristic only when no explicit rule matches, shows guided disk-type choices with Nebius performance/reliability labels, and still lets operators override the final `inputs.<cpu|gpu>_nodes_boot_disk_*` values directly in `config.yaml` when they need exact storage sizing. The guided prompt intentionally offers the recommended SSD-backed types `NETWORK_SSD`, `NETWORK_SSD_NON_REPLICATED`, and `NETWORK_SSD_IO_M3`; other module-supported values such as `NETWORK_HDD` remain available only through direct config edits. Public MK8s node-group `boot_disk` currently exposes size/type only, so optional SSD NRD / SSD IO M3 encryption cannot be toggled from cxcli today. For current disk characteristics and pricing, see [Types of storage volumes in Compute](https://docs.nebius.com/compute/storage/types) and [Compute pricing in Nebius AI Cloud](https://docs.nebius.com/compute/resources/pricing).
- The bundled `mk8s` flow now exposes source-driven GPU validation controls under the project-facing `deploy.validations.mk8s_gpu.*` contract. In wizard mode, when `gpu_enabled=true`, operators can enable or disable operator-readiness and GPU-visibility checks and tune `gpu_visibility.max_nodes`; when `infiniband_fabric` is set, the wizard also exposes NCCL toggles plus `nccl.max_nodes` and `nccl.average_bus_bandwidth_threshold_gbps`. The catalog still owns the defaults in `components.infra.mk8s.cli.gpu.validations`, but the chosen per-project values now persist in `config.yaml` as deploy settings instead of pretending to be Terraform inputs.
- That project-facing config contract intentionally chooses what to run, not where to write it. The human-readable report path is fixed at `generated/inventory/deploy-report.md` so deploy output stays deterministic and generated-bundle-centric rather than adding per-project file-path knobs under `deploy:`.
- The old fake module-input path `infra.components[].inputs.gpu_validation_overrides` is no longer supported. The canonical per-project contract is `deploy.validations.mk8s_gpu.*` only.
- The bundled NVIDIA path intentionally keeps deploy-time validation fast and scoped: operator readiness checks the operator control plane plus scheduler-visible GPUs, GPU visibility runs a bounded CUDA sample on selected Ready GPU nodes, and NCCL remains the optional multi-node / InfiniBand performance check. That matches NVIDIA's own split between install verification, sample GPU workload validation, and DCGM-based observability rather than a single heavy post-deploy "health checker". See [About the NVIDIA GPU Operator](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/24.9/index.html), [GPU Operator Getting Started](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/23.9.0/getting-started.html), [NVIDIA GPU Telemetry](https://docs.nvidia.com/datacenter/cloud-native/gpu-telemetry/latest/index.html), and [DCGM Diagnostics](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/dcgm-diagnostics.html).
- On GPU-cluster / InfiniBand shapes, the bundled catalog now makes pod-facing RDMA exposure explicit on both supported host-stack modes instead of assuming the Network Operator chart default CR is enough. For `gpu_stack_source: nebius_image`, GPU Operator still leaves the host GPU driver and NVIDIA Container Toolkit runtime untouched while Network Operator keeps OFED disabled and post-patches `NicClusterPolicy` so driverful InfiniBand nodes expose `rdma/shared_device`. For `gpu_stack_source: manual`, Network Operator still owns OFED on the host and now gets the same explicit `rdma/shared_device` policy so operator-managed InfiniBand nodes meet the same scheduler-visible RDMA contract.
- Those three validations are intentionally layered and non-duplicative. `operator_readiness` is the cheapest control-plane gate and never launches a workload. `gpu_visibility` is the single-node CUDA smoke test that proves a real GPU pod can run. `nccl` is the expensive distributed-communication check for GPU-cluster / InfiniBand shapes. A successful NCCL run implies much more than the visibility check, but cxcli still runs the cheaper readiness and CUDA smoke checks first so routine failures stop early instead of paying the full NCCL cost.
- `deploy.validations.mk8s_gpu.health_checker.enabled` is not a built-in runner. It is reserved for a custom catalog app with `cli.mk8s_gpu_policy.role: health_checker`. In the bundled catalog there is no such app, so the wizard hides that toggle and cxcli omits it from persisted project defaults unless an active catalog actually supplies one.
- If the MK8s infra prompts turn on a GPU shape that requires operator apps, the same wizard pass now auto-enables those app rows before the app phase starts, so `nvidia-gpu-operator` / `nvidia-network-operator` can still be reviewed in `create` or `component add` instead of only appearing later in the final `config.yaml`.
- MK8s operator readiness is no longer tied to manual `nvidia.com/gpu.deploy.*` node labels. cxcli now uses a hybrid live check: `ClusterPolicy` and `NicClusterPolicy` are the fast control-plane signals, GPU readiness still requires allocatable `nvidia.com/gpu` on Ready nodes, and GPU-cluster / InfiniBand shapes also require those Ready GPU nodes to advertise scheduler-visible RDMA-style allocatable resources such as `rdma/shared_device`. The saved report now records `NicClusterPolicy.status.appliedStates` plus daemonset rollout details instead of treating a green control plane alone as proof that pod-facing RDMA is ready. That matches the current live Nebius MK8s behavior where GPUs can be allocatable even while the upstream GPU Operator `ClusterPolicy` still reports a `NoGPUNodes` reason.
- Fields behind a sibling `<prefix>_enabled` toggle, such as MK8s GPU settings behind `gpu_enabled`, stay hidden until that toggle is true, and enabling the toggle expands the dependent prompts immediately into the remaining wizard flow instead of deferring them to a later pass.
- The bundled MK8s flow also treats effective node-group prerequisites as conditionally required: when the baseline CPU pool is enabled, `cpu_nodes_platform` / `cpu_nodes_preset` must be set unless the CPU override template supplies them, and when `gpu_enabled=true`, the wizard plus strict validation require `gpu_node_groups`, `gpu_nodes_count_per_group` unless GPU autoscaling override is configured, and effective GPU platform/preset values.
- Provider-backed option lists come only from explicit catalog wizard metadata, whether that metadata comes from a built-in `wizard_profile` or a raw `wizard` block, and are resolved live from Nebius APIs when available.
- Prompt-time provider lookups and strict provider-value validation now share the same argument-normalization path, so relative `depends_on` targets such as `inputs.cpu_nodes_platform` resolve against the active component instance consistently in both places.
- If live provider choices are unavailable for a field, the CLI prints a field-specific warning immediately before that prompt and explains whether the next manual-input prompt is required or can be skipped with Enter.
- When a built-in resolver or provider plugin fails internally, the fallback warning now includes that resolver error text instead of silently degrading to a generic unavailable-options message.
- Optional provider-backed fields now accept blank/skip answers as “leave unset” without revalidating that blank value against the live option list.
- Provider-backed fields can now opt into `auto_select_single` or `auto_select_first`, which materialize the resolved live value into `config.yaml` during `create` and `component add` while still leaving the field editable in the wizard when prompting is enabled.
- Helm chart default values discovered from the live chart are not copied into `config.yaml`; the app wizard can show them as prompt defaults, but only explicit overrides are written back.
- Current built-in provider option sources include `mk8s_compatible_platforms` (mk8s platform fields), `mk8s_gpu_stack_presets` and `mk8s_node_group_os_values` (mk8s image selection from the compatibility matrix), `mk8s_infiniband_fabrics` (optional mk8s GPU-cluster fabric selection gated by the selected preset's live clustering capability), `compute_platforms`, `compute_platform_presets`, `project_subnets`, `project_networks`, `tenant_projects`, and `mk8s_control_plane_versions`.
- When live provider options are unavailable, the wizard falls back to manual input.

Shared-derived default example:

```yaml
# component_sources.yaml
shared:
  admin_ssh:
    user_name: ubuntu
    public_key: ~/.ssh/id_ed25519.pub

components:
  infra:
    wireguard-jumphost:
      source:
        portable: git::https://github.com/example/platform-infra.git//modules/wireguard-jumphost?ref=v1.2.3
        local: ../../platform-infra/modules/wireguard-jumphost
      ui:
        enabled: false
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
        ssh_user_name: ubuntu
        ssh_public_key: ssh-ed25519 AAAA... admin@example
```

With that default, `create`/`component add` materialize `shared.admin_ssh.user_name` into `infra.components[].inputs.ssh_user_name`, so later `render` runs do not depend on the active catalog for that field. If an operator removes that field later, `validate`/`render` fail instead of silently restoring it from the catalog.
`ssh_public_key` is intentionally per-project input and should be stored only in the private project `config.yaml`, not in the shipped source catalog.
If a private active `component_sources.yaml` sets `shared.admin_ssh.public_key`, `create` and `component add` accept either inline `ssh-rsa` / `ssh-ed25519` content or a readable local `.pub` path such as `~/.ssh/id_rsa.pub` or `~/.ssh/id_ed25519.pub`, then copy the normalized inline key into `infra.components[].inputs.ssh_public_key` for enabled infra modules that actually declare an `ssh_public_key` variable.
The same normalization also applies when an operator edits `config.yaml` directly and sets `infra.components[].inputs.ssh_public_key` to a local `.pub` path: config-based commands resolve the file locally and rewrite the config back to inline key text before continuing.
Once shared-derived defaults are materialized, the values in `config.yaml` become the customer-owned contract and may be edited explicitly without conflicting with the catalog seed.

Catalog defaults example:

```yaml
# component_sources.yaml
components:
  infra:
    mk8s:
      source:
        portable: git::https://github.com/example/platform-infra.git//modules/mk8s?ref=v1.2.3
        local: ../../platform-infra/modules/mk8s
      defaults:
        inputs.cluster_name: demo-cluster
        inputs.cpu_nodes_count: 3

  apps:
    demo-app:
      source:
        portable:
          repo: https://example.invalid/charts
          chart: demo-app
          version: 1.0.0
      release:
        namespace: demo
        name: demo-app
      defaults:
        values.replicaCount: 2
        values.image.tag: stable
```

With that contract, `create` seeds those values into the starter `config.yaml`, the wizard skips prompting for those target paths, and runtime commands still use them as fallback when the field is omitted from the config.

Component output binding example:

```yaml
# component_sources.yaml
components:
  infra:
    mk8s:
      source:
        portable: git::https://github.com/example/platform-infra.git//modules/mk8s?ref=v1.2.3
        local: ../../platform-infra/modules/mk8s

  apps:
    demo-app:
      source:
        portable:
          repo: https://example.invalid/charts
          chart: demo-app
          version: 1.0.0
      release:
        namespace: demo
        name: demo-app
      input:
        values.global.clusterId: mk8s.cluster_id
```

This contract is fully catalog-driven:

- producers expose Terraform outputs from their source modules
- consumers declare target paths under `input`
- both producer and consumer must exist in `component_sources.yaml`
- source component ids must be globally unique across `infra` and `apps`

Resolution model:

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
   `render` expects the project `config.yaml` path, not the `generated/` directory.
6. Commit the project `config.yaml` and the deployable `generated/` bundle to the customer private repo.
7. Deploy from the generated bundle:
   - `nebius-cxcli deploy <config.yaml>`
   - `nebius-cxcli terraform apply <generated-dir>`
   - `nebius-cxcli flux apply <generated-dir>`
   - CI workflow deploys from `generated/`, not from `config.yaml`
8. Optional CI setup:
   - `nebius-cxcli bootstrap-ci <config.yaml>`
   - The generated customer workflow watches canonical `<tenant>/<project>/generated/**` paths only. Editing `config.yaml` in the customer repo does not trigger CI deploys; rerendering from `config.yaml` is a manual replace action.

`create` is the bootstrap path, not the day-2 component-editing path. When the same resolved `tenant_id/project_id` target already exists, `create` now warns and overwrites from scratch instead of reconciling the existing component selection. Use `component list/add/remove` for normal edits after the project already exists.

`create --force` is intentionally narrow in scope: it targets the one resolved `tenant_id/project_id` folder only after `client_name`, `tenant_id`, and `project_id` are known. It recreates that tenant/project folder from scratch, including deleting existing generated artifacts and any other files already under that project path, but it does not delete the deployments root or unrelated projects.

`create` owns project identity (`client_name`, `tenant_id`, `project_id`, `region_id`) and initial scaffold creation from the deployments root. Once `config.yaml` already exists, use `component list/add/remove` against that file for day-2 component selection changes. Those commands keep the current identity and existing values intact, and `render` remains the full reconcile step back into `generated/`.

The first `render` after `create` should not require overwrite confirmation just because the project already has the empty `generated/` scaffold plus the placeholder `generated/inventory/deploy-report.md`. The overwrite prompt is intended for rerendering over a previously rendered bundle with meaningful generated content.

In the customer private repo, keep both:

- `config.yaml` as the original render/replace contract
- `generated/` as the deploy contract used by day-2 operations and CI

Rerendering from `config.yaml` is still supported, but it is a manual replace action. The CLI now renders into a hidden sibling staging directory and only swaps it into `generated/` after the new bundle is complete, so a failed rerender leaves the current bundle untouched. The replacement still removes stale or legacy content under `generated/`, including an old `generated/flux/flux-system` subtree. In an interactive terminal, `render` prompts for confirmation before overwrite. In non-interactive contexts, rerender requires `--force`.

For Flux/GitOps, the important safety boundary is Git history, not the local render directory swap. The recommended workflow is: rerender locally, validate/review the new `generated/` diff, then commit and push one final snapshot of the watched path. Do not push an intermediate commit that removes manifests from the watched Git path, and do not routinely unbootstrap/rebootstrap Flux just to replace rendered artifacts.

Sensitive per-project values such as jump-host SSH public keys belong in the private project `config.yaml`, not in the shipped public `component_sources.yaml`. A private customer-local source catalog may still carry `shared.admin_ssh.public_key` as a bootstrap seed, because `create`/`component add` materialize that value into `config.yaml`. Non-sensitive shared defaults such as `shared.admin_ssh.user_name` are also materialized into selected component rows so rerendering works from `config.yaml` without re-reading those values from the catalog. For operator convenience, both the private catalog seed and the per-project `inputs.ssh_public_key` field accept inline `ssh-rsa` / `ssh-ed25519` text or a readable local `.pub` path; the persisted contract is always normalized inline key text.

Local `deploy`/`flux bootstrap` behavior when apps + the bundled `mk8s` component are enabled:

- `deploy` now runs `terraform validate` after render and before apply.
- When Terraform is not already in `PATH`, `deploy`, `terraform plan`, `terraform apply`, `terraform unlock`, and backend-backed Terraform output lookups use a managed Terraform CLI download pinned by `component_sources.yaml` `cli.terraform.version`. The binary is cached under the local nebius-cxcli cache and is not installed system-wide.
- During long-running `terraform apply`, `deploy` and `terraform apply` print one merged status surface: Terraform apply transitions plus a light Nebius MK8s API snapshot. When an enabled `mk8s` component is present and Nebius SDK auth is available, the CLI polls Nebius MK8s API for cluster/node-group status; otherwise it falls back to a simple elapsed heartbeat for the API side.
- The merged status surface is rendered as a multi-line block with distinct TF and API sections so Terraform progress and Nebius resource state are visually separate in the terminal.
- Severity colors are standardized across explicit CLI diagnostics: warnings render in amber and errors render in red.
- If Terraform apply fails, the CLI exits with the Terraform error as the canonical failure and appends the last known merged Terraform/API status snapshot.
- Remote state lock failures are called out separately: the CLI explains that Terraform never acquired the backend lock, so the run created nothing, and points at the stale `.tflock` object metadata when Terraform provides it.
- When Nebius MK8s node-group status reports `ERROR` events, the merged status block includes those alerts from the live SDK event objects and prefers the event's human error text over raw SDK object reprs. Known transient bootstrap warnings such as waiting for ProviderID registration or temporary `Ready=False` node conditions are shown as notes instead of alerts while the node group is still provisioning.
- If the live MK8s API reports an active terminal node-group error during apply or destroy, `deploy` / `terraform apply` / `terraform destroy` now abort the Terraform wait loop early and surface that SDK error directly instead of waiting for a generic Terraform timeout.
- After apply, `deploy` reads the rendered Terraform output `cluster_id` and configures a temporary kubeconfig before applying Flux manifests.
- The bundled `mk8s` component derives endpoint access from `inputs.mk8s_cluster_public_endpoint`, so the CLI automatically selects the public or private control-plane endpoint instead of assuming public access.
- On non-CI local runs, that same built-in MK8s handoff also updates the user kubeconfig at `~/.kube/config` with a `nebius-cxcli` exec-based credential entry, so `kubectl` can be used against the target cluster after `deploy`, `flux apply`, or `flux bootstrap` without installing a separate Nebius CLI.
- `destroy` and `flux destroy` still use the same built-in MK8s handoff for temporary cluster access when they need to reach rendered app resources directly, but they do not persist or switch the user's local `~/.kube/config`.
- When the selected cluster-access endpoint is private, `deploy`, `flux apply`, `flux bootstrap`, `destroy`, and `flux destroy` require the current machine to already have a private network path to the MK8s API. The CLI does not hardcode or auto-provision that path; customer environments can satisfy it with VPNs, routed private networks, subnet routers, SSH/WireGuard tunnels, or by running the command from an in-network runner.
- When app charts are enabled, `deploy`, `flux apply`, and `flux bootstrap` now print a Kubernetes node-status snapshot first, then proceed directly into Flux or validation-specific readiness checks instead of blocking on a generic "all nodes Ready" gate before useful work starts.
- When the generated manifest declares deploy-time MK8s GPU validations, local `deploy` uses the same handed-off kubeconfig after Terraform/Flux work to run them directly with `kubectl`, keeps compact ordered JSON detail reports under `generated/inventory/`, refreshes the combined customer-facing `generated/inventory/deploy-report.md`, and prints the same validation summary in the terminal. The JSON files remain the machine-readable detail contract; the Markdown report is the single human-readable rollup with `Infra`, `Apps`, and `Validations`.
- Generated bundles are expected to carry manifest `deploy.validations` metadata from `render`. If that metadata is missing or malformed, `deploy` now fails fast and requires a rerender instead of recomputing validation specs from the runtime config.
- During those deploy-time MK8s GPU validations, `deploy` now keeps one continuous spinner alive across validation boundaries and live in-cluster progress updates, so the command does not go visually idle between operator readiness, GPU visibility, or NCCL phases.
- Once the built-in MK8s handoff is ready, the local Flux phase now keeps one continuous spinner alive and updates its message through cluster reachability, Flux API discovery, rendered manifest apply, and the final rendered-resource readiness wait so the command does not go visually idle between phases.
- When no app charts are enabled, `render` now emits an empty Flux kustomization without a placeholder repository file. Local `deploy` still prepares the built-in MK8s handoff and refreshes local kubeconfig when that handoff exists, but it skips Flux apply entirely; `flux apply` still refuses to run because there are no enabled charts to apply.
- In non-interactive logs such as GitHub Actions, those same phase updates fall back to stable printed lines instead of transient spinner frames, so CI logs remain readable and do not depend on TTY animation support.
- Generated Flux artifacts are treated as the deploy truth. If an app chart depends on Terraform-backed component outputs, you must rerender after the needed Terraform state exists before treating `generated/flux` as the final GitOps payload.
- Flux render writes explicit Namespace manifests for chart target namespaces before namespaced `HelmRelease` resources, so local `kubectl apply -k generated/flux` does not fail with `namespaces "<name>" not found`.
- Flux uses a split namespace model in this project: shared Flux control-plane and source objects such as `HelmRepository` / `GitRepository` typically live in `flux-system`, while the actual `HelmRelease` and workload pods live in their target app namespace. A workload namespace does not need its own dedicated source object unless it truly uses a different chart or repo source.
- If Flux controllers are missing, `deploy` installs the core Flux controllers into the target cluster automatically using the official Flux install manifest. `flux` CLI is not required for local `deploy`.
- The install manifest version used by local `deploy` comes from `component_sources.yaml` `cli.flux.version`.
- After `kubectl apply -k generated/flux`, `deploy` waits for the rendered Flux `source.toolkit` and `helm.toolkit` resources to report `Ready`, so local deploy does not exit before chart source fetch or Helm reconciliation has actually succeeded.
- Helm chart timeout policy stays catalog-driven: `components.apps.<id>.release.timeout` renders into `HelmRelease.spec.timeout`, and the local Flux wait budget now honors the longest rendered workload timeout plus a short grace window when no explicit CLI timeout override is supplied.
- If Flux controllers had to be installed during `deploy`, the CLI also waits for the required Flux CRD-backed APIs to become discoverable before applying the rendered Flux bundle. This avoids transient `the server could not find the requested resource` races immediately after controller install.
- While that Flux wait is in progress, `deploy` and `flux apply` poll the rendered Flux resources from the cluster with `kubectl get -o json` and print a generic status block showing which `HelmRepository`, `GitRepository`, `HelmRelease`, or `Kustomization` objects are still progressing. This is chart-agnostic and does not hardcode a specific release name.
- When one rendered workload resource reaches a terminal Flux failure state while other rendered workloads are still progressing, the CLI keeps watching the remaining workloads until they settle, then exits non-zero with the failed-resource summary instead of sitting on an unrelated source object until the full outer timeout expires.
- If all rendered workload resources are already `Ready` and only rendered Flux source objects remain pending without publishing a `Ready` condition, the CLI stops waiting and completes with a note instead of hanging until the full timeout. This avoids false hangs on source-controller status edge cases after a successful local app apply.
- `deploy` and `flux apply` are intentionally local direct-apply paths. They do not bootstrap GitOps automatically, because that would require implicit GitHub/Flux bootstrap side effects. If the cluster is not bootstrapped yet, the CLI now finishes the local apply and prints a warning with the exact `nebius-cxcli flux bootstrap <generated-dir>` follow-up command.
- `flux apply` uses that same local app-deploy path without running Terraform apply, so it is the apps-only command for day-2 chart deploys after infra already exists.
- `terraform apply` is safe to rerun sequentially with the same `generated/infra`: it validates the existing generated infra bundle and then relies on Terraform state convergence. It is not safe to run concurrently against the same backend state; Terraform remote locking is the protection there.
- `flux apply` is safe to rerun sequentially with the same `generated/flux`: it applies the existing rendered manifests, skips Flux controller installation when controllers are already present, and waits for the rendered Flux resources to become `Ready`.
- `flux bootstrap` auto-downloads a managed Flux CLI binary from the official Flux GitHub release for the catalog-pinned `cli.flux.version` when `flux` is not already in `PATH`. The binary is cached under the local nebius-cxcli cache and is not installed system-wide.
- `flux bootstrap` resolves the GitHub repo slug from `GITHUB_REPOSITORY` when present, otherwise it falls back to the local git `origin` remote.
- `flux bootstrap` uses the same built-in MK8s handoff instead of hardcoding `mk8s_cluster_id` in CI workflow glue.
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

The publish step creates the annotated tag `nebius-cxcli-vX.Y.Z`. That tag triggers the repository workflow at `.github/workflows/nebius-cxcli-release.yml`, which reruns the same local `make all` verification contract, runs `validate-sources component_sources.yaml` against the real portable catalog, verifies that the wheel version matches the tag, verifies the wheel with `nebius_cxcli.release_catalog verify-wheel`, and publishes the GitHub Release from the tagged commit.
The normal `.github/workflows/nebius-cxcli-ci.yml` workflow uses `validate-sources component_sources.yaml` with source profile `local` instead, so branch changes are validated against the checked-out Terraform modules and Helm charts rather than the remote `ref=main` portable sources. That branch CI workflow only checks that the wheel bundles `component_sources.yaml`; it does not require every bundled chart to be portable before release time.
Those post-`make all` workflow checks use the repo `.venv/bin/python` created by that contract so `nebius_cxcli.release_catalog` imports the editable service package reliably under GitHub Actions.

In source/editable checkouts, runtime version resolution prefers live SCM state over a generated `_version.py` cache: it uses `setuptools-scm` when available and falls back to `git describe` when it is not. The local `./publish-release.sh --publish X.Y.Z` flow also verifies that the tagged source checkout resolves `nebius-cxcli.__version__ == X.Y.Z` before it pushes the release tag.

Release assets for `nebius-cxcli` now include:

- the wheel artifact
- the raw release catalog as a direct editable download, with its Terraform Git module refs pinned to the published release tag

## Commands

Idempotency guide:

- Read-only commands are safe to repeat: `validate-sources`, `validate`, `quota-check`, `validate-generated`, `discover`, `terraform plan`, and `auth --validate-profile`.
- Reconcile/apply commands are sequentially idempotent or convergent for the same target: `render`, `deploy`, `terraform apply`, `flux apply`, `flux bootstrap`, `report write`, `bootstrap-ci`, `auth --create`, and `auth --bootstrap-ci`.
- `create`: create-if-missing for a new `tenant_id/project_id` target; existing resolved targets require explicit overwrite confirmation instead of reconcile.
- Destructive commands are sequentially convergent for the same target but intentionally remove resources: `destroy`, `terraform destroy`, and `flux destroy`. They require confirmation or `--yes`.
- Explicit additive or side-effecting commands are intentionally not idempotent: `component add` creates another component instance on repeat, `auth --recreate` rotates auth material, and `email` sends another message on each run.
- `create --force` and `render --force` are still deterministic with the same inputs, but they are explicit overwrite/reset modes rather than the safer default reconcile flow. For `create`, that overwrite scope is the resolved `tenant_id/project_id` folder, not the entire deployments root.
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
nebius-cxcli quota-check /path/to/config.yaml
nebius-cxcli render /path/to/config.yaml
```

- `validate-sources`
  - Validates the active `component_sources.yaml` catalog: Terraform module sources, Helm chart sources, catalog contract shape, and fast source-structure checks for CLI-friendly Terraform modules and Helm charts.
  - Accepts an optional positional catalog path, for example `nebius-cxcli validate-sources ./component_sources.yaml`.
  - Example: `nebius-cxcli validate-sources ./component_sources.yaml`
- `validate <config.yaml>`
  - Validates the project config contract and runtime shape without the stricter deployment-readiness checks.
  - Runs phased validation with visible progress: config/catalog load, active source checks, dependency checks, Terraform module input/schema checks, then a warning-only live Nebius quota/capacity phase.
  - Prints one concise validated-scope list after the phase run, with separate `infra` and `apps` sections and per-group entries such as `Compute`, `Storage`, `Platform`, or `Workloads`.
  - Confirmed live quota shortages do not fail plain `validate`, but they do switch the final status to a quota-warning summary and print the exact `quota-request` remediation command.
  - Defaults to the global source profile `portable`, so validation fails when the requested render contract would rely on non-portable local Terraform module paths.
  - Example: `nebius-cxcli validate ~/deployments/tenant-id-example/project-id-example/config.yaml`
- `validate --strict <config.yaml>`
  - Adds stricter deployment-readiness checks on top of `validate`, including strict readiness checks against the config, MK8s preflight, and a fail-fast live Nebius quota/capacity phase used before rendering or local deploy.
  - Keeps the same visible phase reporting and then adds strict readiness, MK8s preflight, and the stricter live Nebius quota/capacity phase.
  - Prints the same concise validated-scope list as plain `validate`.
  - The strict quota/capacity phase reuses the same live assessment as `quota-check`, including matching tenant Capacity Block Groups for fabric-bound GPU requests. Confirmed insufficiency now fails `validate --strict`, while unresolved live limits remain warning-only.
  - Example: `nebius-cxcli validate --strict ~/deployments/tenant-id-example/project-id-example/config.yaml`
- `quota-check <config.yaml>`
  - Runs the same live Nebius quota/capacity assessment used by `create`, `render`, and `deploy`, but as an explicit read-only operator command against one project config.
  - For fabric-bound GPU requests, the same assessment also checks active tenant Capacity Block Groups and lets a matching CBG satisfy `compute.instance.gpu.*` and `compute.gpucluster.count` when the regular quota surface alone would otherwise show zero available.
  - Also prints a concise per-component summary for components whose checked quota dimensions were sufficient, plus the exact checked quota names listed one per line. Components with coverage gaps still appear there for the dimensions that were confirmed, with the unresolved parts called out separately below.
  - Returns success when no confirmed insufficiency is found, even if some live quota dimensions remain unresolved; those unresolved limits and coverage gaps are still printed as warnings.
  - Coverage-gap warnings are grouped per component and listed vertically under a `gaps:` section so each unresolved reason appears on its own line.
  - `--all-regions` also prints per-region availability for the same quota shape across all discovered tenant/project regions. It does not change pass/fail semantics, which still follow the selected config region, and it does not prove platform/preset support in those other regions.
  - When quota-check ends with confirmed insufficiency and `--all-regions` was not requested, the CLI prints both the direct `quota-request` remediation command and the exact `quota-check --all-regions` rerun command as suggested next steps.
  - A warning by itself does not mean quota is short. For bundled MK8s, exact `compute.disk.size.*` checks work whenever cxcli can resolve the node-group preset resources plus disk type, either from the interactive/defaulted first-class `inputs.<cpu|gpu>_nodes_boot_disk_*` values or from `inputs.mk8s_<cpu|gpu>_node_group_overrides.template.boot_disk`. If the preset resources or disk type still cannot be resolved exactly, quota-check reports a coverage gap instead of guessing.
  - Returns a non-zero exit status when the enabled infra shape is confirmed to exceed currently available live quota.
  - Example: `nebius-cxcli quota-check ~/deployments/tenant-id-example/project-id-example/config.yaml --all-regions`
- `quota-request <config.yaml>`
  - Reuses the same live quota assessment as `quota-check`, but only plans direct quota allowance requests for the quota dimensions/scopes that are confirmed to be insufficient right now, then attempts those writes through the published API.
  - Uses the published Nebius quota allowance API surface (`QuotaAllowanceService`) instead of driving the web console directly, but direct writes can still be denied for the current identity even when the shortage itself was confirmed live.
  - Requests the constraining tenant/project allowance targets only; unresolved live limits and estimator coverage gaps are still reported, but they are not auto-requested.
  - If Nebius denies the direct quota-allowance write, the command falls back cleanly to a manual console step and prints the exact tenant/project quota entries that still need follow-up under Administration -> Limits -> Quotas.
  - When the report contains coverage gaps only, the command now prints those unresolved reasons before the final no-op summary so the operator can see why nothing was submitted.
  - For bundled MK8s node-group disk-size quota, exact auto-requesting works when cxcli can resolve the node-group preset resources plus disk type and therefore materialize the effective boot-disk size/type, or when the equivalent first-class boot-disk fields / override values are already explicit in `config.yaml`. If the shape still cannot be resolved exactly, the command prints the remaining coverage gap instead of issuing a blind request.
  - Prints the exact target limit per requested scope before attempting submission and then points operators back to Administration → Limits → Quotas in the Nebius web console to submit or track request status.
  - Example: `nebius-cxcli quota-request ~/deployments/tenant-id-example/project-id-example/config.yaml`
- `render <config.yaml>`
  - Generates the deployable bundle under `generated/`, refreshes inventory, and writes `generated/nebius-cxcli-manifest.json`.
  - Runs the same non-strict config preflight used by `validate` before it writes anything: config/catalog load, active source checks, dependency checks, then Terraform module input/schema checks.
  - Rerender now stages the new bundle under a hidden sibling directory and swaps it into `generated/` only after the replacement bundle is complete.
  - The replacement recreates the managed generated bundle from a clean canonical layout without stale files from earlier renders and removes any legacy `generated/flux/flux-system` subtree.
  - Performs a best-effort live Nebius quota check for the rendered infra shape, stores that report in the generated manifest, and warns instead of blocking when quota is insufficient or some quota dimensions cannot be resolved precisely.
  - Coverage-gap-only detail stays in the generated manifest, but routine `render` terminal output does not repeat those non-blocking summaries. Use `quota-check` when you want the full coverage-gap summary in the terminal.
  - Defaults to the global source profile `portable`, which rewrites active local module sources to their portable Git equivalents when available.
  - Use `--source-profile local` only for workstation testing against checked-out local Terraform modules; those generated artifacts are intentionally non-portable.
  - Use `--component-sources-file` or `NEBIUS_CXCLI_COMPONENT_SOURCES_FILE` only when you need to select a non-default catalog file.
  - If `generated/` already contains files, `render` prompts before overwrite in an interactive terminal.
  - In non-interactive contexts, use `nebius-cxcli render --force <config.yaml>` to confirm the replacement explicitly.
  - Example: `nebius-cxcli render ~/deployments/tenant-id-example/project-id-example/config.yaml`

### Customer-side Commands

```bash
nebius-cxcli validate-generated /path/to/generated
nebius-cxcli deploy /path/to/config.yaml
nebius-cxcli destroy /path/to/config.yaml --yes
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
  - Example: `nebius-cxcli validate-generated ~/deployments/tenant-id-example/project-id-example/generated --portable`
- `deploy <config.yaml>`
  - Full local reconcile from the generated bundle: `deploy` resolves the sibling `generated/` directory and loads `generated/nebius-cxcli-manifest.json` as the authoritative deploy input. That keeps the rendered bundle, not the latest source file edits, as the applied contract. Before Terraform apply, `deploy` runs a generated-bundle preflight covering strict deployment-readiness checks against the manifest runtime config, MK8s network preflight, live Nebius quota/capacity validation, Terraform validation for `generated/infra`, and `kubectl kustomize` validation for `generated/flux` when app charts are enabled. Terraform apply runs next, then deploy-report refresh for both infra and apps artifacts, then Flux apply when app charts are enabled. If a built-in cluster handoff such as `mk8s` is enabled, `deploy` still refreshes local kubeconfig access for that cluster even when no app charts are configured. If GitOps bootstrap is not configured yet, the CLI warns and prints the follow-up `flux bootstrap` command when Flux work actually runs.
  - The live quota/capacity preflight includes matching tenant Capacity Block Groups for fabric-bound GPU requests and fails fast with an explicit quota/capacity message when the generated bundle still exceeds currently available capacity.
- Deploy-time MK8s GPU checks are configured per project under `deploy.validations.mk8s_gpu.*`. Use `--skip-validations` to bypass all of them for one run, or repeat `--skip-validation <kind>` for targeted one-run skips such as `nccl` or `gpu-visibility`; those CLI flags do not rewrite `config.yaml`.
- Ongoing GPU health and performance monitoring is intentionally outside that fast deploy loop. NVIDIA positions DCGM Exporter as the Kubernetes telemetry path for Prometheus/Grafana, while deeper DCGM diagnostics are invasive administrator workflows with different run levels and runtimes, so cxcli does not fold those checks into every local `deploy`.
  - Non-blocking quota coverage gaps remain recorded in the generated manifest, but routine `deploy` output focuses on confirmed shortages and live lookup failures. Use `quota-check` for the full coverage-gap summary in the terminal.
  - `deploy` is idempotent in the Terraform/Flux sense: rerunning the same generated bundle converges to no-op, but it is not a create-only path. Existing managed infrastructure or workloads can be updated when the generated bundle differs from live state.
  - Use `nebius-cxcli terraform plan <generated-dir>` first when you need a non-mutating preview of the next reconcile.
  - Nebius API status polling for infra is catalog-driven per Terraform module. The generated manifest snapshots enabled module watcher specs, and `deploy`/`terraform apply` fall back to the active catalog when older generated bundles do not have that metadata yet.
  - Each watcher resolves its `parent_id` and `resource_name` from the enabled component row in `config.yaml`, using the catalog's `status.parent_input` and `status.name_input` paths. For example, `mk8s` reads `inputs.parent_id` plus `inputs.cluster_name`, `managed-postgresql` reads `inputs.parent_id` plus `inputs.name`, `object-storage` reads `inputs.parent_id` plus `inputs.name`, jump-host modules read `inputs.parent_id` plus `inputs.name`, and `mysterybox` expands `inputs.secrets` into one watcher per configured secret name.
  - Status output reads Nebius service-native response fields directly. MK8s watchers fail fast from node-group error events, and the PostgreSQL, SFS, object-storage, compute-instance, and MysteryBox watchers fail fast from terminal Nebius operation status once the resource is visible, so long-running applies do not sit on generic Terraform timeouts after the API already knows the operation has failed.
  - `deploy` does not run `flux bootstrap`; use `flux bootstrap` itself or the generated CI apply workflow when you want GitOps bootstrap/reconcile.
  - `deploy` does not run `bootstrap-ci` automatically, even when the bundle lives inside a git repository. GitHub workflow/environment bootstrap stays an explicit generator-side step.
  - Example: `nebius-cxcli deploy ~/deployments/tenant-id-example/project-id-example/config.yaml`
- `destroy <config.yaml>`
  - Full local teardown from the generated bundle. `destroy` resolves sibling `generated/` from the project config, then uses the generated manifest as the authoritative teardown contract. When enabled apps target an external or current cluster, it deletes the rendered Flux resources first and then runs Terraform destroy against the rendered infra bundle. When the generated bundle destroys the handed-off cluster directly, `destroy` skips the separate Flux delete step and relies on cluster teardown instead.
  - `destroy` is the destructive inverse of `deploy`. It does not rerender from `config.yaml`, and it does not uninstall Flux controllers or mutate GitHub CI/bootstrap state.
  - Rendered app teardown is best-effort. If deleting the rendered Flux resources fails, the CLI warns and still continues with Terraform destroy because the rendered infra bundle is the authoritative teardown path.
  - During destroy recovery, the CLI can automatically clear a stale Terraform backend lock and retry once. If destroy is still blocked by a live MK8s node-group create that is stuck in terminal-error provisioning, the CLI can delete that stuck node group via the Nebius SDK and retry destroy again.
  - The command requires explicit confirmation in interactive mode and `--yes` in non-interactive mode.
  - If you only want the infra teardown, use `terraform destroy`. If you only want the rendered app teardown, use `flux destroy`.
  - Example: `nebius-cxcli destroy ~/deployments/tenant-id-example/project-id-example/config.yaml --yes`
- `terraform apply <generated-dir>`
  - Infra-only apply from the generated Terraform bundle. Safe to rerun sequentially for convergence, and does not depend on resolving the original source catalog's module paths.
  - Example: `nebius-cxcli terraform apply ~/deployments/tenant-id-example/project-id-example/generated`
- `terraform destroy <generated-dir>`
  - Infra-only destroy from the generated Terraform bundle. Destructive by intent, requires confirmation or `--yes`, and reuses the same generated-bundle runtime auth/backend/status machinery as `terraform apply`.
  - Uses the same guarded destroy-recovery path as top-level `destroy`: stale-lock auto-unlock/retry first, then direct MK8s node-group cleanup only for live stuck create operations.
  - Example: `nebius-cxcli terraform destroy ~/deployments/tenant-id-example/project-id-example/generated --yes`
- `flux apply <generated-dir>`
  - Apps-only direct apply from the generated Flux bundle. Safe to rerun sequentially for day-2 reconciliation. If GitOps bootstrap is not configured yet, the CLI warns and prints the follow-up `flux bootstrap` command.
  - Example: `nebius-cxcli flux apply ~/deployments/tenant-id-example/project-id-example/generated`
- `flux destroy <generated-dir>`
  - Apps-only direct delete from the generated Flux bundle using the same rendered manifests that `flux apply` manages. Destructive by intent and requires confirmation or `--yes`.
  - If the target cluster is reachable but the Flux CRDs are already absent, the CLI prints a skip note instead of surfacing raw `kubectl` resource-mapping errors.
  - Example: `nebius-cxcli flux destroy ~/deployments/tenant-id-example/project-id-example/generated --yes`
- `flux bootstrap <generated-dir>`
  - GitOps bootstrap/reconcile path from the generated Flux bundle. Use this when the cluster should watch the Git repo/path with Flux.
  - Normal day-2 updates should replace `generated/` locally, then commit and push one final watched-path snapshot. Do not unbootstrap/rebootstrap Flux just to roll out a new rendered bundle.
  - Example: `nebius-cxcli flux bootstrap ~/deployments/tenant-id-example/project-id-example/generated`

### Supporting Commands

```bash
nebius-cxcli component list /path/to/config.yaml
nebius-cxcli component add /path/to/config.yaml
nebius-cxcli component remove /path/to/config.yaml
nebius-cxcli create /path/to/deployments-root
nebius-cxcli quota-check /path/to/config.yaml
nebius-cxcli bootstrap-ci /path/to/config.yaml
nebius-cxcli discover /path/to/deployments-root
nebius-cxcli terraform plan /path/to/generated
nebius-cxcli terraform destroy /path/to/generated --yes
nebius-cxcli terraform unlock /path/to/generated
nebius-cxcli flux destroy /path/to/generated --yes
nebius-cxcli report write /path/to/config.yaml
nebius-cxcli destroy /path/to/config.yaml --yes
nebius-cxcli email /path/to/config.yaml
nebius-cxcli auth --project-config /path/to/config.yaml --validate-profile
```

- Positional target quick map:
  - `create`: pass the deployments root directory.
  - `discover`: pass the deployments root or any narrower directory under it, including one project directory or `generated/`.
  - `component`, `validate`, `quota-check`, `render`, `bootstrap-ci`, `deploy`, `destroy`, `report write`, `email`: pass the project `config.yaml`.
  - `validate-generated`, `terraform *`, `flux *`: pass `generated/`, one of its subdirectories, or a file under that tree as accepted by the command.
  - `validate-sources`: optional explicit `component_sources.yaml` path.
  - `auth`: no positional path; use `--project-config <config.yaml>` or `--project-id`, or omit both with `--validate-profile` to inspect all cached profiles.

- `component list <config.yaml>`
  - Shows enabled and available catalog entries for the current project, split between infra modules and app charts.
  - Read-only inspection command for deciding the next add/remove action against the current `config.yaml`.
  - Example: `nebius-cxcli component list ~/deployments/tenant-id-example/project-id-example/config.yaml`
- `component add <config.yaml>`
  - Adds source-defined infra module rows or app chart rows to an existing project config without recreating the project scaffold.
  - Catalog entries are reusable component types. Each add creates a new enabled component instance with its own `instance_id`, so you can add `mk8s`, `managed-postgresql`, `object-storage`, or app charts multiple times in one project.
  - Interactive mode prompts separately for infra and apps, confirms the selection, auto-resolves app chart dependencies, and then runs the field wizard only for the newly added components.
  - When that wizard reaches per-component field phases, infra components default to `y` and app charts default to `n`.
  - That field wizard offers all discoverable required and optional fields for each new component, including editable literal catalog defaults. Required fields must be filled before advancing; optional blanks stay implicit when they still match module/chart defaults.
  - Source validation runs by default, mirroring `create`. Use `--no-validate-sources` only when you intentionally want to skip catalog preflight.
  - The command revalidates the existing Nebius tenant/project scope before provider-backed field prompts, so missing SDK credentials or inaccessible scope are surfaced as explicit errors.
  - Complex Terraform inputs such as `allowed_cidrs`, `clients`, `secrets`, and `mk8s_*_overrides` are edited as YAML/JSON values in the wizard.
  - Non-interactive mode accepts component ids directly, for example `nebius-cxcli component add /path/to/config.yaml managed-postgresql object-storage gateway-helm --no-interactive`.
  - Repeating the same component id adds another instance. You can also request an explicit instance id with `<component-id>@<instance-id>`, for example `object-storage@logs-bucket`.
  - `object-storage` now represents one bucket per enabled module instance and requires `inputs.name`.
  - Existing component values are preserved. After the edit, run `validate`, optionally `validate --strict`, and `render` again.
  - Example: `nebius-cxcli component add ~/deployments/tenant-id-example/project-id-example/config.yaml managed-postgresql --no-interactive`
- `component remove <config.yaml>`
  - Removes enabled infra module rows or app chart rows from an existing config.
  - Interactive mode prompts separately for infra and apps and asks for confirmation before editing.
  - When multiple instances of the same component type are enabled, remove by exact `instance_id` or `<component-id>@<instance-id>`.
  - The command fails fast when the removal would leave unresolved app dependencies or component input bindings.
  - After the edit, run `validate`, optionally `validate --strict`, and `render` again.
  - Example: `nebius-cxcli component remove ~/deployments/tenant-id-example/project-id-example/config.yaml managed-postgresql@managed-postgresql-2 --no-interactive`
- `create <deployments-root>`
  - Scaffolds one tenant/project folder with `config.yaml` and the generated-folder skeleton.
  - Interactive `create` prompts for `tenant_id` / `project_id` first and only warns when that resolved target already exists; choosing a different new project under the same deployments root does not trigger an overwrite warning.
  - Unless you explicitly pass `--tenant-id` / `--project-id`, interactive `create` starts those identity prompts blank instead of prefilling values from an existing project under the deployments root.
  - When the resolved `tenant_id/project_id` target already exists, interactive `create` warns and asks for confirmation before recreating that tenant/project folder from scratch; non-interactive reruns require `--force`.
- Existing project `client_info` values are not offered back as defaults; overwrite restarts those prompts from the normal create defaults, existing component rows are not merged, and files already under that resolved tenant/project path are deleted during the overwrite.
  - After writing the resulting `config.yaml`, `create` runs the same non-strict runtime validation as `validate` by default. Use `--no-validate-config` only when you intentionally want to skip that post-write check.
  - `create` also runs a best-effort live Nebius quota check for bundled infra components and warns when the selected shape already exceeds current quota. For fabric-bound GPU requests, that same check also considers active matching tenant Capacity Block Groups before deciding the shape is short. It does not block render or config edits.
  - Non-blocking quota coverage-gap detail stays available through `quota-check` and the generated manifest rather than being repeated during normal `create` output.
  - In the bundled MK8s flow, `infiniband_fabric` is now a dependent follow-up to the selected GPU preset rather than an early manual guess: if the chosen preset's live SDK metadata does not allow GPU clustering, the fabric prompt is skipped and any stale fabric value fails fast at render/validate instead of surfacing first at `terraform apply`.
  - In interactive mode, phase prompts still use `q` to stop the wizard; field prompts use `q` to back out one nested prompt level and `qq` to stop the wizard. The command still writes the current project config and warns only when required fields remain unresolved.
  - For selected components, the field wizard offers all discoverable required and optional fields, including editable literal catalog defaults. Required blanks are rejected immediately; optional blanks keep defaults implicit when possible.
  - Per-component field phases default to `y` for infra and `n` for apps.
  - Example: `nebius-cxcli create ~/deployments`
- `quota-check <config.yaml>`
  - Runs a live Nebius quota check for the enabled infra components in the current project config without rendering or deploying anything.
  - Uses the same SDK-backed quota logic as `create`, `render`, and `deploy`, including live compute preset lookups for MK8s, jump hosts, and managed PostgreSQL.
  - For fabric-bound GPU requests, it also checks active tenant Capacity Block Groups and lets matching CBG capacity satisfy `compute.instance.gpu.*` and `compute.gpucluster.count` when the regular quota surface alone would otherwise show zero available.
  - Prints a concise per-component confirmed summary for the quota dimensions that were successfully checked, including the exact checked quota names listed one per line. Components with confirmed shortages or unresolved live limits stay out of that list; components with coverage gaps still appear there with a partial-coverage note, and the missing dimensions are listed separately.
  - Returns non-zero only when quota insufficiency is confirmed. Coverage gaps, unresolved live limits, or partial quota lookup failures are reported as warnings but do not make the command fail on their own.
  - `--all-regions` additionally replays the current config's quota requirements across all discovered tenant/project regions and prints per-region availability for the same shape. The selected config region still decides pass/fail, and the replay does not revalidate region-specific platform or preset availability.
  - When quota-check reports confirmed insufficiency and `--all-regions` was not requested, the CLI suggests the exact `quota-check --all-regions` rerun command as the next diagnostic step.
  - Coverage-gap-only warnings mean the estimator could not check every quota dimension from the current config/API surface; they do not by themselves imply a shortage in the already-checked GPU/CPU quotas. The unresolved reasons are listed one per line under the affected component.
  - Example: `nebius-cxcli quota-check ~/deployments/tenant-id-example/project-id-example/config.yaml`
- `bootstrap-ci <config.yaml>`
  - Generates or reconciles the customer GitHub Actions workflow, always reconciles GitHub email settings from local `email --setup`, and optionally bootstraps/syncs the required Nebius CI auth secrets. The generated workflow watches and deploys only canonical `<tenant>/<project>/generated/**` paths.
  - The workflow file is CLI-managed. Re-running `bootstrap-ci` automatically reconciles `.github/workflows/nebius-deployments.yml` to the latest generated contract and is idempotent when no drift exists.
  - Generated workflows validate changed bundles with `nebius-cxcli validate-generated --portable` before Terraform plan/apply.
  - Generated workflows also support manual `workflow_dispatch`. Manual runs switch discovery to `nebius-cxcli discover --all <scope>`, so every tracked project under the configured deployments scope is included even when there is no fresh git diff.
  - Generated workflows rely on the same generated-bundle CLI commands, which recreate ignored `generated/infra/terraform.auto.tfvars.json` from `generated/nebius-cxcli-manifest.json` before Terraform runs.
  - Generated workflows do not install the standalone `nebius` CLI. MK8s kubeconfig generation and token retrieval stay inside `nebius-cxcli` via the Nebius SDK.
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
  - Example: `nebius-cxcli bootstrap-ci ~/deployments/tenant-id-example/project-id-example/config.yaml`
- `discover <deployment-scope-dir>`
  - Returns changed deployment projects for CI matrix generation.
  - Accepts the deployments root or any narrower directory under it, including one project directory or `generated/`.
  - Scope filtering is project-aware: both `--all` and normal changed-file discovery still resolve the matching project when the scope is a project subdirectory such as `generated/`.
  - Example: `nebius-cxcli discover ~/deployments --all`
- `validate-generated <generated-path>`
  - Validates an existing rendered bundle from `generated/`, one of its subdirectories, or a file under that tree.
  - Example: `nebius-cxcli validate-generated ~/deployments/tenant-id-example/project-id-example/generated`
- `terraform plan <generated-path>`
  - Infra-only plan from the generated Terraform bundle.
  - Example: `nebius-cxcli terraform plan ~/deployments/tenant-id-example/project-id-example/generated`
- `terraform destroy <generated-path>`
  - Destroys the generated Terraform bundle in place after an explicit confirmation or `--yes`.
  - Can auto-clear a stale Terraform state lock and retry once, and can clean up a live stuck MK8s node-group create before retrying again when that is the remaining destroy blocker.
  - Example: `nebius-cxcli terraform destroy ~/deployments/tenant-id-example/project-id-example/generated --yes`
- `terraform unlock <generated-path>`
  - Inspects and clears a stale remote Terraform state lock for the generated infra bundle.
  - Example: `nebius-cxcli terraform unlock ~/deployments/tenant-id-example/project-id-example/generated --force`
- `flux destroy <generated-path>`
  - Deletes the rendered Flux resources from the target cluster after an explicit confirmation or `--yes`.
  - Example: `nebius-cxcli flux destroy ~/deployments/tenant-id-example/project-id-example/generated --yes`
- `destroy <config.yaml>`
  - Deletes rendered apps first only when they target an external or current cluster; otherwise it destroys the handed-off cluster directly with Terraform after an explicit confirmation or `--yes`.
  - Resolves sibling `generated/` automatically and still uses the generated manifest as the authoritative teardown contract.
  - Example: `nebius-cxcli destroy ~/deployments/tenant-id-example/project-id-example/config.yaml --yes`
- `report write <config.yaml>`
  - Refreshes the local human-readable deploy report from the rendered bundle resolved from sibling `generated/`.
  - Example: `nebius-cxcli report write ~/deployments/tenant-id-example/project-id-example/config.yaml`
- `email [config.yaml]`
  - Sends only `generated/inventory/deploy-report.md` to `client_info.notifications.email` via SMTP and fails fast if that file is missing.
  - Omit the path only when using `--setup`.
  - Resolves sibling `generated/` automatically and reads the recipient/runtime snapshot from the generated manifest rather than live source edits.
  - The recipient email comes from the generated-bundle runtime config snapshot in `generated/nebius-cxcli-manifest.json`, not from the rendered report artifact.
  - SMTP is disabled by default. Run `nebius-cxcli email --setup` to create, update, or remove local SMTP settings under `~/.config/nebius-cxcli/email.yaml`.
  - Local email config stores host/port/STARTTLS/from and optional username/password. Runtime `SMTP_HOST`, `SMTP_PORT`, `SMTP_STARTTLS`, `SMTP_FROM`, `SMTP_USERNAME`, and `SMTP_PASSWORD` still override those local values when set.
  - Per-client send/no-send stays in `config.yaml`: `client_info.notifications.email_enabled: true|false`.
  - When `client_info.notifications.email_enabled` is `true` but SMTP is missing, the command warns and exits successfully instead of failing the deploy/email workflow.
  - The email path masks tenant and project identifiers in the subject/body down to their last 4 characters; the on-disk `deploy-report.md` stays unchanged.
  - Example: `nebius-cxcli email ~/deployments/tenant-id-example/project-id-example/config.yaml`
- `auth`
  - Manages runtime auth profiles and optional GitHub environment secret sync.
  - Example: `nebius-cxcli auth --project-config ~/deployments/tenant-id-example/project-id-example/config.yaml --validate-profile`

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
- `quota-check`: `--all-regions`
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

`bootstrap-ci <config.yaml>` remains the full CI workflow bootstrap command and can still perform complete CI auth bootstrap/sync for that config. The generated customer workflow is artifact-driven: it watches and deploys only canonical `<tenant>/<project>/generated/**` paths. Re-running the command automatically reconciles the CLI-managed workflow file to the latest template, always reconciles local SMTP settings into the matching GitHub Environment, and uses `--github-repo` only as an explicit override when repo auto-detection is wrong or unavailable.

`deploy <config.yaml>` is intentionally separate from `bootstrap-ci <config.yaml>`. Local/customer-side deploy commands operate only on the committed generated bundle and runtime auth material; they do not create or update GitHub workflows, GitHub environments, or CI secrets automatically.

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

`deploy <config.yaml>` (default `--auto-auth-bootstrap`) uses the same runtime auth creation core as `auth --create` when auth material is missing.

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

# Non-interactive create
nebius-cxcli create /path/to/deployments-root \
  --client-name client-a \
  --tenant-id tenant-123 \
  --project-id project-123 \
  --infra mk8s \
  --app n8n \
  --app-namespace n8n=automation \
  --app-releasename n8n=workflow-core \
  --no-interactive

# Non-interactive overwrite of an existing resolved tenant/project folder
nebius-cxcli create /path/to/deployments-root \
  --client-name client-a \
  --tenant-id tenant-123 \
  --project-id project-123 \
  --force \
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

# Local deploy from rendered artifacts
nebius-cxcli deploy /path/to/config.yaml

# Local destroy from the project entrypoint
nebius-cxcli destroy /path/to/config.yaml --yes

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

Developer prerequisites for local `make venv`, `make lint`, and `make all`:

- Required baseline tools:
  - Python `3.12+`
  - `make`
  - `git`
  - Python virtual-environment support
  - A native build toolchain for Python packages when prebuilt wheels are unavailable
- Optional command-path tools:
  - `kubectl` for `deploy`, `flux apply`, `flux bootstrap`, and Flux readiness checks
  - `helm` for strict Helm source validation in `validate-sources`
  - `aws` CLI for `terraform unlock`
  - `terraform` and `flux` are auto-managed by `nebius-cxcli` when missing for the command paths that support managed downloads

macOS with Homebrew:

```bash
xcode-select --install
brew install python@3.12 git
```

If you want a Homebrew-managed GNU Make as well:

```bash
brew install make
```

Homebrew installs GNU Make as `gmake`; the repo Makefile also works with the default `/usr/bin/make` that comes from Xcode Command Line Tools.

Optional macOS CLI tools:

```bash
brew install kubectl helm awscli
```

Linux with `apt`:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip make git build-essential
```

Optional Linux CLI tools:

- Install `kubectl`, `helm`, and `aws` CLI if you plan to use those command paths locally.
- `awscli` is commonly available from `apt`; `kubectl` and `helm` are often installed from their vendor-maintained apt repositories depending on distro version.

```bash
make venv
make lint
make test-unit
make coverage
make all
```

`make all` reuses the repo `.venv` for the wheel build (`python -m build --wheel --no-isolation`) and fans that build out in parallel with the lint/test gate after env setup instead of creating a second isolated build env. If you change build requirements, rerun `make venv` first so that shared environment is refreshed.

Developer workflow targets:

- `make test-unit`: fast default pytest lane. Today it runs the repo test suite with `-m "not integration"` and blocks live network access by default through `tests/conftest.py`.
- `make test-integration`: reserved explicit integration lane. It runs `tests/integration` when that tree contains tests and exits cleanly when the lane is still empty.
- `make coverage`: unit-lane coverage report via `pytest-cov`.
- `make test`: alias of `make test-unit`.

Useful checks:

```bash
python -m nebius_cxcli --help
python -m nebius_cxcli create --help
python -m nebius_cxcli auth --help
python -m mypy src/nebius_cxcli/provider_options.py
npx pyright src/nebius_cxcli/provider_options.py
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
- The shipped catalogs avoid tenant/admin-specific key material. Project-scoped SSH public keys belong in the private deployment repo `config.yaml`; a private customer-local `component_sources.yaml` may carry `shared.admin_ssh.public_key` only as a seed that `create`/`component add` resolve and copy into that config.
- Operator-facing SSH public key inputs accept only `ssh-rsa` and `ssh-ed25519`, either inline or via a readable local `.pub` file path. Local paths are a convenience input only; `config.yaml` and generated manifests are normalized back to inline key text.
- `config.yaml` is the canonical render/reset contract and should be versioned in the private deployment repo.
- `generated/` is the deploy contract and should also be versioned, except for ignored runtime/transient files.
- Managed deployments `.gitignore` keeps generated Terraform runtime files and generated tfvars out of git, but does not ignore `config.yaml` or deployable generated manifests, and it intentionally does not add unrelated repo-development ignores such as `.coverage` or `*.tgz`.
- Keep `generated/infra/terraform.auto.tfvars.json` ignored even in a private repo: it is a generated, sensitive duplicate of values already present in `config.yaml`.
- Generated-bundle CLI commands such as `validate-generated`, `terraform plan/apply`, and `deploy` recreate `generated/infra/terraform.auto.tfvars.json` from `generated/nebius-cxcli-manifest.json` before Terraform runs, and generated workflows use those same commands instead of carrying separate inline restore logic.
- GitHub sync requires a token with permission to write GitHub environment secrets.
- Key rotation is explicit with `auth --recreate` and automatic in deploy only when runtime auth bootstrap is needed.
