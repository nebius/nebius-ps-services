# Setup Guide

Use this reference when executing `$install-grafana-mcp-for-nebius` and you
need exact commands, endpoint values, or troubleshooting details.

## Official Sources

- Grafana MCP install binary:
  <https://grafana.com/docs/grafana/latest/developer-resources/mcp/set-up/install-the-binary/>
- Grafana MCP authentication:
  <https://grafana.com/docs/grafana/latest/developer-resources/mcp/configure/authentication/>
- Grafana MCP upstream repository, including token-file rotation behavior:
  <https://github.com/grafana/mcp-grafana>
- Grafana MCP Codex client:
  <https://grafana.com/docs/grafana/latest/developer-resources/mcp/clients/codex/>
- Grafana MCP tool controls:
  <https://grafana.com/docs/grafana/latest/developer-resources/mcp/configure/enable-and-disable-tools/>
- Grafana MCP Prometheus and Loki guides:
  <https://grafana.com/docs/grafana/latest/developer-resources/mcp/guides/query-metrics-with-prometheus/>
  and
  <https://grafana.com/docs/grafana/latest/developer-resources/mcp/guides/query-logs-with-loki/>
- Grafana MCP proxied tools:
  <https://grafana.com/docs/grafana/latest/developer-resources/mcp/configure/proxied-tools/>
- Optional external Grafana data source provisioning and custom HTTP headers:
  <https://grafana.com/docs/grafana/latest/administration/provisioning/>
- Grafana service accounts and service account HTTP API:
  <https://grafana.com/docs/grafana/latest/administration/service-accounts/>
  and
  <https://grafana.com/docs/grafana/latest/developer-resources/api-reference/http-api/api-legacy/serviceaccount/>
- Nebius access tokens and API bearer authentication:
  <https://docs.nebius.com/iam/authorization/access-tokens>
  and
  <https://docs.nebius.com/grpc-api/auth>
- Optional external Grafana connection to Nebius metrics:
  <https://docs.nebius.com/observability/metrics/grafana>
- Optional external Grafana connection to Nebius logs:
  <https://docs.nebius.com/observability/logs/grafana>
- Optional external Grafana connection to Nebius traces:
  <https://docs.nebius.com/observability/traces/grafana>
- Optional Nebius static keys for external tools:
  <https://docs.nebius.com/iam/authorization/static-keys>
- Optional Nebius service accounts and groups for static-key setup:
  <https://docs.nebius.com/iam/service-accounts/manage>,
  <https://docs.nebius.com/iam/authorization/groups/members>,
  <https://docs.nebius.com/iam/authorization/groups/manage>, and
  <https://docs.nebius.com/iam/authorization/roles>

## Inputs

Collect these values for the default Nebius-managed Grafana MCP path:

```bash
export MCP_SERVER_NAME="grafana-nebius"
export GRAFANA_URL="https://grafana.nebius.dev/"
export GRAFANA_TOKEN_FILE="$HOME/.config/grafana-mcp/token"
export NEBIUS_GRAFANA_TOKEN_REFRESH_SECONDS="36000"
```

Use placeholders in docs and examples. Never write real values into a repo.

Ask for `PROJECT_ID` when the user wants project-scoped metric/log/trace query
help, label filtering, or resource-specific prompts. Do not ask for
`TENANT_ID` in the default path. A basic Nebius-managed Grafana API login check
does not need tenant or project values when the local Nebius CLI identity
already has access.

Collect these only for the optional external-Grafana data-source path:

```bash
export TENANT_ID="<tenant_ID>"
export PROJECT_ID="<project_ID>"
export SA_NAME="grafana-external-observability"
export GROUP_NAME="viewers"
```

## Auth Mode Decision

Choose one MCP authentication path before editing config:

