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
token refresh wrapper, register the MCP server with Codex, and validate
readiness by listing Grafana datasources. Static-key and datasource setup is
explicitly optional for external Grafana only.

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

After Codex has restarted with the `grafana-nebius` server enabled and
datasource listing succeeds, use `$nebius-grafana-query` or ask a matching
natural-language observability question:

```text
Show GPU usage for MK8s cluster <MK8S_CLUSTER_ID> in project <PROJECT_ID> over
the last 24 hours.
```

`$nebius-grafana-query` owns datasource/label discovery, bounded PromQL and
LogQL, dashboard panel-query inspection, trace-tool availability, sanitization,
and result summaries. This installer does not retain a second operational query
path.

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
- `scripts/test_grafana_skill_contract.py`: static cross-skill ownership,
  routing, metadata, catalog, and eval assertions.
- `evals/trigger-prompts.md`: explicit setup and query-handoff examples.
- `agents/openai.yaml`: UI metadata for the skill.

## Vendor Evidence

The setup guide links to the official Grafana MCP docs, Grafana provisioning
docs, and Nebius Observability/IAM docs that support the commands and endpoint
values. Re-check those sources before changing command syntax, auth headers,
or endpoint URLs.
