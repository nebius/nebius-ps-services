# nebius-cxcli

`nebius-cxcli` is the Nebius customer experience CLI and an end-to-end automation workflow generator. From one per-project `config.yaml`, it renders a deployable customer artifact bundle: Terraform, Flux, reports, and CI workflow artifacts.

After render, deployment still operates on the generated bundle. `deploy` takes
`config.yaml`, resolves sibling `generated/`, and uses the generated manifest as
the authoritative deploy contract so source changes after render do not silently
change what gets applied.

## Table of Contents

Use this guide by task:

| Need | Start Here |
| --- | --- |
| First project or local run | [Quick Start Guide](#quick-start-guide), then [Recommended Workflow](#recommended-workflow) |
| Existing Soperator cluster or Soperator upgrade | [Soperator Commands](#soperator-commands) |
| MK8s node-template, node-group, or chart upgrades | [Upgrade](#upgrade) |
| Post-deploy smoke or benchmark validation | [Acceptance Testing](#acceptance-testing) |
| Command flags and generated-bundle operations | [Commands](#commands) |
| Catalog authoring and schema details | [Runtime Metadata](#runtime-metadata) and [Catalog File Reference](#catalog-file-reference) |
| Runtime credentials, CI, and security boundaries | [Auth Workflow](#auth-workflow), [Development](#development), and [Security Notes](#security-notes) |

- [Quick Start Guide](#quick-start-guide)
- [Core Concepts](#core-concepts)
- [Features](#features)
- [Prerequisites and Installation](#prerequisites-and-installation)
- [Runtime Metadata](#runtime-metadata)
- [Catalog File Reference](#catalog-file-reference)
- [Recommended Workflow](#recommended-workflow)
- [Acceptance Testing](#acceptance-testing)
  - [Smoke Tests](#smoke-tests)
  - [Benchmark Tests](#benchmark-tests)
  - [NCCL Suite Selection](#nccl-suite-selection)
- [Soperator Commands](#soperator-commands)
  - [Soperator Command Map](#soperator-command-map)
  - [CXCLI Managed Soperator Clusters](#cxcli-managed-soperator-clusters)
  - [Soperator Slurm Scheduling And Command Examples](#soperator-slurm-scheduling-and-command-examples)
  - [External Soperator Onboarding](#external-soperator-onboarding)
  - [External Soperator Upgrade](#external-soperator-upgrade)
  - [Soperator Cluster Upgrade](#soperator-cluster-upgrade)
  - [Soperator Rules and Safety Checks](#soperator-rules-and-safety-checks)
- [Upgrade](#upgrade)
  - [When To Use upgrade](#when-to-use-upgrade)
  - [Upgrade Principles](#upgrade-principles)
  - [Node Template Upgrade](#node-template-upgrade)
  - [Node-Group Migration](#node-group-migration)
  - [Upgrade Strategies](#upgrade-strategies)
  - [Upgrade Examples](#upgrade-examples)
  - [Helm Chart Upgrades](#helm-chart-upgrades)
- [Releases](#releases)
- [Commands](#commands)
  - [Generator-side Commands](#generator-side-commands)
  - [Customer-side Commands](#customer-side-commands)
  - [Command Examples](#command-examples)
  - [Supporting Commands](#supporting-commands)
- [Auth Workflow](#auth-workflow)
- [Development](#development)
- [Security Notes](#security-notes)

## Quick Start Guide

Typical local flow, with validation and quota-remediation checkpoints shown:

```bash
nebius-cxcli --version
nebius-cxcli --help
nebius-cxcli create <deployments-root>
nebius-cxcli quota-check <config.yaml>
nebius-cxcli quota-request <config.yaml>
nebius-cxcli validate <config.yaml>
nebius-cxcli render <config.yaml>
nebius-cxcli validate-generated <generated-dir>
nebius-cxcli deploy <config.yaml>
nebius-cxcli bootstrap-ci <config.yaml>
```

- `nebius-cxcli --version`: print the installed CLI version.
- `nebius-cxcli --help`: show the command surface and path contracts.
- `nebius-cxcli create <deployments-root>`: create one tenant/project scaffold under a deployments root.
- `nebius-cxcli quota-check <config.yaml>`: assess live Nebius quota/capacity for enabled infra.
- `nebius-cxcli quota-request <config.yaml>`: plan or submit requests for confirmed shortages.
- `nebius-cxcli validate <config.yaml>`: validate source config, readiness, VPC networking preflight, and live quota/capacity.
- `nebius-cxcli render <config.yaml>`: turn source config into a deployable `generated/` bundle.
- `nebius-cxcli validate-generated <generated-dir>`: validate the rendered bundle without rerendering.
- `nebius-cxcli deploy <config.yaml>`: resolve sibling `generated/` and apply that rendered bundle.
- `nebius-cxcli bootstrap-ci <config.yaml>`: generate or reconcile the customer CI workflow.

## Core Concepts

- `config.yaml` is the operator-facing orchestration contract. It stores project identity, selected component instances, target deploy settings, and explicit overrides.
- `generated/` is the deploy contract. It contains Terraform, Flux, report, and manifest snapshots that customer-side commands and CI use after render.
- Infra components come from Terraform module sources. Terraform owns desired-state planning, apply/destroy behavior, remote state, locking, and module variable/output interfaces.
- App components come from Helm chart sources. Flux and Helm own app
  reconciliation. cxcli apps are installed only onto enabled built-in MK8s
  targets or onboarded external Nebius MK8s targets in the same project;
  app-only bundles without a cluster target are not supported.
- The Nebius SDK is used for dynamic discovery, validation, status polling,
  quota/capacity checks, guardrails, and bounded existing-network VPC
  parent private-pool CIDR extension performed by the guided subnet wizard.
  Terraform owns config-managed infra lifecycle, including VPC networks,
  private pools, and subnets through `infra:vpc`. New VPC networks created by
  Terraform rely on Nebius' default public pool and default route table unless
  direct config intentionally supplies explicit public pool IDs or subnet route
  tables.
- Component config is dynamic: infra rows live in `infra.components[]`, app rows live in `apps.charts[]`, and reusable component types can be instantiated more than once when the `instance_id` is unique.
- `create` is for initial project scaffolding. Day-2 component selection changes use `component list/add/remove --config <config.yaml>`; after any change, rerun `validate` and `render`.
- For app rows, `id` names the chart type and `instance_id` names the MK8s target. Built-in MK8s targets use the cluster `instance_id` as the app target identity, so target-bound app rows use `<chart-id>@<target-id>`, such as `nvidia-gpu-operator@cluster2`.
- In the interactive `create` wizard, the entered MK8s `cluster.cluster_name`
  becomes the target `instance_id` before app defaults are previewed. Rendered
  Terraform/Flux artifacts use that target identity, not
  `client_info.client_name`; Soperator-created SFS filesystem `name` and
  `mount_tag` defaults also use `<cluster-name>-<role>`. Terraform module labels
  may still replace hyphens with underscores for HCL syntax.
- Authored `config.yaml` does not use `apps.charts[].target_ref`; any internal generated `target_ref` is derived from and must equal the same target `instance_id`.
- Terraform outputs consumed by app bindings, deploy, or bootstrap behavior are stable interfaces. Renaming or changing one is a breaking contract change.
- Generated-bundle commands recreate ignored Terraform tfvars from the generated manifest before Terraform runs. Use `nebius-cxcli terraform plan <generated>`, `nebius-cxcli terraform apply <generated>`, or `nebius-cxcli deploy <config.yaml>` from a fresh checkout.
- `destroy` is the project-wide destructive path: it tears down all rendered
  resources represented by that generated bundle/runtime snapshot rather than
  rerendering from post-render source edits. For onboarded external MK8s
  targets, the external cluster and its node groups stay outside Terraform
  ownership and are not destroyed; only cxcli-managed app resources and
  explicitly owned add-on infra are removed.

## Features

- Guided project creation plus day-2 `component add` and `component remove` for Terraform-backed infra modules and Helm-backed app charts.
- Source-driven catalog model with reusable component types, dynamic Nebius-backed wizard choices, Helm dependency discovery, and target-scoped app binding to MK8s.
- Deterministic render output under `generated/`: Terraform, Flux, Grafana dashboard assets, report artifacts, and `generated/nebius-cxcli-manifest.json`, the snapshot used to operate from the rendered bundle.
- Source and generated validation, including deployment readiness, Terraform validation, Flux manifest validation, live quota/capacity checks, and live VPC network/subnet hierarchy preflight before subnet-attached resources are created.
- Terraform-owned VPC creation through `infra:vpc`, with live SDK discovery
  only for choosing and validating existing networks, subnets, and private
  pools. New networks inherit Nebius public-pool and default-route behavior by
  default so VM-style public IP allocations work in planned custom subnets.
- Local deploy/destroy and Flux apply/destroy flows for rendered bundles, with Nebius service-native status.
- `upgrade node-template` performs SDK-backed MK8s planning and staged
  Terraform upgrades for Terraform-managed MK8s node-group Kubernetes version,
  node OS image, and Nebius-image GPU stack rolling updates. Non-dry runs
  finish with a final MK8s readiness check against the live control plane and
  selected node groups.
- GitOps bootstrap/reconcile flows for rendered Flux trees.
- Bundled MK8s GPU automation for NVIDIA GPU Operator, Network Operator, DCGM
  metrics, bounded deploy-time GPU visibility, and explicit NCCL acceptance
  benchmarks.
- Bundled Soperator self-deployment chart entry plus sibling MK8s, SFS, and
  VM-based NFS infrastructure components for Slurm clusters on Nebius MK8s.
  Existing-cluster Soperator installs map roles onto the selected MK8s node
  groups so workers can stay on GPU groups while system helpers stay on CPU
  groups.
- Optional Soperator child-chart features for Slack job-state notifications,
  active checks, jail backups, and Soperator DCGM job-mapping telemetry are
  configured under the single `apps:soperator` row. Production-training profiles
  keep ActiveChecks, the checks controller, ActiveChecks install wait, and the
  Soperator DCGM exporter disabled unless the operator explicitly opts in.
  Slack webhook URLs and backup credentials stay in runtime Kubernetes Secrets
  instead of `config.yaml` or generated Flux manifests.
- Target-scoped observability with Nebius Observability Agent, Grafana datasource provisioning, cxcli-owned dashboards, dashboard validation, deploy-report links, and visible Grafana runtime diagnostics when public URL reconciliation cannot finish.
- `grafana` exports or normalizes dashboard JSON and can attach deploy-ready imports to `component_sources.yaml`.
- Native External Secrets Operator for MysteryBox sync with generated `ClusterSecretStore`/`ExternalSecret` resources, and runtime connectivity validation.
- Customer CI workflow generation plus runtime auth/profile helpers for local and generated workflow use.
- Human-readable inventory and deploy reports, with optional email delivery.

## Prerequisites and Installation

### Runtime baseline

- Python `3.12`, `3.13`, or `3.14` is required (`requires-python = ">=3.12,<3.15"`).
- Nebius API credentials/profile are required for commands that talk to Nebius APIs such as `validate`, `quota-check`, `quota-request`, `render`, `deploy`, `upgrade`, and `auth`.
- A standalone `nebius` CLI install is not required for the normal query/render/deploy flow; `nebius-cxcli` uses the Nebius Python SDK directly for those paths. The automatic submission branch of `quota-request` is internal-only: it reuses `nebius iam get-access-token` plus the internal `npc` CLI, so it works only on the Nebius internal network for Nebius employees/operators.

### Access requirements

- If you rely on managed Terraform downloads, the machine needs network access to HashiCorp releases.
- If you rely on managed Flux downloads, the machine needs network access to GitHub releases.
- `bootstrap-ci` needs GitHub API access plus `GH_TOKEN` or `GITHUB_TOKEN` (or `--github-token-env <ENV>`).
- `discover` is local git/filesystem discovery over readable project `config.yaml` files; it does not need Nebius API credentials.
- If the target MK8s control-plane endpoint is private, `deploy`, `upgrade`, `destroy`, `flux apply`, `flux destroy`, and `flux bootstrap` require an existing private network path to that cluster.

### Install `nebius-cxcli`

From a local checkout:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
nebius-cxcli --version
```

Directly from the git repository:

```bash
python3.12 -m venv ~/.venvs/nebius-cxcli
source ~/.venvs/nebius-cxcli/bin/activate
python -m pip install --upgrade pip
python -m pip install "git+https://github.com/nebius/nebius-ps-services.git@<branch-or-tag>#subdirectory=services/nebius-cxcli"
nebius-cxcli --version
```

`make` is only required for repo development (`make venv`, `make lint`, `make test-unit`, `make all`); it is not required to use an installed `nebius-cxcli`.

### Install external tools

macOS with Homebrew:

```bash
xcode-select --install
brew install python@3.12 git
brew install kubectl helm awscli
```

Linux with `apt`:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git build-essential awscli
```

On Linux, install `kubectl` and `helm` from their vendor-maintained apt repositories when you need those command paths locally.

Tool notes:

- `terraform` is used for Terraform-backed validation, render lockfile generation, `terraform plan`, `terraform apply`, `terraform unlock`, and backend-backed Terraform output lookups. For those supported command paths, `nebius-cxcli` downloads Terraform into its local cache when it is missing instead of requiring a system-wide install. Managed Terraform downloads verify HashiCorp's published SHA256 manifest before installing or reusing a cached binary.
- `flux` is used for `flux bootstrap`. For that supported command path, `nebius-cxcli` downloads Flux into its local cache when it is missing instead of requiring a system-wide install. Managed Flux downloads verify the published Flux checksum manifest before installing or reusing a cached binary.
- `kubectl` is used for `validate-generated`, `deploy`, `destroy`, `flux apply`, `flux destroy`, `flux bootstrap`, MK8s GPU deployment testing, and acceptance-test flows.
- `helm` is used for `validate-sources` and selected app chart source/metadata validation paths. When source validation is enabled, `create` validates infra sources before any existing project overwrite confirmation, then validates only the selected app chart sources plus auto-enabled app dependencies after app selection. It is not required for the normal `render`, `deploy`, `destroy`, `flux apply`, or `flux destroy` flow.
- `aws` CLI is used for `terraform unlock`.
- `git` is used for `bootstrap-ci`, local `origin` auto-detection, and selected Helm chart sources that resolve from Git tree URLs.

## Runtime Metadata

Primary catalog files (repo root):

- `component_sources.yaml` is the source catalog for reusable infra modules and Helm chart components.
- `component_cli_settings.yaml` is the cxcli settings catalog. It is linked to `component_sources.yaml` by the same `components.<infra|apps>.<component-id>` keys and owns cxcli behavior such as managed tool versions, observability endpoint templates, Grafana bindings, and MK8s policy.

The loader does not raw-merge the two YAML files. It loads the selected
`component_sources.yaml`, loads the sibling `component_cli_settings.yaml`, then
performs a typed join by `(scope, component-id)`: settings under
`components.infra.<id>.cli` attach to the matching Terraform component, and
settings under `components.apps.<id>.cli` attach to the matching Helm chart. A
settings entry that references an unknown component id fails validation instead
of being ignored.

The bundled catalogs may use YAML anchors and aliases as authoring shorthand for
repeated defaults or profile fragments; after loading, cxcli works with ordinary
resolved mappings and keeps the same strict catalog contract.

Schema:

- `component_sources.yaml`:
  - `shared.admin_ssh`: optional shared SSH defaults.
  - `components.infra.<component-id>`: `source.portable`, optional `source.local`, optional `ui`, optional `status`, optional `defaults`, optional `wizard_profile`, optional `wizard`, optional `input`.
  - `components.apps.<component-id>`: optional `source.portable`, optional `source.local`, optional `ui`, optional `release`, optional `defaults`, optional `wizard_profile`, optional `wizard`, optional `input`.
- `component_cli_settings.yaml`:
  - `cli.flux.version`: Flux controller install version used by local `deploy` when controllers are missing and by managed `flux bootstrap` CLI download.
  - `cli.flux.release_timeout`: global default Flux `HelmRelease.spec.timeout` for rendered app releases when a chart does not set `release.timeout`.
- `cli.terraform.version`: Terraform CLI version used by the managed Terraform download path. This selects the Terraform binary; provider source/version constraints stay in generated and module `required_providers` blocks.
  - `observability.endpoints.<read|write>.<endpoint-key>`: global Monitoring, Logging, and Tracing endpoint templates used by all resource types.
  - `components.infra.<component-id>.cli`: cxcli behavior for the matching infra component.
  - `components.apps.<component-id>.cli`: cxcli behavior for the matching app component.

## Catalog File Reference

`component_sources.yaml` and its sibling `component_cli_settings.yaml` use strict schemas. Unsupported keys are rejected at load time rather than silently ignored. The loader rejects cxcli settings such as top-level `cli`, top-level `observability`, or component-local `cli` inside `component_sources.yaml`; those fields belong in `component_cli_settings.yaml`.

Minimal `component_sources.yaml` structure:

```yaml
shared:
  admin_ssh:
    user_name: ubuntu

components:
  infra:
    mk8s:
      source:
        portable: git::https://github.com/org/repo.git//modules/mk8s?ref=v1.2.3
    vm:
      source:
        portable: git::https://github.com/org/repo.git//modules/vm?ref=v1.2.3

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
    nvidia-gpu-operator:
      source:
        portable:
          repo: oci://example.invalid/gpu-operator
          chart: gpu-operator
          version: 1.0.0
      release:
        namespace: nvidia-gpu-operator
        name: gpu-operator
```

Matching `component_cli_settings.yaml` structure:

```yaml
cli:
  terraform:
    version: 1.15.5
  flux:
    version: v2.8.0
    release_timeout: 5m

compute:
  boot_disk_defaults:
    disk_types:
      - value: NETWORK_SSD
        allocation_unit_gib: 1
        label: NETWORK_SSD ...
      - value: NETWORK_SSD_NON_REPLICATED
        allocation_unit_gib: 93
        label: NETWORK_SSD_NON_REPLICATED ...
      - value: NETWORK_SSD_IO_M3
        allocation_unit_gib: 93
        label: NETWORK_SSD_IO_M3 ...
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

components:
  infra:
    mk8s:
      cli:
        gpu:
          image_preferences:
            preferred_gpu_stack_presets: [cuda13.0, cuda12.8, cuda12.4, cuda12]
            preferred_os: [ubuntu24.04, ubuntu22.04]
          deployment_testing:
            operator_readiness:
              enabled_by_default: true
              timeout: 10m
            gpu_visibility:
              enabled_by_default: true
              namespace: gpu-validation
              image: nvcr.io/nvidia/k8s/cuda-sample:vectoradd-cuda12.5.0-ubuntu22.04
              timeout: 5m
              max_nodes: 3
  apps:
    nvidia-network-operator:
      cli:
        mk8s_gpu_policy:
          role: network_operator
          rules:
            - gpu_cluster_enabled: true
              auto_enable: true
    nvidia-gpu-operator:
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
  - `shared`: reusable shared values. Today the supported shape is `shared.admin_ssh.{user_name,public_key}`. `public_key` accepts either an inline `ssh-rsa`, `ssh-ed25519`, or ECDSA public key, or a readable local `.pub` file path such as `~/.ssh/my_ssh_key.pub`.
  - `compute.boot_disk_defaults`: shared cxcli policy for Compute-backed boot-disk recommendations. MK8s, VM, NFS, SSH jump host, and WireGuard VPN gateway components consume this single policy.
  - `components`: source registry split into `infra` and `apps`.
- `components.infra.<component-id>`:
  - `<component-id>` must use lowercase letters, digits, and hyphens.
  - `source.portable`: required portable Terraform module source.
  - `source.local`: optional workstation-local Terraform module path used by the `local` source profile.
  - `ui.title`, `ui.group`, `ui.enabled`: display metadata and default wizard checkbox state.
  - `status`: optional Nebius deployment-status watcher metadata. When present, `status.kind` is required, `status.parent_input` defaults to `parent_id`, `status.name_input` defaults to `name`, and `status.name_inputs` may list ordered name sources for components that can identify multiple resources. Compute instance watchers report public or private IP readiness, so private-only VMs do not look stuck just because they do not have a public IP.
  - `defaults`: target-path map for seeded or fallback values. Infra defaults must target `inputs.*`.
  - cxcli-owned component policy lives in sibling `component_cli_settings.yaml` at `components.infra.<component-id>.cli`. The bundled `mk8s` settings use `cli.gpu.*` for MK8s GPU image preferences and deploy-time validation defaults, and `cli.observability.*` for the MK8s observability contract (`primary_agent.{kind,chart_component_id,logs,metrics,traces,validation}`) that can auto-enable the collector chart, seed `deploy.targets[].observability.*` wizard defaults, and toggle the deploy-time Observability Agent guardrail. Infra settings can also declare `cli.observability.service_metrics.buckets` and `cli.observability.service_logs.buckets`; cxcli uses those records to decide which service-provider Prometheus federation buckets and Loki `__bucket__` values apply to enabled VM, Object Storage, PostgreSQL, MK8s, or storage components. For boot disks, cxcli resolves the selected preset's live resources (vCPU, RAM, GPU count), matches the first ordered `compute.boot_disk_defaults.<cpu|gpu>.rules` entry, and materializes explicit disk size/type inputs. If no rule matches an otherwise resolved shape, cxcli fails fast so maintainers update `component_cli_settings.yaml` instead of relying on a hidden fallback. Explicit first-class inputs, an existing VM boot disk, or MK8s `template.boot_disk` overrides remain authoritative. VM-style components also expose deletion protection for created boot disks, and expose the encryption prompt only when the selected disk type supports explicit Nebius managed encryption. The bundled `vm` settings use `cli.observability.*` for the built-in Monitoring-agent path (`primary_agent.{kind,logs,metrics}`); VM public-image choices come directly from the live Nebius image inventory for the selected platform and region.
  - `wizard_profile`: optional built-in shorthand that expands to a tested `wizard` mapping for that exact component id.
  - `wizard`: optional prompt metadata keyed by target field path such as `inputs.node_groups.system.platform`.
    - Use `options` for provider-backed choices such as Nebius subnet, platform, preset, image, version, or fabric lookups.
    - Use `sources` with `source: static` for fixed local choice lists. Static values may be plain strings or `{value, label}` mappings when the wizard should store one value but show a more descriptive label.
    - Set `prompt: false` on an optional field when it should stay available for manual `config.yaml` editing but should be suppressed from the interactive wizard.
    - Set `write_default_to_config: true` only when accepting the displayed default should persist that default into `config.yaml`; otherwise prompt defaults stay virtual.
  - Terraform module outputs are exported automatically under normalized output names such as `cluster_id` and can be consumed from other components through `input` bindings.
  - `input`: consumer-side binding map. Values must use `<component-id>.<output-alias>` or `<component-id>@<instance-id>.<output-alias>`.
- `components.apps.<component-id>`:
  - `source.portable`: optional portable Helm source mapping. Supports HTTP/S chart repos, `oci://` repos, and GitHub tree URLs.
  - `source.local`: optional developer-local chart mapping with `path`.
  - `source.portable.chart`: optional chart name. Defaults to the component id when omitted. When it differs, runtime validation, dependency lookup, and Flux rendering use the configured chart basename instead of the app id.
  - `source.portable.version`: optional chart version.
  - `ui.title`, `ui.group`, `ui.enabled`: display metadata and default wizard checkbox state.
  - `ui.selectable`: optional selector metadata. Omit it for normal persistent app chart sources. Set it to `false` only for cataloged chart sources that cxcli owns through another runtime flow.
  - `usage.lifecycle`: optional chart lifecycle metadata. Omit it for normal persistent app chart sources. `transient` marks a reusable Helm source for a cxcli-owned runtime flow; transient charts must also set `ui.enabled: false` and `ui.selectable: false`.
  - `usage.config.ref`: optional with `usage.lifecycle: transient`. Omit it for command-only runtime flows such as `acceptance-test benchmark`; use it only when a transient flow is activated by a persistent customer-facing config field.
  - `release.namespace`, `release.name`: default Helm namespace and release name used during `create` and `component add`.
  - `release.timeout`: optional Flux `HelmRelease.spec.timeout` duration such as `10m` or `12m30s`. When omitted, the chart inherits `cli.flux.release_timeout`.
  - `release.install_after`: app prerequisite list. cxcli auto-selects the
    listed app components when this app is selected and renders Flux
    `dependsOn` edges between the corresponding Helm releases.
  - `defaults`: unconditional target-path map for chart values. App defaults must target `values.*`.
  - cxcli-owned policy for that app lives in sibling `component_cli_settings.yaml` at `components.apps.<component-id>.cli`.
  - `components.apps.<component-id>.cli.mk8s_gpu_policy`: optional MK8s GPU automation contract for that app entry. `role` declares what operator role the chart plays, `install_after` adds Flux `dependsOn` ordering edges between app releases, `rules` is the conditional policy list, and optional `default_sets` / `post_render_patch_sets` let the settings catalog name reusable value overlays and post-render patch bundles once. Each `rules[]` item can set `auto_enable: true` to let cxcli auto-select the app for a matching MK8s GPU context and/or define conditional `defaults` / `post_render_patches` directly or reference shared sets with `defaults_from` / `post_render_patches_from`. Post-render patch text can use `{chart_version}` when an operand image tag must follow the app chart's `source.portable.version`. Top-level app `defaults` remain unconditional; the rule-level fields are the conditional version of the same mechanism.
  - `components.apps.<component-id>.cli.observability`: optional app-side observability metadata. In the bundled settings catalog this is used for app-specific metrics endpoint metadata such as the GPU Operator's DCGM Exporter service and for GPU node-label prerequisites, selectors, and stack-source guards required to make that endpoint live.
  - `components.apps.grafana.cli.admin`: optional Grafana-app admin Secret contract. It declares the runtime-only Kubernetes Secret name, admin username, and Secret data keys used by the Grafana Helm chart and generated deploy report.
  - `components.apps.grafana.cli.read_token`: optional Grafana-app Observability read-token Secret contract. It declares the runtime-only Kubernetes Secret name/key and the environment variable used by Grafana datasource provisioning.
  - `components.apps.grafana.cli.datasources`: optional Grafana-app datasource provisioning metadata in `component_cli_settings.yaml`. Each entry declares the Grafana display `name`, stable `uid`, datasource `type`, read endpoint key, optional `isDefault`, and optional report-facing `description`; cxcli materializes these entries into Grafana datasource values from the active Observability endpoint summary and uses Prometheus datasource descriptions in `deploy-report.md`. `validate-sources` fails if a datasource references a read endpoint key that is not declared under `observability.endpoints.read`.
  - `components.apps.grafana.cli.orgId`: optional Grafana organization ID used in generated dashboard and Explore links.
  - `components.apps.grafana.cli.logout-timeout`: optional Grafana idle session duration, defaulting to `20m`. It is rendered into `grafana.ini` under `[auth]` as `login_maximum_inactive_lifetime_duration`; use Grafana duration syntax such as `10m`, `1h`, or `7d`.
  - `components.apps.grafana.cli.explore_queries`: optional fallback Explore query templates keyed by `metrics`, `logs`, or `traces`; signal-bound Grafana status helpers can use them when a catalog-bound dashboard has not been imported yet.
  - `components.apps.grafana.defaults.values.dashboards.*`: Grafana dashboard sources. Each dashboard entry owns `datasource` plus either a Grafana.com `gnetId` with pinned `revision` and imported `uid`, or dashboard JSON with a top-level `uid`; bundled cxcli-owned dashboards use `json_file` package assets so `component_sources.yaml` and project `config.yaml` do not carry large inline JSON. Custom catalog dashboards can also use `json_file`; relative paths resolve from the active `component_sources.yaml` file and absolute paths are accepted. Built-in dashboard JSON stays under `src/nebius_cxcli/grafana_dashboards/` so it ships in the Python wheel; operator-owned dashboard JSON should live next to the custom catalog file or in another operator-owned directory. During `render`, cxcli writes those JSON assets under `generated/grafana_dashboards/<target-id>/<folder>/` and points the generated Grafana HelmRelease at a generated dashboard ConfigMap. A Grafana Helm chart provider key must use either chart-managed `values.dashboards` imports or `dashboardsConfigMaps`, not both; the bundled catalog keeps one Grafana.com service-dashboard example under `nebius`, cxcli-owned Kubernetes JSON dashboards under `nebius-kubernetes`, and cxcli-owned VM JSON dashboards under `nebius-vm`. The cxcli-owned Kubernetes GPU dashboard uses `Nebius Services` because DCGM metrics are exposed through the service-provider Monitoring read endpoint, but it filters by `mk8s_cluster_id` and uses `query_result(...)` variables so stale project-wide label metadata cannot populate the GPU-node selector. The cxcli-owned VM Metrics dashboard also uses `Nebius Services` and built-in Nebius VM Monitoring-agent labels such as `job="nebius-observability-agent"` and `instance_id`; VM Logs binds to `Nebius Logs`, defaults to the `sp_serial` bucket used for Compute VM serial/journald logs, and keeps `default` selectable for user-ingested logs. `render`, `deploy`, and `validate-dashboards` do not dynamically generate or rewrite dashboard JSON to fit live datasources; the explicit `grafana --export-dashboard --attach` and `grafana --dashboard-json --attach` workflows can rewrite dashboard datasource references before catalog attachment. `validate-sources` fails if any dashboard source lacks a resolvable locator or if its datasource is not declared under `components.apps.grafana.cli.datasources`.
  - `components.apps.grafana.cli.dashboard_signals`: optional Grafana-app signal bindings for `metrics`, `logs`, and `traces`. Each signal value is a single `<folder>/<dashboard>` reference to a declared chart `defaults.values.dashboards.*` entry. Signal bindings do not create dashboards; the deploy report lists cxcli-owned bundled dashboards directly instead of adding separate Metrics, Logs, and Traces shortcut rows.
  - `wizard`: optional prompt metadata keyed by chart value path such as `values.image.tag`.
  - `input`: same binding syntax as infra, but target paths should land under `values.*`.

Wizard shorthand and wiring:

- `wizard_profile` is the short form for built-in component-specific wizard wiring. It expands to a built-in `wizard` mapping at catalog-load time.
- `wizard` is the explicit escape hatch when you need full field-by-field control.
- If both are set on the same component, the `wizard_profile` fields load first and explicit `wizard` entries override or extend them.
- Built-in `wizard_profile` names are one-to-one with component ids. When set, the profile name must exactly match the component id.
- `wizard_profile` still does not create Terraform variables. It only predefines how the CLI should populate existing module fields.
- Use `wizard_profile` or `wizard` only when normal Terraform/Helm introspection is not enough or when you want guided choices instead of plain free-text entry.
- Fields that are just ordinary inputs with no guided choices do not need either `wizard_profile` or `wizard`.

Implementation note:

- Built-in component `wizard_profile` definitions are currently centralized in [src/nebius_cxcli/wizard_profiles.py](src/nebius_cxcli/wizard_profiles.py). They are not split into one Python file per component today.
- Provider-backed wizard options use one shared metadata resolver for
  interactive choices, strict live-value validation, and auto-selected
  defaults, so `options.from`, normalized `args`, `filter`, and planned VPC
  choices stay aligned across create and component-add flows.
- Bundled infra runtime validation selection is centralized in [src/nebius_cxcli/validation_profiles.py](src/nebius_cxcli/validation_profiles.py). It is code-owned internal metadata, not a supported public `component_sources.yaml` field.
- When adding a new Nebius Terraform module to `component_sources.yaml`, keep
  the catalog entry aligned with any required wizard, provider-option, status,
  validation, and handoff code changes in this package.

Built-in wizard profiles:

- `mk8s`: guided typed `inputs.cluster.*` prompts followed by a concrete node-group creation loop, live VPC network selection, network-filtered subnet lookup, MK8s platform/preset chaining, labeled GPU stack-source choices that distinguish Nebius images with preinstalled NVIDIA host components from GPU Operator-managed driver installs, live GPU driver-preset choices keyed by the selected GPU platform, OS, and Kubernetes version, optional GPU reservation selection from tenant Capacity Block Groups, live GPU capacity rows before preset selection, derived GPU-cluster fabric materialization when GPU clustering is enabled, and target-scoped native ESO MysteryBox sync prompts.
- `vpc`: optional existing-network lookup plus guided private-pool and subnet
  creation for the Terraform-owned VPC component. Live project-network choices
  recommend `default-network` when it exists, so pressing Enter keeps the
  wizard on the existing Nebius network; choosing `Create a new VPC network`
  asks for a network name and can attach a live unassigned existing private
  pool with at least one CIDR before falling back to
  `inputs.network.ipv4_private_cidrs` for creating a new private pool. Direct
  config can provide existing private pools with
  `inputs.network.ipv4_private_pool_ids`, or set
  `inputs.network.ipv4_private_source_pool_id` when a new managed pool must be
  carved from an existing source pool. Suggested
  custom private network CIDRs include non-default `10.x` `/13` ranges such as
  `10.8.0.0/13`, `10.16.0.0/13`, `10.32.0.0/13`, `10.40.0.0/13`, and
  `10.56.0.0/13`, plus `172.16.0.0/12` and `192.168.0.0/16`, outside
  Nebius' documented regional default private-pool ranges. Public pools follow
  the Nebius default-network pattern:
  direct config can set `inputs.network.ipv4_public_pool_ids`, but leaving it
  unset lets Nebius attach the default public pool to the new network. Declared
  subnets always use explicit private CIDRs: the guided wizard accepts one or
  more comma-separated explicit private CIDRs, stores them in the module's native
  list form, and records `use_network_private_pools=false`. Public pools are still
  inherited unless `use_network_public_pools` is set to `false`, so VM public
  allocations work by default. A VPC row can still create a network with no
  subnets by declining the subnet-add prompt or omitting `subnets`. Explicit
  subnet CIDRs
  must fit inside the selected network range, including default-network ranges
  already attached to the parent, without overlapping other subnets or live
  private allocations in that network.
  When parent ranges are known, the wizard suggests child CIDRs from the
  selected parent private pools while avoiding known explicit subnet CIDRs and
  live private allocations.
  For a new Terraform-owned network, if a custom subnet CIDR is
  outside the currently planned parent ranges, the wizard adds it to
  `inputs.network.ipv4_private_cidrs` first so Terraform extends the parent
  network IP space before creating the explicit subnet child range; the subnet
  prompt includes those new-parent-block suggestions when Terraform can manage
  the network. When an existing live network is selected, the wizard skips the
  new-network name prompt and network-CIDR prompts, then collects only planned
  subnets under that network and suggests child CIDRs from its attached
  private-pool ranges. Already attached RFC1918 extension blocks such as
  `172.16.0.0/12` and `192.168.0.0/16` stay visible as explicit subnet
  candidates when no explicit subnet CIDR or live private allocation overlaps
  them. If the operator selects or enters an out-of-parent custom subnet CIDR,
  cxcli adds that CIDR to an attached private pool on the selected live network
  first, then records the subnet with explicit private pools
  (`use_network_private_pools=false`).
  Terraform still treats `network.existing_id` as externally managed; the
  generated config does not claim ownership of the existing network.
- `managed-postgresql`: VPC network lookup plus static `tier` choices.
- `vm`: live VPC network selection, network-filtered subnet lookup, live compute platform/preset chaining, live Nebius public image-family choices keyed by the selected platform and region through the Nebius SDK `ImageServiceClient.list_public` API, static public-IP mode choices, optional InfiniBand fabric choices for GPU-cluster VM shapes, and a GPU-only preemptible path that materializes the required `recovery_policy=FAIL` when `preemptible_enabled=true`. The same shared GPU preset guidance applies here too: single-GPU shapes stay Ethernet-only/testing-oriented, while clusterable multi-GPU shapes are the InfiniBand path.
- `wireguard-gw`: live VPC network selection, network-filtered subnet lookup, live compute platform/preset, and public image-family chaining for the WireGuard VPN gateway module, which wraps the shared platform-infra `vm` module for VM resources and owns WireGuard cloud-init policy.
- `ssh-jumphost`: live VPC network selection, network-filtered subnet lookup, live compute platform/preset, and public image-family chaining for the SSH jump-host module, which wraps the shared platform-infra `vm` module for VM resources and owns SSH bastion cloud-init policy.
- `nfs`: live VPC network selection, network-filtered subnet lookup, live compute platform/preset, and public image-family chaining for the VM-based NFS module, which wraps the shared platform-infra `vm` module for VM resources and owns NFS cloud-init/export policy. The wizard asks the same guided boot-disk fields as other VM-style components and also asks the first-class secondary data-disk enabled/type/size fields.
- `object-storage`: static choices for `versioning_policy` and `object_audit_logging`.
- `soperator`: guided production mode, NodeSet profile, partition profile,
  topology profile, role-to-node-group mapping, and top-level optional
  child-chart/service gates. Existing-cluster onboarding is handled by
  `nebius-cxcli ext-soperator onboard <config.yaml-or-deployments-root>`.
- `mysterybox`: prompts the required Terraform-native `inputs.secrets` list as a guided loop for Secret names, ESO version policy, and payload keys/types, and suppresses the runtime-only `inputs.payload_values` helper from the interactive wizard.

Bundled infra component alignment:

- `mk8s` uses `wizard_profile: mk8s` because its typed cluster object, network-filtered subnet, Kubernetes version, concrete node-group loop, profile-only node-group helper defaults, GPU stack source, GPU driver preset, derived GPU-cluster fabric helper, GPU reservation choices, SFS attachment keys, SSH keys, service-account attachment, and optional native ESO MysteryBox sync deploy-target fields need guided choices instead of raw YAML object prompts.
- `vpc` uses `wizard_profile: vpc` because it owns the planned VPC resource
  branch: the wizard can either select an existing network for new subnets or
  collect a network name, network private CIDRs, and optional guided subnet
  entries for Terraform to create.
- `managed-postgresql` uses `wizard_profile: managed-postgresql` because `network_id` is Nebius-backed and `tier` is intentionally guided as a fixed choice.
- `vm` uses `wizard_profile: vm` because `network_id`, network-filtered `subnet_id`, `platform`, `preset`, `source_image_family`, `public_ip_mode`, guided secondary data-disk fields, optional GPU-cluster fabric choices, and preemptible GPU VM follow-up fields should come from guided catalog wiring instead of raw manual entry.
- The shared `compute_platform_presets` provider is what keeps generic Compute GPU shape choices aligned across VM-style wizards. When tenant/region context is available and the selected platform is GPU-backed, it queries the live Capacity Dashboard for the exact platform -> region -> preset shape, keeps matching platform rows separated even when preset names overlap, labels the returned VM slots with their GPU totals, and ranks or filters choices by the selected reservation policy. MK8s GPU preset prompts use the dedicated `mk8s_gpu_capacity_choices` row selector so operators choose the exact preset/fabric capacity row; cxcli then stores the preset plus a derived fabric only when the selected row is cluster-capable.
- `wireguard-gw` and `ssh-jumphost` use their matching `wizard_profile` names because `network_id`, network-filtered `subnet_id`, `platform`, `preset`, and `source_image_family` should come from live project discovery instead of module-local hardcoded defaults. The WireGuard wizard materializes `inputs.wireguard_tunnel_cidr` into `config.yaml` because it defines the server tunnel address and client allocation pool. It keeps advanced `endpoint_host`, `clients`, and `labels` off the prompt path: endpoint detection is automatic by default, day-2 clients are generated with the `wireguard` command, and the module applies useful `component`/`name` labels automatically. Both public VM profiles use the same public-IP allocation contract: either create a new allocation, or disable creation and provide `inputs.public_ip_allocation_id`; explicit `inputs.public_ip_allocation_name` values must use lowercase letters, digits, and hyphens so cxcli can reject invalid names before Terraform. The SSH jump-host wizard treats `inputs.allowed_cidrs` as the first-boot seed for SSH reachability, defaults it from the detected operator public IPv4 address when available, and expects later source-IP allowlist updates to use the `ssh-jumphost` command against the deployed VM-local helper.
- `object-storage` uses `wizard_profile: object-storage` because `versioning_policy` and `object_audit_logging` are intentionally guided as fixed choices.
- `nfs` uses `wizard_profile: nfs` because it is a VM-module-backed storage
  component: `network_id`, network-filtered `subnet_id`, `platform`, `preset`,
  and `source_image_family`
  should come from live project discovery, while `data_disk_enabled`,
  `data_disk_type`, and `data_disk_size_gib` are first-class secondary-disk
  choices rather than a raw nested object prompt.
- `sfs` uses `wizard_profile: sfs` because its name, size, filesystem type,
  block size, mount tag, and deletion-protection (`forbid_deletion`) defaults
  should be visible and editable instead of blank optional prompts. When
  Soperator materializes the multi-filesystem SFS map, the generated `name` and
  `mount_tag` defaults use the target cluster name plus role, such as
  `<cluster-name>-accounting`.
- `soperator` uses `wizard_profile: soperator` because its app wizard is a
  concise policy surface over `install_mode`, NodeSet profile, partition
  profile, topology profile, placements, and optional child-chart gates rather
  than a raw walk through parent chart values.
- `mysterybox` uses `wizard_profile: mysterybox` to prompt the required `inputs.secrets` list and hide the runtime-only `inputs.payload_values` helper from prompts. The wizard requires the first MysteryBox Secret name, asks for the target Kubernetes Secret name with a Kubernetes-safe default derived from the MysteryBox name such as `db-credentials` for `db_credentials`, asks for the ESO version policy with `auto-primary-version-pinning` as the default, requires at least one payload key per Secret, stores entered keys such as `username` as uppercase keys such as `USERNAME`, and then uses blank prompts to finish the current Secret or the whole loop. Inside that guided Secret/policy/key loop, `q` backs up to the previous nested question before leaving the whole `inputs.secrets` field. It does not ask for payload values during `create`.
- `mysterybox` keeps the Terraform-native MysteryBox product shape in `inputs.secrets`: a list of secret objects where each secret `name` is the stable identity. Every secret declares one `payload` map with one or more `text` or `file` payload entries, an optional `version_id` for the current primary MysteryBox version, and optional cxcli-only `kubernetes_secret_name` / `eso_version_policy` metadata for ESO target naming and version selection. Use `version_id: n/a` before the first deploy; after Terraform creates the initial primary version, cxcli writes the created `mbsecver-...` back to `config.yaml` and the generated bundle. Later rotations happen in Nebius MysteryBox, and operators update `version_id` to the new primary version ID when they want Terraform metadata and manual ESO pinning to follow that version. The CLI validates that list directly and rejects the old mapping/singular `version`/multi-version shapes instead of translating them.
- App components can use `wizard_profile` for component-owned prompt policies
  such as Soperator. Other app charts generally rely on Helm metadata plus
  optional explicit `wizard` entries when a chart value needs guided choices.

WireGuard client configs:

- After a `wireguard-gw` component has been rendered and deployed, use
  `nebius-cxcli wireguard --gen-client-conf <config.yaml>` to create a new
  client config. The command resolves sibling `generated/`, reads Terraform
  output to find the VPN gateway public IP, SSHes to the gateway VM, asks the
  gateway-local `nebius-wireguard-client` command to allocate the next free
  tunnel `/32`, and downloads the generated `.conf`. It prints the exact
  `wg-quick up <client.conf>` and `wg-quick down <client.conf>` commands to run
  from the local machine.
- `generated/reports/deploy-report.md` includes a WireGuard handoff section
  when `wireguard-gw` is enabled. It records the deployed endpoint when
  Terraform outputs are available, `wireguard_tunnel_cidr`, configured
  `local_subnets`, default client DNS, the client-generation command, and
  exact `wg-quick up/down` commands for any existing local files under
  `wireguard-clients/`.
- The current `config.yaml` must still enable the same selected
  `wireguard-gw` instance as the sibling rendered/deployed `generated/`
  bundle. After component add/remove/rename, run `render` and `deploy` before
  WireGuard day-2 operations.
- By default, downloaded configs are written under
  `<tenant-folder>/<project-folder>/wireguard-clients/`. cxcli adds that
  directory to the deployments-root `.gitignore` because each `.conf` contains
  private key material.
- Generate and distribute one `.conf` per device or user. Reusing the same
  config on multiple simultaneous clients causes key and tunnel-address
  conflicts.
- The WireGuard VPN gateway VM keeps SSH as an admin channel only: key-only login, root
  login disabled, SSH forwarding disabled, fail2ban/auditd enabled, and UFW
  open only for SSH plus the configured WireGuard UDP listener. Use
  `ssh-jumphost` when you need SSH ProxyJump forwarding to private VMs.
- For `--gen-client-conf`, `--local-subnet <cidr>` overrides the private
  destination CIDRs routed by the generated client. If omitted, the gateway
  uses the module's `inputs.local_subnets` plus any day-2 VM-local subnet
  updates.
- `create` and `component add` write `inputs.wireguard_tunnel_cidr` to
  `config.yaml`. The default is `10.8.0.1/22`, which gives about 1,000 client
  `/32` allocations after reserving the network, broadcast, and server
  addresses. Use non-overlapping private address space for this tunnel; do not
  use APIPA/link-local space for this routed VPN path. Change it before first
  deploy when possible; after clients exist, changing the tunnel CIDR requires
  rerendering/redeploying the gateway and generating new client configs.
- New client configs default to DNS servers `1.1.1.1` and `1.0.0.1` through
  the module's `inputs.client_default_dns`; override that list when clients
  should use private DNS.
- Add or remove default private destination CIDRs for future generated clients:

  ```bash
  nebius-cxcli wireguard --add-local-subnets <config.yaml> \
    --local-subnet 10.20.0.0/16,10.30.0.0/16
  nebius-cxcli wireguard --remove-local-subnets <config.yaml> \
    --local-subnet 10.20.0.0/16,10.30.0.0/16
  ```

  Existing downloaded or imported client configs are not rewritten
  automatically; generate and redistribute new configs when clients need the
  updated routes.
- All WireGuard modes accept `--component`, `--ssh-user`, `--ssh-private-key`,
  and `--auto-auth-bootstrap/--no-auto-auth-bootstrap`.
- Client-generation-only flags are `--client-name`, `--dns`,
  `--persistent-keepalive`, `--output-dir`, and `--force`.
- `--client-name` must be a wg-quick-safe interface name: lowercase letters,
  digits, and hyphens, up to 15 characters. When omitted, cxcli generates a
  short unique `wg-...` name so the downloaded `<client-name>.conf` works with
  `wg-quick`.
- `--local-subnet` is mode-specific: repeat it for `--gen-client-conf`, but
  pass exactly one comma-separated value for add/remove subnet mode.
- On macOS, install the WireGuard CLI tools with
  `brew install wireguard-tools`, then connect from Terminal with
  `wg-quick up ./<client-name>.conf` and disconnect with
  `wg-quick down ./<client-name>.conf`. macOS may ask for an admin password
  when changing the tunnel state. `nebius-cxcli wireguard --gen-client-conf`
  checks whether `wg-quick` is installed locally and prints the OS-specific
  install command when it is missing. There is no HTTP URL on the VPN gateway.

SSH jump-host source CIDRs:

- `inputs.allowed_cidrs` is the first-boot SSH reachability seed for the
  `ssh-jumphost` module. It must contain at least one IPv4 source CIDR so the
  VM does not boot with SSH open to the internet or with no allowed operator
  path.
- In the interactive wizard, cxcli tries to detect the operator laptop public
  IPv4 address and offers it as a `/32` default for `inputs.allowed_cidrs`.
  If that lookup is unavailable, enter the source public CIDR manually.
- After an `ssh-jumphost` component has been rendered and deployed, use
  `nebius-cxcli ssh-jumphost` for day-2 source-IP allowlist changes. The
  command resolves sibling `generated/`, reads Terraform output for the
  jump-host public IP, SSHes to the VM, and runs the VM-local
  `nebius-ssh-jumphost` helper.
- The current `config.yaml` must still enable the same selected `ssh-jumphost`
  instance as the sibling rendered/deployed `generated/` bundle. After
  component add/remove/rename, run `render` and `deploy` before SSH jump-host
  day-2 operations.
- Add, remove, or list the active VM-local allowlist:

  ```bash
  nebius-cxcli ssh-jumphost --add-allowed-cidrs <config.yaml> \
    --allowed-cidr 203.0.113.10/32,198.51.100.0/24
  nebius-cxcli ssh-jumphost --remove-allowed-cidrs <config.yaml> \
    --allowed-cidr 198.51.100.0/24
  nebius-cxcli ssh-jumphost --list-allowed-cidrs <config.yaml>
  ```

- Add/remove modes require exactly one comma-separated `--allowed-cidr` value.
  The VM-local helper canonicalizes and deduplicates IPv4 CIDRs, persists the
  runtime allowlist under `/var/lib/nebius-ssh-jumphost/`, reapplies the
  module-owned UFW policy, and refuses to remove the last remaining source CIDR
  to avoid SSH lockout.
- When a deployed project enables both `ssh-jumphost` and one or more private
  `vm` components, `deploy` prints a ProxyJump helper after the report path and
  writes the same concrete command into `generated/reports/deploy-report.md`
  once Terraform outputs expose the jump-host public IP and VM private IP:

  ```bash
  ssh -J <ssh-jumphost-user>@<ssh-jumphost-public-ip> <vm-user>@<vm-private-ip>
  ```

  If the matching private key is not available through `ssh-agent`, add
  `-i /path/to/private_key` to the command. The jump host allowlist must include
  your current source public CIDR before the first SSH hop is accepted.
- These day-2 operations do not edit `config.yaml`. If you need the source
  config to describe a new desired first-boot seed for a replacement jump host,
  update `inputs.allowed_cidrs`, rerender, and review the Terraform plan.

Bundled MysteryBox to Kubernetes sync:

- cxcli uses External Secrets Operator's native `nebiusmysterybox` provider. The custom `mysterybox-bridge` webhook/chart is removed.
- When the Terraform `mysterybox` component and an MK8s target are both enabled, cxcli enables the bundled `external-secrets` app for that target by default so the ESO controller is present. `create` and `component add` include that auto-selection in the resolved component summary and same field-wizard pass, matching other dependency-driven app rows. Native MysteryBox-to-Kubernetes sync also defaults to enabled in that selected-backend wizard context under the target-scoped `deploy.targets[].secrets.mysterybox.*` contract. Operators always provide `sync_namespaces`, defaulting to `default`, and cxcli derives one `ExternalSecret` per declared MysteryBox Secret in each listed namespace. Each declared MysteryBox payload key becomes one `spec.data[].remoteRef.property` mapping in the generated Kubernetes Secret. Operators can answer `false` to leave the ESO controller installed without configuring sync.
- For generated Terraform roots, cxcli treats the MysteryBox module's `payload_values` input as runtime-only: it renders a sensitive root variable such as `mysterybox_payload_values` with default `{}`, passes it to the child module, and omits it from `terraform.auto.tfvars.json` and the generated manifest. Provide payloads at first deploy/plan/apply time with `TF_VAR_mysterybox_payload_values` as a JSON/YAML two-level map keyed by secret name and payload key; for non-default MysteryBox instance ids, use the rendered variable name such as `TF_VAR_secretstore_alpha_payload_values`. Interactive local `deploy`, `terraform plan`, and `terraform apply` runs prompt with hidden input for missing first-deploy values before Terraform starts. Non-interactive runs fail fast with the exact missing entries and env-var example. After cxcli records the created `version_id` in `config.yaml`, the generated manifest, and generated Terraform tfvars, later plan/apply/destroy runs do not need the original payload values. If Terraform exits after Nebius has created the Secret versions but the provider lost the operation poll, cxcli recovers those IDs from Terraform state so a retried deploy can continue from the refreshed bundle; `inputs.payload_values` in `config.yaml` is rejected so cleartext payloads do not enter source or generated artifacts.
- When `deploy.targets[].secrets.mysterybox.enabled: true`, cxcli renders managed `ClusterSecretStore`, generated key-mapped `ExternalSecret`, and non-built-in `Namespace` objects into a generated post-Flux manifest next to the target's Flux files. The external-secrets HelmRelease installs only the ESO controller and CRDs; local `deploy`/`flux apply` apply the post-Flux manifest after that HelmRelease is Ready so Helm does not render ESO CRs before their CRDs exist. Generated ExternalSecrets are derived from `sync_namespaces` and enabled `mysterybox.inputs.secrets`; `deploy` resolves the Terraform-created `mbsec-...` IDs from Terraform output after apply and refreshes the Flux manifests before applying ESO resources. A render before those Terraform outputs exist can only write namespaces and the shared `ClusterSecretStore`; the generated `ExternalSecret` objects, including their `refreshInterval`, appear after deploy refreshes the post-Flux manifest with real MysteryBox IDs. The Soperator notifier's `webhookSource: mysterybox` path is the narrow exception: because the operator provides an existing non-secret `mbsec-...` ID, that notifier `ExternalSecret` can render immediately while still omitting the webhook URL. If a declared Secret has `kubernetes_secret_name`, cxcli uses it for the generated `ExternalSecret` and `spec.target.name`; otherwise it defaults from a Kubernetes-safe form of the MysteryBox Secret name. The default `eso_version_policy` is `auto-primary-version-pinning`, which intentionally omits `remoteRef.version` so ESO asks MysteryBox for the current primary version on every refresh. Set `eso_version_policy: manual-version-pinning` to render `remoteRef.version` from a real `version_id: mbsecver-...`; before the first deploy this value is not available, and deploy fills it from Terraform output before refreshing ESO manifests. Generated ExternalSecrets use `refreshPolicy: Periodic` and default `refreshInterval: 15m`; set `deploy.targets[].secrets.mysterybox.refresh_interval` to another `s`, `m`, or `h` duration such as `30s`, `1m`, `15m`, or `1h`.
- Those cxcli-managed ESO objects are generated output, not source-config content. `config.yaml` keeps only the target sync contract under `deploy.targets[].secrets.mysterybox.*`; normalization strips stale cxcli-managed MysteryBox ESO `extraObjects` from the external-secrets app row while preserving operator-authored chart objects.
- cxcli renders `ClusterSecretStore.spec.provider.nebiusmysterybox.apiDomain` as `api.nebius.cloud:443` by default and intentionally does not render `caProvider` for this public endpoint. ESO uses the controller image's normal public CA trust bundle to validate Nebius-owned TLS for `api.nebius.cloud`; cert-manager and trust-manager are not part of this default public trust path. Use a custom CA only for an internal endpoint, TLS-inspecting proxy, self-signed endpoint, or custom domain that is not chained to a public CA.
- The generated bundle never contains the Nebius service-account credential Secret. During `deploy`, `flux bootstrap`, and `flux apply`, cxcli treats the configured Kubernetes Subject Credentials Secret as the ESO auth source of truth. If that Secret is missing, invalid, or stale, cxcli ensures the dedicated Nebius service account `mysterybox-sa`, grants only `mysterybox.payload-viewer`, creates an authorized key through the Nebius API, and writes the private key only into the runtime Secret. ESO exchanges those credentials for Nebius IAM access tokens when it calls MysteryBox. The IAM-management step deliberately ignores Terraform runtime service-account env vars such as `NEBIUS_SA_ID` so target-scoped `flux apply` does not try to use the Terraform automation identity to manage IAM; local federation profiles can still be used through the Nebius CLI access-token fallback. The default Secret is `external-secrets/nebius-mysterybox-shared-creds` with key `credentials.json`.
- The operator identity running that command must be allowed to manage Nebius service accounts, IAM groups, and access permits in the target project. The created `mysterybox-sa` itself receives only the MysteryBox payload-read role, not `editor` or `admin`.
- Rendered ESO MysteryBox remote references are ID-oriented because the native provider reads MysteryBox keys such as `mbsec-...`. Source config no longer carries raw ExternalSecret specs; it declares MysteryBox Secrets once under `mysterybox.inputs.secrets`, and cxcli renders `spec.data[].remoteRef` entries for each declared payload key in each configured namespace.
- The generated sync path resolves each declared Secret name through Terraform `secret_ids` output to a Terraform-created `mbsec-...` ID. Source config does not expose raw ExternalSecret fields such as `secret_name` or `mysterybox_instance_id`; multiple MysteryBox component instances are resolved from the enabled `mysterybox` component rows. General-purpose externally managed MysteryBox Secrets sync remains out of scope for this simplified generated sync model; the Soperator Slack notifier has a narrow exception through `values.soperator-notifier.slack.webhookSource: mysterybox`, where the non-secret `mbsec-...` ID is consumed only to sync the notifier webhook Secret.
- By default, `allow_all_namespaces: true` exposes the shared store to every namespace and cxcli omits `ClusterSecretStore.spec.conditions`. To restrict access, set `allow_all_namespaces: false`; cxcli then renders `ClusterSecretStore.spec.conditions.namespaces` from the same `sync_namespaces` list that receives generated ExternalSecrets. In both modes, cxcli renders Namespace objects only for sync namespaces that are not built-in Kubernetes namespaces such as `default`; the `ExternalSecret` itself can still target `default`. The shared store namespace boundary does not by itself prevent an allowed namespace from referencing another MysteryBox secret ID readable by `mysterybox-sa`, so keep namespace RBAC and Nebius `mysterybox.payload-viewer` grants aligned with the real access boundary.
- After promoting a new MysteryBox version to primary, the default auto-primary sync path updates on the next ESO periodic refresh. To reconcile immediately instead of waiting up to the configured interval, annotate the generated ExternalSecret:

  ```bash
  kubectl -n ns-1 annotate externalsecret app-config \
    force-sync="$(date +%s)" \
    --overwrite
  ```

- During `deploy`, `flux bootstrap`, and `flux apply`, cxcli validates the actual in-cluster DNS, egress, and TLS path that ESO will use by running a temporary curl pod in the credentials Secret namespace against the configured `api_domain`. For configured native sync targets, `deploy` also runs a required ESO MysteryBox connectivity validation after the ESO controller, store, and ExternalSecrets are applied, checks `ClusterSecretStore Ready=True`, checks every configured `ExternalSecret Ready=True`, scans ESO controller logs since the current validation started for Nebius/MysteryBox TLS/auth/permission errors, and records the result in `generated/reports/deploy-report.md`. This validation is selected by design and is not disabled by the optional deploy validation skip flags. To run the same TLS check manually:

  ```bash
  kubectl -n external-secrets run nebius-tls-check \
    --rm -it \
    --restart=Never \
    --image=curlimages/curl:8.7.1 \
    --command -- sh -c \
      'api_domain=api.nebius.cloud:443
       host="${api_domain%%:*}"
       out="$(curl -vvI --connect-timeout 10 --max-time 30 "https://${api_domain}" 2>&1)"
       printf "%s\n" "$out" | grep -E "SSL certificate verify ok|issuer:|subjectAltName"
       printf "%s\n" "$out" | grep -q "SSL certificate verify ok"
       printf "%s\n" "$out" | grep -q "subjectAltName: host \"${host}\" matched"
       printf "%s\n" "$out" | grep -Eq "HTTP/[0-9.]+ [0-9]"'
  ```

  A public-CA chain, hostname match for `api.nebius.cloud`, and any HTTP response prove the cluster can resolve, connect, and verify TLS. The check validates that an HTTP response was received but does not print the status code, because a root-endpoint `404` is expected and should not be mistaken for a TLS failure. A certificate error points to the pod image trust bundle or TLS interception; timeout, DNS, or connection failures point to egress, DNS, firewall, proxy, or NetworkPolicy. After applying the store and at least one `ExternalSecret`, check `kubectl get clustersecretstore nebius-mysterybox-shared -o yaml`, `kubectl -n <namespace> describe externalsecret <name>`, and `kubectl -n external-secrets logs deploy/external-secrets` for Nebius, MysteryBox, TLS, certificate, unauthorized, or permission errors. The desired ESO condition is `Ready=True`.

Bundled observability architecture:

- Observability is now a first-class deploy contract and stays disabled by default. MK8s Kubernetes observability is target-scoped under `deploy.targets[].observability.*`, with each row bound by the target cluster `instance_id`; VM observability remains under `deploy.observability.vm.*` because it is not a Kubernetes target install. MK8s switches control whether cxcli deploys/configures in-cluster collectors; VM switches control only cxcli-managed VM labels for Nebius built-in journald collection. They do not create the Nebius Monitoring/Logging/Tracing service endpoints themselves.
- `create` and config normalization keep that customer contract scoped to the enabled infra set. MK8s-only projects get `deploy.targets[].observability.kubernetes.*`, VM-only projects get `deploy.observability.vm.logs.*`, mixed projects get both, and unrelated project-scope defaults such as MK8s GPU deploy validations are omitted when no MK8s component is selected.
- When `deploy.targets[].observability.enabled=true` for an MK8s target, cxcli auto-enables the bundled `nebius-observability-agent`, `gateway-helm`, and `grafana` Helm releases for that target. The agent source is pinned to `oci://cr.nebius.cloud/observability/public/nebius-observability-agent-helm`, matching the current Nebius Observability Agent for Kubernetes docs. The agent values are materialized from `deploy.targets[].observability.kubernetes.{logs,metrics,traces}.*`; Grafana is materialized with Prometheus, Loki, and Tempo datasources that point at the project public read endpoints. The Grafana service stays `ClusterIP`; external access is through Envoy Gateway/Gateway API, which creates the Nebius load balancer instead of relying on NGINX Ingress. The bundled EnvoyProxy config sets the generated LoadBalancer service to `externalTrafficPolicy: Cluster`, matching Nebius load-balancer support. Grafana, Envoy Gateway control-plane pods, and the managed Envoy proxy use hard node affinity that excludes nodes labeled `nebius.com/gpu=true` so they stay on CPU nodes when the cluster has GPU workers. In the interactive wizard, the auto-selection notice appears while answering the target observability prompts; later app field prompts only ask whether to customize chart values, and answering `n` keeps the auto-selected apps with defaults.
- When the operator explicitly selects observability apps such as `nebius-observability-agent` or `grafana` before the MK8s target prompt, the wizard treats that as target observability intent and defaults `deploy.targets[].observability.enabled` to `true` for the matching target. Selecting `gateway-helm` alone does not imply observability; it can be used by other Gateway API flows.
- The `nebius-observability-agent` app entry is the Nebius Observability Agent for Kubernetes Helm chart. Do not select it for standalone VMs. VM observability uses `deploy.observability.vm.*`: the Nebius Compute Monitoring agent that is installed automatically on new VMs plus supported Compute labels for journald log collection.
- In `component_sources.yaml`, that CPU-only scheduling policy is defined once as the YAML anchor `&nebius_cpu_only_node_affinity` under Grafana `values.affinity` and reused with `*nebius_cpu_only_node_affinity` for other non-GPU charts. The anchor is only catalog syntax to avoid duplicated affinity blocks; generated HelmRelease values contain normal Kubernetes affinity objects. Because this is hard affinity, GPU-only clusters need an operator override or CPU node capacity for these platform pods.
- The bundled `mk8s` wizard exposes that same contract directly: `deploy.targets[].observability.enabled`, the main logs/metrics/traces toggles, and the `collect_k8s_cluster_metrics` switch are guided prompts; advanced namespace exclusions and agent self-scrape/self-log toggles stay available in `config.yaml` but are hidden from the interactive wizard by default. The catalog default excludes ordinary `kube-system` service/pod annotation scrapes for both logs and metrics while leaving chart-owned infrastructure targets under the agent chart's control. Because the cxcli-managed deploy contract is default-off, cxcli treats cluster-level K8s metrics as part of the enabled baseline once the operator turns this path on. This MK8s contract is separate from the preinstalled Nebius Monitoring agent that already runs on the cluster's node VMs and sends VM/node system metrics through Nebius-managed ingestion to Monitoring dashboards and read APIs.
- The built-in VM Monitoring agent path is always present for Nebius Compute VMs. `deploy.observability.enabled` does not install or remove that built-in agent and does not change how VM service metrics are ingested. The platform-native branch remains `deploy.observability.vm.logs.enabled` plus optional `deploy.observability.vm.logs.systemd_units`, which is the wizard's "collect journald logs?" switch. Answering yes makes cxcli turn on the supported Compute journald collection labels for systemd services. The bundled VM catalog keeps that logs branch enabled by default, but cxcli only materializes the VM journald labels when the parent `deploy.observability.enabled` switch is true.
- VM observability field guide:
  - `deploy.observability.vm.logs.enabled`: applies Nebius Compute journald labels when the parent `deploy.observability.enabled` switch is true.
  - `deploy.observability.vm.logs.systemd_units`: optional systemd unit allowlist rendered as a semicolon-separated Compute label value.
- On the VM path, cxcli materializes `nebius.o11y.systemd-logs-collection.enabled=true` and, when units are specified, `nebius.o11y.systemd-logs-collection.units=<unit1;unit2>` into `infra.components[id=vm].inputs.labels`. If `systemd_units` is empty or omitted, cxcli writes only the enabled label so Nebius collects all supported systemd units. Existing VMs may need a Nebius stop/start cycle before changed labels take effect, so cxcli treats that as the supported day-2 activation boundary instead of trying to manage the built-in packages itself. Compute VM serial/journald logs are exposed through the project Loki read endpoint in the `sp_serial` bucket; the bundled VM Logs dashboard defaults to `sp_serial` and also lets operators switch to `default` for user-ingested logs.
- Nebius docs say omitting `deploy.observability.vm.logs.systemd_units` should collect all systemd units. cxcli keeps that documented default, but for deterministic smoke tests and narrower blast radius it is better to set explicit units such as `["sshd.service"]` or your own service unit names.
- The write path is intentionally split by resource type. For MK8s, the Nebius observability agent collects pod logs, Prometheus-style metrics, and OTLP/gRPC traces from the cluster and forwards them to public regional Nebius write endpoints. The bundled in-cluster OTLP service is for traces and OTLP-forwarded app telemetry; Prometheus-style metrics still flow through the agent's scrape pipeline rather than an in-cluster OTLP metrics receiver. For built-in Nebius VM monitoring and journald collection, cxcli does not render public write endpoints or service accounts because ingestion stays Nebius-owned.
- Nebius-managed service telemetry is catalog-owned separately from cxcli-managed collectors. The bundled catalog declares service-provider metric buckets for Compute/VMs, Compute volumes, GPU-backed MK8s nodes, Object Storage, shared storage backing resources, and Managed PostgreSQL; it declares service log buckets for Compute VM serial logs, Managed Kubernetes control-plane/audit logs, and Managed PostgreSQL logs. Object Storage request logs are Audit Logs, so the Object Storage component declares service metrics but does not declare a Loki log bucket.
- Custom MK8s scrape targets stay chart-native and YAML-configurable. Operators can put extra Prometheus scrape configs under the `nebius-observability-agent` chart row at `values.config.metrics.additionalTargets`; cxcli preserves those entries and removes only stale catalog-owned jobs. For Nebius Observability Agent scrape config, source `config.yaml` stores the observability intent under `deploy.targets[].observability.*`; when `collect_k8s_cluster_metrics=true`, render emits cxcli's safe kubelet, cAdvisor, API server, and Hubble scrape jobs into the generated HelmRelease `additionalTargets` and disables the chart's broad built-in cluster-metrics jobs so NFD or other high-volume node labels are not copied into every container metric. The bundled DCGM exporter target uses the Nebius agent's Prometheus annotation discovery instead of a duplicate `additionalTargets` scrape job.
- The collector-side auth path stays public-safe: the bundled MK8s chart uses `auth_scheme: iam-token-file`, reads `/mnt/cloud-metadata/tsa-token` from the node metadata mount, and talks to `tokens.iam.api.nebius.cloud:443`. Direct external collectors use `Authorization: Bearer <observability static token or IAM token>` with ingest permissions; cxcli does not ask users to paste write-side tokens or static keys into repo config.
- GPU monitoring stays aligned with NVIDIA's current split: For GPU-enabled MK8s, DCGM Exporter must stay enabled in GPU Operator. Omitting `nvidia-gpu-operator.values.dcgmExporter.enabled` is valid because the bundled GPU Operator chart defaults it to enabled; explicitly setting it to `false` is rejected. Scraping and pushing those metrics to Nebius Monitoring happens only when the MK8s observability metrics path is enabled. cxcli does not invent a `ServiceMonitor` dependency or a duplicate DCGM scrape job. Instead, the bundled catalog declares the `nvidia-dcgm-exporter` service as an app metric target with `discovery.kind: prometheus_annotations`, matching the Nebius agent's documented service endpoint discovery for `prometheus.io/scrape=true`. On Nebius driverful GPU images (`gpu_stack_source: nebius_image`), the bundled GPU Operator values pin the NFD worker to Nebius GPU nodes only when Network Operator is not part of the target. That lets NFD own standard NVIDIA discovery labels such as `nvidia.com/gpu.present=true` on non-GPU-cluster targets while GPU-cluster / InfiniBand targets keep Network Operator as the single NFD owner. Those nodes can still default to `nvidia.com/gpu.deploy.operands=false`, which leaves GPU Operator operand DaemonSets, including `nvidia-dcgm-exporter`, at desired count `0`. When `deploy.targets[].observability.enabled=true`, Kubernetes metrics are enabled, and the GPU Operator app is selected on the `nebius_image` stack, cxcli materializes the catalog-owned GPU node labels required to run only DCGM Exporter plus the GPU Operator validator while keeping GPU Operator device-plugin/GFD disabled so they do not duplicate the Nebius-managed path. `deploy` also reconciles those operand labels onto existing live GPU nodes using the catalog-owned selector, because MK8s node-group label updates apply cleanly to future nodes but may not relabel already-running Node objects. The deploy report still tells operators to verify live `nvidia-dcgm-exporter` endpoints after deploy, because the final signal is runtime readiness, not config presence alone.
- MK8s observability also has a generated deploy-time guardrail. When a target requires the Nebius Observability Agent and the settings catalog leaves `components.infra.mk8s.cli.observability.primary_agent.validation` enabled, `render` writes a target-scoped `mk8s_observability_ingestion` validation into `generated/nebius-cxcli-manifest.json`; `deploy` runs it after Flux convergence using the same handed-off kubeconfig. The check verifies the agent HelmRelease is Ready, rendered signal values match the enabled logs/metrics/traces contract, the agent DaemonSet is Ready, and the OTLP/gRPC service has a ready EndpointSlice when traces are enabled. The settings catalog exposes only the boolean `validation` switch, defaulting to enabled; cxcli keeps the Nebius-agent object names, value paths, selectors, and bounded check limits internal. The pass path uses named or limited Kubernetes API reads instead of listing every agent pod or endpoint, so the guardrail stays fast on large clusters. Results are written to `generated/reports/observability-ingestion-report-<target>.json` and summarized in `deploy-report.md`. This is the default-enabled in-cluster health guardrail; `validate-dashboards` remains the read-side dashboard/datasource/read-endpoint fit check.
- Read-side Grafana wiring is automated but still kept out of `config.yaml` secrets. The bundled `grafana` app uses the maintained Grafana community Helm repository and leaves Grafana image registry/repository/tag on the chart defaults so the chart version and chart `appVersion` stay the single source of truth. It keeps one Nebius service dashboard import as an example and references cxcli-owned Kubernetes and VM dashboard JSON assets from `component_sources.yaml`; operators can change the bundled dashboard import or dashboard-file references there when they need a different dashboard set. Source `config.yaml` does not embed cxcli-owned dashboard JSON. During `render`, cxcli writes those JSON files under `generated/grafana_dashboards/<target-id>/<folder>/`, renders a Grafana dashboard ConfigMap into the generated Flux target, and points the generated HelmRelease at that ConfigMap with `dashboardsConfigMaps`. The bundled catalog keeps the Grafana.com service-dashboard example under the `nebius` provider, cxcli-owned Kubernetes JSON dashboards under the `nebius-kubernetes` provider, and cxcli-owned VM JSON dashboards under the `nebius-vm` provider because the Grafana Helm chart does not support mixing chart-managed imports and external ConfigMaps on the same provider key. During `deploy`, cxcli creates or reuses Kubernetes Secrets for the Grafana admin password and the Observability read token, ensures a project service account with `viewer`, issues an Observability static key when the token Secret is missing, and mounts that token into Grafana datasource provisioning. If the token Secret already exists but a catalog-bound Prometheus read endpoint clearly rejects the token with `401` or `403`, cxcli replaces the Secret with a fresh Observability static key. The Grafana admin username, admin Secret name/keys, read-token Secret name/key/env var, datasource names, UIDs, types, default marker, read endpoint bindings, deploy-report datasource descriptions, dashboard signal bindings, org ID, fallback Explore queries, and idle session timeout are settings-owned in `component_cli_settings.yaml`; dashboard sources stay source-owned in `component_sources.yaml`. The binding chain is read endpoint -> Grafana datasource -> dashboard source. `Nebius Services` points at the service-provider Monitoring read endpoint used by Nebius/provider service metrics, including built-in VM agent metrics; `Nebius User Metrics` points at the user-ingested Prometheus read endpoint used by Kubernetes workload metrics. The bundled Kubernetes Metrics dashboard uses Nebius-agent/cAdvisor and API-server metrics for cluster and node discovery, CPU, memory, CPU throttling, memory failures, network throughput, network errors/drops, filesystem usage/IO, API-server request rate, and top-pod tables. The Kubernetes Logs dashboard uses Nebius Loki labels for cluster, namespace, and pod selection, log-volume panels, noisy-pod ranking, and warning/error streams. The Kubernetes GPU dashboard uses `Nebius Services`, filters DCGM metrics by `mk8s_cluster_id`, builds GPU-node variables with `query_result(...)`, and keeps GPU utilization, memory, power, temperature, clocks, the current XID code mapped to `No XID` only when the XID read point reports zero, ECC, PCIe replay, and NVLink panels per GPU UUID with `instance_id` as node context. The bundled VM Metrics dashboard uses built-in Nebius Monitoring-agent labels such as `job="nebius-observability-agent"` and `instance_id` from `Nebius Services`; VM Logs uses `Nebius Logs`, defaults to the service-provider `sp_serial` bucket, and can be switched to `default` for user-ingested logs. Bundled Kubernetes dashboard links include the target cluster variable when deploy/flux can resolve the MK8s cluster ID; bundled VM dashboard links do not add that Kubernetes variable. The bundled Traces dashboard stays generic because Tempo resource attributes depend on the emitting workload and are not normalized to a required cluster label by cxcli; it includes recent, slow, and error TraceQL searches that stay valid before workload-specific attributes exist. The generated deploy report now separates public write endpoints, public read endpoints, and Grafana links per configured target, and its Grafana section explains the Prometheus datasource split using the settings-owned datasource descriptions. It lists every cxcli-owned dashboard JSON asset shipped under `src/nebius_cxcli/grafana_dashboards/` and declared in the active catalog, so adding another packaged dashboard makes it visible in `deploy-report.md`; operator-owned external dashboard JSON is still imported into Grafana but is not listed in that report shortcut list. Endpoint labels, URL templates, inclusion conditions, and bucket expansion are settings-owned under the global `observability.endpoints` section, so adding a future tenant/project read endpoint plus a matching Grafana datasource is a settings-catalog change rather than a Python allowlist change. Before live Gateway status is captured, target Grafana links are shown as pending; after the Envoy Gateway `Gateway` has an address and `deploy` or `flux apply` reads it, cxcli waits briefly for the address and the report includes only the Grafana root plus bundled-dashboard links. Separate dashboard-index, Metrics, Logs, and Traces shortcut rows are intentionally omitted from `deploy-report.md` because the bundled dashboard list is the canonical dashboard handoff. `validate-sources` validates every declared dashboard source and then validates that each `components.apps.grafana.cli.dashboard_signals` signal from the settings catalog references one of those dashboard sources. `validate-dashboards <config.yaml>` goes further after deploy by querying the live Grafana datasource proxy for the bound Prometheus, Loki, and Tempo read endpoints and checking each bundled dashboard source against its datasource/read-endpoint chain; Prometheus validation scopes both `k8s.cluster.id` and `mk8s_cluster_id` selectors to the target cluster when that ID is available, and Loki validation uses dashboard variable defaults such as the VM Logs `sp_serial` bucket instead of replacing every variable with a wildcard. The command shows dashboard-level progress with the current `<target-id>: <folder>/<dashboard>` binding while it waits on live Grafana calls. The same section includes a `kubectl --context=...` command to retrieve the target cluster's admin password. cxcli keeps static tokens and Grafana passwords out of repo config and generated Flux manifests.
- For target-scoped Grafana rows, `validate-dashboards` resolves an explicit kube context from generated Grafana status, the deploy report, the matching current local kubeconfig context, an unambiguous local kubeconfig context, or the generated MK8s handoff before running `kubectl`; unresolved targets fail fast instead of using an unrelated ambient current context.
- `grafana --export-dashboard <grafana-base-or-folder-url>` exports dashboards through the Grafana API into operator-owned JSON files under `./dashboards` by default, while `grafana --dashboard-json <path>` normalizes local dashboard JSON through the same file-writing and attach path without calling Grafana APIs. API export authenticates with `GRAFANA_TOKEN`, `NEBIUS_IAM_TOKEN`, `nebius iam get-access-token`, `--token-env`, or Basic auth from `--username` plus `--password-env`. Export-only never changes the catalog. Adding `--attach` updates the active `component_sources.yaml` with JSON dashboard entries under the bundled Grafana app, creates the matching dashboard provider when needed, rewrites dashboard datasource refs to one selected cxcli datasource UID/type, refuses to mix JSON dashboards into a Grafana.com `gnetId` provider, and validates the updated catalog before keeping the edit.
- Grafana intentionally has two Prometheus datasources. `Nebius Services` reads `https://read.monitoring.api.nebius.cloud/projects/<project-id>/service-provider/prometheus`, which exposes Nebius/provider service metrics such as platform, node, GPU, and managed service telemetry. `Nebius User Metrics` reads `https://read.monitoring.api.nebius.cloud/projects/<project-id>/prometheus`, which exposes customer/user-ingested Prometheus metrics such as Kubernetes API, cAdvisor/container, namespace, pod, and workload metrics. These endpoints are server-side metric-domain views, not automatic PromQL aggregation; dashboards choose the appropriate datasource through the catalog metadata.
- cxcli does not vendor or redistribute third-party binaries, Helm charts, container images, package repositories, or Grafana.com dashboards referenced by the bundled catalog. Those upstream artifacts, including Grafana, Envoy Gateway, NVIDIA operators, Flux, Terraform, Helm, kubectl, Grafana.com dashboard imports, and any Nebius/public package repositories used at deploy time, remain governed by their own licenses, support terms, usage terms, and image/chart/package distribution policies. This project's license covers the cxcli source, bundled cxcli-owned dashboard JSON, and generated automation, not the operator's deployed use of referenced third-party artifacts.
- For the full public-safe architecture, endpoint map, agent split, and onboarding model, see [docs/design.md](docs/design.md#observability).

Bundled MK8s GPU app policy:

- MK8s GPU software defaults are policy-driven in code and source-driven in the catalog.
- The bundled catalog keeps chart source selection, release metadata, and default Helm values in `component_sources.yaml`; activation rules, validation images, thresholds, and timeouts live in `component_cli_settings.yaml`. The CLI only evaluates those rules against the selected MK8s context. Platform charts with documented safe HA knobs default to two replicas where upstream defaults are one: Grafana's Envoy data plane, Envoy Gateway, cert-manager controller/webhook/cainjector, and External Secrets controller/webhook/cert-controller. External Secrets also enables leader election with that default. Grafana itself stays on the upstream one-replica default because the bundled chart path uses per-pod SQLite/emptyDir storage; runtime validation rejects `grafana.values.replicas > 1` unless the chart values configure a shared MySQL or Postgres database. DaemonSets, validation jobs, n8n's enterprise-only multi-main path, and charts without a chart-native safe replica knob stay on upstream defaults.
- Soperator renders make cert-manager `Certificate.spec.privateKey.rotationPolicy` values explicit as `Always`; both local and portable source-backed Soperator outputs use the static post-Flux manifest path, so the normalized Certificate manifests are applied directly.
- The same `source.portable` / `source.local` contract now applies to first-party Helm charts as well as Terraform modules.
- The canonical GPU role is `nvidia-gpu-operator` for both Nebius-image and `operator_managed` node groups. On Nebius-managed images the CLI disables the driver, the NVIDIA Container Toolkit runtime (`values.toolkit.enabled`), and the Nebius `NVIDIADriver` CRD path. On `operator_managed` stacks it keeps the normal GPU Operator driver and toolkit paths enabled but still forces `values.driver.nvidiaDriverCRD.enabled=false`, because the bundled GPU Operator chart's Nebius `NVIDIADriver` CRD template is currently broken during Flux install. The catalog now keeps only those Nebius-specific operator deltas instead of restating live chart defaults. cxcli does not pre-label operator-managed nodes with `nvidia.com/gpu.deploy.operands=true` or `nvidia.com/gpu.deploy.device-plugin=true`; those operand labels are manual escape hatches for preinstalled-driver or forced-operand workflows, while the operator-managed path lets GPU Operator bring the stack up in dependency order and validates readiness through ClusterPolicy, DaemonSets, and allocatable `nvidia.com/gpu`.
- The `gpu_stack_source` wizard menu is part of the GPU-enabled MK8s flow and keeps the same stored values but labels them with the driver ownership difference: `nebius_image` means the Nebius GPU node image already includes the host NVIDIA driver/toolkit, while `operator_managed` means GPU Operator installs and manages those host components. CPU-only MK8s configs omit `inputs.node_group_defaults.gpu.gpu_stack_source` and per-node-group `gpu_stack_source`; when GPU node groups are enabled and the field is omitted, the module and cxcli GPU policy fallback still use `nebius_image`.
- Because that bundled operator baseline assumes the DCGM source remains available to the operator, GPU-enabled MK8s config now fails fast if it explicitly sets `nvidia-gpu-operator.values.dcgmExporter.enabled: false`. Prometheus scrape wiring remains the chart-native `values.dcgmExporter.serviceMonitor.*` surface rather than a `deploy` toggle, and it should only be enabled when the target cluster already has a Prometheus-operator-compatible observability stack.
- cxcli treats GPU-cluster / RDMA as a two-stage decision. First it queries the live Nebius project platform/preset inventory for the exact selected GPU platform and preset and uses `allow_gpu_clustering` as the source of truth for whether the shape is cluster-capable at all. Second, the deployment only enters the GPU-cluster / InfiniBand / GPUDirect-RDMA path when a GPU node group references `inputs.gpu_clusters` with an `infiniband_fabric`. Resource-name preflight still checks every GPU cluster referenced by a node group's `gpu_cluster_key`, even before a fabric is selected, so stale live GPU-cluster names are caught early. In the plain MK8s node-group wizard, selecting a live cluster-capable Capacity Dashboard row materializes that row's fabric automatically; omitting the referenced fabric in manual config keeps the target on the Ethernet-only path for rendering, validation, and app auto-selection.
- `nvidia-gpu-operator` is auto-enabled for every MK8s cluster with at least one GPU `inputs.node_groups` entry. `inputs.node_group_defaults.gpu.*` helper values alone never make a target GPU-enabled; they must be materialized into a real GPU node group first. `nvidia-network-operator` is auto-enabled only on that actual GPU-cluster path, plus the special `gpu_stack_source: operator_managed` B200/B200A path that still needs RDMA plumbing. When Network Operator is required, cxcli renders a Flux `dependsOn` edge so it reconciles before GPU Operator, suppresses GPU Operator's own NFD so the bundled stack keeps only one NFD instance, explicitly enables Network Operator NFD/NodeFeatureRules because the chart defaults them off, and renders an explicit `NicClusterPolicy` patch for `rdma/shared_device` on the InfiniBand path instead of relying on chart defaults. That patch disables the RDMA shared-device plugin's periodic host-device rescan with `periodicUpdateInterval: 0`, while preserving startup discovery and pod-facing RDMA resource advertisement. The bundled catalog keeps Network Operator NFD enablement, Network Operator driverful node selection, and GPU Operator non-cluster Nebius-image NFD affinity as separate named policy sets, so NFD ownership stays explicit and selector details stay catalog-owned without being repeated inline in multiple rules. Those MK8s GPU policy-managed chart-value paths are authoritative on `create`, `component add`, direct `config.yaml` normalization, and `render`: cxcli rewrites the currently applicable paths from the catalog and clears no-longer-applicable policy paths instead of preserving stale older operator values from `config.yaml`. Stale policy-managed GPU app rows can be pruned when no target needs them, including auto target-scoped rows that carry catalog source metadata, but explicitly authored enabled app rows with their own repo/version/namespace remain selected. In multi-target projects, cxcli seeds any missing target-bound required app rows and evaluates policy per target-bound app row, so an RDMA cluster and an Ethernet-only 1-GPU test cluster can coexist without sharing incompatible GPU Operator or Network Operator values.
- MK8s node inventory smoke is required for every MK8s deploy target as a fast read-only all-node gate. It performs one bulk Kubernetes node inventory read, reports every node's Ready state plus CPU/GPU/node-group totals, groups node details by node group in the JSON detail report, and checks that GPU-enabled targets have scheduler-visible `nvidia.com/gpu` allocatable resources, including configured or inventoried GPU node-group presence and minimum expected Ready GPU node counts when available, before any sampled CUDA pod or explicit benchmark workload starts. Rendered MK8s node groups carry the canonical `nebius.com/node-group` label so that the Kubernetes-only inventory can match live nodes back to the configured node-group keys.
- GPU visibility is enabled by default for GPU-backed MK8s deploys, including Soperator production targets, but it remains intentionally bounded and workload-based instead of stopping at a node `allocatable` check: by default it runs the CUDA sample on at most 3 Ready GPU nodes, reports live pod phase progress, bulk-cleans the validation pods afterward, and saves the underlying device-plugin allocatable snapshot in the report for comparison. If existing workloads such as Slurm workers already reserve every GPU on every Ready GPU node, cxcli records the GPU visibility probe as skipped instead of failing on an expected scheduler admission rejection.
- NCCL settings are command-only benchmark settings for explicit `nebius-cxcli acceptance-test benchmark` runs; they are not persisted under `config.yaml` and are not part of deploy smoke. The K8s NCCL workload manifest comes from the first-party transient `helm-charts/nccl-test` chart: `source.local.path` points at the checked-in chart for developer/local use, and `source.portable` is pinned to `oci://cr.<region>.nebius.cloud/<registry-short-id>/charts/nccl-test --version 0.2.8`. The shared image/tag plus benchmark policy defaults are sourced directly from the chart's own `values.yaml` and `component_cli_settings.yaml` at `components.infra.mk8s.cli.gpu.benchmarks.nccl`; local/unit-test default hydration falls back to the checked-in chart values when `helm` is unavailable. `nebius-cxcli` auto-selects NCCL transport from the resolved MK8s shape: Ethernet-only shapes run Socket/TCPIP mode, while GPU-cluster / InfiniBand shapes switch to the RDMA path. On that RDMA path, cxcli appends `NCCL_DMABUF_ENABLE=1` as an MPI environment export. See [Acceptance Testing](#acceptance-testing) for smoke-vs-benchmark guidance, suite selection, and copy-paste benchmark commands.

- For cxcli-managed Soperator targets with fixed or nonzero-minimum GPU workers, the first `deploy` stages local app reconciliation: it applies the platform/GPU operator Flux resources, runs the required MK8s inventory plus bounded GPU visibility checks while the GPUs are still scheduler-free, then applies the full Soperator bundle and runs the required fast Soperator deployment snapshot. If a managed Soperator GPU worker group autoscaling range starts from zero (`min_node_count: 0`, `max_node_count > 0`), cxcli applies the full Soperator bundle, requests the Soperator power-state bootstrap for ephemeral GPU workers, runs the required Soperator deployment snapshot, and then runs the MK8s inventory/readiness/GPU visibility checks against any resumed GPU nodes. For external/adopted Soperator targets, or reruns where Soperator worker pods already reserve every Kubernetes GPU, the Kubernetes GPU visibility probe can still be skipped because there are no scheduler-free GPUs. Deploy writes `cluster-inventory-report-<target>.json` for inventory, `deploy-gpu-stack-readiness-report-<target>.json` and `deploy-gpu-visibility-report-<target>.json` for MK8s GPU deployment testing, and `deploy-smoke-report-<target>.json` for Soperator deployment testing. It does not run exhaustive all-node Slurm checks, Slurm `srun` jobs, or NCCL/performance benchmarks. Operators run `nebius-cxcli acceptance-test smoke <config.yaml> --target <target> --suite slurm` later for exhaustive all-node Slurm hostname/GPU smoke in `acceptance-smoke-report-<target>.json`, and run `nebius-cxcli acceptance-test benchmark ...` explicitly for K8s or Slurm NCCL/performance work in `acceptance-benchmark-report-<target>.json`. Both acceptance-test smoke and benchmark commands require `--suite`; after a suite is selected, they run all generated targets when `--target` is omitted. They resolve target handoff from `generated/reports/deploy-report.md`, an explicit or unambiguous local kubeconfig context, or a known cluster ID; they do not read Terraform state or initialize the Terraform backend. If that handoff is missing, run `deploy` or `flux apply` for the target first. The JSON detail reports include `test_purpose`, `mode`, `scope`, `kind`, and `target_ref` so copied report files remain self-describing. The required Soperator deploy report is a Kubernetes snapshot after bounded first-run storage/pod startup: it checks the `soperator-manager` Deployment, jail storage objects, Pending Soperator pods/events, failed or crash-looping Soperator pods, the target SlurmCluster, and NodeSet resources without waiting for full Slurm availability. If a `populate-jail` Job pod stays active after the same-node `jail-mount` pod can see the jail `.populated` sentinel, cxcli deletes only that stuck Job pod and waits for the Job to complete before taking the final snapshot. The cluster inventory report remains read-only and includes Slurm partitions and nodes when the login pod and `sinfo` are already queryable, so large clusters stay inventory-oriented while deploy stays fast.

What `wizard` is doing:

- `wizard` does not create Terraform variables or Helm values. Those fields already come from the Terraform module or Helm chart contract.
- `wizard.<field>.options` is the provider-backed wiring layer that tells `nebius-cxcli` how to populate one existing field from a guided Nebius or Nebius-contract lookup.
- `wizard.<field>.sources` is the fixed-choice wiring layer. The bundled profiles use `source: static` for local lists such as PostgreSQL tier, object-storage mode, public-IP mode, and GPU stack source. Static values can be strings or `{value, label}` mappings when the stored value needs a richer operator-facing label.
- In other words, the Terraform input path stays the operator-facing destination, and the `wizard` metadata tells the CLI where to fetch valid choices for that destination.
- Without that metadata, the field can still exist and be prompted as a normal string/bool/number field, but the CLI will not know which Nebius-backed lookup to run for it.

What `status` is doing:

- `status` is not a Terraform input and it is not rendered into the module itself.
- It is catalog metadata for Nebius deployment-status polling during commands such as `deploy` and `terraform apply`.
- If an infra component wants Nebius status polling, declare `status.kind` explicitly.
- Use `status.parent_input`, `status.name_input`, and optional ordered `status.name_inputs` only when the resource is identified by input names other than the defaults `parent_id` and `name`.
- A watcher name source may resolve either one scalar resource name or a collection of objects that each contain a nested `name`; in the latter case the CLI expands one component row into one watcher spec per resolved resource name. `status.name_inputs` lets a component prefer a collection first and fall back to a scalar name only when the collection is empty.
- During `destroy`, a watcher resource that is missing from the live Nebius API is reported as already absent. Terraform state remains the authority for whether Terraform still needs to reconcile a delete.
- The bundled `mk8s` component is the scalar example: `status.kind: nebius.mk8s.cluster` declares the Nebius resource type, and `status.name_input: cluster.cluster_name` tells the watcher which typed cluster input contains the actual cluster name.
- The bundled `mysterybox` component is the collection example: `status.kind: nebius.mysterybox.secret` with `status.name_input: secrets` expands the canonical `inputs.secrets` list into one watcher per configured secret `name`.
- The bundled `sfs` component is the ordered-source example: `status.name_inputs: [filesystems, name]` watches each configured `inputs.filesystems` entry when present and otherwise falls back to scalar `inputs.name`.
- Supported bundled watcher kinds currently include `nebius.mk8s.cluster`, `nebius.msp.postgresql.cluster`, `nebius.compute.filesystem`, `nebius.compute.instance`, `nebius.mysterybox.secret`, and `nebius.storage.bucket`.
- Fail-fast behavior is service-native: MK8s watchers inspect live node-group events, while the PostgreSQL/filesystem/compute-instance/MysteryBox/object-storage watchers combine live resource state with the latest terminal Nebius operation status for that resource.
- If one watcher cannot evaluate terminal operation status, the merged status output reports that watcher as terminal-check unavailable instead of hiding the failed sub-poller; this diagnostic does not abort Terraform by itself.

Example:

```yaml
wizard_profile: mk8s
```

That shorthand expands to the equivalent wiring for the built-in MK8s flow, including:

- `inputs.cluster.network_id` from the Nebius `project_networks` lookup
- `inputs.cluster.subnet_id` from the Nebius `project_subnets` lookup, filtered to the selected `inputs.cluster.network_id`
- `inputs.cluster.k8s_version` from the Nebius MK8s control-plane version lookup, with the first live version auto-selected into the wizard/config unless you override it
- a node-group creation loop that defaults the first group name to `system`, then lets you add more concrete `inputs.node_groups.<name>` entries
- each node group's `platform` and `preset` from the MK8s compatibility lookup intersected with the selected project's live compute-platform inventory
- each node group's `boot_disk` materialized from live/provider-backed choices and shared disk policy
- `inputs.node_groups.system` is the concrete default CPU baseline node group for a plain MK8s target. `system` is just the node-group role/name in the generated config; it is not the Soperator app. Its `node_count` or `autoscaling` fields are the actual scale controls. The plain MK8s node-group loop asks whether autoscaling is enabled for each concrete group and keeps it disabled by default; when enabled, the loop writes `autoscaling.min_node_count` and `max_node_count` instead of `node_count`.
- `inputs.cluster.kube_network.service_cidrs` is Kubernetes Service ClusterIP
  space, not Pod IP space. cxcli keeps the `["/20"]` default for Services.
  Nebius allocates one `/24` Pod block and one `/32` internal node IP per node
  group node; default rolling updates also need one extra node of subnet
  capacity per node group. The wizard warns immediately when the selected
  cluster subnet has known explicit CIDRs that cannot fit the entered
  `node_count` or autoscaling `max_node_count`, and `validate` fails the same
  condition for live or planned explicit subnets, including explicit
  node-group subnet bindings. For example, a `/16` subnet provides 256 `/24`
  Pod blocks, while a 1000-node group needs 1001 blocks and therefore at least
  a `/14` equivalent private subnet range.
- `inputs.node_group_defaults.*` is a profile helper surface, not a bundled MK8s Terraform module input and not a scale control. cxcli keeps it for profile materialization such as Soperator `production-cluster`, where helper defaults are copied into real typed `inputs.node_groups` and `inputs.gpu_clusters`; plain MK8s-only create, component-add, and normalized runtime config suppress or prune those helper fields. CPU-only Soperator profiles skip and prune the inactive `inputs.node_group_defaults.gpu.*` helper scope during the wizard and runtime config normalization, so GPU fabric, reservation, and stack fields are not offered or retained unless the selected profile actually creates GPU node groups. Soperator profile boot-disk defaults merge into concrete node groups and keep cxcli's computed `size_gibibytes` values so generated Terraform always carries both boot-disk type and size.
- In profile-owned GPU flows, `inputs.gpu_clusters.<key>.infiniband_fabric` is the single persisted fabric source of truth. cxcli derives that value only after the GPU preset and only when the exact selected platform/preset's live Nebius metadata says `allow_gpu_clustering=true`; stale `inputs.node_group_defaults.gpu.infiniband_fabric` configs fail validation instead of being translated.
- In plain MK8s GPU node-group loops, reservation IDs are offered from tenant Capacity Block Groups filtered by the selected region, platform, and GPU-cluster fabric when a fabric is selected.
- When tenant/project/region context is available, GPU preset selection queries the live Nebius Capacity Dashboard `resource-advice` surface for the selected GPU platform and region. The GPU preset prompt is a policy-matching row selector: each choice shows preset, fabric, regular-vm or reserved VM slots, and GPU totals, for example `1 VM (1 x 1-GPU = 1 GPU)` or `2 VMs (2 x 8-GPU = 16 GPUs)`. For cluster-capable multi-GPU rows, the selected row is the source of truth for both the stored preset and the fabric written to `config.yaml`; for 1-GPU Ethernet-only rows, cxcli stores only the preset and omits the GPU-cluster fabric even though the Capacity Dashboard row is fabric-scoped. `AUTO` keeps both reserved and regular-vm choices while recommending reserved-backed capacity first, `STRICT` lists reserved-capacity choices, and `FORBID` lists regular-vm choices.
- GPU interconnect guidance is printed before GPU preset selection instead of being repeated inside every preset label: single-GPU non-clusterable shapes are Ethernet-only testing/dev shapes, while clusterable multi-GPU shapes are the InfiniBand path.
- The Capacity Dashboard can still return fabric-scoped capacity rows for single-GPU Ethernet-only shapes because capacity is physically partitioned that way. cxcli shows those rows as selectable capacity/preset choices, but the materialized fabric stays empty unless the live preset metadata says GPU clustering is supported.
- This follows the Nebius Compute contract in [Types of virtual machines and GPUs](https://docs.nebius.com/compute/virtual-machines/types#presets-compatible-with-gpu-clusters): cxcli queries the live project platform/preset inventory first, then uses the selected preset's live `allow_gpu_clustering` metadata as the source of truth for GPU-cluster eligibility instead of keeping a hardcoded preset list in the wizard

Profile-plus-override example:

```yaml
wizard_profile: mk8s
wizard:
  inputs.cluster.subnet_id:
    options:
      from: project_subnets
      args:
        network_id_path: inputs.cluster.network_id
      filter_regex: "^vpcsubnet-"
```

That keeps the rest of the built-in `mk8s` profile unchanged and replaces only the `inputs.cluster.subnet_id` field wiring with the explicit override while preserving the selected-network filter.

Explicit `wizard` example:

```yaml
wizard:
  inputs.node_groups.system.platform:
    options:
      from: mk8s_compatible_platforms
      prefix: cpu-

  inputs.node_groups.system.preset:
    options:
      from: compute_platform_presets
      depends_on: inputs.node_groups.system.platform
```

How the explicit example works:

- `inputs.node_groups.system.platform` is the concrete CPU baseline node-group platform field for the built-in plain MK8s flow.
- `from: mk8s_compatible_platforms` tells the CLI to call the Nebius-backed compatibility lookup for that field.
- When `client_info.nebius.project_id` is available, that lookup keeps only platform names that are both MK8s-compatible for the chosen control-plane version and present in the selected project's live compute-platform inventory.
- `prefix: cpu-` keeps only CPU platform names from that compatible/project-scoped result set. This is a plain prefix filter, not regex.
- `inputs.node_groups.system.preset` is the matching concrete preset field.
- `from: compute_platform_presets` tells the CLI to query Nebius for presets of a selected compute platform.
- `depends_on: inputs.node_groups.system.platform` means the preset lookup uses the operator's chosen CPU platform value as input to the next API call.
- That makes the second field a chained lookup: first choose a compatible platform, then choose one of the presets available for that exact platform.
- Chained provider-backed fields are prompted only after the dependency field has a concrete value. If the operator skips an optional platform helper, the dependent preset helper is not prompted yet instead of falling back to a misleading manual-entry warning.

Regex and pattern behavior:

- `wizard.<field>.options.filter_regex` is the only regex-capable field in `component_sources.yaml`.
- `filter_regex` is compiled as a Python regular expression and applied to provider-returned option values with regex `search`, not exact-match.
- The same `filter_regex` is used both for displayed wizard choices and for strict provider-backed manual-entry validation, so operators cannot type a value that the catalog-level filter was meant to exclude.
- `wizard.<field>.options.prefix` is a plain literal prefix helper for provider lookups. It is not regex.
- `wizard.<field>.options.depends_on` is a plain field-path reference such as `inputs.node_groups.system.platform`. It is not regex.
- `wizard.<field>.options.auto_select_single: true` tells the wizard to preselect a provider-backed compatible option when exactly one exists and the field is currently unset.
- `wizard.<field>.options.auto_select_first: true` tells the wizard to preselect the first provider-backed compatible option after provider-specific ordering when the field is currently unset. For VM public images, that means Nebius `recommended_platforms` matches sort before other compatible image families.
- `wizard.<field>.options.args` passes provider-specific lookup arguments through directly; the shorthand helpers `prefix` and `depends_on` are merged into that args mapping during catalog load.
- `wizard.<field>.options.skip_prompt_if_no_choices: true` suppresses an optional provider-backed prompt when the live lookup succeeds but returns no valid choices for the current shape.
- Component ids and instance selectors are validated against the repo's lowercase letters/digits/hyphens naming rules.
- `component_cli_settings.yaml` `cli.flux.version` must look like `v2.8.0`; `cli.flux.release_timeout` and `release.timeout` must be Go-style durations such as `5m` or `12m30s`; `cli.terraform.version` must look like `1.15.5`.

Wizard field keys:

- `default`: optional prompt default. It stays virtual unless `write_default_to_config` is true.
- `write_default_to_config`: optional boolean; when true, accepting an unchanged wizard default writes it into `config.yaml`.
- `prompt`: optional boolean; use `false` to suppress a field from interactive prompting while keeping the manual config field valid.
- `options`: optional provider-backed choice source.
- `sources`: optional static choice source.

Wizard `options` keys:

- `from`: provider-option source name such as `mk8s_compatible_platforms` or `tenant_projects`
- `prefix`: optional literal prefix filter passed into provider lookups
- `depends_on`: optional sibling field path used to drive provider lookup args
- `args`: optional provider-specific argument mapping; use this for extra lookup inputs beyond the `prefix` / `depends_on` shorthands
- `filter_regex`: optional regex post-filter for returned option values
- `auto_select_single`: optional boolean for provider-backed fields; when true, the wizard preselects the one compatible option if the lookup resolves to exactly one option
- `auto_select_first`: optional boolean for provider-backed fields; when true, the wizard materializes the first compatible option after provider-side sorting
- `skip_prompt_if_no_choices`: optional boolean for provider-backed optional fields; when true, the wizard skips the prompt entirely if the live lookup returns no valid choices and no current value is set

Static wizard source keys:

- `source: static`: fixed local choice list
- `values`: list of allowed choices; each item may be a string or a `{value, label}` mapping

Reference syntax:

- `defaults` shared-value reference: `shared.admin_ssh.user_name`
- `input` binding without instance selector: `mk8s.cluster_id`
- `input` binding with explicit instance selector: `mk8s@cluster-a.cluster_id`

`wizard_profile` and `wizard` are optional catalog metadata for advanced wizard behavior. Most components should rely on Terraform variable or Helm values introspection alone; use `wizard_profile` only when the bundled component-specific profile already exists for that same component id, or use `wizard` when a field needs explicit Nebius-backed choices or another catalog-defined override.

Built-in cluster handoff:

- Cluster handoff for kubeconfig/bootstrap is no longer declared in `component_sources.yaml`.
- The bundled `mk8s` component has a code-owned built-in cluster handoff contract.
- That built-in contract reads the Terraform output `cluster_id` and derives endpoint access from `inputs.cluster.public_endpoint`.
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
  - Local chart staging copies symlink targets into the staging tree, rebuilds
    `file://` chart dependencies in that temporary staging tree so local child
    chart edits are not hidden by stale packaged archives, and prepares
    temporary Helm repo entries for locked remote dependencies instead of
    requiring global `helm repo add` state. Generic Helm hooks are stripped from
    the static local render, but hooks annotated with
    `nebius-cxcli.nebius.ai/include-local-render=true` are kept for cxcli's
    post-Flux apply path.
  - Portable build/release verification strips `source.local` and fails if an app chart still has no usable `source.portable`.
  - GitHub tree format: `source.portable.repo` may point at a chart directory in git (`https://github.com/<owner>/<repo>/tree/<ref>/<chart-path>`). Helm validates the chart from that path directly.
  - Helm chart sources are fail-fast validated with `helm show chart`; missing Helm or Git for Git tree chart sources, unreachable repos, bad refs, missing charts, and version mismatches are hard failures.
  - Set `NEBIUS_CXCLI_HELM_TIMEOUT_SECONDS` to raise the Helm validation timeout for slow OCI registries or chart sources without changing the catalog.
  - `validate-sources` validates the full catalog, including optional app charts. `create` and `component add` first validate infra sources, then validate only the app chart sources selected for that operation plus any app dependencies they auto-enable, and run a final app-source check after the wizard to catch late auto-enabled rows before `config.yaml` is written. If a transient Helm repository or registry timeout blocks an interactive create, retry, raise `NEBIUS_CXCLI_HELM_TIMEOUT_SECONDS`, or rerun with `--no-validate-sources` to skip that source check.
  - `validate-sources` also materializes the resolved chart and checks the CLI-facing chart contract:
    - fails when `Chart.yaml`, `values.yaml`, or `templates/` are missing
    - fails when `Chart.yaml` is missing `apiVersion`, `name`, or `version`
    - warns when the chart is not on canonical Helm v2 metadata
    - warns when a local chart path is missing `README.md`; remote Helm chart packages are allowed to omit it
  - For `source.local.path`, missing catalog `chart` or `version` metadata is
    derived from the checked-out chart's `Chart.yaml`, so local-profile
    generated `config.yaml` rows show the active local chart version while
    still leaving `repo` blank for static local chart rendering.
  - In a project `config.yaml`, an app chart row with `repo: ''` stays on the
    static local render path when the selected catalog source has local chart
    metadata. A non-empty `repo` selects a Helm source directly. For example,
    to use the published Soperator parent OCI package instead of the local
    chart tree, set the Soperator row to:

    ```yaml
    repo: oci://cr.eu-north1.nebius.cloud/e00th0mgv3zddz7468/charts/soperator
    version: 4.0.2-ps.3
    ```

    Then run `render` and `deploy` or the narrower `flux apply`. Soperator is
    special-cased to render the selected OCI/local chart source into a static
    post-Flux manifest instead of a Flux `HelmRelease`, because the umbrella
    chart can exceed Kubernetes' 1 MiB Helm release Secret limit when Helm
    stores release state in-cluster.
  - App `defaults` seed `values.*` when missing.
  - `release.namespace` and `release.name` are the default Helm namespace and release name used by `create` and `component add`.

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
When a `component_sources.yaml` file is selected, nebius-cxcli also loads sibling `component_cli_settings.yaml` when it exists.
`component_sources.yaml` is the source catalog for `create` component selection and runtime source-backed validation; `component_cli_settings.yaml` supplies cxcli-owned behavior for the same component ids.
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
        name_input: cluster.cluster_name
      defaults:
        inputs.cluster.public_endpoint: true
        inputs.cluster.kube_network.service_cidrs: ["/20"]
      wizard:
        inputs.node_groups.system.platform:
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

Matching cxcli settings example inside sibling `component_cli_settings.yaml`:

```yaml
cli:
  flux:
    version: v2.8.0
    release_timeout: 5m
  terraform:
    version: 1.15.5

observability:
  endpoints:
    read:
      metrics_user_read:
        label: Metrics read (Prometheus, user-ingested metrics)
        template: https://read.monitoring.api.nebius.cloud/projects/{project_id}/prometheus
        include_when:
          - kubernetes_metrics

components:
  apps:
    grafana:
      cli:
        datasources:
          user-metrics:
            name: Nebius User Metrics
            uid: nebius-user-metrics
            type: prometheus
            read_endpoint: metrics_user_read
```

`cli.flux.version` is the settings-controlled Flux controller version for local `deploy` and the managed Flux CLI download path.
`cli.flux.release_timeout` is the settings-controlled default Flux `HelmRelease.spec.timeout` used when an app chart does not set `release.timeout`.
`cli.terraform.version` is the settings-controlled Terraform CLI version for the managed Terraform download path. It controls the Terraform binary only; rendered roots and platform modules still declare provider source/version compatibility in `terraform.required_providers`.
The bundled default is `5m`, which matches the upstream Helm/Flux default action timeout. To change one global app-install timeout policy or either managed tool version, bump the value in the active `component_cli_settings.yaml`.

Portable build/release behavior:

- `component_sources.yaml` and `component_cli_settings.yaml` are the checked-in catalog pair.
- Build/package steps bundle a portable view of `component_sources.yaml` into the wheel by stripping `source.local`, and bundle `component_cli_settings.yaml` unchanged.
- Any app chart that still lacks `source.portable` is intentionally local-only and will fail portable release verification until a portable chart source is published.
- CI/release workflows rewrite internal `source.portable` refs from `?ref=main` to the current commit or tag before publishing wheel or catalog assets.

Recommended workflow:

- Automatic catalog resolution is a convenience default, not a portability guarantee.
- `validate` and `render` default to the `portable` source profile, which emits deployable Terraform module sources suitable for CI and other machines.
- Installed-package fallback is portable by default: when no repo-local/user/global override is present, the packaged `nebius_cxcli/component_sources.yaml` uses Git Terraform module sources and the packaged `nebius_cxcli/component_cli_settings.yaml` supplies cxcli settings.
- Use `--source-profile local` or `NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE=local` when you intentionally want generated Terraform to point at checked-out local module paths for workstation testing.
- Use `--component-sources-file` or `NEBIUS_CXCLI_COMPONENT_SOURCES_FILE` only when you need to override which catalog file is active; it is not the primary portable-vs-local switch.
- Generated-bundle commands do not need the source catalog to resolve Terraform module paths from the original render environment.

Typical usage:

```bash
# Local development against checked-out Terraform modules
nebius-cxcli --source-profile local validate /path/to/config.yaml
nebius-cxcli --source-profile local render /path/to/config.yaml

# Portable generation for CI / another repository / another machine
nebius-cxcli render /path/to/config.yaml
```

Managed vs external local tools:

- Downloaded by `nebius-cxcli` into its local cache when missing for supported command paths:
  - `terraform` for Terraform-backed validation, render lockfile generation, `terraform plan`, `terraform apply`, `terraform unlock`, and backend-backed Terraform output lookups
  - `flux` for `flux bootstrap`
- Still external prerequisites:
  - `kubectl` for `validate-generated`, `deploy`, `upgrade`, `destroy`, `flux apply`, `flux destroy`, `flux bootstrap`, and Flux readiness checks
  - `helm` for `validate-sources` and other live Helm chart source/metadata validation paths; not for the normal `deploy`/`flux apply` flow
  - `aws` CLI for `terraform unlock` remote lock inspection
  - `git` for `bootstrap-ci` repo-origin auto-detection and Git tree chart sources

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
When enabled charts are deployed, the CLI reads the rendered Terraform output `cluster_id` and derives endpoint access from `inputs.cluster.public_endpoint` before Flux/kubectl work starts.

For app source entries, `release.namespace` and `release.name` are defaults:

- interactive `create` and `component add` wizards prompt them for selected app
  rows
- non-interactive `create` and `component add` can override added/selected app
  rows with:
  - `--app-namespace <app-id>=<namespace>`
  - `--app-releasename <app-id>=<release-name>`
  - `--app-version <app-id>=<chart-version>`

App chart versions default to the active `component_sources.yaml` pin. During
interactive `create` and `component add`, each selected app chart prompts for
`apps.charts[].version` immediately after the default preview and before the
full app-config yes/no prompt; pressing Enter keeps the pin, and typing a
different version records that value in `apps.charts[].version` even when the
rest of the app config is skipped. In non-interactive mode, `--app-version`
provides the same override for app rows added by the command. With source
validation enabled, cxcli validates requested non-catalog chart versions against
the resolved Helm/OCI source before writing `config.yaml`.

Runtime config shape:

- `client_info`: `client_name`, `nebius.{project_id,region_id}` plus optional `nebius.tenant_id`, `notifications.{email_enabled,email}`
- `client_info.notifications.email_enabled` is the single per-client gate for deploy-report email delivery across local runs and CI. Keep it `true` when this client should receive the deploy report email, and set it to `false` when this specific client should not receive mail.
- In `create`, leaving the optional notifications email blank writes `client_info.notifications.email_enabled: false` and `client_info.notifications.email: null`.
- `client_info` does not include legacy `env` or `cluster_name` fields.
- `infra.components[]`: `id`, `instance_id`, `enabled`, `inputs`
- `apps.charts[]`: `id`, `instance_id`, `group`, `enabled`, `repo`, `version`, `namespace`, `release-name`, `values`
- Enabled `apps.charts[]` rows require at least one enabled MK8s target in `infra.components[]`, and each app row `instance_id` must match a target cluster `instance_id`.
- Source catalogs use `release.name`; project `config.yaml` uses `release-name`. Alias keys are intentionally unsupported.
- Static nested component configs (`infra.<component>.enabled`, `apps.<group>.<chart>.enabled`) are not supported.
- Canonical project path shape: `<deployments-root>/<tenant-folder>/<project-folder>/config.yaml`

Infra module source selection comes from the active `component_sources.yaml`. `config.yaml` does not need to pin `infra.components[].source` or `infra.components[].version`.
New starter configs omit those fields entirely.

Flux render output (canonical):

- Built-in MK8s target bundles:
  - `generated/flux/targets/<target-id>/helm-repositories.yaml`
  - `generated/flux/targets/<target-id>/namespace-<namespace>.yaml`
  - `generated/flux/targets/<target-id>/configmap-grafana-<folder>-dashboards.yaml` for cxcli-owned Grafana dashboard JSON when Grafana is enabled
  - `generated/flux/targets/<target-id>/helmrelease-<group>-<release>.yaml`
  - `generated/flux/targets/<target-id>/kustomization.yaml`
- cxcli-owned Grafana dashboard JSON assets:
  - `generated/grafana_dashboards/<target-id>/<folder>/<dashboard>.json`
- Legacy nested Flux layout (`generated/flux/apps`, `generated/flux/sources`) is not supported.

Generated manifest output:

- `generated/nebius-cxcli-manifest.json`
- The generated manifest includes the render-time quota report alongside the runtime config snapshot and deploy metadata, so later bundle commands can explain quota-related failures without rerendering first.
- `create` and `render` do not create `generated/reports/deploy-report.md`. That file is a runtime handoff report and is written by deploy/apply commands after live state can be read. All lifecycle reports stay in the single `generated/reports/` folder. Each command owns a deterministic latest artifact, for example `upgrade-node-template-report.md`, `upgrade-node-group-report.md`, `soperator-discovery/<target>/manifest.json`, `ext-soperator-upgrade-report.md`, and `soperator-upgrade-report.md`; reruns replace that command's latest report or bundle, and longer-term session history belongs in git history or an explicit project snapshot.
- Rerendering preserves command-owned runtime reports such as `deploy-report.md`, the external/managed Soperator `soperator-discovery/` bundle directory, `ext-soperator-upgrade-report.md`, `upgrade-node-template-report.md`, `upgrade-node-template-report.json`, `upgrade-node-group-report.md`, `upgrade-node-group-report.json`, `soperator-upgrade-report.md`, `soperator-upgrade-report.json`, and JSON detail reports referenced from those Markdown reports, while unrelated stale report files are still removed with the replaced generated bundle.
- `deploy-report.md` is the deploy-time human-readable customer handoff report. It combines the project inventory with a `Validations` section, and `nebius-cxcli email` sends that same file after it exists. Command-specific upgrade reports are operational evidence for the command that wrote them.
- The report starts with a `Client` section for the client name, tenant, project, and region. `Infra`, `Apps`, and `Grafana` use focused subsections: `Infra Component Status` and `App Component Status` list enabled and disabled catalog rows, while catalog-driven `Infra Component Reports` and `App Component Reports` include enabled rows only. MK8s cluster details are nested per cluster, enabled app handoff details stay grouped by platform/observability/workload where useful, and Grafana links plus credentials are grouped per target. MK8s cluster rows include the Nebius cluster ID and derived kube context when Terraform state is available, and Grafana credentials use that target-specific `kubectl --context=...` command.
- The generated report is emitted without trailing blank lines so customer-repo Markdown linting stays clean.
- `deploy`, `terraform apply`, `flux apply`, and `flux bootstrap` refresh that report artifact for the active project.

Terraform render output (canonical):

- `generated/infra/backend.tf`
- `generated/infra/versions.tf`
- `generated/infra/providers.tf`
- `generated/infra/variables.tf`
- `generated/infra/main.tf`
- `generated/infra/outputs.tf`
- `generated/infra/terraform.auto.tfvars.json` (rendered locally and recreated by cxcli from `generated/nebius-cxcli-manifest.json`; ignored in git to avoid a second versioned copy of the same inputs)
- `generated/infra/.terraform.lock.hcl` (generated during `render` when Terraform is available from `PATH` or the managed download path and backend-disabled init succeeds; transient `.terraform/` workdir state is removed afterward)
- Local Terraform module sources are rendered as resolved filesystem paths. Use an explicit `git::...//subdir?ref=...` source in `component_sources.yaml` when you want a portable or pinned remote module reference.
- Terraform remote state is managed separately from app/object-storage components: backend bucket settings are derived from `client_info` (`client_name` + `project_id` + `region_id`), not from `infra.components[id=object-storage].inputs`.
- Backend locking uses Terraform S3 lockfile mode (`use_lockfile = true`) and Nebius Object Storage endpoint (`https://storage.<region>.nebius.cloud`).
- If a deploy/apply is canceled while Terraform is waiting on or holding the backend lock, the remote `.tflock` object can remain behind. In that case the next apply fails before creating any resources; the CLI now reports that explicitly and includes the lock owner/creation time from Terraform's lock metadata.
- `nebius-cxcli terraform unlock <generated-dir>` is the explicit manual recovery path for that case. It inspects the remote `.tflock`, refuses by default when local Terraform/deploy processes are still active or the lock owner is from another machine/user, and then uses Terraform's own `force-unlock` only when the lock looks stale. Do not run it as routine cleanup.
- `destroy` and `terraform destroy` can use that same guarded stale-lock recovery automatically inside an already-confirmed destroy flow: if Terraform destroy fails before acquiring the backend lock and the existing local-owner safety checks pass, the CLI clears the stale lock and retries destroy once instead of making you run a separate unlock command first.
- `terraform unlock` requires `aws` CLI in `PATH`. Terraform itself can come from `PATH` or from the managed Terraform download path.
- Report artifacts are local-only outputs under `generated/reports`; they are not uploaded to Object Storage by the CLI.

Wizard field behavior:

- Infra input field names are discovered dynamically from Terraform module variables (required and optional).
- Interactive `create` and `component add` offer all discoverable required and optional component fields for newly selected components.
- When no `--app` values are provided, interactive `create` opens app chart selection only after an MK8s target is selected. Explicit app selections still run normal target validation, so Helm charts cannot be added by `create` without a managed MK8s target. Existing external Nebius MK8s targets are registered later with `nebius-cxcli ext-soperator onboard <config.yaml-or-deployments-root>`.
- Infra component field phases default to `y`; app chart field phases default to `n`, because chart overrides are usually optional and Helm/chart defaults still apply unless you choose to edit them.
- App chart version prompts keep the active `component_sources.yaml` pin as the
  default and run before the full app field phase in `create` and
  `component add`. Operators can type a known published chart version, then
  answer `n` to the longer app config prompt when no other app overrides are
  needed, or pass `--app-version <app-id>=<chart-version>` in non-interactive
  mode. With source validation enabled, cxcli fails before writing `config.yaml`
  if that requested version cannot be resolved.
- Optional-wizard controls are consistent across component selection, component phase prompts, and field prompts: `q` backs up to the previous step so you can revise an earlier answer, while `qq` stops the wizard immediately and saves the current config state. Interactive TTY list and checkbox prompts bind those controls directly to the keys instead of showing Back/Quit as selectable rows. When a field has a constrained choice list, the TTY wizard shows only selectable values, plus an explicit skip row for optional unset fields; the non-TTY fallback accepts only a listed index or exact value. Manual free text is reserved for fields without resolved choices, except required VPC network/subnet fields which fail fast when live lookup is unavailable.
- Interactive component selection prints one resolved infra/apps summary after dependency resolution finishes. During field input, the wizard context is a one-line Rich-colored `Wizard context: Current: <scope> / <component-or-target-feature>` marker, so long app lists are not repeated before every prompt. Fields under `deploy.targets[]`, such as native MysteryBox ESO sync, are labeled as deploy-target context rather than ordinary MK8s Terraform inputs.
- Interactive `component add` uses that target-aware summary as the component report and does not repeat final `Added infra/apps components` lines after writing `config.yaml`; non-interactive adds still print compact added-component summaries for categories that actually changed because they do not run the wizard summary.
- Required fields are prompted first, are labeled `required`, and must receive a valid value before the wizard advances unless the operator backs out or stops the wizard.
- Optional fields are labeled `optional`; pressing Enter keeps the current/default value and leaves the field unset in `config.yaml` when the value is still only a virtual default.
- Prompt labels include Terraform input type hints (for example `string`, `number`, `bool`) plus `required` or `optional`.
- Simple string lists (`list(string)` / `set(string)`) are entered as comma-separated values such as `ns1,ns2`. Other collection/object Terraform inputs (`list(object(...))`, `map(...)`, `object(...)`, `tuple(...)`) are entered as single-line YAML/JSON values in the wizard, except for intentionally guided product flows such as MysteryBox `inputs.secrets`.
- Terraform module defaults and Helm chart defaults can be shown as prompt defaults without being copied into `config.yaml`; they remain virtual until the operator explicitly overrides them. A declared `wizard.<field>` spec can opt into `write_default_to_config: true` when accepting the default is itself a real config choice, such as enabling native MysteryBox ESO sync.
- Literal defaults from `component_sources.yaml` are still shown in the wizard as editable current values instead of being hidden once pre-seeded into the component block.
- Declared `component_sources.yaml` `wizard` paths under `inputs.*` or `values.*` remain valid even when the target key is not yet materialized in the current payload; the wizard now prompts those fields directly instead of printing a spurious “path not found in config payload” warning.
- `wizard.<field>.prompt: false` suppresses optional fields from the interactive wizard while leaving the field in the underlying Terraform/Helm contract for manual config editing.
- Empty optional YAML/JSON defaults such as `{}` and `[]` are rendered as blank-input prompts with explicit “blank keeps current empty map/list” guidance instead of awkward literal default tokens.
- Empty top-level app `values: {}` blocks no longer trigger a generic whole-map prompt; the wizard only prompts concrete chart value leaves that already exist, come from chart defaults, or are declared explicitly in `wizard`.
- Multiline Terraform defaults discovered from module `variables.tf` files, including map/object defaults, are parsed as full values in wizard mode instead of being truncated to the first line.
- Source-backed infra `inputs.parent_id`/`inputs.project_id` default to `client_info.nebius.project_id` when those variables exist.
- `component_sources.yaml` can declare top-level `shared` values and shared-derived `defaults` so components read shared values from the active source catalog instead of duplicating them under component `inputs` or chart `values`.
- The bundled `mk8s` catalog entry defaults `inputs.cluster.public_endpoint: true`, and the built-in MK8s cluster handoff derives access dynamically from that input. If you switch the control plane to private-only, local app operations still work, but only from a machine that already has private network reachability to the MK8s API endpoint.
- The bundled `mk8s` catalog entry also defaults `inputs.cluster.kube_network.service_cidrs: ["/20"]`. Nebius treats an omitted MK8s service CIDR as `["/16"]`; on a single-pool `/16` subnet that can consume the whole pool and leave no address space for control-plane allocations, which looks like a long `PROVISIONING` stall.
- The bundled `mk8s` catalog entry now uses `wizard_profile: mk8s`, which wires typed cluster fields to live VPC/subnet/version providers and then opens a node-group loop that writes concrete `inputs.node_groups.<name>` entries. The loop prompts for each node group's name, autoscaling or fixed size, CPU/GPU resource type, preemptible flag, platform, GPU reservation policy when relevant, GPU preset row, materialized GPU cluster fabric when that row is cluster-capable, Capacity Block Group IDs when relevant, OS, boot disk, SFS attachments, SSH, and service account settings. Node groups default to no service account assignment; operators can instead use an existing service account ID or create one by name. For a plain MK8s-only target, the default concrete CPU baseline group name is `system`.
- In the plain MK8s node-group loop, platform, preset, GPU stack, OS, fabric, reservation, and boot-disk choices are resolved from live Nebius-backed providers when tenant/project/region context is available. GPU stack choices are constrained by the selected platform, Kubernetes version, and selected/defaulted OS when present, so a stale catalog preference is not kept when the live compatibility matrix excludes that tuple. A single compatible OS from the MK8s compatibility matrix is materialized without asking a redundant OS question; multiple compatible OS values still prompt. For Nebius-image GPU node groups, `gpu_stack_preset` compatibility is OS-specific, so deploy preflight requires an explicit `os` instead of letting Terraform/provider defaults choose one implicitly. Boot-disk type defaults to the shared compute boot-disk policy, currently `NETWORK_SSD`, and boot-disk size is recommended from the selected platform/preset instead of a fixed value. The SSH toggle defaults to enabled, but cxcli writes an `ssh` block only when the operator accepts that prompt and provides a public key. Pressing `q` while drafting a node group restarts that draft group instead of jumping out to the add-another prompt.
- The bundled `mk8s` wizard keeps preemptible, reservation, service-account, SSH, and SFS decisions on each typed node-group entry. Advanced MK8s node-group fields such as public IPs, taints, and arbitrary labels remain manual `config.yaml` fields or profile materialization output. `inputs.node_group_defaults.*` is kept only as a profile helper for flows such as Soperator `production-cluster`, where those helper values are copied into real `inputs.node_groups` and `inputs.gpu_clusters`; plain MK8s-only configs should not persist that helper block after create, component add, or runtime normalization.
- The bundled `mk8s` flow now exposes source-driven observability controls under `deploy.targets[].observability.*`. When enabled, cxcli auto-enables the bundled `nebius-observability-agent` chart for that target, persists the target-facing logs/metrics/traces toggles in `config.yaml`, and keeps the auth path public-safe by relying on the chart's metadata/IAM token-file default instead of asking operators to paste secrets into repo config. In wizard mode, MK8s Terraform inputs are completed before target-scoped deploy settings such as observability and GPU deployment-testing prompts are offered, and the auto-enable notice is emitted immediately after the observability answer makes the chart required; in multi-target `component add` runs, that notice and the following app phase stay scoped to exact rows such as `grafana@cluster2` rather than every existing `grafana` row. The later app prompt only controls whether to customize values or keep defaults.
- When the Terraform `mysterybox` component is selected with MK8s, the bundled `mk8s` flow exposes target-scoped `deploy.targets[].secrets.mysterybox.*` fields for native ESO MysteryBox sync. These are Kubernetes sync settings for the MK8s target, not Terraform inputs for creating MysteryBox payloads. The sync toggle defaults to `true` in this context, and accepting defaults persists `enabled: true`, `allow_all_namespaces: true`, `refresh_interval: 15m`, and `sync_namespaces: [default]` into `config.yaml`. That default is a cluster-wide store with no rendered `conditions`; it can be narrowed with `allow_all_namespaces: false`, in which case the same `sync_namespaces` list is rendered to `ClusterSecretStore.spec.conditions.namespaces`. cxcli creates one key-mapped `ExternalSecret` per declared MysteryBox Secret per sync namespace, uses `inputs.secrets[].kubernetes_secret_name` as the Kubernetes target Secret name when set, resolves Terraform-created MysteryBox secret IDs after apply, and omits `remoteRef.version` by default so ESO follows the current MysteryBox primary version. The same flow supports explicit `manual-version-pinning` from a real `version_id` and auto-enables the target's `external-secrets` chart row while keeping the `mysterybox-sa` Subject Credentials Secret runtime-only.
- The bundled `mk8s` flow exposes source-driven deployment-testing controls under the target-facing `deploy.targets[].deployment_testing.mk8s_gpu.*` contract. In wizard mode, when `inputs.node_groups` includes at least one GPU group, operators can enable or disable deploy-time operator-readiness and bounded GPU visibility checks per MK8s target, including Soperator production targets, and tune `gpu_visibility.max_nodes`. The fast MK8s node inventory smoke is not a config toggle: `render` generates it as a required deploy validation for every MK8s target so CPU-only and GPU-backed clusters both get a read-only all-node readiness/inventory report. The settings catalog owns deployment-testing defaults in `component_cli_settings.yaml` `components.infra.mk8s.cli.gpu.deployment_testing`, and the chosen per-target values persist in `config.yaml` under `deploy.targets[].deployment_testing.*` instead of pretending to be Terraform inputs. Soperator ActiveChecks remain opt-in benchmark/diagnostic workloads, not production-training defaults.
- NCCL is not configured in `config.yaml`. Operators run it explicitly with `nebius-cxcli acceptance-test benchmark --suite ...`; omitted `--suite` fails fast instead of defaulting to a K8s NCCL run. Once a suite is selected, omitting `--target` runs all generated targets, omitting `--max-nodes` uses all schedulable GPU nodes, omitting `--timeout` leaves no cxcli benchmark timeout, and the RDMA average bus-bandwidth threshold defaults to 300 Gbps. Operators can override `--suite`, `--target`, `--max-nodes`, `--timeout`, and `--average-bus-bandwidth-threshold-gbps` for that run only. On Ethernet-only and 1-GPU shapes, the benchmark remains a launch/smoke check rather than representative distributed-training performance; Slurm NCCL one-GPU node selections cap the message sweep at 2G to keep learning runs bounded. When a 1-GPU K8s or Slurm NCCL run completes and reports average bandwidth below the threshold, cxcli records the threshold result and comment in the JSON report but does not fail the benchmark.
- `validate-generated`, deploy preflight, and direct generated-bundle `terraform apply` also check Nebius-image GPU node groups against the live MK8s compatibility matrix and fail before Terraform when `platform`, `os`, and `gpu_stack_preset` do not form a supported tuple.
- That target-facing config contract intentionally chooses what to run, not where to write it. The human-readable report path is fixed at `generated/reports/deploy-report.md` so deploy output stays deterministic and generated-bundle-centric rather than adding per-target file-path knobs under `deploy:`.
- The old fake module-input path `infra.components[].inputs.gpu_validation_overrides` is no longer supported. The canonical per-target deploy-time contract is `deploy.targets[].deployment_testing.mk8s_gpu.*` only.
- The bundled NVIDIA path intentionally keeps deploy-time validation fast and scoped: operator readiness checks the operator control plane plus scheduler-visible GPUs, and GPU visibility runs a bounded CUDA sample on selected Ready GPU nodes. NCCL remains the optional explicit multi-node communication benchmark under `acceptance-test benchmark`: Ethernet-only shapes use Socket/TCPIP mode for smoke or comparative validation, while GPU-cluster / InfiniBand shapes switch to the RDMA path and become the GPUDirect-oriented performance gate. That matches NVIDIA's own split between install verification, sample GPU workload validation, and DCGM-based observability rather than a single heavy post-deploy "health checker". See [About the NVIDIA GPU Operator](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/24.9/index.html), [GPU Operator Getting Started](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/23.9.0/getting-started.html), [NVIDIA GPU Telemetry](https://docs.nvidia.com/datacenter/cloud-native/gpu-telemetry/latest/index.html), and [DCGM Diagnostics](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/dcgm-diagnostics.html).
- VM-backed `nfs` is a general MK8s RWX storage component, not a
  Soperator-only path. When an enabled `nfs` instance sets
  `inputs.kubernetes_target_ref`, cxcli binds that export to the named MK8s
  target. When there is exactly one enabled `nfs` instance, cxcli can bind the
  same export to every enabled MK8s target automatically. In both cases cxcli
  auto-enables, reports, and persists the bundled `csi-driver-nfs` app for the target.
  Terraform creates the NFS VM and export first; after Terraform outputs
  exist, deploy refreshes Flux so the NFS CSI Helm release creates a
  StorageClass named `nfs-rwx-retain` with `server`, `share`, and
  `mountOptions` sourced from the NFS module outputs. Normal workloads should
  create namespace-local RWX PVCs from that StorageClass. If multiple
  namespaces intentionally need the same files, create one static PV/PVC pair
  per namespace and point those PVs at the
  same NFS export path; Kubernetes PVCs remain namespace-local, so a single PVC
  is not the cross-namespace sharing primitive.
- The VM-backed `nfs` component is intentionally not a high-availability NFS
  service. Even when the data disk uses a replicated Nebius disk type such as
  `NETWORK_SSD_IO_M3`, the NFS daemon, guest OS, network interface, and
  exported `server_ip` remain a single VM endpoint. Use it for tests, demos,
  short-lived environments, or compatibility cases where an NFS protocol export
  is explicitly required. For production or long-lived MK8s RWX storage, prefer
  direct Nebius SFS with the [Nebius shared-filesystem CSI
  path](https://docs.nebius.com/kubernetes/storage/filesystem-over-csi)
  instead of placing an extra VM NFS gateway in front of SFS.
- The bundled `sfs` infra component uses `wizard_profile: sfs`. The wizard asks
  the single-filesystem fields (`name`, `size_gib`, `block_size_kib`,
  `mount_tag`, `forbid_deletion`, and `type`) and suppresses the raw
  `inputs.filesystems` map. `mount_tag` is the value passed through to MK8s
  filesystem attachments; if omitted, the SFS module defaults it to the
  filesystem name. When Soperator materializes the jail, controller-spool, and
  accounting SFS filesystems for a target, the single-filesystem prompts are
  skipped and the guided fields move to each generated filesystem entry:
  `name`, `size_gib`, `block_size_kib`, `mount_tag`, and `forbid_deletion`.
  The component-level `type` prompt remains the shared default for those
  generated filesystems and is asked before the generated entry fields; final
  multi-filesystem configs omit single-filesystem-only `name`, `size_gib`, and
  `mount_tag` inputs.
  The guided standalone defaults are `name=sfs`, `size_gib=1024`,
  `type=NETWORK_SSD`, `block_size_kib=4`, and `forbid_deletion=false`. The
  provider-backed filesystem type enum is
  `NETWORK_SSD`, `NETWORK_HDD`, `WEKA`, and `VAST`; Weka and VAST are advanced
  choices that still require nonzero project quota for the corresponding
  filesystem type. Nebius high-performance block-disk types such as
  `NETWORK_SSD_IO_M3` are disk types, not SFS filesystem type values.
- The bundled `soperator` app is a target-scoped Helm chart for Nebius
  Soperator self-deployment. Terraform components such as `mk8s`, `sfs`, and
  optional `nfs` create only Nebius infrastructure; the chart owns in-cluster
  CRDs, PV/PVC storage glue, mount DaemonSets, Services, `SlurmCluster`, and
  `NodeSet` resources. Selecting `apps:soperator` also seeds the required
  sibling `mk8s` and `sfs` infra components, the `cert-manager` app dependency,
  Soperator-oriented MK8s node-group labels, and SFS filesystem maps. `create`
  and `component add` print explicit adjusted-selection reasons for those
  Soperator-owned additions. The Soperator create/component wizard uses
  `production-cluster` and creates the complete MK8s+SFS+Soperator five-role
  bundle with `system` autoscaling from 3 to 5 nodes, two fixed `controller`,
  `login`, and `accounting` nodes, and one worker node by default. Existing
  Nebius MK8s clusters use the dedicated
  [Soperator Commands](#soperator-commands) workflow: `ext-soperator onboard`
  registers the external target and `ext-soperator upgrade` plans or executes
  approved source-cluster upgrade/adoption work without Terraform-importing the cluster.
  This catalog entry keeps chart ownership and value-default details; the
  command workflow, flags, upgrade rules, and managed-vs-external upgrade
  boundary live in [Soperator Commands](#soperator-commands). If an enabled
  sibling `nfs` component matches the
  target, cxcli projects its Terraform `server_ip` and `export_path` outputs
  into `values.externalNfs`. The chart defaults to structured Slurm partitions,
  chart-managed MariaDB accounting, and Slurm REST so worker NodeSets are
  registered before worker pods undrain themselves. It also mounts
  generated Slurm scripts into worker NodeSets and points Slurm at the plugin
  directory used by the selected chart images, which keeps basic `srun` jobs
  runnable from the default chart values. For GPU NodeSets, the chart derives
  Slurm `Gres=gpu:<count>` from `slurmd.resources.gpu`, so the profile does not
  duplicate GPU counts but GPU partitions still accept `--gres=gpu:*` jobs. When
  a selected GPU preset has fewer vCPUs than the profile template's static
  Slurm topology, cxcli downsizes generated `nodeConfig.static` to fit that
  Kubernetes worker host. For Soperator cert-manager and MariaDB webhook chart
  values, cxcli treats
  generated YAML `null` booleans as unset before render so optional wizard skips
  do not override chart defaults; explicit `false` values remain real overrides.
  Other Helm values, including intentional `null` overrides, are preserved.
  The app field wizard defaults to `n`; before that prompt, cxcli prints up to
  four concise lines of the app chart defaults that will be kept if the phase is
  skipped. For Soperator, that preview includes release/profile basics,
  cluster/partition defaults, and SFS-derived jail, controller-spool, and
  accounting volume sizes. The SFS infra row remains the capacity source of
  truth; Soperator mirrors those values into Helm `values.volume.*`,
  `values.sfs.filesystems.*`, and chart-managed MariaDB storage because the
  chart renders the PV/PVC and mount wiring. Accepting `n` keeps the production
  GPU layout from the catalog:
  `system`, `controller`, `login`, and `accounting` CPU role groups, the GPU
  `worker` NodeSet, SFS jail/controller-spool/accounting filesystems, Slurm
  accounting, SlurmDBD, and chart-managed MariaDB enabled with storage bound to
  the accounting SFS-backed `slurm-local-pv` class. ActiveChecks,
  checks controller, Soperator DCGM job mapping, notifier, backup, QoS
  reconciliation, SSSD, and NodeConfigurator rebooter disabled. Answering
  `y` opens only the guided app surface: profile, partition profile, topology
  profile, and selected intent-level optional-service gates. Raw chart
  internals remain editable in `config.yaml` rather than being prompted field
  by field.
  For GPU MK8s targets, cxcli's existing
  MK8s GPU policy auto-enables NVIDIA GPU Operator and keeps the GPU Operator
  DCGM Exporter path authoritative; GPU
  fabric / InfiniBand shapes still auto-enable Network Operator through the
  existing GPU policy. The `values.soperator-dcgm-exporter.enabled` child
  chart provides Slurm per-job DCGM labels when the default GPU Operator DCGM
  metrics are not enough.
- The optional `values.soperator-notifier.enabled` child chart installs the
  Soperator Slack job notifier from the parent `soperator` Helm release.
  The chart expects VictoriaMetrics Operator CRDs to already exist because it
  renders `VMAlertmanagerConfig`, `VMAlertmanager`, `VMRule`, and `VMAlert`.
  Slack delivery uses a Slack App incoming webhook, following Slack's
  [incoming webhook guide](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/).
  The webhook URL is sensitive and must not be stored in `config.yaml`, Helm
  values, generated manifests, or Git. cxcli supports two webhook sources:
  `values.soperator-notifier.slack.webhookSource` set to `mysterybox` for a
  no-action deploy path, or `deploy-time` for hidden deploy-time input. With `mysterybox`,
  set `values.soperator-notifier.slack.mysterybox.secretId` to the existing
  Nebius MysteryBox Secret ID (`mbsec-...`) whose primary version contains the
  webhook URL at property `url`; cxcli enables target MysteryBox ESO sync,
  renders an `ExternalSecret` in the Soperator namespace, omits
  `remoteRef.version` so ESO follows the primary version, and lets ESO create
  the Secret referenced by `values.soperator-notifier.slack.existingSecret` /
  `values.soperator-notifier.slack.existingSecretKey`. This also auto-selects
  the target `external-secrets` app; no separate component action is required.
  With `deploy-time`,
  provide the URL during `deploy` through
  `NEBIUS_CXCLI_SOPERATOR_SLACK_WEBHOOK_URL_<TARGET>` for target-scoped
  Soperator rows, or the bare `NEBIUS_CXCLI_SOPERATOR_SLACK_WEBHOOK_URL` only
  for an unscoped row. An interactive hidden prompt or a precreated Kubernetes
  Secret can also satisfy the runtime secret; cxcli writes only the runtime
  Kubernetes Secret.
- Optional Soperator child charts are catalog-owned under `apps:soperator`.
  The production profile leaves `values.soperator-checks.enabled`,
  `values.soperator-activechecks.enabled`,
  `values.soperator-activechecks.waitForChecks.enabled`, and
  `values.soperator-dcgm-exporter.enabled` disabled by default. This is the
  production-training best practice: ActiveChecks are recurring Slurm CUDA,
  NCCL, GPU stress, RDMA, and maintenance workloads, so they are appropriate for
  benchmark/diagnostic clusters or maintenance windows, not production training
  clusters. When an operator intentionally enables
  `values.soperator-activechecks.enabled`, cxcli enables the `soperator-checks`
  controller, derives the target Slurm cluster name and login-node count from
  the matching `soperator` row, and still keeps expensive `runAfterCreation`
  jobs disabled unless explicitly requested. cxcli derives the ActiveChecks
  readiness partition from the selected Soperator profile at render time; the
  readiness partition and any internal hidden partition are profile plumbing,
  not wizard fields or source-config knobs. For Soperator targets, the create
  wizard still asks the cxcli-owned MK8s GPU deployment-testing settings for
  operator readiness and bounded GPU visibility. `nccl-test` declares
  `usage.lifecycle: transient` rather than behaving as a selectable app, and
  Soperator ActiveChecks stay disabled unless an operator explicitly chooses
  Slurm-level diagnostics. Run K8s NCCL only through
  `nebius-cxcli acceptance-test benchmark`; if Soperator NCCL ActiveChecks and
  the cxcli K8s NCCL benchmark are both runnable, schedule them deliberately
  because the Slurm NCCL checks and transient Kubernetes `MPIJob` can compete
  for GPUs and RDMA bandwidth.
  The
  `values.soperator-backup-config.enabled` switch installs the parent chart's
  optional K8up dependency plus the jail backup schedule backed by Nebius
  Object Storage and runtime-only Kubernetes Secrets. K8up runs inside the
  Soperator Helm release namespace, not as a separate cxcli app release.
  Baseline Soperator installs leave notifier, backup, and K8up disabled.
  Provide backup credentials at deploy time with
  `NEBIUS_CXCLI_SOPERATOR_BACKUP_AWS_ACCESS_KEY_ID_<TARGET>`,
  `NEBIUS_CXCLI_SOPERATOR_BACKUP_AWS_SECRET_ACCESS_KEY_<TARGET>`, and
  `NEBIUS_CXCLI_SOPERATOR_BACKUP_REPOSITORY_PASSWORD_<TARGET>` for
  target-scoped Soperator rows, or the unsuffixed names only for an unscoped
  row.
  `values.soperator-dcgm-exporter.enabled` remains opt-in and provides the
  Soperator Slurm job-mapping DCGM path only when the default GPU Operator DCGM
  metrics plus Nebius Observability Agent are not enough. Avoid enabling both
  DCGM exporters for the same signal unless duplicate scraping is deliberately
  handled. CPU-only or otherwise no-GPU Soperator profiles force
  `values.soperator-dcgm-exporter.enabled=false` because the exporter has no
  GPU job-mapping signal to scrape. In-cluster Soperator NFS is not exposed;
  production shared storage should use Nebius SFS, while the Terraform-owned VM
  NFS component remains a
  non-HA compatibility bridge for explicit NFS cases.
  In short, production training should keep
  `values.soperator-activechecks.enabled=false`,
  `values.soperator-activechecks.waitForChecks.enabled=false`,
  `values.soperator-checks.enabled=false`,
  `values.soperator-dcgm-exporter.enabled=false`,
  `values.soperator-notifier.enabled=false`, and
  `values.soperator-backup-config.enabled=false`; enable these only for an
  explicit benchmark, diagnostic, maintenance, notification, backup, or
  per-job DCGM-labeling workflow.
  Slurm accounting, SlurmDBD, and the chart-managed accounting database stay
  enabled for `production-cluster`; the partition profile does not toggle the
  accounting database. The partition profile is the guided scheduling policy:
  `shape-default` keeps the default visible worker partition for the selected
  shape with no QoS/preemption. `with-debug-long` adds short/long policy queues
  on the same capacity without QoS objects, and `with-qos-preemption` adds
  QOS/fairshare/preemption policy queues that require SlurmDBD accounts, QOS
  objects, associations, and QOS preemption relationships. The wizard only asks
  `values.qosConfiguration.enabled` for QoS-capable partition profiles. cxcli
  rejects `with-qos-preemption` unless that toggle is enabled and the rendered
  `qosConfiguration.qos` list covers every partition `AllowQos` value; this
  avoids deploying a Slurm controller that crashes before matching QOS objects
  exist. The reconciliation hook uses `alpine/k8s:1.33.5` for Bash plus
  kubectl and streams `sacctmgr` work into the accounting pod with
  `kubectl exec -i`. SSSD is disabled by default; keep the guided
  `values.sssd.enabled=false` identity gate off unless Slurm login/worker pods
  should use an existing LDAP/IdP identity configuration from SSSD Secrets.
  When that gate is enabled, cxcli materializes both
  `values.slurmNodes.sssd.enabled=true` and generated
  `values.nodesets[].sssd.enabled=true`; direct chart values remain available
  for advanced `config.yaml` edits when the guided helper is absent.
  `values.rebooter.enabled=false` is also the
  default; the normal wizard does not prompt this raw host-maintenance helper.
  Enable it only by deliberate `config.yaml` edit when the cluster should allow
  Soperator-managed Kubernetes worker-node drain/handoff or reboot maintenance.
  Enabling it turns on the NodeConfigurator reboot helper and RBAC. It is not a
  per-NodeSet switch, does not reboot nodes at install time, does not create a
  reboot schedule by itself, and is not a Slurm job restart. cxcli may mirror
  worker NodeSet tolerations onto the helper so it can run on tainted worker
  hosts, but the helper acts only after a runtime maintenance flow sets the
  node's `SlurmNodeDrain` or `SlurmNodeReboot` condition. In upstream Soperator
  3.0.5,
  drain is implemented by cordoning the node, adding a `NoExecute` taint, and
  waiting for non-DaemonSet pods without matching tolerations to leave. The
  chart still keeps a no-op NodeConfigurator custom container enabled so
  host-setup initContainers can run safely while the rebooter is off. The wizard
  asks the child chart and service `enabled` gates first and then only curated
  nested fields for enabled features; advanced child values and the rebooter
  gate remain direct `config.yaml` edits under the same `apps:soperator` row.
  Example condition flow: an external node maintenance signal such as
  `NebiusMaintenanceScheduled=True` causes Soperator checks to drain the Slurm
  workers on that Kubernetes node, set `SoperatorChecksNodeMaintenance=True`,
  and then set `SlurmNodeDrain=True` for the rebooter. A reboot flow is similar:
  a degraded Slurm node reason such as `Kill task failed` or
  `[compute_maintenance] node reboot process` sets
  `SoperatorChecksNodeDegraded=True`, which is converted into
  `SlurmNodeReboot=True`.
  Advanced production-maintenance mode is for operators who intentionally enable
  both `values.soperator-checks.enabled=true` and
  `values.rebooter.enabled=true` outside the normal wizard. It has two distinct
  intents. `NebiusMaintenanceScheduled=True` is a graceful maintenance
  drain/node handoff signal: Soperator drains Slurm workers, the rebooter
  cordons and `NoExecute`-drains Kubernetes pods, and the checks controller can
  hand the node back to the maintenance platform by deleting the Kubernetes Node
  object. It does not call the host `reboot now` path by itself. For an actual
  Soperator host reboot after drain, use the `SlurmNodeReboot=True` path. That
  path is normally produced by the Soperator degraded-node flow after Slurm has
  a reboot/degraded reason; if an external tool sets `SlurmNodeReboot=True`
  directly, it must first ensure Slurm workloads are already drained.
- On the actual GPU-cluster / InfiniBand path, the bundled catalog now makes pod-facing RDMA exposure explicit on both supported host-stack modes instead of assuming the Network Operator chart default CR is enough. For `gpu_stack_source: nebius_image`, GPU Operator still leaves the host GPU driver and NVIDIA Container Toolkit runtime untouched while Network Operator keeps OFED disabled and post-patches `NicClusterPolicy` so driverful InfiniBand nodes expose `rdma/shared_device`. For `gpu_stack_source: operator_managed`, Network Operator still owns OFED on the host and now gets the same explicit `rdma/shared_device` policy so operator-managed InfiniBand nodes meet the same scheduler-visible RDMA contract.
- Deploy-time GPU deployment testing is intentionally layered and non-duplicative.
  `operator_readiness` is the cheapest control-plane gate and never launches a
  workload. `gpu_visibility` is the bounded CUDA sample probe that proves a
  real GPU pod can run during deploy. NCCL is the expensive
  distributed-communication benchmark: Socket/TCPIP on Ethernet-only shapes,
  RDMA on GPU-cluster / InfiniBand shapes. It runs only through explicit
  `acceptance-test benchmark` commands, so routine deploy failures stop early
  instead of paying the full NCCL cost.
- When the wizard enables `gpu_visibility`, it also persists
  `gpu_visibility.max_nodes` instead of leaving the cap unset. It remains
  editable in `config.yaml`, but an enabled deploy workload check always has an
  explicit bound.
- In operator-facing output and the combined deploy report, that first gate is now labeled `GPU stack readiness` because it covers GPU Operator plus Network Operator / `NicClusterPolicy` when the actual GPU-cluster / InfiniBand path requires the network stack.
- `GPU stack readiness` already scans every Ready GPU node in the cluster; it is not sampled by `max_nodes`. That keeps the control-plane gate cheap even on large clusters, but it still does not launch a workload, so it is not proof that every node can run CUDA successfully.
- Deploy workload validations use scheduler-free GPUs for the bounded CUDA
  smoke path. Explicit `acceptance-test benchmark` NCCL runs also require
  scheduler-free GPUs; if Soperator worker pods or any other workload reserves
  every Ready GPU before or during NCCL startup, cxcli records NCCL as skipped
  in `acceptance-benchmark-report-<target>.json` instead of failing deploy
  because the transient benchmark job was preempted. When Soperator NCCL
  ActiveChecks and the cxcli K8s NCCL benchmark are both runnable for the same
  MK8s target, cxcli also emits a warning because the Slurm NCCL checks and
  Kubernetes NCCL `MPIJob` can compete for GPUs and RDMA bandwidth.
- For Soperator targets, deploy-time Soperator testing is deliberately fast:
  it records a Kubernetes snapshot of the Soperator manager, jail storage,
  Pending pods/events, SlurmCluster, and NodeSets,
  then writes `deploy-smoke-report-<target>.json`. Exhaustive all-node Slurm
  hostname/GPU smoke moves to
  `nebius-cxcli acceptance-test smoke ... --suite slurm` and writes
  `acceptance-smoke-report-<target>.json`. Soperator JSON detail reports use
  the `nebius-cxcli-soperator-cluster-validation/v2` schema and keep command
  `stdout`/`stderr` as arrays of lines. Acceptance GPU allocation node entries
  record whether the Slurm job proved GPU visibility through `nvidia-smi` or
  through NVIDIA proc-driver plus `/dev/nvidia*` device evidence when the jail
  exposes an unusable `nvidia-smi` stub. Slurm-side NCCL is a
  benchmark-only login-pod-driven Slurm allocation that runs
  `mpirun /usr/bin/all_reduce_perf_mpi`; it belongs to explicit
  `nebius-cxcli acceptance-test benchmark ... --suite slurm-nccl` runs and writes
  `acceptance-benchmark-report-<target>.json`.
- Validation cleanup is intentionally split by resource type: cxcli keeps dedicated validation namespaces such as `gpu-validation` and `nccl-test` for isolation and easy reruns, but deletes transient validation pods, transient NCCL `MPIJob` resources, and any transient Training Operator install after each run so finished workload objects do not accumulate in the cluster. CUDA smoke pods use the cxcli-owned `cuda-smoke-validation` ServiceAccount with token automount disabled, so a fresh validation namespace does not depend on the Kubernetes-created `default` ServiceAccount before pod admission.
- On `gpu_stack_source: nebius_image`, Network Operator remains auto-enabled only when the selected MK8s platform/preset is cluster-capable in the live Nebius inventory and the config actually sets a referenced `inputs.gpu_clusters` fabric. The plain MK8s wizard materializes that fabric from the selected live cluster-capable Capacity Dashboard row so the common InfiniBand path does not require a raw fabric prompt. That follows Nebius guidance that Network Operator is optional outside those cases, but operators may still enable the chart manually if they want its CRD-managed networking features; cxcli keeps `operator.ofedDriver.deploy=false` on the driverful path so that optional install does not try to reinstall host OFED.
- The NCCL threshold compares NCCL's `average bus bandwidth` metric, not a raw NIC or switch-port speed. On single-node runs it reflects the effective GPU-to-GPU communication path inside that node, such as NVLink, NVSwitch, or PCIe. On multi-node runs it reflects the normalized end-to-end collective communication path across the full topology, including both intra-node GPU links and the inter-node network, so it should not be read as a one-to-one `400 Gbps` or `800 Gbps` InfiniBand switch-speed number.
- `deploy.targets[].deployment_testing.mk8s_gpu.health_checker.enabled` is not a built-in runner. It is reserved for a custom catalog app with `cli.mk8s_gpu_policy.role: health_checker`. In the bundled catalog there is no such app, so the wizard hides that toggle and cxcli omits it from persisted target defaults unless an active catalog actually supplies one.
- If the selected infra path implies a required app, the same wizard pass now auto-enables that app row before the app phase starts, so `mysterybox` plus MK8s exposes `external-secrets`, and GPU-enabled MK8s exposes `nvidia-gpu-operator` / `nvidia-network-operator`, for review in `create` or `component add` instead of only appearing later in the final `config.yaml`.
- MK8s operator readiness is no longer tied to manual `nvidia.com/gpu.deploy.*` node labels. cxcli now uses a hybrid live check: `ClusterPolicy` and `NicClusterPolicy` are the fast control-plane signals, GPU readiness still requires allocatable `nvidia.com/gpu` on Ready nodes, and the actual GPU-cluster / InfiniBand path also requires those Ready GPU nodes to advertise scheduler-visible RDMA-style allocatable resources such as `rdma/shared_device`. The saved report now records `NicClusterPolicy.status.appliedStates` plus daemonset rollout details instead of treating a green control plane alone as proof that pod-facing RDMA is ready. If GPU Operator condition text is stale or conservative, for example a `NoGPUNodes` reason, allocatable GPUs on Ready nodes remain the data-plane signal cxcli uses.
- The bundled MK8s flow treats typed node-group prerequisites as conditionally required: each enabled group must provide `platform` and `preset`, and either `node_count` or enabled `autoscaling`.
- Soperator uses the catalog-owned `soperator_nodesets_profile`
  (`nebius-gpu-v1` by default) to seed typed MK8s node groups instead of
  hardcoded Python dictionaries. Built-in profile choices are `nebius-cpu-v1`,
  `nebius-gpu-v1`, and `nebius-mixed-v1`. `nebius-cpu-v1` maps the Slurm
  worker role only to `worker-cpu`, keeps
  `system`, `controller`, `login`, and `accounting` as service groups outside
  the CPU partition, and disables GPU-only child features such as the Soperator DCGM
  exporter when no GPU node groups exist. The app row carries an explicit
  `install_mode`: `production-cluster` materializes the complete production
  path with five logical host groups `system`, `controller`, `login`,
  `accounting`, and `worker`, plus SFS jail/controller-spool/accounting
  filesystems. Adding `apps:soperator` in `production-cluster` mode to an
  existing managed MK8s target with non-empty `inputs.node_groups` now fails
  fast unless the target already has the required Soperator service-role groups
  or the app row provides an explicit complete `apps.charts[].placements` map.
  `onboard-existing-cluster` is written by
  `nebius-cxcli ext-soperator onboard <config.yaml-or-deployments-root>` for an
  external Nebius MK8s target; it writes `apps.charts[].placements` from
  `deploy.targets[].inventory.node_groups` and the selected profile instead of
  creating Terraform-managed node groups. Render then compiles those placements
  into Soperator chart-native filters and NodeSets. The operational onboarding, upgrade,
  and managed-vs-external upgrade rules live in
  [Soperator Commands](#soperator-commands).
  The profile also applies onboarding-only service sizing for the login pod so
  small external CPU pools can pass first install; production-cluster mode keeps
  the chart's production resource defaults, and operators can still override
  the rendered Helm values in `config.yaml`. Fresh production-cluster profiles
  use `cpu-d3/32vcpu-128gb` for catalog-owned CPU role groups and taint the
  login group with `slurm.nebius.ai/nodeset-name=login:NoSchedule` so the
  Soperator login pod has enough dedicated capacity instead of competing with
  generic cluster workloads. The production wizard now exposes curated service-role
  node count helpers under `inputs.soperator.system_node_count`,
  `controller_node_count`, `login_node_count`, and `accounting_node_count`,
  plus service-role autoscaling helpers under
  `inputs.soperator.<role>_autoscaling.*` for `system`, `controller`, `login`,
  and `accounting`. Production profiles default the `system` role to
  autoscaling from 3 to 5 nodes. `controller`, `login`, and `accounting`
  default to two fixed nodes each, and worker capacity defaults to one node.
  Other service-role autoscaling helpers are disabled by default. When one is
  enabled, cxcli materializes the matching concrete
  `inputs.node_groups.*.autoscaling` block and omits `node_count`; otherwise it
  writes the fixed count. Worker autoscaling is controlled per generated shard
  through `inputs.soperator.worker_node_groups.<worker>.autoscaling`. Raw
  profile-owned `inputs.node_groups.*` prompts stay hidden.
  The wizard lists the target's node groups for each role so the operator can
  override the proposed mapping, then cxcli renders role filters, worker
  NodeSets, storage selectors, partition refs, and NodeConfigurator rebooter
  tolerations from that one map; taints on the selected MK8s groups are
  converted into the required Soperator tolerations for those rendered
  selectors. Onboarding also caps generated worker
  NodeSet CPU and memory requests from live node allocatable data when the
  catalog template would otherwise request more than Kubernetes can schedule on
  the discovered external nodes, and uses that allocatable CPU shape for
  generated CPU worker Slurm topology. The mixed profile creates separate
  homogeneous Slurm worker NodeSets, `worker-cpu` and `worker-gpu`, then maps
  Slurm partitions to those NodeSets. The app wizard also exposes
  catalog-derived `values.partitionProfile` and `values.topologyProfile`
  choices for the selected worker profile. Those profiles fill or replace
  catalog-owned defaults only; repeated create/render materialization preserves
  explicit operator edits to the same value paths. Profile-owned NodeSet values
  intentionally leave worker `slurmd` and `munge` image selection to the
  selected Soperator chart defaults, so bumping the app source version does not
  require duplicating worker image tags in `component_cli_settings.yaml`. The
  production profiles seed a catalog-owned CPU shape for `system`,
  `controller`, `login`, `accounting`, and CPU worker node groups so every
  Terraform `node_groups` entry has the required `platform` and `preset` fields
  before render. The opt-in
  `with-qos-preemption` partition profile writes persistent Slurm
  `PreemptType=preempt/qos` config plus `debug`, `eval`, `train`, and `data`
  policy partitions and standard QOS object definitions. The bundled profile
  also includes a root account/association so live smoke tests can submit into
  those queues immediately; production configs should add the real
  project/user accounts and associations in `config.yaml`.
  Slurm topology remains disabled by default for clusters without verified
  topology labels.
  Selecting `nebius-tiered-tree-v1` explicitly enables `topology/tree` with
  `topology.nebius.com/tier-*` node-label discovery for production clusters
  that expose those labels; selecting `nebius-nvl-rack-v1` enables
  rack-scoped `topology/block` with `topology.nvidia.com/rack` label discovery
  for GB300/NVL clusters. Slurm
  features such as `h100`, `a100`, `highmem`, or `infiniband` remain NodeSet
  `nodeConfig.features`, not partition fields. The default production worker
  count is one host per active worker shape, with 100 nodes per host-pool shard:
  CPU workers use `soperator.worker_cpu_total_nodes` and
  `soperator.worker_cpu_nodes_per_group`, GPU workers use
  `soperator.worker_gpu_total_nodes` and
  `soperator.worker_gpu_nodes_per_group`, and the mixed profile uses both.
  Each `worker_*_total_nodes` value is a Kubernetes worker host count for that
  shape, not total GPU count and not an aggregate CPU/GPU split. cxcli maps it
  to matching worker NodeSet replicas, while GPU count per host comes from the
  selected preset and is written to `slurmd.resources.gpu`. Each
  `worker_*_nodes_per_group` value must be less than or equal to the selected
  profile's per-group limit; Nebius production profiles cap worker shards at
  100 MK8s nodes per generated group. For example, 5 x
  `1gpu-*` hosts means five Slurm worker replicas with `gpu: 1`, while 5 x
  `8gpu-*` hosts means five replicas with `gpu: 8` and 40 total GPUs. cxcli
  writes and refreshes `inputs.soperator.worker_node_groups` for each generated
  worker shard, such as `worker-cpu-0` or `worker-gpu-2`. Each shard has
  canonical `autoscaling` and `ephemeral_nodes` controls. During `create`, the
  wizard uses `autoscaling.enabled` as the per-shard Infra/MK8s worker
  autoscaling toggle: answering `true` writes same-shard
  `ephemeral_nodes.enabled=true` and asks min/max, with max defaulting to that
  shard's generated capacity, while answering `false` clears same-shard
  autoscaling bounds and writes `ephemeral_nodes.enabled=false`. When more than
  one generated worker shard exists, the wizard first offers a synthetic
  bulk apply-to-all choice for all CPU worker shards, all GPU worker shards, or
  all worker shards in mixed CPU+GPU layouts. The visible mixed-layout helper is
  shortened to `all_worker_shards_apply_to_all` and defaults to `true`;
  accepting it asks one `autoscaling.enabled` prompt and writes only canonical
  per-shard controls, while declining keeps the per-shard prompts. No bulk key
  is saved. The wizard asks the global
  suspend-time value only after at least one shard has autoscaling-backed
  ephemeral nodes enabled. In hand-authored config, when
  `inputs.soperator.worker_node_groups.<worker>.autoscaling.enabled=true`, that
  shard renders Kubernetes autoscaling min/max values instead of fixed
  `node_count`; `max_node_count` cannot exceed the shard capacity, and NodeSet
  replicas reflect that shard's max, including an explicit `0..0`
  scale-to-zero worker range. By itself this remains maximum-capacity
  materialization, not Slurm-demand worker elasticity. To enable Slurm-demand
  workers for one shard, set
  `inputs.soperator.worker_node_groups.<worker>.ephemeral_nodes.enabled=true`;
  cxcli then requires autoscaling on that same shard, renders
  `nodesets[].ephemeralNodes: true`, derives `initialNumberEphemeralNodes` from
  that shard's autoscaling `min_node_count` for CPU workers, raises GPU worker
  shards to at least one initial active worker when max capacity is positive so
  Soperator can seed GPU libraries into the jail, and writes finite non-negative
  `slurmConfig.suspendTime` from the global
  `inputs.soperator.worker_ephemeral_nodes.suspend_time_seconds`.
  `initialNumberEphemeralNodes` is only the initial active Slurm worker pods; day-2
  active-node changes, including suspending that bootstrap GPU worker, happen
  through Slurm power control and Soperator
  `NodeSetPowerState`.
  CPU service-role counts are independent of worker sharding: `system` defaults
  to autoscaling 3..5, and if that helper is disabled it falls back to fixed
  count 3; `controller`, `login`, and `accounting` default to fixed count 2
  unless their autoscaling helpers are enabled. Service-role autoscaling must
  keep `max_node_count` at least `1`.
  NFS stays a VM-based sibling infra component, not an MK8s node
  group.
- The five-role Nebius Soperator production shape and Slurm topology are separate concerns. The five role groups provide workload isolation and placement for `system`, `controller`, `login`, `accounting`, and `worker`. Slurm topology is an additional worker-placement optimization for distributed jobs that care about physical or fabric locality, especially multi-node GPU/NCCL jobs.
- For a fresh Nebius production MK8s plus Soperator deployment, use `values.topologyProfile: nebius-tiered-tree-v1` when the same provisioning flow prepares accurate `topology.nebius.com/tier-*` labels for worker nodes. Use `values.topologyProfile: nebius-nvl-rack-v1` only for GB300/NVL clusters whose nodes expose accurate `topology.nvidia.com/rack` labels. For already-installed Nebius MK8s clusters onboarded by cxcli, keep `values.topologyProfile: disabled` by default; operators can opt in after they have prepared and verified equivalent labels.
- Topology labels must describe real locality. Manually pre-labeling external clusters outside cxcli ownership before installing the Soperator chart is valid only when the labels are complete, stable, and reflect the actual fabric hierarchy for all worker nodes. Misleading or stale labels can make Slurm wait for a topology that does not exist, place jobs poorly, or make multi-node jobs fail when selected nodes cannot communicate as modeled. Nebius MK8s node-group metadata labels also do not update already-created Kubernetes Nodes, so existing clusters may need explicit node relabeling or node replacement before topology is safe.
- Topology can help NCCL performance indirectly by giving Slurm enough locality data to schedule distributed GPU jobs on topologically close workers. Nebius documents topology-aware AllReduce tests showing up to 20% improvement depending on cluster size, but the gain is workload and placement dependent. Treat NCCL validation as the proof, not the presence of the topology profile alone.
- Provider-backed option lists come only from explicit catalog wizard metadata, whether that metadata comes from a built-in `wizard_profile` or a raw `wizard` block, and are resolved live from Nebius APIs when available.
- Prompt-time provider lookups and strict provider-value validation now share the same argument-normalization path, so relative `depends_on` targets such as `inputs.node_groups.system.platform` resolve against the active component instance consistently in both places.
- If live provider choices are unavailable for a field, the CLI prints a field-specific warning immediately before that prompt and explains whether the next manual-input prompt is required or can be skipped with Enter.
- When a built-in resolver or provider plugin fails internally, the fallback warning now includes that resolver error text instead of silently degrading to a generic unavailable-options message.
- Optional provider-backed fields now accept blank/skip answers as “leave unset” without revalidating that blank value against the live option list.
- Provider-backed fields can now opt into `auto_select_single` or `auto_select_first`, which materialize the resolved option into `config.yaml` during `create` and `component add` while still leaving the field editable in the wizard when prompting is enabled.
- The bundled VPC network and subnet fields use `auto_select_single` across the combined live/planned choice list: when exactly one valid project VPC network or planned VPC network exists, and exactly one live or planned subnet belongs to that selected network, non-interactive `create` and `component add` can materialize either literal IDs or row-level bindings. When several choices exist, the operator must choose interactively or pass scoped `--network-id` / `--subnet-id` or `--network-ref` / `--subnet-ref` values.
- The bundled `infra:vpc` component lets the same config create a VPC network
  with optional subnets, or create subnets under `inputs.network.existing_id`.
  Workload rows bind to planned VPC outputs through
  `infra.components[].bindings`, while live networks/subnets stay literal
  `network_id` / `subnet_id` values. When `infra:vpc` is selected together
  with MK8s or VM-style infra in the interactive field wizard, cxcli configures
  the VPC row first so the planned network and subnet choices are available to
  the consuming component in the same `create` or `component add` run. During
  `render`, those row-level `inputs.*` bindings become direct Terraform module
  arguments such as `network_id` and `subnet_id` on the consuming module. The
  generated deploy report lists enabled `infra:vpc` rows as standard infra
  components and shows row-level bindings from consuming modules to planned VPC
  network and subnet outputs.
- Existing explicit subnets created from prefix allocation requests such as
  `cidr: /16` are treated by their resolved live subnet CIDR in provider choices
  and VPC networking preflight; inherited network-pool subnets still stay
  non-owning for subnet CIDR automation.
- Wizard VPC choices use the mental model “choose an existing resource, or
  choose a resource this config will create”: live project networks/subnets are
  listed beside enabled planned `infra:vpc` rows, and planned subnet choices are
  filtered to the selected live or planned network. Live project-network choices
  recommend `default-network` when it exists, and the `infra:vpc` field wizard
  keeps `Create a new VPC network` as an explicit prompt row for new-network
  creation. For that path it can select a live unassigned existing private pool
  with at least one CIDR for `inputs.network.ipv4_private_pool_ids`; pools
  already assigned to another network or subnet are hidden. If no pool is
  selected, it collects `inputs.network.ipv4_private_cidrs` before any subnet
  prompt, and it can still skip subnet creation entirely. Direct config can
  also set `inputs.network.ipv4_private_source_pool_id` when Terraform should create a
  managed private pool from an existing source pool. Direct config may set
  `inputs.network.ipv4_public_pool_ids` for explicit public pools; if omitted,
  Nebius attaches the default public pool and creates the network default route
  table. Network
  CIDR prompts suggest custom private non-default `10.x` `/13` ranges such as
  `10.8.0.0/13`, `10.16.0.0/13`, `10.32.0.0/13`, `10.40.0.0/13`, and
  `10.56.0.0/13`, plus `172.16.0.0/12` and `192.168.0.0/16`, outside
  Nebius' documented regional default private-pool ranges. Planned subnets are
  collected through name and subnet-specific private-CIDR prompts instead of a
  raw YAML/JSON map prompt. Every declared subnet uses explicit private CIDRs:
  the guided wizard accepts one or more comma-separated explicit private CIDRs,
  stores them in the module's native list form, and records
  `use_network_private_pools=false`. Public pools are inherited unless
  `use_network_public_pools` is set to `false`. Explicit subnet CIDRs
  must fit inside the selected network range, including default-network ranges
  already attached to the parent, and must not overlap other subnets or live
  private allocations in that network.
  When parent ranges are known, the prompt suggests child CIDRs from the
  selected parent private pools while avoiding known explicit subnet CIDRs and
  live private allocations.
  For a Terraform-owned new network, cxcli adds any out-of-parent
  custom subnet CIDR to `inputs.network.ipv4_private_cidrs` first so the
  parent network IP space exists before Terraform creates the explicit subnet
  child range, and the subnet prompt includes those new-parent-block
  suggestions when Terraform can manage the network. For
  `inputs.network.existing_id`, cxcli suggests child CIDRs from the attached
  parent private-pool ranges and keeps already attached RFC1918 extension
  blocks such as `172.16.0.0/12` and `192.168.0.0/16` visible as explicit
  subnet candidates when no explicit subnet CIDR or live private allocation
  overlaps them. If the operator selects or enters an out-of-parent custom
  child range, cxcli adds that CIDR to an attached private pool on the selected
  live network first, then records the subnet with explicit private pools
  (`use_network_private_pools=false`). Terraform still treats the selected
  network as externally managed. The VPC component's own
  `inputs.network.existing_id` prompt is live-only so a planned VPC row cannot
  reference itself as an existing network.
- Profile-backed MK8s GPU flows use the selected live Capacity Dashboard row as the materialization source for `inputs.gpu_clusters.<key>.infiniband_fabric` when the row is cluster-capable, so Soperator GPU creates use the same capacity-aware fabric choice without showing a raw fabric prompt.
- The bundled Soperator GPU and Mixed production profiles prompt `inputs.node_group_defaults.gpu.reservation.policy`, default it to `AUTO`, and materialize the selected value into the generated GPU worker node group's `reservation.policy`. `AUTO` lets matching reservations be used first and then falls back to suitable regular-vm capacity, `STRICT` uses only selected/suitable reservations, and `FORBID` avoids reservations. There is no global `create` flag for this policy because reservation behavior is per GPU worker/node group.
- Helm chart default values discovered from the live chart are not copied into `config.yaml`; the app wizard can show them as prompt defaults, but only explicit overrides are written back.
- Current built-in provider option sources include `mk8s_compatible_platforms` (mk8s platform fields), `mk8s_gpu_capacity_choices` (MK8s GPU preset row choices from live Capacity Dashboard advice, materialized into preset plus fabric only when the selected row is cluster-capable), `mk8s_gpu_stack_presets` and `mk8s_node_group_os_values` (mk8s image selection from the compatibility matrix, including selected-OS filtering for GPU stack presets), `mk8s_infiniband_fabrics` (hidden/backstop mk8s GPU-cluster fabric materialization gated by the selected preset's live clustering capability and sourced/ranked from live Capacity Dashboard fabric rows using the selected reservation policy when available), `capacity_block_groups` (tenant Capacity Block Groups filtered by region/platform/fabric for GPU reservations), `compute_boot_disk_types`, `compute_platforms`, `compute_platform_presets` (generic compute preset labels/ranking enriched by live Capacity Dashboard advice when tenant/region context is available), `project_subnets`, `project_networks`, `project_private_pools`, `project_private_allocations`, `project_filesystems`, `tenant_projects`, `mk8s_control_plane_versions`, `soperator_nodesets_profiles`, `soperator_partition_profiles`, `soperator_topology_profiles`, and `soperator_node_groups` for target-scoped Soperator placements.
- For GPU presets, cxcli uses live preset metadata as the source of truth for whether the interconnect is Ethernet-only or InfiniBand-capable; it does not hardcode preset-name lists. Today that matches the public Nebius Compute docs: the supported GPU-cluster path is the listed 8-GPU preset set, while single-GPU presets are the testing/dev path with no GPUDirect-RDMA.
- Plain MK8s GPU node-group loops prompt `reservation.policy` after GPU platform and before GPU preset so the live row choices and materialized fabric follow the selected policy. `AUTO` is the default, `STRICT` limits choices to reserved capacity, and `FORBID` limits choices to regular-vm capacity. Tenant Capacity Block Group IDs are still prompted later because they are matched by region, service, platform, and fabric.
- When live provider options are unavailable, optional wizard fields can still fall back to manual input. Required VPC network/subnet fields fail fast instead of accepting free text, because the selected subnet must be validated against the selected project network.

Shared-derived default example:

```yaml
# component_sources.yaml
shared:
  admin_ssh:
    user_name: ubuntu
    public_key: ~/.ssh/my_ssh_key.pub

components:
  infra:
    wireguard-gw:
      source:
        portable: git::https://github.com/example/platform-infra.git//modules/wireguard-gw?ref=v1.2.3
        local: ../../platform-infra/modules/wireguard-gw
      ui:
        enabled: false
      defaults:
        inputs.ssh_user_name: shared.admin_ssh.user_name
```

```yaml
# config.yaml
infra:
  components:
    - id: wireguard-gw
      enabled: true
      inputs:
        parent_id: project-123
        ssh_user_name: ubuntu
        ssh_public_key: ssh-ed25519 AAAA... admin@example
```

With that default, `create`/`component add` materialize `shared.admin_ssh.user_name` into `infra.components[].inputs.ssh_user_name`, so later `render` runs do not depend on the active catalog for that field. If an operator removes that field later, `validate`/`render` fail instead of silently restoring it from the catalog.
`ssh_public_key` is intentionally per-project input and should be stored only in the private project `config.yaml`, not in the shipped source catalog.
The bundled `component_sources.yaml` omits `shared.admin_ssh.public_key`; a private/customer-local catalog may still set it as a bootstrap seed when the file exists on the machine running cxcli.
If a private active `component_sources.yaml` sets `shared.admin_ssh.public_key`, `create` and `component add` accept either inline `ssh-rsa`, `ssh-ed25519`, or ECDSA content or a readable local `.pub` path such as `~/.ssh/my_ssh_key.pub`, then copy the normalized inline key into `infra.components[].inputs.ssh_public_key` for enabled infra modules that actually declare an `ssh_public_key` variable.
In interactive wizard mode, required `inputs.ssh_public_key` fields list supported public key files from `~/.ssh/*.pub`; selecting one stores the file content in `config.yaml`, not the local path.
The same normalization also applies when an operator edits `config.yaml` directly and sets `infra.components[].inputs.ssh_public_key` to a local `.pub` path: config-based commands resolve the file locally and rewrite the config back to inline key text before continuing.
If an enabled infra module declares `ssh_public_key` and neither the private catalog seed nor the project `config.yaml` provides it, `validate`/`render` fail with the missing `inputs.ssh_public_key` field.
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
        inputs.cluster.cluster_name: demo-cluster
        inputs.cluster.public_endpoint: true

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
   - `nebius-cxcli component list --config <config.yaml>`
   - `nebius-cxcli component add infra:vm --config <config.yaml>`
   - `nebius-cxcli component remove managed-postgresql --config <config.yaml>`
3. Edit the project `config.yaml` with real values.
4. Validate the project config: `nebius-cxcli validate <config.yaml>`
5. `nebius-cxcli render <config.yaml>`
   `render` expects the project `config.yaml` path, not the `generated/` directory.
   On successful render, the terminal output prints a copy-paste deploy helper:
   `Next step: deploy the rendered bundle:` followed by a colored
   `nebius-cxcli deploy <config.yaml>` command line.

   > **IMPORTANT:** After any manual or wizard change to `config.yaml`, run
   > `nebius-cxcli render <config.yaml>` again before `nebius-cxcli deploy`,
   > `nebius-cxcli terraform plan`, `nebius-cxcli terraform apply`,
   > `nebius-cxcli flux apply`, `nebius-cxcli flux bootstrap`, or CI.
   > Render updates `generated/nebius-cxcli-manifest.json`; `deploy` then
   > recreates `generated/infra/terraform.auto.tfvars.json` from that manifest
   > before Terraform runs. Passing `config.yaml` to `deploy` only locates the
   > sibling `generated/` directory and does not rerender changed config values.

6. Validate the rendered bundle: `nebius-cxcli validate-generated <generated-dir>`
7. Commit the project `config.yaml` and the deployable `generated/` bundle to the customer private repo.
8. Deploy from the generated bundle:
   - `nebius-cxcli deploy <config.yaml>`
   - `nebius-cxcli terraform apply <generated-dir>`
   - `nebius-cxcli flux apply <generated-dir>`
   - CI workflow deploys from `generated/`, not from `config.yaml`
9. Optional CI setup:
   - `nebius-cxcli bootstrap-ci <config.yaml>`
   - The generated customer workflow watches canonical `<tenant-folder>/<project-folder>/generated/**` paths only. Editing `config.yaml` in the customer repo does not trigger CI deploys; rerendering from `config.yaml` is a manual replace action.

`create` is the bootstrap path, not the day-2 component-editing path. When the same resolved project folder for the same `tenant_id`/`project_id` already exists, `create` now warns and overwrites from scratch instead of reconciling the existing component selection. Use `component list/add/remove --config <config.yaml>` for normal edits after the project already exists.

`create --force` is intentionally narrow in scope: it targets the one resolved project folder only after `tenant_id` and `project_id` are known. It recreates that folder from scratch, including deleting existing generated artifacts and any other files already under that project path, but it does not delete the deployments root or unrelated projects.

If those normalized tenant/project names would collide with an existing different project's folder, `create` fails fast instead of overwriting the wrong config. Other commands accept any existing `<tenant-folder>/<project-folder>/config.yaml`; GitHub environment names, generated manifests, deploy reports, and runtime operations read `project_id` from `config.yaml`, and read `tenant_id` from `config.yaml` only when tenant-scoped quota, Capacity Dashboard, or metadata context is needed. Folder names are not used as identity fallbacks.

One deployments root owns one cxcli-managed `.gitignore` block for all tenant/project folders below it. Folder names remain flexible, but `create`, `render`, and `bootstrap-ci` reject targets inferred under another cxcli-managed deployments root instead of supporting nested root compatibility.

`create` owns project identity (`client_name`, `tenant_id`, `project_id`, `region_id`) and initial scaffold creation from the deployments root. Once `config.yaml` already exists, use `component list/add/remove --config <config.yaml>` for day-2 component selection changes. Those commands keep the current identity and existing values intact, and `render` remains the full reconcile step back into `generated/`.

The first `render` after `create` should not require overwrite confirmation just because the project already has an empty `generated/` scaffold or command-owned lifecycle reports under `generated/reports/`. The overwrite prompt is intended for rerendering over a previously rendered bundle with meaningful render-owned generated content.

In the customer private repo, keep both:

- `config.yaml` as the original render/replace contract
- `generated/` as the deploy contract used by day-2 operations and CI

Rerendering from `config.yaml` is still supported, but it is a manual replace action. The CLI now renders into a hidden sibling staging directory and only swaps it into `generated/` after the new bundle is complete, so a failed rerender leaves the current bundle untouched. The replacement still removes stale or legacy content under `generated/`, including an old `generated/flux/flux-system` subtree. In an interactive terminal, `render` prompts for confirmation before replacing render-owned generated artifacts. In non-interactive contexts, rerendering over those artifacts requires `--force`.

Lifecycle reports under `generated/reports/` are carried forward during that
bundle replacement: `deploy-report.md`,
the `soperator-discovery/` bundle directory,
`ext-soperator-upgrade-report.md`, `upgrade-node-template-report.md`,
`upgrade-node-template-report.json`, `upgrade-node-group-report.md`,
`upgrade-node-group-report.json`, `soperator-upgrade-report.md`,
`soperator-upgrade-report.json`, and JSON detail files referenced from those
Markdown reports are preserved. Unreferenced stale report files are removed with
the rest of the old generated bundle.

The render replacement is a local artifact replacement, not a live infrastructure
destroy. After rerender, `terraform apply`, `flux apply`, `flux bootstrap`, and
`deploy` reconcile the generated desired state against Terraform state and the
live cluster. They apply only the create/update/delete operations required by
that diff. Review the Terraform plan and generated Flux diff before applying
customer changes that rename resources, remove generated manifests, change
ForceNew Terraform inputs, or intentionally disable components, because those
specific diffs can still replace or delete live resources. Only `destroy`,
`terraform destroy`, and `flux destroy` are explicitly destructive command
paths.

For Flux/GitOps, the important safety boundary is Git history, not the local render directory swap. The recommended workflow is: rerender locally, validate/review the new `generated/` diff, then commit and push one final snapshot of the watched path. Do not push an intermediate commit that removes manifests from the watched Git path, and do not routinely unbootstrap/rebootstrap Flux just to replace rendered artifacts.

Project-scoped values such as jump-host SSH public keys belong in the private project `config.yaml`, not in the shipped public `component_sources.yaml`. A private customer-local source catalog may still carry `shared.admin_ssh.public_key` as a bootstrap seed, because `create`/`component add` materialize that value into `config.yaml`. Non-sensitive shared defaults such as `shared.admin_ssh.user_name` are also materialized into selected component rows so rerendering works from `config.yaml` without re-reading those values from the catalog. For operator convenience, both the private catalog seed and the per-project `inputs.ssh_public_key` field accept inline `ssh-rsa`, `ssh-ed25519`, or ECDSA text or a readable local `.pub` path; the persisted contract is always normalized inline key text.

Local `deploy`/`flux bootstrap` behavior when apps + the bundled `mk8s` component are enabled:

- `deploy` runs generated-bundle preflight and Terraform validation before
  Terraform apply; it does not rerender `config.yaml`.
- When Terraform is not already in `PATH`, `deploy`, `terraform plan`, `terraform apply`, `terraform unlock`, and backend-backed Terraform output lookups use a managed Terraform CLI download pinned by `component_cli_settings.yaml` `cli.terraform.version`. The binary is cached under the local nebius-cxcli cache and is not installed system-wide. Managed tool cache entries are reused only when their local checksum sidecar still matches the binary.
- During long-running Terraform apply or destroy operations, `deploy`, `terraform apply`, and `terraform destroy` print one merged status surface: Terraform transitions plus a light Nebius MK8s API snapshot. When an enabled `mk8s` component is present and Nebius SDK auth is available, the CLI polls Nebius MK8s API for cluster/node-group status, suppresses SDK retry tracebacks for requests that are still being retried, and omits completed MK8s operations that predate the current watcher run; otherwise it falls back to a simple elapsed heartbeat for the API side.
- The merged status surface is rendered as a multi-line block with distinct TF and API sections so Terraform progress and Nebius resource state are visually separate in the terminal. Only fixed labels and explicit severity markers use color; Nebius resource names, IDs, counts, and states stay plain text instead of being syntax-highlighted.
- Severity colors are standardized across explicit CLI diagnostics: warnings render in amber and errors render in red.
- If Terraform apply fails, the CLI exits with the Terraform error as the canonical failure and appends the last known merged Terraform/API status snapshot.
- Remote state lock failures are called out separately: the CLI explains that Terraform never acquired the backend lock, so the run created nothing, and points at the stale `.tflock` object metadata when Terraform provides it.
- When Nebius MK8s node-group status reports `ERROR` events, the merged status block includes those alerts from the live SDK event objects and prefers the event's human error text over raw SDK object reprs. Known transient bootstrap warnings such as waiting for ProviderID registration or temporary `Ready=False` node conditions are shown as notes instead of alerts while the node group is still provisioning.
- If the live MK8s API reports an active terminal node-group error during apply or destroy, `deploy` / `terraform apply` / `terraform destroy` now abort the Terraform wait loop early and surface that SDK error directly instead of waiting for a generic Terraform timeout.
- After apply, `deploy` reads the rendered Terraform output `cluster_id` and configures a temporary kubeconfig before applying Flux manifests.
- The bundled `mk8s` component derives endpoint access from `inputs.cluster.public_endpoint`, so the CLI automatically selects the public or private control-plane endpoint instead of assuming public access.
- When more than one built-in cluster target is enabled, enabled app charts bind to one target by setting `apps.charts[].instance_id` to that target id, and target-scoped deploy settings live under `deploy.targets[]` rows with the same `instance_id`. For MK8s, the target id is the normalized cluster resource name stored as that row's `instance_id`. Render writes internal generated target metadata with `target_ref` equal to that `instance_id` and Flux manifests under `generated/flux/targets/<target-id>/`; generated-bundle commands reject stale manifests where those two fields diverge. A plain `deploy <config.yaml>` reconciles every generated target by default; use `deploy --target <target-id>` to narrow one target or `deploy --all-targets` to spell out the default. `flux apply`, `flux destroy`, and `flux bootstrap` still require `--target <target-id>` or `--all-targets` when the generated bundle contains more than one target that needs Kubernetes access.
- On non-CI local runs, that same built-in MK8s handoff also updates the user kubeconfig at `~/.kube/config` with a `nebius-cxcli` exec-based credential entry, creating the `.kube` directory and `config` file when they do not already exist, so `kubectl` can be used against the target cluster after `deploy`, `flux apply`, or `flux bootstrap` without installing a separate Nebius CLI.
- `destroy` and `flux destroy` still use the same built-in MK8s handoff for temporary cluster access when they need to reach rendered app resources directly, but they do not persist or switch the user's local `~/.kube/config`.
- When the selected cluster-access endpoint is private, `deploy`, `flux apply`, `flux bootstrap`, `destroy`, and `flux destroy` require the current machine to already have a private network path to the MK8s API. The CLI does not hardcode or auto-provision that path; customer environments can satisfy it with VPNs, routed private networks, subnet routers, SSH/WireGuard tunnels, or by running the command from an in-network runner.
- When app charts are enabled, `deploy`, `flux apply`, and `flux bootstrap` now print a Kubernetes node-status snapshot first, then proceed directly into Flux or validation-specific readiness checks instead of blocking on a generic "all nodes Ready" gate before useful work starts.
- When the generated manifest declares deploy-time validations, local `deploy`
  uses the same handed-off kubeconfig after Terraform/Flux work to run them
  directly with `kubectl`, keeps compact ordered JSON detail reports under
  `generated/reports/`, refreshes the combined customer-facing
  `generated/reports/deploy-report.md`, and prints a shorter target-grouped
  validation footer in the terminal. GPU-enabled targets can declare deploy-time
  GPU readiness and bounded GPU visibility checks from
  `deploy.targets[].deployment_testing.mk8s_gpu.*`. NCCL settings are
  command-only options for explicit `acceptance-test benchmark` runs. Enabled Soperator targets also get
  a required `soperator_cluster_smoke` deployment test that performs a fast
  Kubernetes snapshot of the `soperator-manager` Deployment, jail storage
  objects, Pending Soperator pods/events, target `SlurmCluster`, and worker
  `NodeSet` resources.
  Deploy waits only through bounded first-run storage/pod startup, does not
  wait for full Slurm availability, does not start Slurm jobs, and does not run Slurm NCCL. The report still includes Kubernetes event
  causes such as `FailedMount` when Pending pods expose a hard storage blocker.
  During local Soperator post-Flux apply, deploy also
  removes legacy source-family ActiveChecks CronJobs/jobs/pods before applying
  target Slurm custom resources, so old v1/v2 check pods cannot keep blocking
  target smoke validation. Observability-enabled MK8s targets get a generated
  in-cluster Observability Agent ingestion guardrail when the active settings
  catalog leaves `primary_agent.validation` enabled; its live Kubernetes reads
  are bounded for large clusters. Native ESO MysteryBox sync targets get a
  required `mysterybox_eso_connectivity` guardrail that validates in-cluster
  Nebius API TLS, `ClusterSecretStore Ready=True`, every configured
  `ExternalSecret Ready=True`, and ESO controller log errors since the current
  validation started. The JSON files remain the machine-readable detail
  contract; the Markdown report is the single human-readable rollup with
  grouped `Infra`, `Apps`, `Grafana`, and `Validations` sections. Its infra
  status list and enabled infra/app component reports are generated from
  `component_sources.yaml`, so new catalog components get a concise report
  without a Python allowlist; sensitive inputs such as keys, passwords, secrets,
  tokens, credentials, and MysteryBox payloads are omitted. MK8s cluster rows
  report CPU and GPU node counts with the same total-node wording, with GPU
  group geometry shown as additional context. Validation sections keep the
  one-line summary and also render a numbered list from each detail report's
  `checks[]` array when one is present. In multi-target MK8s deployments, the
  report lists each cluster shape under `Infra` > `MK8s Clusters`, groups
  Grafana links per target, and keeps validation headings target-scoped so
  repeated checks such as GPU visibility, Soperator smoke, Observability
  ingestion, and ESO MysteryBox connectivity remain distinguishable.
  A plain deploy and `--all-targets` report every selected target. When a run selects one target with `--target <target-id>`, the refreshed validation section is scoped to that selected target instead of marking unselected target validations as not run.
  On Soperator targets whose worker pods reserve all
  Kubernetes GPUs, raw Kubernetes GPU visibility detail reports and the human
  `deploy-report.md` both keep the scheduler skip visible as a GPU visibility
  result; Slurm allocation evidence is reported by explicit
  `acceptance-test smoke --suite slurm` runs instead.
- Generated bundles are expected to carry manifest `deploy.validations` metadata from `render`. If that metadata is missing or malformed, `deploy` now fails fast and requires a rerender instead of recomputing validation specs from the runtime config.
- During deploy-time validations, `deploy` keeps one continuous spinner alive across validation boundaries and live in-cluster progress updates, so the command does not go visually idle between operator readiness, GPU visibility, Observability Agent, or ESO MysteryBox phases.
- Once the built-in MK8s handoff is ready, the local Flux phase now keeps one continuous spinner alive and updates its message through cluster reachability, Flux API discovery, rendered manifest apply, and the final rendered-resource readiness wait so the command does not go visually idle between phases.
- When no app charts are enabled, `render` now emits an empty Flux kustomization without a placeholder repository file. Local `deploy` still prepares the built-in MK8s handoff and refreshes local kubeconfig when that handoff exists, but it skips Flux apply entirely; `flux apply` still refuses to run because there are no enabled charts to apply.
- In non-interactive logs such as GitHub Actions, those same phase updates fall back to stable printed lines instead of transient spinner frames, so CI logs remain readable and do not depend on TTY animation support.
- Generated Flux artifacts are treated as the deploy truth. If an app chart depends on Terraform-backed component outputs, you must rerender after the needed Terraform state exists before treating the rendered Flux tree (`generated/flux` or `generated/flux/targets/<target-id>`) as the final GitOps payload.
- Flux render writes explicit Namespace manifests for chart target namespaces before namespaced `HelmRelease` resources, so local `kubectl apply -k` against the rendered Flux tree does not fail with `namespaces "<name>" not found`.
- Flux uses a split namespace model in this project: shared Flux control-plane and source objects such as `HelmRepository` / `GitRepository` typically live in `flux-system`, while the actual `HelmRelease` and workload pods live in their target app namespace. A workload namespace does not need its own dedicated source object unless it truly uses a different chart or repo source.
- If Flux controllers are missing, `deploy` installs the core Flux controllers into the target cluster automatically using the official Flux install manifest. `flux` CLI is not required for local `deploy`.
- The install manifest version used by local `deploy` comes from `component_cli_settings.yaml` `cli.flux.version`.
- After `kubectl apply -k generated/flux`, `deploy` waits for the rendered Flux `source.toolkit` and `helm.toolkit` resources to report `Ready`, so local deploy does not exit before chart source fetch or Helm reconciliation has actually succeeded.
- Helm chart timeout policy stays catalog-driven: `components.apps.<id>.release.timeout` renders into `HelmRelease.spec.timeout`, and the local Flux wait budget now honors the longest rendered workload timeout plus a short grace window when no explicit CLI timeout override is supplied.
- If Flux controllers had to be installed during `deploy`, the CLI also waits for the required Flux CRD-backed APIs to become discoverable before applying the rendered Flux bundle. This avoids transient `the server could not find the requested resource` races immediately after controller install.
- While that Flux wait is in progress, `deploy` and `flux apply` poll the rendered Flux resources from the cluster with `kubectl get -o json` and print a generic status block showing which `HelmRepository`, `GitRepository`, `HelmRelease`, or `Kustomization` objects are still progressing. This is chart-agnostic and does not hardcode a specific release name.
- When one rendered workload resource reaches a terminal Flux failure state while other rendered workloads are still progressing, the CLI keeps watching the remaining workloads until they settle, then exits non-zero with the failed-resource summary instead of sitting on an unrelated source object until the full outer timeout expires.
- If all rendered workload resources are already `Ready` and only rendered Flux source objects remain pending without publishing a `Ready` condition, the CLI stops waiting and completes with a concise note instead of hanging until the full timeout. The note points operators at `kubectl get helmreleases.helm.toolkit.fluxcd.io -A` to verify the installed workload releases directly.
- `deploy` and `flux apply` are intentionally local direct-apply paths. They do not bootstrap GitOps automatically, because that would require implicit GitHub/Flux bootstrap side effects and some customers intentionally operate without continuous GitOps sync. If the cluster is not bootstrapped yet, the CLI finishes the local apply and prints an informational GitOps note with the exact optional `nebius-cxcli flux bootstrap <generated-dir>` follow-up command. That command takes the local generated bundle path; `flux bootstrap` resolves the GitHub repository separately from `GITHUB_REPOSITORY` or the local git `origin`, and the rendered `generated/flux` path must be committed and pushed before continuous GitOps sync can reproduce local apply. Customers who use local direct apply as the intended workflow can skip that GitOps step.
- At the end of `deploy`, cxcli prints a compact `Deployment summary` footer with three sections: target-grouped validation PASS/FAIL, copy-paste commands such as `wg-quick up/down`, SSH `ProxyJump`, and GitOps bootstrap follow-ups, and important paths limited to the generated bundle plus `generated/reports/deploy-report.md`. Machine-readable validation JSON reports remain under `generated/reports/` for troubleshooting but are not printed in the footer.
- For day-2 component upgrades, use the top-level `upgrade` command group. See
  [Upgrade](#upgrade) for supported layers, safety policies, and copy-paste
  examples.
- `flux apply` uses that same local app-deploy path without running Terraform apply, so it is the apps-only command for day-2 chart deploys after infra already exists.
- `terraform apply` is safe to rerun sequentially with the same `generated/infra`: it validates the existing generated infra bundle and then relies on Terraform state convergence. It is not safe to run concurrently against the same backend state; Terraform remote locking is the protection there.
- `flux apply` is safe to rerun sequentially with the same rendered Flux tree (`generated/flux` or `generated/flux/targets/<target-id>`): it applies the existing rendered manifests, skips Flux controller installation when controllers are already present, and waits for the rendered Flux resources to become `Ready`.
- `flux bootstrap` auto-downloads a managed Flux CLI binary from the official Flux GitHub release for the catalog-pinned `cli.flux.version` when `flux` is not already in `PATH`. The binary is cached under the local nebius-cxcli cache and is not installed system-wide. Managed downloads verify the official release SHA256 manifest before installing cache entries.
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

## Acceptance Testing

Acceptance testing is the explicit post-deploy validation surface for work that
is too broad, disruptive, or time-consuming to run inside normal `deploy`.
These commands are ad-hoc and report-only: they write JSON detail reports under
`generated/reports/`, do not edit `config.yaml`, do not update generated deploy
reports, and do not read Terraform state or initialize the Terraform backend.
Target handoff comes from `generated/reports/deploy-report.md`, an explicit or
unambiguous local kubeconfig context, or a known cluster ID. If that handoff is
missing, run `deploy` or `flux apply` for the target first.

Use smoke tests when you need broad functional evidence that a deployed target
can run basic workloads. Use benchmark tests when you need explicit NCCL
performance or transport evidence. Smoke testing and benchmark testing write
different report files, but each target keeps one canonical report per command:
`acceptance-smoke-report-<target>.json` for smoke and
`acceptance-benchmark-report-<target>.json` for benchmark. For each generated
report, terminal output also prints one concise result line with `PASSED`,
`FAILED`, or `SKIPPED`, the suite scope, target, and the most relevant summary
or skip reason, plus elapsed time in `hh:mm:ss`. The JSON report also records
`elapsed_seconds` for machine processing and `elapsed_time` for the same
formatted `hh:mm:ss` display value. On color-capable terminals, result status
is colorized: green for `PASSED`, red for `FAILED`, yellow for `SKIPPED`, and
cyan for unknown report parsing status. Report paths, suite names, and target
names use bold accent colors, while default-color labels, summaries, skip
reasons, and elapsed times stay unbolded for readability.

### Smoke Tests

Smoke tests answer whether the selected target works end to end for the chosen
workload family. They do not attempt to measure cluster performance.

- `nebius-cxcli acceptance-test smoke <config.yaml> --suite k8s-cuda`
  runs a Kubernetes CUDA workload smoke on MK8s GPU targets. It uses the
  handed-off kubeconfig, schedules CUDA validation pods across every currently
  scheduler-free Ready GPU node, and proves that Kubernetes can admit a GPU
  workload and that the GPU stack exposes usable devices to pods. The suite
  name selects the Kubernetes runtime path directly; explicit
  `--suite k8s-cuda` can also be used when an operator intentionally wants the
  Kubernetes smoke on a Soperator-owned GPU target.
- `nebius-cxcli acceptance-test smoke <config.yaml> --suite slurm`
  runs the Soperator/Slurm smoke suite. It reaches the Soperator login pod,
  inspects Slurm partitions and queue state, then runs Slurm jobs for hostname
  coverage, GPU driver-jail evidence, and one-GPU allocation evidence across
  eligible nodes. For GPU worker NodeSets, the report shows whether the
  chart-owned `nvidia-driver-root` mount and `cxcli-gpu-driver-jail` init guard
  are present when the chart version owns that contract, and whether Slurm jobs
  can see non-empty `libcuda.so.1`, `libnvidia-ml.so.1`, and `nvidia-smi` from
  the job root. Use
  `--batch-size`, `--concurrency`, and
  `--continue-on-failure/--fail-fast` to control exhaustive all-node Slurm
  collection.

Smoke commands require `--suite`; omitted `--suite` fails fast instead of
choosing a K8s or Slurm suite automatically. Once a suite is selected, omitting
`--target` runs every generated target, equivalent to `--all-targets`.
Operators can narrow the run with `--target <target-id>`.

### Benchmark Tests

Benchmark tests answer whether the selected target runs the selected benchmark
suite and whether observed bandwidth satisfies the run threshold. On 1-GPU
K8s or Slurm NCCL runs, a below-threshold average bus-bandwidth result is
recorded as a report comment instead of failing the benchmark when NCCL
completed and reported the average.
`acceptance-test benchmark` is suite-driven so additional benchmark types can
be added without changing the command shape. Benchmark commands require
`--suite`; omitted `--suite` fails fast instead of choosing a K8s suite
automatically. Once a suite is selected, omitted benchmark selectors and
run-only flags default to:

- every generated target, equivalent to `--all-targets`
- all schedulable GPU nodes, equivalent to omitting `--max-nodes`
- no cxcli benchmark timeout, so the run continues until completion or user
  cancellation
- `--average-bus-bandwidth-threshold-gbps 300`

Operators can narrow the run with `--target`, cap node count with
`--max-nodes`, set a run deadline with `--timeout`, and adjust the RDMA
bandwidth gate with `--average-bus-bandwidth-threshold-gbps`.

Common benchmark commands:

```bash
nebius-cxcli acceptance-test benchmark <config.yaml> --suite k8s-nccl
nebius-cxcli acceptance-test benchmark <config.yaml> --suite slurm-nccl
nebius-cxcli acceptance-test benchmark <config.yaml> --target mk8s-prod --suite k8s-nccl --max-nodes 4 --timeout 20m --average-bus-bandwidth-threshold-gbps 300
nebius-cxcli acceptance-test benchmark <config.yaml> --target sop-cluster1 --suite slurm-nccl
nebius-cxcli acceptance-test benchmark <config.yaml> --target sop-cluster1 --suite slurm-nccl --max-nodes 2 --timeout 5m --average-bus-bandwidth-threshold-gbps 300
```

### NCCL Suite Selection

`k8s-nccl` and `slurm-nccl` both run NCCL `all_reduce_perf`, but they validate
different schedulers and runtime paths.

| Suite | Runs Through | What It Conducts | Use When |
| --- | --- | --- | --- |
| `k8s-nccl` | Kubernetes, Kubeflow Training Operator, and the transient `nccl-test` Helm chart | Creates a temporary `MPIJob` in the validation namespace, selects Ready GPU nodes through Kubernetes, renders chart values for the resolved target shape, and runs NCCL in worker pods. cxcli selects Socket/TCPIP transport for Ethernet-only shapes and RDMA transport for GPU-cluster / InfiniBand shapes. For 1-GPU shapes, below-threshold average bandwidth is recorded as a comment when NCCL completes and reports the average. | You want Kubernetes-level NCCL evidence for an MK8s GPU target, including GPU Operator, Network Operator/RDMA, pod scheduling, and MPIJob behavior. |
| `slurm-nccl` | Soperator login pod, Slurm partitions, Slurm allocation, and `mpirun` inside the Slurm environment | Selects an eligible Slurm GPU partition, chooses idle GPU nodes, runs a GPU driver-jail preflight for non-empty `libcuda.so.1`, `libnvidia-ml.so.1`, and `nvidia-smi` from the Slurm job root, optionally caps nodes with `--max-nodes`, allocates through Slurm, and runs `/usr/bin/all_reduce_perf_mpi`. It prefers 8-GPU Slurm nodes when available, but it also runs valid one-GPU Slurm nodes: multiple idle one-GPU nodes run a multi-node NCCL benchmark capped at a 2G message size, while one total GPU runs a launch/smoke check with no collective bandwidth threshold. For 1-GPU Slurm runs that do report average bandwidth, below-threshold bandwidth is recorded as a comment instead of failing the benchmark. | You want Slurm-level NCCL evidence for a Soperator target and need to validate the scheduler path users will run training jobs through. |

## Soperator Commands

`nebius-cxcli` has two Soperator lifecycle command groups, and they are
intentionally separate:

- `nebius-cxcli soperator` is for Soperator app rows that cxcli already manages
  in `config.yaml` and the rendered bundle. `soperator backup` and
  `soperator restore` create or apply restore-capable archives for moving a
  Soperator cluster to a new empty compatible cluster. Restore is DR/new-empty-target
  only; it is not same-cluster rollback and must not target the original/source
  cluster or an existing Soperator namespace. `soperator upgrade` can
  run MK8s node-template changes and Soperator chart changes in one
  checkpointed maintenance window with Slurm-aware preflight, backup, apply,
  postflight, and reports.
- `nebius-cxcli ext-soperator` is for existing Nebius MK8s clusters that are
  outside Terraform ownership. `ext-soperator backup` and
  `ext-soperator restore` use the same archive contract for onboarded external
  targets, with `external-soperator-backup-*.tar.gz` archive names. Restore is
  DR/new-empty-target only; it is not same-cluster rollback and must not target
  the original/source cluster or an existing Soperator namespace.
  `ext-soperator backup` can also create a pre-onboarding archive directly
  from `--project-id` plus `--cluster-id` or `--kube-context`. For direct
  `--cluster-id` access, `--access external` selects the public control-plane
  endpoint and `--access internal` selects the private endpoint; cxcli assumes
  VPN or another private network path is already available for internal access.
  Do not combine `--access` with `--kube-context`; the kubeconfig context
  already selects its API endpoint.
  `ext-soperator onboard` registers and analyzes one external target, and
  `ext-soperator upgrade` is used only when the accepted onboarding report says
  external-upgrade-owned work is required.

For a new CXCLI Managed Soperator deployment, use `create` or
`component add apps:soperator@<target>` to build the cxcli-managed
MK8s+SFS+Soperator bundle, then use `validate`, `render`, and `deploy`. For
later full-cluster or chart-only upgrades of that cxcli-managed row, use
`nebius-cxcli soperator upgrade`.

In this section, CXCLI Managed Soperator means a self-managed Soperator
deployment that `nebius-cxcli create` or `component add apps:soperator` records
in `config.yaml` and renders into the generated bundle. It is separate from the
Managed Soperator service exposed through the Nebius Console.

For an existing external Nebius MK8s cluster, use `ext-soperator onboard`
first. If onboarding finds no external-upgrade-owned work, the next path is normal
`render` and `deploy`. If onboarding records external-upgrade-owned work, run
`ext-soperator upgrade` before deploy. Onboarding locks the full accepted
upgrade path in `deploy.targets[].soperator_onboarding.upgrade_path`; repeat
`ext-soperator upgrade --execute --approve` until every locked segment is
complete. Each run advances at most one Kubernetes minor hop. After the final
locked segment completes and the target refreshes into the deploy-owned shape,
future cxcli-managed Soperator chart upgrades can use
`nebius-cxcli soperator upgrade`; the external MK8s cluster still remains
outside Terraform ownership.

To move an external Soperator cluster to a new empty compatible cluster without
running the full external upgrade workflow, create an archive from the source
and restore it against the new cluster context. Do not point restore at the
original/source cluster or an existing Soperator namespace; it is not an
in-place rollback. For an already-onboarded source:

```bash
nebius-cxcli ext-soperator backup <config.yaml> --target <target>
nebius-cxcli ext-soperator restore <backup.tar.gz> \
  --kube-context <new-cluster-context> \
  --execute \
  --approve
```

Before onboarding, use direct cluster identity instead:

```bash
nebius-cxcli ext-soperator backup \
  --project-id <project-id> \
  --cluster-id <mk8scluster-id> \
  --access internal \
  --backup-dir ./backups
```

External onboarding is not a Terraform import. The MK8s cluster and its node
groups remain outside Terraform ownership. cxcli records enough target
metadata, Soperator analysis, placements, and accepted remediation decisions
to render/apply the Soperator app and to run guarded external upgrade phases.

### Soperator Command Map

| Command | Use it for | Mutation model |
| --- | --- | --- |
| `nebius-cxcli soperator discover <config.yaml> --target <target>` | Write a read-only support-safe discovery bundle for a cxcli-managed Soperator cluster before upgrade or support review. | Uses the generated managed MK8s kube handoff unless `--kube-context` overrides it. Writes `generated/reports/soperator-discovery/<target>/manifest.json` plus identity, Kubernetes, Slurm, accounting, customizations, fingerprints, findings, and summary files. `--output-dir` selects the output root and still preserves the `generated/reports/soperator-discovery/<target>/` structure below it. The result footer and summary print discovered Kubernetes version plus Soperator status and version; if no Soperator installation is detected, they say so explicitly. `summary.md` includes `Upgrade Guidance` without gating discovery; that section shows Kubernetes minor hops, the one-shot Soperator hop to the cxcli-pinned target, and canonical ordering across the Kubernetes `1.33+` boundary. It is not a backup and does not include raw Secret values, SQL, DB dumps, tokens, or cert material. |
| `nebius-cxcli soperator backup <config.yaml> --target <target>` | Create a restore-capable backup for a cxcli-managed Soperator target before upgrade, migration, or other maintenance. | Temporarily quiesces chart-managed accounting when present, writes a local mode-`0600` `soperator-backup-*.tar.gz` archive under `<config.yaml parent>/backups` by default, and includes raw Kubernetes Secrets, ConfigMaps, service accounts, services, PVCs, workloads, RBAC, policy/networking resources, Soperator CRs, Helm values, Slurm snapshots, and the chart-managed MariaDB accounting DB dump when live accounting exists. Secret values and SQL are never printed. |
| `nebius-cxcli soperator restore <backup.tar.gz> --execute --approve` | DR restore a Soperator backup archive onto a new empty compatible cxcli-managed target cluster namespace. | Dry-run by default. This is not same-cluster rollback: do not target the original/source cluster or an existing Soperator namespace. With `--execute --approve`, validates archive checksums, creates the namespace when needed, rewrites archived namespaced resources to the selected namespace, applies restore-ready Kubernetes manifests, imports the DB dump into chart-managed MariaDB when the archive contains one, and restores accounting replicas when accounting was quiesced. |
| `nebius-cxcli soperator upgrade <config.yaml> --target <target> [--to-chart-version <chart-version>] [--to-k8s-version <major.minor>] [--to-os <image>] [--to-gpu-stack-preset <preset>]` | Upgrade a cxcli-managed Soperator cluster after it is already part of the generated bundle. Omitted MK8s flags mean MK8s no-op; when MK8s flags are supplied without `--to-chart-version`, the chart is a no-op. Use this for cxcli-created Soperator targets and for external targets only after onboarding/external upgrade has handed them back to the deploy-owned desired-state path. | Soperator-aware cxcli-managed full upgrade: validates the current bundle, checks the committed Soperator/Kubernetes support policy before mutation, creates a restore-capable local backup with raw Kubernetes Secrets and optional chart-managed MariaDB accounting DB dump, captures protected customer state before mutation, drains cxcli-owned Slurm worker nodes and handles running jobs when MK8s changes are requested, runs the Terraform-managed node-template workflow with stable node-group readiness confirmation, applies the Soperator chart when requested, verifies static Soperator chart version on live Kubernetes objects, runs a fast stage-scoped verification gate after each completed managed upgrade stage before advancing, compares protected Slurm/Soperator config fingerprints plus shared protected-state hashes while excluding cxcli-owned temporary drain state and replacement instance churn, reruns required Soperator/Slurm validation, runs bounded read-only fast safety checks, and writes `generated/reports/soperator-upgrade-report.md` / `.json` with `Stage Fast Verification` and JSON `stage_verification` details. |
| `nebius-cxcli ext-soperator discover [<config.yaml-or-deployments-root>] --target <target>` or `nebius-cxcli ext-soperator discover --project-id <project-id> --cluster-id <mk8scluster-id>` | Write the same read-only discovery bundle for an external Soperator source cluster, either from an onboarded target, a config-backed direct `--cluster-id` / `--kube-context` source, or a standalone Nebius MK8s cluster id before onboarding. | Writes `generated/reports/soperator-discovery/<target>/manifest.json` and the same section files used by managed discovery. When no config/deployments path is supplied, `--project-id` is required for `--cluster-id`, `--tenant-id` is optional metadata, `--client-name` selects a specific runtime-auth cache profile when needed, and the default bundle root is the current directory. `--output-dir` selects a different root, for example `~/deployments/generated/reports/soperator-discovery/<target>/manifest.json`. The result footer and summary print discovered Kubernetes version plus Soperator status and version; when Helm release metadata is missing but Soperator resources carry chart labels, discovery uses those labels for the displayed Soperator version, and if no Soperator installation is detected, it says so explicitly. `summary.md` includes `Upgrade Guidance` without gating discovery; that section shows Kubernetes minor hops, the one-shot Soperator hop to the cxcli-pinned target, and canonical ordering across the Kubernetes `1.33+` boundary. It collects facts and remediation findings only; customer-approved remediation remains owned by `ext-soperator onboard`, `ext-soperator upgrade`, or `soperator upgrade`. |
| `nebius-cxcli ext-soperator backup [<config.yaml>] --target <target>` or `nebius-cxcli ext-soperator backup --project-id <project-id> --cluster-id <mk8scluster-id>` | Create the same restore-capable archive for an external Soperator source cluster, either from an accepted onboarded target or directly before onboarding. | The config form validates the accepted external onboarding target and uses the stored kube context or temporary Nebius kubeconfig handoff for `cluster_id` targets. The standalone form requires `--project-id` with `--cluster-id`, accepts `--kube-context` for local kubeconfig access, and reads live Helm release evidence before planning so the source version is recorded. With standalone `--cluster-id`, `--access external` selects the public control-plane endpoint and `--access internal` selects the private endpoint; cxcli does not create VPN/routing and expects private reachability to already exist. `--access` is rejected with standalone `--kube-context` because the kubeconfig context already selects its API endpoint. Both forms write `external-soperator-backup-*.tar.gz` archives under the config parent `backups/` directory or `./backups` in standalone mode unless `--backup-dir` is provided, and run the same sensitive archive flow as managed backup. Chart-managed MariaDB is backed up when present; `externalDB.enabled=true` fails fast before mutation. |
| `nebius-cxcli ext-soperator restore <backup.tar.gz> --kube-context <new-context> --execute --approve` | DR restore a Soperator archive onto a new empty external target cluster. | Archive-driven and dry-run by default. This is not same-cluster rollback: do not target the original/source cluster or an existing Soperator namespace. With approval, applies restore-ready Kubernetes manifests and imports the chart-managed MariaDB accounting dump into the selected kube context when the archive contains one. |
| `nebius-cxcli ext-soperator onboard <config.yaml-or-deployments-root>` | Register one existing Nebius MK8s cluster by `cluster_id`, discover source Soperator state, choose storage/compute onboarding modes, and write the accepted onboarding plan. | Read-only against live cluster state; writes local `config.yaml` and the canonical discovery bundle at `generated/reports/soperator-discovery/<target>/manifest.json`. Interactive runs show the discovered Kubernetes version, default external MK8s node-template work to the next minor hop when no final target is supplied, and print the matched Soperator/Kubernetes upgrade-path rule during the decision summary. Non-interactive runs use `--cluster-id` and optional `--target-id`; pass the final intended Kubernetes target with `--to-k8s-version` to lock the full path, and cxcli will split it into one-hop `ext-soperator upgrade` segments. The locked path is stored under `deploy.targets[].soperator_onboarding.upgrade_path` and is included in the accepted onboarding fingerprint. Unsupported accepted plans still require `--allow-unsupported-soperator-upgrade-path`. No-op reruns preserve stable discovery content so unchanged onboarding does not invalidate external upgrade checkpoints. |
| `nebius-cxcli ext-soperator upgrade <config.yaml> --target <target> --dry-run` | Inspect the accepted external-cluster upgrade plan before any live mutation. | Read-only; refreshes discovery for the next incomplete locked segment, validates accepted onboarding, shows the matched Soperator/Kubernetes upgrade-path rule, refuses deploy-owned/no-upgrade action sets with render/deploy guidance, and prints a color-highlighted sectioned plan covering target discovery, versions, the full locked path, completed/current/remaining segments, the accepted one-minor Kubernetes hop for the current segment, accepted onboarding actions, node-template rollout, phases, execution controls, and execution guarantees in interactive terminals. Normal hop progression does not require rerunning discovery or onboarding. |
| `nebius-cxcli ext-soperator upgrade <config.yaml> --target <target> --execute --approve` | Execute one approved locked external-upgrade segment: one MK8s control-plane/node-template hop plus any target GPU stack, storage, compute, Soperator Helm cutover, configured MK8s GPU deployment testing, and required Soperator/Slurm smoke validation assigned to that segment. | Creates a restore-capable backup before mutation for DR restore to a new/replacement cluster only, captures protected customer state before approved mutation, fails unsupported or not-validated Soperator upgrade paths unless the run also passes `--allow-unsupported-soperator-upgrade-path`, mutates only supported external upgrade surfaces, handles affected-node Slurm jobs through `--job-policy`, writes a local checkpoint with `upgrade_path_fingerprint`, `current_segment_id`, `completed_segment_ids`, and segment report metadata, enforces one Kubernetes minor hop per run, rechecks completed selected actions against live state on rerun, runs a fast stage-scoped verification after each executed stage before advancing within the segment, verifies external MK8s node-template state, verifies target Helm chart workloads, suspends old source-family Flux Kustomization desired state, deletes suspended old source-family Flux HelmRelease records, retires stale profile-derived source-family Helm release records while preserving shared/storage resources, writes validation detail reports under `generated/reports/`, runs the shared bounded read-only fast safety checks in validation hold, writes stage verification, MK8s GPU deployment-testing, Soperator/Slurm validation, and protected-state rollups into `generated/reports/ext-soperator-upgrade-report.md` and `.json`, refreshes `deploy-report.md` as a secondary deploy-compatible MK8s GPU summary only after protected comparison passes, keeps accepted onboarding while locked segments remain, prints the next same-command invocation, and stops at guarded pending gates. |
| `nebius-cxcli upgrade node-template` and `upgrade node-group` | Upgrade Terraform-managed MK8s infrastructure underneath a cxcli-managed deployment. | `node-template` owns Kubernetes version, OS, and Nebius-image GPU stack rolling updates and writes `generated/reports/upgrade-node-template-report.md` / `.json` after verification. Node-group hardware, preset, CPU/GPU kind, GPU cluster, or fabric changes require the approved `upgrade node-group` planner; current execute writes `generated/reports/upgrade-node-group-report.md` / `.json` with the approved pre-mutation checkpoint and then stops before live replacement/cutover/retirement. |

Operationally, finish a running `ext-soperator upgrade` or `soperator upgrade`
from the same laptop, workdir, and operator account that started it. The resume
checkpoints are local operational state, not deploy input and not Git-tracked
desired state:

- `ext-soperator upgrade`: `.nebius-cxcli/ext-soperator-upgrades/<target>/checkpoint.json`
- `soperator upgrade`: `.nebius-cxcli/soperator-upgrades/<target>/checkpoint.json`

The cxcli-managed deployments `.gitignore` excludes `.nebius-cxcli/`, so these
checkpoints stay local. After upgrade completes and `config.yaml` plus
generated reports are refreshed, normal `validate`, `render`, and `deploy`
commands can run from any workstation that has the repo state and required
Nebius/Kubernetes access.

### CXCLI Managed Soperator Clusters

Use the cxcli-managed path when cxcli should create and own the infrastructure:

```bash
nebius-cxcli create <deployments-root>
# select infra:mk8s and apps:soperator in the wizard
nebius-cxcli validate <config.yaml>
nebius-cxcli render <config.yaml>
nebius-cxcli deploy <config.yaml>
```

The Soperator create/component wizard uses `production-cluster` and materializes
the complete MK8s+SFS+Soperator five-role bundle: `system`, `controller`,
`login`, `accounting`, and `worker`, plus SFS jail, controller-spool, and
accounting filesystems. It skips external placement prompts because cxcli is
creating the target node groups itself.

For day-2 upgrades of a cxcli-managed Soperator deployment:

- Use `soperator backup` when you need a restore-capable archive outside an
  upgrade workflow, for example before moving a Soperator cluster to a newly
  empty compatible cluster. Do not use `soperator restore` against the
  original/source cluster or an existing Soperator namespace; restore is not an
  in-place rollback:

  ```bash
  nebius-cxcli soperator backup <config.yaml> \
    --target <target>
  nebius-cxcli soperator restore <backup.tar.gz> \
    --kube-context <new-cluster-context> \
    --execute \
    --approve
  ```

  The backup archive includes restore-ready Kubernetes manifests plus raw
  Kubernetes resource JSON for Secrets, ConfigMaps, service accounts, services,
  PVCs, Deployments, StatefulSets, DaemonSets, CronJobs, RBAC,
  PodDisruptionBudgets, NetworkPolicies, HPAs, Ingresses, SlurmCluster,
  NodeSet, and ActiveCheck resources. It also includes Helm values, Slurm
  snapshots, and the chart-managed MariaDB Slurm accounting DB dump when live
  accounting exists. The restore command is dry-run by default and requires
  `--execute --approve` before applying manifests or importing any included DB.

- Use `soperator upgrade` for the canonical cxcli-managed Soperator cluster
  upgrade path. Pass any subset of chart and MK8s targets; omitted fields are
  treated as explicit no-ops:

  ```bash
  nebius-cxcli soperator upgrade <config.yaml> \
    --target <target> \
    --to-chart-version <chart-version> \
    --to-k8s-version <major.minor> \
    --to-os <image> \
    --dry-run
  nebius-cxcli soperator upgrade <config.yaml> \
    --target <target> \
    --to-chart-version <chart-version> \
    --to-k8s-version <major.minor> \
    --to-os <image>
  ```

  Before mutation it validates the current generated bundle, captures protected
  Soperator/Slurm config fingerprints, and writes a restore-capable backup under
  `<config.yaml parent>/backups` by default. The archive is mode `0600` and
  includes raw Kubernetes Secret restore material plus a chart-managed MariaDB
  Slurm accounting DB dump when live accounting exists; reports show only path,
  size, checksums, and included categories. Secret values and SQL contents are never printed. If
  `externalDB.enabled=true`, v1 fails fast before mutation because external DB
  backup is not implemented yet.
  When MK8s target flags are requested, the command places affected Slurm
  worker nodes in `DRAIN`, shows or enforces the selected running-job policy,
  and lets the Terraform/Nebius node-group rollout own Kubernetes drain/cordon
  behavior; it does not run raw `kubectl drain`. After MK8s settles, it reruns
  Soperator/Slurm validation and compares protected customer config/policy
  fingerprints before applying the chart phase.
  When the interactive wizard prompts for `soperator.upgrade.to_chart_version`,
  it
  shows the selected row's current chart version and uses the active
  `component_sources.yaml` Soperator chart pin as the default target version;
  pressing Enter accepts that pin.
  `nebius-cxcli upgrade helm-chart` is intentionally non-Soperator-only and
  fails fast for `apps:soperator@<target>` with the canonical
  `soperator upgrade` command.
  If the cxcli-managed Soperator app row has
  `values.soperator-activechecks.enabled=true` or
  `values.soperator-activechecks.waitForChecks.enabled=true`, non-dry-run
  `soperator upgrade` owns a checkpointed maintenance-window lifecycle: it
  snapshots the original values, writes
  `.nebius-cxcli/soperator-upgrades/<target>/checkpoint.json`, renders and
  applies a temporary suspension, patches matching live ActiveCheck CRs to stop
  launch-on-create checks, deletes matching already-launched ActiveChecks
  CronJobs/jobs/pods, runs the upgrade and postflight validation, then restores
  the original cxcli-owned values. If cxcli cannot inspect live ActiveCheck
  state for an ActiveChecks-enabled row, the cxcli-managed upgrade fails closed
  before the chart upgrade rather than leaving launch-on-create checks
  ambiguous. The command writes validation details under `generated/reports/` and
  `generated/reports/soperator-upgrade-report.md` and
  `generated/reports/soperator-upgrade-report.json` so the
  operator can verify postflight evidence, what was suspended, and whether it
  was restored.

  If the row currently has `repo: ''`, it remains a local static chart render.
  If you want the exact published parent OCI package, first set the row `repo`
  to `oci://cr.eu-north1.nebius.cloud/e00th0mgv3zddz7468/charts/soperator` and
  `version` to the desired package version, then run `soperator upgrade` or
  `render` and `deploy`. cxcli still renders Soperator as a static post-Flux
  manifest so the chart source can be OCI without using Helm's in-cluster
  release Secret storage.
  If `upgrade helm-chart --to-version` appears lower than the current
  configured chart version, cxcli prints a production downgrade warning but
  still allows the change for rollback or recovery. Helm chart downgrades are
  not guaranteed safe; review chart release notes, CRDs/schema migrations,
  application state, and backups first. The same warning applies through
  `soperator upgrade`. Direct `config.yaml` edits followed by `render` and
  `deploy` remain desired-state changes, but the canonical cxcli-managed
  Soperator upgrade path is `soperator upgrade` because it adds the explicit
  pre/post Soperator validation gates.

- Use `upgrade node-template` for Terraform-managed MK8s Kubernetes minor,
  node OS image, and Nebius-image GPU stack rolling updates. You can pass any
  subset of `--to-version`, `--to-os`, and `--to-gpu-stack-preset`; omitted
  values keep the selected live value when it is unambiguous and compatible.
- Use `upgrade node-group` for one Terraform-managed MK8s node-group migration
  at a time when the target changes hardware platform, hardware preset, CPU/GPU
  kind, GPU cluster, or InfiniBand fabric.

These `upgrade` commands are desired-state workflows. They run live discovery
and safety checks, update `config.yaml`, rerender `generated/`, validate the
bundle, and apply the relevant Terraform or Flux target when not in `--dry-run`.
For `upgrade node-template --strategy safe-surge`, cxcli also checks the
temporary surge-node quota/capacity for the selected node-group stages before
the first staged `config.yaml` write or Terraform mutation; plain `validate`
checks desired-state quota but cannot infer this runtime strategy choice.
Kubernetes version downgrade targets are refused. Helm chart targets remain
operator-controlled desired state: lower target versions are allowed with an
explicit warning because they can be useful for recovery, but they are risky for
production stateful workloads and CRD/schema changes.
For MK8s targets, non-dry runs finish with a final MK8s readiness check that
re-reads the live control plane and selected node groups, then verifies the
expected Kubernetes version, node OS image, platform/preset layer, and Nebius
`drivers_preset` / CUDA stack where that command changed them. This requires
provider node-group status rather than accepting matching spec fields alone:
the live node group must show ready, target, and total node counts. If the
provider also returns outdated-node or reconciliation fields, those must be
clean before cxcli reports success.
They do not have `--yes`; `--dry-run` is the non-mutating preview path. See
[Upgrade](#upgrade) for the full upgrade command contract, upgrade strategies,
and examples.

### Soperator Slurm Scheduling And Command Examples

This section is for self-managed Soperator clusters deployed by cxcli. The
Soperator Helm chart remains the owner of `SlurmCluster`, `NodeSet`, partitions,
and rendered `slurm.conf`; make persistent changes in `config.yaml` / Helm
values, then `render` and `deploy` again. Do not edit rendered ConfigMaps for a
durable change because the operator can reconcile them back. For Managed
Soperator, use the Nebius Console or Nebius support workflow for settings that
are not exposed in the managed service UI.

Slurm concepts used by the bundled profiles:

The important split is ownership. The Helm chart owns persistent Slurm
resources and `slurm.conf` rendering. cxcli selects bundled profiles and writes
Helm values into `config.yaml`. Slurm users still own per-job choices such as
`--qos`, `--nice`, `--time`, and `--requeue`.
For production clusters, cxcli keeps raw profile-owned `inputs.node_groups.*`
prompts hidden but exposes `inputs.soperator.*_node_count` helpers for CPU
service-role counts and `inputs.soperator.*_autoscaling` helpers for every
Soperator-managed role. The `system` service role defaults to autoscaling from
3 to 5 nodes; disabling that helper falls back to three fixed nodes.
`controller`, `login`, and `accounting` default to two fixed nodes each, with
their autoscaling helpers disabled unless the operator enables them. Worker
fixed sizing is shape-specific:
`soperator.worker_cpu_total_nodes` / `worker_cpu_nodes_per_group` for CPU
workers and `soperator.worker_gpu_total_nodes` /
`worker_gpu_nodes_per_group` for GPU workers. cxcli writes
`soperator.worker_node_groups.<worker>` entries for the generated shards, with
canonical `autoscaling` and `ephemeral_nodes` controls ready to edit. The
selected profile's per-group limit is enforced before materialization; Nebius
production profiles cap worker shards at 100 MK8s nodes per generated group.
The `create` wizard uses `autoscaling.enabled` as the per-shard Infra/MK8s worker
autoscaling toggle: answering `true` also writes same-shard
`ephemeral_nodes.enabled=true` and asks min/max, with max defaulting to that
shard's generated capacity, while answering `false` clears same-shard
autoscaling bounds and writes `ephemeral_nodes.enabled=false`. When more than
one generated worker shard exists, the wizard first offers a synthetic bulk
apply-to-all choice for all CPU worker shards, all GPU worker shards, or all CPU
and GPU worker shards. The mixed CPU+GPU helper is shown as
`all_worker_shards_apply_to_all` and defaults to `true`; accepting it asks one
`autoscaling.enabled` prompt and writes only canonical per-shard controls, while
declining keeps the per-shard prompts. No bulk key is saved. The wizard asks
`worker_ephemeral_nodes.suspend_time_seconds` only after at least one shard has
autoscaling-backed ephemeral nodes enabled. In hand-authored config, enabling a
shard's `autoscaling` block renders K8s autoscaling min/max values instead of
fixed `node_count`, and preserves an explicit `0..0` scale-to-zero range. Each
`worker_*_total_nodes` value is a Kubernetes worker host count for that shape,
not total GPU count: Soperator worker replicas match the worker hosts, and GPU
count per host is written to `slurmd.resources.gpu`.
Current worker autoscaling remains maximum-capacity materialization unless
that same shard's `ephemeral_nodes.enabled=true`. In that mode cxcli derives
`initialNumberEphemeralNodes` from the shard's autoscaling `min_node_count` for
CPU workers, raises GPU worker shards to at least one initial active worker when
max capacity is positive so Soperator can seed GPU libraries into the jail,
writes global `slurmConfig.suspendTime` from
`worker_ephemeral_nodes.suspend_time_seconds`, and leaves day-2 active-node
changes to Slurm power control / `NodeSetPowerState`.
CPU service-role counts are independent of worker sharding. CPU service-role
autoscaling must keep `max_node_count` at least `1`.

| Concept | Meaning | Handled by the Helm chart | Handled by cxcli | If not fully handled, why and how to cover it |
| --- | --- | --- | --- | --- |
| `SlurmCluster` | The root Soperator custom resource. It is the object Soperator reconciles into controller, login, accounting, worker, partition, storage, and `slurm.conf` state. | Yes. The chart renders the `SlurmCluster` CR and feeds it `slurmConfig`, `customSlurmConfig`, `partitionConfiguration`, NodeSets, filters, and storage values. | Indirectly. cxcli does not render `SlurmCluster` itself; it materializes profile choices and Helm values that the chart renders. | Do not edit the rendered CR or ConfigMaps for durable policy. Add or override chart values in `config.yaml`; if the chart lacks a common durable field, add a typed chart value first, then let cxcli profiles use it. |
| `NodeSet` | Slurm worker capacity, usually mapped to Kubernetes node groups such as `worker`, `worker-cpu`, or `worker-gpu`. | Yes. The chart renders NodeSet CRs, worker filters, features, GPU/GRES metadata, and role placement. | Yes. The selected `values.nodesetsProfile` materializes the worker layout, node-group mapping, and generated MK8s node groups for production mode. | Physical capacity is still Terraform/MK8s-owned. Use cxcli profiles for standard layouts, or direct Helm values for custom worker shapes. |
| `NodeConfigurator` | Soperator host-preparation machinery. The chart also exposes an optional rebooter helper for maintenance workflows. | Yes. The chart renders the NodeConfigurator resources and keeps the host-setup carrier valid even when the rebooter is disabled. | Yes for gates/defaults. cxcli leaves rebooter-style maintenance behavior off unless the operator opts in. | This is not Slurm scheduling policy. Do not use it to express job restart or preemption behavior. |
| `Partition` | A Slurm queue plus policy. It selects one or more NodeSets and carries settings such as `Default`, `Hidden`, `State`, `DefaultTime`, `MaxTime`, `PriorityTier`, `PreemptMode`, `AllowQos`, and resource defaults. | Yes. The chart renders `values.partitionConfiguration` into `SlurmCluster.spec.partitionConfiguration`; typed `partitions[].policy` fields are preferred, with per-partition `config` as the escape hatch. | Yes. `values.partitionProfile` selects bundled layouts such as `shape-default`, `with-debug-long`, and `with-qos-preemption`. | If a partition token is missing from typed `policy`, use the partition `config` escape hatch for a one-off. Add a typed chart field when the token should become a supported contract. |
| `PriorityTier` | Partition scheduling tier. Higher tiers are evaluated before lower tiers. With `PreemptType=preempt/partition_prio`, it also becomes the tier used for partition-priority preemption between jobs that share resources. | Yes. Use `partitionConfiguration.partitions[].policy.priorityTier`. | Yes. Bundled partition profiles set tiers for hidden, shape, debug, long, and QOS-style queues. | A tier alone does not enable preemption. It gives deterministic queue ordering; preemption also requires a matching cluster-wide `PreemptType` and compatible `PreemptMode`. |
| `PriorityJobFactor` and `PriorityWeightPartition` | Numeric multifactor priority inputs for the partition factor. They are different from `PriorityTier`; they only matter when the partition priority weight is nonzero. | Partially. The chart types `schedulingConfig.priorityWeights.partition` as `PriorityWeightPartition`, but does not currently type per-partition `PriorityJobFactor`. | Not in bundled profiles. The profiles use `PriorityTier` for partition policy and leave `PriorityWeightPartition` unset unless the operator overrides it. | Use per-partition `config: PriorityJobFactor=<n>` today. If partition-factor priority becomes a standard profile need, add a typed `policy.priorityJobFactor` field and chart tests. |
| `PriorityWeight*` | Cluster-wide weights for Slurm multifactor priority components: age, association, fairshare, partition, job size, QOS, and TRES. A zero weight makes that factor inert. | Yes. Use `values.schedulingConfig.priorityWeights.*`. | Yes for QOS profiles. `with-qos-preemption` sets nonzero QOS and fairshare weights; baseline and debug/long profiles intentionally avoid multifactor tuning. | Keep this as explicit policy. cxcli should not guess weights for a tenant because the right values depend on queue economics and accounting policy. |
| Fairshare | Slurm accounting fairness based on account/user associations and historical usage. It affects priority only when `PriorityWeightFairshare` is nonzero. | Yes. The chart can render `PriorityWeightFairshare` through `schedulingConfig.priorityWeights.fairshare` and can reconcile association `fairshare` values under `qosConfiguration.associations`. | Partially. QOS-capable profiles enable the fairshare priority weight, but only seed smoke-test-style defaults. | Real fairshare is tenant policy: users, accounts, shares, and reset behavior are not inferable from a node profile. Add accounts/associations in `config.yaml`; for Managed Soperator, coordinate through the managed-service path. |
| QOS | SlurmDBD quality-of-service objects. QOS can affect priority, limits, and preemption, but QOS priority by itself does not define preemption. With `preempt/qos`, explicit QOS `Preempt` relationships are required. | Yes for self-managed clusters when `values.qosConfiguration.enabled=true`; the hook reconciles accounts, QOS rows, and associations, while partition `AllowQos` remains chart-rendered partition policy. The chart also types `PriorityWeightQOS` and QOS preemption settings. | Yes for the `with-qos-preemption` profile. cxcli writes values for standard `debug`, `eval`, `train`, and `data` QOS objects and fails fast if QOS reconciliation is not enabled. | Managed Soperator cannot run this self-managed chart hook in the managed operator namespace. Keep QOS profiles off there and use the Nebius Console/support workflow for managed-service QOS policy. |
| `PreemptType` | Cluster-wide plugin that decides which running jobs are eligible as victims, for example `preempt/partition_prio`, `preempt/qos`, or `preempt/none`. | Yes. Use `values.schedulingConfig.preemptType`. | Yes for QOS profiles. `with-qos-preemption` sets `preempt/qos`. Partition-only profiles set tiers but do not need SlurmDBD QOS objects. | If you want actual tier-based victim selection for debug/long queues, add `schedulingConfig.preemptType: preempt/partition_prio` plus an intentional `PreemptMode` policy. |
| `PreemptMode` and `JobRequeue` | What happens to selected victims: `OFF`, `CANCEL`, `REQUEUE`, `SUSPEND`, `GANG`, or combinations. `REQUEUE` only works for jobs that are requeueable. | Yes. Use `schedulingConfig.preemptMode`, per-partition `policy.preemptMode`, QOS `preemptMode`, and optional `schedulingConfig.jobRequeue`. | Yes where a bundled QOS profile needs it. The QOS profile uses `REQUEUE`; the internal `hidden` ActiveChecks partition can opt out with partition-level `OFF`. | Do not enable `REQUEUE` blindly for long GPU jobs. Use it for checkpointed or restartable work, or submit jobs explicitly with `--requeue`. |
| Niceness / `--nice` | Per-job user modifier applied directly to job priority. It is useful for voluntarily lowering or adjusting a submitted job's priority. | No persistent chart field. | No profile field. | This is intentionally a submit-time job option, not cluster policy. Put it in `sbatch`/`srun` commands, job wrappers, or team runbooks; there is no `PriorityWeightNice` setting to model in Helm. |
| `AccountingStorageEnforce` and `EnforcePartLimits` | Slurm.conf enforcement knobs often paired with QOS/accounting policy. `AccountingStorageEnforce` controls association, limit, QOS, and related accounting enforcement; `EnforcePartLimits` controls whether invalid partition requests fail at submit time. | Yes. Use `values.schedulingConfig.accountingStorageEnforce` and `values.schedulingConfig.enforcePartLimits`; the chart renders the matching Slurm.conf lines and rejects typed/raw overlap. | Yes for QOS profiles. `with-qos-preemption` sets `associations,limits,qos` enforcement and `EnforcePartLimits=ANY` through the typed chart surface. | Keep these values explicit. The chart can type and validate the Slurm.conf keys, but operators still own the accounts, associations, QOS objects, and partition limits that enforcement applies to. |

For raw Slurm behavior, cross-check the official Slurm documentation for
[preemption](https://slurm.schedmd.com/preempt.html),
[QOS](https://slurm.schedmd.com/qos.html),
[multifactor priority](https://slurm.schedmd.com/priority_multifactor.html),
[fair tree / fairshare](https://slurm.schedmd.com/fair_tree.html), and
[sacctmgr](https://slurm.schedmd.com/sacctmgr.html).

The guided partition profiles are intentionally policy-sized:

- `shape-default` creates the default visible worker partition for the selected
  CPU, GPU, or mixed worker layout. It does not add QOS/fairshare policy. When
  ActiveChecks are enabled, cxcli derives their readiness partition and any
  needed internal hidden partition from the selected profile during render
  instead of exposing another queue choice.
- `with-debug-long` adds short `debug` and long `long` queues on the same
  worker capacity. The live validation profile rendered `debug` as the high
  tier short queue and `long` as the low tier seven-day queue. This is a Slurm
  partition policy, not part of the ActiveChecks child chart.
- `with-qos-preemption` adds `debug`, `eval`, `train`, and `data` policy queues
  with QOS/fairshare/preemption settings. Select this only when
  `values.qosConfiguration.enabled=true` is also enabled so the chart hook can
  reconcile the required SlurmDBD QOS rows, accounts, and associations.

After the cluster is provisioned, connect to the Slurm login service and run
Slurm commands from the login node. cxcli-generated bundles set the chart
`clusterName` from the MK8s target id. The examples below use target id
`soperator-cluster1`, namespace `soperator`, and login SSH service
`soperator-cluster1-login-svc`. If your target id or `clusterName` differs,
replace `soperator-cluster1` with that value.

```bash
# Get the LoadBalancer address for the login SSH service.
kubectl get svc soperator-cluster1-login-svc -n soperator
```

Example output; use the `EXTERNAL-IP` value:

```text
NAME                            TYPE           CLUSTER-IP      EXTERNAL-IP    PORT(S)
soperator-cluster1-login-svc    LoadBalancer   10.100.46.154   203.0.113.10   22:31010/TCP
```

Then SSH to the login node with the private key that matches the configured
`slurmNodes.login.sshRootPublicKeys` value. Add `-i <path-to-private-key>` if
that key is not the default key loaded by your SSH agent:

```bash
ssh root@<login-external-ip>
```

Once connected, run the Slurm inspection commands directly:

```bash
# List Slurm partitions and their policies.
# Use this first to confirm which queues exist and which NodeSets they target.
scontrol show partition

# Inspect one partition in detail.
# Look for Nodes, PriorityTier, PreemptMode, DefaultTime, MaxTime, Hidden, and AllowQos.
scontrol show partition debug

# Show global scheduling and preemption settings rendered by Soperator.
# This confirms whether the cluster is using partition-priority or QOS preemption.
scontrol show config | egrep "AccountingStorageEnforce|EnforcePartLimits|PreemptType|PreemptMode|PriorityType|PriorityWeight|JobRequeue|SelectTypeParameters"

# Show Slurm nodes as the scheduler sees them.
# This is useful when a partition exists but jobs stay pending because no nodes are ready.
sinfo -N -l

# Show the queue with job state, partition, node count, and pending reasons.
# Run this while testing preemption or QOS policy.
squeue -l

# Show QOS rows from SlurmDBD.
# This should show only normal for non-QOS profiles and debug/eval/train/data for the QOS profile.
sacctmgr -nP show qos format=Name,Priority,MaxWall,MaxTRES,MaxTRESPerJob,Preempt,PreemptMode

# Show account/user associations, default QOS access, and fairshare values.
# Use this when a job is rejected, pending, or unexpectedly deprioritized.
sacctmgr -nP show assoc format=User,Account,DefaultQOS,QOS,Fairshare

# Show multifactor priority breakdown when the sprio command is available.
# This helps explain why one pending job is ordered ahead of another within the same tier.
sprio -l

# Show a specific job's effective partition, QOS, priority, nice value, and requeue state.
scontrol show job <JOBID>

# Show completed-job accounting.
# Use this after a smoke test or preemption test to confirm partition, QOS, priority, start, and end data.
sacct -j <JOBID> --format=JobID,JobName,Partition,Account,QOS,Priority,Start,End,State
```

Run these smoke checks from the same SSH session on the Slurm login node:

```bash
# CPU profile smoke test. Expected hostname is a worker-cpu pod/node name.
srun -p cpu -N1 -n1 /bin/hostname

# GPU profile smoke test. Use this after GPU workers are ready.
srun -p gpu -N1 -n1 --gres=gpu:1 /bin/hostname

# Debug queue smoke test for the QOS profile.
# This verifies both the debug partition and the debug QOS association.
srun -p debug --qos=debug -N1 -n1 /bin/hostname

# Long queue example. Use --requeue only for jobs that can safely restart.
sbatch -p long --requeue --time=7-00:00:00 --wrap 'hostname; sleep 60'

# QOS policy example for a training queue.
# The requested QOS must exist in SlurmDBD and be allowed for the association.
sbatch -p train --qos=train --requeue --time=04:00:00 --wrap 'hostname; sleep 60'
```

For longer upgrade-policy demonstrations, use the public sample jobs in
[`examples/slurm-jobs/`](examples/slurm-jobs/). These examples are generic:
they test Slurm allocation, job visibility, interruption, wait, cancellation,
and requeue behavior rather than CPU or GPU performance.

```bash
cd examples/slurm-jobs

# Submit 10 CPU jobs to the cpu partition. Defaults to 30 minutes of runtime.
./submit-soperator-smoke.sh --kind cpu --partition cpu --count 10

# Submit 10 GPU jobs to the gpu partition, requesting one GPU per job.
./submit-soperator-smoke.sh --kind gpu --partition gpu --count 10 --gpus-per-job 1
```

By default, Slurm may place multiple sample jobs on one node when the partition
policy permits it. Add `--exclusive` to request one exclusive node allocation
per job where the cluster policy allows that. Use `--run-minutes` and
`--wall-minutes` to change the job duration, `--submit-mode array` for compact
bulk submission, and `--dry-run` to inspect the generated `sbatch` commands.

During Soperator upgrades, use the interactive job policy when you want the
operator to select wait, cancellation, requeue, or other available handling for
each affected running job:

```bash
nebius-cxcli soperator upgrade CONFIG_YAML --target TARGET \
  --to-chart-version TARGET_VERSION \
  --job-policy interactive
```

How to read the common outputs:

- `shape-default` should show only the selected worker-shape partitions:
  CPU-only shows `cpu*`, GPU-only shows `gpu*`, and mixed CPU+GPU shows `cpu*`
  plus `gpu`. The baseline profile does not add QOS/fairshare policy or QOS
  objects; if your cluster shows extra `PreemptType`, `PriorityWeight*`, or QOS
  rows, those came from explicit chart values, image defaults, or
  operator-managed settings. When ActiveChecks are enabled for GPU-capable
  profiles, cxcli can add an internal `hidden` readiness partition during
  render; that partition is profile plumbing, not a user queue.
- `with-debug-long` should add `debug` and `long` partitions. `debug` is the
  high-tier short queue; `long` is the low-tier seven-day queue. Because these
  are partition policies, `sacctmgr show qos` still does not need to show
  `debug` or `long` QOS rows for this profile. Actual preemption for these
  queues requires an explicit `schedulingConfig.preemptType` such as
  `preempt/partition_prio`.
- `with-qos-preemption` should show nonzero `PriorityWeightQOS` and
  `PriorityWeightFairshare`, `PreemptType=preempt/qos`, QOS rows such as
  `debug`, `eval`, `train`, and `data`, and associations that allow users to
  request those QOS values.
- Preemption only works between jobs that compete for the same resources. If
  two partitions map to disjoint NodeSets, they will not preempt each other just
  because one has a higher tier. Use overlapping partitions or QOS policy when
  that behavior is intentional.
- Be careful with `REQUEUE` for long GPU jobs. It is useful for checkpointed or
  restartable work, but it can waste expensive training time for jobs that do
  not handle restart cleanly.

For operator changes, prefer profile-level config first:

- Use `values.partitionProfile` for the bundled baseline, debug/long, or QOS
  policy layouts.
- Use `values.qosConfiguration.enabled=true` only with QOS-capable profiles
  where the chart should manage SlurmDBD accounts, QOS rows, and associations.
- Use direct `config.yaml` Helm values only for advanced overrides that the
  guided profile intentionally does not expose.
- Use `scontrol reconfigure` only for temporary/manual Slurm experiments. If a
  plugin-level change reports that a daemon restart is required, plan a
  maintenance window and make the persistent change through Soperator values.

### External Soperator Onboarding

Use onboarding when the cluster already exists in Nebius MK8s and should not be
Terraform-owned by cxcli:

```bash
nebius-cxcli ext-soperator onboard <config.yaml-or-deployments-root>
```

`CONFIG_OR_DEPLOYMENTS_ROOT` can be:

- an existing project `config.yaml`;
- the project directory containing `config.yaml`;
- a deployments root. In that case pass or answer `--client-name`,
  `--tenant-id`, `--project-id`, and `--region-id` so cxcli can create or
  resolve the canonical tenant/project `config.yaml`.

Interactive onboarding lists existing Nebius MK8s clusters in the selected
project and onboards one cluster per run. It records the selected Nebius
`cluster_id` as the durable access handle, registers a `kind: external-mk8s`
target under `deploy.targets[]`, stores discovered inventory under
`deploy.targets[].inventory.node_groups`, and writes Soperator decisions under
`deploy.targets[].soperator_onboarding`. It does not accept arbitrary vanilla
Kubernetes clusters in the interactive flow; local kube contexts are only access
details for the selected Nebius MK8s cluster.

Non-interactive onboarding uses the Nebius MK8s cluster id as the durable
selector. cxcli generates temporary kubeconfig access from the Nebius API by
default, equivalent to using the cluster's external or internal endpoint:

```bash
nebius-cxcli ext-soperator onboard <config.yaml-or-deployments-root> \
  --client-name <client-name> \
  --tenant-id <tenant-id> \
  --project-id <project-id> \
  --region-id <region-id> \
  --cluster-id <mk8scluster-id> \
  --target-id <logical-target-id> \
  --storage-mode keep-existing-storage \
  --compute-mode keep-existing-compute \
  --to-chart-version <soperator-chart-version> \
  --to-k8s-version <major.minor> \
  --no-interactive
```

When the first argument is an existing project `config.yaml`, the
`--client-name`, `--project-id`, and `--region-id` values can come from that
file instead, and `tenant_id` is optional existing-config metadata unless the
run needs tenant-scoped quota or Capacity Dashboard checks. When the first
argument is a deployments root, pass `--client-name`, `--tenant-id`,
`--project-id`, and `--region-id` explicitly so cxcli can resolve or create the
canonical tenant/project `config.yaml`.

Important onboarding flags:

- `--cluster-id`: Nebius MK8s cluster id to onboard, such as
  `mk8scluster-e00...`. This is the authoritative cluster selector for
  non-interactive onboarding and is saved under `deploy.targets[].cluster_id`.
  cxcli uses it to fetch the cluster endpoint and CA with the Nebius Python SDK,
  then writes a temporary kubeconfig for discovery and later deploy/upgrade
  handoff.
- `--target-id`: optional cxcli logical target id to write under
  `deploy.targets[].instance_id`. It is the id used by app rows, rendered Flux
  target directories, `deploy --target`, and `ext-soperator upgrade --target`.
  It is not the Nebius MK8s `cluster_id`. When omitted, cxcli derives it from
  the live cluster name; use `--target-id` only to choose a clearer or
  collision-free local alias.
- `--kube-context`: optional kubectl context override for discovery. Use it only
  when the workstation already has the intended context and you deliberately
  want cxcli to read through that kubeconfig. By default, non-interactive
  onboarding uses `--cluster-id` and does not require a preexisting kubeconfig
  context.
- `--access`: endpoint to use when generating temporary kubeconfig from
  `--cluster-id`; `external` by default, or `internal` when the workstation has
  a private network path to the MK8s control plane.
- `--storage-mode`: `keep-existing-storage` or `create-aligned-sfs`.
  `keep-existing-storage` preserves discovered live PVC/PV sizes and selectors;
  `create-aligned-sfs` plans aligned SFS when the accepted profile requires
  storage remediation.
- `--compute-mode`: `keep-existing-compute` or
  `create-aligned-node-groups`. `keep-existing-compute` reuses discovered node
  groups; `create-aligned-node-groups` plans cxcli-aligned service and worker
  node groups when upgrade requires them.
- When unsure about storage or compute mode, choose the aligned SFS and
  node-group options; cxcli will keep existing storage or compute automatically
  when the live cluster already satisfies the target Soperator layout.
- `--to-chart-version`: target Soperator chart version for onboarding analysis
  and the accepted external upgrade plan. Defaults to the
  `component_sources.yaml` Soperator chart pin. Interactive onboarding prompts
  with that default; non-interactive runs can pass an exact supported chart
  version. With source validation enabled, non-default versions must resolve
  from the configured Soperator chart source. cxcli persists the value to
  `deploy.targets[].soperator_onboarding.target_version` and the Soperator app
  chart row so `render` and `ext-soperator upgrade` target the same version.
- `--to-k8s-version`: target Kubernetes `major.minor` version for onboarding
  analysis and accepted external MK8s node-template work. Interactive onboarding
  prints the discovered live Kubernetes version and defaults this field to the
  next provider-supported minor hop, for example `1.32 -> 1.33` before `1.34`;
  it does not jump straight to the latest supported minor. When onboarding
  selects `upgrade-external-node-template`, non-interactive runs must pass this
  explicitly.
- `--source-version`: source Soperator version to use when discovery finds
  Soperator CRDs but no compatible Helm release version. Interactive onboarding
  asks the operator to choose a source version from the exact committed
  external-upgrade profile rows or enter one manually. Manual values must match
  an exact row or a known major-generation profile group; unsupported major
  versions fail instead of guessing an upgrade profile. When a canonical
  pinned-target `soperator` release is already present, older same-name
  source-family Helm records are treated as
  informational stale discovery evidence in the saved report and do not force
  source-version confirmation. Discovery enumerates Helm releases across all
  namespaces and stores only Soperator-like releases, so a known Soperator
  release name in a non-standard namespace is reported with its release name,
  namespace, chart, detected version, and matched migration profile instead of
  requiring source-version input.
- `--worker-rollout-strategy`, `--worker-wave-groups`,
  `--worker-wave-percent`, `--max-parallel-worker-groups`,
  `--strategy-max-surge-count`, `--strategy-max-unavailable-count`, and
  `--strategy-drain-timeout`: optional external node-template rollout defaults
  to persist under
  `deploy.targets[].soperator_onboarding.node_template_upgrade.rollout` during
  non-interactive onboarding. They use the same semantics as the upgrade flags:
  `zero-surge` is the default and avoids surge quota but can temporarily reduce
  service or worker capacity, `safe-surge` uses temporary nodes for active
  service groups and worker waves, and verifies the needed quota and capacity
  during `--execute` preflight before mutation.
  `--worker-wave-groups` is the exact fixed worker-group count per safe-surge
  wave; `--worker-wave-percent` scales from the discovered worker-group count;
  `--max-parallel-worker-groups` is only an optional upper cap for percent-based
  waves. `none` waits indefinitely for drain completion, and a finite drain
  timeout can let Nebius delete the node after that timeout when eviction is
  still blocked.
- `--validate-sources` / `--no-validate-sources`: validate selected Soperator
  dependency chart sources before writing `config.yaml`; enabled by default.
  Use `--no-validate-sources` only when the workstation cannot run the source
  validation path, such as disconnected/offline source checks.
- `--allow-unsupported-soperator-upgrade-path`: advanced/testing override for
  Soperator upgrade-path rejections only. It records
  `support_override_used: true` with the matched rule in accepted onboarding
  state and reports. It does not bypass Kubernetes minor-hop validation,
  Nebius API validation, protected-state checks, backup checks, quota checks,
  or other safety preflights.
- `--no-interactive`: disable project cluster selection; use `--cluster-id` to
  identify the Nebius MK8s cluster. `--target-id` and `--kube-context` are
  optional.

The accepted onboarding report controls the next step:

- If no existing Soperator is detected, onboarding prepares the external target
  for a normal cxcli-rendered Soperator install.
- The initial discovery summary is read-only and does not list future upgrade
  phases as live onboarding actions. After the storage and compute modes are
  resolved, onboarding prints the accepted layout decisions explicitly. When
  discovery reports `storage-sfs: target-compatible` and
  `placements: target-compatible`, `keep-existing-storage` means no aligned SFS
  creation or storage data migration is planned, and `keep-existing-compute`
  means no replacement compute node groups or compute migration are planned.
- If an older Soperator or Soperator Helm release with non-standard identity is
  detected and the selected source version matches an exact profile row or
  known major-generation profile, onboarding can mark the state as
  upgrade-supported and record
  `upgrade-soperator`,
  `approve-external-soperator-upgrade`, storage remediation, or compute replacement
  actions.
- If a pinned-target Soperator release is already detected alongside an older
  same-name source-family Helm record, onboarding keeps the target version as
  authoritative and records the older record as informational stale evidence
  for upgrade cleanup rather than as source-version uncertainty or selected
  onboarding work.
- If GPU node groups are discovered, onboarding also records
  `reconcile-target-gpu-stack` and writes the target-scoped standard MK8s GPU
  stack into `config.yaml`: GPU Operator for GPU workers, Network Operator when
  the discovered target is GPU-cluster/RDMA-capable, deploy-time GPU stack and
  GPU visibility validations, and explicit acceptance benchmark settings for NCCL.
  On reruns, discovery also inspects the
  live GPU Operator and Network Operator Helm releases, NVIDIA ClusterPolicy
  and NicClusterPolicy readiness, scheduler-visible GPU/RDMA resources, and
  Nebius driver labels. Healthy evidence is reported as `gpu-stack: verified`;
  the selected action then means cxcli will adopt/reconcile desired state and
  keep deploy-time validation reports, not that remediation is currently
  missing. A source cluster that lacks scheduler-visible `rdma/*` resources is
  recorded as `gpu-rdma: validation-planned`, not as a vague placeholder
  action. Target GPU stack reconciliation alone is not external-upgrade-owned
  work; if no Soperator chart, storage, compute, or external node-template
  upgrade action is selected, `ext-soperator upgrade` fails fast and `render` followed by plain
  `deploy <config.yaml>` applies the generated desired state. Use `deploy
  --target <target-id>` only when you intentionally want to narrow one local
  run.
- If discovery is incomplete or incompatible, onboarding records concrete
  action-required findings such as `source-version-required` and does not
  silently take over the cluster.

Onboarding asks for two independent layers. Storage mode is
`keep-existing-storage` or `create-aligned-sfs`; compute mode is
`keep-existing-compute` or `create-aligned-node-groups`. Keeping existing
storage means cxcli will not plan aligned SFS creation. Keeping existing
compute preserves the discovered node groups and only maps Soperator placements
onto them. The default placement proposal maps `worker` onto GPU node groups and
`system`, `controller`, `login`, and `accounting` onto CPU node groups, then lets
the operator override `apps.charts[].placements.*` before render. When existing
Soperator placement labels are present, onboarding writes `apps.charts[].placements.*`
from the live node-group ids so service-role pods keep scheduling onto the
adopted system, controller, login, accounting, and worker groups. If live
worker labels distinguish `worker-cpu` and `worker-gpu`, onboarding selects the
mixed Soperator profile and writes worker-specific
`apps.charts[].placements.worker-cpu` and
`apps.charts[].placements.worker-gpu` entries so render keeps the adopted worker
NodeSet names and partition references instead of creating synthetic worker
NodeSets from raw node-group ids. Render compiles those placements into
chart-native `k8sNodeFilters`, `slurmNodes.*.k8sNodeFilterName`, storage
selectors, partition refs, and worker `nodesets[]`. Onboarding also samples `lscpu -J` from one
running `slurmd` pod per worker NodeSet and preserves the normalized
CPU/socket/core/thread topology in the adopted `values.nodesets[].nodeConfig.static`
values, while leaving chart-owned worker image tags to the target Soperator
chart defaults. Rerun onboarding before render if the external worker shape
changes.
Adopted Soperator values also make Pyxis optional and clear the importer path
so a legacy or incompatible Pyxis importer option does not prevent `slurmd`
from starting during chart takeover.
Chart-managed MariaDB defaults to `compute-csi-default-sc` with
`ReadWriteOnce` storage during adoption. If discovery finds an existing
MariaDB PVC, onboarding uses that live PVC's storage class, access mode, and
largest observed size instead of rendering the shared Slurm filesystem storage
class for the accounting database.
When an existing Soperator release is adopted, onboarding also preserves the
live `SlurmCluster` resource name as `values.clusterName` so render/deploy
continues managing and validating the adopted cluster instead of creating a
new target-named SlurmCluster.

For `keep-existing-storage`, discovered storage sizes are lower bounds. cxcli
records the largest live value it sees for each adopted jail, controller-spool,
and accounting volume across PVC request, PVC capacity, and PV capacity, and
keeps those explicit values through config pruning. Render/deploy must not
request a smaller PVC/PV size for adopted storage; Nebius and Kubernetes storage
resize paths are expansion-only.

`ext-soperator onboard` is read-only against live cluster state: it does not
create or attach SFS filesystems, drain nodes, run copy jobs, mutate Soperator
CRs, or change the external cluster lifecycle. It updates only local project
artifacts:

- `config.yaml`, including the external target, app row, fingerprint,
  `source_version`, `target_version`, `migration_profile_id`, selected actions,
  storage/compute modes, target GPU operator app rows when GPU workers are
  discovered, and deploy-time MK8s GPU deployment-testing settings;
- `generated/reports/soperator-discovery/<target>/manifest.json` plus section
  files, containing the support-safe source discovery snapshot and the accepted
  analysis.

After onboarding succeeds, validate and render the accepted target:

```bash
nebius-cxcli validate <config.yaml>
nebius-cxcli render <config.yaml>
```

If the accepted onboarding report says no external-upgrade-owned work is required,
deploy the rendered desired state:

```bash
nebius-cxcli deploy <config.yaml>
```

Plain `deploy <config.yaml>` reconciles every generated target by default. Use
`deploy --target <target-id>` only when you intentionally want to narrow local
Flux/app work and deploy-time validations to one target. The selector is the
cxcli target id that onboarding wrote under `deploy.targets[].instance_id`, not
the Nebius MK8s `cluster_id`.

If the accepted onboarding report says external-upgrade-owned work is required, do not
run `deploy` before the external upgrade. The decision is based on the selected
`deploy.targets[].soperator_onboarding.actions` list, not on storage and
compute modes alone: a target can keep existing storage and compute but still
require upgrade work for `upgrade-soperator` or `upgrade-external-node-template`.
`deploy` applies the rendered Terraform/Flux desired state and now refuses
selected external-upgrade-required Soperator onboarding targets before starting
preflight or apply work; the guard checks both the rendered manifest runtime
config and the current source `config.yaml` so older rendered bundles fail
closed. `ext-soperator upgrade --execute` must first verify the live source
release, create a restore-capable backup whose restore path is only a
new/replacement cluster and not the original source cluster, and then perform
checkpointed phases such as the accepted external node-template Kubernetes hop
through ad hoc Nebius API calls, target chart apply, cutover, and validation
hold. Each executed stage runs a fast
stage-scoped verification before the next stage starts; a failed stage
verification leaves that same phase pending and records the details in the
checkpoint and external upgrade report. Use `ext-soperator upgrade` for
resume/rerun while external-upgrade-owned actions remain selected. A completed
locked segment reports `Pending phase: none`, updates checkpoint
`completed_segment_ids`, and keeps accepted onboarding in place while later
locked segments remain. After the final locked segment completes,
`generated/reports/ext-soperator-upgrade-report.md` reports `Pending phase:
none` and cxcli refreshes `config.yaml` from live post-upgrade discovery when it
can, so the selected actions become deploy-owned for the next normal
reconciliation. If the report still shows any pending phase other than `none`,
rerun the same `ext-soperator upgrade ... --execute --approve` command. If the
final report shows `Pending phase: none` but the post-upgrade config refresh was
skipped, rerun `ext-soperator onboard` only as an intentional repair path,
rerun `render`, then use `deploy` only for normal rendered reconciliation if
needed.

The external Soperator steady-state handoff is:

1. `ext-soperator onboard` discovers the live cluster, decides whether the
   accepted `deploy.targets[].soperator_onboarding.actions` list contains
   external-upgrade-owned work, and locks the full path under
   `deploy.targets[].soperator_onboarding.upgrade_path`.
2. If external-upgrade-owned actions are selected, `deploy` is blocked and the next
   command is `ext-soperator upgrade`; deploy cannot perform the ad hoc Nebius
   API mutation phases.
3. Each `ext-soperator upgrade --execute --approve` run advances one locked
   segment. If more segments remain, cxcli keeps onboarding in place and prints
   the next same-command invocation.
4. When the final locked segment completes and
   `generated/reports/ext-soperator-upgrade-report.md` shows `Pending phase: none`, cxcli
   performs live post-upgrade discovery and rewrites both `config.yaml` and
   the `generated/reports/soperator-discovery/<target>/` bundle into
   the deploy-owned onboarding shape.
5. After that handoff, normal day-2 Soperator changes use the rendered desired
   state path: edit `config.yaml`, run `render`, then run `deploy`. Run
   `validate` before render when you want the normal pre-render checks.
6. Rerunning `ext-soperator onboard` after a completed external upgrade is read-only.
   It should rediscover the target layout and keep the target on the deploy
   path; if live discovery or provider inventory still reports external-upgrade-owned
   work, cxcli keeps the upgrade path selected conservatively.

When target GPU stack reconciliation is selected together with an external-upgrade-required
action, upgrade applies that reconciliation as a checkpointed phase before
Soperator compute/cutover work. For preserved worker NodeSets, upgrade derives
Slurm CPU/GPU topology from live worker inventory and one representative worker
pod per NodeSet, not from fixed platform assumptions. The validation
hold runs the required MK8s node inventory smoke plus the target-scoped optional
`deploy.targets[].deployment_testing.mk8s_gpu.*` checks from `config.yaml`, including
operator readiness and GPU visibility when those checks are enabled.
NCCL/performance validation is reserved for explicit `acceptance-test
benchmark` runs. The hold also runs the
required Soperator deployment snapshot after bounded first-run storage/pod
startup: `soperator-manager` Deployment availability, jail storage object
visibility, Pending Soperator pod/event diagnostics, target `SlurmCluster`
visibility, and worker `NodeSet` visibility using the public Soperator CRDs.
Explicit `acceptance-test smoke --suite slurm` runs the Slurm CLI and all-node
Slurm smoke checks later; Slurm nodes reported as `inval` remain unhealthy
there. The
upgrade run writes `generated/reports/ext-soperator-upgrade-report.md` and
`generated/reports/ext-soperator-upgrade-report.json` with phase, remediation,
stage verification, upgrade, layout, validation, Slurm decision, backup, and event summaries,
including the MK8s GPU rollup; it also refreshes
`generated/reports/deploy-report.md` as a secondary deploy-compatible MK8s GPU
summary:

```bash
nebius-cxcli ext-soperator upgrade <config.yaml> --target <target> --dry-run
nebius-cxcli ext-soperator upgrade <config.yaml> \
  --target <target> \
  --execute \
  --approve
```

Use the same cxcli target id for upgrade that onboarding wrote under
`deploy.targets[].instance_id`. For example, if onboarding wrote target id
`external-cluster`, the matching dry run is:

```bash
nebius-cxcli ext-soperator upgrade <config.yaml> --target external-cluster --dry-run
```

Do not pass the raw Nebius MK8s `cluster_id` to `--target` unless that exact
value is also the configured cxcli target id.

The accepted fingerprint is checked against deterministic Soperator defaults
that runtime normalization materializes, so day-2 app edits and Soperator Helm
chart version edits do not invalidate an accepted onboarding plan. In
multi-target configs, each `apps:soperator` onboarding row must map to the
matching `kind: external-mk8s` `deploy.targets[]` row; cxcli does not collapse
all onboarding rows onto the first external target.
Rerunning `ext-soperator onboard` is safe and refreshes the source discovery
bundle without mutating the cluster. For Nebius `--cluster-id` onboarding,
cxcli enriches the single bulk Kubernetes node inventory with Nebius
control-plane and node-group template inventory by node group, including
Kubernetes version, node OS image, and GPU driver preset. If a previous
upgrade attempt already aligned every discovered node group and the control
plane, onboarding records `mk8s-node-template: target-compatible` and omits the
`upgrade-external-node-template` action; missing, partial, or errored provider
inventory keeps that action selected so `ext-soperator upgrade` can verify and
resume conservatively.

Non-interactive `component add apps:soperator@<target>` also selects onboarding
automatically when `<target>` is an existing external MK8s target. It remains a
target-scoped compatibility path and does not create Terraform-managed MK8s/SFS
rows. The canonical initial onboarding command is `nebius-cxcli ext-soperator
onboard <config.yaml-or-deployments-root>`, which also ensures the required
target-scoped Soperator app dependencies exist on that same target.

### External Soperator Upgrade

Use upgrade only after `ext-soperator onboard` has written an accepted Soperator
analysis:

```bash
nebius-cxcli ext-soperator upgrade <config.yaml> --target <target> --dry-run
```

The command reads `generated/reports/soperator-discovery/<target>/manifest.json`,
validates the accepted onboarding analysis, and prints the target remediation,
layout migration, and selected upgrade plan.
`--dry-run` is the default and makes no cluster changes.
The selected `deploy.targets[].soperator_onboarding.actions` list is the
desired external upgrade contract. If an action is absent, for example no
`create-aligned-sfs` or `plan-soperator-compute-migration` action after the
analyzer selected `keep-existing-storage` and `keep-existing-compute`, upgrade
does not invent that layout work on rerun. The dry-run plan labels those
preserved-layout choices as `keep-existing-storage-layout` and
`keep-existing-compute-layout` so they are not confused with Kubernetes or
Soperator upgrade work. Conversely, keep-existing storage and compute do not
make the target deploy-owned when other external-upgrade-owned actions remain,
such as Soperator chart upgrade or external MK8s control-plane/node-template
upgrade.

Execution is deliberately gated:

```bash
nebius-cxcli ext-soperator upgrade <config.yaml> \
  --target <target> \
  --execute \
  --approve
```

External upgrade follows these stages:

1. Plan and dry run: load `config.yaml`, read the accepted discovery bundle,
   validate the target's `deploy.targets[].soperator_onboarding.actions`, and
   print only the external-upgrade-owned work selected by onboarding.
2. Execute preflight: refresh live discovery, verify the source release and
   onboarding fingerprint, require `--approve`, create or reuse a
   restore-capable backup for new/replacement-cluster restore only, capture
   protected customer state, check quota and capacity for net-new
   storage/compute, verify selected worker-node health, and apply the Slurm
   `--job-policy` decision before any affected worker rollout.
3. External infrastructure remediation: upgrade the external MK8s control plane
   first to the accepted Kubernetes target for this run when selected, then
   service-role node groups serially, worker node groups with zero-surge or
   safe-surge waves, target GPU stack reconciliation when it is paired with
   upgrade work, aligned SFS creation/attachment, and guarded PVC data-copy
   phases. External node-template work is one Kubernetes minor hop per
   `ext-soperator upgrade` run; later Kubernetes hops come from the same locked
   path and advance with later `ext-soperator upgrade --execute --approve` runs.
4. Soperator takeover and cutover: apply target Soperator CRDs and chart values,
   preserve discovered worker NodeSets and partition refs when the source proves
   them, normalize source-era runtime settings, suspend or retire legacy source
   Flux/Helm records, and hold destructive old-storage retirement behind
   explicit checkpoints.
5. Validation hold: verify external MK8s control-plane and node-group readiness,
   target Soperator Helm release and rendered workloads, MK8s inventory/GPU
   checks configured for the target, required Soperator deployment snapshot,
   protected-state before/after deltas, and the shared bounded fast safety
   verifier. Heavy Slurm/NCCL/performance checks remain explicit
   `acceptance-test` or manual follow-ups.
6. Segment completion: write `ext-soperator-upgrade-report.md` and JSON,
   checkpoint any pending phase, and when the segment reports `Pending phase:
   none`, record `completed_segment_ids` and print the next same-command
   invocation if locked segments remain.
7. Final handoff: after the last locked segment reports `Pending phase: none`,
   refresh `config.yaml` plus the discovery bundle into the deploy-owned shape
   so future normal reconciliation is `validate`, `render`, then `deploy`.

Important external upgrade flags:

- `--target`: required when more than one onboarded Soperator target exists;
  otherwise the only onboarded target is selected. This is the cxcli target id
  from `deploy.targets[].instance_id`, not the Nebius MK8s `cluster_id`.
- `--backup-dir`: directory for the restore-capable backup created before the
  first mutation. The default is `<config.yaml parent>/backups`.
- `--dry-run` / `--execute`: print the plan without live changes, or request
  live execution.
- `--approve` / `--no-approve`: record customer approval for the accepted
  external upgrade plan. Mutating phases require approval.
- `--approve-remediation` / `--no-approve-remediation`: record operator
  approval for `remediation_required` protected-state deltas reported by the
  shared verifier. Deltas classified as `blocked` still stop the run and cannot
  be overridden by this flag.
- `--allow-unsupported-soperator-upgrade-path`: allow a previously accepted
  unsupported or not-validated Soperator upgrade path to execute after
  `--execute --approve`. This flag only bypasses the Soperator support-policy
  rejection; Kubernetes skipped-minor validation and all existing safety
  preflights still fail closed.
- `--job-policy interactive|wait|fail|cancel-selected|cancel-all|requeue-selected|requeue-all|requeue-hold-selected|requeue-hold-all`:
  decide how to handle Slurm jobs before cxcli mutates Soperator worker pods.
  Managed `soperator upgrade` checks selected underlying MK8s nodes before
  node-template rollouts and checks all live worker NodeSets before Soperator
  chart reconciliation, including chart-only upgrades. External
  `ext-soperator upgrade` checks affected external node-template nodes and all
  live worker NodeSets before target chart reconciliation or worker NodeSet
  recreation. Local `deploy` and `flux apply` use the same policy flags before
  applying rendered Soperator Flux resources for a target that already has a
  live SlurmCluster, and skip the gate only for first install when no live
  SlurmCluster exists yet.
  `interactive` shows the affected jobs and asks the operator, `wait` polls
  until jobs finish, `fail` stops before mutation, the cancel policies call
  `scancel`, the requeue policies call `scontrol requeue`, and the
  requeue-hold policies call `scontrol requeuehold`. Requeue and requeue-hold
  policies wait for the selected jobs to leave affected nodes before rollout
  continues. If cxcli cannot map the affected Kubernetes nodes to Slurm node
  names, or Slurm rejects the scoped node filter, the upgrade fails before any
  job action instead of querying or mutating an unfiltered cluster-wide job
  list.
- `--cancel-job`: job id to cancel when `--job-policy cancel-selected` is used.
  Repeat the flag for multiple jobs.
- `--requeue-job`: job id to requeue when `--job-policy requeue-selected` or
  `--job-policy requeue-hold-selected` is used. Repeat the flag for multiple
  jobs. Jobs must be requeueable by Slurm, and cxcli still stops if any selected
  job keeps running on an affected node. Held requeued jobs remain held until an
  operator runs `scontrol release <jobid>`.
- `--job-wait-timeout` and `--job-refresh-interval`: bound and refresh the
  `wait` policy. While waiting, cxcli shows elapsed time and the longest known
  Slurm remaining time from `squeue`.

Interactive Slurm job actions map to these Slurm commands:

- Cancel one job: `scancel <jobid>`.
- Cancel all blocking jobs on affected nodes: `scancel <jobid>...` for the job
  ids cxcli read from `squeue -w <node-list>`.
- Requeue one job: `scontrol requeue <jobid>`.
- Requeue all blocking jobs on affected nodes: `scontrol requeue <jobid>...` for
  the job ids cxcli read from `squeue -w <node-list>`.
- Requeue and hold one job: `scontrol requeuehold <jobid>`.
- Requeue and hold all blocking jobs on affected nodes:
  `scontrol requeuehold <jobid>...` for the job ids cxcli read from
  `squeue -w <node-list>`.
- Release one held job after the upgrade: `scontrol release <jobid>`.
- Release all admin-held pending jobs after review:
  `squeue --states=PD -h -o '%A|%r' | awk -F'|' '$2 == "JobHeldAdmin" { print $1 }' | xargs -r scontrol release`.
- `--worker-rollout-strategy zero-surge|safe-surge`: select the external
  node-template rollout strategy. `zero-surge` is the default and avoids surge
  quota, but can reduce active service or worker capacity during the rollout.
  `safe-surge` uses temporary nodes for active service groups and worker waves,
  and checks the required quota and capacity before mutation.
- `--worker-wave-groups`: exact fixed number of worker groups to update per
  safe-surge wave.
- `--worker-wave-percent` and `--max-parallel-worker-groups`: percent-based
  safe-surge wave sizing plus an optional upper cap. `--worker-wave-percent` is
  a percentage of worker groups, not worker nodes. Do not combine
  `--max-parallel-worker-groups` with `--worker-wave-groups`; the fixed group
  count is already the concurrency limit.
- `--strategy-max-surge-count`, `--strategy-max-unavailable-count`, and
  `--strategy-drain-timeout`: configure the Nebius node-group strategy inside
  each active service or worker group. zero-surge defaults are `0`, `1`, and `30m`;
  safe-surge defaults are `1`, `0`, and `30m`. Use
  `--strategy-drain-timeout none` to wait indefinitely for drain completion;
  a finite timeout can let Nebius delete the node after that timeout when drain
  is still blocked.

The same rollout contract is persisted under the target onboarding block:

```yaml
deploy:
  targets:
    - instance_id: external-cluster
      soperator_onboarding:
        node_template_upgrade:
          rollout:
            strategy: zero-surge
            worker_group_strategy:
              max_surge_count: 0
              max_unavailable_count: 1
              drain_timeout: 30m
```

When onboarding accepts a multi-hop external Kubernetes target, the same target
also stores the locked path. `node_template_upgrade.target_k8s_version` records
the accepted final target; `ext-soperator upgrade` derives the per-run effective
target from the next incomplete segment:

```yaml
deploy:
  targets:
    - instance_id: external-cluster
      soperator_onboarding:
        node_template_upgrade:
          target_k8s_version: "1.34"
        upgrade_path:
          schema: nebius-cxcli-ext-soperator-upgrade-path/v1
          locked: true
          source_k8s_version: "1.31"
          target_k8s_version: "1.34"
          source_soperator_version: "1.22.3"
          target_soperator_version: "4.0.2-ps.3"
          support_rule_id: k8s-1-33-soperator-4-supported
          segments:
            - id: segment-1-kubernetes-1-31-1-32-soperator-1-22-3-4-0-2-ps-3
              current_k8s_version: "1.31"
              target_k8s_version: "1.32"
              actions:
                - approve-external-soperator-upgrade
                - upgrade-external-node-template
                - upgrade-soperator
            - id: segment-2-kubernetes-1-32-1-33
              current_k8s_version: "1.32"
              target_k8s_version: "1.33"
              actions:
                - approve-external-soperator-upgrade
                - upgrade-external-node-template
            - id: segment-3-kubernetes-1-33-1-34
              current_k8s_version: "1.33"
              target_k8s_version: "1.34"
              actions:
                - approve-external-soperator-upgrade
                - upgrade-external-node-template
```

For a capacity-preserving safe-surge rollout, set:

```yaml
deploy:
  targets:
    - instance_id: external-cluster
      soperator_onboarding:
        node_template_upgrade:
          rollout:
            strategy: safe-surge
            worker_wave_percent: 1
            # Optional cap only for percent-based waves:
            # max_parallel_worker_groups: 10
            # Or set an exact fixed wave size instead:
            # worker_wave_groups: 10
            worker_group_strategy:
              max_surge_count: 1
              max_unavailable_count: 0
              drain_timeout: 30m
```

Before the first mutation, `--execute --approve` refreshes live discovery,
verifies the accepted onboarding fingerprint and source release, creates a
restore-capable backup, captures shared protected customer state, and enriches
the accepted inventory from live Nebius MK8s node-group names and Kubernetes
node labels. cxcli then auto-detects
source worker node groups from console-visible Nebius
node-group names plus `slurm.nebius.ai/nodeset` worker labels such as
`worker-gpu` or `worker-cpu`. For approved execution, cxcli also runs a strict
net-new upgrade quota preflight before any SFS or node-group mutation:
aligned SFS filesystems that do not already exist are counted as spare storage
required during data copy, and target service-role node groups that do not
already exist are counted as net-new compute capacity. Existing worker node
groups are preserved in place. For external node-template work, the default
strategy is zero-surge: it avoids surge quota but may reduce active service or
worker capacity during the rollout. When operators choose safe-surge, cxcli
counts `max_surge_count` temporary surge node(s) for each active service group
or worker group in the active wave, checks the required spare quota and GPU
capacity before mutation, requires all selected worker nodes to start Ready and
schedulable, checks Slurm jobs on affected external node-template workers, and
checks all live worker NodeSets before target Soperator chart reconciliation or
worker NodeSet recreation.
Confirmed shortages, unresolved live limits, coverage gaps, or quota lookup
errors stop upgrade before mutation. The local `.nebius-cxcli/ext-soperator-upgrades/`
timeout-guarded checkpoint records the resolved source worker groups and quota
preflight result for resume and is ignored by cxcli-managed deployments
`.gitignore` files.

The planned phases depend on the accepted storage and compute modes:

- `reconcile-target-gpu-stack` applies or reconciles the target-scoped GPU
  Operator and, when selected, Network Operator app rows. For no-upgrade
  targets this happens through normal render/deploy; use `deploy --target
  <target-id>` only when a deliberate one-target local run is needed. When
  another external-upgrade-required action is selected, the same app rows are applied
  during the `target-gpu-stack-remediation` phase before Soperator
  compute/cutover work. If live discovery already verifies the GPU stack,
  onboarding reports that evidence and still keeps the action selected for
  desired-state ownership and deploy-time validation output.
- `keep-existing-storage` removes aligned-SFS creation, bulk data sync, and
  storage cutover phases from the plan. When existing chart-owned PVs are
  present, upgrade preserves their live nodeAffinity selectors in the target
  Helm values so chart takeover does not attempt an immutable PV selector
  update, and preserves the largest discovered PVC/PV size so chart takeover
  never attempts to shrink adopted storage.
- `create-aligned-sfs` creates or reuses aligned jail, controller-spool, and
  accounting SFS filesystems, attaches them to discovered Nebius node groups,
  runs Kubernetes data-copy Jobs when old and target PVC pairs exist, and holds
  old storage retirement for explicit confirmation. Quota must cover this
  spare target storage while source storage remains mounted for data copy.
- `keep-existing-compute` keeps discovered node groups and maps roles onto
  them. When the source discovery bundle contains worker NodeSets such as `worker-gpu`
  and `worker-cpu`, upgrade renders those source NodeSet names and source
  partition references into the target chart instead of creating a synthetic
  merged `worker` NodeSet, then clears stale source-era camelCase
  `ephemeralStorage` resource keys from the adopted worker NodeSet CRs so the
  target operator creates valid Pods. Completed-checkpoint reconciliation waits
  for those worker NodeSets to report desired-ready replicas before returning
  `Pending phase: none`.
- `create-aligned-node-groups` creates or reuses aligned service-role node
  groups for `system`, `controller`, `login`, and `accounting`, while mapping
  worker NodeSets onto the existing detected worker node groups. It does not
  create duplicate worker groups or require 2x worker quota. Upgrade-owned
  external node-group template changes, including Kubernetes version, node OS
  image, Nebius-image GPU stack, and aligned SFS filesystem attachments, use
  direct Nebius node-group updates: service-role groups are serial, zero-surge
  quiesces login workloads, one-node service workloads, and known drain-blocking
  webhook replicas, and safe-surge uses one temporary replacement node per
  active service or worker group when selected. Worker
  groups default to zero-surge and can use bounded safe-surge waves. cxcli
  restores each node group's original strategy after the active rollout.

The executor runs the supported phases in order. It can apply target-scoped GPU
and Network Operator app rows plus the same catalog-owned post-render patches
that Flux would apply, create or reuse aligned SFS resources, create or reuse
aligned service-role MK8s node groups, verify a Slurm quiet window from a login
pod, apply the pinned target Soperator chart values mapped to preserved worker
groups, normalize target Slurm plugin runtime settings, recreate target worker
Kruise StatefulSets when source-era specs cannot be mutated in place, validate
Soperator reconciliation, and hold old storage retirement for explicit
confirmation. The validation hold also runs the configured deploy-time MK8s GPU
readiness/CUDA checks, the required Soperator deployment snapshot with
Soperator manager, jail storage, Pending pod/event, SlurmCluster, and NodeSet
visibility checks, and the shared read-only fast safety verifier. That verifier
checks expected pod phases, protected PVCs, `/home` mount evidence, ActiveChecks
restore state, observability-agent workload evidence when present, zero-downtime
blocking evidence, and protected-state before/after deltas. Slurm jobs,
backend metrics/log ingestion, Terraform drift review, and NCCL/performance work
are reported as heavy/manual follow-ups unless an existing validation path owns
them; the upgrade run writes
`generated/reports/ext-soperator-upgrade-report.md` with MK8s GPU and Soperator/Slurm
validation rollups plus phase and event summaries, writes
`generated/reports/ext-soperator-upgrade-report.json`, and also refreshes
`generated/reports/deploy-report.md` as a secondary deploy-compatible MK8s GPU
summary. During chart takeover, cxcli suspends legacy Flux HelmReleases
that would otherwise reconcile the old Soperator release, applies Soperator CRDs
with server-side conflict resolution, and retries bounded admission-webhook
startup races after the target controller/webhook is created. After the target
chart is healthy and before validation-and-rollback hold, cxcli deletes those
suspended source-family Flux HelmRelease records and legacy source-family
ActiveChecks CronJobs/jobs/pods so stale old-chart desired state no longer
appears in discovery or smoke validation. It does not drain
or delete preserved worker node groups as part of a synthetic parallel-worker
replacement.
If a legacy external cluster has Soperator ActiveChecks or
`wait-for-active-checks` enabled, treat them as a maintenance-window concern:
they can consume GPU/RDMA capacity or extend readiness waits. CXCLI managed
`soperator upgrade` handles cxcli-owned ActiveChecks with the checkpointed
suspend/restore lifecycle described above, and external upgrade removes stale
source-family check workloads during takeover, but cxcli does not silently
disable arbitrary live external ActiveChecks before upgrade because those
settings may be operator-owned diagnostics. Disable external diagnostics
deliberately before the external upgrade window unless they are part of a
cxcli-managed upgrade checkpoint.
After a mutating phase starts, resume relies on phase checkpoints because the
original full discovery fingerprint is expected to change as new storage,
attachments, and target node groups appear.
Reruns are action-idempotent rather than checkpoint-only: before skipping a
completed action phase, `ext-soperator upgrade --execute` rechecks the
corresponding live state. External node-template phases compare the live MK8s
control plane and source node groups against the target Kubernetes version, OS
image, and GPU driver preset; target GPU reconciliation checks the selected GPU
Operator and Network Operator Helm releases; aligned-SFS phases verify the
filesystems and node-group attachments; final cutover verifies the target
SlurmCluster and expected NodeSets. If a completed action is no longer
satisfied, cxcli removes that phase from the local completed set and reruns the
existing phase handler. During execute, every completed stage also records a
stage-scoped `fast_verification` checkpoint and the external upgrade report
includes a `Stage Fast Verification` rollup plus the JSON `stage_verification`
array. Before validation hold and again before reporting completion, cxcli
verifies the target Soperator Helm release and rendered workloads, records the
final post-upgrade MK8s and Helm readiness checks in the same stage-verification
report, then suspends old source-family Flux Kustomization desired state,
deletes suspended old source-family Flux HelmRelease records, and retires stale
Helm release records while preserving shared/storage/custom resources. This completion path
also verifies the external MK8s control plane and discovered Nebius node-group
provider readiness before the Helm checks; when onboarding selected external
MK8s node-template changes, that MK8s check also verifies the requested
Kubernetes version, OS image, and GPU driver preset. If a Nebius node-group
update command times out after the request is accepted, cxcli re-reads the live
node group: ready matching state continues, still-rolling state is checkpointed
as a pending external-node-template phase, and the same execute command resumes
without starting a duplicate update. Data-copy and old
infrastructure retirement phases remain guarded by their explicit checkpoints
because rerunning them can have customer-data or teardown impact.

Approved `--execute` runs show an interactive spinner while live preflight and
mutating phases are active, emit concise `External Soperator upgrade phase ...`
comments for preflight, backup metadata lookup/reuse, backup archive creation,
protected-state capture, final post-upgrade checks, and report writing, and log
phase-aware `External Soperator upgrade status` lines in non-interactive
output. Every status line starts with the elapsed time, canonical phase id,
operator-facing top-level stage (`MK8s Node Upgrades` or `Soperator Upgrade`),
human-readable phase label, and overall phase health before component details,
so a single copied line is enough to identify the active stage and phase.
Storage phases show aligned
SFS/PVC copy progress plus MK8s and Slurm serving/degradation signals. Compute
and cutover phases show MK8s status as separate `Node groups:` and `Nodes:`
sections: node-group readiness stays in the first section, while node-level
rollout transitions such as
`replacing (cordoned)` and real problem-node details such as `NotReady (down)`
stay in the second section. Transition nodes and down states are highlighted in
terminal output, while large clusters stay summarized with `+N more` suffixes.
Slurm worker names/states, queue health, and Soperator SlurmCluster
reconciliation stay adjacent component details. These signals are best-effort
progress and degradation indicators, not a no-downtime guarantee.
Phases complete only when their live prerequisites are absent or satisfied;
otherwise cxcli writes the pending phase and reason to the checkpoint for the
next explicit action.

### Soperator Cluster Upgrade

A cxcli-managed Soperator cluster upgrade can involve the underlying MK8s
cluster/node groups, the Soperator Helm chart/app row, or both. Use
`nebius-cxcli soperator upgrade` as the canonical maintenance-window command:
pass `--to-k8s-version`, `--to-os`, or `--to-gpu-stack-preset` only when the
MK8s node-template should change, and pass `--to-chart-version` only when the
Soperator chart should change.

Use `upgrade node-template` separately only when you intentionally want a
standalone Terraform-managed MK8s upgrade outside the Soperator maintenance
window:

```bash
nebius-cxcli upgrade node-template <config.yaml> infra:mk8s@<target> \
  --to-version <major.minor> \
  --to-os <os> \
  --to-gpu-stack-preset <cuda...> \
  --dry-run
nebius-cxcli upgrade node-template <config.yaml> infra:mk8s@<target> \
  --to-version <major.minor> \
  --to-os <os> \
  --to-gpu-stack-preset <cuda...>
```

Use `soperator upgrade` when cxcli already manages the Soperator app row and the
target is already part of the rendered bundle. It owns the full two-layer
maintenance window when MK8s target flags are supplied:

```bash
nebius-cxcli soperator upgrade <config.yaml> \
  --target <target> \
  --to-chart-version <chart-version> \
  --to-k8s-version <major.minor> \
  --dry-run
nebius-cxcli soperator upgrade <config.yaml> \
  --target <target> \
  --to-chart-version <chart-version> \
  --to-k8s-version <major.minor>
```

For Kubernetes minor changes, run provider-supported hops rather than skipping
intermediate target versions. For example, upgrade a managed cluster from
`1.31` to `1.34` as `soperator upgrade --to-k8s-version 1.32`, then
`--to-k8s-version 1.33`, then `--to-k8s-version 1.34`. cxcli rejects
`1.31 -> 1.33` and `1.31 -> 1.34` requests and prints the next valid hop.
Managed upgrades do not persist a locked multi-run path because `config.yaml`
plus live MK8s state are the source of truth for each requested run. cxcli does
still enforce the committed Soperator/Kubernetes recommended order: if a
cluster is already at the Soperator staging Kubernetes minor, for example
`1.32`, and the next requested hop would cross to `1.33` while the Soperator
chart is still old, `soperator upgrade` blocks the combined run and prints a
chart-first command. Run the Soperator chart upgrade while Kubernetes stays at
`1.32`, then rerun `soperator upgrade --to-k8s-version 1.33`, and later
`--to-k8s-version 1.34` as separate provider-supported hops.

Before mutation, managed and external Soperator upgrade flows evaluate the
committed Soperator/Kubernetes support policy from
`soperator_migration_profiles.yaml`. `unsupported` and `not_validated` paths
fail fast unless `--allow-unsupported-soperator-upgrade-path` is passed; the
override is recorded in reports and checkpoints, and does not bypass
Kubernetes minor-hop validation or other safety preflights. Paths marked
`supported_with_warning` continue without the override but keep the warning in
the dry-run and execution plans. Paths with no committed support rule are
treated as `not_validated`.

CXCLI-managed Soperator upgrade follows these stages:

1. Plan and dry run: resolve the target Soperator row, requested chart version,
   requested MK8s node-template fields, active source-catalog pins, and repeatable
   dry-run/execute command from the current generated bundle.
2. Preflight and backup: validate the current bundle, inspect live Soperator and
   Slurm state, create a restore-capable backup with raw Kubernetes restore
   material and chart-managed MariaDB accounting dump, capture protected customer
   state and config fingerprints, and checkpoint any cxcli-owned ActiveChecks
   suspension.
3. Slurm and MK8s rollout: when MK8s target flags are supplied, identify affected
   node groups and Slurm nodes, drain only cxcli-owned Slurm nodes, apply
   `--job-policy` (`wait`, cancel, requeue, or requeue-hold), run the
   Terraform-managed node-template workflow, and wait for stable control-plane
   and node-group readiness.
4. Soperator chart apply: when a chart target is requested, update the Soperator
   app row, rerender and validate the bundle, apply the target Flux/static
   Soperator manifests, and verify the live chart identity on Kubernetes objects.
5. Fast stage verification gates: after ActiveChecks suspension, Slurm job
   drain, MK8s node-template rollout, post-MK8s validation, Soperator chart
   apply, postflight validation, ActiveChecks restore, Slurm restore, and shared
   safety verification, record a stage-scoped `fast_verification` result before
   the next stage starts. Failed gates write the checkpoint/report and stop the
   run.
6. Postflight validation and restore: restore Slurm node state and cxcli-owned
   ActiveChecks, compare protected config and shared protected-state hashes while
   ignoring cxcli-owned temporary drain/replacement churn, run required
   Soperator/Slurm smoke validation, run the shared bounded fast safety verifier,
   and write `soperator-upgrade-report.md` plus JSON detail.
7. Resume behavior: rerun the same command from the same workdir when a phase is
   interrupted. If the target is already current, the command still performs the
   verification/report path without replaying unnecessary mutations.

`upgrade node-template` validates the live compatibility matrix, updates
`config.yaml`, rerenders, validates, applies the staged Terraform changes, waits
for the MK8s control plane and selected node groups, and writes
`generated/reports/upgrade-node-template-report.md` /
`generated/reports/upgrade-node-template-report.json`. Use `upgrade node-group`
instead when the change is hardware platform, hardware preset, CPU/GPU kind, GPU
cluster, or InfiniBand fabric; current `upgrade node-group --execute --approve`
writes the approved pre-mutation checkpoint/report and then stops before live
replacement/cutover/retirement.

`soperator upgrade` is the cxcli-managed full Soperator cluster upgrade. It
validates the current generated bundle, writes a restore-capable backup with
raw Secrets and an optional chart-managed MariaDB accounting DB dump, captures shared
protected customer state before mutation, handles Slurm worker drain and
running-job policy for affected MK8s nodes, runs the node-template workflow when
requested with stable node-group readiness confirmation, applies the Soperator
chart when requested, verifies the static Soperator chart version on live
Kubernetes objects, compares protected customer config fingerprints plus shared
protected-state before/after hashes while excluding cxcli-owned temporary drain
state and replacement instance churn, reruns the required Soperator/Slurm smoke
validation, runs the same bounded read-only fast safety verifier used by
external upgrades, gates each completed managed upgrade stage with a
stage-scoped `fast_verification`, and writes command-owned validation details
plus the Markdown `Stage Fast Verification` rollup and JSON `stage_verification`
details in
`generated/reports/soperator-upgrade-report.md` /
`generated/reports/soperator-upgrade-report.json`.
The command prints and checkpoints the current phase with the operator-facing
top-level stage (`MK8s Node Upgrades` or `Soperator Upgrade`), component label,
and a concise operator comment from preflight through backup, protected-state
capture, rollout, postflight, shared safety verification, and final report
writing. Quiet terminal phases such as discovery, backup, protected-state
capture, live ActiveChecks patching, Slurm restore, shared safety verification,
and report writing keep a spinner active. The Markdown and JSON reports include
the final `current_phase`, its top-level stage, and phase history.
Use `--approve-remediation` only to record approval for
`remediation_required` protected-state deltas; blocked data-loss or downtime
deltas still stop the run.
`upgrade helm-chart` is intentionally non-Soperator-only and fails fast for
`apps:soperator@<target>` with the canonical `soperator upgrade` command. The
cxcli-managed upgrade path does not run the external source-cluster upgrade
analyzer, does not use
the external source-cluster `generated/reports/soperator-discovery/<target>/`
bundle, and
does not perform external source-cluster storage-copy actions.

If the existing Soperator row uses `repo: ''`, it is pinned to static local
chart rendering for that project config. To upgrade by the published parent OCI
package instead, edit that same Soperator row to the OCI repo and target version
explicitly, for example:

```yaml
repo: oci://cr.eu-north1.nebius.cloud/e00th0mgv3zddz7468/charts/soperator
version: 4.0.2-ps.3
```

Then run `nebius-cxcli soperator upgrade <config.yaml> --target <target> --to-chart-version <chart-version>`
or, for manual desired-state changes, `nebius-cxcli render <config.yaml>` and
`nebius-cxcli deploy <config.yaml>`.
The rendered Soperator output remains a static post-Flux manifest even when the
source is OCI; this avoids the Helm release Secret size limit for the Soperator
umbrella chart.

For external Soperator clusters, start with onboarding instead of the
Terraform-managed MK8s upgrade commands:

```bash
nebius-cxcli ext-soperator onboard <config.yaml-or-deployments-root>
nebius-cxcli validate <config.yaml>
nebius-cxcli render <config.yaml>
```

If onboarding records no external-upgrade-owned actions, use the normal deploy path:

```bash
nebius-cxcli deploy <config.yaml>
```

If onboarding records external-upgrade-owned actions, run `ext-soperator upgrade`
as the guarded upgrade/adoption workflow:

```bash
nebius-cxcli ext-soperator upgrade <config.yaml> --target <target> --dry-run
nebius-cxcli ext-soperator upgrade <config.yaml> --target <target> --execute --approve
```

Use `ext-soperator onboard` plus `ext-soperator upgrade` when the source cluster
is not yet under cxcli Soperator management or when onboarding found a source
Soperator that must be upgraded while adopting or changing layout. In that case
the accepted upgrade profile can combine adoption, Soperator chart upgrade to
the cxcli-pinned target, external MK8s control-plane and node-template upgrade,
storage remediation, compute remediation, target GPU stack reconciliation when
paired with external upgrade work, and final cutover in one guarded workflow. That is
why external upgrade reads the source discovery bundle and accepted action plan, prints
external node-template and target GPU stack reconciliation as their own required
actions when present, and checkpoints phase state instead of acting like a plain
Helm chart bump.

Underlying MK8s upgrade ownership is different for managed and external targets:

- `upgrade node-template` and `upgrade node-group` operate on Terraform-managed `infra:mk8s`
  rows.
- Onboarded external MK8s clusters are not Terraform-managed, so those commands
  do not upgrade the external cluster or its node groups.
- External Soperator upgrade owns external Kubernetes minor, node OS image, and
  Nebius-image GPU-stack upgrades selected by onboarding. It uses direct Nebius
  `mk8s cluster update` and `mk8s node-group update` calls, upgrades the
  control plane first, then updates service-role node groups serially and
  worker node groups with zero-surge by default, or safe-surge when selected.
  Service groups run serially; worker safe-surge runs in bounded waves. It does
  not report completion until the live control plane and selected node groups
  match the requested Kubernetes version, OS image, and Nebius `drivers_preset`
  / CUDA stack.
- For external node-group template updates, upgrade snapshots each original
  node-group strategy and restores it after that group finishes. Service-role
  groups use the selected temporary strategy one group at a time. zero-surge
  sets `max_surge=0`, `max_unavailable=1`, `drain_timeout=30m` and quiesces
  login workloads, one-node service workloads, and known drain-blocking webhook
  replicas. Worker groups also default to zero-surge; safe-surge
  (`max_surge=1`, `max_unavailable=0`,
  `drain_timeout=30m`) applies to service groups and runs workers in bounded
  waves only when selected, after
  quota/capacity, worker-node health, and Slurm queue preflights pass. Set
  `drain_timeout: none` when
  waiting indefinitely is safer than provider drain fallback. CPU node groups
  that carry stale GPU driver presets are reset to the CPU-supported empty
  preset before rollout, and login plus one-node controller/accounting groups
  temporarily quiesce their Soperator workloads and known drain-blocking webhook
  replicas, then restore them after the matching node-group update.
- After an external target has been onboarded and deployed under cxcli app
  management with no external-upgrade-owned work remaining, future Soperator chart
  upgrades can use `soperator upgrade --target <target>`; the external cluster
  lifecycle still remains outside Terraform ownership.

### Soperator Rules and Safety Checks

- `ext-soperator onboard` is separate from `create`. `create` builds a new
  cxcli-managed project; onboarding registers an existing Nebius MK8s target.
- Interactive onboarding lists project MK8s clusters and onboards one cluster
  per run.
- Existing project configs are updated in place only after the interactive
  warning is accepted. Non-interactive runs with `--tenant-id` and
  `--project-id` print the same warning and continue.
- The analyzer treats the canonical Soperator CRDs and exact compatible
  Soperator Helm release identity as source signals. Sibling charts such as
  checks or backup helpers are not enough by themselves.
- If discovery finds Soperator CRDs but no compatible Helm release version,
  `--source-version` or the interactive source-version picker must match an
  exact committed row or known major-generation profile group in
  `soperator_migration_profiles.yaml`.
- Partial or incompatible analyses are not accepted automatically. cxcli fails
  fast rather than assuming a vanilla cluster is safe to adopt.
- Upgrade profile compatibility is release-scoped and component-scoped:
  cxcli compares source SlurmCluster, NodeSet, NodeConfigurator, storage,
  accounting, REST, MariaDB, Kruise, SConfigController, and ActiveChecks
  contracts against the target profile.
- The committed profile generator records per-component chart tarball, CRD,
  template, image, and Slurm contract fingerprints. Runtime onboarding uses
  those exact committed profiles and generation-level profile groups instead
  of fetching live release data.
- Profile groups also record the node-role label layout used during external upgrade:
  legacy source inventories may expose `slurm.nebius.ai/nodeset` or
  `slurm.nebius.ai/nodeset-name`; cxcli creates or reuses service-role groups
  for `system`, `controller`, `login`, and `accounting`, accepts either label
  key for source-era scheduling, and normalizes current Nodes toward
  `slurm.nebius.ai/nodeset-name` during cutover. Worker NodeSets map to the
  preserved detected worker node groups.
- Profile groups also declare release-family takeover behavior. For legacy v1
  and v2 sources, cxcli suspends old Flux desired state, deletes the source
  Soperator admission webhooks declared by the profile, and scales down the
  source Soperator controller deployment declared by the profile before the
  pinned target chart reconciles compute resources, while shared storage and
  custom resources remain protected until the normal retirement phase. The
  retirement phase derives old source-family Helm chart identities from
  `soperator_migration_profiles.yaml`, including the upstream
  `soperator-fluxcd` fan-out release records such as
  `flux-system-soperator-fluxcd-*` and `soperator-fluxcd-values`, while
  protecting the current target `soperator/soperator` release.
- Accepted onboarding actions that modify existing node-group templates, such
  as adding SFS filesystem attachments, are disruptive. Nebius Managed
  Kubernetes rolls those changes by creating replacement nodes, cordoning and
  draining old nodes, then deleting old nodes. Pods can be evicted, Slurm
  workers can restart, and running jobs can be interrupted. Schedule a
  maintenance window or drain/quiesce Slurm jobs before selecting disruptive
  remediation.
- The Soperator command path is best-effort high availability. It is designed
  to preserve service where possible and report degraded status clearly, but it
  does not guarantee zero downtime.

## Upgrade

`nebius-cxcli upgrade` is the day-2 lifecycle surface for version and component
changes that need cxcli safety gates before live reconciliation. It supports
MK8s node-template rolling updates for Kubernetes version, OS, and Nebius-image
GPU stack, explicit MK8s node-group migrations for hardware platform, hardware
preset, CPU/GPU kind, GPU cluster, or InfiniBand fabric changes, and
non-Soperator target-scoped Helm chart version upgrades.

### When To Use upgrade

Use `upgrade` when the change is one of the covered operational upgrades and
you want cxcli to protect the operator from unsafe or incomplete steps:

- MK8s Kubernetes minor upgrades.
- MK8s node-template upgrades: Kubernetes version, OS, and GPU stack.
- MK8s node-group migrations: hardware platform, hardware preset, CPU/GPU
  kind, GPU cluster, or InfiniBand fabric changes.
- Target-scoped Helm chart version bumps. Use `soperator upgrade` instead of
  the generic Helm path for Soperator chart bumps.

These paths keep `config.yaml` as the source of desired state, but they add live
discovery, compatibility checks, Kubernetes preflight checks, staged output,
repeatable dry-run commands, rollout waits, and final readiness verification
where the layer supports it. Edit `config.yaml` manually instead when the change
is outside those covered upgrade layers, spans broader project refactoring,
changes a generic VM image family, or changes a chart source family such as
`repo` plus `version`; then run `render` and `deploy`, `terraform apply`, or
`flux apply` as appropriate.

### Upgrade Principles

- Upgrade commands always take an explicit layer, target selector, and target
  version or target value, or prompt for missing required values in an
  interactive terminal. MK8s node-template rolling updates use
  `infra:mk8s@<target>` plus any subset of version, OS, and Nebius-image GPU
  stack target flags.
- Terraform remains the mutation path for Terraform-managed infrastructure.
  cxcli updates `config.yaml`, rerenders `generated/`, validates the bundle,
  runs Terraform plan, then runs Terraform apply in staged order.
- The Nebius SDK is used for live discovery, version compatibility checks,
  generated handoff, Kubernetes preflight, progress/error watching, and final
  rollout verification. cxcli does not shell out to the Nebius CLI for MK8s
  upgrades.
- The guided target picker shows only the managed target selector, for example
  `infra:mk8s@cluster1` or `infra:vm@worker`. Public/private endpoint access is
  not part of MK8s target identity; it only affects local Kubernetes preflight
  and post-upgrade validation reachability.
- Guided upgrade value prompts use the same reusable `OptionChoice` provider
  path as the main create wizard when live Nebius choices are available. MK8s
  platform, OS image, GPU stack, CPU preset, and GPU preset choices are resolved
  from the live SDK-backed compatibility matrix, compute platform inventory,
  and compute preset inventory before falling back to manual input.
- `--dry-run` performs live discovery and prints the plan plus a
  live compatibility-matrix summary and copy/paste-ready repeat dry-run command
  without changing `config.yaml`, `generated/`, Terraform backend state, or live
  Nebius resources. For repeat commands printed by `upgrade`, removing only
  `--dry-run` keeps the apply command aligned with the reviewed plan.
  When live resources already match the requested node-template values but
  `config.yaml` or `generated/` is stale, cxcli still syncs those
  source/generated files through Terraform plan/apply so the next run cannot
  drift the cluster backward.
- Kubernetes version downgrade targets are refused by the structured MK8s
  upgrade paths. Helm chart targets remain operator-controlled desired state:
  lower target versions are allowed with an explicit warning because they can
  be useful for rollback or recovery, but they are risky for production
  stateful workloads and CRD/schema changes.
- Upgrade layers stay separate except for `upgrade node-template`, which is the
  explicit combined path for moving Kubernetes version, node-template OS, and
  Nebius-image GPU stack together so a selected node group rolls once.
  Hardware shape, platform, GPU cluster, and app/chart concerns remain separate.
  Node firmware is maintained by the Nebius hardware team and is not a customer
  upgrade layer.
- Manual desired-state upgrades remain supported outside the `upgrade` command:
  operators can edit `config.yaml` directly, for example bump
  non-guarded desired-state fields such as a generic VM image family or an app
  chart row's `repo` and `version`, then run `render`, review the generated
  diff and Terraform plan, and use `deploy`, `terraform apply`, or `flux apply`
  to reconcile that desired state. Guarded MK8s node-template changes should use
  `upgrade node-template`, and hardware platform, preset, CPU/GPU kind, GPU
  cluster, or fabric changes should use `upgrade node-group`. Manual edits still
  go through the guardrails of the chosen generated-bundle command:
  `deploy` runs the full generated-bundle preflight, including schema/readiness
  checks, VPC/resource-name preflight, live quota/capacity checks, MK8s
  GPU-stack compatibility for Nebius-image GPU node groups, Terraform/provider
  validation, and Flux validation; `terraform apply` is the infra-only path and
  still runs the MK8s infra preflights plus Terraform/provider validation before
  apply. Resource replacements or deletes are still governed by the generated
  diff and Terraform plan.

### Node Template Upgrade

Use `upgrade node-template` when a Terraform-managed MK8s node group needs a
Kubernetes minor, node OS image, or Nebius-image GPU stack rolling update. The command can run as
a guided wizard from only `config.yaml`:

```bash
nebius-cxcli upgrade node-template <config.yaml>
```

The guided wizard prompts for the managed MK8s target, requested node-template
values, optional node-group narrowing, dry-run/apply choice, upgrade strategy,
drain timeout, and post-upgrade validation choice.

Automation should pass the explicit target and at least one requested
node-template field. Omitted `--to-version`, `--to-os`, and
`--to-gpu-stack-preset` values keep the selected live value when it is
unambiguous and compatible; `--no-interactive` fails fast if the target or all
node-template field flags are missing:

```bash
nebius-cxcli upgrade node-template <config.yaml> infra:mk8s@<target> \
  --to-version <major.minor> \
  --to-os <os> \
  --to-gpu-stack-preset <cuda...>
```

For each selected live node group, cxcli validates the target tuple through the
Nebius SDK compatibility matrix using the requested Kubernetes version and that
group's live platform. The matching tuple must include the requested OS and,
for Nebius-image GPU groups, the requested `drivers_preset`/CUDA stack.
Dry-run and non-dry-run plans print the returned OS and driver-preset choices
for each selected platform before any source file or live resource mutation.
During the intermediate control-plane stage, generated-bundle compatibility
validation honors explicit `inputs.node_groups.*.version` pins, so node groups
still waiting for their own stage are checked against their current Kubernetes
minor instead of the newly staged control-plane minor.

The rollout order is control plane first, then selected node groups in the same
CPU/system-before-GPU order as other MK8s upgrades. During a node-group stage,
cxcli writes the node-group Kubernetes version, OS, and Nebius-image
`gpu_stack_preset` together, rerenders, validates, runs Terraform plan/apply,
waits for the Managed Kubernetes rolling replacement to finish, and then runs a
final MK8s readiness check for the live control-plane version plus selected
node-group version, OS, and Nebius `drivers_preset` / CUDA stack. That final
check requires provider node-group status rather than accepting matching spec
fields alone. After that check, cxcli writes
`generated/reports/upgrade-node-template-report.md` and
`generated/reports/upgrade-node-template-report.json` with the requested
template tuple, live readiness summary, node-group readiness, and any selected
post-upgrade validation rollups. Leave
`--node-group` unset to select all managed node groups, or pass one source key
or live name to narrow the command. In guided mode, the optional `node_group`
prompt says blank selects all managed node groups, the `safe-surge` strategy
choice says it defaults to one spare node per active node group, the
`strategy_max_surge_count` prompt asks for temporary extra nodes per active
node group when safe-surge is selected, and the `drain_timeout` prompt
shows all `auto` defaults (`30m` for zero-surge/safe-surge, `10m` for
force-delete).

`--to-gpu-stack-preset` is required when the selected groups include
Nebius-image GPU groups, and rejected when none of the selected groups can
consume a Nebius `drivers_preset`. Operator-managed GPU groups can still
receive Kubernetes version and OS changes through this command; they do not get
a Nebius GPU stack preset. Existing node-group platform, hardware preset, and
GPU cluster remain out of scope because Nebius does not allow changing those
fields on an existing node group.

### Node-Group Migration

Use `upgrade node-group` when a Terraform-managed MK8s node group needs a new
hardware platform, hardware preset, CPU/GPU kind, GPU cluster, or InfiniBand
fabric. This is the explicit blue/green node-group migration surface; do not
edit those fields directly and apply them, because Nebius cannot change the
platform, preset, or GPU cluster of an existing node group in place.

```bash
nebius-cxcli upgrade node-group <config.yaml> infra:mk8s@<target> \
  --node-group <group> \
  --to-platform <platform> \
  --to-preset <preset> \
  --to-os <os> \
  --to-gpu-stack-preset <cuda...> \
  --to-fabric <fabric> \
  --dry-run
```

For GPU-cluster / InfiniBand node groups, `--to-fabric` is optional. Omitting it
keeps the current `inputs.gpu_clusters.<key>.infiniband_fabric`; passing the same
value reports an unchanged fabric; passing a different value plans a replacement
node group bound to a new GPU cluster on the target fabric. CPU groups reject
`--to-fabric`, and GPU groups that are not on the GPU-cluster / InfiniBand path
reject it too.

The dry-run plan prints current config fabric, current Terraform state fabric,
effective target fabric, fabric status, platform/preset/OS/GPU-stack deltas,
reservation policy, SFS/PVC evidence, target quota/capacity findings, and the
repeatable dry-run and approved execute commands. Current execute requires
`--execute --approve`, writes an approved pre-mutation checkpoint after the
local gates, and then stops before live replacement/cutover/retirement; the
live executor is not enabled yet. That approved gate also writes
`generated/reports/upgrade-node-group-report.md` and
`generated/reports/upgrade-node-group-report.json` so the report folder has
separate evidence for node-template and node-group upgrade sessions.

### Upgrade Strategies

`upgrade node-template` uses `--strategy` plus `--drain-timeout` to control
node replacement safety:

```text
zero-surge   -> 30m
safe-surge   -> 30m
force-delete -> 10m
```

- `zero-surge` is the default. It sets `max_surge=0` and
  `max_unavailable=1`, avoids spare node quota, and can temporarily reduce
  active capacity by one node per active node group. PDB blockers still stop
  preflight.
- `safe-surge` defaults to `max_surge=1` and `max_unavailable=0`. It preserves
  active node-group capacity with temporary replacement nodes, so it requires
  enough spare quota and capacity; cxcli fails preflight when quota assessment
  reports a shortage. Use `--strategy-max-surge-count <n>` to request `n`
  temporary extra nodes per active node group; the default is `1`. For GPU
  node groups attached to a GPU cluster, spare capacity is checked on the same
  InfiniBand fabric and with the same `reservation.policy` as the selected node
  group. Moving to another fabric means creating or migrating to another GPU
  cluster/node group through `upgrade node-group`; it is not an in-place
  node-template upgrade.
- `force-delete` is a last-resort mode selected explicitly through the
  upgrade strategy. It sets a finite Terraform node-group `drain_timeout` so
  Managed Kubernetes may fall back to Pod deletion and old-node deletion after
  the timeout.

`--drain-timeout auto` resolves to the defaults above. Duration values use
Go-style units such as `10m`, `30m`, or `1h`; `none` waits indefinitely instead
of allowing provider drain fallback.

If an `upgrade` rerun only needs to wait for an already-requested MK8s
node-group rollout after a staged temporary strategy, cxcli still performs one
final rendered apply after the rollout settles so the configured node-group
strategy is restored.
Temporary node-group strategy settings are restored in `config.yaml` and
`generated/` if a staged render, validation, Terraform plan/apply, or rollout
wait fails.

The drain timeout is the provider drain fallback, not cxcli's whole rollout
watch. cxcli's SDK node-group rollout watch starts after each Terraform apply,
is for the whole node group, and uses `max(1h, 10m * target node count)`.

cxcli never deletes PVC/PV objects. Persistent volumes remain Kubernetes
storage objects, but forced Pod deletion can still create application-level
consistency risk when old processes, shared storage, locks, or external APIs are
involved.

Kubernetes preflight inspection failures block non-dry runs for every
upgrade strategy, including `force-delete`, because cxcli cannot safely
distinguish known blockers from unknown cluster state.

### Upgrade Examples

Run the guided MK8s node-template upgrade wizard:

```bash
nebius-cxcli upgrade node-template \
  ~/deployments/tenant-name-example/project-name-example/config.yaml
```

Plan a Kubernetes version-only node-template update without writes:

```bash
nebius-cxcli upgrade node-template \
  ~/deployments/tenant-name-example/project-name-example/config.yaml \
  infra:mk8s@mk8s \
  --to-version 1.33 \
  --dry-run
```

Plan an OS-only node-template update for one node group:

```bash
nebius-cxcli upgrade node-template \
  ~/deployments/tenant-name-example/project-name-example/config.yaml \
  infra:mk8s@mk8s \
  --to-os ubuntu24.04 \
  --node-group system \
  --dry-run
```

Plan a combined MK8s node-template upgrade without writes:

```bash
nebius-cxcli upgrade node-template \
  ~/deployments/tenant-name-example/project-name-example/config.yaml \
  infra:mk8s@mk8s \
  --to-version 1.33 \
  --to-os ubuntu24.04 \
  --to-gpu-stack-preset cuda13.0 \
  --dry-run
```

For node-group hardware or fabric migration, pass the concrete source node group
and only the target fields that change:

```bash
nebius-cxcli upgrade node-group <config.yaml> infra:mk8s@<target> --node-group system --to-platform cpu-d3 --to-preset <preset> --dry-run
nebius-cxcli upgrade node-group <config.yaml> infra:mk8s@<target> --node-group worker --to-platform gpu-b200-sxm --to-preset 8gpu-160vcpu-1792gb --to-fabric fabric-6 --dry-run
```

### Helm Chart Upgrades

Use generic `upgrade helm-chart` for non-Soperator chart rows. Soperator keeps
its dedicated command because it adds Soperator-aware pre/post validation gates:

```bash
nebius-cxcli upgrade helm-chart <config.yaml> apps:<chart>@<target> --to-version <chart-version>
nebius-cxcli soperator upgrade <config.yaml> --target <target> --to-chart-version <chart-version>
```

- `helm-chart` updates the selected target-scoped `apps.charts[]` row version,
  rerenders, validates, and applies the selected target's Flux bundle. Its live
  readiness check requires the selected generated target handoff, then verifies
  the Helm release and rendered workloads. It has no node-drain flags. It does
  not switch a row between local static rendering and an OCI/HTTP/Git chart
  source; edit `repo` plus `version` directly when that source-family change is
  the desired state, then run `render` and `deploy` or `flux apply`. When the
  selected chart is `apps:soperator@<target>`, `upgrade helm-chart` fails fast
  with the canonical `soperator upgrade` command.
- `soperator upgrade` is the canonical cxcli-managed Soperator full-upgrade path.
  It wraps optional MK8s node-template changes and optional chart
  render/apply with restore-capable backup, live Soperator/Slurm preflight and
  postflight validation, protected config comparison that ignores cxcli-owned
  temporary Slurm drain state and Nebius replacement instance churn, and writes
  `generated/reports/soperator-upgrade-report.md` /
  `generated/reports/soperator-upgrade-report.json`. If a previous run was
  interrupted after temporary ActiveChecks suspension, rerun the same command;
  cxcli uses the local upgrade checkpoint to restore the original ActiveChecks
  values before completing. In interactive mode, omitted `--to-chart-version` prompts
  with the current row version and the active `component_sources.yaml` Soperator
  chart pin as the default target version.
- Operators can still upgrade manually by editing the required desired-state
  values in `config.yaml`, such as Kubernetes version, OS image, platform,
  preset, GPU stack preset, or chart version, then running `render` and
  `deploy` or the narrower Terraform/Flux command. Use the structured
  `upgrade` command for these specific changes when you want its live
  discovery, compatibility checks, preflight checks, staged output, and repeat
  dry-run command.
- Node firmware is maintained by the Nebius hardware team and is not a customer
  upgrade responsibility.

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
The normal `.github/workflows/nebius-cxcli-ci.yml` workflow uses `validate-sources component_sources.yaml` with source profile `local` instead, so branch changes are validated against the checked-out Terraform modules and Helm charts rather than the remote `ref=main` portable sources. That branch CI workflow checks that the wheel bundles both `component_sources.yaml` and `component_cli_settings.yaml`; it does not require every bundled chart to be portable before release time.
Those post-`make all` workflow checks use the repo `.venv/bin/python` created by that contract so `nebius_cxcli.release_catalog` imports the editable service package reliably under GitHub Actions.

In source/editable checkouts, runtime version resolution prefers live SCM state over a generated `_version.py` cache: it uses `setuptools-scm` when available and falls back to `git describe` when it is not. The local `./publish-release.sh --publish X.Y.Z` flow also verifies that the tagged source checkout resolves `nebius-cxcli.__version__ == X.Y.Z` before it pushes the release tag.

Release assets for `nebius-cxcli` now include:

- the wheel artifact
- the raw release catalog as a direct editable download, with its Terraform Git module refs pinned to the published release tag

## Commands

Idempotency guide:

- Read-only commands are safe to repeat: `validate-sources`, `validate`, `quota-check`, `validate-generated`, `discover`, `terraform plan`, and `auth --validate-profile`.
- Reconcile/apply commands are sequentially idempotent or convergent for the same target: `render`, `deploy`, `terraform apply`, `flux apply`, `flux bootstrap`, `bootstrap-ci`, `auth --create`, and `auth --bootstrap-ci`.
- `create`: create-if-missing for a new resolved project folder; existing resolved targets for the same `tenant_id`/`project_id` require explicit overwrite confirmation unless `--force` is provided, and are not reconciled in place.
- Destructive commands are sequentially convergent for the same target but intentionally remove resources: `destroy`, `terraform destroy`, and `flux destroy`. They require confirmation or `--yes`.
- Day-2 component commands are safe to repeat for exact rows: `component list`
  is read-only, non-interactive `component add` skips already-enabled exact
  rows unless a new `<component-id>@<resource-name>` is supplied, and
  `component remove` skips already-absent rows. In interactive mode, repeating
  a bare scalar named infra selector such as `infra:vm` prompts for the
  resource name, with the next available normalized name (`vm-2`, `vm-3`, and
  so on) as the default. The saved `instance_id` is derived from that name.
- Explicit side-effecting commands are intentionally not idempotent: `auth --recreate` rotates auth material, and `email` sends another message on each run.
- `create --force` and `render --force` are still deterministic with the same inputs, but they are explicit overwrite/reset modes rather than the safer default reconcile flow. For `create`, that overwrite scope is the resolved project folder, not the entire deployments root.
- `terraform unlock` is operationally safe to repeat: once the lock is cleared, reruns report that no lock is present.

Global options:

- `--version`
- `--component-sources-file <path>`
- `--source-profile {portable|local}`

### Generator-side Commands

```bash
nebius-cxcli validate-sources
nebius-cxcli grafana --export-dashboard https://grafana.example.invalid/ --folder-uid folder-uid --dashboard-uid dashboard-uid --dashboard-folder mk8s --datasource "Nebius User Metrics" --attach
nebius-cxcli grafana --dashboard-json ./dashboards/mk8s/custom.json --dashboard-folder mk8s --datasource "Nebius User Metrics" --attach
nebius-cxcli validate /path/to/config.yaml
nebius-cxcli validate-dashboards /path/to/config.yaml
nebius-cxcli quota-check /path/to/config.yaml
nebius-cxcli render /path/to/config.yaml
```

- `validate-sources`
  - Validates the active `component_sources.yaml` catalog plus sibling `component_cli_settings.yaml`: Terraform module sources, Helm chart sources, settings contract shape, and fast source-structure checks for CLI-friendly Terraform modules and Helm charts.
  - Accepts an optional positional catalog path, for example `nebius-cxcli validate-sources ./component_sources.yaml`.
  - Example: `nebius-cxcli validate-sources ./component_sources.yaml`
- `grafana --export-dashboard <grafana-base-or-folder-url>` / `grafana --dashboard-json <path>`
  - Exports selected dashboards from a Grafana API or normalizes local dashboard JSON into `./dashboards/<folder>/` by default. Use `--output-dir` for a different destination, `--folder-uid` and repeatable `--dashboard-uid` for non-interactive API exports, repeat `--dashboard-json` for multiple local files, and `--overwrite` to replace existing JSON files.
  - Interactive API exports show folders and dashboards sorted by title, then UID, and support first-character jump keys in the selector.
  - Authentication tries `GRAFANA_TOKEN`, `NEBIUS_IAM_TOKEN`, `nebius iam get-access-token --format text`, `--token-env <ENV_NAME>`, then Basic auth when `--username` is provided with `--password-env` or an interactive password prompt.
  - Export-only is read-only for the catalog. Add `--attach` to update the active `component_sources.yaml` with `json_file` dashboard entries under `components.apps.grafana.defaults.values.dashboards`; `--component-sources` can select a different catalog, `--dashboard-folder` can select the dashboard folder/provider key, and `--datasource` is required in non-interactive mode when datasource mapping is ambiguous.
  - `--attach` writes only the Grafana `dashboard` object, removes runtime `id` and `version`, preserves the top-level `uid`, rewrites dashboard datasource refs to the selected cxcli datasource UID/type, creates the matching dashboard provider when needed, refuses to mix JSON dashboards into a Grafana.com `gnetId` provider, and restores the catalog if post-write validation fails.
  - `nebius-cxcli grafana --help` includes scenario examples for interactive API export, non-interactive API export, API export with catalog attach, local JSON attach, and multi-file local JSON attach with an explicit catalog.
  - Example: `nebius-cxcli grafana --export-dashboard https://grafana.example.invalid/dashboards/f/folder-uid/mk8s --folder-uid folder-uid --dashboard-uid dashboard-uid --datasource "Nebius User Metrics" --attach`
  - Example: `nebius-cxcli grafana --dashboard-json ./dashboards/mk8s/custom.json --dashboard-folder mk8s --datasource "Nebius User Metrics" --attach`
- `validate <config.yaml>`
  - Validates the project config contract and deployment-readiness shape in one canonical command.
  - Runs phased validation with visible progress: config/catalog load, active source checks, dependency checks, Terraform module input/schema checks, strict readiness checks, VPC networking preflight, then a fail-fast live Nebius quota/capacity phase.
  - Prints one concise validated-scope list after the phase run, with separate `infra` and `apps` sections and per-group entries such as `Compute`, `Storage`, `Platform`, or `Workloads`.
  - Reuses the same live quota/capacity assessment as `quota-check`. GPU quota dimensions are resolved from the live Nebius Capacity Dashboard for the exact platform/preset/fabric shape, interpreted as VM slots for that preset, and converted to GPU units before comparison, while non-GPU quota dimensions still use the regular quota allowance APIs. Confirmed insufficiency fails `validate`, while unresolved live limits remain warning-only.
  - For day-2 edits to an already rendered/deployed MK8s bundle, `validate` uses the same sibling generated manifest plus Terraform state discount as `quota-check`, so an unchanged existing cluster is not treated as a fresh capacity request. Without generated state, it falls back to the full desired shape from `config.yaml`.
  - Defaults to the global source profile `portable`, so validation fails when the requested render contract would rely on non-portable local Terraform module paths.
  - Example: `nebius-cxcli validate ~/deployments/tenant-name-example/project-name-example/config.yaml`
- `validate-dashboards <config.yaml>`
  - Validates each enabled bundled Grafana dashboard source against the live bundled Grafana datasources for the project config. Signal-bound dashboard sources are validated for runtime status, while `deploy-report.md` lists the bundled dashboard set directly instead of separate Metrics, Logs, and Traces shortcut links.
  - Checks the concrete binding chain: `observability.endpoints.read` key -> Grafana datasource UID/type -> dashboard JSON query contract. It validates the fixed dashboard sources; it does not generate, mutate, or repair dashboards. Prometheus checks metric names and required label keys for every dashboard source, runs representative PromQL queries where the validator has a target-scoped query contract, and summarizes no-data service-dashboard metrics as warnings. For target-scoped dashboards, it resolves the target MK8s cluster ID from generated Grafana status, generated reports, or the persisted kube context and validates cluster-filtered selectors such as `k8s.cluster.id` and `mk8s_cluster_id` instead of letting another cluster's data satisfy the check. Loki checks bucket and Kubernetes log labels plus representative LogQL in the same target-aware way; Tempo checks TraceQL reachability and reports a warning when the endpoint is reachable but no traces exist in the selected window.
  - Prints each dashboard with a simple `Source:` line, optional `Checks:`, grouped `Warnings:`, and grouped `Errors:`. Grafana.com imports are shown as source provenance, not as warnings.
  - Shows live dashboard progress while it waits on Grafana datasource/dashboard API calls, with the total based on every target-bound Grafana.com and cxcli-owned dashboard binding and the current item labeled as `<target-id>: <folder>/<dashboard>`.
  - Use `--target <target-id>` to validate one target-scoped Grafana row in a multi-target config. For MK8s, the target id is the normalized cluster resource name stored as that row's `instance_id`. When omitted, every enabled Grafana row is checked and each target must resolve an explicit kube context; the current kube context is accepted only when its generated Nebius name matches that target.
  - Example: `nebius-cxcli validate-dashboards ~/deployments/tenant-name-example/project-name-example/config.yaml --target cluster1`
- `quota-check <config.yaml>`
  - Runs the same live Nebius quota/capacity assessment used by `create`, `render`, and `deploy`, but as an explicit read-only operator command against one project config. It reruns against current Nebius state every time; it does not reuse or trust a cached create-time result.
  - For day-2 edits to an already rendered/deployed MK8s bundle, `quota-check` best-effort reads the sibling generated manifest plus Terraform state and discounts capacity already managed by that bundle. For example, editing a node count from 4 to 6 checks the net-new 2 nodes when state is available; without generated state, it falls back to the full desired shape from `config.yaml`.
  - GPU quota dimensions are centralized on the live Nebius Capacity Dashboard `resource-advice` surface for the exact platform + region + preset + fabric shape. The returned availability is VM slots for that preset, so cxcli multiplies by the selected preset's GPU count before comparing with `compute.instance.gpu.*`; a two-node `8gpu-*` request requires 16 GPUs and passes when at least two matching VM slots are available. cxcli no longer overlays a separate Capacity Block Group-specific GPU path or a synthetic `compute.gpucluster.count` check.
  - Also prints a concise per-component summary for components whose checked quota dimensions were sufficient, plus the exact checked quota names listed one per line. Components with coverage gaps still appear there for the dimensions that were confirmed, with the unresolved parts called out separately below.
  - Returns success when no confirmed insufficiency is found, even if some live quota dimensions remain unresolved; those unresolved limits and coverage gaps are still printed as warnings.
  - Coverage-gap warnings are grouped per component and listed vertically under a `gaps:` section so each unresolved reason appears on its own line.
  - `--all-regions` also prints per-region availability for the same shape across all discovered quota regions plus any GPU regions returned by the Capacity Dashboard. It does not change pass/fail semantics, which still follow the selected config region.
  - When quota-check ends with confirmed insufficiency and `--all-regions` was not requested, the CLI prints either the direct `quota-request` remediation command for requestable quota shortages or a GPU Capacity Dashboard shape-change hint for capacity-only shortages, plus the exact `quota-check --all-regions` rerun command as a diagnostic next step.
  - A warning by itself does not mean quota is short. For bundled MK8s, exact `compute.disk.size.*` checks work whenever cxcli can resolve the typed node-group preset resources plus group-level `boot_disk` type and size. If the preset resources or disk type still cannot be resolved exactly, quota-check reports a coverage gap instead of guessing.
  - Returns a non-zero exit status when the enabled infra shape is confirmed to exceed currently available live quota.
  - Example: `nebius-cxcli quota-check ~/deployments/tenant-name-example/project-name-example/config.yaml --all-regions`
- `quota-request <config.yaml>`
  - Reuses the same live quota assessment as `quota-check`, but keeps allowance lookup and request submission separate: live `QuotaAllowance` data confirms the shortage, then the command plans `QuotaRequest` targets for the constraining tenant/project scopes only.
  - This command exists for remediation after `create`, `quota-check`, `validate`, `render`, `deploy`, or `validate-generated` reports a confirmed shortage. If the current config is already sufficient at run time, it is a no-op; it does not pre-request quota just because a config exists.
  - It is also the day-2 manual edit path: after an operator changes `config.yaml`, such as scaling a resource from 4 to 6, `quota-request` reruns live quota assessment and requests only the confirmed requestable shortfall. When sibling generated state is available for MK8s, it discounts the already-managed 4 and plans the net-new request.
  - No verified public Nebius quota-request API surface is currently used here. Automatic submission works only on the Nebius internal network for Nebius employees/operators via the internal request path; otherwise cxcli falls back cleanly to manual web-console follow-up under Administration -> Limits -> Quotas.
  - Internal auto-submit may also expand the final request set when the quota-recommendation service says related quotas must move together. For example, an H200 increase can also imply a matching `compute.instance.count` increase on the same tenant.
  - Requests only the constraining tenant/project scopes; unresolved live limits, estimator coverage gaps, and GPU Capacity Dashboard capacity-only shortages are still reported, but they are not auto-requested.
  - The manual fallback prints the minimum total target limit and minimum increase to request for each confirmed shortage, so operators can transfer the numbers directly into the console even when internal auto-submit is unavailable.
  - When the report contains coverage gaps only, the command now prints those unresolved reasons before the final no-op summary so the operator can see why nothing was submitted.
  - For bundled MK8s node-group disk-size quota, exact auto-requesting works when cxcli can resolve the node-group preset resources plus disk type and therefore materialize the effective boot-disk size/type, or when the equivalent first-class boot-disk fields / override values are already explicit in `config.yaml`. If the shape still cannot be resolved exactly, the command prints the remaining coverage gap instead of issuing a blind request.
  - Prints the exact target limit per requested scope before attempting submission and reminds operators that current quota allowances stay unchanged until the request is approved.
  - Example: `nebius-cxcli quota-request ~/deployments/tenant-name-example/project-name-example/config.yaml`
- `render <config.yaml>`
  - Generates the deployable bundle under `generated/` and writes `generated/nebius-cxcli-manifest.json`.
  - Runs pre-render config validation before it writes anything: config/catalog load, active source checks, dependency checks, then Terraform module input/schema checks.
  - Rerender now stages the new bundle under a hidden sibling directory and swaps it into `generated/` only after the replacement bundle is complete.
  - The replacement recreates the managed generated bundle from a clean canonical layout without stale files from earlier renders and removes any legacy `generated/flux/flux-system` subtree.
  - Performs a best-effort live Nebius quota check for the rendered infra shape, discounts capacity already managed in the current sibling generated Terraform state when available, stores that report in the generated manifest, and warns instead of blocking when quota is insufficient or some quota dimensions cannot be resolved precisely.
  - Coverage-gap-only detail stays in the generated manifest, but routine `render` terminal output does not repeat those non-blocking summaries. Use `quota-check` when you want the full coverage-gap summary in the terminal.
  - Defaults to the global source profile `portable`, which rewrites active local module sources to their portable Git equivalents when available.
  - Use `--source-profile local` only for workstation testing against checked-out local Terraform modules; those generated artifacts are intentionally non-portable.
  - Use `--component-sources-file` or `NEBIUS_CXCLI_COMPONENT_SOURCES_FILE` only when you need to select a non-default catalog file.
  - If `generated/` already contains render-owned artifacts, `render` prompts before replacement in an interactive terminal.
  - In non-interactive contexts, use `nebius-cxcli render --force <config.yaml>` to confirm replacing those artifacts explicitly.
  - On successful render, terminal output prints the copy-paste deploy helper as `Next step: deploy the rendered bundle:` followed by a colored `nebius-cxcli deploy <config.yaml>` command line.
  - Example: `nebius-cxcli render ~/deployments/tenant-name-example/project-name-example/config.yaml`

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
  - Validates an existing generated bundle without rerendering it, including strict readiness checks against the manifest runtime config, live quota/capacity, Terraform validation for `generated/infra`, and `kubectl kustomize` against each rendered Flux tree when apps exist.
  - The generated-bundle quota/capacity gate is state-aware for bundled MK8s reruns: after backend init, cxcli reads the current Terraform state and discounts MK8s quota already managed by that bundle, so rerunning an unchanged existing cluster does not fail like a fresh create. Real added capacity, such as scaling the node groups up or changing to a larger GPU shape, still has to fit live quota/capacity and will still fail fast when it does not.
  - Confirmed generated-bundle quota/capacity failures print the exact source-config follow-up commands for `quota-request` and `quota-check --all-regions`.
  - For bundled MK8s, the generated-bundle Terraform validation path now also fails fast on live MK8s cluster / derived GPU-cluster name collisions that are not already tracked in the current Terraform state, so stale Nebius resources surface as targeted preflight errors before `terraform apply`. Nebius `NOT_FOUND` responses remain non-blocking and are treated as the expected "resource does not exist yet" case.
  - Reports visible phases for strict readiness, VPC networking preflight, backend auth/bootstrap, live quota/capacity, Terraform validation, Flux manifest validation, and optional portability enforcement.
  - Add `--portable` in CI or pre-commit checks to reject generated Terraform bundles that still embed local filesystem module paths.
  - Uses the generated bundle as the deploy contract; it does not need the original render machine's local module paths.
  - Example: `nebius-cxcli validate-generated ~/deployments/tenant-name-example/project-name-example/generated --portable`
- `deploy <config.yaml>`
  - Full local reconcile from the generated bundle: `deploy` resolves the sibling `generated/` directory and loads `generated/nebius-cxcli-manifest.json` as the authoritative deploy input. That keeps the rendered bundle, not the latest source file edits, as the applied contract. Before Terraform apply, `deploy` runs a generated-bundle preflight covering strict deployment-readiness checks against the manifest runtime config, VPC networking preflight, live Nebius quota/capacity validation, Terraform validation for `generated/infra`, and MK8s GPU-stack compatibility for Nebius-image GPU node groups; on bundled MK8s that Terraform-validation pass now also catches live MK8s cluster / derived GPU-cluster name collisions that are not already managed in the current Terraform state, while treating Nebius `NOT_FOUND` responses as the normal "resource is absent" case. `deploy` then applies Terraform, writes an interim inventory report from infra/app artifacts, applies Flux when app charts are enabled, captures runtime status such as Grafana URLs, runs deploy-time validations, and refreshes the final `deploy-report.md`. On success, the terminal footer prints target-grouped validation PASS/FAIL, copy-paste commands, and only the generated bundle plus `generated/reports/deploy-report.md` paths. Local runs now merge every selected built-in cluster target into `~/.kube/config`; single-target runs still switch `current-context`, while multi-target runs preserve the operator's existing `current-context` and add switchable contexts for each target. Plain multi-target `deploy` and `deploy --all-targets` reconcile every generated target, and `flux apply --all-targets` / `flux bootstrap --all-targets` leave every selected target available through `kubectl config use-context ...`. Use `deploy --target <target-id>` only when you want to narrow app and validation work to one target. If GitOps bootstrap is not configured yet, the CLI includes the optional `flux bootstrap` command in the copy-paste footer when Flux work actually runs; customers who intend to manage the cluster through local direct apply can ignore that GitOps handoff.
  - The live quota/capacity preflight uses the Capacity Dashboard for GPU quota dimensions, converts matching VM-slot availability to GPU units, and is rerun-safe for existing bundled MK8s clusters: after backend init, cxcli subtracts the MK8s quota already managed in the current Terraform state before comparing the desired bundle against live quota/capacity. Unchanged reruns therefore stay idempotent instead of failing like first deploys, while real extra requested capacity still fails fast with an explicit quota/capacity message and the exact `quota-request` / `quota-check --all-regions` follow-up commands when it exceeds live availability.
- Deploy-time optional MK8s GPU checks are configured per target under `deploy.targets[].deployment_testing.mk8s_gpu.*`, where each row uses `instance_id` to bind to the cluster target. The fast MK8s node inventory smoke is generated outside that config block as a required manifest validation for every MK8s target, writes `cluster-inventory-report-<target>.json` under `generated/reports/`, and cannot be disabled by `--skip-validations` or `--skip-validation`. GPU stack readiness writes `deploy-gpu-stack-readiness-report-<target>.json`, and bounded GPU visibility writes `deploy-gpu-visibility-report-<target>.json`. NCCL settings are not persisted in `config.yaml`; they are command-only options for explicit `acceptance-test benchmark` runs. The Observability Agent ingestion check is generated for each observability-enabled MK8s target when the active settings catalog leaves `components.infra.mk8s.cli.observability.primary_agent.validation` enabled; that settings-catalog switch defaults to enabled and is separate from customer `config.yaml`. Native ESO MysteryBox connectivity is generated as a required guardrail whenever target-scoped MysteryBox sync is configured, so `--skip-validations` and repeatable `--skip-validation <kind>` only skip optional deploy checks such as `gpu-visibility` or `observability-ingestion`; those CLI flags do not rewrite `config.yaml`. If a validation fails before its normal report is complete, `deploy` still writes a failure JSON report so the combined deploy summary shows `FAIL` with the underlying error instead of `NOT RUN`.
- Ongoing GPU health and performance monitoring is intentionally outside that fast deploy loop. NVIDIA positions DCGM Exporter as the Kubernetes telemetry path for Prometheus/Grafana, while deeper DCGM diagnostics are invasive administrator workflows with different run levels and runtimes, so cxcli does not fold those checks into every local `deploy`.
  - Non-blocking quota coverage gaps remain recorded in the generated manifest, but routine `deploy` output focuses on confirmed shortages and live lookup failures. Use `quota-check` for the full coverage-gap summary in the terminal.
  - `deploy` is idempotent in the Terraform/Flux sense: rerunning the same generated bundle converges to no-op, but it is not a create-only path. Existing managed infrastructure or workloads can be updated when the generated bundle differs from live state.
  - Use `nebius-cxcli terraform plan <generated-dir>` first when you need a non-mutating preview of the next reconcile.
  - Nebius API status polling for infra is catalog-driven per Terraform module. The generated manifest snapshots enabled module watcher specs, and `deploy`/`terraform apply` fall back to the active catalog when older generated bundles do not have that metadata yet.
  - Each watcher resolves its `parent_id` and `resource_name` from the enabled component row in `config.yaml`, using the catalog's `status.parent_input` and `status.name_input` paths. For example, `mk8s` reads `inputs.cluster.parent_id` plus `inputs.cluster.cluster_name`, `managed-postgresql` reads `inputs.parent_id` plus `inputs.name`, `object-storage` reads `inputs.parent_id` plus `inputs.name`, SSH/WireGuard public VM wrappers read `inputs.parent_id` plus `inputs.name`, and `mysterybox` expands the canonical `inputs.secrets` list into one watcher per configured secret `name`.
  - Status output reads Nebius service-native response fields directly. MK8s watchers fail fast from node-group error events, and the PostgreSQL, SFS, object-storage, compute-instance, and MysteryBox watchers fail fast from terminal Nebius operation status once the resource is visible, so long-running applies do not sit on generic Terraform timeouts after the API already knows the operation has failed.
  - A watcher terminal-check failure is shown as an API status diagnostic but does not abort Terraform unless a watcher reports an actual terminal Nebius resource failure.
  - `deploy` does not run `flux bootstrap`; use `flux bootstrap` itself or the generated CI apply workflow when you want GitOps bootstrap/reconcile.
  - `deploy` does not run `bootstrap-ci` automatically, even when the bundle lives inside a git repository. GitHub workflow/environment bootstrap stays an explicit generator-side step.
  - Example: `nebius-cxcli deploy ~/deployments/tenant-name-example/project-name-example/config.yaml`
- `destroy <config.yaml>`
  - Full local teardown from the generated bundle. `destroy` resolves sibling `generated/` from the project config, then uses the generated manifest as the authoritative teardown contract for all rendered project resources. When app charts are enabled, it deletes rendered Flux and locally applied post-Flux app resources first so Kubernetes finalizers and CSI cleanup can run, then runs Terraform destroy against the rendered infra bundle. For generated bundles with built-in MK8s handoff metadata, Terraform still removes the handed-off cluster after app teardown.
  - For onboarded external MK8s targets, the cluster and node groups are not Terraform-owned and are not destroyed. `destroy` removes only rendered app resources from that external target plus any explicitly cxcli-owned add-on infra in the generated bundle.
  - `destroy` is the destructive inverse of `deploy` and is intentionally project-wide. It does not rerender from `config.yaml`, and it does not uninstall Flux controllers or mutate GitHub CI/bootstrap state.
  - Rendered app teardown failure is fatal before Terraform destroy when app charts are enabled, even for managed clusters that Terraform will remove afterward. In multi-target generated bundles, cxcli attempts all selected targets first and then reports the collected teardown failure so Kubernetes finalizers and CSI cleanup are not skipped silently.
  - During destroy recovery, the CLI can automatically clear a stale Terraform backend lock and retry once. If destroy is still blocked by a live MK8s node-group create that is stuck in terminal-error provisioning, the CLI can delete that stuck node group via the Nebius SDK and retry destroy again.
  - The command requires explicit confirmation in interactive mode and `--yes` in non-interactive mode.
  - If you only want the infra teardown, use `terraform destroy`. If you only want the rendered app teardown, use `flux destroy`.
  - Example: `nebius-cxcli destroy ~/deployments/tenant-name-example/project-name-example/config.yaml --yes`
- `terraform apply <generated-dir>`
  - Infra-only apply from the generated Terraform bundle. Safe to rerun sequentially for convergence, and does not depend on resolving the original source catalog's module paths.
  - Accepts the project `generated/` directory or a path under `generated/infra/`; other generated subtrees such as `generated/flux/` are rejected so infra-only commands cannot accidentally target app manifests.
  - In a fresh checkout, use this cxcli wrapper instead of raw `terraform apply`: it loads `generated/nebius-cxcli-manifest.json`, recreates the ignored `generated/infra/terraform.auto.tfvars.json`, prepares backend auth, and then runs Terraform from `generated/infra`.
  - For bundled MK8s, it runs VPC networking and Nebius-image GPU-stack compatibility preflight before Terraform apply, so unsupported `platform` + `os` + `gpu_stack_preset` tuples fail in cxcli instead of surfacing as provider API errors during apply.
  - Example: `nebius-cxcli terraform apply ~/deployments/tenant-name-example/project-name-example/generated`
- `terraform destroy <generated-dir>`
  - Infra-only destroy from the generated Terraform bundle. Destructive by intent, requires confirmation or `--yes`, and reuses the same generated-bundle runtime auth/backend/status machinery as `terraform apply`.
  - Accepts the project `generated/` directory or a path under `generated/infra/`; other generated subtrees are rejected.
  - Uses the same guarded destroy-recovery path as top-level `destroy`: stale-lock auto-unlock/retry first, then direct MK8s node-group cleanup only for live stuck create operations.
  - Example: `nebius-cxcli terraform destroy ~/deployments/tenant-name-example/project-name-example/generated --yes`
- `flux apply <generated-dir>`
  - Apps-only direct apply from the rendered Flux tree. Safe to rerun sequentially for day-2 reconciliation. If the rendered manifest needs Terraform-backed handoff or app-input outputs, `flux apply` initializes `generated/infra` first and reads the current outputs from state, but it does not run `terraform apply`. Its Flux API discovery check is resource-type based, so it does not require the app target namespaces to exist before the manifests create them. Use `--target <target-id>` or `--all-targets` when the generated bundle declares more than one built-in cluster target. If GitOps bootstrap is not configured yet, the CLI prints an optional GitOps handoff command; local direct apply remains valid when continuous Git sync is not part of the customer's operating model.
  - Accepts the project `generated/` directory or a path under `generated/flux/`; other generated subtrees such as `generated/infra/` are rejected so apps-only commands cannot accidentally target Terraform artifacts.
  - Example: `nebius-cxcli flux apply ~/deployments/tenant-name-example/project-name-example/generated`
- `flux destroy <generated-dir>`
  - Apps-only direct delete from the rendered Flux tree using the same manifests that `flux apply` manages. Destructive by intent and requires confirmation or `--yes`. Use `--target <target-id>` or `--all-targets` when the generated bundle declares more than one built-in cluster target.
  - Accepts the project `generated/` directory or a path under `generated/flux/`; other generated subtrees are rejected.
  - If the target cluster is reachable but the Flux CRDs are already absent, the CLI prints a skip note instead of surfacing raw `kubectl` resource-mapping errors.
  - Example: `nebius-cxcli flux destroy ~/deployments/tenant-name-example/project-name-example/generated --yes`
- `flux bootstrap <generated-dir>`
  - GitOps bootstrap/reconcile path from the rendered Flux tree. Use this when the cluster should watch the Git repo/path with Flux. Use `--target <target-id>` or `--all-targets` when the generated bundle declares more than one built-in cluster target.
  - Accepts the project `generated/` directory or a path under `generated/flux/`; other generated subtrees are rejected.
  - Normal day-2 updates should replace `generated/` locally, then commit and push one final watched-path snapshot. Do not unbootstrap/rebootstrap Flux just to roll out a new rendered bundle.
  - Example: `nebius-cxcli flux bootstrap ~/deployments/tenant-name-example/project-name-example/generated`

### Command Examples

```bash
# Generator side: create and render artifacts
# Interactive create (default wizard mode)
nebius-cxcli create /path/to/deployments-root

# Day-2 config edits against an existing project
nebius-cxcli component list --config /path/to/config.yaml

# Interactive add of a new Terraform-module-backed infra component
nebius-cxcli component add --config /path/to/config.yaml

# Non-interactive add/remove
nebius-cxcli component add managed-postgresql --config /path/to/config.yaml --no-interactive
nebius-cxcli component add managed-postgresql object-storage@logs-bucket --config /path/to/config.yaml --no-interactive
nebius-cxcli component add infra:vm@worker --config /path/to/config.yaml --no-interactive --network-id infra:vm@worker=vpcnetwork-123 --subnet-id infra:vm@worker=vpcsubnet-123
nebius-cxcli component add mk8s@training-cluster mk8s@serving-cluster --config /path/to/config.yaml --no-interactive
nebius-cxcli component add apps:external-secrets@serving-cluster --config /path/to/config.yaml --no-interactive
nebius-cxcli component add apps:gateway-helm@serving-cluster --config /path/to/config.yaml --no-interactive
nebius-cxcli component remove managed-postgresql@analytics-pg --config /path/to/config.yaml --no-interactive
nebius-cxcli component remove gateway-helm@serving-cluster --config /path/to/config.yaml --no-interactive

# Non-interactive create
nebius-cxcli create /path/to/deployments-root \
  --client-name client-a \
  --tenant-id tenant-123 \
  --project-id project-123 \
  --infra mk8s \
  --network-id vpcnetwork-123 \
  --subnet-id vpcsubnet-123 \
  --app n8n \
  --app-namespace n8n=automation \
  --app-releasename n8n=workflow-core \
  --no-interactive

# Guided create with identity and infra choices preselected
nebius-cxcli create /path/to/deployments-root \
  --client-name client-slug \
  --tenant-id TENANT_ID \
  --project-id PROJECT_ID \
  --infra mk8s,vm,wireguard-gw,ssh-jumphost \
  --no-validate-sources \
  --no-validate-config

# Guided create with multiple infra and app choices preselected
nebius-cxcli create /path/to/deployments-root \
  --client-name client-slug \
  --tenant-id TENANT_ID \
  --project-id PROJECT_ID \
  --infra mk8s,vm \
  --infra wireguard-gw,ssh-jumphost \
  --app n8n,gateway-helm \
  --app cert-manager \
  --no-validate-sources \
  --no-validate-config

# Non-interactive overwrite of an existing resolved project folder
nebius-cxcli create /path/to/deployments-root \
  --client-name client-a \
  --tenant-id tenant-123 \
  --project-id project-123 \
  --force \
  --no-interactive

# Validate and render
nebius-cxcli validate /path/to/config.yaml
nebius-cxcli render /path/to/config.yaml

# Local render against checked-out Terraform modules
nebius-cxcli --source-profile local validate /path/to/config.yaml
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

### Supporting Commands

```bash
nebius-cxcli component list --config /path/to/config.yaml
nebius-cxcli component add infra:vm --config /path/to/config.yaml
nebius-cxcli component add apps:soperator@training-cluster --config /path/to/config.yaml
nebius-cxcli component remove managed-postgresql --config /path/to/config.yaml
nebius-cxcli create /path/to/deployments-root
nebius-cxcli grafana --export-dashboard https://grafana.example.invalid/ --folder-uid folder-uid --dashboard-uid dashboard-uid --dashboard-folder mk8s --datasource "Nebius User Metrics" --attach
nebius-cxcli grafana --dashboard-json ./dashboards/mk8s/custom.json --dashboard-folder mk8s --datasource "Nebius User Metrics" --attach
nebius-cxcli quota-check /path/to/config.yaml
nebius-cxcli quota-request /path/to/config.yaml
nebius-cxcli bootstrap-ci /path/to/config.yaml
nebius-cxcli discover /path/to/deployments-root
nebius-cxcli wireguard --gen-client-conf /path/to/config.yaml
nebius-cxcli wireguard --add-local-subnets /path/to/config.yaml \
  --local-subnet 10.20.0.0/16,10.30.0.0/16
nebius-cxcli ssh-jumphost --add-allowed-cidrs /path/to/config.yaml \
  --allowed-cidr 203.0.113.10/32,198.51.100.0/24
nebius-cxcli ssh-jumphost --list-allowed-cidrs /path/to/config.yaml
nebius-cxcli terraform plan /path/to/generated
nebius-cxcli terraform destroy /path/to/generated --yes
nebius-cxcli terraform unlock /path/to/generated
nebius-cxcli flux destroy /path/to/generated --yes
nebius-cxcli destroy /path/to/config.yaml --yes
nebius-cxcli email /path/to/config.yaml
nebius-cxcli auth --project-config /path/to/config.yaml --validate-profile
```

- Positional target quick map:
  - `create`: pass the deployments root directory; if it does not exist yet,
    cxcli creates it before writing the tenant/project scaffold.
  - `discover`: pass the deployments root or any narrower directory under it, including one project directory or `generated/`.
  - `component list/add/remove`: pass the project `config.yaml` with `--config <config.yaml>` so component selectors can be written first.
  - `grafana`: no positional path; use `--export-dashboard <grafana-base-or-folder-url>` or `--dashboard-json <path>` and optional `--component-sources` with `--attach`.
  - `component`, `validate`, `validate-dashboards`, `quota-check`, `quota-request`, `render`, `deploy`, `soperator`, `upgrade`, `bootstrap-ci`, `wireguard`, `ssh-jumphost`, `destroy`, `email`: operate on the project `config.yaml`.
  - `upgrade node-template`: pass `config.yaml` alone in an interactive terminal to choose the managed MK8s target, version, OS, GPU stack, and options through the wizard; pass an explicit `infra:mk8s@<target>` selector plus one or more of `--to-version <major.minor>`, `--to-os <os>`, and `--to-gpu-stack-preset <preset>` for automation. Supports Terraform-managed MK8s targets only.
  - `validate-generated`: pass `generated/`, one of its subdirectories, or a file under that tree.
  - `terraform *`: pass the project `generated/` directory or a path under `generated/infra/`.
  - `flux *`: pass the project `generated/` directory or a path under `generated/flux/`.
  - `validate-sources`: optional explicit `component_sources.yaml` path; sibling `component_cli_settings.yaml` is loaded when present.
  - `auth`: no positional path; use `--project-config <config.yaml>` or `--project-id`, or omit both with `--validate-profile` to inspect all cached profiles.

- `component list --config <config.yaml>`
  - Shows enabled and available catalog entries for the current project, split between infra modules and app charts.
  - Read-only inspection command for deciding the next add/remove action against the current `config.yaml`.
  - Example: `nebius-cxcli component list --config ~/deployments/tenant-name-example/project-name-example/config.yaml`
- `grafana --export-dashboard <grafana-base-or-folder-url>` / `grafana --dashboard-json <path>`
  - Exports selected Grafana dashboards or normalizes local dashboard JSON, and only updates `component_sources.yaml` when `--attach` is passed.
  - Interactive API exports show sorted folder/dashboard lists; type a starting letter or digit to jump within the list.
  - Example: `nebius-cxcli grafana --export-dashboard https://grafana.example.invalid/ --folder-uid folder-uid --dashboard-uid dashboard-uid --dashboard-folder mk8s --datasource "Nebius User Metrics" --attach`
  - Example: `nebius-cxcli grafana --dashboard-json ./dashboards/mk8s/custom.json --dashboard-folder mk8s --datasource "Nebius User Metrics" --attach`
- Upgrade commands have a dedicated operator section. See [Upgrade](#upgrade)
  for the copy-paste commands, upgrade strategies, drain-timeout defaults,
  Terraform/SDK ownership model, node-group migrations, Helm chart upgrades, and
  manual desired-state fallback.
- `component add [component-selector...] --config <config.yaml>`
  - Adds source-defined infra module rows or app chart rows to an existing project config without recreating the project scaffold.
  - Catalog entries are reusable component types. Each newly added infra row has its own `instance_id`; for scalar named infra modules, the user-facing `inputs.name` or catalog-declared scalar `status.name_input` is the source of truth and `instance_id` is derived from that normalized name. Target-bound app chart rows are selected as `apps:<chart-id>@<target-id>` (bare `<chart-id>@<target-id>` is also accepted when unambiguous). For target-bound charts, the app `instance_id` is the cluster target id, and the same chart can be enabled only once per target.
  - Transient catalog charts such as `nccl-test` are not `--app` or
    `component add` selectors. They declare `usage.lifecycle: transient` and
    no target-facing config ref. Configure NCCL benchmark overrides on the
    command line, then run the benchmark explicitly with
    `nebius-cxcli acceptance-test benchmark`. Deploy does not run NCCL for
    Soperator production-training targets; use Soperator ActiveChecks only for
    additional Slurm-side benchmark/diagnostic clusters or maintenance windows.
  - Interactive mode prompts for infra first and can complete an infra-only add without selecting any app. It asks for app selection only when no infra was selected or when you explicitly choose to add apps too, then confirms the final selection, auto-resolves app chart dependencies plus `release.install_after` prerequisites, and runs the field wizard only for the newly added components. Auto-enabled app rows created by that field wizard are target-scoped to the newly selected target, so adding `mk8s@cluster2` to a config that already has `cluster1` shows and prompts only the new rows such as `grafana@cluster2`. If apps are selected without an enabled MK8s target, cxcli warns immediately and sends the operator back to select `infra:mk8s` or remove the app selection.
  - Newly added app charts prompt for `apps.charts[].version` before the longer
    app config phase. Press Enter to keep the pinned `component_sources.yaml`
    version, or type a known chart package version. Non-interactive
    `component add` accepts `--app-namespace`, `--app-releasename`, and
    `--app-version` for app rows added by that operation.
  - When that wizard reaches per-component field phases, infra components
    default to `y` and app charts default to `n`. Answering `n` for a newly
    added infra component cancels that pending add instead of writing an
    unconfigured row; answering `n` for an app chart keeps the selected chart
    with its defaults.
  - That field wizard offers all discoverable required and optional fields for each new component, including editable literal catalog defaults. Required fields must be filled before advancing; optional blanks stay implicit when they still match module/chart defaults.
  - For `apps:soperator`, the create/component wizard uses
    `production-cluster` and asks for the worker profile before MK8s/SFS field
    materialization so CPU-only, GPU-only, or mixed layout is known before
    shape/fabric helpers and target GPU deployment-testing prompts. `production-cluster`
    creates the complete MK8s+SFS+Soperator five-role bundle with `system`
    autoscaling from 3 to 5 nodes, two fixed `controller`, `login`, and
    `accounting` nodes, one worker node by default, and skips external
    placement prompts. If an existing managed MK8s target already has custom
    node groups such as `cpu-nodes`/`gpu-nodes` but not the Soperator
    `system`, `controller`, `login`, and `accounting` service-role groups,
    `component add apps:soperator` rejects it before writing config changes.
    Existing external Nebius MK8s targets should use
    `nebius-cxcli ext-soperator onboard <config.yaml-or-deployments-root>`; see
    [Soperator Commands](#soperator-commands) for onboarding, upgrade, and
    managed-vs-external upgrade rules.
  - If `qq` stops the field wizard while any newly added component still has unresolved required fields, no `config.yaml` changes are written. If only optional fields were skipped, the edit is saved.
  - Source validation runs by default, mirroring `create`: infra sources are checked first, and app chart validation is limited to selected or auto-enabled app rows, including a final late-auto-enabled app pass before `config.yaml` is written. Use `--no-validate-sources` only when you intentionally want to skip that source check.
  - Infra-only adds do not re-resolve Helm chart dependencies for already-enabled app rows; app chart dependency resolution runs when the add request includes app components.
  - The command prompts for selected infra resource names before live provider checks, then revalidates the existing Nebius tenant/project scope before provider-backed field prompts, so missing SDK credentials or inaccessible scope are surfaced as explicit errors. Provider-backed Nebius SDK requests are bounded by `NEBIUS_CXCLI_PROVIDER_REQUEST_TIMEOUT_SECONDS` when set, or 15 seconds by default.
  - For subnet-attached infra, `--network-id` and `--subnet-id` can preseed the
    added row. A bare value is valid only when exactly one applicable infra row
    is added. With multiple applicable rows, scope each value, for example
    `--network-id infra:vm@worker=vpcnetwork-... --subnet-id infra:vm@worker=vpcsubnet-...`.
    Use `--network-ref` / `--subnet-ref` for planned VPC resources created by
    the same config, for example
    `--network-ref infra:vm@worker=vpc@worker-vpc.network_id --subnet-ref infra:vm@worker=vpc@worker-vpc.subnets.worker.id`.
    If those flags are omitted in non-interactive mode, cxcli auto-selects
    only when the combined live/planned choices contain exactly one valid
    network and exactly one subnet for that network; otherwise it fails with a
    scoped-flag hint.
  - Simple string-list Terraform inputs are edited as comma-separated values. MysteryBox `inputs.secrets` uses the guided Secret/policy/key loop, and VPC `inputs.subnets` uses an optional guided subnet loop. Other complex inputs such as client lists and MK8s override objects are edited as YAML/JSON values in the wizard.
  - Non-interactive mode accepts component selectors directly: `<component-id>`, `infra:<component-id>`, `apps:<component-id>`, `all`, `none`, or `<component-id>@<resource-name-or-target-id>`. Scoped app selectors use the plural `apps:` prefix; singular `app:` is invalid. For example: `nebius-cxcli component add apps:external-secrets@mk8s --config <config.yaml> --no-interactive`.
  - In interactive mode, scalar named infra modules prompt for the resource name
    before field prompts, defaulting to the next unique normalized name such as
    `vm-2`. The saved `instance_id` is derived from that normalized name and
    must stay aligned with the scalar resource-name input. In non-interactive
    mode, a bare infra selector creates the default named row when absent;
    `<component-id>@<resource-name>` chooses the resource name or creates another
    named infra row and seeds the same value into `inputs.name` or the
    catalog-declared scalar `status.name_input`, for example
    `object-storage@logs-bucket` or `mk8s@training-cluster`. For app charts, the
    explicit suffix is the cluster target id and becomes
    `apps.charts[].instance_id`; duplicate
    `apps:<chart-id>@<target-id>` installs are skipped. When more than one cluster
    target is enabled, app charts must be added with an explicit target
    instance, for example `apps:n8n@cluster2`.
  - `object-storage` now represents one bucket per enabled module instance and requires `inputs.name`.
  - Existing component values are preserved. The command updates only `config.yaml`; existing `generated/` artifacts and live resources are unchanged until you run `render` and then deploy/destroy as needed. After the edit, run `validate` and `render` again.
  - Example: `nebius-cxcli component add managed-postgresql --config ~/deployments/tenant-name-example/project-name-example/config.yaml --no-interactive`
- `component remove [component-selector...] --config <config.yaml>`
  - Removes enabled infra module rows or app chart rows from an existing config.
  - Interactive mode prompts separately for infra and apps and asks for confirmation before editing.
  - Non-interactive mode accepts enabled row selectors: `<component-id>`, `infra:<component-id>`, `apps:<component-id>`, `all`, `none`, `<row-id>`, or `<component-id>@<resource-name-or-target-id>`. For scalar named infra, the row id is the normalized resource name; for target-bound app charts, it is the target id. When multiple rows match the same component type, remove by exact row id or `<component-id>@<resource-name-or-target-id>`.
  - Already-absent selectors are skipped, so rerunning the same remove command leaves the config unchanged.
  - When removing a cluster target, the command also removes app chart rows and `deploy.targets[]` settings whose `instance_id` points at that target.
  - The command fails fast when the resulting config would still leave unresolved app dependencies or component input bindings.
  - The command updates only `config.yaml`; existing `generated/` artifacts and live resources are unchanged until you run `render` and then deploy/destroy as needed. After the edit, run `validate` and `render` again.
  - Example: `nebius-cxcli component remove managed-postgresql@analytics-pg --config ~/deployments/tenant-name-example/project-name-example/config.yaml --no-interactive`
- `create <deployments-root>`
  - Scaffolds one name-derived tenant/project folder with `config.yaml` and the generated-folder skeleton.
  - The deployments root may be an existing directory or a new path; cxcli
    creates the root directory when it is missing, then writes the resolved
    `<tenant-folder>/<project-folder>` below it.
  - Operators still enter `tenant_id` / `project_id`; the CLI resolves names only for the folder path after ID validation succeeds.
  - Interactive `create` prompts for `tenant_id` / `project_id` first and only warns when that resolved target already exists; choosing a different new project under the same deployments root does not trigger an overwrite warning.
  - Unless you explicitly pass `--tenant-id` / `--project-id`, interactive `create` starts those identity prompts blank instead of prefilling values from an existing project under the deployments root.
  - To move faster through the guided wizard, preseed identity and component choices with flags, for example:
    `nebius-cxcli create /path/to/deployments-root --client-name client-slug --tenant-id TENANT_ID --project-id PROJECT_ID --infra mk8s,vm,wireguard-gw,ssh-jumphost --no-validate-sources --no-validate-config`.
    Without `--no-interactive`, this still runs the wizard for remaining prompts
    such as region, optional email, app selection, and component fields.
    These flags skip source validation and post-write config validation; they do
    not skip the warning-only live quota/capacity assessment. Add `--app none`
    when you also want to skip the app-selection prompt.
  - `--infra` and `--app` can each be repeated or passed as comma-separated
    lists. For example, `--infra mk8s,vm --infra wireguard-gw,ssh-jumphost`
    and `--app n8n,gateway-helm --app cert-manager` select multiple infra and
    app components in one create run. App chart dependencies can still add
    required chart rows automatically.
  - App chart rows use the active `component_sources.yaml` version pin by
    default. In `create` and `component add`, use
    `--app-version soperator=4.0.1-ps.2`, or type the version at the
    interactive app chart version prompt before the full app config phase, when
    you intentionally want a different published chart package. With source
    validation enabled, cxcli checks that requested version before writing
    `config.yaml`.
  - Interactive `create` offers app chart selection only after the infra
    selection includes an MK8s target. To configure Soperator interactively,
    select `infra:mk8s` first and then `apps:soperator`; non-interactive
    `--app soperator` still expands to the production MK8s+SFS+Soperator
    bundle.
  - Non-interactive subnet-attached infra can receive VPC IDs with
    `--network-id` and `--subnet-id`. A bare value is valid only when exactly
    one applicable infra row is selected. With multiple applicable rows, scope
    each value, for example
    `--network-id infra:vm@worker=vpcnetwork-... --subnet-id infra:vm@worker=vpcsubnet-...`.
    Use `--network-ref` / `--subnet-ref` instead when the target network or
    subnet is planned by an enabled `infra:vpc` row in the same config.
    If those flags are omitted, non-interactive create auto-selects only when
    the combined live/planned choices contain exactly one valid VPC network and
    exactly one subnet in that network. In interactive create, selected
    `infra:vpc` rows are prompted before subnet-consuming infra so the operator
    can create a subnet and then choose that planned subnet for MK8s, VM, NFS,
    WireGuard gateway, or SSH jump-host rows in the same wizard pass.
  - When the resolved project folder for the same `tenant_id`/`project_id` already exists, interactive `create` warns and asks for confirmation before recreating that folder from scratch unless `--force` is provided; non-interactive reruns require `--force`.
  - Uses exactly one cxcli-managed `.gitignore` at the deployments root for all tenant/project folders below it; nested cxcli-managed deployments roots are rejected instead of supported as a compatibility path.
  - Existing project `client_info` values are not offered back as defaults; overwrite restarts those prompts from the normal create defaults, existing component rows are not merged, and files already under that resolved project path are deleted during the overwrite.
  - After writing the resulting `config.yaml`, `create` runs the internal warning-only post-create validation by default. Use `--no-validate-config` only when you intentionally want to skip that post-write validation; the separate warning-only live quota/capacity assessment still runs.
  - `create` also runs a best-effort live Nebius quota check for bundled infra components and warns when the selected shape already exceeds current quota. GPU quota dimensions come from the Capacity Dashboard for the selected platform/preset/fabric shape. It does not block render or config edits, does not reserve capacity, and is not a wizard-selectable deploy gate. Confirmed requestable quota shortages print the exact `quota-request <config.yaml>` follow-up command, while capacity-only GPU shortages point to choosing another available shape or region.
  - Non-blocking quota coverage-gap detail stays available through `quota-check` and the generated manifest rather than being repeated during normal `create` output.
  - In profile-backed MK8s flows such as Soperator `production-cluster`, interactive `create` and `component add` ask for the Soperator worker profile before MK8s shape fields and target GPU deployment-testing prompts, so CPU-only, GPU-only, or mixed worker layout is known before those fields are offered. CPU defaults are prompted first as `inputs.node_group_defaults.cpu.platform` and `inputs.node_group_defaults.cpu.preset`; they are required and provider-defaulted rather than left blank. GPU defaults are prompted next as `inputs.node_group_defaults.gpu.platform`, then `inputs.node_group_defaults.gpu.reservation.policy`, then `inputs.node_group_defaults.gpu.preset`; they are also required when the selected profile materializes GPU workers, while CPU-only profiles skip and prune the inactive GPU helper scope. The GPU preset prompt is a policy-matching live Capacity Dashboard row selector for the selected platform/region, showing preset, fabric, regular-vm or reserved VM slots, and GPU totals. `AUTO` keeps reserved-backed and regular-vm choices with reserved recommendations first, `STRICT` lists reserved-capacity choices, and `FORBID` lists regular-vm choices. The GPU preset list is not filtered by an existing derived fabric, so regular 1-GPU presets and reserved-backed multi-GPU presets can both remain selectable when the selected reservation policy allows them. `inputs.node_group_defaults.gpu.reservation.policy` defaults to `AUTO` and is materialized into the generated GPU worker node group's `reservation.policy`; there is no global `create` flag because reservation policy is per GPU worker/node group. `inputs.gpu_clusters.<key>.infiniband_fabric` is the canonical fabric field, but it is not shown as a raw wizard prompt. Selecting a cluster-capable multi-GPU row writes the row's preset to `inputs.node_group_defaults.gpu.preset` and the row's fabric to `inputs.gpu_clusters.<key>.infiniband_fabric`; GPU node groups reference that cluster through `gpu_cluster_key`. Selecting a 1-GPU Ethernet-only row writes only the preset and clears profile-managed GPU-cluster references, including `inputs.gpu_clusters` entries and worker `gpu_cluster_key` values. Plain MK8s-only create uses concrete `inputs.node_groups.*` entries and follows the same row materialization rule. If the chosen preset's live SDK metadata does not allow GPU clustering, stale fabric values fail fast at render/validate instead of surfacing first at `terraform apply`. GPU interconnect guidance is printed before preset selection rather than repeated in every preset label. If live fabric rows are unavailable for a keyed GPU cluster, validation rejects the missing `inputs.gpu_clusters.<key>.infiniband_fabric` and quota assessment reports a coverage gap instead of checking any-fabric GPU capacity.
  - In the bundled VM flow, preemptible VM prompts are shown only after a GPU platform is selected. When `inputs.preemptible_enabled=true`, cxcli writes `inputs.recovery_policy: FAIL`, matching the Compute Terraform contract that renders `preemptible.on_preemption = "STOP"` without the deprecated priority field.
  - In interactive mode, `q` backs up through optional wizard steps and `qq` stops the wizard immediately. In TTY list and checkbox prompts, those are key shortcuts rather than Back/Quit rows that must be selected. At the first wizard step, `q` asks whether to exit. After infra and app selection plus dependency resolution, the wizard prints one `Component selections:` summary with target-bound app labels such as `soperator on mk8s`; per-field context then stays focused on the current component or target feature. The wizard prints visible `Infra` and `Apps` section separators and echoes each answered field as `Selected <path> = <value>` with secret-like fields redacted, so operators can scan the terminal history before reviewing the saved `config.yaml`. If `qq` stops while any selected component still has unresolved required fields, `create` cancels before writing or overwriting `config.yaml` and `generated/`; existing project folders stay untouched. If only optional fields were skipped, the current config is written.
  - For selected components, the field wizard offers all discoverable required and optional fields, including editable literal catalog defaults. Required blanks are rejected immediately; optional blanks keep defaults implicit when possible.
  - Per-component field phases default to `y` for infra and `n` for apps.
  - Example: `nebius-cxcli create ~/deployments`
- `quota-check <config.yaml>`
  - Runs a live Nebius quota check for the enabled infra components in the current project config without rendering or deploying anything.
  - Reruns against current Nebius state every time instead of relying on the create-time warning result.
  - For day-2 MK8s scale edits, quota-check best-effort discounts capacity already managed in Terraform state, so a 4-to-6 node edit is checked as net-new capacity when the sibling generated bundle/state is available.
  - When an operator identity is available, quota assessment prefers that operator auth over the auto-bootstrapped project runtime service account so tenant-scope quota and Capacity Dashboard reads can still succeed during day-2 checks and reruns. If only runtime project auth is available, tenant-scope results can remain partially unavailable and are still reported as warnings instead of being treated as confirmed sufficiency.
  - Uses the same SDK-backed quota logic as `create`, `render`, and `deploy`, including live compute preset lookups for MK8s, jump hosts, and managed PostgreSQL.
  - GPU quota dimensions come only from the live Capacity Dashboard rows for the exact platform + region + preset + fabric shape. There is no separate Capacity Block Group overlay or standalone `compute.gpucluster.count` GPU check anymore.
  - Prints a concise per-component confirmed summary for the quota dimensions that were successfully checked, including the exact checked quota names listed one per line. Components with confirmed shortages or unresolved live limits stay out of that list; components with coverage gaps still appear there with a partial-coverage note, and the missing dimensions are listed separately.
  - Returns non-zero only when quota insufficiency is confirmed. Coverage gaps, unresolved live limits, or partial quota lookup failures are reported as warnings but do not make the command fail on their own.
  - `--all-regions` additionally replays the current config's quota requirements across all discovered tenant/project regions and any GPU regions visible in the Capacity Dashboard, then prints per-region availability for the same shape. The selected config region still decides pass/fail.
  - When quota-check reports confirmed insufficiency and `--all-regions` was not requested, the CLI suggests the exact `quota-check --all-regions` rerun command as the next diagnostic step. The quota-request hint appears only when the current shortage has an actual tenant/project quota target; pure GPU Capacity Dashboard capacity shortages instead point operators toward a different platform/preset/fabric or region.
  - Coverage-gap-only warnings mean the estimator could not check every quota dimension from the current config/API surface; they do not by themselves imply a shortage in the already-checked GPU/CPU quotas. The unresolved reasons are listed one per line under the affected component.
  - Example: `nebius-cxcli quota-check ~/deployments/tenant-name-example/project-name-example/config.yaml`
- `quota-request <config.yaml>`
  - Plans quota requests for confirmed live shortages in the current project config, then submits them through the internal Nebius request path only when that path is available and permitted.
  - Acts only on shortages confirmed by the current live assessment. If the current config has no confirmed insufficiency at run time, it exits as a no-op instead of pre-requesting quota just because the config exists.
  - Supports manual day-2 edits such as changing a count from 4 to 6 in `config.yaml`; when generated Terraform state exists for MK8s, it requests only the net-new shortfall rather than the whole desired count.
  - If a GPU shortage is only Capacity Dashboard availability for the selected shape and no tenant/project quota target is constraining it, the workflow is to select another available shape or region rather than submit a quota request.
  - Public/customer runs fall back cleanly to manual web-console follow-up and print the exact tenant/project quota entries plus minimum target limits to request.
  - Unresolved live limits and estimator coverage gaps remain report-only; they are not requested automatically.
  - Example: `nebius-cxcli quota-request ~/deployments/tenant-name-example/project-name-example/config.yaml`
- `bootstrap-ci <config.yaml>`
  - Generates or reconciles the customer GitHub Actions workflow, always reconciles GitHub email settings from local `email --setup`, and optionally bootstraps/syncs the required Nebius CI auth secrets. The generated workflow watches and deploys only canonical `<tenant-folder>/<project-folder>/generated/**` paths.
  - If the deployments root is the repository root, the generated workflow uses `NEBIUS_DISCOVER_TARGET: .` and the canonical `*/*/generated/**` trigger glob instead of embedding `./` in path filters.
  - The workflow file is CLI-managed. Re-running `bootstrap-ci` automatically reconciles `.github/workflows/nebius-deployments.yml` to the latest generated contract and is idempotent when no drift exists.
  - Generated workflows validate changed bundles with `nebius-cxcli validate-generated --portable` before `nebius-cxcli terraform plan` and `nebius-cxcli terraform apply`. That generated-bundle validation now includes the same strict readiness, VPC networking preflight, and live quota/capacity gate used by local deploy preflight.
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
  - Example: `nebius-cxcli bootstrap-ci ~/deployments/tenant-name-example/project-name-example/config.yaml`
- `discover <deployment-scope-dir>`
  - Returns changed deployment projects for CI matrix generation.
  - Uses local git change detection plus readable project `config.yaml` files; it does not call Nebius APIs or require Nebius credentials.
  - Accepts the deployments root or any narrower directory under it, including one project directory or `generated/`.
  - Scope filtering is project-aware: both `--all` and normal changed-file discovery still resolve the matching project when the scope is a project subdirectory such as `generated/`.
  - Example: `nebius-cxcli discover ~/deployments --all`
- `wireguard`
  - Manages day-2 WireGuard work for a deployed `wireguard-gw`.
  - Requires the current `config.yaml` and sibling `generated/` bundle to
    contain the same selected component row.
  - Use exactly one mode: `--gen-client-conf <config.yaml>`,
    `--add-local-subnets <config.yaml>`, or
    `--remove-local-subnets <config.yaml>`.
  - Add/remove subnet mode requires exactly one comma-separated `--local-subnet`
    value. Client generation may repeat `--local-subnet` for per-client routes.
  - Example: `nebius-cxcli wireguard --gen-client-conf ~/deployments/tenant-name-example/project-name-example/config.yaml`
- `ssh-jumphost`
  - Manages day-2 SSH source CIDRs for a deployed `ssh-jumphost`.
  - Requires the current `config.yaml` and sibling `generated/` bundle to
    contain the same selected component row.
  - Use exactly one mode: `--add-allowed-cidrs <config.yaml>`,
    `--remove-allowed-cidrs <config.yaml>`, or
    `--list-allowed-cidrs <config.yaml>`.
  - Add/remove mode requires exactly one comma-separated `--allowed-cidr`
    value. The VM-local helper refuses to apply an empty allowlist.
  - Example: `nebius-cxcli ssh-jumphost --add-allowed-cidrs ~/deployments/tenant-name-example/project-name-example/config.yaml --allowed-cidr 203.0.113.10/32`
- `validate-generated <generated-path>`
  - Validates an existing rendered bundle from `generated/`, one of its subdirectories, or a file under that tree, including generated-bundle readiness and live quota/capacity.
  - Example: `nebius-cxcli validate-generated ~/deployments/tenant-name-example/project-name-example/generated`
- `acceptance-test smoke <config.yaml>`
  - Runs explicit post-deploy acceptance smoke suites and writes JSON reports only under `generated/reports/`.
  - Requires `--suite`; omitted `--suite` fails fast instead of choosing a K8s or Slurm suite.
  - Use `--suite slurm` for Slurm all-node smoke and `--suite k8s-cuda` for Kubernetes CUDA smoke on MK8s GPU targets.
  - After a suite is selected, defaults to every generated target when `--target` is omitted.
  - Example: `nebius-cxcli acceptance-test smoke ~/deployments/tenant-name-example/project-name-example/config.yaml --target sop-cluster1 --suite slurm --batch-size 128 --concurrency 8 --fail-fast`
- `acceptance-test benchmark <config.yaml>`
  - Runs explicit post-deploy benchmark suites and writes JSON reports only under `generated/reports/`.
  - Requires `--suite`; omitted `--suite` fails fast instead of defaulting to K8s NCCL.
  - Use `--suite k8s-nccl` for the Kubernetes NCCL benchmark and `--suite slurm-nccl` for the Slurm NCCL benchmark.
  - After a suite is selected, defaults to every generated target when `--target` is omitted.
  - Benchmark node count, timeout, and RDMA bandwidth threshold are run-only flags, not `config.yaml` settings.
  - Example: `nebius-cxcli acceptance-test benchmark ~/deployments/tenant-name-example/project-name-example/config.yaml --target mk8s-prod --suite k8s-nccl --max-nodes 4 --timeout 20m --average-bus-bandwidth-threshold-gbps 300`
- `terraform plan <generated-path>`
  - Infra-only plan from the generated Terraform bundle.
  - Example: `nebius-cxcli terraform plan ~/deployments/tenant-name-example/project-name-example/generated`
- `terraform destroy <generated-path>`
  - Destroys the generated Terraform bundle in place after an explicit confirmation or `--yes`.
  - Can auto-clear a stale Terraform state lock and retry once, and can clean up a live stuck MK8s node-group create before retrying again when that is the remaining destroy blocker.
  - Example: `nebius-cxcli terraform destroy ~/deployments/tenant-name-example/project-name-example/generated --yes`
- `terraform unlock <generated-path>`
  - Inspects and clears a stale remote Terraform state lock for the generated infra bundle.
  - Example: `nebius-cxcli terraform unlock ~/deployments/tenant-name-example/project-name-example/generated --force`
- `flux destroy <generated-path>`
  - Deletes the rendered Flux resources from the target cluster after an explicit confirmation or `--yes`.
  - Example: `nebius-cxcli flux destroy ~/deployments/tenant-name-example/project-name-example/generated --yes`
- `destroy <config.yaml>`
  - Destroys all rendered project resources represented by the sibling generated bundle after an explicit confirmation or `--yes`.
  - Deletes rendered apps first for enabled app charts, including managed MK8s handoff bundles, so Kubernetes finalizers and CSI cleanup can remove app-owned resources such as PVC-backed disks before Terraform destroys the cluster.
  - For onboarded external MK8s targets, deletes only cxcli-managed rendered app/add-on resources and never destroys the external cluster or node groups.
  - Resolves sibling `generated/` automatically and still uses the generated manifest as the authoritative teardown contract.
  - Example: `nebius-cxcli destroy ~/deployments/tenant-name-example/project-name-example/config.yaml --yes`
- `email [config.yaml]`
  - Sends only `generated/reports/deploy-report.md` to `client_info.notifications.email` via SMTP and fails fast if that file is missing.
  - Omit the path only when using `--setup`.
  - Resolves sibling `generated/` automatically and reads the recipient/runtime snapshot from the generated manifest rather than live source edits.
  - The recipient email comes from the generated-bundle runtime config snapshot in `generated/nebius-cxcli-manifest.json`, not from the rendered report artifact.
  - SMTP is disabled by default. Run `nebius-cxcli email --setup` to create, update, or remove local SMTP settings under `~/.config/nebius-cxcli/email.yaml`.
  - Local email config stores host/port/STARTTLS/from and optional username/password. Runtime `SMTP_HOST`, `SMTP_PORT`, `SMTP_STARTTLS`, `SMTP_FROM`, `SMTP_USERNAME`, and `SMTP_PASSWORD` still override those local values when set. Setup, GitHub sync, and sending require STARTTLS so report contents and optional SMTP credentials are not sent in plaintext.
  - Per-client send/no-send stays in `config.yaml`: `client_info.notifications.email_enabled: true|false`.
  - When `client_info.notifications.email_enabled` is `true` but SMTP is missing, the command warns and exits successfully instead of failing the deploy/email workflow.
  - The email path redacts tenant and project identifiers in the subject/body; the on-disk `deploy-report.md` stays unchanged.
  - Example: `nebius-cxcli email ~/deployments/tenant-name-example/project-name-example/config.yaml`
- `auth`
  - Manages runtime auth profiles and optional GitHub environment secret sync.
  - Target either a project config with `--project-config <config.yaml>` or a manual identity with `--project-id <id>` plus `--client-name <name>` when needed; do not mix the two modes.
  - Example: `nebius-cxcli auth --project-config ~/deployments/tenant-name-example/project-name-example/config.yaml --validate-profile`

Common command flags:

- `component add`:
  `--no-interactive`, `--app-namespace`, `--app-releasename`, `--app-version`,
  `--network-id`, `--subnet-id`, `--network-ref`, `--subnet-ref`,
  `--validate-sources/--no-validate-sources`
- `component remove`:
  `--no-interactive`
- `create`:
  `--client-name`, `--tenant-id`, `--project-id`, `--region-id`, `--email`, `--infra`, `--app`, `--app-namespace`, `--app-releasename`, `--app-version`, `--network-id`, `--subnet-id`, `--network-ref`, `--subnet-ref`, `--validate-sources/--no-validate-sources`, `--validate-config/--no-validate-config`, `--no-interactive`, `--force`
- `bootstrap-ci`:
  `--auth-bootstrap/--no-auth-bootstrap`, `--github-repo`, `--github-token-env`, `--cli-ref`
- `grafana`: `--export-dashboard`, `--dashboard-json`, `--output-dir`, `--folder-uid`, `--dashboard-uid`, `--overwrite`, `--attach`, `--component-sources`, `--dashboard-folder`, `--datasource`, `--token-env`, `--username`, `--password-env`
- `validate-dashboards`: `--target`
- `quota-check`: `--all-regions`
- Global source-selection for config-based commands: `--source-profile`, `NEBIUS_CXCLI_COMPONENT_SOURCES_PROFILE`
- `validate-generated`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, `--portable`
- `render`: `--force`
- `deploy`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`,
  `--skip-validations`, `--skip-validation`, `--target`, `--all-targets`,
  `--job-policy`, `--cancel-job`, `--requeue-job`, `--job-wait-timeout`,
  `--job-refresh-interval`
- `acceptance-test smoke`: `--target`, `--all-targets`, `--suite`,
  `--batch-size`, `--concurrency`,
  `--continue-on-failure/--fail-fast`
- `acceptance-test benchmark`: `--target`, `--all-targets`, `--suite`,
  `--continue-on-failure/--fail-fast`,
  `--max-nodes`, `--timeout`, `--average-bus-bandwidth-threshold-gbps`
- `upgrade node-template`: `--to-version`, `--to-os`, `--to-gpu-stack-preset`, `--node-group`, `--dry-run`, `--strategy`, `--strategy-max-surge-count`, `--drain-timeout`, `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, `--skip-validations`, `--skip-validation`, `--interactive/--no-interactive`
- `upgrade node-group`: `--node-group`, `--to-platform`, `--to-preset`, `--to-os`, `--to-gpu-stack-preset`, `--to-fabric`, `--dry-run/--execute`, `--approve/--no-approve`
- `soperator backup`: `--target`, `--backup-dir`, `--namespace`,
  `--release-name`, `--kube-context`, `--dry-run`,
  `--interactive/--no-interactive`
- `soperator discover`: `--target`, `--output-dir`, `--namespace`,
  `--release-name`, `--kube-context`, `--to-chart-version`,
  `--to-k8s-version`, `--to-os`, `--to-gpu-stack-preset`,
  `--redaction`, `--interactive/--no-interactive`
- `soperator restore`: `--target`, `--namespace`, `--kube-context`,
  `--dry-run/--execute`, `--approve/--no-approve`,
  `--restore-accounting-db/--no-restore-accounting-db`
- `soperator upgrade`: `--target`, `--to-chart-version`, `--to-k8s-version`,
  `--to-os`, `--to-gpu-stack-preset`, `--node-group`, `--strategy`,
  `--strategy-max-surge-count`, `--drain-timeout`, `--backup-dir`,
  `--job-policy`, `--cancel-job`, `--requeue-job`, `--job-wait-timeout`,
  `--job-refresh-interval`, `--dry-run`,
  `--approve-remediation/--no-approve-remediation`,
  `--allow-unsupported-soperator-upgrade-path`,
  `--interactive/--no-interactive`
- `ext-soperator backup`: `--client-name`, `--tenant-id`, `--project-id`,
  `--target`, `--backup-dir`, `--namespace`, `--release-name`,
  `--kube-context`, `--cluster-id`, `--access`, `--dry-run`
- `ext-soperator restore`: `--target`, `--namespace`, `--kube-context`,
  `--dry-run/--execute`, `--approve/--no-approve`,
  `--restore-accounting-db/--no-restore-accounting-db`
- `ext-soperator discover`: `--client-name`, `--tenant-id`, `--project-id`, `--target`,
  `--output-dir`, `--namespace`, `--release-name`, `--kube-context`, `--cluster-id`, `--access`,
  `--to-chart-version`, `--to-k8s-version`, `--to-os`,
  `--to-gpu-stack-preset`, `--redaction`
- `ext-soperator onboard`: `--client-name`, `--tenant-id`, `--project-id`,
  `--region-id`, `--email`, `--cluster-id`, `--target-id`,
  `--kube-context`, `--access`, `--storage-mode`, `--compute-mode`,
  `--to-chart-version`, `--to-k8s-version`, `--source-version`,
  `--allow-unsupported-soperator-upgrade-path`, `--worker-rollout-strategy`,
  `--worker-wave-groups`, `--worker-wave-percent`, `--max-parallel-worker-groups`,
  `--strategy-max-surge-count`, `--strategy-max-unavailable-count`,
  `--strategy-drain-timeout`, `--validate-sources/--no-validate-sources`,
  `--no-interactive`
- `ext-soperator upgrade`: `--target`, `--backup-dir`, `--job-policy`,
  `--cancel-job`, `--requeue-job`, `--job-wait-timeout`, `--job-refresh-interval`,
  `--dry-run/--execute`, `--approve/--no-approve`,
  `--approve-remediation/--no-approve-remediation`,
  `--allow-unsupported-soperator-upgrade-path`,
  `--interactive/--no-interactive`, `--worker-rollout-strategy`,
  `--worker-wave-groups`, `--worker-wave-percent`,
  `--max-parallel-worker-groups`, `--strategy-max-surge-count`,
  `--strategy-max-unavailable-count`, `--strategy-drain-timeout`
- `upgrade helm-chart`: `--to-version`, `--dry-run`,
  `--interactive/--no-interactive` (non-Soperator app charts only)
- `destroy`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, `--yes`
- `discover`: `--all`
- `wireguard`:
  `--gen-client-conf`, `--add-local-subnets`, `--remove-local-subnets`,
  `--component`, `--local-subnet`, `--ssh-user`, `--ssh-private-key`,
  `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, plus generation-only
  `--client-name`, `--dns`, `--persistent-keepalive`, `--output-dir`, `--force`
- `ssh-jumphost`:
  `--add-allowed-cidrs`, `--remove-allowed-cidrs`, `--list-allowed-cidrs`,
  `--component`, `--allowed-cidr`, `--ssh-user`, `--ssh-private-key`,
  `--auto-auth-bootstrap/--no-auto-auth-bootstrap`
- `terraform plan`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`
- `terraform apply`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`
- `terraform destroy`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, `--yes`
- `terraform unlock`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, `--force`
- `flux apply`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, `--target`,
  `--all-targets`, `--job-policy`, `--cancel-job`, `--requeue-job`,
  `--job-wait-timeout`, `--job-refresh-interval`
- `flux destroy`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, `--yes`, `--target`, `--all-targets`
- `flux bootstrap`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, `--target`, `--all-targets`
- `auth`:
  `--project-id`, `--project-config`, `--client-name`, `--profile`, `--endpoint`, `--sdk-config-file`, `--github-repo`, `--github-token-env`, `--validate-profile`, `--create`, `--recreate`, `--bootstrap-ci`

## Auth Workflow

Terraform runtime auth behavior:

- Generated `providers.tf` uses direct provider fields (`service_account.account_id/public_key_id/private_key_file`) and sets `module_name`.
- Generated `backend.tf` stores only non-secret backend location/settings; credentials are supplied by environment/runtime profile.
- Runtime values are passed through Terraform variables (`TF_VAR_*`) instead of provider `_env` indirection.
- MysteryBox payload material is runtime-only. Generated Terraform roots expose `payload_values` as a sensitive root variable and pass it into the module, but do not write it to generated tfvars or manifests. Interactive local deploy/plan/apply commands ask for missing first-deploy payload values with hidden input; CI and other non-interactive runs should set `TF_VAR_mysterybox_payload_values` for the default `mysterybox` instance, or the rendered instance variable such as `TF_VAR_secretstore_alpha_payload_values`, with JSON/YAML shape `{"secret-name":{"PAYLOAD_KEY":"value"}}`. After the first versions exist, cxcli records their `mbsecver-...` IDs in source and generated artifacts so reruns do not need the sensitive payload values again, including reruns after a transient provider polling failure.
- Local runtime auth can be auto-bootstrapped with a dedicated service account name: `nebius-cxcli-tf-sa`.
- Auto-bootstrapped runtime auth material is cached under `~/.config/nebius-cxcli/<client_name>-<project-id>/` to avoid creating new key material every run.
- The local runtime auth cache intentionally stores long-lived Terraform private-key material and Object Storage access keys in cleartext files protected by a `0700` profile directory and `0600` files. Keep this cache on local protected storage, avoid placing `NEBIUS_CXCLI_RUNTIME_AUTH_DIR` inside synced or backed-up folders, and use `nebius-cxcli auth --recreate ...` to rotate the cached keys when the local machine or cache location may be exposed.
- ESO MysteryBox auth does not use a local cxcli auth cache. The in-cluster Kubernetes Subject Credentials Secret is the persisted ESO auth location; deploy/Flux commands create or replace it only when it is missing, invalid, or stale.
- The Terraform runtime auth key flow is authorized-key based: the CLI generates keypair material, uploads the public key for `nebius-cxcli-tf-sa`, and stores private key material locally for Terraform runtime use. ESO uses the same Nebius authorized-key API pattern for `mysterybox-sa`, but stores the private key only in the configured Kubernetes Secret.
- Terraform backend init uses AWS-compatible Object Storage keys (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`); the runtime auth profile cache auto-populates these for local runs.
- `render` generates `.terraform.lock.hcl` with backend-disabled `terraform init -backend=false` and removes transient `.terraform/` workdir state afterward, so render does not need runtime auth/bootstrap side effects just to pin providers.

`auth` behavior:

- Target selection has two canonical modes:
  - `--project-config <config.yaml>` resolves both `project_id` and `client_name` from the config and must not be combined with `--project-id` or `--client-name`.
  - `--project-id <id>` is the manual target mode; use `--client-name <name>` when creating or when the project id cannot be mapped to exactly one cached profile.
  - Omitting both target options is valid only for global `--validate-profile`, which inspects every cached runtime auth profile.
- `auth` targets only the Terraform runtime auth cache. ESO MysteryBox credential rotation is cluster-runtime plus IAM state: remove or replace the configured Kubernetes Subject Credentials Secret, revoke the old Nebius authorized key when you still have its ID, and rerun deploy/Flux so cxcli creates fresh Subject Credentials. deploy/Flux also replaces the Secret automatically when it is invalid or references a stale Nebius authorized key.
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

`bootstrap-ci <config.yaml>` remains the full CI workflow bootstrap command and can still perform complete CI auth bootstrap/sync for that config. The generated customer workflow is artifact-driven: it watches and deploys only canonical `<tenant-folder>/<project-folder>/generated/**` paths, using `*/*/generated/**` when the deployments root is the repository root. Re-running the command automatically reconciles the CLI-managed workflow file to the latest template, always reconciles local SMTP settings into the matching GitHub Environment, and uses `--github-repo` only as an explicit override when repo auto-detection is wrong or unavailable.

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
When cached runtime auth exists locally but its Nebius auth public key was deleted, the same
auto-bootstrap path now recreates that stale profile automatically instead of requiring a
separate manual `auth --recreate` step.
After creating new runtime auth keys, cxcli also waits until Nebius token exchange accepts the
new public key before continuing to Terraform backend/apply work. During stale-profile checks
and this propagation wait, cxcli closes the short-lived Nebius SDK clients it opens and filters
the expected first-attempt deleted-key token-refresh traceback and retryable token-exchange
deadline tracebacks so the operator sees the cxcli warning/retry path instead of an unrelated
SDK stack trace while Terraform continues.

This keeps repeated runs safe by default while still allowing explicit rotation.

## Development

Python: `3.12 - 3.14`

Developer-only prerequisites for local `make venv`, `make lint`, and `make all`
(runtime/user install requirements are listed in [Prerequisites and Installation](#prerequisites-and-installation)):

- Required baseline tools:
  - Python `3.12+`
  - `make`
  - `git`
  - Python virtual-environment support
  - A native build toolchain for Python packages when prebuilt wheels are unavailable
- Optional command-path tools:
  - `kubectl` for `validate-generated`, `deploy`, `destroy`, `flux apply`, `flux destroy`, `flux bootstrap`, and Flux readiness checks
  - `helm` for `validate-sources` and other live Helm chart source/metadata validation paths
  - `aws` CLI for `terraform unlock`
  - `terraform` and `flux` are downloaded by `nebius-cxcli` into its local cache when missing for the command paths that support managed downloads

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
  - Default: none. Bundled component runtime rules still run during strict
    deployment-readiness validation; this knob only adds custom rule packs.
- `NEBIUS_CXCLI_PROVIDER_OPTION_PLUGINS`: optional provider-option lookup plugins.
- Provider option lookups prefer operator-facing SDK auth before Terraform
  runtime service-account env vars, so `create`/`component add` live discovery
  can still use your local SDK config or Nebius CLI token when runtime auth env
  vars are present in the shell.
- `NEBIUS_CXCLI_STRICT_PROVIDER_OPTION_CHECKS=1`: enable live option membership checks during deployment-readiness validation.

## Security Notes

- Keep deployment repositories private.
- Never commit credentials or secret values.
- The shipped catalogs avoid tenant/admin-specific key material. Project-scoped SSH public keys belong in the private deployment repo `config.yaml`; a private customer-local `component_sources.yaml` may carry `shared.admin_ssh.public_key` only as a seed that `create`/`component add` resolve and copy into that config.
- Operator-facing SSH public key inputs accept `ssh-rsa`, `ssh-ed25519`, and ECDSA keys, either inline or via a readable local `.pub` file path. Local paths are a convenience input only; `config.yaml` and generated manifests are normalized back to inline key text.
- `config.yaml` is the canonical render/reset contract and should be versioned in the private deployment repo.
- `generated/` is the deploy contract and should also be versioned, except for ignored runtime/transient files.
- Managed deployments `.gitignore` keeps generated Terraform runtime files and generated tfvars out of git, but does not ignore `config.yaml` or deployable generated manifests, and it intentionally does not add unrelated repo-development ignores such as `.coverage` or `*.tgz`.
- Keep `generated/infra/terraform.auto.tfvars.json` ignored even in a private repo: it is a generated, sensitive duplicate of values already present in `config.yaml` and the generated manifest.
- Generated-bundle CLI commands such as `nebius-cxcli validate-generated`, `nebius-cxcli terraform plan`, `nebius-cxcli terraform apply`, and `nebius-cxcli deploy` recreate `generated/infra/terraform.auto.tfvars.json` from `generated/nebius-cxcli-manifest.json` before Terraform runs, and generated workflows use those same commands instead of carrying separate inline restore logic. A raw Terraform CLI run from a fresh clone is intentionally not the customer handoff path unless the operator first restores that ignored tfvars file and provides the same backend/auth environment.
- GitHub sync requires a token with permission to write GitHub environment secrets.
- Key rotation is explicit with `auth --recreate`. Commands that run with `--auto-auth-bootstrap`
  also self-heal a stale cached runtime-auth profile when its Nebius auth public key has been
  deleted, but they do not rotate healthy cached profiles on every run.
