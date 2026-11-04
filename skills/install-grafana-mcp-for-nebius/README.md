# Install Grafana MCP for Nebius

This skill guides Codex through installing the official Grafana MCP server,
wiring it into Codex, and connecting it to Nebius-managed Grafana at
`https://grafana.nebius.dev/` as one pinned human Nebius CLI user so Codex can
query authorized metrics, logs, and traces through Grafana datasources.

## Design

The skill keeps a strict boundary between four credentials and token surfaces:

- Nebius IAM access token: retrieved through one pinned, validated human CLI
  profile and used by MCP for Nebius-managed Grafana. Nebius documents a
  12-hour lifetime, but retrieval can return a cached token. Setup stores it
  in a protected file. Startup reuses a file aged at most one hour, otherwise
  retrieves it before launch. File mtime proves neither issuance nor expiry.
  Foreground identity validation can open browser authentication; retrieval
  and the second identity check are noninteractive. A best-effort ten-hour
  timer retrieves again and closes stdio with restart-required status 75,
  even if the token bytes are unchanged. This schedule does not guarantee
  stopping before expiry, especially across laptop sleep. Start a new chat or
  restart Codex; same-chat automatic recovery is not assumed.
- Grafana service-account token: used only for generic Grafana, Grafana Cloud,
  or another external Grafana instance.
- Nebius Observability static key: used only when configuring an external
  Grafana datasource to call Nebius public read endpoints directly.
- Nebius CLI identity: selected explicitly during setup, bound privately to its
  expected human user ID, and revalidated on every token retrieval. Ambient agent
  profiles and credentials are cleared.

Interrupted refresh initialization can leave an empty `owner-pid.tmp` before
the owner record is published. Once the incomplete-lock grace period expires,
the wrapper recovers this exact state only when the directory and file remain
private, owned, unchanged, and free of other entries. Populated PID files,
symlinks, hard links, and unknown state are preserved for inspection. As with
empty incomplete locks, the grace period assumes initialization has not been
suspended beyond that period.

The most important correction is that `GRAFANA_URL` must be a Grafana base URL,
not a Nebius Prometheus, Loki, or Tempo read URL. Installing the
`mcp-grafana` binary alone also does not make Codex discover it; Codex needs an
MCP server entry in `~/.codex/config.toml` or a `codex mcp add` registration.
The default Nebius-managed Grafana path does not create Nebius service
accounts or static keys.

The setup helper checks local registration, private-state safety and binary
capability before online identity validation, so an authentication outage cannot
hide those local defects. Diagnostics use stable phase/code fields and suppress
raw CLI and child stderr. See the setup guide for the code table.

The local setup is idempotent by design. Re-running the skill checks the
existing `mcp-grafana` binary, private profile/user binding, profile-scoped
token state, and `grafana-nebius` Codex MCP entry before changing anything.
Matching state is reused, including an entry pointing at an executable,
byte-identical trusted wrapper copy. Successful structured inventory must prove
that an entry is missing; a CLI failure or malformed response never authorizes
an add. Registration eligibility is checked before creating private identity
state or renewing a token, so unapproved registration drift leaves that state
unchanged. Structured registration values are compared without
printing them, including the command, enabled state, tool filters, and absence
of a tool-timeout override. The canonical registration has a 300-second startup
timeout so bounded browser authentication can finish before MCP initialization.
The helper sets only this timeout atomically because `codex mcp add` has no
timeout option. Missing state is created, timeout-only drift is repaired in
place, and other mismatched registration state requires the explicit
`--apply --replace-existing` migration. The helper accepts only a typed simple
stdio shape, binds the structured timeout check to a SHA-256 digest of the
inspected config bytes, and rechecks those bytes immediately before atomic
replacement. An observed concurrent change is rejected. The helper discards
prior command, argument, and environment values rather than replaying them
through process arguments. If the canonical add does not verify, inspect the
named entry and rerun apply after correcting the add failure; no legacy
registration is restored. Failed post-write verification leaves the
unverifiable entry intact instead of risking deletion of another process's
update.

