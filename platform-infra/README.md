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
- [`mysterybox`](modules/mysterybox/README.md): Nebius MysteryBox secrets and initial secret versions.
- [`wireguard-jumphost`](modules/wireguard-jumphost/README.md): WireGuard VPN jump host VM.
- [`ssh-jumphost`](modules/ssh-jumphost/README.md): SSH bastion/jump host VM.

Each module has its own README and one or more runnable example roots under
`modules/<module>/examples/`.

## Shared Requirements

These requirements apply to direct consumers of the modules in this repo:

- Terraform: `>= 1.10.0, < 2.0.0`
- Nebius provider source:
  `terraform-provider.storage.eu-north1.nebius.cloud/nebius/nebius`
- Nebius provider version: `>= 0.5.55, < 0.6.0`
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
- shared values such as admin SSH settings can be wired centrally in
  `nebius-cxcli` and then bound explicitly into module inputs
- secrets should still be injected at runtime rather than committed to config
  files

If a custom or third-party module is used with `nebius-cxcli`, it should follow
the same Terraform module hygiene described in
[Custom Module Compatibility](#custom-module-compatibility).

If a module is intended to act as the cluster source for `deploy`/Flux
handoff, it must expose a stable cluster ID output so the CLI can obtain
kubeconfig after Terraform apply.

## Using Modules Directly

You can consume any module from your own Terraform root.

Example with pinned Git source:

```hcl
terraform {
  required_version = ">= 1.10.0, < 2.0.0"

  required_providers {
    nebius = {
      source  = "terraform-provider.storage.eu-north1.nebius.cloud/nebius/nebius"
      version = ">= 0.5.55, < 0.6.0"
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
- Examples:
  - `modules/mk8s/examples/minimal`
  - `modules/mk8s/examples/gpu`

### [`managed-postgresql`](modules/managed-postgresql/README.md)

- Creates a Nebius Managed PostgreSQL cluster
- Typical required inputs:
  - `parent_id`
  - `network_id`
- Example:
  - `modules/managed-postgresql/examples/minimal`

### [`sfs`](modules/sfs/README.md)

- Creates a Nebius Shared File System
- Typical required input:
  - `parent_id`
- Example:
  - `modules/sfs/examples/minimal`

### [`object-storage`](modules/object-storage/README.md)

- Creates one or more Nebius Object Storage buckets from a map input
- Typical required inputs:
  - `parent_id`
  - `buckets`
- Example:
  - `modules/object-storage/examples/minimal`

### [`mysterybox`](modules/mysterybox/README.md)

- Creates Nebius MysteryBox secrets and initial versions
- Typical required inputs:
  - `parent_id`
  - `secrets`
- Secret payload values should be injected at runtime through
  `TF_VAR_mysterybox_secret_values`
- Example:
  - `modules/mysterybox/examples/minimal`

### [`ssh-jumphost`](modules/ssh-jumphost/README.md)

- Creates an SSH jump host VM
- Typical required inputs:
  - `parent_id`
  - `region`
  - `subnet_id`
  - `name`
  - `ssh_user_name`
  - `ssh_public_key`
- Example:
  - `modules/ssh-jumphost/examples/minimal`

### [`wireguard-jumphost`](modules/wireguard-jumphost/README.md)

- Creates a WireGuard VPN jump host VM
- Typical required inputs:
  - `parent_id`
  - `region`
  - `subnet_id`
  - `name`
  - `ssh_user_name`
  - `ssh_public_key`
- Example:
  - `modules/wireguard-jumphost/examples/minimal`

## Running Examples

Examples are standalone Terraform roots and are the fastest way to validate
module behavior during development.

Examples:

```bash
terraform -chdir=modules/mk8s/examples/minimal init -backend=false
terraform -chdir=modules/mk8s/examples/minimal validate

terraform -chdir=modules/managed-postgresql/examples/minimal init -backend=false
terraform -chdir=modules/managed-postgresql/examples/minimal validate
```

If you want repo-wide checks from `platform-infra/`:

```bash
make fmt
make validate
make test-all
```

`make validate` runs `terraform init -backend=false -upgrade=false` and
`terraform validate` in each module directory, then deletes module-local lock
files.

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
    wireguard-jumphost/
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

- the source must resolve cleanly:
  - local path must exist
  - the module directory must contain Terraform `*.tf` files
  - remote Git sources should be pinned to a stable ref
- all user-settable inputs must be declared as Terraform variables
- the module should pass:
  - `terraform init -backend=false`
  - `terraform validate`
- avoid unsafe optional-value expressions that fail on omitted inputs
  - for example, prefer `try(x, null)` or `coalesce(try(x, null), {})` where
    appropriate
  - avoid patterns like `coalesce(try(x, null), null)`
- secrets should be runtime-injected instead of committed to VCS
- document required inputs, outputs, and example usage in the module README

If a Terraform error originates inside `.terraform/modules/...`, the issue is in
the source module itself rather than the generated root. Fix and validate the
module, then rerender.
