# nebius-cxcli

`nebius-cxcli` is the Nebius customer experience CLI and an end-to-end automation workflow generator. From one per-instance `config.yaml`, it renders a deployable customer artifact bundle: Terraform, Flux, inventory, and CI workflow artifacts.

After render, deployment should operate on the generated bundle. `config.yaml` remains the original render/reset contract, not the day-2 deployment surface.

The current implementation is provider-driven and source-configured for Nebius environments:

- Infra components come from Terraform module sources.
- App components come from Helm chart sources.
- Runtime options and dependency checks use live provider/chart metadata where available.
- Canonical instance model is dynamic: `infra.components[]` and `apps.charts[]`.

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

- `config.yaml` is the canonical render/reset contract per instance.
- `generated/` is the deploy contract for customer repositories.
- Source-driven component model from `component_sources.yaml`.
- `create` scaffolds or reconciles instance config idempotently.
- `create` writes dynamic component state (`infra.components[]`, `apps.charts[]`).
- `create` keeps only selected components/charts in `config.yaml` (unselected entries are omitted).
- `create` validates `component_sources.yaml` by default (`--no-validate-sources` to skip).
- `create` auto-manages a deployments-root `.gitignore` block when target path is inside a git repo (keeps `config.yaml` and deployable generated artifacts versioned while ignoring Terraform transient/runtime files and generated tfvars).
- App dependency resolution from Helm `Chart.yaml` metadata.
- Interactive wizard supports `q` to stop optional phases/field prompting.
- `create` validates `tenant_id`/`project_id` against Nebius IAM APIs before continuing.
- Infra field options are resolved dynamically from Nebius APIs where supported.
- Flux output is flat under `generated/flux` (no `apps/` or `sources/` subdirectories).
- `validate` runtime checks, plus `validate --strict` deployment-readiness checks.
- `render` writes deterministic Terraform, Flux, inventory, and `generated/nebius-cxcli-manifest.json`.
- `render` resets the generated bundle by recreating managed files from a clean layout, while preserving bootstrap-owned `generated/flux/flux-system` so rerendering does not tear down an existing Flux GitOps bootstrap.
- `render` warns before overwriting existing generated artifacts, so rerendering is an explicit reset back to the original `config.yaml` contract.
- `deploy`, `terraform plan/apply/unlock`, `flux apply/bootstrap`, `inventory write`, and `email` all operate on an existing generated bundle instead of reading `config.yaml`.
- `terraform apply`, `flux apply`, and `deploy` are designed for sequential idempotent reruns against the same generated bundle.
- `bootstrap-ci` generates CI workflow and can bootstrap/sync CI environment secrets.
- `discover` outputs deployment-instance discovery JSON with `config`, `generated`, `config_changed`, `generated_changed`, and `github_environment`.

## Runtime Metadata

Primary source file (repo root):

- `component_sources.yaml` for repo-local development
- `component_sources.release.yaml` for portable/release generation with Git module sources

Schema:

- `cli.flux.version`: Flux controller install version used by local `deploy` when controllers are missing and by managed `flux bootstrap` CLI download
- `cli.terraform.version`: Terraform CLI version used by the managed Terraform download path
- `infra.tf_modules[]`: `module`, `source`, `version`, `group`, `enable`, optional `defaults`, optional `outputs`, optional `input`, optional `handoff`
- `apps.helm_charts[]`: `name`, `repo`, `version`, `namespace`, `releasename`, `group`, `enable`, optional `defaults`, optional `outputs`, optional `input`

`apps.helm_charts[].repo` supports:

- HTTP/S Helm repositories (must serve `index.yaml`)
- OCI chart repositories (`oci://...`)
- GitHub tree URLs for charts stored in git (`https://github.com/<owner>/<repo>/tree/<ref>/<chart-path>`)

Source requirements enforced by `validate-sources`:

- Terraform modules (`infra.tf_modules[]`):
  - `module` must be lowercase letters/digits/hyphens.
  - Local `source` may use a relative path (`./...`, `../...`, `../../...`) or an absolute filesystem path.
  - Relative local paths are resolved from the active `component_sources.yaml` file location first.
  - Local `source` must resolve to an existing directory.
  - Directory must contain at least one `*.tf` file.
  - Every module source is install-checked with `terraform init -backend=false` during validation, so broken remote refs, missing auth, and nested module source failures fail fast.
  - Missing `main.tf` or `variables.tf` is reported as warning (wizard field discovery depends on variables).
  - Supported Terraform module source formats are only:
    - relative local path
    - absolute local path
    - Git repo source address such as `git::https://github.com/org/repo.git//modules/mk8s?ref=v1.2.3`
  - Local Terraform module sources are rendered as resolved local filesystem paths. If you need a pinned remote ref, declare an explicit `git::...?...ref=...` source instead of combining a local path with `version`.
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

