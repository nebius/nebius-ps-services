# nebius-cxcli

`nebius-cxcli` is the Python CLI that validates a canonical `config.yaml`,
renders deterministic Terraform + Flux artifacts, and drives CI-safe operations
for Nebius customer platform instances.

The binary command is `nebius-cxcli`.

## Recommended Workflow

`create` now supports three explicit operating modes:

1. Scaffold only (default, no CI generated):
   - create instance path + starter `config.yaml` under any existing folder
   - no GitHub workflow generation
   - no deployment actions
2. Local one-shot deploy (`--deploy`):
   - scaffold instance path + config
   - run `validate --strict`, `render`, `terraform apply`, and local Flux
     manifest apply
3. CI bootstrap (`--bootstrap-ci`):
   - scaffold instance path + config
   - generate `.github/workflows/nebius-deployments.yml` at git repo root
   - optionally auto-bootstrap Nebius/GitHub CI auth secrets

Mode selection quick guide:

| Goal | Command mode | Requires git repo | Executes deployment |
| --- | --- | --- | --- |
| Generate instance config/artifacts only | `create` (default) | No | No |
| Generate and deploy locally once | `create --deploy` | No | Yes (local) |
| CI automation | `create --bootstrap-ci` | Yes | Yes (CI) |

For CI-driven operation:

1. Clone the customer private repo locally and create a branch.
2. Create an empty deployments root folder in that repo (any folder name).
3. Run `nebius-cxcli create <deployments-root-path> --bootstrap-ci`.
4. Edit the generated `config.yaml`.
5. Commit and open a PR.
6. CI runs:
   - PR: `validate --strict`, `render`, `terraform plan`
   - merge to `main`: `validate --strict`, `render`, `terraform apply`, Flux
     bootstrap/reconcile, inventory/email steps

`validate`, `render`, `terraform`, `flux`, and `inventory/email` commands can
still be run locally, but local execution is optional and mainly useful for
troubleshooting or pre-checks.
Generated customer CI installs `nebius-cxcli` directly from this GitHub
repository (not PyPI) using `NEBIUS_CXCLI_REF`.
For stable CLI releases (`x.y.z`), `create` pins `NEBIUS_CXCLI_REF` to
`nebius-cxcli-vx.y.z`; for development builds, it defaults to `main`.
Generated Terraform `main.tf` points to
`platform-infra/stacks/customer-platform` in this same repository using the
same ref strategy.
For Flux bootstrap, generated CI installs Flux + Nebius CLI, creates a Nebius
service-account profile, reads `mk8s_cluster_id` from Terraform outputs, then
runs `nebius mk8s cluster get-credentials --external`.
If you prefer an explicit/manual flow, `nebius-cxcli auth bootstrap` can still
be used to rotate or push CI auth secrets on demand.
Create an empty deployments root folder first (any name), then pass that exact
folder path to `create` and `discover`.
Only CI-related operations require a git repository (`create --bootstrap-ci`,
`discover`, and auto GitHub secret sync).
Each instance has one canonical config file:
`<deployments-root>/instances/<client_name>--<tenant_id>/<env>/<cluster_name>/config.yaml`.
Identity metadata is grouped under `client_info` in that file.

## CI Automation Requirements

For end-to-end CI automation (`create --bootstrap-ci` with auth bootstrap/secret
sync), ensure all of these are available:

- Nebius auth context is available locally (token/profile):
  `NEBIUS_IAM_TOKEN` or `nebius auth login` profile config.
- GitHub token is exported locally as `GH_TOKEN` or `GITHUB_TOKEN` and has
  permission to write repository Actions secrets.
- Repository slug is resolvable from `git remote origin`, or explicitly
  provided via `--github-repo <owner>/<repo>`.

`create --bootstrap-ci` now fails fast when this context is missing, so CI auth
is always fully bootstrapped instead of partially scaffolded.

`create` generates a minimal MK8s config by default. You can optionally add
advanced provider-style fields when needed:

- `infra.mk8s.cluster_overrides`
- `infra.mk8s.cpu_node_group_overrides`
- `infra.mk8s.gpu_node_group_overrides`

Examples of optional advanced fields:

- Cluster: `control_plane.subnet_id`, `control_plane.version`, `control_plane.etcd_cluster_size`,
  `control_plane.audit_logs`, `control_plane.endpoints.public_endpoint`,
  `kube_network.service_cidrs`, `labels`, `name`, `parent_id`, `resource_version`.
