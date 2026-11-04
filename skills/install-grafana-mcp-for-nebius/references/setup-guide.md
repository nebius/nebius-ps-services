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
- Grafana MCP stdio token-file reload limitation:
  <https://github.com/grafana/mcp-grafana/issues/987>
- Grafana MCP Codex client:
  <https://grafana.com/docs/grafana/latest/developer-resources/mcp/clients/codex/>
- Codex MCP configuration:
  <https://learn.chatgpt.com/docs/extend/mcp>
- Codex `config.toml` reference:
  <https://learn.chatgpt.com/docs/config-file/config-reference>
- Grafana MCP tool controls:
  <https://grafana.com/docs/grafana/latest/developer-resources/mcp/configure/enable-and-disable-tools/>
- Grafana MCP command-line flags and proxied Tempo tools:
  <https://grafana.com/docs/grafana/latest/developer-resources/mcp/configure/command-line-flags/>
  and
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
- Nebius CLI profiles and IAM identity inspection:
  <https://docs.nebius.com/cli/reference/profile>,
  <https://docs.nebius.com/cli/reference/profile/active>, and
  <https://docs.nebius.com/cli/reference/iam>
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
export NEBIUS_GRAFANA_USER_PROFILE="<human_profile>"
```

Use placeholders in docs and examples. Never write real values into a repo.

When `NEBIUS_GRAFANA_USER_PROFILE` is omitted, the setup helper uses the
config-owned profile reported by `nebius profile active`. It then requires
`nebius iam whoami --format json` to expose a human `.user_profile.id`. It does
not use `nebius profile current`, ambient `NEBIUS_PROFILE`, or an agent
credential.

Do not ask for `TENANT_ID` or `PROJECT_ID` in the default path. Managed Grafana
authorization comes from the pinned human user. `$nebius-grafana-query`
collects explicit tenant, project, and resource scopes for operational queries.

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
| Nebius-managed Grafana | Pinned human CLI identity and protected profile-scoped token file. | One-hour file-age cache heuristic at startup; bounded browser authentication only in the foreground. Best-effort noninteractive retrieval after ten hours closes stdio with status 75; restart Codex or start a new chat. Retrieval may return the same token and does not prove a new lifetime. |
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
./install-grafana-mcp-for-nebius/scripts/ensure-local-config.sh \
  --check \
  --user-profile "<human_profile>"
```

When the user has explicitly approved local config changes, apply the same
ensure logic:

```bash
./install-grafana-mcp-for-nebius/scripts/ensure-local-config.sh \
  --apply \
  --user-profile "<human_profile>"
```

The helper is intentionally conservative:

- It does not install `mcp-grafana`; it reports whether the binary is already
  present.
- It validates the selected profile as a human and creates a mode-`0600`
  profile/user binding below a profile-scoped mode-`0700` state directory.
- It derives the token path from the MCP server name and pinned profile; two
  profiles never share a token file.
- It adds the Codex MCP server only when a successful `codex mcp list --json`
  proves the named entry is absent, and rechecks absence before adding. Failed,
  malformed, or duplicate-name discovery fails closed without config writes.
- It checks registration eligibility before creating private identity state
  or renewing a token. Unapproved registration drift does not refresh even an
  old cache or create a new binding.
- It uses `codex mcp get "$MCP_SERVER_NAME" --json` to compare the exact URL,
  profile, identity path, token path, args, and command without printing those
  values. Masked human-readable output is not sufficient. For timeout-only
  repair, it digest-binds that structured read to the exact config bytes and
  rechecks the bytes immediately before atomic replacement.
- It accepts an existing Codex MCP server whose command points at a
  byte-identical executable wrapper file, for example a repo source checkout instead of
  the installed `~/.agents` copy, only when every component from `HOME` is
  owned, non-symlinked, and not group/world writable.
