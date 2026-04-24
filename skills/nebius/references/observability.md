# Nebius Observability

Use this reference when the task is about Nebius observability architecture,
public endpoints, agent selection, or how `services/nebius-cxcli` should model
observability in `component_sources.yaml` and `config.yaml`.

## Public Product Model

Nebius observability has three public services:

- Monitoring: metrics storage, dashboards, alerts, and Prometheus-compatible
  read/write flows.
- Logging: log storage, LogQL-compatible querying, CLI access, and
  Loki-compatible read flows.
- Tracing: OpenTelemetry trace ingest plus Tempo-compatible read flows.

Nebius also has two agent families:

- Monitoring agent on Compute VMs:
  - preinstalled on Compute VMs and Managed Kubernetes node VMs
  - collects system metrics automatically
  - can collect journald logs from systemd services when the supported VM
    labels are enabled
- Nebius Observability Agent for Kubernetes:
  - explicit Helm chart for Managed Kubernetes clusters
  - detailed public docs define logs, metrics, and traces for this agent
  - exposes an in-cluster OTLP/gRPC trace receiver

For `nebius-cxcli`, treat these as separate control surfaces. Do not collapse
the VM Monitoring agent and the MK8s Helm agent into one generic "o11y agent"
config branch.

## `nebius-cxcli` Design Contract

`component_sources.yaml` owns source facts:

- `components.infra.mk8s.cli.observability.*`
- `components.infra.vm.cli.observability.*`
- `components.apps.<id>.cli.observability.metric_targets`

Built-in agent defaults now live under `primary_agent.*`:

- MK8s: `primary_agent.{kind,chart_component_id,logs,metrics,traces}`
- VM: `primary_agent.{kind,metrics,logs}`
- VM standalone collector remains the separate `public_ingest.*` branch

`config.yaml` owns project intent:

- `observability.enabled`
- `observability.kubernetes.*`
- `observability.vm.logs.*`
- `observability.vm.collector.*`

`observability.enabled` is the cxcli project switch for deploying/configuring
collectors. It does not create the Nebius Monitoring/Logging/Tracing service
endpoints themselves; those are project-scoped service surfaces.

Normalization/materialization owns runtime state:

- MK8s:
  - ensure collector app rows
  - write `values.config.*` into the chart rows
  - keep target scoping explicit in multi-cluster projects
- VM:
  - write supported Compute labels into `infra.components[].inputs.labels`
  - built-in Monitoring-agent metrics stay platform-managed
- VM standalone collector:
  - install/configure `nebius-o11y-agent` plus a Prometheus agent companion
  - pull the package from the canonical public Artifactory APT repo `https://artifactory.nebius.dev/artifactory/nebius-o11y-agent`
  - require a VM-attached service account and use `/mnt/cloud-metadata/token`
  - keep this path separate from the built-in Monitoring agent

## Endpoint Model

Use `assets/observability/public-endpoints.yaml` for the public-safe endpoint
map and config-branch summary.

High-level split:

- MK8s path:
  - write endpoints are relevant
  - read endpoints are relevant
  - agent auth should stay on Nebius-managed metadata/IAM paths
- VM path:
  - read endpoints are relevant
  - built-in VM metrics and opt-in journald logs use Nebius-managed internal ingest
  - journald log collection uses supported Compute labels
  - cxcli should not invent a customer-configurable VM public write-endpoint contract for the built-in agent
- VM standalone collector path:
  - public write endpoints are relevant
  - host metrics use Monitoring Prometheus remote_write
  - journald logs use the public Logging gRPC endpoint
  - auth stays on the VM metadata token, not static repo config

## Operational Notes

- Existing VMs need stop/start after changing journald labels.
- Public docs say omitted VM `systemd_units` means all units; explicit units are
  still the deterministic smoke-test path.
- The standalone VM collector is a narrower first cut: module-managed
  Ubuntu-family boot disks, host metrics plus journald logs, and a required
  attached service account for metadata-token auth.
- The public agents overview page is simplified; use the detailed Kubernetes
  agent page as the source of truth for logs, metrics, and traces support.
- Keep static observability keys, Grafana credentials, and raw agent secrets
  out of public repo config and generated artifacts.

## Useful Public Docs

- `https://docs.nebius.com/observability/`
- `https://docs.nebius.com/observability/agents`
- `https://docs.nebius.com/observability/agents/nebius-o11y-agent`
- `https://docs.nebius.com/observability/agents/monitoring-agent`
- `https://docs.nebius.com/observability/logs/journald`
- `https://docs.nebius.com/observability/metrics/grafana`
- `https://docs.nebius.com/observability/traces/grafana`