Supported `--component-sources-file` examples:

- Relative file in the current directory: `nebius-cxcli --component-sources-file ./component_sources.yaml validate-sources`
- Portable/release catalog: `nebius-cxcli --component-sources-file ./component_sources.release.yaml validate-sources`
- Relative file elsewhere: `nebius-cxcli --component-sources-file ../../shared/component_sources.yaml validate-sources`
- Absolute file: `nebius-cxcli --component-sources-file /Users/alice/catalogs/component_sources.yaml validate-sources`
- Environment override: `NEBIUS_CXCLI_COMPONENT_SOURCES_FILE=/Users/alice/catalogs/component_sources.yaml nebius-cxcli validate-sources`

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
      source: git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=main
      outputs:
        tf_outputs: true
        static:
          access: external
      handoff:
        cluster_id: cluster_id
        access: access
    - module: shared-vpc
      source: git::https://github.com/example/platform-infra.git//modules/shared-vpc?ref=v1.2.3

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

Recommended catalog split:

- `component_sources.yaml`: repo-local developer catalog, using local filesystem module paths for fast iteration
- `component_sources.release.yaml`: portable/release catalog, using Git module sources so generated artifacts work on other machines and in CI
- The checked-in developer and portable catalogs should stay semantically identical apart from Terraform module `source` values; only the transport changes between local-dev and portable renders.

The portable/release catalog is a template. Build/package steps derive the bundled `nebius_cxcli/component_sources.yaml` fallback from `component_sources.release.yaml`, and the CI/release workflows rewrite its Terraform Git module refs from `?ref=main` to the current commit/tag before building the wheel or publishing the raw catalog asset.
If you use `component_sources.release.yaml` manually outside those workflows, pin its Git refs to a released tag or commit first instead of using `main`.

Recommended workflow:

- Automatic catalog resolution is a convenience default, not a portability guarantee.
- `validate` and `render` default to the `portable` render profile, which emits deployable Terraform module sources suitable for CI and other machines.
- Installed-package fallback is portable by default: when no repo-local/user/global override is present, the packaged `nebius_cxcli/component_sources.yaml` uses Git Terraform module sources.
- Use `--render-profile local-dev` when you intentionally want generated Terraform to point at checked-out local module paths for workstation testing.
- Use `--component-sources-file` or `NEBIUS_CXCLI_COMPONENT_SOURCES_FILE` only when you need to override which catalog is active; it is not the primary portable-vs-local switch.
- Customer-side commands that operate on `generated/` do not need the source catalog to resolve Terraform module paths from the original render environment.

Typical usage:

```bash
# Local development against checked-out Terraform modules
nebius-cxcli validate --strict --render-profile local-dev /path/to/config.yaml
nebius-cxcli render --render-profile local-dev /path/to/config.yaml

# Portable generation for CI / another repository / another machine
nebius-cxcli render /path/to/config.yaml
```

Managed vs external local tools:

- Auto-managed by `nebius-cxcli` when missing:
  - `terraform` for Terraform-backed validation, render lockfile generation, `terraform plan`, `terraform apply`, `terraform unlock`, and backend-backed Terraform output lookups
  - `flux` for `flux bootstrap`
- Still external prerequisites:
  - `kubectl` for `deploy`, `flux apply`, `flux bootstrap`, and Flux readiness checks
  - `nebius` CLI for cluster handoff kubeconfig setup when enabled charts depend on a handoff-enabled infra component
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
`handoff.access` must reference a declared config/static `outputs` alias that resolves to `external` or `internal`.  
Generic exported values do not replace `handoff`; local deploy/bootstrap still needs an explicit runtime contract for kubeconfig setup.  
When enabled charts are deployed, the CLI uses that contract to read the rendered Terraform root output and prepare kubeconfig before Flux/kubectl work starts.

For `apps.helm_charts[]`, `namespace` and `releasename` are defaults:

- interactive create wizard prompts them for enabled apps
- non-interactive create can override with:
  - `--app-namespace <app-id>=<namespace>`
  - `--app-releasename <app-id>=<release-name>`

Runtime config shape:

- `client_info`: `client_name`, `nebius.{tenant_id,project_id,region_id}`, `notifications.{inventory_markdown,email}`
- `client_info` does not include legacy `env` or `cluster_name` fields.
- `infra.components[]`: `id`, `enabled`, `inputs`
- `apps.charts[]`: `id`, `group`, `enabled`, `repo`, `version`, `namespace`, `release-name`, `values`
- Static nested component configs (`infra.<component>.enabled`, `apps.<group>.<chart>.enabled`) are not supported.
- Canonical instance path: `<deployments-root>/instances/<client-name>--<tenant-id>/<project-id>/config.yaml`

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
- `generated/inventory/infra.json`
- `generated/inventory/apps.json`
- `generated/inventory/mk8s.json` only when MK8s is enabled
- `generated/inventory/postgresql.json` only when Managed PostgreSQL is enabled
- `generated/inventory/sfs.json` only when SFS is enabled
- `render`, `deploy`, `terraform apply`, `flux apply`, `flux bootstrap`, and `inventory write` refresh inventory artifacts for the active instance instead of leaving the starter placeholder behind.
- Those refreshes also delete stale disabled component inventory files so the directory reflects the current enabled config.

Terraform render output (canonical):

- `generated/infra/backend.tf`
- `generated/infra/versions.tf`
- `generated/infra/providers.tf`
- `generated/infra/variables.tf`
- `generated/infra/main.tf`
- `generated/infra/outputs.tf`
- `generated/infra/terraform.auto.tfvars.json`
- `generated/infra/.terraform.lock.hcl` (generated during `render` when Terraform is available from `PATH` or the managed download path and backend init succeeds)
- Local Terraform module sources are rendered as resolved filesystem paths. Use an explicit `git::...//subdir?ref=...` source in `component_sources.yaml` when you want a portable or pinned remote module reference.
- Terraform remote state is managed separately from app/object-storage components: backend bucket settings are derived from `client_info` (`client_name` + `project_id` + `region_id`), not from `infra.components[id=object-storage].inputs`.
- Backend locking uses Terraform S3 lockfile mode (`use_lockfile = true`) and Nebius Object Storage endpoint (`https://storage.<region>.nebius.cloud`).
- If a deploy/apply is canceled while Terraform is waiting on or holding the backend lock, the remote `.tflock` object can remain behind. In that case the next apply fails before creating any resources; the CLI now reports that explicitly and includes the lock owner/creation time from Terraform's lock metadata.
- `nebius-cxcli terraform unlock <generated-dir>` is the recovery path for that case. It inspects the remote `.tflock`, refuses by default when local Terraform/deploy processes are still active or the lock owner is from another machine/user, and then uses Terraform's own `force-unlock` only when the lock looks stale. Do not run it as routine cleanup.
- `terraform unlock` requires `aws` CLI in `PATH`. Terraform itself can come from `PATH` or from the managed Terraform download path.
- Inventory artifacts are local-only outputs under `generated/inventory`; they are not uploaded to Object Storage by the CLI.

Wizard field behavior:

- Infra input field names are discovered dynamically from Terraform module variables (required and optional).
- Wizard prompts required Terraform variables first (plus existing optional values and dependency-enabled option sets).
- Prompt labels include Terraform input type hints (for example `string`, `number`, `bool`) and `required` markers.
- Source-backed infra `inputs.parent_id`/`inputs.project_id` default to `client_info.nebius.project_id` when those variables exist.
- `component_sources.yaml` can declare top-level `shared` values and shared-derived `defaults` so components read shared values from the active source catalog instead of duplicating them under component `inputs` or chart `values`.
- The bundled `mk8s` catalog entry defaults `inputs.mk8s_cluster_public_endpoint: true` because its handoff contract is `access: external`; that keeps local `deploy`/Flux handoff aligned with Nebius CLI `get-credentials --external`.
- The bundled `mk8s` catalog entry also defaults `inputs.kube_network_service_cidrs: ["/20"]`. Nebius treats an omitted MK8s service CIDR as `["/16"]`; on a single-pool `/16` subnet that can consume the whole pool and leave no address space for control-plane allocations, which looks like a long `PROVISIONING` stall.
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

Shared-derived default example:

```yaml
# component_sources.yaml
shared:
  admin_ssh:
    user_name: ubuntu

infra:
  tf_modules:
    - module: wireguard-jumphost
      source: ../../platform-infra/modules/wireguard-jumphost
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
`ssh_public_key` is intentionally per-instance input and should be stored only in the private instance `config.yaml`, not in the shipped source catalog.  
Do not duplicate shared-derived default targets under `infra.components[].inputs` or `apps.charts[].values`; strict validation rejects explicit values for catalog-managed targets.

Catalog defaults example:

```yaml
# component_sources.yaml
infra:
  tf_modules:
    - module: mk8s
      source: ../../platform-infra/modules/mk8s
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
2. Edit the generated instance `config.yaml` with real values.
3. `nebius-cxcli validate --strict <config.yaml>`
4. `nebius-cxcli render <config.yaml>`
5. Commit the instance `config.yaml` and the deployable `generated/` bundle to the customer private repo.
6. Deploy from the generated bundle:
   - `nebius-cxcli deploy <generated-dir>`
   - `nebius-cxcli terraform apply <generated-dir>`
   - `nebius-cxcli flux apply <generated-dir>`
   - CI workflow deploys from `generated/`, not from `config.yaml`
7. Optional CI setup:
   - `nebius-cxcli bootstrap-ci <config.yaml>`
   - The generated customer workflow watches `generated/**` only. Editing `config.yaml` in the customer repo does not trigger CI deploys; rerendering from `config.yaml` is a manual reset action.

`create` is idempotent by default. Re-running the same instance identity reconciles selections and preserves existing values. Use `create --force` only when you want reset/overwrite behavior.

In the customer private repo, keep both:

- `config.yaml` as the original render/reset contract
- `generated/` as the deploy contract used by day-2 operations and CI

Rerendering from `config.yaml` is still supported, but it is a manual reset action. It will overwrite the generated artifact bundle back to the rendered baseline. Bootstrap-owned `generated/flux/flux-system` is preserved so rerender does not prune an existing Flux bootstrap. In an interactive terminal, `render` prompts for confirmation before reset. In non-interactive contexts, rerender requires `--force`.

Sensitive per-instance values such as jump-host SSH public keys belong in the private instance `config.yaml`, not in the shipped public `component_sources.yaml`.

Local `deploy`/`flux bootstrap` behavior when apps + a handoff-enabled cluster component are enabled:

- `deploy` now runs `terraform validate` after render and before apply.
- When Terraform is not already in `PATH`, `deploy`, `terraform plan`, `terraform apply`, `terraform unlock`, and backend-backed Terraform output lookups use a managed Terraform CLI download pinned by `component_sources.yaml` `cli.terraform.version`. The binary is cached under the local nebius-cxcli cache and is not installed system-wide.
- During long-running `terraform apply`, `deploy` and `terraform apply` print one merged status surface: Terraform apply transitions plus a light Nebius MK8s API snapshot. When an enabled `mk8s` component is present and Nebius SDK auth is available, the CLI polls Nebius MK8s API for cluster/node-group status; otherwise it falls back to a simple elapsed heartbeat for the API side.
- The merged status surface is rendered as a multi-line block with distinct TF and API sections so Terraform progress and Nebius resource state are visually separate in the terminal.
- If Terraform apply fails, the CLI exits with the Terraform error as the canonical failure and appends the last known merged Terraform/API status snapshot.
- Remote state lock failures are called out separately: the CLI explains that Terraform never acquired the backend lock, so the run created nothing, and points at the stale `.tflock` object metadata when Terraform provides it.
- When Nebius MK8s node-group status reports `ERROR` events, the merged status block includes those alerts. Known transient bootstrap warnings such as waiting for ProviderID registration or temporary `Ready=False` node conditions are shown as notes instead of alerts while the node group is still provisioning.
- After apply, `deploy` reads the rendered Terraform output declared by `handoff.cluster_id` and configures a temporary kubeconfig before applying Flux manifests.
- On non-CI local runs, that same cluster handoff also updates the user kubeconfig at `~/.kube/config`, so `kubectl` can be used against the target cluster after `deploy`, `flux apply`, or `flux bootstrap` without another manual `nebius mk8s cluster get-credentials` step.
- Before `deploy`, `flux apply`, or `flux bootstrap` starts Flux work against a handed-off MK8s cluster, the CLI waits for Kubernetes nodes to become `Ready` so Terraform completion is not mistaken for workload readiness.
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
- `deploy` requires `kubectl`. It also requires `nebius` CLI when the generated bundle includes enabled charts and a `handoff`-enabled infra component.
- `flux bootstrap` still needs network access to GitHub releases when the managed Flux CLI download path is used.
- The managed Terraform download path needs network access to HashiCorp releases when Terraform is not already in `PATH`.
- If your Nebius CLI is already logged in, `deploy` reuses that auth. Otherwise rerun with runtime auth material available, for example `--auto-auth-bootstrap`.