- Node group: `autoscaling.min_node_count`, `autoscaling.max_node_count`,
  `fixed_node_count`, `strategy.max_surge`, `strategy.max_unavailable`,
  `template.resources.platform`, `template.resources.preset`,
  `template.boot_disk.*`, `template.cloud_init_user_data`, `template.filesystems`,
  `template.gpu_cluster.id`, `template.gpu_settings.drivers_preset`,
  `template.metadata.labels`, `template.network_interfaces`,
  `template.preemptible`, `template.reservation_policy`, `template.service_account_id`,
  `template.taints`, `labels`, `name`, `parent_id`, `resource_version`, `version`.

These keys are schema-validated and rendered only when present, so default
`config.yaml` stays concise.
Unknown fields are rejected (`extra="forbid"`), so if you add optional fields
they must be part of the supported provider-aligned schema.
When set, they are emitted into `terraform.auto.tfvars.json` as:
`k8s_version`, `etcd_cluster_size`, `subnet_id`,
`mk8s_cluster_overrides`, `mk8s_cpu_node_group_overrides`,
`mk8s_gpu_node_group_overrides`, plus platform identity keys (`tenant_id`,
`parent_id`, `region`), MysteryBox keys (`mysterybox_*`), WireGuard keys
(`wireguard_*`), and SSH jump-host keys (`ssh_jumphost_*`).
Your target Terraform stack must declare matching variables for these keys.
`infra.mk8s.egress_gateway.enabled` is GitOps-owned in this CLI:
it renders Flux manifests for in-cluster Cilium egress-gateway enablement.
No `enable_egress_gateway` key is emitted in generated tfvars.
`apps.platform.observability.*` is also GitOps-owned:
observability values are rendered only into Flux HelmRelease manifests, not
into Terraform tfvars.
`infra.wireguard-jumphost` is Terraform-owned in this CLI:
`render` writes `wireguard_*` inputs into generated tfvars and feeds them into
`module "customer_platform"` from
`platform-infra/stacks/customer-platform` in generated `main.tf`.
By default, WireGuard creates and attaches a dedicated static public IP
allocation. Set `create_public_ip_allocation: false` and provide
`public_ip_allocation_id` to reuse an existing allocation.
By default it uses NAT mode (`nat_mode: true`) and UDP listen port `51820`
(`listen_port`) for point-to-site connectivity.
Use `tunnel_cidr` to override the server interface CIDR (default
`10.8.0.1/24`) when your client/VPC routes require a different range.
Use `clients` to automate peer creation and client config generation on the
WireGuard VM (no manual UI workflow required); client entries are generated
with per-client PSK for defense-in-depth.
`infra.ssh-jumphost` is also Terraform-owned in this CLI:
`render` writes `ssh_jumphost_*` inputs into generated tfvars and feeds them
into the same stack.
`infra.ssh-jumphost.allowed_cidrs` is the required source IP allowlist when
`infra.ssh-jumphost.enabled: true`; bootstrap uses strict mode and fails if the
allowlist is empty (no open-to-world fallback).

Validation model:

- `nebius-cxcli validate` checks schema + path alignment only.
- `nebius-cxcli validate --strict` checks deployment readiness and rejects
  starter placeholders.
- The starter `config.yaml` is intentionally schema-valid first, then operators
  replace placeholders before CI/apply (`--strict` enforces this boundary).

Template customization model:

- Per instance: run `create`, then edit that instance `config.yaml` directly.
- Schema boundaries are enforced by `validate`/`validate --strict`.
- Changing global starter defaults in generated files currently requires a code
  change in `src/nebius_cxcli/config_template.py` and a new CLI release.

SSH public key input:

- `infra.ssh_public_key` is required and must be the inline public key
  string.
- `infra.ssh_user_name` defines the shared SSH username used by MK8s node
  bootstrap and jump-host modules.
- Path-based keys are intentionally not supported to avoid local-path vs CI
  runner drift.
- WireGuard jump host reuses this same inline SSH key by default.

State bucket security defaults:

- `infra.object_storage.state_bucket.manage` controls whether the platform stack
  should create/manage the state bucket resource. Set
  `infra.object_storage.state_bucket.manage: true` when you want Terraform to
  create the tf-state bucket.
- `infra.object_storage.state_bucket.encryption` defaults to `true` and renders
  Terraform backend `encrypt = true`.
