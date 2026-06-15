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
reused, missing state is created, and mismatched existing MCP config is
reported for an explicit replacement decision instead of being duplicated.

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

## Files

- `SKILL.md`: runtime trigger, workflow, guardrails, and output contract.
- `references/setup-guide.md`: detailed vendor-backed commands, endpoint
  table, Codex config snippets, and troubleshooting notes.
- `scripts/ensure-local-config.sh`: check/apply helper for idempotent local
  shell defaults and Codex MCP registration.
- `scripts/run-nebius-grafana-mcp.sh`: local wrapper that refreshes a Nebius
  IAM token into `GRAFANA_TOKEN_FILE`, maps it to the supported Grafana MCP
  auth environment, and starts `mcp-grafana` read-only by default.
- `scripts/test-run-nebius-grafana-mcp.sh`: local fake-command smoke test for
  wrapper token export modes and token-file permissions.
- `agents/openai.yaml`: UI metadata for the skill.

## Vendor Evidence

The setup guide links to the official Grafana MCP docs, Grafana provisioning
docs, and Nebius Observability/IAM docs that support the commands and endpoint
values. Re-check those sources before changing command syntax, auth headers,
or endpoint URLs.
