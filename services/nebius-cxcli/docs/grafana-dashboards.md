# Grafana dashboards

`nebius-cxcli grafana` has three commands: `import`, `export`, and `validate`.
They use the stable `dashboard.grafana.app/v1` API, qualified with Grafana 13.2.2.
Older servers without that API fail with a capability error.

## Install local dashboards in your configured cluster

```bash
# One dashboard; saves project intent and installs it immediately.
nebius-cxcli grafana import ./gpu.json --config ./config.yaml --target CLUSTER_TARGET

# One dashboard, also registered in the reusable source catalog.
nebius-cxcli grafana import ./gpu.json --config ./config.yaml --target CLUSTER_TARGET --attach

# Every *.json file directly in the directory.
nebius-cxcli grafana import ./dashboards --config ./config.yaml --target CLUSTER_TARGET

# Every dashboard, also registered in the catalog.
nebius-cxcli grafana import ./dashboards --config ./config.yaml --target CLUSTER_TARGET --attach
```

`--config` defaults to `./config.yaml`. Automation must specify one exact
`--target`; an interactive terminal can ask for it. Add `--recursive` to include
subdirectories. Files must have distinct, stable dashboard UIDs.
The Grafana component is selected by the catalog's `chart_component_id`; custom
component IDs work with dashboard declarations and rendering.

Cluster access requires an installed Grafana app, completed deployment ownership,
and the existing configured admin Secret. cxcli checks the accepted cluster ID
and Kubernetes UID, the owned ready HelmRelease, rendered configuration, and
Service binding. Newly rendered Grafana releases carry the project/target ownership
annotation in both ordinary and full deployments. An older unmarked release needs
normal deployment reconciliation before dashboard commands can use it; import does
not adopt it automatically. This also supports Grafana installed through ordinary app apply
after the accepted cluster deployment. It creates a temporary loopback-only tunnel
and keeps credentials in memory. Import does not deploy Helm, create credentials,
change exposure or advance cluster acceptance.

Without `--attach`, import saves JSON under
`grafana_dashboards/<target>/<uid>.json`, adds typed `dashboard_imports` entries to
the Grafana chart row in `config.yaml`, **and installs the dashboards immediately**.
It is not a config-only operation. `--attach` additionally publishes reusable JSON
and provider entries to `component_sources.yaml`; `--component-sources PATH`
selects a different existing catalog. Attachment is available only in cluster
mode. The importing target retains API ownership; other targets can use the
attached catalog source through normal file provisioning.

## Import progress and local execution

API import shows the active stage and elapsed time while it checks project and
cluster access, connects to Grafana, validates dashboards, saves configuration,
and installs and verifies each dashboard. The spinner pauses while you select a
datasource. Completion messages remain visible; final verification is reported
after the connection and operation leases have been cleaned up.

Progress goes to stderr. Redirected output uses plain stage messages without
animation; `NO_COLOR` disables colors. Progress does not change timeouts or add
network requests.

Cluster import shares the cross-workstation Deployment lease with operations
using the same project backend. Existing dashboard ownership is checked before
datasource discovery and selection, which happen before acquiring mutation leases.
One datasource inventory serves all selection prompts. The renewable Kubernetes
authentication setup remains open through selection and is reused after checking
the same configuration and accepted identity. Import then rechecks configuration, destination
identity and datasource availability before saving or installing anything.
Changed selections stop the import; rerun to choose again.

The checks under the leases remain fresh, including deployment acceptance,
cluster identity, release ownership and dashboard ownership. The admin Secret
is read again and the Grafana connection is reopened after selection to account
for credential rotation or a replaced pod.

To update an existing dashboard, keep its UID and use `--overwrite`. For a
file-provisioned dashboard, this works only when Grafana identifies its classic
file provider and explicitly allows edits (`allowUiUpdates: true`). The flag is
sufficient confirmation. Import preserves the folder and provisioning metadata,
and warns that provisioning can replace the edit later. It never changes the
provisioning source. An omitted folder preserves the existing managed folder;
an explicitly different `--folder-uid` or `--attach` rejects the entire batch
before datasource selection or new writes.

