# Configuration reference

[Back to the command guide](../README.md#projects-and-configuration)

Create a project through the wizard, then edit its `config.yaml` for day-2
changes. Terraform module variables and Helm chart values remain the component
APIs; this guide explains the surrounding catalog and configuration contracts.

## File ownership

| File | Owns |
| --- | --- |
| `config.yaml` | Project identity, component instances, explicit inputs/values, and target settings |
| `component_sources.yaml` | Terraform/Helm sources, release metadata, defaults, bindings, and wizard metadata |
| `component_cli_settings.yaml` | cxcli tool versions, compute policy, GPU automation, and observability settings |
| `src/nebius_cxcli/soperator_wizard.yaml` | Packaged Soperator profiles and install wizard policy |
| `generated/nebius-cxcli-manifest.json` | Rendered runtime snapshot used by bundle commands |

Source and settings catalogs are siblings and share the same
`components.<infra|apps>.<component-id>` identities. Unknown keys and settings for
undeclared components fail at load time. `cli` and `observability` settings
belong in the settings catalog, not the source catalog.

Soperator is outside the generic catalogs. The official upstream release owns
its charts, images, and dependencies; the packaged wizard owns install policy.
Use the [Soperator commands](soperator.md) to configure it.

## Project configuration

Source configurations require `version: v2`. The top-level `deploy` section is
rejected, including an empty or null section. Settings sit directly beside
`inputs` or `values`, without an extra settings wrapper.

| Owner | Policy blocks |
| --- | --- |
| `infra.components[id=mk8s]` | `observability`, `secrets.mysterybox`, `deployment_testing.mk8s_gpu` |
| `infra.components[id=vm]` | Per-VM `observability.enabled` and `observability.vm.logs` |
| `infra.external_clusters[]` | Cluster policy plus `instance_id`, `cluster_id` and/or `kube_context`, `access`, `project_id`, `region_id`, `inventory`, `desired_platform` |
| `apps.charts[id=soperator]` | `deployment_testing.smoke`, `rollout`, `profiling`, `registration` |

The old `soperator_*` prefixes are removed from relocated policy names.
External ownership is implicit in the registry; do not add `kind` or `ownership`.
Managed and external cluster identities must be unique. Optional managed-cluster
`project_id` and `region_id` must match the effective Terraform identity; they
cannot redirect deployment to another project or region. Ordinary chart values
remain under `values`; Terraform variables remain under `inputs`.
For MK8s, the effective project is `inputs.cluster.parent_id`, falling back to
`client_info.nebius.project_id`. Infrastructure readiness verifies that same identity.

Component instance names cannot replace the component list or external-cluster
registry. An omitted or null `observability` block uses the same defaults;
scoped observability commands leave unselected observability blocks unchanged.

To adopt this breaking contract, explicitly move each old target policy to its
owner and copy any former shared VM policy to each applicable VM. Finish active
old attempts with the matching old CLI first. Then run a full `render` and full
`deploy` using v2 source to establish a new accepted baseline before upgrades or
ordinary Apps operations. Existing frozen records are never converted or reset.
The generated manifest now uses `nebius-cxcli-generated/v3`; derived targets,
required outputs, watchers and validations live under `execution` beside the
frozen backend. The outer deployment-record schema is unchanged.

| Field | Meaning |
| --- | --- |
| `client_info.client_name` | Client label |
| `client_info.nebius.project_id` | Authoritative Nebius project identity |
| `client_info.nebius.region_id` | Region |
| `client_info.nebius.tenant_id` | Tenant context for quota/capacity and discovery |
| `client_info.notifications.email_enabled` | Per-client report-email gate |
| `client_info.notifications.email` | Recipient; blank creation input disables email |
| `infra.components[]` | Rows with `id`, `instance_id`, `enabled`, and `inputs` |
| `apps.charts[]` | Rows with `id`, `instance_id`, `group`, `enabled`, `repo`, `version`, `namespace`, `release-name`, and `values` |
| `infra.external_clusters[]` | Existing cluster identity, connection, inventory, and cluster policy; never Terraform resources |

Infra `id` selects a component type and `instance_id` identifies an instance.
Repeated component types need distinct instance IDs. App instance IDs identify
the cluster target: `grafana@cluster1` installs Grafana on logical target
`cluster1`. Authored config does not use `apps.charts[].target_ref`; generated
metadata derives it from the same instance ID.

Apps need an enabled built-in MK8s target or a registered onboarded Nebius MK8s
target. Do not invent target records to bypass onboarding. Source catalogs use
`release.name`; project config uses `release-name`. Nested component-specific
root blocks and alternate spelling aliases are not accepted.

Infra module source selection belongs to the active catalog; starter configs
omit infra `source` and `version` fields. Treat module outputs consumed by
bindings and cluster handoff as stable interfaces.

## Catalog example

These files form one matching structural example. Replace the illustrative
source locations and versions with reviewed components before source validation.
For a complete starting point, use the [bundled sources](../component_sources.yaml)
and [bundled settings](../component_cli_settings.yaml).

`component_sources.yaml`:

```yaml
shared:
  admin_ssh:
    user_name: ubuntu
components:
  infra:
    mk8s:
      source:
        portable: git::https://github.com/org/repo.git//modules/mk8s?ref=v1.2.3
  apps:
    grafana:
      source:
        portable:
          repo: oci://example.invalid/charts
          chart: grafana
          version: 1.0.0
      release:
        namespace: grafana
        name: grafana
```

`component_cli_settings.yaml`:

```yaml
cli:
  terraform:
    version: 1.15.5
  flux:
    version: v2.8.0
    release_timeout: 5m
components:
  apps:
    grafana:
      cli:
        orgId: 1
```

## Source profiles and chart versions

Use portable sources for CI or another checkout, and local sources for
development against checked-out Terraform modules/charts:

```bash
nebius-cxcli --source-profile local render ./config.yaml
nebius-cxcli --source-profile portable render ./config.yaml
```

Terraform portable sources can use pinned Git module addresses. Local sources
render as resolved filesystem paths and are not portable across machines.
For local modules that call sibling modules, include the containing package:
`../../platform-infra//modules/wireguard-gw`. The `//` separates the package root
from the module subdirectory and is preserved in generated sources. Terraform
copies absolute local sources as packages; selecting only the wrapper directory
would exclude its sibling dependencies. Bundled WireGuard, SSH jump-host, NFS
and NAT gateway sources use this form. See the
[Terraform module source reference](https://developer.hashicorp.com/terraform/language/block/module#source).

Helm source discovery supports HTTP/HTTPS repositories, OCI repositories,
GitHub tree URLs, and local chart paths. GitHub tree charts are supported for
source inspection, but execution admission rejects them: deployment requires
an admitted HTTP/OCI source or local chart. `source.portable.chart` defaults to the component
ID; when supplied, its basename is used for validation and rendering.

An enabled repository-backed chart needs an **exact version**. A version field
can be omitted where local chart metadata supplies it; omission does not select
an arbitrary latest repository version. Interactive create/add prompts for each
selected app version. Noninteractive overrides use `--app-version APP_ID=VERSION`,
`--app-namespace APP_ID=NAMESPACE`, and `--app-releasename APP_ID=NAME`.
Source validation checks requested non-catalog versions before saving.

Ordinary OCI charts are bound to admitted digests. HTTP charts use exact versions
and execution compares downloaded content with the rendered snapshot. Later
Flux HTTP fetches still trust the upstream repository. See
[compatibility admission](compatibility-matrix.md) for supported interfaces.

## Source-catalog fields

| Field | Contract |
| --- | --- |
| `shared.admin_ssh.user_name` | Non-sensitive shared username seed |
| `shared.admin_ssh.public_key` | Inline SSH public key or readable `.pub` path; private catalogs only |
| `components.infra.ID.source` | Portable Terraform source and optional local source |
| `components.apps.ID.source` | Portable Helm metadata and optional local chart source |
| `ui.title`, `ui.group`, `ui.enabled` | Wizard presentation and default selection |
| `ui.selectable` | Whether an app participates in normal selection |
| `defaults` | Seeded paths: `inputs.*` for infra, `values.*` for apps |
| `wizard_profile` | Built-in wizard policy for the matching component |
| `wizard` | Field-specific prompt metadata |
| `input` | Consumer bindings from component outputs |
| `status` | Infra deployment-status watcher metadata |
| `release.namespace`, `release.name` | App release defaults |
| `release.timeout` | Flux timeout; otherwise inherits `cli.flux.release_timeout` |
| `release.install_after` | App prerequisites, auto-selection, and Flux dependency edges |
| `usage.lifecycle: transient` | Chart for a command-owned runtime flow |
| `usage.config.ref` | Optional config activation field for a transient flow |

Transient charts must be disabled and unselectable in the normal wizard.
Component IDs use lowercase letters, digits, and hyphens. Status metadata uses
`kind`, optional `parent_input`/`name_input`, and ordered `name_inputs`.
Private-only VM readiness does not require a public address.

### Bindings and wizard behavior

Bindings use `<component-id>.<output-alias>` or
`<component-id>@<instance-id>.<output-alias>`. Consumers declare bindings;
Terraform outputs use normalized names such as `cluster_id`. Select explicit
instances for repeated types. App targets and target-scoped settings must name
the same logical cluster; do not edit generated metadata to retarget an app.

Catalog defaults seed prompts; **they do not suppress editable fields**.
Ordinary defaults preserve explicit saved values; policy-owned GPU paths follow
the active catalog rules and may be replaced or removed by those rules.
A separate wizard choice can skip
detailed app configuration; chart version is still prompted.

- `options` chooses provider-backed values such as subnets, images, and presets.
- `sources` with `source: static` supplies fixed strings or `{value, label}` rows.
- `prompt: false` hides an optional field while retaining manual configuration.
- `write_default_to_config: true` persists an accepted prompt default.
- Simple string-list inputs accept comma-separated entries. Unguided maps,
  objects, and object lists accept single-line YAML/JSON.

Public-key seeds become inline key text in private project config. The shipped
public catalog must not contain customer keys. Render can resolve public-key
paths into generated artifacts without rewriting authored config.

## Infrastructure policy

`infra:vpc` owns Terraform-managed networks, private pools, and subnets. Existing
network selection uses live SDK discovery and filters subnets to that network.
The guided existing-network path can extend parent private-pool CIDRs. New
Terraform-managed networks use Nebius's default public pool and route table
unless explicit public pool IDs or subnet route tables are configured.

VM, NFS, jump-host, and WireGuard profiles resolve network, platform, preset,
and image choices from live project/region context. Managed PostgreSQL uses
live network selection and guided tier choices. Public allocation names must
use lowercase letters, digits, and hyphens.

`compute.boot_disk_defaults` in the settings catalog supplies disk types,
allocation units, and ordered CPU/GPU resource rules shared by MK8s and VM-style
components. Omitted size follows the selected disk allocation unit; explicit
size wins. An otherwise resolved shape with no matching rule fails rather than
using a hidden fallback. VM-style profiles expose supported secondary-disk,
deletion-protection, and encryption fields.

The SFS profile exposes size, filesystem type, block size, mount tag, and
`forbid_deletion`. Protection does not stop a storage-preserving upgrade, but it
blocks requested SFS deletion.

### MK8s and GPU policy

The MK8s wizard collects cluster name, network/subnet, Kubernetes version, node
groups, disks, SSH keys, service accounts, and storage attachments. Cluster name
becomes the target `instance_id` before app defaults are previewed. Terraform
labels may normalize hyphens to underscores.

GPU choices distinguish Nebius-image drivers from GPU Operator driver
management. Fabric/reservation choices use live capacity information; stale
capacity is not proof of available GPUs. Worker host totals differ from GPUs per
host and autoscaling bounds.

`components.infra.mk8s.cli.gpu` owns image preferences and deployment tests.
App `cli.mk8s_gpu_policy` provides roles, matching rules, automatic selection,
conditional defaults, and post-render patches. Rules can reference shared
`default_sets` and `post_render_patch_sets`; top-level defaults are unconditional.
`install_after` contributes Flux dependency ordering.

Deploy-time GPU checks cover bounded readiness and visibility. NCCL benchmark
options are command-only; see [acceptance testing](mk8s.md#acceptance-testing).

## Secrets and SecretStash

The product is [SecretStash](https://docs.nebius.com/mysterybox/); Nebius CLI,
API, Terraform, and configuration identifiers remain `mysterybox`.

The component declares `inputs.secrets` as a list of objects with stable `name`,
a `payload` map of `text`/`file` entries, optional `version_id`, and optional ESO
metadata such as `kubernetes_secret_name` and `eso_version_policy`. Without
effective synchronization, the wizard collects only names and key definitions;
it omits new ESO metadata and leaves existing entries intact. At least one payload
key is required. Runtime payload values are never collected by this wizard.

Selecting an enabled ESO application on a cluster enables application-secret
synchronization by default, including when adding the first MysteryBox secret.
Explicit `secrets.mysterybox.enabled: false` on that cluster overrides this default.
When at least one eligible target has synchronization enabled, the wizard also
asks for the Kubernetes Secret name and ESO version policy. MysteryBox selection
does not select ESO, and metadata-only WireGuard secrets stay outside this flow.

At the beginning of ordinary MysteryBox configuration, the wizard asks
`Sync SecretBox secrets to Kubernetes secrets using ESO?`, defaulting to the
target's effective setting (No when inactive). A single target is selected
automatically; with multiple targets, select which ones to configure first.
Choices update the existing target's `secrets.mysterybox.enabled`; unselected
targets remain unchanged. This policy covers all eligible ordinary MysteryBox
secrets, not just the component being edited.

Yes offers explicit ESO activation when necessary, preserving existing chart
values, and collects `sync_namespaces` with the current list or `[default]`.
Newly enabled ESO appears in the later Apps section and component-add summary.
No writes `enabled: false` without removing ESO or existing sync metadata.
The wizard stages each target until its setup is complete; back/quit discards
that target's unfinished changes while retaining previously completed work.
An enabled, unselected target still causes Kubernetes naming and ESO policy
prompts; the wizard lists the effective sync targets before secret entry.
Without a configured Kubernetes target, Yes explains the prerequisite and retries.

Use `version_id: n/a` before initial deployment. cxcli records the initial
created version. Later rotations happen in SecretStash; update the selected
version when Terraform metadata/manual ESO pinning should follow it. When syncing,
the wizard policy default is `auto-primary-version-pinning`.

Native External Secrets Operator renders `ClusterSecretStore` and
`ExternalSecret` resources. Deployment verifies Nebius API connectivity,
store/secret readiness, and relevant controller errors. Keep payload values and
tokens in the intended runtime secret path, out of examples, manifests, reports,
and Git. References do not authorize plaintext secret values in configuration.

On first deploy, supply payloads through hidden interactive prompts or a
protected `TF_VAR_<rendered_module_name>_payload_values` runtime variable. Its
JSON mapping is keyed by secret name, then payload key; use the exact rendered
module name. Noninteractive execution fails when required payloads are missing.
Do not put the runtime values in versioned config or shell history.

## Observability and rendered artifacts

Telemetry supports local, remote, or both. Ordinary MK8s defaults to Nebius-managed
remote collection; focused Grafana installation defaults to local. Collectors and
Grafana datasources follow routing and target type. Soperator can use native
telemetry; other targets select applicable local collectors or Nebius agents.
Do not assume all deployments use remote credentials or two Prometheus sources.

Project routing is stored at the cluster owner’s `observability.routing` in
`config.yaml`. The settings catalog owns endpoint/default policy, collector
policy, Grafana Secret contracts, and datasource/dashboard metadata. Ordinary
MK8s defaults to enabled Nebius-managed collection; an explicit `enabled: false`
keeps collection disabled. Local Grafana installation selects local signal storage
unless overridden. Explicit `kubernetes.<signal>.enabled: false` stops that ordinary
target's producer without removing its saved route or query connection. An explicit
named managed-mode selection restores Nebius write endpoints and reserved remote
query connections; separately named query-only connections remain unchanged.
Generic `postgresql` uses user-supplied Secret references in
the Database group; `grafana-postgresql` owns the Grafana-specific binding. Use the
[observability](observability.md) and [dashboard](grafana-dashboards.md) guides.

Terraform lives in `generated/infra/`, target Flux trees in
`generated/flux/targets/TARGET_ID/`, and dashboard JSON in
`generated/grafana_dashboards/`. The manifest holds runtime config, deployment
metadata, and render-time quota assessment. `terraform.auto.tfvars.json` is
ignored runtime output reconstructed from that manifest.

`.terraform.lock.hcl` records provider selections after backend-disabled init.
Backend Object Storage settings derive from project identity, not an enabled
application Object Storage component. `deploy-report.md` is a runtime handoff
written after deploy/apply, not by creation or render. See
[project workflows](project-workflows.md) for execution and recovery.

## Custom NAT gateway configuration

See [the NAT gateway guide](nat-gateway.md) for the full staged lifecycle.
`inputs.gateway_subnet_mode` selects `create` (default) or `reuse`. Creation uses
`inputs.gateway_subnet_cidr` (default `/28`); reuse requires either a literal
`inputs.gateway_subnet_id` or a canonical VPC subnet binding and omits the CIDR.
The interactive wizard derives these fields from one gateway-subnet picker after
VPC selection: choose an eligible existing/planned subnet or dedicated creation.
It does not prompt separately for `gateway_subnet_mode`. Creation remains the
new-configuration default; valid saved subnet selections are preserved.
New boot disks default to 40 GiB; explicit valid sizes are preserved.
`inputs.ssh_user_name` and `inputs.ssh_public_key` configure non-root key-only SSH
from any IPv4 source, with fail2ban protection. `inputs.admin_cidrs` is removed
and rejected; use the guide's detached upgrade procedure for existing gateways.
`inputs.source_cidrs` still contains bounded network private ranges for forwarding.

Each `routing.targets` entry has its own `policy` key and resulting route table.
`routing.verification: {enabled: true}` is the default. It enables one sequential
temporary private VM per target, with automatic pinned SSH through the gateway and
transient quota. False skips data-path verification but keeps infrastructure,
gateway readiness checks and route activation. Normal wizard completion saves
`routing.enabled: true`, including edits of disabled configurations, without an
activation question. The wizard only saves YAML; validate and render it before
deploy provisions the gateway, verifies readiness, activates routes and runs
enabled tests. The final summary shows route impact and ownership. External
attachment delegation is explained before selecting workload subnets.
Explicit `routing.enabled: false` in YAML prepares a new gateway without changing
workload attachments, or restores original routes for an active gateway.
Deploy honors this operational setting; completing the normal wizard resets it
to `true`.
Existing shared-policy configurations must detach, run `nat refresh-routes`, and
review the per-target policies before reactivation.

## WireGuard access

New enabled `wireguard-gw` rows require `access.admin_group_id`, selected from
complete live SDK listings of the tenant and selected project. This group is
externally managed; cxcli does not add users. `access.connection_secret_id` and
`access.generation` are generated after verified installation. Preserve and share
these nonsecret references with authorized administrators.

```yaml
- id: wireguard-gw
  instance_id: vpn
  enabled: true
  access:
    admin_group_id: group-placeholder
  inputs:
    ssh_user_name: ubuntu
    # Other ordinary gateway/network/disk inputs omitted.
```

`inputs.ssh_public_key`, `inputs.ssh_host_keys`, and authored
`inputs.access_bootstrap` are rejected. The deployment owner supplies the public
bootstrap map to Terraform at runtime. Private SSH material exists only in
SecretStash, protected temporary command files, and root-owned guest host files.
WG resources are excluded from generic mysterybox Kubernetes synchronization.
See [WireGuard](wireguard.md) for access and lifecycle operations.