- It reports an old shared-token or ambient-profile registration as migration
  drift. After the exact server name, wrapper, profile, and paths are reviewed,
  `--apply --replace-existing` removes and re-adds only that named entry. The
  helper first verifies that the prior entry has a typed simple stdio shape,
  then re-reads it to reject concurrent changes. Replacement discards the old
  command, arguments, and environment values instead of replaying them through
  process arguments. If the canonical add does not verify, inspect the named
  entry and rerun apply after correcting the add failure; there is no legacy
  rollback. If structured post-write verification sees drift, the helper
  leaves that entry intact for inspection rather than deleting potentially
  concurrent state.
- Repeated check/apply on canonical state preserves config, binding, acceptable token cache,
  and private-directory metadata. A cache older than one hour is retrieved once and is then reused.
  Registration rechecks detect observed races but do not serialize independent
  installers or external config editors.

### Hook-Safe Invocation Boundary

Invoke `ensure-local-config.sh` directly as its own Bash command. Do not copy
the helper's internal `env -u` or `unset` operations into the parent command,
and do not combine those operations with a direct Nebius CLI probe. The
Nebius-auth `PreToolUse` guard is expected to deny such a parent command before
it executes. The denial changes no installer state and is a command-shape
coordination event, not an installer or credential failure.

If profile discovery is needed, run `nebius profile active` separately without
assigning or unsetting managed authentication variables, then pass the
resolved profile to the standalone helper. The helper owns cleaning inherited
auth variables for its audited child CLI calls.

## Private Human Identity State

The managed path does not depend on `.zshrc`. The setup helper writes only a
private binding and non-secret Codex MCP environment values:

```text
$HOME/.config/grafana-mcp/nebius/<server>/<profile>/
  identity  # mode 0600; profile plus expected human user ID
  token     # mode 0600; retrieved access token
```

The wrapper requires both files to share the same owned, non-symlinked,
mode-`0700` leaf directory below `HOME`. It compares the binding with
`iam whoami` before and after every token retrieval. If the named profile resolves to
another human, a service account, or anonymous auth, it fails without replacing
the binding. Create and select a distinct human profile instead.

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
./install-grafana-mcp-for-nebius/scripts/ensure-local-config.sh \
  --check \
  --user-profile "<human_profile>"
codex mcp get grafana-nebius
```

The wrapper:

- requires `nebius` and `mcp-grafana` on `PATH`;
- refuses any `GRAFANA_URL` other than `https://grafana.nebius.dev/`, because
  the wrapper authenticates with a Nebius IAM token;
- requires `NEBIUS_GRAFANA_USER_PROFILE`,
  `NEBIUS_GRAFANA_IDENTITY_FILE`, and the profile-scoped
  `GRAFANA_TOKEN_FILE` registered by the setup helper;
- clears project selectors, ambient Nebius tokens/profiles/credentials,
  impersonation state, and competing Grafana credentials for every validation
  and retrieval;
- validates `.user_profile.id` against the binding before and after
  `nebius iam get-access-token`;
- serializes refreshes per token path, waits up to 90 seconds in the background
  or 210 seconds during foreground startup for a live owner, binds lock
  metadata to both PID and process start time, recovers dead, PID-reused, and
  old incomplete locks, writes atomically, and fails if ownership or mode
  `0600` cannot be enforced;
- requires the installed `mcp-grafana` binary to advertise
  `GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE`, uses a one-hour file-age cache heuristic
  without a network call, retrieves the token before launching MCP when the cache
  is missing or older, permits browser authentication only for that foreground
  retrieval's initial identity check, then retrieves and revalidates noninteractively
  against the authenticated session, and rejects older inline-only builds;
- attempts scheduled retrieval after 10 hours, retries failures after 60, 300, and
  900 seconds, then stops stdio after success with a restart-required nonzero
  result; it also stops MCP with failure if retries exhaust or the worker exits
  unexpectedly, and never assumes same-chat automatic recovery;
- terminates refresh timers and removes its exact temporary token and owned
  lock when MCP exits during a refresh;