| Target Grafana | Token source for MCP | Refresh behavior |
| --- | --- | --- |
| Nebius-managed Grafana at `https://grafana.nebius.dev/` | Nebius IAM access token from `nebius iam get-access-token`, written to `GRAFANA_TOKEN_FILE`. The wrapper exposes it as `GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE` when the installed binary supports token-file auth, otherwise as startup-only `GRAFANA_SERVICE_ACCOUNT_TOKEN`. | Token-file-capable builds refresh before the 12-hour Nebius token lifetime ends. Inline-only released builds require a Codex restart before token expiry. |
| Generic self-hosted Grafana or Grafana Cloud | Grafana service-account token, preferably stored in `GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE` when the installed binary supports token-file auth. Use `GRAFANA_SERVICE_ACCOUNT_TOKEN` for released binaries that lack token-file support. | Rotate by creating a new Grafana service-account token; existing token values cannot be read back |
| External Grafana data source calling Nebius read endpoints | Nebius Observability static key in the data source HTTP header | Static key lifecycle is separate from the MCP token and is not used by MCP |

`GRAFANA_URL` is always the Grafana base URL. Do not use a Nebius Prometheus,
Loki, or Tempo read endpoint as `GRAFANA_URL`.

For the default Nebius-managed Grafana path, do not create Nebius service
accounts or issue static keys. `mcp-grafana` queries Grafana, and Grafana
already owns the datasource connection details.

## Idempotent Setup Contract

Treat local setup as an ensure workflow:

1. Inspect the laptop.
2. Reuse matching state.
3. Create only missing state.
4. Report mismatched existing state before replacing it.

Use the helper in check mode before making changes:

```bash
./install-grafana-mcp-for-nebius/scripts/ensure-local-config.sh --check
```

When the user has explicitly approved local config changes, apply the same
ensure logic:

```bash
./install-grafana-mcp-for-nebius/scripts/ensure-local-config.sh --apply
```

The helper is intentionally conservative:

- It does not install `mcp-grafana`; it reports whether the binary is already
  present.
- It updates only a managed shell-startup block and exact matching legacy
  `GRAFANA_URL` or `GRAFANA_TOKEN_FILE` exports.
- It adds the Codex MCP server only when `codex mcp get "$MCP_SERVER_NAME"`
  cannot find it.
- It accepts an existing Codex MCP server whose command points at a
  byte-identical wrapper file, for example a repo source checkout instead of
  the installed `~/.agents` copy.
- It does not remove or replace an existing Codex MCP server. If an existing
  server uses the wrong command or environment, inspect it and replace it only
  after the user confirms.

## Shell Defaults

When the user asks to persist local shell defaults, write only non-secret
values to `~/.zshrc` and keep them in this managed block:

```bash
# >>> grafana-mcp-for-nebius >>>
export GRAFANA_URL="https://grafana.nebius.dev/"
export GRAFANA_TOKEN_FILE="$HOME/.config/grafana-mcp/token"
# <<< grafana-mcp-for-nebius <<<
```

If the block already exists, replace the block instead of appending another
one. If old standalone exports already exist with the same desired values,
normalize them into the managed block. If standalone exports exist with
different values, stop and ask whether the user wants to preserve a custom
setup or replace it.

Do not put `GRAFANA_SERVICE_ACCOUNT_TOKEN`, Nebius access tokens, or Nebius
static keys in `.zshrc`. The wrapper maps `GRAFANA_TOKEN_FILE` to the
authentication environment supported by the installed `mcp-grafana` binary.

## Install `mcp-grafana`

Check before installing:

```bash
command -v mcp-grafana
mcp-grafana --help
```

If the binary is already present and `mcp-grafana --help` works, do not
reinstall it.

macOS or Linux with Homebrew:

```bash
brew install mcp-grafana
mcp-grafana --help
```

Linux without Homebrew:

- Download the official release archive for the platform and place the
  extracted `mcp-grafana` binary in a directory on `PATH`; or
- build from source with Go:

```bash
GOBIN="$HOME/go/bin" go install github.com/grafana/mcp-grafana/cmd/mcp-grafana@latest
export PATH="$HOME/go/bin:$PATH"
mcp-grafana --help
```

## Nebius-Managed Grafana Token Refresh

For `https://grafana.nebius.dev/`, prefer the bundled wrapper:

```bash
./install-grafana-mcp-for-nebius/scripts/run-nebius-grafana-mcp.sh --print-config
./install-grafana-mcp-for-nebius/scripts/run-nebius-grafana-mcp.sh --refresh-token-only
```