## Releases

Use the release flow in three steps:

1. Prepare the changelog on your working branch with `./publish-release.sh --prep X.Y.Z`.
2. Merge that branch to `main`.
3. From a clean, synced `main`, create and push the release tag with `./publish-release.sh --publish X.Y.Z`.

The publish step creates the annotated tag `nebius-cxcli-vX.Y.Z`. That tag triggers the repository workflow at `.github/workflows/nebius-cxcli-release.yml`, which rebuilds the wheel, verifies that the wheel version matches the tag, verifies that the bundled fallback `component_sources.yaml` is present inside the wheel, and publishes the GitHub Release from the tagged commit.

Release assets for `nebius-cxcli` now include:

- the wheel artifact
- the raw release catalog as a direct editable download, with its Terraform Git module refs pinned to the published release tag

## Commands

Global options:

- `--version`
- `--component-sources-file <path>`

### Generator-side Commands

```bash
nebius-cxcli validate-sources
nebius-cxcli validate /path/to/config.yaml
nebius-cxcli validate --strict /path/to/config.yaml
nebius-cxcli render /path/to/config.yaml
```

- `validate-sources`
  - Validates the active `component_sources.yaml` catalog: Terraform module sources, Helm chart sources, and catalog contract shape.
- `validate <config.yaml>`
  - Validates the instance contract and runtime shape without the stricter deployment-readiness checks.
  - Defaults to `--render-profile portable`, so validation fails when the requested render contract would rely on non-portable local Terraform module paths.
- `validate --strict <config.yaml>`
  - Adds stricter deployment-readiness checks on top of `validate`, including source-backed and runtime-backed checks used before rendering.
  - Accepts `--render-profile {portable|local-dev}` like `validate`.
- `render <config.yaml>`
  - Generates the deployable bundle under `generated/`, refreshes inventory, writes `generated/nebius-cxcli-manifest.json`, and treats rerender as a reset operation.
  - Rerender recreates the managed generated bundle from a clean canonical layout without stale files from earlier renders, while preserving bootstrap-owned `generated/flux/flux-system`.
  - Defaults to `--render-profile portable`, which rewrites active local module sources to their portable Git/registry equivalents when available.
  - Use `--render-profile local-dev` only for workstation testing against checked-out local Terraform modules; those generated artifacts are intentionally non-portable.
  - Use `--component-sources-file` or `NEBIUS_CXCLI_COMPONENT_SOURCES_FILE` only when you need to select a non-default catalog file.
  - If `generated/` already contains files, `render` prompts before overwrite in an interactive terminal.
  - In non-interactive contexts, use `nebius-cxcli render --force <config.yaml>` to confirm the reset explicitly.

### Customer-side Commands

```bash
nebius-cxcli validate-generated /path/to/generated
nebius-cxcli deploy /path/to/generated
nebius-cxcli terraform apply /path/to/generated
nebius-cxcli flux apply /path/to/generated
nebius-cxcli flux bootstrap /path/to/generated
```

- `validate-generated <generated-dir>`
  - Validates an existing generated bundle without rerendering it. Runs `terraform validate` against `generated/infra` and `kubectl kustomize` against `generated/flux` when apps exist.
  - Add `--portable` in CI or pre-commit checks to reject generated Terraform bundles that still embed local filesystem module paths.
  - Uses the generated bundle as the deploy contract; it does not need the original render machine's local module paths.
- `deploy <generated-dir>`
  - Full local deploy from the generated bundle: Terraform apply first, then inventory refresh for both infra and apps artifacts, then Flux apply. If GitOps bootstrap is not configured yet, the CLI warns and prints the follow-up `flux bootstrap` command.
- `terraform apply <generated-dir>`
  - Infra-only apply from the generated Terraform bundle. Safe to rerun sequentially for convergence, and does not depend on resolving the original source catalog's module paths.
