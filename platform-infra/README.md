# platform-infra

`platform-infra` is the central Terraform library for Nebius customer platform
infrastructure.

It supports two consumption models:

1. Through `nebius-cxcli` for end-to-end customer automation.
2. Directly from any Terraform root (CLI not required).

## How `nebius-cxcli` Uses `platform-infra`

`nebius-cxcli` is a config-first authoring and automation tool for customer
private repositories.

High-level customer workflow:

1. Create/update one instance `config.yaml` in the customer repo.
2. Open a PR.
3. CI runs `validate --strict`, `render`, and Terraform `plan` on PR.
4. On merge, CI runs Terraform `apply` and GitOps bootstrap/reconcile.

During `render`, the CLI generates a Terraform root under the customer repo
(`generated/infra`) and points it to this library's stack:

```hcl
module "customer_platform" {
  source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/stacks/customer-platform?ref=vX.Y.Z"
  # inputs mapped from config.yaml -> terraform.auto.tfvars.json
}
```

Terraform execution still happens in the customer repo. Modules are fetched
remotely from this public repo during `terraform init`.
For secrets, `customer-platform` can provision MysteryBox secrets while taking
payload values at runtime (for example CI environment variables), so cleartext
values are not committed to repo files.

## Use Without `nebius-cxcli`

You can consume this library from any Terraform project.

### Option A: Consume the opinionated stack

```hcl
module "customer_platform" {
  source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/stacks/customer-platform?ref=vX.Y.Z"

  tenant_id      = var.tenant_id
  parent_id      = var.project_id
  region         = var.region
  cluster_name   = var.cluster_name
  subnet_id      = var.subnet_id
  ssh_public_key = var.ssh_public_key
  # ...other stack inputs
}
```

### Option B: Consume modules individually

```hcl
module "mk8s" {
  source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=vX.Y.Z"
  # module-specific inputs
}

module "sfs" {
  source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/sfs?ref=vX.Y.Z"
  # module-specific inputs
}
```

Use pinned refs (`?ref=vX.Y.Z`) for reproducibility.

## Repository Structure

```text
platform-infra/
  modules/
    mk8s/
    managed-postgresql/
    sfs/
    object-storage/
    mysterybox/
    wireguard-jumphost/
    ssh-jumphost/
  stacks/
    customer-platform/
```

### `modules/`

Reusable building blocks for individual infrastructure concerns:

- `mk8s`: managed Kubernetes cluster and node groups.
- `managed-postgresql`: Nebius managed PostgreSQL.
- `sfs`: shared filesystem infrastructure.
- `object-storage`: generic Nebius Object Storage bucket provisioning from
  input maps.
- `mysterybox`: Nebius MysteryBox secret/version provisioning with write-only
  payload support.
- `wireguard-jumphost`: VM-based WireGuard jump host.
- `ssh-jumphost`: VM-based SSH jump host with hardened cloud-init.

### `stacks/`

Opinionated compositions that wire multiple modules into a deployable platform
shape.

- `customer-platform`: the default stack consumed by Terraform roots generated
  by `nebius-cxcli`.