The wrapper:

- requires `nebius` and `mcp-grafana` on `PATH`;
- refuses any `GRAFANA_URL` other than `https://grafana.nebius.dev/`, because
  the wrapper authenticates with a Nebius IAM token;
- refreshes `nebius iam get-access-token` into `GRAFANA_TOKEN_FILE`;
- writes the token file with mode `0600` and does not print the token;
- prefers `GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE="$GRAFANA_TOKEN_FILE"` when the
  installed `mcp-grafana` binary advertises token-file support;
- falls back to `GRAFANA_SERVICE_ACCOUNT_TOKEN` for released binaries that only
  support inline tokens, replacing any inherited inline token with the
  refreshed Nebius token;
- refreshes periodically while `mcp-grafana` runs only when token-file support
  is available; inline fallback users must restart Codex before the Nebius
  token expires; and
- starts `mcp-grafana --disable-write` by default.

If a non-default Nebius CLI profile is needed:

```bash
export NEBIUS_PROFILE="<profile_name>"
```

Because Nebius access tokens are valid for 12 hours, keep the refresh interval
below that lifetime. The default is 10 hours.

## Query Through Nebius-Managed Grafana

After Codex is restarted with the `grafana-nebius` MCP server enabled, the
first runtime step is datasource discovery:

```text
Use grafana-nebius to list Grafana datasources and identify PromQL-compatible
monitoring, Loki logging, and Tempo tracing datasources available for my
Nebius project.
```

Nebius-managed Grafana may expose monitoring as a Prometheus datasource or as a
PromQL-compatible datasource such as `victoriametrics-datasource`. Use the UID
returned by datasource discovery instead of assuming a fixed datasource name.

For project-scoped metric exploration, discover labels before running a metric
query:

```text
Use grafana-nebius to list label names for the discovered monitoring datasource
UID <monitoring_datasource_uid> over the last 30 minutes. Identify labels that
look like project, resource, cluster, instance, namespace, or node identifiers.
Do not return raw series data.
```

Then discover bounded values for the project and resource labels:

```text
Use grafana-nebius to list up to 20 values for label <project_label> in
datasource <monitoring_datasource_uid> over the last 30 minutes, filtered only
as needed to find project <project_ID>.
```

```text
Use grafana-nebius to list up to 20 values for label <resource_label> in
datasource <monitoring_datasource_uid> over the last 30 minutes, filtered by
<project_label>="<project_ID>" and, if known, a narrow resource name or
resource type.
```

After labels and metric names are known, run a short, resource-filtered query:

```text
Use grafana-nebius to run an instant PromQL query in datasource
<monitoring_datasource_uid> for metric <metric_name>, filtered by
<project_label>="<project_ID>" and <resource_label>="<resource_ID>". Limit the
result to the matching resource and summarize only the latest value.
```

For a short range query, keep the window and step bounded:

```text
Use grafana-nebius to run a PromQL range query in datasource
<monitoring_datasource_uid> over the last 15 minutes with a 60 second step:
<metric_name>{<project_label>="<project_ID>", <resource_label>="<resource_ID>"}.
Return a compact summary, not every sample.
```

For logs, query only through a discovered Loki datasource and start with label
discovery:

```text
Use grafana-nebius to list Loki label names in datasource <loki_datasource_uid>
over the last 30 minutes. Identify the project/workspace/bucket and resource
labels needed for project <project_ID>.
```

Then run bounded LogQL, such as a count or a very small log limit:

```text
Use grafana-nebius to run a LogQL count_over_time query in datasource
<loki_datasource_uid> over the last 15 minutes, filtered by the discovered
project/workspace label for <project_ID> and the resource label for
<resource_ID>. Do not return raw log lines unless I ask for them.
```

Grafana MCP has documented Prometheus and Loki query tools. For traces, first
list available MCP tools and inspect the discovered Tempo datasource. Do not
promise direct TraceQL or `tempo_*` tools unless the configured server actually
exposes them.

## External Grafana Only: Nebius Service Account Flow

Nebius Observability docs require a service account for observability services
and a tenant group with at least the `viewer` role.

