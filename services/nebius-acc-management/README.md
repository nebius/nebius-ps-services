# Nebius Account Management CLI

Nebius account management CLI to help tenant administrators create projects, manage project-level IAM groups and access permits, and apply project quotas in batch.

## Features

- Keep automation stateless: no client lists stored in the repo.
- Use a tenant-admin user token and the Nebius Python SDK for all operations.
- Support batch project creation with a single command.
- Enable quota application via JSON/YAML files when required.
- Invite users to project groups via CLI or invite files.

## Requirements

- Nebius Python SDK (`nebius`) installed.
- Tenant-admin user token via `NEBIUS_IAM_TOKEN` or a configured Nebius CLI profile.
- Python 3.10+.
- Optional: Nebius CLI installed if you want the CLI to auto-fetch tokens via `nebius iam get-access-token`.

## Commands

### Create a config template

```bash
nebius-acc create-config my-projects.config.yaml
```

If the output path is omitted, the CLI writes `nebius-acc.config.yaml` in the current directory.
The command also writes sample quota and invite files alongside it with `-quota`
and `-invite` appended (for example `my-projects-quota.config.yaml` and
`my-projects-invite.config.yaml`).
You can also pass `--config-file` to point to a specific YAML when running `apply`.

### Validate config/quota/invite files

```bash
nebius-acc validate --config-file my-projects.config.yaml \
  --quota-file my-projects-quota.config.yaml \
  --invite-file my-projects-invite.config.yaml
```

### Apply YAML config (recommended)

```bash
nebius-acc apply --config-file tenant1.config.yaml \
  --quota-file tenant1-quota.config.yaml \
  --invite-file tenant1-invite.config.yaml
```

This reads the tenant config, creates projects/groups, applies quotas from the
quota file (if provided), and then processes invitations from the invite file
(if provided). Use `--quota-file` to supply per-region or per-project quotas.

### Create projects, groups, and access permits (CLI only)

```bash
nebius-acc create-projects \
  --tenant-id <tenant-id> \
  --region-id <region-id> \
  --projects project1,project2,project3 \
  --role editor
```

Defaults:

- Group naming: `grp-{project}` (e.g., `grp-project1`).
- CLI project lists use a single region; use the config file to create projects in multiple regions.

### Apply quotas to existing projects (CLI only)

```bash
nebius-acc set-quotas \
  --tenant-id <tenant-id> \
  --region-id <region-id> \
  --projects project1,project2 \
  --quota compute.disk.count=5000
```

Repeat `--quota` to add multiple quotas.

### Configure SSO federation (CLI only)

```bash
nebius-acc configure-sso \
  --tenant-id <tenant-id> \
  --name <federation-name> \
  --sso-url <login-url> \
  --idp-issuer <issuer-id>
```

SSO configuration is disabled by default in the YAML config. Use `apply` with
`configure_sso.enabled: true` to run it from YAML.

### Invite users to project groups (CLI only)

CLI-only:

```bash
nebius-acc invite-users \
  --tenant-id <tenant-id> \
  --project projectA \
  --emails user1@example.com,user2@example.com
```

Batch invite file via `apply`:

```bash
nebius-acc apply --config-file tenant1.config.yaml --invite-file tenant1-invite.config.yaml
```

Optional certificate upload:

```bash
nebius-acc configure-sso \
  --tenant-id <tenant-id> \
  --name <federation-name> \
  --sso-url <login-url> \
  --idp-issuer <issuer-id> \
  --cert-file /path/to/certificate.pem \
  --cert-description "Entra ID certificate"
```

## Config file (optional)

Each config file is for a single tenant. If a customer has multiple tenants, use
separate config files (and separate quota and invite files). The config is versioned with
`version: 1` to allow future schema changes.

Use a YAML config file with the `apply` command. CLI-only commands ignore YAML.

Top-level keys:

- `tenant_id`
- `group_name`
- `role`
- `projects` (grouped by region)
- `configure_sso`

Example:

```yaml
version: 1
tenant_id: tenant-EXAMPLE_ID
group_name: "grp-{project}"
role: editor

projects:
  eu-north1:
    projectA: {}
    projectB: {}
  eu-west1:
    projectC: {}
    projectD: {}

configure_sso:
  enabled: false
  name: corp-entra
  sso_url: https://login.microsoftonline.com/.../saml2
  idp_issuer: https://sts.windows.net/.../
  auto_create_users: true
  active: true
  force_authn: false
```

Schema file: `src/nebius_acc/config_schema.json`.

## Quota file format

Quotas are applied via the Nebius Quota Allowance API. Values replace existing limits.

### Per-region quotas

```yaml
version: 1
tenant_id: tenant-EXAMPLE_ID
regions:
  eu-north1:
    - quota: compute.disk.count
      limit: 5000
  eu-west1:
    - quota: compute.disk.count
      limit: 5000
```

### Per-project quotas (overrides per-region quotas)

```yaml
version: 1
tenant_id: tenant-EXAMPLE_ID
projects:
  projectA:
    - quota: compute.disk.count
      region: eu-north1
      limit: 2000
  projectB:
    - quota: compute.disk.count
      region: eu-north1
      limit: 10000
```

Notes:

- Units supported: `KiB`, `MiB`, `GiB`, `TiB`, `PiB` (case-insensitive). Plain integers are allowed.
- Quota files are versioned; use `version: 1` and `tenant_id`.
- `apply` checks that `tenant_id` matches the config file.
- `apply` applies quotas only from the quota file (if provided).
- When both per-region and per-project quotas are present, per-project entries override matching quota+region.
- Per-project quota entries must include `region`. Per-region quotas use the region key.
- Quota files should use the `*.config.yaml` pattern so they stay git-ignored.

### Invite file format

```yaml
version: 1
tenant_id: tenant-EXAMPLE_ID
invites:
  projectA:
    - email: user1@example.com
    - email: user2@example.com
  projectB:
    - email: user3@example.com
```

Notes:

- Invite files are versioned; use `version: 1`.
- Emails are normalized to lowercase and deduplicated.
- Invite files should use the `*.config.yaml` pattern so they stay git-ignored.
- A user can belong to multiple project groups. If the user already exists in the tenant, the CLI adds them directly to each group; otherwise it sends one invitation and waits for acceptance before group membership can be created.

## Release & Versioning

- Versions are derived from annotated Git tags (`nebius-acc-vMAJOR.MINOR.PATCH`)
  via `setuptools-scm`.
- Tags are prefixed with the project name to avoid collisions in this shared repo.
- Semantic Versioning:
  - **MAJOR:** breaking changes
  - **MINOR:** backward-compatible features
  - **PATCH:** bug fixes
- Keep `CHANGELOG.md` updated before tagging.

### How to create a release

1. Prepare on your working branch: `./release.sh --prep nebius-acc-vX.Y.Z`
2. Open a PR and merge it to `main`.
3. On `main`, publish the release: `./release.sh --publish nebius-acc-vX.Y.Z`

Note: `--publish` requires `main` to be clean and up to date with
`origin/main`.

## Project Structure

```text
├── LICENSE
├── README.md
├── CHANGELOG.md
├── pyproject.toml
├── release.sh
├── .markdownlint.json
├── doc/
│   └── design.md
├── src/
│   └── nebius_acc/
│       ├── __init__.py
│       ├── cli.py
│       ├── auth.py
│       ├── config_loader.py
│       ├── config_schema.json
│       ├── config_template.py
│       ├── core.py
│       ├── errors.py
│       ├── nebius_sdk.py
│       └── quota.py
```

- `doc/`: architecture and workflows.
- `src/nebius_acc/`: CLI and core automation logic.
