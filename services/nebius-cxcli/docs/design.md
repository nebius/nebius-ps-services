# nebius-cxcli Design

## 1. Goal

Provide a repeatable way for a Nebius customer to provision multiple isolated
platforms, each including:

- Out-of-cluster Nebius resources via Terraform.
- In-cluster components via GitOps (Flux + Helm).
- Operator workflow driven by one canonical `config.yaml` file per instance.

The delivery model has three supported operation modes:

- Scaffold-only mode (`create` default): author with `nebius-cxcli`, no CI
  workflow generation, no deployment.
- Local deployment mode (`create --deploy`): scaffold + local deployment
  execution.
- CI automation mode (`create --bootstrap-ci`): scaffold + workflow generation,
  then CI plan/apply on PR/merge.

Recommended CI operator workflow:

1. Clone customer private repo locally and create a branch.
2. Create an empty deployments root folder in that private repo (any folder name).
3. Run `nebius-cxcli create <deployments-root-path> --bootstrap-ci`.
4. Edit the generated instance `config.yaml`.
5. Commit and open a PR.
6. CI processes changed configs:
   - PR: `validate --strict -> render -> terraform plan`
   - Merge to `main`: `validate --strict -> render -> terraform apply`
     (then Flux bootstrap/reconcile and inventory/email steps).

Local runs of `validate`, `validate --strict`, `render`, `terraform`, `flux`,
and inventory/email commands are optional and intended mainly for
troubleshooting or pre-flight checks.

CI automation prerequisites:

- Nebius auth context available locally (token/profile).
- GitHub token available as `GH_TOKEN` or `GITHUB_TOKEN` with permission to
  write repository Actions secrets.
- Repository slug resolvable from git `origin`, or passed via `--github-repo`.

Design goals:

- Keep `config.yaml` as the canonical contract.
- Keep `nebius-cxcli` as the authoring, config generator and validation tool.
- Keep CI as the executor (`plan` on PR, `apply` on merge).
- Keep generated output deterministic under `generated/`.
- Keep schema structure UI-ready for future API/UI usage.

Config contract internals:

- `src/nebius_cxcli/schema.py`: strict Pydantic schema (`extra="forbid"`) for `config.yaml`.
- `src/nebius_cxcli/config_loader.py`: YAML load + validation + consistent
  error formatting.
- `src/nebius_cxcli/config_template.py`: starter `config.yaml` generator used
  by `create`.

## 2. Naming and Contract

- Product/package name: `nebius-cxcli`.
- CLI binary: `nebius-cxcli`.
- Canonical input contract: `config.yaml`.
- Generated artifacts: everything under `generated/`.
- `generated/` is machine-owned and deterministic.

## 3. Repository Model

### 3.1 Public repository (open source)

Public code and reusable assets live in `nebius/nebius-ps-services`.

Intended library structure:

```text
nebius-ps-services/
  platform-infra/    # Terraform modules/stacks (library)
  mk8s-apps/         # Flux/Helm defaults/templates (library)
  services/
    nebius-cxcli/    # this CLI
```

Note: `platform-infra` and `mk8s-apps` are treated as version-pinned library
paths for rendering. They should remain configurable in case repository layout
changes across releases.

Current `platform-infra` layout used by `nebius-cxcli`:

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

### 3.2 Customer repository (private)

Customer deployments must live in a private repository.

Recommended layout:

```text
<customer-private-repo>/
  <deployments-root>/
    instances/
      <client_name>--<tenant_id>/
        <env>/
          <cluster_name>/
            config.yaml
            generated/
              infra/
              flux/
              inventory/
                inventory.md
  .github/                    # only when using create --bootstrap-ci
    workflows/
      nebius-deployments.yml
```

`<deployments-root>` is user-defined. Operators create this empty folder first
in the private repo, then pass it to `create`/`discover`.
The deployments root path only needs to be inside a cloned git repository for
CI features (`create --bootstrap-ci`, `discover`, and GitHub secret sync).

## 4. Why `<cluster_name>` is required in path

Path-level cluster separation avoids collisions when a customer has multiple
clusters in the same environment (`prod`, `stage`, `dev`).

## 5. Canonical `config.yaml`

`config.yaml` is the only human-edited input file per instance.

Rules:

- Schema is strict and versioned (`version: v1`).
- Unknown keys are rejected.
- `validate --strict` enforces deployment-readiness checks (for example, rejects
  starter placeholders such as `subnet-REPLACE-ME`, default SSH key placeholder,
  and default `example.internal` hostname for enabled n8n).
- Validation has two layers by design:
  - `validate`: contract correctness (schema + path alignment).
  - `validate --strict`: operational readiness checks before render/apply.
- Starter output from `create` is expected to be contract-valid first, and may
  include placeholders that must be replaced before strict validation passes.
- Path and payload must align:
  - `client_info.client_name == <client_name from path>`
  - `client_info.nebius.tenant_id == <tenant_id from path>`
  - `client_info.env == <env from path>`
  - `client_info.cluster_name == <cluster_name from path>`
- App values are deep-merged into HelmRelease `spec.values`.
- Each enabled app declares an explicit namespace.
- `infra.sfs.csi` controls GitOps generation of CSI driver + PVC manifests.
- `infra.object_storage.state_bucket` contract:
  - `manage` controls whether platform Terraform creates/manages the bucket
    resource.
  - `encryption: true` (renders Terraform S3 backend `encrypt = true`).
  - `versioning_policy: ENABLED`.
  - `object_audit_logging: ALL`.
  - `protect_from_destroy: true`.
- `infra.object_storage.inventory_bucket` contract:
  - `manage: true` by default.
  - `versioning_policy: DISABLED`.
  - `object_audit_logging: NONE`.
  - `protect_from_destroy: false`.
- `infra.mysterybox` contract:
  - Terraform-owned secret manager integration for both platform and app scopes.
  - `enabled` toggles MysteryBox management in platform stack.
  - `secrets[]` defines metadata, payload keys, and source env-var names
    (`entries[].value_from_env`).
  - `secrets[].k8s_sync` defines optional in-cluster sync targets for app
    secrets (namespace, target secret name, refresh policy).
  - `config.yaml` intentionally does not carry raw payload values.
  - During `terraform plan/apply`, CLI resolves env vars and injects runtime
    `TF_VAR_mysterybox_secret_values`.
  - Platform stack uses provider write-only fields for payload values so raw
    values are not persisted in Terraform state.
  - GitOps sync path:
    - `apps.platform.external_secrets` installs ESO by Flux.
    - `apps.platform.external_secrets.mysterybox` renders a webhook
      `ClusterSecretStore` and MysteryBox bridge deployment/service.
    - Bridge implementation is maintained as a standalone service under
      `services/mysterybox-bridge` (webhook code + Dockerfile + Helm charts),
      so it can be reused without `nebius-cxcli`.
    - Bridge runtime auth Secret is seeded out-of-band from env vars at
      deploy/bootstrap time (`NEBIUS_SA_ID`, `NEBIUS_AUTH_PUBLIC_KEY_ID`,
      `NEBIUS_AUTH_PRIVATE_KEY_PEM`, `NEBIUS_PROJECT_ID`).
    - ESO->bridge request auth is enforced via webhook headers + webhook secret
      templating (`external-secrets.io/type=webhook`) generated/seeded by CLI.
    - Flux then reconciles `ExternalSecret` resources into native Kubernetes
      Secrets per `secrets[].k8s_sync` contract.
- Additional out-of-cluster mappings:
  - `infra.managed_postgresql.postgresql_version` maps to
    `managed_postgresql_postgresql_version`.
  - `infra.managed_postgresql.public_access` maps to
    `managed_postgresql_public_access`.
  - `infra.sfs.type` maps to `sfs_type`.
- Platform stack maps state/inventory settings into a generic object-storage
  module input (`buckets` map), so bucket behavior is fully input-driven and not
  hardcoded by resource purpose.
- Note: state bucket must exist before backend init in standard remote-backend
  runs; if creating it via Terraform, use a bootstrap flow.
