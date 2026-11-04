# Observability and Grafana installation

Install Grafana and configure its storage, collectors and query connections on
an existing cxcli-managed MK8s target:

```bash
nebius-cxcli grafana install --config /path/to/config.yaml --target CLUSTER_TARGET
```

Both flags are required. Interactive terminals open the configuration wizard;
`--no-interactive` uses explicit flags and established local-installation settings.
Entering `grafana install` from a managed-only target selects the local stack by
default. Explicit per-signal flags can choose remote or both; subsequent reruns
preserve those established routes. This command is the focused entrypoint for
in-cluster Grafana and uses the same resolver, renderer, deployment and validation
as the main workflow.
Setup and datasource preview resolve backends only for the selected target.
Plain MK8s setup does not look up Soperator sources, even when another target in
the configuration uses Soperator. The complete configuration is still validated.
Selecting Grafana through the Apps wizard or noninteractive project creation and
component addition uses the same configuration routine, including when standalone
backends were selected earlier. Backtracking to disable observability removes
only the routing and dependencies introduced by that wizard.
Settings persist under `observability.routing` on the matching managed component
or `infra.external_clusters[]` record. Apps remain
separate entries in `apps.charts[]`. Apps selection saves configuration intent;
use the full `render`/`deploy` workflow to apply native Soperator routing changes.
The ordinary Apps deployment path cannot apply protected native changes.

After confirmation, the command atomically saves changed desired settings,
always renders current `config.yaml`, and invokes normal project deployment.
Its confirmation covers replacing generated artifacts. Pending infrastructure
and unrelated App changes are included in the normal plan and retain the usual
approvals. Soperator workload maintenance, jail protection, ownership and
acceptance checks still apply.

An unchanged rerun keeps configuration and datasource identities stable, renders
again, and verifies actual convergence. A cancelled older deployment does not
block it. Matching local deploy attempts can resume; different artifacts or
semantic options select a separate attempt. No shared S3 deployment history or
execution lease is consulted. Dedicated commands, including `soperator upgrade`,
retain their own local checkpoints and recovery rules.

Cancellation before saving leaves configuration unchanged. Later failure keeps
saved intent for retry. Use `--no-interactive` to reuse saved choices.
The short render lock serializes publication; a local process lock excludes
simultaneous mutations of the same backend. Terraform retains its native remote
state lock. Serialize whole deployments across machines or CI repositories.

## Creation wizard defaults

For new ordinary MK8s targets, the interactive `create` and `component add`
wizards ask:

```text
Observability:
  Nebius-managed observability (default)
  In-cluster observability
  Disabled
```

Accepting these defaults enables metrics, logs and traces through one Nebius
Observability Agent. It configures remote storage and does not automatically add
Grafana, VictoriaMetrics, VictoriaLogs or VictoriaTraces. Skipping field editing
also accepts these defaults. The canonical `observability.enabled` field stays
true because collection is enabled; setting it false disables collection.
Selecting local installation opens the existing Grafana/storage setup flow.
Explicit `grafana install` selects the in-cluster workflow. Dedicated Soperator
defaults are unchanged. Headless creation uses the same managed default.

