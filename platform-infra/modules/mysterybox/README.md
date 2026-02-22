# mysterybox module

Reusable Terraform module that manages Nebius MysteryBox secrets and primary
secret versions.

## What this module does

- Creates MysteryBox secret resources.
- Creates one primary secret version per secret.
- Uses provider write-only fields for payload values so raw secret values are
  not stored in Terraform state.
- Accepts secret payload values at runtime through `secret_values`.

## Inputs

- `parent_id` (required): Nebius project ID.
- `secrets` (required): map of secret definitions keyed by logical secret ID.
  - `name`
  - `description` (optional)
  - `version_description` (optional)
  - `labels` (optional)
  - `set_primary` (optional, default `true`)
  - `payload_keys` (required list of keys that must exist in payload)
- `secret_values` (sensitive): map of payload values by secret ID and key.

## Runtime secret injection

Do not store payload values in `config.yaml` or `terraform.auto.tfvars.json`.
Inject them at runtime through environment variable based Terraform variables:

```bash
export TF_VAR_mysterybox_secret_values='{
  "n8n-runtime": {
    "N8N_ENCRYPTION_KEY": "...",
    "N8N_BASIC_AUTH_PASSWORD": "..."
  }
}'
```

The module validates that all configured `payload_keys` have values.

## Example

```hcl
module "mysterybox" {
  source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mysterybox?ref=vX.Y.Z"

  parent_id = var.parent_id
  secrets = {
    app = {
      name         = "app-runtime"
      description  = "Application runtime secrets"
      payload_keys = ["API_KEY", "API_SECRET"]
      labels = {
        scope = "apps"
      }
    }
  }

  secret_values = var.mysterybox_secret_values
}
```

## Outputs

- `secret_ids`
- `secret_names`
- `secret_version_ids`
