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
(`generated/infra`) and points module sources to this library:

```hcl
module "mk8s" {
  source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mk8s?ref=vX.Y.Z"
  # inputs mapped from config.yaml -> terraform.auto.tfvars.json
}

module "sfs" {
  source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/sfs?ref=vX.Y.Z"
  # inputs mapped from config.yaml -> terraform.auto.tfvars.json
}
```

Terraform execution still happens in the customer repo. Modules are fetched
remotely from this public repo during `terraform init`.
For secrets, generated Terraform can provision MysteryBox secrets while taking
payload values at runtime (for example CI environment variables), so cleartext
values are not committed to repo files.

## Use Without `nebius-cxcli`

You can consume this library from any Terraform project.

### Consume modules individually

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

## Local Development

Run repository-wide quality checks from `platform-infra/`:

```bash
make fmt
make validate
make test-all
```

Lock-file policy:

- Module directories do not keep `.terraform.lock.hcl`.
- Lock files are expected in root configurations (for example consumer repos or
  module `examples/` roots) where `terraform init` is executed.

## Repository Structure

```text
platform-infra/
  Makefile
  modules/
    mk8s/
      examples/
        minimal/
        gpu/
    managed-postgresql/
      examples/
        minimal/
    sfs/
      examples/
        minimal/
    object-storage/
      examples/
        minimal/
    mysterybox/
      examples/
        minimal/
    wireguard-jumphost/
      examples/
        minimal/
    ssh-jumphost/
      examples/
        minimal/
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