Use this section only when configuring self-hosted Grafana or Grafana Cloud to
call Nebius public read endpoints directly. Skip it for
`https://grafana.nebius.dev/`.

Read-only discovery:

```bash
nebius iam service-account get-by-name \
  --name "$SA_NAME" \
  --format json

nebius iam group get-by-name \
  --name "$GROUP_NAME" \
  --parent-id "$TENANT_ID" \
  --format json
```

If the service account does not exist and the user has explicitly approved live
Nebius IAM changes, create it in the current Nebius CLI project context:

```bash
export SA_ID="$(nebius iam service-account create \
  --name "$SA_NAME" \
  --format json | jq -r '.metadata.id')"
```

If the service account already exists:

```bash
export SA_ID="$(nebius iam service-account get-by-name \
  --name "$SA_NAME" \
  --format json | jq -r '.metadata.id')"
```

Resolve the group:

```bash
export GROUP_ID="$(nebius iam group get-by-name \
  --name "$GROUP_NAME" \
  --parent-id "$TENANT_ID" \
  --format json | jq -r '.metadata.id')"
```

Check membership before writing:

```bash
nebius iam group-membership list-members \
  --parent-id "$GROUP_ID" \
  --format json | jq -e --arg sa_id "$SA_ID" \
  '.items[]? | select(.metadata.id == $sa_id or .id == $sa_id)'
```

If membership is missing and live IAM changes are approved:

```bash
nebius iam group-membership create \
  --parent-id "$GROUP_ID" \
  --member-id "$SA_ID"
```

### Custom Group Option

Prefer the default tenant `viewers` group unless the user asks for a dedicated
group. If a custom group is needed, create it only after confirming tenant or
project scope:

```bash
export GROUP_ID="$(nebius iam group create \
  --parent-id "$TENANT_ID" \
  --name "$GROUP_NAME" \
  --format json | jq -r '.metadata.id')"

nebius iam access-permit create \
  --parent-id "$GROUP_ID" \
  --resource-id "$TENANT_ID" \
  --role viewer
```

Nebius docs show tenant-level `viewer` as the Observability prerequisite. If a
project-scoped custom group is used instead, validate each Grafana data source
with "Save and test" before presenting the setup as complete.

## External Grafana Only: Issue Nebius Static Key

Issue a static key only after the service account and group membership are
ready:

```bash
nebius iam static-key issue \
  --name "<name_for_the_key>" \
  --account-service-account-id "$SA_ID" \
  --service=OBSERVABILITY
```

Copy only the `token` value into the target secret store or a one-time local
shell variable. Do not echo the token in logs or write it into repo files.

Static keys are service-specific and cannot grant more access than the service
account has through its groups.

Do not pass this static key to `mcp-grafana`. It belongs only in the external
Grafana datasource HTTP header.

## External Grafana Only: Nebius Read Endpoints

Configure these as Grafana data sources:

| Signal | Grafana data source | URL |
| --- | --- | --- |
| Metrics | Prometheus | `https://read.monitoring.api.nebius.cloud/projects/<project_ID>/service-provider/prometheus` |
| Logs | Loki | `https://read.logging.api.nebius.cloud/projects/<project_ID>` |
| Traces | Tempo | `https://read.tracing.api.nebius.cloud/projects/<project_ID>/tempo` |

Use a custom HTTP header named `Authorization` for the Nebius static key.
Current Nebius docs explicitly show `Bearer <static_token>` for Tempo. The
metrics and logs Grafana pages say to use the static key value, while other
Nebius logging docs use bearer-token fields. For a provisioned setup, start
with:

```text
Authorization: Bearer <static_token>
```

If Prometheus or Loki "Save and test" fails with an authentication error,
retry the exact Nebius page wording for that data source by using the raw
static key as the header value, then record which form passed.

Grafana provisioning can keep the header value in `secureJsonData`:

```yaml
apiVersion: 1

datasources:
  - name: Nebius Metrics
    type: prometheus
    access: proxy
    url: https://read.monitoring.api.nebius.cloud/projects/<project_ID>/service-provider/prometheus
    jsonData:
      httpHeaderName1: Authorization
    secureJsonData:
      httpHeaderValue1: Bearer ${NEBIUS_OBSERVABILITY_STATIC_TOKEN}

  - name: Nebius Logs
    type: loki
    access: proxy
    url: https://read.logging.api.nebius.cloud/projects/<project_ID>
    jsonData:
      httpHeaderName1: Authorization
    secureJsonData:
      httpHeaderValue1: Bearer ${NEBIUS_OBSERVABILITY_STATIC_TOKEN}

  - name: Nebius Traces
    type: tempo
    access: proxy
    url: https://read.tracing.api.nebius.cloud/projects/<project_ID>/tempo
    jsonData:
      httpHeaderName1: Authorization
    secureJsonData:
      httpHeaderValue1: Bearer ${NEBIUS_OBSERVABILITY_STATIC_TOKEN}
```

Use this YAML only as a shape. Place it in the Grafana provisioning location
for the target deployment, and keep `NEBIUS_OBSERVABILITY_STATIC_TOKEN` outside
the repo.

## MCP Token Sources

`mcp-grafana` sends bearer authentication to the Grafana API. Current Grafana
docs support both `GRAFANA_SERVICE_ACCOUNT_TOKEN` and
`GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE`, and prefer the file form because the
server can read the file fresh on every request and pick up rotations without
restart. The latest released/Homebrew `mcp-grafana` may lag those docs; when an
installed binary does not advertise token-file support, use inline
`GRAFANA_SERVICE_ACCOUNT_TOKEN` and restart Codex before the 12-hour Nebius
token expires.

### Nebius-Managed Grafana

For `https://grafana.nebius.dev/`, use the wrapper to put a refreshed Nebius
IAM access token in `GRAFANA_TOKEN_FILE`. Do not try to recover an existing
Grafana service-account token value. Nebius access tokens are renewable through
the local Nebius CLI and expire after 12 hours. Run `--print-config` to see
whether the local binary will use token-file mode or inline-env fallback.

Read-only API probe shape:

```bash
python3 - <<'PY'
import subprocess
import urllib.request

token = subprocess.check_output(
    ["nebius", "iam", "get-access-token"], text=True
).strip()
request = urllib.request.Request(
    "https://grafana.nebius.dev/api/health",
    headers={"Authorization": f"Bearer {token}"},
)
with urllib.request.urlopen(request, timeout=20) as response:
    print(response.status)
PY
```

Never print the token. Use a throwaway token file for wrapper tests when you do
not want to touch the default user token file:

```bash
tmp_token_file="$(mktemp)"
GRAFANA_TOKEN_FILE="$tmp_token_file" \
  ./install-grafana-mcp-for-nebius/scripts/run-nebius-grafana-mcp.sh \
  --refresh-token-only
rm -f "$tmp_token_file"
```

### Generic or External Grafana

For self-hosted Grafana, Grafana Cloud, or any external Grafana instance,
create a Grafana service-account token with the minimum RBAC needed for the
enabled MCP tools. For read-only observability queries, the service account
needs enough Grafana RBAC to read and query the relevant data sources, such as
`datasources:read` and `datasources:query` for Prometheus and Loki data source
UIDs. Dashboard read tools require dashboard/folder read scopes. Keep write
tools disabled unless the user wants mutations.

Grafana service-account tokens can have no expiration by default, or an
expiration if one was set. The HTTP API can create new tokens and expose token
metadata such as expiration status, but it does not recover an old token value.
If the token file is missing or the token is expired, create a new token and
replace the file.

Store the generic Grafana token outside the repo:

```bash
mkdir -p "$(dirname "$GRAFANA_TOKEN_FILE")"
printf '%s' '<grafana_service_account_token>' > "$GRAFANA_TOKEN_FILE"
chmod 600 "$GRAFANA_TOKEN_FILE"
```

## Register MCP in Codex

Preferred Nebius-managed Grafana registration uses the wrapper as the MCP
server command. First inspect existing state:

```bash
export MCP_SERVER_NAME="grafana-nebius"
codex mcp get "$MCP_SERVER_NAME"
```