- `infra.object_storage.state_bucket.versioning_policy` defaults to `ENABLED`.
- `infra.object_storage.state_bucket.object_audit_logging` defaults to `ALL`.
- `infra.object_storage.state_bucket.protect_from_destroy` defaults to `true`.

Inventory bucket policy controls:

- `infra.object_storage.inventory_bucket.manage` (default `true`)
- `infra.object_storage.inventory_bucket.versioning_policy` (default `DISABLED`)
- `infra.object_storage.inventory_bucket.object_audit_logging` (default `NONE`)
- `infra.object_storage.inventory_bucket.protect_from_destroy` (default `false`)

Additional infra-to-Terraform mappings:

- `infra.managed_postgresql.postgresql_version` ->
  `managed_postgresql_postgresql_version`
- `infra.managed_postgresql.public_access` ->
  `managed_postgresql_public_access`
- `infra.sfs.type` -> `sfs_type`

MysteryBox secrets model:

- `infra.mysterybox` is Terraform-owned and managed by
  `platform-infra/modules/mysterybox`.
- `config.yaml` stores only secret metadata and payload key -> env-var references
  (`entries[].value_from_env`), not secret values.
- During `terraform plan/apply`, `nebius-cxcli` resolves those environment
  variables and injects `TF_VAR_mysterybox_secret_values` at runtime.
- Terraform uses provider write-only fields for payload values, so raw secret
  data is not persisted in Terraform state.
- Use `scope: platform` / `scope: apps` labels in config to separate ownership
  intent while keeping one MysteryBox backend.
