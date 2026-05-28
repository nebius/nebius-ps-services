# platform-infra

`platform-infra` is the reusable Terraform module library for Nebius customer
platform infrastructure.

It supports two consumption models:

1. Through `nebius-cxcli` for config-driven customer automation.
2. Directly from any Terraform root without the CLI.

## What This Repo Contains

Reusable modules under `modules/`:

- [`mk8s`](modules/mk8s/README.md): Nebius Managed Kubernetes cluster and node groups.
- [`managed-postgresql`](modules/managed-postgresql/README.md): Nebius Managed PostgreSQL cluster.
- [`sfs`](modules/sfs/README.md): Nebius Shared File System.
- [`object-storage`](modules/object-storage/README.md): Nebius Object Storage buckets.
- [`mysterybox`](modules/mysterybox/README.md): Nebius MysteryBox secrets and versioned payloads.
- [`vm`](modules/vm/README.md): General Nebius Compute virtual machine provisioning.
- [`wireguard-gw`](modules/wireguard-gw/README.md): WireGuard VPN gateway VM.
- [`ssh-jumphost`](modules/ssh-jumphost/README.md): SSH bastion/jump host VM.

Each module has its own README and one or more runnable example roots under
`modules/<module>/examples/`.

## Shared Requirements

These requirements apply to direct consumers of the modules in this repo:

- Terraform: `>= 1.10.0, < 2.0.0`
- `mysterybox` requires Terraform `>= 1.11.0, < 2.0.0` because it uses
  provider write-only payload fields
- Nebius provider source:
  `terraform-provider.storage.eu-north1.nebius.cloud/nebius/nebius`
- Nebius provider version: `>= 0.5.55, < 0.6.0` for most modules; `vm`
  requires `>= 0.5.217, < 0.6.0` so preemptible instances can omit the
  deprecated priority field
- `managed-postgresql` also requires `hashicorp/random >= 3.6.0, < 4.0.0`
- The calling Terraform root owns backend configuration, provider configuration,
  credentials, and lock files
- Use pinned Git refs such as `?ref=vX.Y.Z` in production
- Use real existing Nebius IDs for required inputs such as `parent_id`,
  `network_id`, and `subnet_id`
- Do not commit secrets or secret-bearing `*.tfvars` files

Important repo policy:

- Module directories do not keep `.terraform.lock.hcl`
- Lock files belong in consumer roots and example roots where `terraform init`
  is actually executed

## Using With `nebius-cxcli`

`nebius-cxcli` renders a Terraform root in the customer repository and points it
at modules from this repo by Git source.

Rendered module usage looks like:

```hcl
module "mk8s" {
  source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=vX.Y.Z"
}
```

Key behavior when these modules are consumed through the CLI:

- `config.yaml` values are mapped into Terraform variables in
  `generated/infra/terraform.auto.tfvars.json`
- root provider/backend configuration is generated in the customer repo, not in
  this repo
- reusable child modules must not configure provider auth or backend blocks
- shared values such as admin SSH settings can be wired centrally in
  `nebius-cxcli` and then bound explicitly into module inputs
- collection/object Terraform inputs are supported; the CLI wizard edits them as
  YAML/JSON values instead of flattening them into string-only prompts
- module-level `enabled` switches are not part of the contract; enablement
  belongs in `config.yaml` plus the rendered Terraform root
- secrets should still be injected at runtime rather than committed to config
  files

`nebius-cxcli validate-sources` now enforces the fast module-side contract that
keeps these Terraform child modules CLI-friendly:

- module source must resolve and pass `terraform init -backend=false`
- `versions.tf` must declare `required_version` and `required_providers`
- child modules must not contain backend or provider configuration blocks
- missing canonical files such as `main.tf`, `variables.tf`, `outputs.tf`,
  `README.md`, and runnable `examples/` roots are reported as warnings

