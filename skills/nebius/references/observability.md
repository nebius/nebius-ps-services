# Observability

Reviewed 2026-09-07. Monitoring, Logging and Tracing have distinct ingest/read
interfaces; Audit Logs is a separate tenant-level security service documented
as preview. Use current service coverage and regional docs rather than assuming
every resource emits every signal. The endpoint template is a reference, not
proof that a credential or project can use the endpoint.

## Choose collection and query paths

| Path | Responsibility | Verification |
| --- | --- | --- |
| Built-in Compute Monitoring agent | Provider-managed VM system metrics; optional journald collection | Resource signal freshness and expected labels |
| Standalone VM collector | Explicitly configured additional collection | Service health, auth, export errors and actual ingestion |
| Nebius Observability Agent for Kubernetes | Chart-managed logs/metrics/traces | Config, pods, service endpoints and received signals |
| Prometheus/OpenTelemetry integrations | Application-specific signals | Scrape/receiver/exporter health plus query results |
| Audit Logs | Control-plane activity by actors/resources | Explicitly scoped audit query and supported-service coverage |

The Kubernetes agent's documented chart is
`oci://cr.nebius.cloud/observability/public/nebius-observability-agent-helm`.
Helm owns installation/upgrade; keep the selected chart version and effective
values in the owning project. Built-in VM ingestion is not a configurable public
write endpoint. Use `assets/observability/public-endpoints.yaml` for protocol,
region/project placeholders and auth distinctions; resolve exact endpoints
from current docs and never substitute private observed endpoints into templates.

## Operational workflow

1. Select project/region, target resources, signal types, collection owner and
   read/write principals. Verify service-specific roles or static-token scope.
2. Check built-in signals first. Avoid installing duplicate collectors or
   duplicate scrape jobs; limit labels and exclude sensitive log fields.
3. Configure only the authorized owner. Changing VM journald labels requires
   stop/start according to the docs, so it is an availability-affecting change.
   Explicit systemd unit selection makes collection scope reviewable.
4. Verify configuration, agent pods/services and exporter errors without
   claiming end-to-end delivery from component readiness alone.
5. Query bounded resource/time-scoped metrics/logs/traces. Confirm freshness,
   expected labels and timestamps. LogQL requires an explicit `__bucket__`
   selector (or the Nebius CLI `--bucket`); verify it before diagnosing empty
   results. Empty results may mean no signal, missing
   permissions, wrong scope or query limitations; distinguish those outcomes.
6. When a synthetic marker is needed, declare it as an active data write, run
   it only with authority, and verify receipt independently. Keep configuration
   readiness, ingest delivery and application performance as separate evidence.

Alerts need a concrete condition, evaluation window, scope and response owner.
Control scrape volume, high-cardinality labels and retention using current
limits/pricing. Trace context propagation and sampling are application concerns;
agent deployment alone does not instrument an application. Dashboard presence
is not evidence of healthy workloads.

Use the dedicated Grafana-query skill when available. Do not install/configure
MCP or repair IAM during a query. Audit Logs are excluded from implicit broad
inspection; follow the explicit audit workflow when requested. Log export,
alert creation, agent installation and journal reconfiguration are writes.

## Official references

- [Service signal coverage](https://docs.nebius.com/observability/services)
- [Agents](https://docs.nebius.com/observability/agents)
- [Kubernetes agent](https://docs.nebius.com/observability/agents/nebius-o11y-agent)
- [Journald labels](https://docs.nebius.com/observability/logs/journald)
- [Alerts](https://docs.nebius.com/observability/alerts)
- [Metrics queries](https://docs.nebius.com/observability/metrics/prometheus)
- [Logs query language](https://docs.nebius.com/observability/logs/query-language)
- [Tracing](https://docs.nebius.com/observability/tracing)
- [Audit Logs](https://docs.nebius.com/audit-logs)
