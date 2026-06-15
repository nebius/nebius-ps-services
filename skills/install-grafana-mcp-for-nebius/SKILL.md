---
name: install-grafana-mcp-for-nebius
description: "Install and configure the official Grafana MCP server for Codex against Nebius-managed Grafana. Use when Codex needs to set up mcp-grafana on macOS or Linux, wire Codex MCP config, refresh Nebius-managed Grafana IAM token files, query metrics/logs through Grafana Prometheus/Loki datasources, check whether trace tools are available, or troubleshoot Grafana MCP access to Nebius observability data. Only use Nebius static-key/data-source setup when the user explicitly asks to connect external Grafana to Nebius read endpoints."
---

# Install Grafana MCP for Nebius

## Purpose

Set up the official Grafana MCP server so Codex can inspect
`https://grafana.nebius.dev/` and query Nebius Observability data through
datasources that are already available in that Grafana instance.

## Core Model

- Installing `mcp-grafana` is not enough for Codex to discover it. Codex needs
  a configured `[mcp_servers.*]` entry or `codex mcp add`.
- Do not point `GRAFANA_URL` at a Nebius read endpoint. It must be the Grafana
  base URL.
- `mcp-grafana` talks to Grafana. It can query Prometheus and Loki through
  Grafana datasources, but it is not a Nebius public-read-endpoint client.
- For Nebius-managed Grafana at `https://grafana.nebius.dev/`, use a refreshed
  Nebius IAM access token in `GRAFANA_TOKEN_FILE`. Prefer
  `GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE` when the installed `mcp-grafana` binary
  supports it, because the server can reread rotated tokens. For released
  binaries that only support `GRAFANA_SERVICE_ACCOUNT_TOKEN`, the wrapper must
  export the refreshed token inline at startup; restart Codex before the
  12-hour Nebius token expires.
- For generic Grafana or Grafana Cloud, use a Grafana service-account token.
  Grafana token metadata can show expiration, but the token value cannot be
  recovered later; create a new token if the old value is missing or expired.
- Nebius service accounts and static keys are not part of the default
  Nebius-managed Grafana MCP path. They are only needed when configuring an
  external Grafana instance to call Nebius public read endpoints directly.

## Before You Act

- Re-check current official Grafana and Nebius docs before changing commands,
  endpoints, auth headers, or config snippets.
- Use `references/setup-guide.md` when you need the detailed workflow,
  endpoint table, command examples, or troubleshooting notes.
- Confirm the user wants live changes before editing `~/.codex/config.toml`,
  editing shell startup files, running Nebius IAM writes, issuing static keys,
  or changing Grafana data sources.
- Never print, persist, commit, or paste real Grafana tokens, Nebius access
  tokens, Nebius static keys, private endpoints, or customer data. Use
  placeholders for tenant and project IDs in repo files.

## Idempotency Contract

- Treat this as an ensure workflow. Re-running the skill must inspect the
  laptop first, reuse matching state, and create only missing state.
- Check `command -v mcp-grafana` before installing. Do not reinstall when the
  binary is already present and responds to `mcp-grafana --help`.
- Check `codex mcp get <server-name>` before registering Codex MCP config. If
  the server exists and matches the desired wrapper-based command, read-only
  args, and expected env keys, leave it in place. If it exists but points
  somewhere else, report the mismatch and update only after the user confirms
  replacement.
- Use a managed shell-startup block for non-secret defaults. Replace that block
  when values change; do not append duplicate `GRAFANA_URL` or
  `GRAFANA_TOKEN_FILE` exports.
- The token refresh wrapper is safe to run repeatedly: it writes a fresh token
  to a temporary file, atomically replaces `GRAFANA_TOKEN_FILE`, and keeps mode
  `0600`.
- For the optional external Grafana flow, look up the service account, group,
  membership, and existing datasource state before creating anything. Do not
  issue another Nebius static key unless the user explicitly asks for rotation.

## Workflow

1. Identify the target OS, Codex surface, and whether the user wants the
   default Nebius-managed Grafana path or an external Grafana data-source
   setup. For the default path, do not ask for `TENANT_ID`; ask for
   `PROJECT_ID` only when the user wants project-scoped metric/log/trace query
   help.
2. Ensure `mcp-grafana` is installed using the official binary path for macOS
   or Linux. Prefer Homebrew only when Homebrew is already available; otherwise
   use the official release binary or Go install path.
