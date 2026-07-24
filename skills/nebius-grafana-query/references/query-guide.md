# Nebius Grafana Query Guide

Use this reference after `nebius-grafana-query` triggers and the
`grafana-nebius` MCP server is available.

## Official Sources

- Grafana MCP Prometheus queries:
  <https://grafana.com/docs/grafana/latest/developer-resources/mcp/guides/query-metrics-with-prometheus/>
- Grafana MCP Loki queries:
  <https://grafana.com/docs/grafana/latest/developer-resources/mcp/guides/query-logs-with-loki/>
- Grafana MCP Codex client:
  <https://grafana.com/docs/grafana/latest/developer-resources/mcp/clients/codex/>
- Grafana MCP proxied tools:
  <https://grafana.com/docs/grafana/latest/developer-resources/mcp/configure/proxied-tools/>

Re-check these sources before changing tool names, supported query behavior, or
trace-tool claims.

## MCP Flow

`nebius-grafana-query` is a workflow skill. `grafana-nebius` is the configured
MCP server. Grafana owns the datasource connections.

```text
User request
  -> Codex selects the query skill
  -> Codex calls a grafana-nebius MCP tool
  -> the MCP server calls Grafana or a datasource API
  -> the MCP server returns data to Codex
  -> Codex sanitizes and summarizes the result
```

Implicit skill invocation does not install or configure the MCP server. When
the tools are unavailable or fail during startup/authentication, stop and
direct the user to invoke `$install-grafana-mcp-for-nebius` explicitly.

## Datasource Discovery

List datasources first. Select by the returned UID and observed capability:

- Prometheus or another PromQL-compatible datasource for metrics;
- Loki or another explicitly supported LogQL-compatible datasource for logs;
- Tempo for trace datasource discovery, subject to exposed proxied tools.

Do not assume a fixed datasource name, UID, project label, resource label, or
metric prefix.

## Metrics

1. List metric names over a short window, filtered by a user keyword when
   possible.
2. List label names for the selected metric or datasource.
3. List a bounded number of values for likely project and resource labels.
4. Run an instant query for current state or a range query for trends.
5. Filter with both project and resource selectors when those dimensions
   exist. Choose a range step that keeps the response compact.

Example request:

```text
Show average and peak GPU utilization by node for MK8s cluster
<MK8S_CLUSTER_ID> in project <PROJECT_ID> over the last 24 hours. State the
metric and datasource used and call out nodes with missing data.
```

## Logs

1. Discover Loki label names and bounded values for the requested scope.
2. Start with an aggregate, statistics call, or `count_over_time` query.
3. Retrieve log lines only after the selector is proven narrow and the user
   explicitly requests examples.
4. Return at most 20 sanitized examples. Omit authorization data, secrets,
   unrelated identifiers, and large embedded payloads.

When the user omits a time range, use the last hour. If the request lacks an
authoritative project or resource filter and could cross projects, ask for the
missing scope instead of running a broad query.

## Dashboards And Traces

Dashboard queries can reveal the actual metric, datasource UID, template
variables, and selector shape used by an existing panel. Substitute only
user-authorized project/resource values.

For traces, inspect the Tempo datasource and available proxied tools. Do not
claim direct TraceQL or `tempo_*` access unless those tools are present. Missing
proxied tools means querying is unavailable through this MCP configuration; it
does not prove that the datasource contains no traces.

## Failure Routing

| Failure | Response |
| --- | --- |
| `grafana-nebius` tools unavailable | Stop and request explicit `$install-grafana-mcp-for-nebius`. |
| Server startup or authentication failure | Stop and route repair to the installer skill. |
| No compatible datasource | Report discovered datasource types and stop. |
| No data for the exact scope | Report the selectors and time range checked; do not call it an auth failure. |
| Prometheus/Loki category missing | Report the unavailable capability and route server configuration to the installer. |
| Tempo tools missing | Report traces as unavailable through the current MCP tool set. |
| Write requested | Stop; do not widen this read-only skill. |

## Result Shape

Return a compact Markdown summary containing:

- project, resource, and time scope;
- datasource UID and type;
- metric/query and aggregation used;
- per-resource result summary;
- missing-data or partial-window coverage;
- raw-log limit and sanitization applied;
- tool or setup limitations.