The wizard shows regional write destinations for the configured project using
the existing endpoint templates and supported collector discovery/authentication.
It also shows the collector's OTLP/gRPC address for application traces.
Applications must be instrumented and configured to send traces; the collector
does not create spans for them. See the [Nebius agent guide](https://docs.nebius.com/observability/agents/nebius-o11y-agent).

Per-signal selections remain configurable. On ordinary MK8s, each
`observability.kubernetes.<signal>.enabled` flag limits the selected routing signals;
an explicit false is preserved across rendering and deployment. Re-enabling a
signal reuses its saved route. Disabling every signal stops collection while
retaining storage and query connections. Existing targets retain saved settings;
an explicit mode change uses staged collector handover. Selecting the named
Nebius-managed mode resets write endpoints, authentication and reserved
`metrics-remote`, `logs-remote` and `traces-remote` query connections to Nebius
defaults. Separately named query-only datasources are preserved.
If the selected default was an automatic local datasource removed by this mode
change, the default follows the managed route. An explicit query-only default
remains selected.
Backtracking removes only routing and Apps introduced by the current wizard;
it does not remove preexisting storage or data. If setup temporarily enables a
saved disabled collector, backtracking restores that row's original identity,
disabled state and custom values.

## Browser access after deployment

Successful `grafana install` and ordinary `deploy` print Grafana access instructions
and save the same commands in `generated/reports/deploy-report.md`, including for
local-only observability and successful unchanged or resumed deployments.

To retrieve fresh instructions later:

```bash
nebius-cxcli grafana show --config /path/to/config.yaml --target CLUSTER_TARGET
```

Both flags are required. Every invocation verifies the selected live cluster and
its owned, ready Grafana release. It derives the HTTP Service port and admin
Secret references from the live workload, rather than replaying a saved report
or assuming that local Grafana settings have already been deployed. It requires
working Nebius/Kubernetes authentication and a recorded deployment identity.

The output includes the login username, a copyable `kubectl port-forward`
command, the browser URL `http://127.0.0.1:3000`, and a command that retrieves and
decodes the admin password. Keep the port-forward running in its terminal and run
the password command in another terminal. If local port 3000 is busy, change the
local side of the port mapping and use the matching browser port.

Copyable commands use the CLI's shared light-gray background in color-enabled
terminals. Labels and the browser URL stay outside the highlight; saved reports
use ordinary code blocks.

Commands explicitly select the verified persistent kubeconfig, context and
namespace. Deployment completion and `grafana show` may refresh the target entry
in `~/.kube/config`, preserving an existing current context. They honor `CI` and
`NEBIUS_CXCLI_PERSIST_LOCAL_KUBECONFIG`; when persistence is disabled, an existing
verified durable context is required. Unavailable access setup is reported
explicitly. A failed handoff does not reverse an already accepted deployment;
`grafana show` returns nonzero if it cannot verify access.

Access discovery does not read the password, start forwarding, call the Grafana
API, or modify Kubernetes resources. It reads only the Secret username and key
names needed for the handoff. Gateway-backed installations retain their public
links and also receive forwarding instructions; forwarding does not bypass
Gateway readiness checks. It does not create or rotate credentials.
Username and password must reference distinct Secret keys; an aliased binding
fails before any Secret read.

For private Grafana in a custom catalog, retain a ClusterIP Service, disable its
route, and set the catalog's automatic Gateway component ID to an empty string.
Status and dashboard validation use a temporary loopback port-forward pinned to
the selected Kubernetes context and clean it up on success and failure. A pending
public Gateway is not classified as private.

## Interactive setup

The wizard explains each storage choice: **Local** stores telemetry in the selected
cluster, **Remote** sends it to external storage, and **Both** keeps a local copy
and sends it remotely. Local metrics use VictoriaMetrics, logs use VictoriaLogs,
and traces use VictoriaTraces. Remote setup offers Nebius for the configured
project/region or a custom destination. Nebius write URLs and required protocols
are automatic; custom destinations ask for their write URL and protocol.

Grafana datasources are created automatically for standard local and Nebius
backends. Before saving, the wizard previews each effective datasource's name,
backend/type, query URL, and default selection, including saved additions and
overrides. This is a configuration preview; connectivity is verified during the
installation lifecycle.

For a target configured with Soperator, setup first explains that its metrics and
logs collectors will be reused. It then verifies the selected release source to
resolve native query addresses and shows progress while doing so. This can happen
while loading saved routing, before the storage prompts. Verified defaults are
reused throughout the same install invocation, including post-confirmation config
qualification, saving and render-time configuration processing, without repeating
source-only discovery. Preview does not download the release's chart packages.
These messages describe configured ownership and source verification, not live
cluster health. Full required-chart verification and a fresh release identity check
remain part of render after confirmation; deployment uses the resulting frozen
snapshot. A later invocation verifies source defaults again, including after a
cancelled or failed attempt. Required upstream access can still fail; defaults
reuse does not turn installation into an offline operation.

For local storage, no manual datasource fields are needed:

```text
Grafana will use these datasources:
  metrics-local -> VictoriaMetrics [default]
  logs-local    -> VictoriaLogs
  traces-local  -> VictoriaTraces

? Customize datasource connections? No
```

The actual preview also prints the resolved query URLs and plugin types.
Answering **No** keeps the displayed connections. **Yes** offers adding a
datasource, editing an existing connection, choosing the default, or finishing.
Backend types and defaults are selected from menus. New names are suggested and
checked for duplicates; editing keeps the existing name and stable UID. Select
**Automatic metrics default** to follow the metrics storage choice, or explicitly
select another configured datasource. A valid saved explicit default is retained;
if a storage change makes it unavailable, the wizard asks for a replacement.

A custom remote write destination requires a separate typed **read** connection.
The wizard supplies its reserved name (`metrics-remote`, `logs-remote`, or
`traces-remote`) and asks only for missing query details. Changing a write
destination requires review of its saved read connection unless an explicit
datasource option supplies the replacement. Write and read URLs are independent.

Explicit command options take precedence and cannot be overwritten through
customization. Repeating an identical `--datasource NAME TYPE READ_URL` preserves
the connection's position, stable UID and complete authentication settings.
Authenticated URL/type changes require editing the matching configuration and
authentication together. Authentication remains configuration-managed: existing settings
are preserved, authenticated datasource edits and changes involving custom write
Secret references direct you to update the corresponding configuration together.
The wizard never asks for tokens or creates Secrets. Optional Pushgateway is
explained as a way to publish results from short-lived jobs. Selecting remote-only
storage retains existing local stores and volumes; it does not retire them.

## Defaults and flags

For explicit `grafana install`, defaults are identical for labs, development and
production, with or without Soperator. Each signal defaults to local storage. A remote URL by itself does
not enable remote export.

| Flag | Required | Default / meaning |
| --- | --- | --- |
| `--config PATH` | Yes | Managed project's source `config.yaml` |
| `--target TARGET` | Yes | Enabled managed MK8s instance in that file |
| `--metrics-storage local\|remote\|both` | No | `local` |
| `--logs-storage local\|remote\|both` | No | `local` |
| `--traces-storage local\|remote\|both` | No | `local` |
| `--metrics-remote URL` | No | Region/project's Nebius Prometheus **write** URL |
| `--logs-remote URL` | No | Region's Nebius OTLP gRPC logs **write** URL |
| `--traces-remote URL` | No | Region's Nebius OTLP gRPC traces **write** URL |
| `--logs-remote-protocol otlp-grpc\|otlp-http` | No | `otlp-grpc` |
| `--traces-remote-protocol otlp-grpc\|otlp-http` | No | `otlp-grpc` |
| `--datasource NAME TYPE READ_URL` | No | Repeat to add/replace a named query connection |
| `--default-datasource NAME` | No | Metrics local connection, or metrics remote in remote-only mode |
| `--pushgateway / --no-pushgateway` | No | Disabled on a new configuration; asked in the wizard |
| `--interactive / --no-interactive` | No | Interactive when attached to a terminal |

Remote defaults come from `component_cli_settings.yaml`, using the configured
region and project. They are write destinations, separate from Grafana's read
connections. The default Nebius log/trace write endpoints require OTLP gRPC.
Custom gRPC URLs must contain only the scheme and authority. Custom OTLP HTTP
URLs are complete signal write endpoints, including `/v1/logs` or `/v1/traces`
when required by the server.
A Loki **read** URL does not imply an OTLP-compatible log **write** URL.

```bash
# Local storage plus a copy in Nebius, with optional batch-result ingestion.
nebius-cxcli grafana install --config config.yaml --target CLUSTER_TARGET \
  --metrics-storage both --logs-storage both --traces-storage both \
  --pushgateway --no-interactive

# Add an independent query-only datasource; this does not change log collection.
nebius-cxcli grafana install --config config.yaml --target CLUSTER_TARGET \
  --datasource production-logs loki http://loki.example.com:3100 \
  --no-interactive

# Custom remote metric storage needs an explicit, typed read connection.
nebius-cxcli grafana install --config config.yaml --target CLUSTER_TARGET \
  --metrics-storage remote \
  --metrics-remote https://metrics.example.com/api/v1/write \
  --datasource metrics-remote prometheus https://metrics.example.com \
  --default-datasource metrics-remote --no-interactive
```

The reserved routing connections are `metrics-local`, `metrics-remote`,
`logs-local`, `logs-remote`, `traces-local` and `traces-remote`. A custom remote
write endpoint requires the corresponding `*-remote` read connection. Additional
names such as `production-logs` are independent query connections. Each has a
stable target/name-derived UID, printed in the installation summary.

| Signal | Local storage | Local datasource type | Nebius remote datasource type |
| --- | --- | --- | --- |
| Metrics | VictoriaMetrics VMSingle | `prometheus` | `prometheus` |
| Logs | VictoriaLogs | `victoriametrics-logs-datasource` | `loki` |
| Traces | VictoriaTraces | `jaeger` | `tempo` |

The VictoriaLogs plugin is installed automatically. VictoriaTraces uses Grafana's
built-in Jaeger client at `/select/jaeger`; no Jaeger server is installed.
Existing extra Grafana plugins are preserved. New Grafana and database Services
are private; access uses the explicit target's port-forward workflow. Existing
Grafana access settings remain in place.

Local metrics retain 90 days, logs 30 days, and traces 7 days. New standalone
stores request 20Gi, 10Gi and 10Gi respectively; native stores retain their
existing volume settings. These are initial sizes, not capacity guarantees.
Local single-server storage does not provide database HA or backups. Tune chart
storage and resources for the actual ingestion rate before relying on production
retention. Changing export to remote preserves previously selected local stores
and PVCs; storage retirement is a separate explicit operation.

## Collector ownership

On Soperator, cxcli reuses the native VMAgent, DCGM exporters, and native node,
event and jail log collectors from the frozen upstream release. Local service
identities come from that release, including its port; older releases may use
8429 for VMSingle while standalone installations use 8428.

On plain MK8s, the VictoriaMetrics stack supplies VMAgent and Kubernetes metric
exporters. Selected GPU Operator DCGM targets are also scraped. VictoriaLogs and
VictoriaTraces are independent Helm Apps in `component_sources.yaml`, so they
can also be selected without Grafana as storage components. The verified cxcli
collection workflow requires a Grafana query path for each destination; use
`grafana install` to configure that path before deploying local collection.

The OpenTelemetry chart supplies an OTLP gateway for local/custom logs and
traces. A separate DaemonSet of the same chart reads node pod logs when needed.
Soperator retains its existing log collectors and only needs the gateway for
its additional trace pipeline. An unowned signal using only Nebius's default
remote endpoint can use the Nebius observability agent. VMAgent handles metrics;
it does not collect logs or traces. Applications must emit instrumented spans
to produce traces.

One collector owns each signal. Ordinary mode changes prepare destination stores
with the destination collector inactive, stop source collection and verify current
HelmRelease generations and stopped Pods, then activate the destination. Only then
does the stable trace Service switch. A target-owned ConfigMap records progress
and the frozen destination digest under the deployment lease. Retries inspect live
state and never restart a quiesced source during preparation. Retired collectors
remain as inactive releases; stores, PVCs and Secrets remain intact. A failed
handover or query check leaves that state available for diagnosis and retry.
Foreign releases are not adopted. Soperator keeps its native ownership rules.

## Exact VMAgent destinations

`local` renders only the internal Prometheus remote-write URL. `remote` renders
only the selected remote URL. `both` renders local first, then remote. There is
no disabled remote URL setting: unwanted entries are removed from the complete
list, including `additionalRemoteWrites` and destination-specific extra flags.
Shared scalar queue/buffering settings are retained. Ambiguous positional arrays
must be explicitly remapped.

VMAgent disk queues depend on both URL and position. An existing configuration
with external first and internal second cannot safely become local-only by
silently moving the internal queue. Installation stops for an explicit drained,
stopped-collector queue handoff when that identity would change. It does not
delete, rename or discard buffered samples. Fresh configurations keep local
first to avoid this problem on ordinary `both` to `local` changes.
See [VMAgent persistent queues](https://docs.victoriametrics.com/victoriametrics/vmagent/).

The operator-generated workload arguments are checked after reconciliation,
alongside datasource type, URL, default selection, health and recent metric
samples. Soperator additionally verifies fresh metrics and logs against the
current workload Pod identity. These checks do not manufacture workload traces.

## Authentication and dashboards

A custom source catalog can declare a Prometheus datasource with `auth: none`
and an internal read endpoint. That datasource receives no cloud authorization
header or read-token Secret. This catalog setting is separate from the routing
fields below.

Nebius reader and writer credentials use separate runtime Kubernetes Secrets.
Local routes need neither credential. Managed Nebius credentials are never
forwarded to custom endpoints. For custom authentication, author Secret
references; the referenced Secrets must already exist in the collector or
Grafana namespace:

```yaml
metrics:
  storage: remote
  remote:
    url: https://metrics.example.com/api/v1/write
    auth: none
    auth_secret: {name: metrics-writer, key: token}
datasources:
  - name: metrics-remote
    type: prometheus
    url: https://metrics.example.com
    auth_secret: {name: metrics-reader, key: token}
```

These are fields within `observability.routing`; never put token values in
configuration. `auth_secret` supplies bearer authentication. The Nebius defaults
follow the official [metrics](https://docs.nebius.com/observability/metrics/ingest),
[logs](https://docs.nebius.com/observability/logs/ingest/opentelemetry-collector),
and [traces](https://docs.nebius.com/observability/traces/ingest) ingestion contracts.

Routing-aware Grafana starts with typed connections and Explore links. The old
Nebius-specific catalog dashboards assume different labels/query languages and
are not provisioned automatically for these connections. Import compatible
dashboards explicitly, mapping their datasource inputs to the printed UIDs:

```bash
nebius-cxcli grafana import ./dashboards --config config.yaml --target CLUSTER_TARGET \
  --datasource-map DS_METRICS=PRINTED_METRICS_UID --attach
```

## Short-lived lab results

Pushgateway is optional for both local and remote storage. Exporters such as
DCGM are scraped directly and do not need it. A short-lived lab publisher may
push its measured results to Pushgateway; VMAgent scrapes those results every
five seconds with `honor_labels`, then writes to the selected database(s).
Pushgateway uses a separate release with a 1Gi PVC and persistence enabled.
Its scrape and publication addresses follow chart release, name, namespace and
service-port overrides.
Grafana never owns it through `extraObjects`.

Immutable result JSON remains the result of record. The installation summary
prints the publication Service URL, query connections and datasource UIDs for
clients and dashboard imports. The course discovery helper consumes the generated v2 manifest, accepted
ordinary-app artifact inventory and target HelmRelease datasource provisioning,
then verifies the corresponding live Services and native `cxcli-pushgateway`
scrape. It writes course connection files only. Unsupported or stale artifact
shapes fail explicitly; this is an artifact consumer, not a public JSON discovery
command. Courses do not install Pushgateway or create a compatibility bridge. The current
course publisher's authenticated remote readback is not qualified here; retain
local metrics for that flow, optionally replicating with `both`.

## Application instrumentation

The preinstalled VM monitoring agent and VMAgent are insufficient for Kubernetes
logs and traces. Managed mode uses the Nebius Kubernetes Observability Agent for
all three signals. Both managed mode and the local VMAgent discover application
Pods with `prometheus.io/scrape: "true"`, `prometheus.io/port`, and optional
`prometheus.io/path` and `prometheus.io/scheme` annotations. Declare the matching
container port. See the [Nebius annotation contract](https://docs.nebius.com/observability/metrics/ingest/nebius-o11y-agent).

On ordinary MK8s, instrument applications to send OTLP/gRPC traces to
`cxcli-otlp.observability.svc.cluster.local:4317` without TLS inside the cluster.
This address is rendered when traces are enabled and follows mode changes.
Soperator uses the gateway Service address printed by its installation summary.
Applications using older collector-specific
addresses need explicit reconfiguration. Generic OTLP application metrics are not
configured by this workflow; use Prometheus scraping. The two OTel catalog entries
serve separate roles: `opentelemetry-collector` receives and exports OTLP in a
Deployment; `opentelemetry-logs` reads node Pod logs in a DaemonSet. Neither is
installed by the managed default.

## Delivery validation and reports

Ordinary observability deployment creates a temporary, restricted, digest-pinned
probe Pod. It exposes a uniquely labeled Prometheus sample, writes a structured
stdout log, and emits an OTLP/gRPC span through the advertised trace ingress.
It never writes directly into a backend. Each enabled destination must return
current project, target, frozen configuration, run and time evidence through both its backend API and
Grafana datasource proxy. The polling window is five minutes; the Pod has a
10-minute active deadline and cleanup uses its immutable UID. The probe requires
an eligible CPU node under its placement policy. Interrupted cleanup is reported
separately, with the failure reason in JSON and the deployment summary.
Readiness or a datasource health response cannot produce
a delivery OK.

Set `NEBIUS_PROFILE` to an explicit human Nebius CLI profile for managed validation.
The operator IAM token is used in memory for reads; it is separate from producer
metadata credentials and is never written into reports. Preflight checks access
to `https://grafana.nebius.dev/`, discovers organizations and matches datasource
types and project read URLs without assuming organization 1 or fixed UIDs. No
managed dashboards or datasources are created or changed. Local Grafana uses its
existing administrator Secret over a temporary loopback-only tunnel. Custom
remote backend reads require HTTPS and their configured Secret references.

Missing authentication, ambiguous or missing datasources, stale results, empty
queries, skipped required checks and cleanup failure all prevent success. Failed
validation leaves the deployed stack for diagnosis. Terminal checks, the target's
`observability-ingestion-report-<target>.json`, and `deploy-report.md` share the
same result. Reports list effective producer transports; the Nebius agent writes
metrics with OTLP/HTTP while VMAgent uses Prometheus remote write. Dual-write
reports list both destinations. A new deployment attempt clears its selected
validation results before preparation, so an early failure cannot reuse an older
success. A source test run does not certify live ingestion.

## Independent PostgreSQL applications

`postgresql` is in the **Database** group and can serve arbitrary applications.
Its chart settings remain user-owned. Set `values.auth.existingSecret`; an enabled
custom user also needs `values.customUser.existingSecret`. Use the pinned chart's
Secret key settings and keep password values out of config.yaml.

`grafana-postgresql` is a distinct **Observability** dependency. Only that identity
receives Grafana database users, generated Secret references and ingress policy.
Both database components can coexist on one target with distinct release names.
Existing implicit Grafana bindings under `postgresql` fail before adding a new
database. Explicitly change that binding to `grafana-postgresql`, preserving its
release name, namespace, PVCs and Secret references; verify the retained database
before deployment. cxcli does not silently rename or replace a database.
