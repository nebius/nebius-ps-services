# object-storage module

Reusable Terraform module that creates one Nebius Object Storage bucket per
module instance.

## What this module does

- Creates one bucket from a simple scalar input contract.
- Lets each bucket define:
  - `name`
  - `versioning_policy`
  - `object_audit_logging`
  - `protect_from_destroy`
  - `labels`

## What this module does not do

- It does not configure Terraform backend blocks (`backend "s3"`). Backend
  config stays in the calling Terraform root.
- It does not manage S3 credentials/secrets.
- It does not expose a bucket encryption toggle because
  `nebius_storage_v1_bucket` currently has no encryption argument. Backend-side
  state encryption is configured in Terraform backend settings (`encrypt=true`).

## Usage

### Local path source

```hcl
module "object_storage" {
  source = "./platform-infra/modules/object-storage"

  parent_id = "project-xxxxxxxx"
  name      = "tfstate-customer-123"

  versioning_policy    = "ENABLED"
  object_audit_logging = "ALL"
  protect_from_destroy = true
  labels = {
    purpose = "terraform-state"
  }
}
```

### Git tag source

```hcl
module "object_storage" {
  source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/object-storage?ref=v0.1.0"

  parent_id = "project-xxxxxxxx"
  name      = "artifacts-customer-123"

  versioning_policy    = "ENABLED"
  object_audit_logging = "MUTATE_ONLY"
  protect_from_destroy = true
}
```

### Registry source (when published)

```hcl
module "object_storage" {
  source  = "nebius/object-storage/nebius"
  version = "~> 0.1"

  parent_id = "project-xxxxxxxx"
  name      = "logs-customer-123"

  versioning_policy    = "ENABLED"
  object_audit_logging = "ALL"
  protect_from_destroy = true
}
```

## Examples

- `examples/minimal`: single protected bucket suitable for Terraform state.

## Inputs summary

- Required:
  - `parent_id`
  - `name`
- Optional:
  - `versioning_policy = "DISABLED"`
  - `object_audit_logging = "NONE"`
  - `protect_from_destroy = false`
  - `labels = {}`

## nebius-cxcli usage

- `nebius-cxcli` treats this module as one bucket per enabled component row.
- `inputs.name` is required and must be unique for the target Nebius bucket.
- `labels` can be provided as a YAML/JSON mapping in the wizard or edited
  directly in `config.yaml`.

## Outputs summary

- `bucket_id`
- `bucket_name`

## Validation commands

```bash
terraform fmt -recursive
terraform -chdir=examples/minimal init -backend=false
terraform -chdir=examples/minimal validate
```