- `create` keeps MK8s config minimal; advanced MK8s provider-style fields are
  optional and only rendered when explicitly added:
  - `infra.mk8s.cluster_overrides`
  - `infra.mk8s.cpu_node_group_overrides`
  - `infra.mk8s.gpu_node_group_overrides`
  - Typical cluster overrides: `control_plane.subnet_id`, `control_plane.version`,
    `control_plane.etcd_cluster_size`, `control_plane.audit_logs`,
    `control_plane.endpoints.public_endpoint`, `kube_network.service_cidrs`.
  - Typical node-group overrides: `autoscaling`, `fixed_node_count`, `strategy`,
    and `template.*` (`resources`, `boot_disk`, `cloud_init_user_data`,
    `filesystems`, `gpu_settings`, `network_interfaces`, `reservation_policy`,
    `service_account_id`, `taints`).
  - Rendering emits advanced keys as `k8s_version`, `etcd_cluster_size`,
    `subnet_id`, `mk8s_cluster_overrides`,
    `mk8s_cpu_node_group_overrides`, `mk8s_gpu_node_group_overrides`.
    The platform-infra Terraform stack must expose matching variables.
  - MIG remains Terraform-owned:
    - `infra.mk8s.gpu_nodes.mig.*` renders to `mig_strategy` / `mig_parted_config`.
  - Egress-gateway in-cluster wiring is Flux-owned:
    - `infra.mk8s.egress_gateway.enabled` renders Cilium manifests under `generated/flux`.
    - Generated tfvars omit `enable_egress_gateway` to avoid split ownership
      of in-cluster Cilium objects.
    - Cloud-side prerequisites (for example egress nodegroup/subnet) remain in
      Terraform stack scope.
  - Observability app values are Flux-owned:
    - `apps.platform.observability.values` render to Flux HelmRelease values.
    - Generated tfvars omit observability app toggles to keep Terraform focused
      on out-of-cluster infrastructure.
  - WireGuard jump host is Terraform-owned in the central stack:
    - `infra.wireguard-jumphost.*` renders as `wireguard_*` tfvars keys.
    - `infra.wireguard-jumphost.tunnel_cidr` controls the server interface
      CIDR (default `10.8.0.1/24`).
    - `infra.wireguard-jumphost.listen_port` controls WireGuard UDP port
      (default `51820`).
    - `infra.wireguard-jumphost.nat_mode` enables NAT/masquerade mode
      (default `true`, recommended for point-to-site).
    - `infra.wireguard-jumphost.clients` enables automated peer creation and
      generated client configs on the server (including per-client PSK).
    - `generated/infra/main.tf` contains one
      `module "customer_platform"` that receives all rendered tfvars keys.
    - Default behavior creates and attaches a dedicated static public IP
      allocation; existing allocation reuse is supported via
      `public_ip_allocation_id`.
  - SSH jump host is also Terraform-owned in the central stack:
    - `infra.ssh-jumphost.*` renders as `ssh_jumphost_*` tfvars keys.
    - Uses shared `infra.ssh_user_name` / `infra.ssh_public_key` plus
      SSH-specific hardening/ingress controls (`allowed_cidrs`).
    - Uses strict bootstrap policy for ingress: when enabled, an empty
      `allowed_cidrs` list is rejected (no automatic open-to-world SSH rule).
- Implementation split follows a config-contract pattern:
  - `schema.py` for strict versioned models.
  - `config_loader.py` for file loading and validation errors.
  - `config_template.py` for canonical starter config generation.
  - `schema_catalog.py` for schema introspection used by
    `nebius-cxcli list <schema_path>`.

Schema introspection command:

- `nebius-cxcli list infra.mk8s`
- `nebius-cxcli list infra.mk8s --all`
- `nebius-cxcli list infra.mk8s --required`
- `nebius-cxcli list infra.mk8s --optional`

This command is read-only and prints field path, required/optional status,
type, and default value.
For filtered views:

- `--required` prints required leaf fields, and required object-level fields
  only when requirement is modeled at the object level.
- `--optional` prints optional leaf fields.

Template customization boundary:

- Per-instance customization is done by editing the generated `config.yaml`.
- Schema/strict validation defines the safe boundary for those edits.
- Changing global starter defaults currently requires code changes in
  `config_template.py` and releasing a new CLI version.

SSH public key contract:

- `infra.ssh_public_key` is required and must contain inline public key
  content.
- `infra.ssh_user_name` defines the shared SSH username used by MK8s node
  bootstrap and jump-host modules.
- Path-based key references are intentionally not supported to avoid local/CI
  file path drift.

### 5.1 Bootstrap workflow (`create` only)

`create` is the single bootstrap entrypoint and supports:

1. Scaffold-only mode (default): first/additional instance scaffolding under an
   existing deployments root path.
2. Local deployment mode (`--deploy`): scaffold + local deployment execution
   with automatic runtime auth bootstrap (service account/auth key/S3 key) when
   required auth env vars are missing.