If a custom or third-party module is used with `nebius-cxcli`, it should follow
the same Terraform module hygiene described in
[Custom Module Compatibility](#custom-module-compatibility).

If a module is intended to act as the cluster source for `deploy`/Flux
handoff, it must expose a stable cluster ID output so the CLI can obtain
kubeconfig after Terraform apply.

If a module is intended to participate in Nebius API status reporting during
`deploy`/`terraform apply`, the source catalog entry should be able to point to
stable component inputs for:

- the project or parent scope (`status.parent_input`)
- the resource identity (`status.name_input`)

That pattern is why the modules in this repo consistently use inputs such as
`parent_id`, `cluster_name`, and `name`.

## Using Modules Directly

You can consume any module from your own Terraform root.

Example with pinned Git source. The example uses the `modules/vm` provider
floor, which is also valid for the other modules.

```hcl
terraform {
  required_version = ">= 1.10.0, < 2.0.0"

  required_providers {
    nebius = {
      source  = "terraform-provider.storage.eu-north1.nebius.cloud/nebius/nebius"
      version = ">= 0.5.217, < 0.6.0"
    }
  }
}

module "mk8s" {
  source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=vX.Y.Z"

  parent_id    = "project-xxxxxxxx"
  cluster_name = "example-mk8s"
  subnet_id    = "vpcsubnet-xxxxxxxx"

  cpu_nodes_count    = 2
  cpu_nodes_platform = "cpu-d3"
  cpu_nodes_preset   = "4vcpu-16gb"
}
```

Caller expectations:

- configure Nebius provider auth in the caller root
- configure remote state/backend in the caller root when needed
- pin module refs
- validate with `terraform init` and `terraform validate` in the caller root

## Module Catalog

Use the per-module README for the full contract. The summary below is intended
to help users choose the right module quickly.

### [`mk8s`](modules/mk8s/README.md)

- Creates one MK8s cluster and CPU/GPU node groups
- Core required inputs:
  - `parent_id`
  - `cluster_name`
  - `subnet_id`
- Caller should set `cpu_nodes_count` explicitly when a baseline CPU node group
  is desired; `nebius-cxcli` seeds that value into `config.yaml` through
  `component_sources.yaml` so the resulting project config makes node count
  visible.
- Examples:
  - `modules/mk8s/examples/minimal`
  - `modules/mk8s/examples/gpu`

### [`managed-postgresql`](modules/managed-postgresql/README.md)

- Creates a Nebius Managed PostgreSQL cluster
- Required inputs:
  - `parent_id`
  - `network_id`
  - `name`
- Example:
  - `modules/managed-postgresql/examples/minimal`

### [`sfs`](modules/sfs/README.md)

- Creates a Nebius Shared File System
- Required inputs:
  - `parent_id`
  - `name`
  - `size_gib`
- Example:
  - `modules/sfs/examples/minimal`

### [`object-storage`](modules/object-storage/README.md)

- Creates one Nebius Object Storage bucket per module instance
- Required inputs:
  - `parent_id`
  - `name`
- Example:
  - `modules/object-storage/examples/minimal`

### [`mysterybox`](modules/mysterybox/README.md)

- Creates Nebius MysteryBox secrets and one initial primary version per secret
- Uses provider write-only payload fields for text and file secret values
- Secret versions are immutable MysteryBox snapshots; rotate by creating a new
  MysteryBox version outside Terraform, marking it primary, and recording that
  `version_id`
- Typical required inputs:
  - `parent_id`
  - `secrets`
- Secret payload values should be injected at runtime through
  `TF_VAR_mysterybox_payload_values` as
  `{secret_name={payload_key=value}}`
- After the created primary `version_id` is recorded, reruns and destroy do not
  need the original runtime payload values.
- Payload readers need the MysteryBox payload-viewer role; create/update
  permissions do not automatically grant payload viewing
- Example:
  - `modules/mysterybox/examples/minimal`

### [`vm`](modules/vm/README.md)

- Creates one Nebius Compute virtual machine with explicit platform/preset
  selection
- Required inputs:
  - `parent_id`
  - `subnet_id`
  - `name`
  - `platform`
  - `preset`
  - `ssh_public_key`
- Key optional capabilities:
  - regular or preemptible GPU VMs
  - managed boot/data disks and attached filesystems
  - boot/data disk managed encryption for supported SSD NRD / SSD IO M3 disks
    and disk deletion protection
  - optional GPU cluster creation or attachment
  - optional Docker-based container bootstrap on the VM
- Examples:
  - `modules/vm/examples/minimal`
  - `modules/vm/examples/gpu-preemptible`
  - `modules/vm/examples/containerized`

### [`ssh-jumphost`](modules/ssh-jumphost/README.md)

- Creates an SSH jump host VM
- Required inputs:
  - `parent_id`
  - `subnet_id`
  - `name`
  - `platform`
  - `preset`
  - `source_image_family`
  - `ssh_public_key`
  - `allowed_cidrs`
- Example:
  - `modules/ssh-jumphost/examples/minimal`

### [`wireguard-gw`](modules/wireguard-gw/README.md)

- Creates a WireGuard VPN gateway VM
- Required inputs:
  - `parent_id`
  - `subnet_id`
  - `name`
  - `platform`
  - `preset`
  - `source_image_family`
  - `ssh_public_key`
- Example:
  - `modules/wireguard-gw/examples/minimal`

## Running Examples

Examples are standalone Terraform roots and are the fastest way to validate
module behavior during development.

Examples:

```bash
terraform -chdir=modules/mk8s/examples/minimal init -backend=false
terraform -chdir=modules/mk8s/examples/minimal validate

terraform -chdir=modules/managed-postgresql/examples/minimal init -backend=false
terraform -chdir=modules/managed-postgresql/examples/minimal validate

terraform -chdir=modules/mysterybox/examples/minimal init -backend=false
terraform -chdir=modules/mysterybox/examples/minimal validate

terraform -chdir=modules/vm/examples/minimal init -backend=false
terraform -chdir=modules/vm/examples/minimal validate
```

If you want repo-wide checks from `platform-infra/`:

```bash
make fmt
make validate
make test-all
```

`make validate` runs `terraform init -backend=false -upgrade=false` and
`terraform validate` in each module directory, then deletes module-local lock
files. It also validates every `modules/*/examples/*` root with
`-lockfile=readonly` so checked-in example lock files cannot drift silently.

## Repository Structure

```text
platform-infra/
  Makefile
  README.md
  modules/
    mk8s/
      README.md
      examples/
        minimal/
        gpu/
    managed-postgresql/
      README.md
      examples/
        minimal/
    sfs/
      README.md
      examples/
        minimal/
    object-storage/
      README.md
      examples/
        minimal/
    mysterybox/
      README.md
      examples/
        minimal/
    vm/
      README.md
      examples/
        minimal/
        gpu-preemptible/
        containerized/
    wireguard-gw/
      README.md
      examples/
        minimal/
    ssh-jumphost/
      README.md
      examples/
        minimal/
```

## Custom Module Compatibility

If you want a custom Terraform module to behave well when consumed by
`nebius-cxcli`, keep it aligned with the conventions used in this repo:

### When Adding a New Module

When you add a new Terraform module under `platform-infra/modules/<module>/`,
follow this sequence:

1. Create the reusable child-module files:
   - `main.tf`
   - `variables.tf`
   - `outputs.tf`
   - `versions.tf`
   - `locals.tf` when computed values improve readability
2. Create a runnable example root under `modules/<module>/examples/`:
   - at minimum `examples/minimal/`
   - include the example root `versions.tf`
   - example roots are where `terraform init` and lock files belong
3. Write `modules/<module>/README.md`:
   - what the module does and does not do
   - required inputs and important optional inputs
   - outputs consumed by operators or `nebius-cxcli`
   - local path and pinned Git source examples
   - any runtime-only secret injection pattern
4. Validate the module locally:
   - `terraform -chdir=modules/<module>/examples/minimal init -backend=false`
   - `terraform -chdir=modules/<module>/examples/minimal validate`
   - `make validate` from `platform-infra/`
5. If the module should be selectable from `nebius-cxcli`, add or update the
   catalog entry in `services/nebius-cxcli/component_sources.yaml`:
   - `portable_source`
   - `local_source`
   - `description`
   - `group`
   - optional `defaults`
   - optional `handoff`
   - optional `status`
6. If the new module changes the CLI contract, update the relevant
   `nebius-cxcli` docs and changelog in the same change.

### Canonical Pattern

- the source must resolve cleanly:
  - local path must exist
  - the module directory must contain Terraform `*.tf` files
  - remote Git sources should be pinned to a stable ref
- the module must be a reusable child module:
  - declare `required_version` and `required_providers`
  - do not configure provider auth inside the module
  - do not configure backend blocks inside the module
- all user-settable inputs must be declared as Terraform variables
- do not add an internal Terraform `enabled` toggle for modules that the CLI
  already enables/omits at the config/render layer
- use stable, human-meaningful identity inputs when possible:
  - `parent_id` for project/scope
  - `name` or a similarly clear resource-name input for the created resource
- export stable outputs for integration-critical values
  - for example `cluster_id`, `instance_id`, `bucket_id`, `filesystem_id`
  - cluster-source modules used by local `deploy` should expose a stable cluster
    ID output for kubeconfig handoff
- keep secrets and secret-bearing runtime values out of committed config:
  - model declarative metadata as normal variables
  - inject secret payloads at runtime where needed

### CLI-Friendly Input Shape

- scalar inputs are the easiest wizard experience and should be preferred for
  common day-1/day-2 values
- collection/object inputs are still acceptable:
  - `list(...)`, `map(...)`, `object(...)`, and `tuple(...)` are supported by
    the CLI wizard as YAML/JSON values
  - document those inputs clearly in the module README so operators know they
    are editing structured values
- if an optional input is actually operationally required in real use, enforce
  that with Terraform validation/preconditions and document it explicitly
- avoid unsafe optional-value expressions that fail on omitted inputs
  - for example, prefer `try(x, null)` or `coalesce(try(x, null), {})` where
    appropriate
  - avoid patterns like `coalesce(try(x, null), null)`
- prefer stable input names that the CLI can reason about across modules:
  - `parent_id` for Nebius project/scope
  - `name` for the primary resource identity
  - more specific names such as `cluster_name` only when they improve clarity
- when a module needs Nebius-backed status reporting, keep the watched resource
  name and parent scope available from config inputs rather than only from
  computed state
- if an input needs a good interactive experience in the CLI, prefer a real
  Terraform type over stringly typed blobs:
  - `list(string)` for CIDRs and peer lists
  - `map(string)` for labels/simple metadata
  - `object(...)` / `map(object(...))` for structured module-native config

### Validation and Documentation

- the module should pass:
  - `terraform init -backend=false`
  - `terraform validate`
- keep runnable example roots under `examples/`
- document required inputs, outputs, example usage, and any runtime-only secret
  injection pattern in the module README
- if the module should participate in CLI status polling, keep the resource
  identity discoverable from config inputs so `component_sources.yaml` can map
  `status.parent_input` and `status.name_input` without inspecting Terraform
  state
- if the module should participate in local `deploy` cluster handoff, export a
  stable cluster identifier output that `nebius-cxcli` can consume after
  Terraform apply

### `component_sources.yaml` Author Notes

If a new module is meant to be exposed through `nebius-cxcli`, the catalog
entry is part of the implementation, not an optional follow-up.

Typical entry shape:

```yaml
infra:
  tf_modules:
    - module: example-module
      portable_source: git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/example-module?ref=main
      local_source: ../../platform-infra/modules/example-module
      description: Short operator-facing description
      group: Compute
      enable: false
      outputs:
        tf_outputs: true
      defaults:
        inputs.parent_id: shared.some_default
      handoff:
        cluster_id: cluster_id
        access: access
      status:
        kind: nebius.some.service.resource
        parent_input: parent_id
        name_input: name
```

Guidance:

- `module` should match the module directory name unless there is a very strong
  reason not to
- `description` should be short and operator-facing because it appears in the
  CLI selection UX
- `group` should match the existing grouping style (`Compute`, `Storage`,
  `Security`, `Network`)
- `handoff` is only for modules that act as the cluster source for local
  kubeconfig/Flux flows
- `status` is only for modules where the CLI should poll Nebius APIs during
  deploy/apply

If a Terraform error originates inside `.terraform/modules/...`, the issue is in
the source module itself rather than the generated root. Fix and validate the
module, then rerender.