If the server exists and points to the desired wrapper command, leave it in
place. A byte-identical wrapper is accepted only from the installed skill roots
or from the same skill-relative path inside a Git source checkout under the
current user's home directory, and only when the file and nearby parent
directories are not group- or world-writable. If it exists but points to a
different command, an untrusted byte-identical copy, a different URL, or a
token source, do not add a second server with the same purpose. Ask before
replacing it:

```bash
codex mcp remove "$MCP_SERVER_NAME"
```

Then re-add it with the desired wrapper command.

If the server is missing, resolve the script path from the installed skill
folder and add it:

```bash
export GRAFANA_URL="https://grafana.nebius.dev/"
export GRAFANA_TOKEN_FILE="$HOME/.config/grafana-mcp/token"
export WRAPPER_PATH="<path_to_skill>/scripts/run-nebius-grafana-mcp.sh"

codex mcp add "$MCP_SERVER_NAME" \
  --env GRAFANA_URL="$GRAFANA_URL" \
  --env GRAFANA_TOKEN_FILE="$GRAFANA_TOKEN_FILE" \
  -- "$WRAPPER_PATH" --disable-write
```

Generic Grafana read-only registration for token-file-capable builds:

```bash
codex mcp add "$MCP_SERVER_NAME" \
  --env GRAFANA_URL="$GRAFANA_URL" \
  --env GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE="$GRAFANA_TOKEN_FILE" \
  -- mcp-grafana --disable-write
```

Inline token registration is supported by Grafana docs. Use it when the
installed binary lacks token-file support; otherwise prefer the token-file
shape above:

```bash
codex mcp add "$MCP_SERVER_NAME" \
  --env GRAFANA_URL="$GRAFANA_URL" \
  --env GRAFANA_SERVICE_ACCOUNT_TOKEN="<grafana_service_account_token>" \
  -- mcp-grafana --disable-write
```

Manual TOML shape:

```toml
[mcp_servers.grafana-nebius]
command = "<path_to_skill>/scripts/run-nebius-grafana-mcp.sh"
args = ["--disable-write"]
env = { GRAFANA_URL = "https://grafana.nebius.dev/", GRAFANA_TOKEN_FILE = "/path/to/token" }
startup_timeout_ms = 20000
tool_timeout_ms = 120000
```

Codex uses `mcp_servers` with an underscore. Restart Codex after changing the
config.

## Validation Prompts

After local checks pass and Codex is restarted, ask for safe read-only checks:

```text
Use the grafana-nebius MCP server to list Grafana data sources.
```

```text
Use the Grafana MCP server to run a small PromQL query against a discovered Nebius monitoring datasource.
```

```text
Use the Grafana MCP server to run a small LogQL query against a discovered Nebius Loki datasource.
```

For traces, first list available MCP tools. Do not promise `tempo_*` tools
unless they appear. Grafana MCP loads Tempo-specific tools only when proxied
Tempo MCP support is available through the configured Tempo data source.

## Common Failure Modes

- `server not found`: verify `mcp-grafana` is on `PATH` for the Codex process.
- Installed but not listed: binary installation does not register an MCP server
  with Codex; run `codex mcp add` or add `[mcp_servers.<name>]` manually.
- Codex config ignored: verify `~/.codex/config.toml` TOML syntax and the
  `mcp_servers` key.
- Nebius-managed Grafana auth failure: refresh the local Nebius CLI login,
  rerun the wrapper, and verify `GRAFANA_TOKEN_FILE` is readable by the Codex
  process.
- Generic Grafana auth failure: verify `GRAFANA_URL` is the Grafana base URL
  and the token file contains a Grafana service-account token, not a Nebius
  static key.
- Missing Nebius project data: verify the local Nebius identity has access to
  the project and that the target Grafana datasource actually contains the
  requested project's data.
- External Grafana data source auth failure: validate the Nebius service
  account group membership, static-key service `OBSERVABILITY`, and whether the
  data source expects `Bearer <static_token>` or the raw static key.
- Prometheus/Loki tools missing: verify those categories were not disabled.
- Write tools exposed unexpectedly: add `--disable-write` and restart Codex.
- Trace tools missing: treat as expected until the Tempo datasource exposes
  proxied Tempo MCP tools.
