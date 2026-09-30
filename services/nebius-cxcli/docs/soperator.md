# Soperator lifecycle

[Back to Soperator commands](../README.md#soperator)

Use the dedicated Soperator family for fresh configuration, discovery,
onboarding, status, and release upgrades. Generic component commands do not
configure the product. Examples assume the intended Nebius project and an
installed CLI; set `CONFIG` to its config path and replace uppercase identifiers.

## Create and deploy

```bash
nebius-cxcli soperator create ./deployments
```

The wizard selects project/network, CPU/GPU/mixed profile, worker shape/count,
storage, root SSH keys, release, and deployment mode. Noninteractive creation
can supply identity and choices directly:

```bash
nebius-cxcli soperator create ./deployments \
  --client-name example --tenant-id TENANT_ID --project-id PROJECT_ID \
  --profile gpu --release latest --no-interactive
```

`--release` accepts `latest` or an exact official stable `X.Y.Z`. Admission
freezes the tag, commit, tree, source archive, selected charts/images, and
interfaces. There is no local product chart, private fork, or offline release
substitute. Source discovery and installation policy remain separate.

Standard mode performs the applicable upstream checks. Explicit
`--fast-deploy` selects the fast-dev-test profile with reduced GPU diagnostic
coverage; it is not production qualification. The interactive wizard always asks
for a deployment mode. Headless creation honors the explicit flag, then values
file, then standard. Hardware alone does not select reduced checks.

Creation writes configuration; deploy through the shared workflow:

```bash
nebius-cxcli validate "$CONFIG"
nebius-cxcli render "$CONFIG"
nebius-cxcli validate-generated "$(dirname "$CONFIG")/generated"
nebius-cxcli deploy "$CONFIG" --dry-run
```

Inspect the preview before running deploy without `--dry-run`. Execution uses
the rendered generation and verifies Kubernetes/Slurm product readiness;
Helm readiness alone is not completion. `--acceptance readiness` finishes after
mandatory readiness; `--acceptance full` also runs optional full acceptance.
When omitted in a terminal, cxcli asks after readiness; unattended omission
selects full. During optional checks, Ctrl+G requests a safe finish without
skipping mandatory readiness or interrupting an in-flight check.

### SSH identity and values files

Fresh login configuration chooses root public keys, preferring existing
Ed25519, ECDSA, then RSA public key files. Headless creation fails without an
available key unless values explicitly supply a list, including `[]` to disable
root keys. `slurmNodes.login.sshRootPublicKeys` configures root, not named-user
access. Saved keys remain inline through render, recovery, and upgrade.

Named upstream users such as `nebius` use `/opt/soperator-home/USERNAME`;
`/home/ubuntu` belongs to the separate Ubuntu account. Home storage persists,
while account definitions under `/etc` remain target-image/upstream-owned.

`--values-file ./soperator-values.yaml` accepts one values-only UTF-8 YAML mapping
in a regular file below 1 MiB, without a `values:` wrapper or infrastructure
configuration. Interactive answers override its seeds. Explicit false values,
lists, and default-equal selections remain explicit; list intent is atomic.
Release/image/storage/generated-topology overrides, inline credentials, and
unsupported umbrella/dependency overrides fail admission. Required checks stay
enabled. Recovery uses saved configuration, not another values-file import.

## Discover and onboard

Discovery inspects raw cloud scope without requiring a config:

```bash
nebius-cxcli soperator discover ./support-bundles \
  --tenant-id TENANT_ID --project-id PROJECT_ID --cluster-id CLUSTER_ID
```

It produces a concise support-safe Markdown summary and complete schema-v2
`report.json`. The terminal summary groups node counts and never prints
individual nodes; detailed normalized inventory stays in JSON. Incomplete
collectors preserve a partial bundle and return nonzero. Inspect bundles for
sensitive information before sharing.

Onboarding registers an existing official installation, either in an existing
project or by creating a config beneath a deployments root:

```bash
nebius-cxcli soperator onboard "$CONFIG"
nebius-cxcli soperator onboard ./deployments \
  --client-name example --tenant-id TENANT_ID --project-id PROJECT_ID \
  --region-id eu-north1 --cluster-id CLUSTER_ID \
  --target-id existing-cluster --no-interactive
```

`--cluster-id` is the cloud identity; `--target-id` is the optional logical
identity saved in config. Onboarding verifies one installed official release
and records Nebius/Kubernetes/source identities. Missing, ambiguous, incomplete,
or unofficial installations do not produce an adopted target. It does not
choose upgrades, storage migration, or hardware migration. Core operations use
Nebius and Kubernetes APIs, not workstation SSH to login/worker nodes.

## Inspect status

```bash
nebius-cxcli soperator status "$CONFIG" --target TARGET_ID
nebius-cxcli soperator status "$CONFIG" --target TARGET_ID --no-live
nebius-cxcli soperator status "$CONFIG" --target TARGET_ID --show-checks
```

Live status is the default. It combines recorded operation state with current
cluster observations, showing pending/failed phases and acceptance history.
`--no-live` reports recorded state; it cannot establish current health.
`--show-checks` expands stored check detail and does not submit new workload tests.

Optional observability verification uses fresh bounded reads:

```bash
nebius-cxcli soperator status "$CONFIG" --target TARGET_ID --verify-observability
```

It requires live mode. Metrics/logs verification is separate from upgrade
completion and scheduling restoration. Status identifies the deployment
recovery entry point, but cannot reconstruct all original options. A dedicated
node-group migration has separate evidence and is not shown as a Soperator
upgrade.

## Upgrade

A campaign composes official release, sequential Kubernetes minor hops, node
OS/Nebius GPU drivers, release-defined Jail CUDA, Slurm maintenance, and final
runtime validation. Select targets explicitly for an unattended preview:

```bash
nebius-cxcli soperator upgrade "$CONFIG" --target TARGET_ID \
  --to-release latest --to-k8s-version latest \
  --to-os auto --to-gpu-stack-preset auto --no-interactive --dry-run
```

Review the plan and rerun without `--dry-run` to execute. The provider determines
the highest reachable supported Kubernetes endpoint; the campaign freezes each
required intermediate hop. Same-release does not necessarily mean no changes:
changed desired settings, protected paths, check policy, or infrastructure can
require reconciliation or fresh acceptance.

Managed clusters use Terraform for infrastructure stages. Onboarded campaigns
use their admitted provider-API authority. The official frozen release supplies
upstream workloads and a thin cxcli adapter supplies Nebius integration.
Execution stops rather than guessing at ambiguous ownership or unsupported
release interfaces.

Managed Soperator capacity/topology changes use edited config, render, and
deploy. Hardware replacements can retire and recreate changed groups under the
coordinated campaign. Use `migrate node-group` for a dedicated explicitly named
replacement-group migration with its own preview, approval, and recovery.
Managed topology reconciliation is not supported for onboarded targets.

**Deploy has a different job default:** it asks interactively in a prompt-capable
terminal; unattended deployment defaults to `wait-then-cancel` with a `1h` wait.
Review and explicitly choose the applicable job controls before automated
capacity/topology changes. Do not assume upgrade's no-cancellation default applies.

### Jobs and rollout controls

The upgrade pauses all observed active partitions and retains a scheduling
barrier across its stages. Default guarded job handling is `requeue-hold-all`:
eligible active batch jobs are requeued and held; jobs that cannot safely be
requeued are reported and waited for. Cancellation is never implicit. Review current `--job-policy`, `--cancel-job`, and
`--requeue-job` options when different handling is required. Explicit selection
policies require matching job IDs and reject incompatible combinations.

`--job-wait-timeout` defaults to `0s` for upgrade and
`--job-refresh-interval` to `30s`. Zero wait timeout means no wait deadline.
The campaign restores exact partition state and only its owned holds,
reservations, and drains after final readiness. Failure can leave maintenance
in place for recovery.

Node rollout uses `--node-group-strategy`; per-group software selectors include
`--node-group-os` and `--node-group-gpu-stack-preset`. Safe surge requires spare
capacity and accepts a positive `--strategy-max-surge-count`. See the
[MK8s strategy table](mk8s.md#rollout-strategies) for drain behavior; use the
Soperator command's own flag names.

## Protected paths and storage

Jail upgrades are target-wins. `/home`, `/data`, `/scripts`, `/models`, and
`/opt/soperator-home` are mandatory retained paths outside versioned rootfs
slots. Other content is replaced by the target image: unselected local packages,
files, and changes under `/usr`, `/opt`, or `/etc` are not preserved.

Interactive upgrades offer additional existing absolute directory paths. Paths
must be real directories, with no symlink traversal or overlapping mounts.
Selecting a directory binds its existing storage without copying/moving data;
files are not accepted as protected directories. No or an empty selection keeps
existing protections. Dry-run does not persist new selections; unattended
execution and recovery do not prompt.

An optional protected directory can reside in a physical rootfs generation.
That generation is then retained. Reusing its logical slot allocates fresh
backing and PV/PVC identities on the same filesystem; retained generations are
not automatically reclaimed. Preview exposes the extra storage requirement.
Existing protected backing cannot be removed or redirected during upgrade.

The product verifies immutable physical SFS or optional VM-NFS storage,
path-specific PV/PVC bindings, and consumers. Cluster, controller/accounting,
and SSH identities remain protected. SFS `forbid_deletion` does not block an
upgrade or storage-preserving destroy; it blocks `destroy --delete-sfs`.

![Target-wins rootfs with mandatory shared paths and retained optional generations](jail-rootfs-active-passive-storage.svg)

## Recover an interrupted campaign

Recovery is forward-only through `deploy` using the original frozen generated
bundle and execution controls. Do not repeat `soperator upgrade`, rerender a
similar config, edit a receipt, or delete protected storage to bypass a gate.
Status identifies the pending phase but does not supply every original option.

Preserve target selection, skips, and job controls. In particular, upgrade's
`0s` job wait default differs from deploy's `1h`; explicitly pass the original
value and any other original controls. Follow the reported prerequisite repair,
then let deployment finish its pending transition and verify final readiness.
See [deployment recovery](project-workflows.md#interrupted-operations).

![Full-stack Soperator upgrade campaign and deployment recovery](soperator-protected-upgrade-workflow.svg)

## Backup and directory integration

These optional values belong to the creation values file. Example references
contain no credentials; replace the bucket and integration names with your
chosen resources:

```yaml
soperator-backup-config:
  enabled: true
  bucket:
    name: existing-backup-bucket
    endpoint: https://storage.example.invalid
  backup:
    schedule: "0 1 * * *"
  prune:
    schedule: "0 2 * * *"
    retention:
      keepDaily: 7
  secret:
    name: jail-backup
    keys:
      accessKeyID: aws-access-key-id
      secretAccessKey: aws-access-secret-key
      backupPassword: backup-password
sssd:
  enabled: true
  sssdConfSecretRefName: soperator-sssd-config
  sssdLdapCAConfigMapRefName: directory-ca
```

cxcli does not create the backup bucket. Schedule/retention defaults come from
the selected frozen chart; daily retention must be positive. Supply credentials
through protected runtime environment variables or hidden execution prompts:

- `NEBIUS_CXCLI_SOPERATOR_BACKUP_AWS_ACCESS_KEY_ID`
- `NEBIUS_CXCLI_SOPERATOR_BACKUP_AWS_SECRET_ACCESS_KEY`
- `NEBIUS_CXCLI_SOPERATOR_BACKUP_REPOSITORY_PASSWORD`
- `NEBIUS_CXCLI_SOPERATOR_SSSD_CONFIG_FILE` for a local `sssd.conf`
- `NEBIUS_CXCLI_SOPERATOR_SSSD_LDAP_CA_FILE` for a PEM CA bundle when configured

For target `cluster1`, append `_CLUSTER1` to every variable name. Targeted
execution does not fall back to unscoped variables. Files must be nonempty UTF-8
regular files below 1 MiB. Runtime delivery uses Secret key `sssd.conf` and
optional ConfigMap key `ca.crt`; configure the CA path as `/mnt/ldapCA/ca.crt`.

Complete existing runtime objects are reused; incomplete objects need explicit
repair. Input paths and contents do not enter config, generated manifests, or
operation receipts. Dry-run does not request credentials/files or write those
objects. Rotation, workload restarts, backup/restore, and live directory-login
verification remain separate actions.

## Grafana and Nsight

Use [Grafana/telemetry installation](observability.md) for routing and access,
and [dashboard management](grafana-dashboards.md) for imports/exports.
Ordinary apps on an accepted Soperator target can use the guarded Flux workflow
in [project operations](project-workflows.md#terraform-and-flux-operations).

```bash
nebius-cxcli soperator profiling show "$CONFIG" --target TARGET_ID
nebius-cxcli soperator profiling recover "$CONFIG" --target TARGET_ID \
  --stage install --job-uid FAILED_JOB_UID --dry-run
```

Profiling recovery binds an exact failed job and stage. Review the preview and
supported repair before execution; do not remove arbitrary Jobs or workload
state. The [Nsight guide](nsight-profiling.md) owns installation, credential,
worker-tool, access, and recovery details.
