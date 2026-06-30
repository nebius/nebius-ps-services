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
for the outcome you need. Natural-language prompts are fine when they include
the scope, time window, and resource identifiers. Codex should use
`grafana-nebius` to discover the datasource, labels, and metric names before it
queries Grafana:

```text
Use grafana-nebius to query the GPU usage for the last 30 days for all the
nodes of this MK8s cluster:
<MK8S_CLUSTER_ID>
in project:
<PROJECT_ID>
```

For this kind of request, Codex should report the GPU-reporting nodes it finds,
the average and peak GPU utilization per node, the metric used, and whether the
cluster has data for the whole requested window.

Other useful prompts can stay just as direct:

```text
Use grafana-nebius to list the monitoring and logging datasources available in
Grafana, and tell me which ones can be used for project <PROJECT_ID>.
```

```text
Use grafana-nebius to show CPU and memory usage for the nodes in MK8s cluster
<MK8S_CLUSTER_ID> in project <PROJECT_ID> over the last 24 hours. Summarize by
node and call out missing data.
```

```text
Use grafana-nebius to check whether MK8s cluster <MK8S_CLUSTER_ID> in project
<PROJECT_ID> had any GPU XID errors in the last 7 days. Return counts by node
and GPU only.
```

```text
Use grafana-nebius to summarize recent error logs for MK8s cluster
<MK8S_CLUSTER_ID> in project <PROJECT_ID> over the last hour. Return counts and
the most relevant short examples only; do not dump raw logs.
```

When you already know the exact datasource UID, labels, or PromQL, include
them. Otherwise, let Codex discover those details through Grafana MCP and ask
for a compact result instead of raw series or logs.

## How The MCP Flow Works

`grafana-nebius` is the Codex MCP server entry for the Grafana MCP server. It is
not Grafana itself, and Grafana does not reroute user prompts to it.

The runtime flow is:

```text
User prompt
  -> Codex decides which grafana-nebius tool to call
  -> Codex runtime sends an MCP tool call to grafana-nebius
  -> grafana-nebius calls Grafana and its datasource APIs
  -> Grafana returns datasource, Prometheus-compatible, Loki, or other results
  -> grafana-nebius returns the tool result to Codex
  -> Codex summarizes the answer for the user
```

The Codex-to-MCP hop is an MCP tool/RPC call. Depending on the MCP server
configuration, that hop can run over stdio to a local process or over an HTTP
transport to a remote MCP endpoint. The MCP-to-Grafana hop is where Grafana API
and datasource API calls happen.

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