3. CI bootstrap mode (`--bootstrap-ci`): scaffold + workflow generation
   (`.github/workflows/nebius-deployments.yml`).

Operator precondition:

- Create an empty deployments root folder in the private repo (any name), then
  pass that path to `create`.

Input styles:

1. Non-interactive flags (`--client-name`, `--tenant-id`, `--env`,
   `--cluster-name`, `--project-id`, `--subnet-id`).
2. Interactive prompt mode (`--interactive`) for missing values.

`--subnet-id` maps to MK8s control plane subnet and is required because
`control_plane.subnet_id` is required by `nebius_mk8s_v1_cluster`.
In interactive mode, when `--subnet-id` is not provided, `create` writes a
placeholder (`subnet-REPLACE-ME`) so operators can fill the real subnet in
`config.yaml` before render/apply.

Instance scaffolding builds:

- `instances/<client>--<tenant>/<env>/<cluster>/config.yaml`
- `generated/infra`, `generated/flux`, and `generated/inventory` skeleton directories
- optional `generated/inventory/inventory.md`

The command is idempotent for directories/workflow and can be re-run safely.
`--deploy` and `--bootstrap-ci` are intentionally mutually exclusive.

Force-refresh helper:

- `create --force --keep-client-info --config-file <path/to/config.yaml>` reuses
  missing values from an existing
  instance `config.yaml` (`client_info` identity values, nebius IDs, region,
  notifications email) while rewriting the config from the current starter
  template. It intentionally does not keep `subnet_id`; pass `--subnet-id`
  explicitly when needed.
- `--config-file` must point to one explicit `instances/.../config.yaml`.
  If `TARGET_PATH` is omitted, `create` infers deployments root from that path.

## 6. Generated Artifacts

Everything below is generated from `config.yaml`.

### 6.1 Terraform (`generated/infra/`)

- `terraform.tf`
  - Nebius provider source + version constraint.
  - S3 backend pointing to Nebius Object Storage endpoint.
  - `use_lockfile` and `encrypt` support.
  - No embedded credentials.
- `main.tf`
  - Version-pinned module source for platform-infra stack.
  - One `module "customer_platform"` block.
  - Reads `terraform.auto.tfvars.json` and maps all rendered keys into module inputs.
- `terraform.auto.tfvars.json`
  - Rendered machine inputs mapped from the schema.
  - Includes identity/platform keys (`tenant_id`, `parent_id`, `region`) and
    component keys (MK8s, PostgreSQL, SFS, Object Storage, MysteryBox, WireGuard).

### 6.2 Flux (`generated/flux/`)

- `kustomization.yaml` (cluster root for reconciliation).
- `sources/helm-repositories.yaml`.
- `apps/platform/*-helmrelease.yaml`.
- `apps/workloads/*-helmrelease.yaml`.
- Optional `apps/workloads/n8n-httproute.yaml`.
- Optional ESO/MysteryBox sync artifacts when enabled:
  - `apps/platform/external-secrets-helmrelease.yaml`
  - `apps/platform/mysterybox-bridge-{deployment,service}.yaml`
  - `apps/platform/mysterybox-clustersecretstore.yaml`
  - `apps/workloads/externalsecret-<namespace>-<secret-id>.yaml`
- When `infra.mk8s.egress_gateway.enabled=true`, also generates:
  - `apps/platform/cilium-config-egress-gateway.yaml`
  - `apps/platform/cilium-daemonset-restart-egress-gateway.yaml`
  - `apps/platform/cilium-operator-restart-egress-gateway.yaml`
  - `apps/platform/cilium-egress-nodes-network-policy.yaml`
- When `infra.sfs.csi.enabled=true`, also generates:
  - `apps/platform/namespace-<csi-namespace>.yaml` (optional via `create_namespace`)
  - `apps/platform/csi-mounted-fs-path-helmrelease.yaml`
  - `apps/workloads/namespace-<pvc-namespace>.yaml` (optional per PVC via `create_namespace`)
  - `apps/workloads/pvc-<namespace>-<name>.yaml` for each entry in `infra.sfs.csi.pvcs`
  - In `mode=static`, also `apps/workloads/pv-<name>.yaml` per PVC for
    explicit pre-binding.
  - source entry for OCI chart
    `oci://cr.eu-north1.nebius.cloud/mk8s/helm/csi-mounted-fs-path`
  - PVC storage class is derived from chart release
    (`csi-mounted-fs-path-sc`) to avoid drift.