Other managers and file providers without explicit edit permission remain
blocked. Update their provisioning source, or use a new, unused UID for a separate
API-owned copy. Ownership and edit permission are checked again before installation
and readback, including when the content is identical.

Grafana commands use a local process lock for the selected backend. Nested
configuration and deployment steps share that invocation. The kernel and
contained-process supervisors own the lock lifetime; no remote lease is created.

Terraform owns its S3 state and native lock. Cxcli neither reads nor writes shared
S3 deployment checkpoints or leases. Obsolete objects are ignored and untouched.
Command-local checkpoints, including Soperator upgrade recovery, remain supported.
`grafana install` always renders current settings and runs normal deployment;
old cancelled attempts do not prevent it. See [installation](observability.md)
for approvals and retry behavior.

Serialize complete mutation workflows for a backend across workstations and CI.
Terraform's state lock alone does not serialize Grafana, Flux, Helm or SDK writes.
Local process cleanup cannot revoke requests already accepted by remote APIs.

## Use an external Grafana server

```bash
# Set GRAFANA_TOKEN securely in your shell before running this command.
nebius-cxcli grafana import ./dashboards --url https://grafana.example.com/ --token-env GRAFANA_TOKEN

# Browser-assisted manual import for a server using SSO.
nebius-cxcli grafana import ./dashboards --url https://grafana.example.com/ --sso
```

`--url` selects external mode and rejects config, target and catalog options.
There is no project lookup or automatic Nebius/Grafana token fallback. API access
requires an explicitly selected token environment variable with sufficient
Grafana organization, folder, dashboard and datasource permissions. HTTPS is
required except for loopback HTTP; reverse-proxy subpaths are preserved and
credential-bearing redirects are rejected.

`--token-env` is optional for cluster mode and interactive external import. The
cluster uses its Secret; interactive external import asks for token or SSO mode.
Headless external API operations require `--token-env ENV`.

`--sso` prepares normalized files in `./dashboards-prepared` (override with
`--output-dir`) and opens Grafana's import page. Complete login, file selection,
datasource selection and server overwrite confirmation in the browser. cxcli
does not extract browser cookies or claim an API login. The command exits **3**
with `manual import pending`; it has not verified remote installation. Export and
live validation require API authentication and do not accept `--sso`.

## Folders, datasources and updates

Imports default to Grafana's root folder. Use `--folder-uid UID` for an existing
folder; cxcli does not create or select folders by ambiguous title. Distinct
embedded datasource UIDs/types and datasource template variables are preserved.
Use repeatable `--datasource-map SOURCE=UID` to resolve names or `${DS_NAME}`
placeholders. Interactive API import offers a searchable list for unresolved
mappings. Each choice shows its name, type and UID; only compatible datasource
types are offered when the dashboard declares a concrete type. Otherwise, all
existing datasources are shown. Use arrow keys to navigate, type to filter, and
press Enter to select. Even a single choice requires Enter. Confirmed mappings
are reused throughout the batch, and availability/type are checked again during
preflight. A filter with no matches shows a message and blocks selection until
you change or clear the filter, or cancel.

Ctrl+C cancels the new batch. No compatible choices, cancellation, unresolved
noninteractive mappings or type-incompatible references stop the new batch before
project publication or committed dashboard writes. Existing recovery of an earlier
interrupted operation can occur before selection. Browser-assisted SSO continues
to select datasources in Grafana. Query text is not rewritten. Embedded library
elements require a resolved Classic JSON export.

For automation, supply the mapping explicitly:

```bash
nebius-cxcli grafana import ./dashboard.json --config ./config.yaml --target CLUSTER_TARGET --datasource-map DS_METRICS=prometheus
```

Replace `DS_METRICS` with the dashboard's datasource reference and `prometheus`
with the existing destination datasource UID. Explicit mappings skip the picker.

Classic dashboard JSON, `{ "dashboard": ... }` exports, and
`dashboard.grafana.app/v1` resources are accepted. Runtime IDs and versions are
removed; public UIDs are retained. Symlink inputs, duplicate UIDs and oversized
JSON are rejected.