3. For Nebius-managed Grafana, ensure non-secret shell defaults and run
   `scripts/run-nebius-grafana-mcp.sh`. Use
   `scripts/ensure-local-config.sh --check` first; use `--apply` only when the
   user wants local config changes. The wrapper refreshes
   `nebius iam get-access-token` into `GRAFANA_TOKEN_FILE`, maps it to
   `GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE` when supported, falls back to
   startup-only `GRAFANA_SERVICE_ACCOUNT_TOKEN` for released binaries that lack
   token-file support, and starts `mcp-grafana --disable-write` unless
   different args are supplied.
4. Ensure Codex MCP config exists with the wrapper for Nebius-managed Grafana.
   Never embed token values in config. Do not call `codex mcp add` blindly when
   `codex mcp get <server-name>` already succeeds.
5. Validate locally with `mcp-grafana --help`, `codex mcp list`, and
   `codex mcp get <server-name>`.
6. Validate live access by listing Grafana datasources first. If Prometheus or
   Loki datasources are present, run a small PromQL or LogQL query through
   Grafana MCP. For traces, first list available MCP tools and do not promise
   direct Tempo querying unless `tempo_*` proxied tools or an equivalent
   supported panel-query path is available.
7. Use the external Grafana static-key flow only when the user explicitly asks
   to configure self-hosted Grafana or Grafana Cloud to call Nebius read
   endpoints directly.

## Optional External Grafana Static-Key Flow

Use this flow only when the user explicitly wants to connect an external
Grafana instance to Nebius public read endpoints. It is not needed for the
default `https://grafana.nebius.dev/` MCP path.

- Treat `TENANT_ID`, `PROJECT_ID`, `SA_NAME`, and `GROUP_NAME` as inputs for
  external Grafana Nebius IAM/static-key/data-source setup.
- Default `GROUP_NAME` to the tenant `viewers` group because Nebius
  Observability docs require at least the tenant `viewer` role.
- First look up the service account and group. Create missing resources only
  when the user explicitly asked for live cloud changes and the tenant/project
  were confirmed.
- If the default `viewers` group is unavailable or the user wants a dedicated
  group, create a custom group and access permit only after confirming the
  desired scope. Tenant-scoped viewer access is the documented baseline for
  Observability; narrower project-scoped groups must be verified with
  Grafana "Save and test" before claiming they work.
- Check existing group membership before creating it. Treat "already exists"
  or an already-listed member as success.
- Issue a Nebius `OBSERVABILITY` static key only after the service account and
  group membership are ready, then put that static key in the external Grafana
  datasource HTTP header. Reuse an existing valid key from the user's secret
  store when available; issue a new static key only for first setup or explicit
  rotation. Do not pass the Nebius static key to `mcp-grafana`.

## Trace Tool Caveat

Nebius docs verify a Tempo data source URL for viewing traces in Grafana.
Grafana MCP docs verify trace-specific MCP tools only through proxied Tempo MCP
tools when the Tempo backend exposes that MCP server. Do not claim direct
Nebius trace querying through `mcp-grafana` until the configured server lists
usable `tempo_*` proxied tools, or until a current Nebius doc explicitly says
its Tempo endpoint supports the Grafana Tempo MCP proxy path.

## Validation

Run safe static and local checks first:

```bash
mcp-grafana --help
install-grafana-mcp-for-nebius/scripts/ensure-local-config.sh --check
install-grafana-mcp-for-nebius/scripts/run-nebius-grafana-mcp.sh --print-config
install-grafana-mcp-for-nebius/scripts/test-run-nebius-grafana-mcp.sh
install-grafana-mcp-for-nebius/scripts/run-nebius-grafana-mcp.sh --refresh-token-only
codex mcp list
codex mcp get <server-name>
```

Live validation should list Grafana datasources first, then run a small
PromQL/LogQL query only through a datasource available in the target Grafana.

## Output Contract

Report:

- Grafana and Nebius docs checked.
- Install and config commands run.
- Whether setup reused existing state or created/updated anything.
- Whether Codex config edits and token-file refresh were run.
- Whether Nebius IAM writes, static-key issuance, or Grafana data-source
  changes were intentionally skipped because they are external-Grafana-only.
- Which Grafana datasources and MCP tools were available.
- Whether MCP access was validated, and what remains unverified.