- In-cluster sync is now built in:
  - `apps.platform.external_secrets` installs External Secrets Operator (ESO)
    via Flux
    ([external-secrets.io](https://external-secrets.io/latest/)).
  - `apps.platform.external_secrets.mysterybox` renders a webhook-based
    `ClusterSecretStore` plus a Flux-managed MysteryBox bridge deployment.
  - Bridge default image is public: `quay.io/nebius/mysterybox-bridge:latest`
    (override with your pinned tag).
  - Standalone bridge source, Docker build context, and Helm charts live in
    `services/mysterybox-bridge` so teams can reuse it without `nebius-cxcli`.
  - The bridge uses Nebius SDK calls (`SecretService.GetByName`,
    `PayloadService.GetByKey`) because ESO has no native MysteryBox provider.
  - ESO->bridge requests are protected with a webhook header token secret
    (`external-secrets.io/type=webhook`) rendered/seeded automatically.
  - `infra.mysterybox.secrets[].k8s_sync` defines which app-scoped MysteryBox
    secrets are synced into Kubernetes Secrets (`ExternalSecret` resources).
  - Secret values are still not committed to Git; the bridge auth Secret is
    seeded at deploy/bootstrap time from environment variables.

State bucket operation modes:

- Prod/common practice (recommended): pre-create the tf-state bucket and keep
  `state_bucket.manage: false`.
- Bootstrap/dev convenience: set `state_bucket.manage: true` and use explicit
  bootstrap steps (for example local backend first, then switch to remote)
  because Terraform backend init happens before resource creation.

For shared filesystem-to-pods flow, `create` now includes `infra.sfs.csi`
defaults. When `infra.sfs.csi.enabled: true`, `render` generates Flux manifests
for:

- `OCIRepository` + `HelmRelease` of `csi-mounted-fs-path`
- Namespace manifests (for CSI namespace and PVC namespaces when enabled)
- One or more `PersistentVolumeClaim` manifests from `infra.sfs.csi.pvcs`

This keeps CSI driver installation and PVC lifecycle in GitOps/Flux instead of
manual Helm/kubectl steps.

When `infra.mk8s.egress_gateway.enabled: true`, `render` also generates
Flux-managed Cilium egress gateway manifests:

- `ConfigMap` toggle (`enable-ipv4-egress-gateway=true`)
- restart patches for `cilium` DaemonSet and `cilium-operator` Deployment
- `CiliumClusterwideNetworkPolicy` for egress-gateway nodes
- Cloud-side prerequisites (for example dedicated egress node group/subnet)
  must be provisioned by the Terraform stack.

WireGuard jump host rendering:

- WireGuard is implemented inside the central Terraform stack
  `platform-infra/stacks/customer-platform`.
- `render` generates one `module "customer_platform"` block and maps all
  rendered tfvars keys (including `wireguard_*`) into that stack.
- `infra.wireguard-jumphost.tunnel_cidr` is passed to Terraform as
  `wireguard_tunnel_cidr`.
- `infra.wireguard-jumphost.listen_port` is passed as
  `wireguard_listen_port` (default `51820`).
- `infra.wireguard-jumphost.nat_mode` is passed as `wireguard_nat_mode`
  (default `true`).
- `infra.wireguard-jumphost.clients` is passed as `wireguard_clients` for
  automated peer/client config bootstrap on the VM.
- This keeps all core infra modules in the same central library:
  - `platform-infra/modules/mk8s`
  - `platform-infra/modules/managed-postgresql`
  - `platform-infra/modules/sfs`
  - `platform-infra/modules/object-storage`
  - `platform-infra/modules/mysterybox`
  - `platform-infra/modules/wireguard-jumphost`
  - `platform-infra/modules/ssh-jumphost`

Workload charts (for example `n8n`) must reference the created PVC name in
their chart values to mount it into pods.
PVC storage class is derived from the CSI chart release (`csi-mounted-fs-path-sc`)
to avoid config drift.
CSI supports two modes:

- `mode: dynamic` (default): StorageClass-based dynamic provisioning.
- `mode: static`: pre-bound `PersistentVolume` + `PersistentVolumeClaim` manifests.
  Useful when you want explicit PV lifecycle and shared underlying SFS path across
  multiple namespaces (each namespace still uses its own PVC).

## Install (CLI via pipx wheel)

Python requirement: `3.12+`.

```bash
brew install pipx
pipx ensurepath
curl -L -o nebius_cxcli-x.y.z-py3-none-any.whl \
  https://github.com/nebius/nebius-ps-services/releases/download/nebius-cxcli-vx.y.z/nebius_cxcli-x.y.z-py3-none-any.whl
pipx install ./nebius_cxcli-x.y.z-py3-none-any.whl
nebius-cxcli --help
```

Use the wheel from your GitHub Release assets and replace `x.y.z` with the
release version.

To upgrade from a newer wheel:

```bash
pipx install --force ./nebius_cxcli-x.y.z-py3-none-any.whl
```

## Release Workflow

`nebius-cxcli` uses tag-driven release publishing.

1. Prepare the release from your working branch:

```bash
./publish-release.sh --prep X.Y.Z
```

1. Open and merge the PR to `main` (do not edit `CHANGELOG.md` directly on `main`).
2. On clean, synced `main`, publish the release tag:

```bash
./publish-release.sh --publish X.Y.Z
```

`--publish` creates and pushes `nebius-cxcli-vX.Y.Z`.

1. Tag push triggers `.github/workflows/nebius-cxcli-release.yml`, which:
   - validates tag format and main-branch ancestry
   - runs lint/tests
   - builds wheel artifact(s)
   - verifies artifact version matches tag version
   - creates the GitHub release and uploads the wheel

## Happy Path Commands

```bash
# Create instance scaffold only (default mode).
# target path is the deployments root folder itself.
nebius-cxcli create /path/to/customer-repo/deployments-root \
  --client-name client-a \
  --tenant-id tenant-123 \
  --env prod \
  --cluster-name client-a-prod \
  --project-id project-456 \
  --subnet-id subnet-abc123 \
  --region-id eu-north1 \
  --email ops@example.com

# Create + local deploy (no CI workflow generation).
nebius-cxcli create /path/to/deployments-root \
  --client-name client-a \
  --tenant-id tenant-123 \
  --env prod \
  --cluster-name client-a-prod \
  --project-id project-456 \
  --subnet-id subnet-abc123 \
  --deploy

# Create + bootstrap CI workflow at git repo root.
# If GH_TOKEN/GITHUB_TOKEN and Nebius auth context are available,
# CI auth secrets are auto-bootstrapped/synced.
nebius-cxcli create /path/to/customer-repo/deployments-root \
  --client-name client-a \
  --tenant-id tenant-123 \
  --env prod \
  --cluster-name client-a-prod \
  --project-id project-456 \
  --subnet-id subnet-abc123 \
  --bootstrap-ci

# Alternative: catalog-driven non-interactive create.
# Choose infra/apps by id (repeat option or comma-separated list).
nebius-cxcli create /path/to/customer-repo/deployments-root \
  --client-name client-a \
  --tenant-id tenant-123 \
  --env prod \
  --cluster-name client-a-prod \
  --project-id project-456 \
  --subnet-id subnet-abc123 \
  --infra managed-postgresql,sfs,wireguard-jumphost \
  --app envoy-gateway,cert-manager,n8n \
  --no-interactive

# Interactive wizard mode.
# create defaults to wizard mode and prompts for:
# client identity + infra/apps catalog checkbox selection.
# subnet_id is auto-filled as `subnet-REPLACE-ME` and should be edited later.
nebius-cxcli create /path/to/customer-repo/deployments-root

# Alternative: run from anywhere with an absolute path.
nebius-cxcli create /path/to/customer-repo/another-deployments-root \
  --client-name client-a \
  --tenant-id tenant-123 \
  --env prod \
  --cluster-name client-a-prod \
  --project-id project-456 \
  --subnet-id subnet-abc123 \
  --region-id eu-north1 \
  --email ops@example.com

# Alternative: use a custom deployments folder path directly.
# In non-interactive automation/CI, set --no-interactive.
nebius-cxcli create /path/to/yet-another-deployments-root \
  --no-interactive \
  --client-name client-a \
  --tenant-id tenant-123 \
  --env prod \
  --cluster-name client-a-prod \
  --project-id project-456 \
  --subnet-id subnet-abc123
```

For CI mode, use `--bootstrap-ci`, then edit `config.yaml`, commit, and open a
PR. CI runs validation/render/plan/apply based on event type.

## Optional Local Commands (Troubleshooting / Pre-checks)

```bash
# Instance config used by commands below.
CFG="<deployments-root>/instances/<client>--<tenant>/<env>/<cluster>/config.yaml"

# Bootstrap CI auth material (service account + role grant + auth/access keys)
# and sync to GitHub Actions secrets automatically.
# Requires local Nebius auth context (NEBIUS_IAM_TOKEN or `nebius auth login`)
# and a GitHub token in GH_TOKEN or GITHUB_TOKEN.
# Default project grant is roles/editor (override with repeated --role-id).
nebius-cxcli auth bootstrap --project-id project-456

# Same bootstrap, but infer project_id from an existing instance config.
nebius-cxcli auth bootstrap --instance-config "$CFG"

# Disable GitHub sync (no secret values are printed to stdout).
nebius-cxcli auth bootstrap --project-id project-456 --no-github-sync

# Optional: overwrite existing config for this target.
nebius-cxcli create /path/to/customer-repo/deployments-root \
  --client-name client-a \
  --tenant-id tenant-123 \
  --env prod \
  --cluster-name client-a-prod \
  --project-id project-456 \
  --subnet-id subnet-abc123 \
  --force

# Optional: generate/refresh CI workflow and CI auth bootstrap behavior.
nebius-cxcli create /path/to/customer-repo/deployments-root \
  --client-name client-a \
  --tenant-id tenant-123 \
  --env prod \
  --cluster-name client-a-prod \
  --project-id project-456 \
  --subnet-id subnet-abc123 \
  --bootstrap-ci

# Note: --deploy and --bootstrap-ci are mutually exclusive.

# Validate config schema, path alignment, and catalog contracts.
nebius-cxcli validate "$CFG"

# Validate with deployment-readiness checks (reject starter placeholders).
nebius-cxcli validate --strict "$CFG"

# Render deterministic generated/infra and generated/flux artifacts.
nebius-cxcli render "$CFG"

# Render + deploy locally in one command.
# Runs strict validation, terraform apply, then applies generated Flux manifests.
nebius-cxcli render --deploy "$CFG"

# Run Terraform in generated/infra for this config.
# If provider/Object Storage auth env vars are missing, nebius-cxcli
# auto-bootstraps runtime credentials from local Nebius auth context.
# If infra.mysterybox.enabled=true, also requires every env var referenced by:
# infra.mysterybox.secrets[].entries[].value_from_env
nebius-cxcli terraform plan "$CFG"
nebius-cxcli terraform apply "$CFG"

# Bootstrap Flux if missing; otherwise reconcile existing install.
# Requires GITHUB_TOKEN.
# If apps.platform.external_secrets.mysterybox.enabled=true, nebius-cxcli
# auto-bootstraps and seeds the required auth Secret values.
# (NEBIUS_PROJECT_ID is always read from config.yaml automatically.)
# Optional: set NEBIUS_MYSTERYBOX_WEBHOOK_TOKEN to control the webhook auth
# token value; otherwise nebius-cxcli generates one automatically.
nebius-cxcli flux bootstrap "$CFG"

# Discover config list JSON: generate config.yaml files CI should process now.
# This command only prints JSON to stdout; it does not change files, state, or
# infrastructure.
# discover expects the deployments root folder path and runs in git context.
nebius-cxcli discover /path/to/customer-repo/deployments-root

# Same command from a nested target path.
nebius-cxcli discover /path/to/customer-repo/another-deployments-root

# Optional: include all configs under deployments dir (not only changed files).
nebius-cxcli discover /path/to/customer-repo/deployments-root --all

# List component catalogs used by create wizard/flags.
nebius-cxcli list-catalog
nebius-cxcli list-catalog --infra
nebius-cxcli list-catalog --apps
nebius-cxcli list-catalog --defaults

# Equivalent subcommand form:
nebius-cxcli catalog list
nebius-cxcli catalog list --infra
nebius-cxcli catalog list --apps
nebius-cxcli catalog list --defaults

# Catalog registry file resolution order:
# 1) --catalog-file
# 2) $NEBIUS_CXCLI_CATALOG_FILE
# 3) <TARGET_PATH>/catalogs/catalog.yaml (if present)
# 4) ~/.config/nebius-cxcli/catalog.yaml

# Add custom catalog entries without changing CLI code.
# Wizard mode (default): prompts for missing required fields.
nebius-cxcli catalog add

# Automation/CI mode: pass all required flags explicitly.
nebius-cxcli catalog add --no-interactive \
  --scope apps \
  --id argo-cd \
  --description "Argo CD Helm release" \
  --chart-repo "https://argoproj.github.io/argo-helm" \
  --chart-name "argo-cd"

# Infra example (Terraform module metadata):
nebius-cxcli catalog add \
  --scope infra \
  --id managed-redis \
  --description "Managed Redis module" \
  --module-source \
  "git::https://github.com/example/iac.git//modules/managed-redis" \
  --version "1.0.0"
# Optional flags:
# --default         include in default create selections
# --non-selectable  make always enabled in wizard

# Custom infra entries selected in create are rendered automatically into:
# - generated/infra/main.tf as additional Terraform module blocks.
# Optional per-module overrides can be set in config.yaml:
# catalog.infra.values.<infra-id>.module_name / inputs / depends_on_platform / version

# App example (Helm chart metadata):
nebius-cxcli catalog add \
  --scope apps \
  --id argo-cd \
  --description "Argo CD Helm release" \
  --chart-repo "https://argoproj.github.io/argo-helm" \
  --chart-name "argo-cd" \
  --version "7.6.7"

# Custom app entries selected in create are rendered automatically into:
# - generated/flux/sources/helm-repositories.yaml
# - generated/flux/apps/{platform|workloads}/<release>-helmrelease.yaml
# Optional per-app overrides can be set in config.yaml:
# catalog.apps.values.<app-id>.namespace / release_name / interval / values / create_namespace

# Generate local inventory artifacts, then upload to Object Storage.
# Writes local files under: <cluster>/generated/inventory/
# upload requires AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY.
nebius-cxcli inventory write "$CFG"
nebius-cxcli inventory upload "$CFG"

# Send inventory email.
# Requires client_info.notifications.email in config.yaml and SMTP_HOST.
# Optional SMTP vars: SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM, SMTP_STARTTLS.
nebius-cxcli email "$CFG"
```

## Development

```bash
make install
make all
```

### pre-commit (macOS)

```bash
brew install pre-commit
pre-commit install
```

## Security Notes

- Keep customer deployment repositories private.
- Never commit secrets in `config.yaml`.
- Use GitHub Actions secrets for Object Storage, Terraform/Nebius auth, and
  Flux credentials.
- Expected CI secrets:
  `NEBIUS_S3_ACCESS_KEY_ID`,
  `NEBIUS_S3_SECRET_ACCESS_KEY`,
  `NEBIUS_SA_ID`,
  `NEBIUS_AUTH_PUBLIC_KEY_ID`,
  `NEBIUS_AUTH_PRIVATE_KEY_PEM`,
  `FLUX_GITHUB_TOKEN`
  (and SMTP secrets when email notifications are enabled).
- `create --bootstrap-ci` auto-syncs these secrets and fails if GH token/repo
  context is missing.
- Use `auth bootstrap` for explicit rotation or to resync manually.
- `auth bootstrap` intentionally never prints raw secret values to stdout.
- `generated/` files are machine-generated and should not contain raw credentials.