Matching canonical content, folder and ownership is a no-op, including with
`--overwrite`: no saved API write, version bump, duplicate declaration, or rewrite
of unchanged configuration, JSON, receipt or catalog files. Reads, schema-admission
dry runs and normal lease acquisition still occur. `--overwrite` permits replacing
different API-owned content and eligible editable file-provisioned content. It
does not overwrite a concurrent edit after preflight. Import checks Grafana's
resource version and verifies saved content after each write; schema migration
is accounted for when comparing dashboards.

Catalog attachment keeps one provider/key per dashboard UID. Reattaching an
existing UID under a different folder or key is rejected before project or API
writes, including with `--overwrite`. Resolve the existing catalog ownership
explicitly before attaching elsewhere; cxcli does not automatically relocate or
remove catalog entries that other targets may use.

Project publication, Grafana writes and catalog attachment are separate
transactions. Earlier successful dashboards remain installed if a later one fails.
A nonsecret `.grafana-imports/<target>.json` receipt records intent and progress.
Rerun the same command to finish; uncertain writes are reconciled through reads.
After a completed import, changed remote content starts a new pending update
using its current version. An interrupted update retains its frozen version guard.
Committed local publication is recovered only for the same operation destination.
Catalog attachment is published last; a failed attachment reports pending work.
Errors exit **1**; verified success exits **0**. Input/parser errors may exit **2**.

API-owned declarations replay by default (`replay: true`). Render validates and
packages those project sources without contacting Grafana. Ordinary app apply
and deploy replay them after Grafana readiness.
Missing dashboards are restored. Existing API-owned dashboards are preserved,
including browser edits. Only an explicit `grafana import --overwrite` replaces
changed content; importing different content without that flag fails. Live ownership is checked before Helm can remove any linked file provisioning.
Final deployment verification checks existence and API ownership without requiring
content equality. Frozen local source hashes still have to match the declarations.

Managed cluster imports save **manual copies** with `replay: false` and a required
`management_sha256` binding the destination, namespace, UID, folder and provider
identity/permission. They have no catalog link and do not suppress provisioning,
generate replay assets or participate in deployment checks. A manual-only set
does not open a Grafana connection during deployment. Use `grafana validate` to
check these saved files or explicitly re-import them with `--overwrite`. Missing
provenance, missing managed dashboards or changed ownership stop re-import;
cxcli does not recreate them as API-owned dashboards. Keep the import receipt
with the project configuration and JSON files.

## Persistent storage and replicas

New managed installations use Grafana chart **13.2.5**, Grafana **13.2.2** and the
separate CloudPirates PostgreSQL chart **0.20.6** with PostgreSQL **18.6**. Grafana
selection automatically adds PostgreSQL on the same target and makes the Grafana
HelmRelease depend on authenticated PostgreSQL readiness.

Two stateless Grafana replicas share PostgreSQL and one encryption key. Sessions
and dashboards survive Grafana pod replacement. A single PostgreSQL primary stores
data on a **10Gi ReadWriteOnce PVC** using **compute-csi-default-sc**. PostgreSQL
pod replacement reuses that claim. Change `values.persistence.size` and
`values.persistence.storageClass` on the `postgresql` component before installation
to choose storage. Existing-claim attachment is unsupported. Claim expansion and
storage-class changes remain subject to Kubernetes/CSI constraints.

Deployment generates separate runtime Kubernetes Secrets for the database
administrator, the ordinary `grafana` database owner, Grafana administrator and
shared encryption key. Configuration contains only Secret references. Owned,
complete Secrets are reused; missing or partial credentials with retained state
stop deployment rather than generating replacements. PostgreSQL can be selected
alone and bootstraps its database credentials independently. Attaching Grafana to
an already initialized standalone database without the original Grafana Secrets
is unsupported; select both for a fresh Grafana installation.

Grafana and its database must share a namespace. PostgreSQL exposes only a
ClusterIP Service, authenticates with SCRAM, and admits Grafana-labeled pods in
that namespace through NetworkPolicy. This requires a NetworkPolicy-enforcing
CNI. The selected private connection uses `ssl_mode=disable`; there is no TLS or
certificate automation. No database administrator password is supplied to Grafana.