- always enforces `--disable-write --max-loki-log-limit 20`, rejects every
  noncanonical caller argument, and keeps proxied Tempo tools enabled.

Pin a specific human profile through the setup helper:

```bash
./install-grafana-mcp-for-nebius/scripts/ensure-local-config.sh \
  --apply \
  --user-profile "<human_profile>"
```

Do not set `NEBIUS_PROFILE` for this workflow. The wrapper intentionally removes
it and passes the pinned profile explicitly.

## Post-Setup Readiness

After Codex is restarted with the `grafana-nebius` MCP server enabled, validate
setup by listing Grafana datasources:

```text
Use the grafana-nebius MCP server to list Grafana datasources.
```

Successful datasource listing completes the installer readiness check. Routine
datasource exploration, PromQL, LogQL, dashboard panel-query inspection, and
trace-tool availability belong to `$nebius-grafana-query`. Do not keep a
second operational query cookbook in this setup guide.

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

`mcp-grafana` sends bearer authentication to the Grafana API. Upstream supports
`GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE`, but its generic per-request reload claim
must be qualified by transport. Source review of `v2.1.1` confirms that
[stdio constructs its context once](https://github.com/grafana/mcp-grafana/blob/v2.1.1/cmd/mcp-grafana/main.go#L1193-L1204).
The [stdio context and HTTP middleware](https://github.com/grafana/mcp-grafana/blob/v2.1.1/mcpgrafana.go)
use different client lifecycles; upstream issue
[#987](https://github.com/grafana/mcp-grafana/issues/987) tracks the limitation.
The wrapper closes stdio after scheduled retrieval so a new process loads the
file. Generic Grafana retains its separate service-account-token configuration.

Nebius documents a [12-hour IAM token lifetime](https://docs.nebius.com/iam/authorization/access-tokens).
Do not infer remaining lifetime from file mtime or assume `get-access-token`
forces issuance. A successful retrieval may return identical bytes. The
one-hour startup cache and ten-hour schedule are best-effort heuristics, not
expiry checks or guarantees, including across laptop suspend/resume. Browser
sign-in is needed when the pinned CLI session cannot authenticate; this is not
a guaranteed prompt every twelve hours or verified silent refresh forever.

Keep the protected file (`0600` within a `0700` directory); no Keychain is
required. [`mcp-remote`](https://github.com/geelen/mcp-remote) bridges to an HTTP
MCP server and owns its OAuth storage. It cannot turn a Grafana web/API URL into
an OAuth MCP endpoint or refresh the Nebius CLI's credentials. A remote gateway
would be a separate deployment and is outside this local setup.

### Nebius-Managed Grafana

For `https://grafana.nebius.dev/`, use the wrapper and private binding created
by the setup helper. Do not call `nebius iam get-access-token` through ambient
auth or register a raw token directly. Nebius access tokens expire after
12 hours; `--print-config` reports whether the installed MCP build will use
token-file mode or is unsupported, without printing credentials.

Use `scripts/test-run-nebius-grafana-mcp.sh` for local wrapper validation. It
uses fake commands and private temporary state rather than a live user token.

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
./install-grafana-mcp-for-nebius/scripts/ensure-local-config.sh \
  --check \
  --user-profile "<human_profile>"
```

If the server exists and uses the desired wrapper, pinned profile, binding,
token path, URL, and exact read-only arguments, leave it in place. A
byte-identical wrapper is accepted only from installed skill roots or the same
skill-relative path in a trusted Git checkout below the current user's home.
If an older entry lacks the profile/binding or uses another command, inspect the
reported migration and replace only after explicit approval:

```bash
./install-grafana-mcp-for-nebius/scripts/ensure-local-config.sh \
  --apply \
  --replace-existing \
  --user-profile "<human_profile>"
```

If the server is missing, use the helper. For inspection only, the registration
fields it gives to `codex mcp add` are equivalent to:

```bash
export GRAFANA_URL="https://grafana.nebius.dev/"
export NEBIUS_GRAFANA_USER_PROFILE="<human_profile>"
export NEBIUS_GRAFANA_IDENTITY_FILE="$HOME/.config/grafana-mcp/nebius/grafana-nebius/<human_profile>/identity"
export GRAFANA_TOKEN_FILE="$HOME/.config/grafana-mcp/nebius/grafana-nebius/<human_profile>/token"
export WRAPPER_PATH="<path_to_skill>/scripts/run-nebius-grafana-mcp.sh"

codex mcp add "$MCP_SERVER_NAME" \
  --env GRAFANA_URL="$GRAFANA_URL" \
  --env GRAFANA_TOKEN_FILE="$GRAFANA_TOKEN_FILE" \
  --env NEBIUS_GRAFANA_IDENTITY_FILE="$NEBIUS_GRAFANA_IDENTITY_FILE" \
  --env NEBIUS_GRAFANA_USER_PROFILE="$NEBIUS_GRAFANA_USER_PROFILE" \
  -- "$WRAPPER_PATH" --disable-write --max-loki-log-limit 20
```

Do not use that standalone command as the complete setup: it cannot persist the
required startup timeout. The setup helper uses `codex mcp add` for registration
fields and then verifies every structured field through
`codex mcp get --json`. Codex does not expose a pre-start command or a
`codex mcp add` startup-timeout option. The registered wrapper is therefore the
prelaunch retrieval boundary, and the helper atomically sets only
`startup_timeout_sec = 300.0` in the exact simple server table. It preserves
unrelated `config.toml` content, digest-binds its structured check to the
inspected bytes, rechecks those bytes immediately before replacement, and
refuses symlinks, wrong ownership, duplicate tables/settings, or an observed
concurrent file change.

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
args = ["--disable-write", "--max-loki-log-limit", "20"]
startup_timeout_sec = 300.0
env = { GRAFANA_URL = "https://grafana.nebius.dev/", GRAFANA_TOKEN_FILE = "/private/profile/token", NEBIUS_GRAFANA_IDENTITY_FILE = "/private/profile/identity", NEBIUS_GRAFANA_USER_PROFILE = "<human_profile>" }
```

Codex uses `mcp_servers` with an underscore. Restart Codex after changing the
config.

## Validation Prompt

After local checks pass and Codex is restarted, ask for one safe readiness
check:

```text
Use the grafana-nebius MCP server to list Grafana data sources.
```

After readiness succeeds, use `$nebius-grafana-query` for operational
observability questions.

## Common Failure Modes

- `server not found`: verify `mcp-grafana` is on `PATH` for the Codex process.
- Installed but not listed: binary installation does not register an MCP server
  with Codex; run `codex mcp add` or add `[mcp_servers.<name>]` manually.
- Codex config ignored: verify `~/.codex/config.toml` TOML syntax and the
  `mcp_servers` key.
- `connection closed: initialize response`: inspect the phase/code on wrapper
  stderr first. The message means the process closed before initialization;
  it does not identify authentication as the cause. Validate local config and
  private state with check mode, then address the reported phase. Keep the
  canonical `startup_timeout_sec = 300.0`; complete browser sign-in only if
  foreground setup opens it. Never put command substitution or tokens in TOML.
- Nebius-managed Grafana auth failure: rerun the helper in check mode, verify
  the pinned profile is still a human user, and confirm the binding and token
  paths remain private. Never fall back to an agent token.
- Token refresh lock timeout before authentication: an interrupted startup can
  leave an empty `owner-pid.tmp` without an owner record. The wrapper recovers
  this stale, private provisional state after the incomplete-lock grace period.
  Populated PID files, unsafe entries, or unknown contents are preserved; inspect
  their owner before recovery instead of deleting the lock indiscriminately.
- Identity binding mismatch: create or reauthenticate a distinct human profile;
  do not overwrite the existing binding to accept a changed principal.
- Old shared-token registration: review and run
  `--apply --replace-existing --user-profile <human_profile>`, then restart
  Codex.
- Generic Grafana auth failure: verify `GRAFANA_URL` is the Grafana base URL
  and the token file contains a Grafana service-account token, not a Nebius
  static key.
- External Grafana data source auth failure: validate the Nebius service
  account group membership, static-key service `OBSERVABILITY`, and whether the
  data source expects `Bearer <static_token>` or the raw static key.
- Write tools exposed unexpectedly: repair the wrapper/registration. The
  canonical wrapper rejects attempts to disable read-only enforcement.
- Missing project data, Prometheus/Loki query tools, or trace tools: hand the
  operational diagnosis to `$nebius-grafana-query`; keep setup repair here only
  when the server configuration or authentication is the cause.

## Diagnostic And Readiness Contract

Diagnostics use `[grafana-mcp phase=<phase> code=<code>]` on stderr; stdout is
reserved for MCP protocol traffic (or explicit helper reporting modes). Raw CLI
stderr, browser authorization URLs and child log payloads are suppressed.
Child categories are hints, not authoritative HTTP responses. A child diagnostic
alone does not establish failure; validate initialization and a bounded read.

| Phase / code | Meaning and next action |
| --- | --- |
| `config / CONFIG_INVALID`, `COMMAND_MISSING` | Fix local configuration, registration or missing dependencies before authentication. |
| `private-state / PRIVATE_STATE_INVALID` | Inspect ownership, modes, paths and binding format; do not print credential contents. |
| `identity / AUTH_FOREGROUND` | Foreground identity validation may open the browser; complete sign-in if requested. |
| `identity / AUTH_REQUIRED`, `IDENTITY_INVALID`, `IDENTITY_MISMATCH` | Identity validation failed, returned a nonhuman identity or differs from the binding. Inspect the pinned profile; never silently rebind. `AUTH_REQUIRED` alone does not distinguish network failure from expired login. |
| `lock / LOCK_UNSAFE`, `LOCK_TIMEOUT`, `LOCK_FAILED`, `LOCK_RECOVERED` | Unsafe, busy or recovered token-retrieval coordination state; inspect exact ownership before intervention. |
| `token / TOKEN_CACHE_RETRIEVAL`, `TOKEN_CACHE_REUSED`, `TOKEN_RETRY`, `TOKEN_RETRIEVAL_FAILED`, `TOKEN_PAYLOAD_INVALID` | Cache retrieval or payload result, not issuance/expiry evidence. |
| `child-startup / CHILD_STARTING`, `CHILD_STARTUP_FAILED` | Launch attempted or local binary/capability check failed; initialization is still a separate gate. |
| `child / UPSTREAM_401`, `UPSTREAM_403`, `UPSTREAM_TIMEOUT`, `CHILD_DIAGNOSTIC` | Sanitized child log category; confirm against a bounded MCP tool response. |
| `child-exit / CHILD_EXIT` | Natural child exit; wrapper preserves the numeric exit status, including zero. |
| `restart / RESTART_REQUIRED` | Scheduled retrieval completed; wrapper exits 75 after stopping its child. Start a new chat or restart Codex. |
| `restart / TOKEN_RETRIEVAL_EXHAUSTED`, `REFRESH_WORKER_FAILED` | Background retrieval/supervision failed; wrapper stops MCP and exits nonzero. Foreground recovery may need sign-in. |

Readiness requires initialization and a successful bounded datasource listing.
Report an observed HTTP 401 as rejected authentication, HTTP 403 as denied
access (do not trigger login loops), and timeout as network/service uncertainty.
Local initialization failure is a separate outcome. Never swap identities,
expand access, or claim the original warning's cause from a later successful
read. Controlled restart tests, browser reauthentication and natural twelve-hour
operation are separate evidence lanes.
