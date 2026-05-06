# MysteryBox Module

Reusable Terraform child module for Nebius MysteryBox secrets and versioned
payloads.

## What This Module Does

- Creates one `nebius_mysterybox_v1_secret` per `secrets` entry.
- Creates one initial primary `nebius_mysterybox_v1_secret_version` per secret
  when the secret is first deployed.
- Keeps the current primary `version_id` as metadata in the secret definition:
  use `n/a` before the first deploy, then record the created or operator-rotated
  `mbsecver-...` primary version ID.
- Sends text and file payload entries through the Nebius provider
  write-only payload fields.
- Fails before first-version creation when a configured payload key has no
  runtime value.

## What This Module Does Not Do

- It does not configure providers, credentials, or backends. Caller roots own
  those concerns.
- It does not grant IAM roles. Caller credentials need permission to create
  secrets and versions, and payload readers need the MysteryBox payload-viewer
  role.
- It does not read secret payloads after creation.
- It does not sync secrets into Kubernetes or applications.
- It does not rotate versions after initial creation. Rotate by creating a new
  MysteryBox version outside Terraform, marking it primary in Nebius, and
  updating the secret's `version_id` metadata.

## Requirements

- Terraform `>= 1.11.0, < 2.0.0`
- Nebius provider
  `terraform-provider.storage.eu-north1.nebius.cloud/nebius/nebius`
  `>= 0.5.55, < 0.6.0`

Terraform 1.11 or later is required because this module uses provider
write-only fields for secret payloads.

## MysteryBox Model

Nebius MysteryBox stores sensitive data as encrypted secrets. A secret lives in
a Nebius project, has one or more versions, and each version is an immutable
snapshot of key-value payload entries. MysteryBox returns the primary version
when a caller does not request a specific version.

This module creates each secret and one initial primary version in the same
Terraform run. The `payload` map declares the payload keys and value types for
that initial version. The `version_id` field records the current primary
MysteryBox version ID for downstream sync tools; set it to `n/a` before the
first deploy, then update it to the created primary version ID or a later
operator-rotated primary version ID.

## Usage

Local path example:

```hcl
variable "mysterybox_payload_values" {
  type      = map(map(string))
  sensitive = true
}

provider "nebius" {
  parent_id = "project-xxxxxxxx"
}

module "mysterybox" {
  source = "./platform-infra/modules/mysterybox"

  parent_id = "project-xxxxxxxx"

  secrets = [
    {
      name        = "app-runtime"
      description = "Application runtime secrets"
      version_id  = "n/a"
      payload = {
        API_KEY = {
          type = "text"
        }
        API_SECRET = {
          type = "text"
        }
      }
    }
  ]

  payload_values = var.mysterybox_payload_values
}
```

Pinned Git source example:

```hcl
module "mysterybox" {
  source = "git::https://github.com/nebius/nebius-ps-services.git//platform-infra/modules/mysterybox?ref=vX.Y.Z"

  parent_id      = "project-xxxxxxxx"
  secrets        = var.mysterybox_secrets
  payload_values = var.mysterybox_payload_values
}
```

Registry source example, if this module is published to a Terraform registry:

```hcl
module "mysterybox" {
  source  = "<namespace>/mysterybox/nebius"
  version = "~> X.Y"

  parent_id      = "project-xxxxxxxx"
  secrets        = var.mysterybox_secrets
  payload_values = var.mysterybox_payload_values
}
```

## Provider Authentication

Caller roots own Nebius provider configuration. Before applying, make sure the
active Nebius CLI profile or provider `profile` configuration points at the
same tenant/project as `parent_id`. A profile that can list the target project
but is still scoped to another default tenant/project can fail secret creation
with a Nebius `PermissionDenied` response.

For simple local runs, set the provider parent explicitly:

```hcl
provider "nebius" {
  parent_id = var.parent_id
}
```

If your default CLI profile belongs to another tenant, either switch to a
target-scoped Nebius profile before running Terraform or pass a provider
`profile` object that reads a target-scoped Nebius config file:

```hcl
provider "nebius" {
  parent_id = var.parent_id
  profile = {
    name        = "default"
    config_file = "/path/to/target-scoped/nebius-config.yaml"
    cache_file  = pathexpand("~/.nebius/credentials.yaml")
  }
}
```

## Runtime Secret Injection

Do not store payload values in `config.yaml`, `terraform.tfvars`,
`terraform.auto.tfvars.json`, or committed example files. Define a sensitive
root variable and pass it to this module's `payload_values` input, then inject
values at runtime:

```bash
export TF_VAR_mysterybox_payload_values='{
  "app-runtime": {
    "API_KEY": "replace-at-runtime",
    "API_SECRET": "replace-at-runtime"
  }
}'
```

The provider schema exposes `sensitive.payload.*` as write-only, so raw payload
values are not stored in Terraform state by that resource. The provider stores
the `sensitive.version` marker; this module derives that marker from the
declared payload shape, not from payload values. After creation, the version
resource ignores payload changes so Terraform does not mutate an immutable
MysteryBox snapshot. Once `version_id` records a created or operator-rotated
primary version, later plan/apply/destroy runs can omit `payload_values`; the
module uses a non-secret placeholder only to satisfy provider schema validation
for the ignored write-only payload fields. Protect state and plan artifacts
because they can still contain sensitive metadata.

## Inputs

- `parent_id` (required): Nebius project ID where secrets are created.
- `secrets` (required): list of secret definitions. Each secret `name` is the
  stable identity used in module outputs and `payload_values`.
  - `name`: MysteryBox secret name.
  - `description`: optional secret description.
  - `labels`: optional labels to attach to the secret.
  - `version_id`: current primary MysteryBox version ID. Use `n/a` or leave it
    empty before the first deploy; use an `mbsecver-...` ID after deploy or
    after an operator rotation.
  - `payload`: map of payload entries keyed by MysteryBox payload key.
    - `type`: optional payload value type, either `text` for plain strings or
      `file` for binary file content.
- `payload_values` (sensitive): runtime payload values by secret name and
  payload key. Keep this empty in committed configuration and provide it at
  runtime for the first version creation. After `version_id` is set to an
  `mbsecver-...` value, reruns and destroy do not require the original payload
  values.

Run `terraform-docs` after interface changes if you use generated input/output
tables in downstream documentation.

## Outputs

- `secret_ids`: created MysteryBox secret IDs keyed by secret name.
- `secret_names`: created MysteryBox secret names keyed by secret name.
- `secret_version_ids`: created initial secret-version IDs keyed by secret
  name.
- `primary_secret_version_ids`: current primary secret-version IDs keyed by
  secret name. This reports the created initial version until `version_id`
  records an operator-rotated primary version.

## Examples

- `examples/minimal`: one secret definition with runtime payload injection.

Validate the module and example:

```bash
terraform init -backend=false
terraform validate

terraform -chdir=examples/minimal init -backend=false
terraform -chdir=examples/minimal validate
```

Plan the minimal example with placeholder values:

```bash
cd examples/minimal
cp terraform.tfvars.example terraform.tfvars
terraform init -backend=false
terraform plan
```

Do not commit the copied `terraform.tfvars`.

## State And Locking

This module is a reusable child module. The caller root must configure remote
state, provider authentication, locking, and `.terraform.lock.hcl`. For shared
or production infrastructure, use a remote backend with locking and keep
credentials in environment identity, CI secrets, or a secret manager.