Imported JSON is saved with **`editable: true`**, so authorized Grafana UI users
can edit and save it. `allowUiUpdates` belongs to file provisioning and is not
needed for API-owned dashboards. API ownership remains explicit; importing does
not convert file-provisioned dashboards into API-owned dashboards.

Database claims and runtime Secrets are retained on ordinary component removal.
Keep the encryption key together with database backups: losing it can make stored
datasource credentials unreadable. A PVC is persistence, not a backup. This default
has one database primary and therefore does **not** provide database HA. Backups,
point-in-time recovery and database replication are outside this installation.
Cluster/PVC deletion can still destroy data.

A failed first Helm install may have release history without a Deployment.
Normal deployment can retry after the stored owned manifest proves the expected
PostgreSQL backend, no residual workload remains, and every credential is present.
Stale or ambiguous history and missing credentials still block recovery; cxcli
does not reset Helm history or recreate secrets to get past these checks.

Existing SQLite or unidentifiable Grafana backends are rejected before application
preparation, even with no dashboard declarations. There is no migration, fallback,
or compatibility path. A failed initialization retains its volume and credentials
for diagnosis; deployment does not erase or reinitialize it.
Managed Grafana uses the pinned image's startup command and
`/etc/grafana/grafana.ini`. Custom startup commands, arguments and
`GF_PATHS_CONFIG` overrides are rejected. Admission checks the mounted configuration
and exact database/Secret bindings, including pending HelmReleases; it does not
accept an unrelated ConfigMap as proof of the active backend.

## Export and validate

```bash
# Export all dashboards, or add --dashboard-uid UID repeatedly.
nebius-cxcli grafana export --config ./config.yaml --target CLUSTER_TARGET --output-dir ./exported

# Read an external server; --folder-uid filters a folder.
nebius-cxcli grafana export --url https://grafana.example.com/ --token-env GRAFANA_TOKEN --folder-uid FOLDER_UID

# Offline input validation.
nebius-cxcli grafana validate ./dashboards --recursive

# Validate files against an external API without installing them.
nebius-cxcli grafana validate ./dashboards --url https://grafana.example.com/ --token-env GRAFANA_TOKEN

# Validate declared project dashboards and existing datasource/read-endpoint fit.
nebius-cxcli grafana validate --config ./config.yaml --target CLUSTER_TARGET
```

Export only reads remote dashboards and writes portable JSON, by default to
`./dashboards`. It never attaches to a catalog or changes config. Existing different
output files require `--overwrite`; the entire output batch is checked first.
API listing is paginated. Local validation with no destination is offline.
Configured validation retains the existing Prometheus/Loki/Tempo read-side checks;
passing JSON paths with a destination validates their schema and datasource fit.
API validation uses a POST dry-run for schema admission; it never stores dashboards.

The old `grafana --export-dashboard`, `--dashboard-json`, `--dashboard-folder`,
blanket `--datasource`, username/password flags and top-level
`validate-dashboards` are removed. There are no compatibility aliases.

## Development validation

Run the focused `test_grafana_*` tests. `scripts/verify_grafana_api.py` runs the API
integration tests in a disposable, digest-pinned Grafana 13.2.2 Docker container,
with a generated fixture credential and loopback-only port. It removes that
container afterward. This qualifies schema migration, no-op repeat imports,
resource-version conflicts, organization isolation, editable provisioned updates,
metadata preservation, denied-edit protection and later source reconciliation; it does not prove
customer cluster reachability, SSO completion or live monitoring ingestion.

`scripts/verify_grafana_postgres.py` verifies the immutable upstream chart artifacts,
production-generated connection values, retained claims, disruption budget,
headless service and the authenticated SQL readiness patch.
`scripts/verify_grafana_persistence.py` uses two disposable Grafana containers and
one PostgreSQL volume to verify shared sessions, editable API dashboards and
restart persistence without replay. These Docker checks do not prove Kubernetes
PVC retention, NetworkPolicy enforcement, or browser UI interactions; those require
an independently declared disposable-cluster trial.
