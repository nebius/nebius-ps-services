# Install Grafana MCP for Nebius

This skill guides Codex through installing the official Grafana MCP server,
wiring it into Codex, and connecting it to Nebius-managed Grafana at
`https://grafana.nebius.dev/` so Codex can query metrics and logs through
Grafana datasources.

## Design

The skill keeps a strict boundary between four credentials and token surfaces:

- Nebius IAM access token: a 12-hour token refreshed by the local Nebius CLI
  and used by MCP for Nebius-managed Grafana at `https://grafana.nebius.dev/`.
  The wrapper prefers token-file auth when the installed `mcp-grafana` supports
  it, and falls back to startup-only inline token auth for released binaries
  that do not.
- Grafana service-account token: used only for generic Grafana, Grafana Cloud,
  or another external Grafana instance.
- Nebius Observability static key: used only when configuring an external
  Grafana datasource to call Nebius public read endpoints directly.
- Nebius CLI identity: used by the wrapper to refresh the Nebius IAM token.

The most important correction is that `GRAFANA_URL` must be a Grafana base URL,
not a Nebius Prometheus, Loki, or Tempo read URL. Installing the
`mcp-grafana` binary alone also does not make Codex discover it; Codex needs an
MCP server entry in `~/.codex/config.toml` or a `codex mcp add` registration.
The default Nebius-managed Grafana path does not create Nebius service
accounts or static keys.

The local setup is idempotent by design. Re-running the skill should check the
existing `mcp-grafana` binary, managed shell-startup block, token file, and
`grafana-nebius` Codex MCP entry before changing anything. Matching state is
reused, including an existing Codex MCP entry that points at a byte-identical
source or installed wrapper copy from a trusted skill source path. Missing state
is created, and mismatched existing MCP config is reported for an explicit
replacement decision instead of being duplicated.

## Workflow

Use the skill when a user wants Codex to set up `mcp-grafana` on macOS or
Linux, register it in Codex, and connect it to Nebius metrics, logs, or traces.
The runtime instructions install the official binary, configure the Nebius IAM
token refresh wrapper, register the MCP server with Codex, discover Grafana
datasources, and run safe read-only validation. Static-key and datasource setup
is explicitly optional for external Grafana only.

Live Nebius IAM changes and static-key issuance are intentionally outside the
default path. The skill documents an idempotent external-Grafana pattern, but
it does not run it unless the user explicitly asks for that setup.

## How To Use

Invoke the skill explicitly:

```text
$install-grafana-mcp-for-nebius install Grafana MCP for Nebius-managed Grafana
and validate read-only access.
```

The skill will check for `mcp-grafana`, configure the `grafana-nebius` MCP
server when missing, refresh the local Nebius-managed Grafana token file, and
validate by listing datasources. It keeps the wrapper bound to
`https://grafana.nebius.dev/`; use a generic Grafana service-account-token setup
for any external Grafana instance.

After Codex has restarted with the `grafana-nebius` server enabled, ask Codex
to discover datasources before querying metrics:

```text
Use grafana-nebius to list Grafana datasources. Identify the PromQL-compatible
monitoring datasource UID, the Loki datasource UID, and whether Tempo tools are
available for project <PROJECT_ID>.
```

To check metrics for one Nebius project resource, start by discovering labels
and metric names. Label names differ by datasource and service, so do not guess
them:

```text
Use grafana-nebius to list label names for monitoring datasource
<MONITORING_DATASOURCE_UID> over the last 30 minutes. Identify labels that look
like project, resource, cluster, instance, namespace, node, or service
identifiers. Do not return raw series data.
```

```text
Use grafana-nebius to list metric names in monitoring datasource
<MONITORING_DATASOURCE_UID> matching <RESOURCE_OR_SERVICE_PREFIX> over the last
30 minutes, limited to 20 names.
```

Then run a bounded query with both project and resource filters:

```text
Use grafana-nebius to run an instant PromQL query in datasource
<MONITORING_DATASOURCE_UID> for <METRIC_NAME>, filtered by
<PROJECT_LABEL>="<PROJECT_ID>" and <RESOURCE_LABEL>="<RESOURCE_ID>". Return a
compact summary of the latest value for that resource only.
```

For a recent trend, keep the time range short:

```text
Use grafana-nebius to run a PromQL range query in datasource
<MONITORING_DATASOURCE_UID> over the last 15 minutes with a 60 second step:
<METRIC_NAME>{<PROJECT_LABEL>="<PROJECT_ID>", <RESOURCE_LABEL>="<RESOURCE_ID>"}.
Summarize min, max, and latest values instead of returning every sample.
```

For logs tied to the same resource, discover Loki labels first, then use a small
aggregate or low limit:

```text
Use grafana-nebius to list Loki label names in datasource <LOKI_DATASOURCE_UID>
over the last 30 minutes, then run a count_over_time query for
<PROJECT_ID>/<RESOURCE_ID> over the last 15 minutes. Do not return raw log lines
unless I explicitly ask.
```

Use placeholders in reusable docs and prompts. Do not store real project IDs,
resource IDs, raw logs, or query results in this skill source.

## Files

- `SKILL.md`: runtime trigger, workflow, guardrails, and output contract.
- `references/setup-guide.md`: detailed vendor-backed commands, endpoint
  table, Codex config snippets, and troubleshooting notes.
- `scripts/ensure-local-config.sh`: check/apply helper for idempotent local
  shell defaults and Codex MCP registration, with redacted config inspection and
  guardrails against persisted token exports.
- `scripts/run-nebius-grafana-mcp.sh`: local wrapper that refreshes a Nebius
  IAM token into `GRAFANA_TOKEN_FILE`, maps it to the supported Grafana MCP
  auth environment, refuses non-Nebius Grafana URLs, and starts `mcp-grafana`
  read-only by default.
- `scripts/test-run-nebius-grafana-mcp.sh`: local fake-command smoke test for
  wrapper token export modes and token-file permissions.
- `agents/openai.yaml`: UI metadata for the skill.

## Vendor Evidence

The setup guide links to the official Grafana MCP docs, Grafana provisioning
docs, and Nebius Observability/IAM docs that support the commands and endpoint
values. Re-check those sources before changing command syntax, auth headers,
or endpoint URLs.