- `flux apply <generated-dir>`
  - Apps-only direct apply from the generated Flux bundle. Safe to rerun sequentially for day-2 reconciliation. If GitOps bootstrap is not configured yet, the CLI warns and prints the follow-up `flux bootstrap` command.
- `flux bootstrap <generated-dir>`
  - GitOps bootstrap/reconcile path from the generated Flux bundle. Use this when the cluster should watch the Git repo/path with Flux.

### Supporting Commands

```bash
nebius-cxcli create /path/to/deployments-root
nebius-cxcli bootstrap-ci /path/to/config.yaml
nebius-cxcli discover /path/to/deployments-root
nebius-cxcli terraform plan /path/to/generated
nebius-cxcli terraform unlock /path/to/generated
nebius-cxcli inventory write /path/to/generated
nebius-cxcli email /path/to/generated
nebius-cxcli auth --instance-config /path/to/config.yaml --validate-profile
```

- `create <target_path>`
  - Scaffolds or reconciles the instance `config.yaml` and generated-folder skeleton.
- `bootstrap-ci <config.yaml>`
  - Generates the customer GitHub Actions workflow and can optionally bootstrap/sync CI auth secrets. The generated workflow watches and deploys only `generated/**`.
  - Generated workflows validate changed bundles with `nebius-cxcli validate-generated --portable` before Terraform plan/apply.
  - Generated workflows also keep the Python version in one env var and emit compact single-line discovery JSON into `GITHUB_OUTPUT` so matrix handoff stays deterministic.
  - The target `config.yaml` must already live inside the customer git repository because the workflow is written at that repo root under `.github/workflows/`.
  - With default `--auth-bootstrap`, the command auto-detects the target GitHub repo from that checkout's `origin` remote. Use `--github-repo <owner/repo>` only as an explicit override when the remote is missing, non-GitHub, or not the repo you want to manage.
  - When `--cli-ref` is omitted, generated workflows default to `main` for development builds and to `nebius-cxcli-v<version>` for stable tagged releases.
  - Use `--cli-ref <branch|tag|sha>` when the workflow should install a specific nebius-cxcli ref for PR or branch validation instead of the default release tag or `main`.
  - Generated workflows also honor an optional GitHub repo/org variable `NEBIUS_CXCLI_REF`; when set, it overrides the generated default ref without editing the workflow file.
  - `bootstrap-ci` creates the GitHub Environment and syncs Environment Secrets. It does not create GitHub repo/org variables; `NEBIUS_CXCLI_REF` remains an optional manual override.
- `discover <target_path>`
  - Returns changed deployment instances for CI matrix generation.
- `terraform plan <generated-dir>`
  - Infra-only plan from the generated Terraform bundle.
- `terraform unlock <generated-dir>`
  - Inspects and clears a stale remote Terraform state lock for the generated infra bundle.
- `inventory write <generated-dir>`
  - Refreshes local non-sensitive inventory files from the generated bundle.
- `email <generated-dir>`
  - Sends inventory markdown to `client_info.notifications.email` via SMTP env vars.
- `auth`
  - Manages runtime auth profiles and optional GitHub environment secret sync.

Common command flags:

- `create`:
  `--client-name`, `--tenant-id`, `--project-id`, `--region-id`, `--email`, `--infra`, `--app`, `--app-namespace`, `--app-releasename`, `--validate-sources/--no-validate-sources`, `--no-interactive`, `--force`
- `bootstrap-ci`:
  `--force`, `--auth-bootstrap/--no-auth-bootstrap`, `--github-repo`, `--github-token-env`, `--cli-ref`