CSI mode semantics:

- `dynamic` (default): provision PVCs through CSI StorageClass.
- `static`: render explicit PV+PVC bindings for controlled lifecycle and path mapping.
  Static mode supports shared underlying SFS path usage across namespaces by creating
  one PV+PVC pair per namespace/workload.

This aligns the in-cluster storage part of the workflow with GitOps:
CSI driver install and PVC are reconciled by Flux.
Workload Helm values still need to reference that PVC to mount it in pods.

## 7. Flux bootstrap and day-2 idempotency

`nebius-cxcli flux bootstrap <config.yaml>` behaves as:

1. If Flux is absent, bootstrap with
   `flux bootstrap github ... --path <generated/flux relative path>`.
2. If Flux is present, run reconcile commands.
3. Always converge desired state from Git.

When ESO MysteryBox sync is enabled, `flux bootstrap` also seeds/updates the
bridge auth Secrets from runtime env vars before bootstrap/reconcile so webhook
sync can authenticate to Nebius API and ESO webhook calls are authenticated.

This removes operator branching between "bootstrap" and "reconcile".

## 8. Inventory model

Per instance:

- Local optional markdown summary: `<cluster>/generated/inventory/inventory.md`.
- Local JSON artifacts: `<cluster>/generated/inventory/*.json`.
- Object Storage uploads under:

```text
inventory/<client_name>--<tenant_id>/<env>/<cluster_name>/
  infra.json
  mk8s.json
  postgresql.json
  sfs.json
  apps.json
  inventory.md
```

Terraform state key pattern:

```text
tfstate/<client_name>--<tenant_id>/<env>/<cluster_name>/platform.tfstate
```

## 9. GitHub Actions model (customer repo)

`nebius-cxcli create --bootstrap-ci` scaffolds:

- `<deployments-root>/instances/` under the path passed to `create`.
- `.github/workflows/nebius-deployments.yml` at the detected git repo root.
- Optional first instance hierarchy and starter `config.yaml` when create
  options are provided (flags or `--interactive`).

If `create` is executed from a nested subdirectory, the generated
workflow uses the repo-relative deployments path so CI discovery still works
correctly.

Workflow model (CI mode):

- PR: `validate --strict -> render -> terraform plan`.
- Push to `main`: `validate --strict -> render -> terraform apply ->
  flux bootstrap -> inventory/email`.
- If `infra.mysterybox.enabled=true`, plan/apply jobs must expose each
  environment variable referenced by
  `infra.mysterybox.secrets[].entries[].value_from_env`.
- `create --bootstrap-ci` performs CI auth bootstrap + GitHub secret sync
  automatically and fails fast when GitHub token/repo context is unavailable.
- Terraform auth uses provider `service_account` environment references:
  `NEBIUS_SA_ID`, `NEBIUS_AUTH_PUBLIC_KEY_ID`,
  `NEBIUS_AUTH_PRIVATE_KEY_FILE` (derived in CI from
  `NEBIUS_AUTH_PRIVATE_KEY_PEM` secret).
- For ESO MysteryBox sync, flux bootstrap step uses:
  `NEBIUS_SA_ID`, `NEBIUS_AUTH_PUBLIC_KEY_ID`,
  `NEBIUS_AUTH_PRIVATE_KEY_PEM` (project ID is read from config).
- Customer workflow installs `nebius-cxcli` from
  `nebius/nebius-ps-services` git source (not PyPI), controlled by
  `NEBIUS_CXCLI_REF`.
- Reference policy for generated scaffolding:
  - stable CLI release (`x.y.z`): `NEBIUS_CXCLI_REF=nebius-cxcli-vx.y.z`.
  - development builds: `NEBIUS_CXCLI_REF=main`.
- Generated Terraform source for
  `platform-infra/stacks/customer-platform` follows the same ref policy.
- For Flux bootstrap, CI:
  1. installs Flux CLI and Nebius CLI.
  2. creates a Nebius service-account profile.
  3. reads `mk8s_cluster_id` from `terraform output`.
  4. runs `nebius mk8s cluster get-credentials --external`.

## 10. Security and secrets

Mandatory controls:

- Keep customer deployment repository private.
- Keep credentials only in secret stores (for example GitHub Secrets).
- Never commit access keys, SMTP passwords, or Flux tokens.
- Treat tenant/project IDs, hostnames, and topology metadata as sensitive
  operational data.

Expected secret names:

- `NEBIUS_S3_ACCESS_KEY_ID`
- `NEBIUS_S3_SECRET_ACCESS_KEY`
- `NEBIUS_SA_ID` (Nebius service-account ID for kubeconfig fetch)
- `NEBIUS_AUTH_PUBLIC_KEY_ID` (Nebius authorized key ID)
- `NEBIUS_AUTH_PRIVATE_KEY_PEM` (private key content, PEM)
- `FLUX_GITHUB_TOKEN`
- SMTP settings (`SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`, etc.) when
  email notifications are enabled.

Secrets are auto-synced during `create --bootstrap-ci` (required; command fails
when GitHub token context is missing).
Use `nebius-cxcli auth bootstrap` for explicit rotation/resync flows.
`auth bootstrap` intentionally never prints raw secret values to stdout.

## 11. Extensibility model

### 11.1 Infra components

Add a new component by:

1. Adding module support in platform-infra library.
2. Extending schema in `nebius-cxcli`.
3. Extending Terraform renderer mapping.

### 11.2 In-cluster apps

Add a new app by:

1. Adding chart/template defaults in mk8s-apps library.
2. Extending schema and render mapping.
3. Adding HelmRepository/HelmRelease generation and optional Gateway API resources.

## 12. Vendor-aligned implementation notes

- S3 backend behavior (`endpoints`, `use_lockfile`, credential source) is
  implemented in line with Terraform backend guidance.
- Backend configuration stays static in `.tf` files (Terraform backend block
  limitations).
- MK8s schema follows `nebius_mk8s_v1_cluster` and `nebius_mk8s_v1_node_group`
  provider field names for the supported write-path subset:
  - Cluster required input: control plane subnet (`infra.mk8s.subnet_id`).
  - Cluster optional overrides: `control_plane.audit_logs`,
    `control_plane.endpoints.public_endpoint`,
    `control_plane.etcd_cluster_size`, `control_plane.version`,
    `kube_network.service_cidrs`, plus `name/labels/parent_id/resource_version`.
  - Node group optional overrides include `autoscaling`, `fixed_node_count`,
    `auto_repair`, `strategy`, and `template.*`.
  - Nested required validation is enforced where those nested objects are used
    (for example `template.resources.platform`,
    `template.gpu_settings.drivers_preset`,
    `template.filesystems.attach_mode`, `template.filesystems.mount_tag`).
- `nebius-cxcli list <schema_path>` is the operator-facing source of truth for
  current required vs optional fields (`--required` / `--optional` filters).
- Flux bootstrap/reconcile behavior follows Flux CLI docs for GitHub bootstrap
  and kustomization reconciliation.
- WireGuard jump host implementation follows Nebius WireGuard tutorial flow and
  provider resources:
  - docs:
    [Nebius WireGuard docs](https://docs.nebius.com/compute/virtual-machines/wireguard)
    and [WireGuard conceptual overview](https://www.wireguard.com/#conceptual-overview)
  - resources: `nebius_compute_v1_disk`, `nebius_compute_v1_instance`,
    optional `nebius_vpc_v1_allocation` for dedicated static public IP.
  - rendered `wg0.conf` keeps `SaveConfig=false` so runtime peer edits do not
    overwrite Terraform-managed interface configuration.
- SSH jump host implementation is provided as a separate Terraform module
  (`platform-infra/modules/ssh-jumphost`) with cloud-init hardening:
  key-only SSH auth, root login disabled, UFW default deny, and source CIDR
  allowlisting for inbound SSH.
- ESO MysteryBox sync uses webhook provider + in-cluster bridge because ESO has
  no native Nebius provider:
  - Bridge resolves secret name -> ID via `SecretService.GetByName`.
  - Bridge reads values via `PayloadService.GetByKey`.
  - Both calls are executed through Nebius Python SDK service-account auth.
  - Bridge image default is public `quay.io/nebius/mysterybox-bridge:latest`
    (override/pin per environment policy).
- Nebius-specific volatile values (for example GPU fabric matrix, chart
  versions) stay explicit in `config.yaml` and must be validated by operators
  against current Nebius docs at execution time.
