# object-storage module

Reusable Terraform module that creates Nebius Object Storage buckets from a
generic input map.

## What this module does

- Creates one or more buckets from `buckets` map entries.
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
  buckets = {
    state = {
      name                 = "tfstate-customer-123"
      versioning_policy    = "ENABLED"
      object_audit_logging = "ALL"
      protect_from_destroy = true
      labels = {
        purpose = "terraform-state"
      }
    }
    inventory = {
      name                 = "inventory-customer-123"
      versioning_policy    = "DISABLED"
      object_audit_logging = "NONE"
      protect_from_destroy = false
    }
  }
}
```

### Git tag source

```hcl
module "object_storage" {
  source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/object-storage?ref=v0.1.0"

  parent_id = "project-xxxxxxxx"
  buckets = {
    artifacts = {
      name                 = "artifacts-customer-123"
      versioning_policy    = "ENABLED"
      object_audit_logging = "MUTATE_ONLY"
      protect_from_destroy = true
    }
  }
}
```

### Registry source (when published)

```hcl
module "object_storage" {
  source  = "nebius/object-storage/nebius"
  version = "~> 0.1"

  parent_id = "project-xxxxxxxx"
  buckets = {
    logs = {
      name                 = "logs-customer-123"
      versioning_policy    = "ENABLED"
      object_audit_logging = "ALL"
      protect_from_destroy = true
    }
  }
}
```

## Inputs summary

- Required:
  - `parent_id`
  - `buckets` (non-empty map)
- Bucket-level defaults:
  - `versioning_policy = "DISABLED"`
  - `object_audit_logging = "NONE"`
  - `protect_from_destroy = false`
  - `labels = {}`

## Outputs summary

- `bucket_ids` (map)
- `bucket_names` (map)

## Validation commands

```bash
terraform fmt -recursive
terraform init -backend=false
terraform validate
```