- `validate`: `--strict`, `--render-profile`
- `validate-generated`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, `--portable`
- `render`: `--force`, `--render-profile`
- `deploy`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`
- `discover`: `--all`
- `terraform plan`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`
- `terraform apply`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`
- `terraform unlock`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, `--force`
- `flux apply`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`
- `flux bootstrap`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`
- `auth`:
  `--project-id`, `--instance-config`, `--client-name`, `--profile`, `--endpoint`, `--sdk-config-file`, `--github-repo`, `--github-token-env`, `--validate-profile`, `--create`, `--recreate`, `--bootstrap-ci`

## Auth Workflow

Terraform runtime auth behavior:

- Generated `providers.tf` uses direct provider fields (`service_account.account_id/public_key_id/private_key_file`) and sets `module_name`.
- Generated `backend.tf` stores only non-secret backend location/settings; credentials are supplied by environment/runtime profile.
- Runtime values are passed through Terraform variables (`TF_VAR_*`) instead of provider `_env` indirection.
- Local runtime auth can be auto-bootstrapped with a dedicated service account name: `nebius-cxcli-tf-sa`.
- Auto-bootstrapped runtime auth material is cached under `~/.config/nebius-cxcli/<client_name>-<project-id>/` to avoid creating new key material every run.
- The auth key flow is authorized-key based: the CLI generates keypair material, uploads the public key for the service account, and stores private key material locally for Terraform runtime use.
- Terraform backend init uses AWS-compatible Object Storage keys (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`); the runtime auth profile cache auto-populates these for local runs.
- `render` performs create-if-missing runtime auth bootstrap automatically before backend-ready lockfile init.

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
- `--bootstrap-ci`:
  - Syncs local runtime auth profile secrets to GitHub environment secrets.
  - GitHub environment name is `<client_name>-<project_id>`.
  - Requires existing local runtime auth profile (create first if missing).

`bootstrap-ci <config.yaml>` remains the full CI workflow bootstrap command and can still perform complete CI auth bootstrap/sync for that config. The generated customer workflow is artifact-driven: it watches and deploys only `generated/**`. The command requires the target config to be inside the customer git repository, auto-detects the GitHub repo from that checkout when `--auth-bootstrap` is enabled, and uses `--github-repo` only as an explicit override.

Generated workflow CLI ref:

- The generated customer workflow sets `NEBIUS_CXCLI_REF` and installs `nebius-cxcli` from that git ref.
- `bootstrap-ci` does not create the optional `NEBIUS_CXCLI_REF` GitHub repo/org variable; create that override manually only when you want to supersede the generated default ref.
- `NEBIUS_CXCLI_REF: main` means the workflow installs the latest code from the repository main branch on each run. That is appropriate during active development before a release is cut.
- To pin the generated workflow up front, run `bootstrap-ci --cli-ref <branch|tag|sha>`.
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

# Local-dev render against checked-out Terraform modules
nebius-cxcli validate --strict --render-profile local-dev /path/to/config.yaml
nebius-cxcli render --render-profile local-dev /path/to/config.yaml

# Customer side: validate and deploy generated bundle
# Validate generated bundle before deploy
nebius-cxcli validate-generated --portable /path/to/generated

# Local deploy from generated artifacts
nebius-cxcli deploy /path/to/generated

# Infra only
nebius-cxcli terraform plan /path/to/generated
nebius-cxcli terraform apply /path/to/generated

# Safe to rerun sequentially after artifact changes or partial failures
nebius-cxcli terraform apply /path/to/generated

# If `terraform` is not in PATH, nebius-cxcli downloads the catalog-pinned
# Terraform CLI into its local cache automatically before these commands run.
nebius-cxcli terraform apply /path/to/generated

# Apps only: direct local apply to the target cluster
nebius-cxcli flux apply /path/to/generated

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
nebius-cxcli auth --instance-config /path/to/config.yaml --validate-profile

# Sync local auth profile to GitHub environment secrets
nebius-cxcli auth --instance-config /path/to/config.yaml --bootstrap-ci --github-repo owner/repo
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
python -m nebius_cxcli auth --help
```

Test suite focus:

- `tests/test_cli.py` and `tests/test_cli_command_coverage.py` cover the command contract, including `bootstrap-ci`, render-profile behavior, and generated-bundle validation paths.
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
- The shipped catalogs avoid tenant/admin-specific key material. Per-instance SSH public keys belong in the private deployment repo `config.yaml`.
- `config.yaml` is the canonical render/reset contract and should be versioned in the private deployment repo.
- `generated/` is the deploy contract and should also be versioned, except for ignored runtime/transient files.
- Managed deployments `.gitignore` keeps generated Terraform runtime files and generated tfvars out of git, but does not ignore `config.yaml` or deployable generated manifests.
- Keep `generated/infra/terraform.auto.tfvars.json` ignored even in a private repo: it is a generated, sensitive duplicate of values already present in `config.yaml`.
- GitHub sync requires a token with permission to write GitHub environment secrets.
- Key rotation is explicit with `auth --recreate` and automatic in deploy only when runtime auth bootstrap is needed.