Repeated check/apply on canonical state preserves the config, identity binding,
acceptable token cache, and private-directory metadata. Caches older than one hour are retrieved once and are
then reused. The helper rechecks absence before adding and rechecks inspected
values before modifying existing entries. These checks detect observed races;
they do not serialize independent installers or external config editors.

Run the setup helper as a standalone Bash command. Do not inline its internal
managed-auth environment cleanup into a parent inspection command or combine
that cleanup with direct `nebius` probes. The local `PreToolUse` hook is
expected to deny that parent command before execution. That denial changes no
installer state and indicates command-shape enforcement, not broken
credentials; run profile discovery separately and invoke the helper directly.

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

The skill will select a human CLI profile, check for `mcp-grafana`, create a
private profile binding, configure the `grafana-nebius` MCP server when missing,
and validate by listing datasources. It keeps the wrapper bound to
`https://grafana.nebius.dev/`; use a generic Grafana service-account-token setup
for any external Grafana instance.

After Codex has restarted with the `grafana-nebius` server enabled and
datasource listing succeeds, use `$nebius-grafana-query` or ask a matching
natural-language observability question:

```text
Show GPU usage for MK8s cluster <MK8S_CLUSTER_ID> in project <PROJECT_ID> over
the last 24 hours.
```

`$nebius-grafana-query` owns datasource/label discovery, explicit
tenant/project/resource fan-out, bounded PromQL and LogQL, dashboard panel-query
inspection, Tempo tools and guarded trace fallback, sanitization, and result
summaries. This installer does not retain a second operational query path.

## Files

- `SKILL.md`: runtime trigger, workflow, guardrails, and output contract.
- `references/setup-guide.md`: detailed vendor-backed commands, endpoint
  table, Codex config snippets, and troubleshooting notes.
- `scripts/ensure-local-config.sh`: check/apply helper that validates and pins a
  human CLI profile, manages its private binding, and converges Codex MCP
  registration plus its bounded startup timeout without embedding a token in
  Codex configuration.
- `scripts/run-nebius-grafana-mcp.sh`: local wrapper that retrieves a Nebius
  IAM token through that profile before launch when needed, isolates and
  updates profile-scoped token state, supervises the exact child PID/start
  identity, closes stdio after scheduled retrieval with an explicit
  restart-required result, preserves natural child exits, refuses non-Nebius
  Grafana URLs, clears competing credentials, and always starts
  `mcp-grafana` read-only.
- `scripts/test-run-nebius-grafana-mcp.sh`: local fake-command smoke test for
  stock macOS Bash/Python compatibility, human identity enforcement, token
  cache age and scheduled retrieval, concurrent/stale/PID-reused/incomplete locks,
  repeated setup and retrieval convergence, failed-discovery preservation,
  rejected registration drift, canonical replacement failure, stopped-child supervision, environment
  sanitization, and private-state and wrapper-path safety.
- `scripts/test_grafana_skill_contract.py`: static cross-skill ownership,
  routing, metadata, catalog, and eval assertions.
- `scripts/test_stdio_token_lifecycle.py`: installed-binary qualification using
  loopback HTTP and dummy tokens; verifies retained stdio credentials and token
  loading after process restart. Accepts `--binary <path>`; never contacts Nebius.
- `evals/trigger-prompts.csv`: explicit setup and query-handoff examples.
- `evals/process-cases.md`: supplemental setup and query-handoff expectations.
- `agents/openai.yaml`: UI metadata for the skill.

## Vendor Evidence

The setup guide links to the official Grafana MCP docs, Grafana provisioning
docs, and Nebius Observability/IAM docs that support the commands and endpoint
values. Re-check those sources before changing command syntax, auth headers,
or endpoint URLs.
