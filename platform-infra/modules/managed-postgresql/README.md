# managed-postgresql module

Reusable Terraform module that creates a Nebius Managed PostgreSQL cluster.

Resources managed:

- `nebius_msp_postgresql_v1alpha1_cluster`
- `random_password` (only when `bootstrap_user_password` is not provided)

Out of scope:

- DB schema migrations and application database lifecycle
- In-cluster app deployment/secret wiring (handled by GitOps/CI workflows)

## Tier profiles

`tier` selects a built-in profile:

- `small`: `cpu-e2`, `2vcpu-8gb`, 1 host, `network-ssd`
- `medium` (default): `cpu-d3`, `4vcpu-16gb`, 1 host, `network-ssd`
- `large`: `cpu-d3`, `8vcpu-32gb`, 2 hosts, `network-ssd`
Tier input is validated and must be one of `small`, `medium`, or `large`.

## Usage

### Local path source

```hcl
module "managed_postgresql" {
  source = "./platform-infra/modules/managed-postgresql"

  enabled     = true
  parent_id   = "project-xxxxxxxx"
  network_id  = "vpcnetwork-xxxxxxxx"
  name        = "client-a-prod-pg"
  tier        = "medium"
  storage_gib = 100
}
```

### Git tag source

```hcl
module "managed_postgresql" {
  source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/managed-postgresql?ref=v0.1.0"

  enabled     = true
  parent_id   = "project-xxxxxxxx"
  network_id  = "vpcnetwork-xxxxxxxx"
  name        = "client-a-prod-pg"
  tier        = "medium"
  storage_gib = 100
}
```

### Registry source (when published)

```hcl
module "managed_postgresql" {
  source  = "nebius/managed-postgresql/nebius"
  version = "~> 0.1"

  enabled     = true
  parent_id   = "project-xxxxxxxx"
  network_id  = "vpcnetwork-xxxxxxxx"
  name        = "client-a-prod-pg"
  tier        = "medium"
  storage_gib = 100
}
```

## Inputs summary

- Required:
  - `parent_id`
  - `network_id`
- Enablement:
  - `enabled` (default `false`)
- Cluster shape:
  - `name`
  - `tier` (`small|medium|large`)
  - `storage_gib`
  - `postgresql_version` (default `16`)
  - `public_access` (default `false`)
- Runtime precondition:
  - when `enabled = true`, `name` must be non-empty
- Bootstrap:
  - `bootstrap_db_name`
  - `bootstrap_user_name`
  - `bootstrap_user_password` (optional; random password generated when null)

## Outputs summary

- `cluster_id`
- `private_read_write_endpoint`

## Security notes

- `bootstrap_user_password` is marked sensitive input.
- If omitted, a random bootstrap password is generated.
- As with most Terraform-managed credentials, bootstrap values can still exist
  in Terraform state; protect remote state and access controls accordingly.

## nebius-cxcli mapping

When consumed through `platform-infra/stacks/customer-platform`,
`nebius-cxcli` maps:

- `infra.managed_postgresql.enabled` -> `managed_postgresql_enabled`
- `infra.managed_postgresql.name` -> `managed_postgresql_name`
- `infra.managed_postgresql.tier` -> `managed_postgresql_tier`
- `infra.managed_postgresql.storage_gib` -> `managed_postgresql_storage_gib`

## Validation commands

```bash
terraform fmt -recursive
terraform init -backend=false
terraform validate
```
