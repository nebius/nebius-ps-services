---
name: install-grafana-mcp-for-nebius
description: "Install, configure, validate, or repair the official Grafana MCP server for Codex against Nebius-managed Grafana using one pinned human Nebius CLI profile. Use only when the user explicitly invokes this skill to set up mcp-grafana on macOS or Linux, wire Codex MCP config, repair the human-token wrapper, or configure explicitly requested external Grafana Nebius datasources. Routine read-only multi-tenant/project/resource metrics, logs, dashboards, and traces belong to $nebius-grafana-query."
---

# Install Grafana MCP for Nebius

## Help

For `$install-grafana-mcp-for-nebius --help` or `$install-grafana-mcp-for-nebius -h`, return concise help and stop before
any workflow step. Include the purpose, invocation policy, public usage/actions,
and `-h, --help` plus only documented skill-level options; say "No additional
public flags" when none exist. For internal or coordinator-only skills, state
that boundary and that no standalone public workflow action exists. After the
selected `SKILL.md` is loaded, help is report-only: do not call any additional
tools, inspect project state, or modify files, private state, Git, or external
systems. Never
expose private helper actions or treat help as workflow authorization.

## Purpose

Set up the official Grafana MCP server so Codex can access
`https://grafana.nebius.dev/` through a validated read-only MCP connection
authenticated as one pinned human Nebius CLI user. Hand routine observability
queries to `$nebius-grafana-query`.

## Core Model

- Installing `mcp-grafana` is not enough for Codex to discover it. Codex needs
  a configured `[mcp_servers.*]` entry or `codex mcp add`.
- Do not point `GRAFANA_URL` at a Nebius read endpoint. It must be the Grafana
  base URL.
- `mcp-grafana` talks to Grafana. It can query PromQL-compatible monitoring
  datasources, such as Prometheus or VictoriaMetrics, and Loki through Grafana
  datasources, but it is not a Nebius public-read-endpoint client.
- For Nebius-managed Grafana, pin one explicitly selected human CLI profile.
  The setup helper defaults to the config-owned `nebius profile active`, proves
  `nebius iam whoami --format json` returns `.user_profile.id`, and stores a
  private profile/user binding. Never use an ambient `NEBIUS_PROFILE`, agent
  credential, project selector, or service-account profile.
- The wrapper revalidates the pinned identity before and after every
  `nebius iam get-access-token` call. It writes a profile-scoped
  `GRAFANA_TOKEN_FILE`, clears competing Nebius and Grafana authentication
  variables, and never prints the token or user ID.
- Require an `mcp-grafana` build with
  `GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE` support for Nebius-managed Grafana,
  while accounting for stdio builds through `v0.17.2` retaining the startup
  client instead of rereading rotations. Setup pre-mints a private startup
  token; the wrapper reuses it for up to one hour and renews a missing or older
  token before launching MCP. Foreground renewal may open the pinned profile's
  browser-authentication flow and is covered by the canonical bounded Codex
  startup timeout. After ten hours, background renewal stays noninteractive,
  refreshes the file, and closes MCP with a restart-required result. Update an
  older binary instead of adding an inline-token compatibility path.
- The Nebius token-refresh wrapper must only run with
  `GRAFANA_URL=https://grafana.nebius.dev/`. Use the generic Grafana
  service-account-token path for external Grafana instances so Nebius IAM tokens
  cannot be sent to a non-Nebius Grafana URL.
- For generic Grafana or Grafana Cloud, use a Grafana service-account token.
  Grafana token metadata can show expiration, but the token value cannot be
  recovered later; create a new token if the old value is missing or expired.
- The wrapper always enforces `--disable-write`, `--max-loki-log-limit 20`, and
  proxied-tool availability. It accepts only that exact canonical flag pair
  from a registration and rejects tool filters, transport weakening, and every
  other caller-supplied argument.
- Nebius service accounts and static keys are not part of the default
  Nebius-managed Grafana MCP path. They are only needed when configuring an
  external Grafana instance to call Nebius public read endpoints directly.

## Before You Act

- Re-check current official Grafana and Nebius docs before changing commands,
  endpoints, auth headers, or config snippets.
- Use `references/setup-guide.md` when you need the detailed workflow,
  endpoint table, command examples, or troubleshooting notes.
- Confirm the user wants live changes before editing `~/.codex/config.toml`,
  creating the private identity binding, running Nebius IAM writes, issuing
  static keys, or changing Grafana data sources.
- Never print, commit, or paste real Grafana tokens, Nebius access tokens,
  Nebius static keys, private endpoints, project IDs, resource IDs, or customer
  data. Persist the managed access token only in its profile-scoped,
  user-owned mode-`0600` token file. Use placeholders for tenant, project, and
  resource IDs in repo files.

## Idempotency Contract

- Treat this as an ensure workflow. Re-running the skill must inspect the
  laptop first, reuse matching state, and create only missing state.
- Invoke the setup helper as its own Bash command. Do not copy its internal
  `env -u` or `unset` operations into a parent inspection command or combine
  them with direct Nebius CLI probes. `PreToolUse` is expected to deny that
  parent command before execution; this is a command-shape coordination event,
  not an installer or credential failure. Retry the helper directly, or run
  `nebius profile active` separately without assigning or unsetting managed
  authentication variables.
- Check `command -v mcp-grafana` before installing. Do not reinstall when the
  binary is already present and responds to `mcp-grafana --help`.
- Check `codex mcp get <server-name>` before registering Codex MCP config. If
  the server exists and matches the desired wrapper-based command, read-only
  args, and expected env keys, leave it in place. If it exists but points
  at a byte-identical source or installed copy of the wrapper, treat it as
  matching state. Compare the structured `--json` values, not masked display
  placeholders. If it points somewhere else or any value differs, report the
  mismatch and update only after the user confirms replacement. Accept only a
  typed simple stdio shape and re-read it immediately before removal. Discard
  its prior command, arguments, and environment instead of replaying legacy
  values through process arguments. If the canonical add does not verify, do
  not attempt rollback; inspect the named entry and rerun apply after correcting
  the add failure. If post-write verification sees drift, leave the entry
  intact for inspection; do not delete state that may have been replaced
  concurrently.
- Treat an enabled server, no tool allow/deny lists, the canonical
  `startup_timeout_sec = 300.0`, no tool-timeout override, and no custom
  working directory as part of the canonical registration. Use `codex mcp`
  for registration fields. Because `codex mcp add` has no startup-timeout
  option, the helper atomically inserts or updates only that one setting after
  verifying the exact simple registration table. It digest-binds that
  structured check to the inspected `config.toml` bytes, rechecks the bytes
  immediately before replacement, preserves unrelated content, and fails on
  unsafe files or any concurrent change it observes.
- Keep the profile name, identity-binding path, token path, and managed Grafana
  URL in the Codex MCP registration. Do not rely on `.zshrc` or inherited
  environment state for the selected identity.
- Store each server/profile binding and token below a private mode-`0700`
  directory. Bindings and tokens are regular, non-symlinked, user-owned
  mode-`0600` files; token refresh uses a bounded-wait owner lock tied to a PID
  and process start time. Background refresh waits up to 90 seconds; foreground
  startup waits up to 210 seconds so one bounded browser login can complete
  within the 300-second MCP startup budget. Refresh recovers dead, PID-reused,
  and incomplete locks and replaces the token atomically.
- Require the registered wrapper and every path component from `HOME` to that
  file to be user-owned, non-symlinked, and not group/world writable.
- For the optional external Grafana flow, look up the service account, group,
  membership, and existing datasource state before creating anything. Do not
  issue another Nebius static key unless the user explicitly asks for rotation.

## Workflow

1. Identify the target OS, Codex surface, and whether the user wants the
   default Nebius-managed Grafana path or an external Grafana data-source
   setup. For the default path, select `--user-profile <human_profile>` or
   confirm the profile reported by `nebius profile active`. Do not ask for
   `TENANT_ID` or `PROJECT_ID`; authorization comes from the pinned human.
2. Ensure `mcp-grafana` is installed using the official binary path for macOS
   or Linux. Prefer Homebrew only when Homebrew is already available; otherwise
   use the official release binary or Go install path.
3. Run `scripts/ensure-local-config.sh --check --user-profile <profile>` first;
   use `--apply` only when the user wants local config changes. Invoke it as a
   standalone command without outer managed-auth assignments or unsets. The
   helper owns sanitizing inherited Nebius auth state for its child CLI calls,
   validates the human profile, creates the private binding, and derives
   profile-scoped paths. Check mode stays noninteractive. Apply mode may open
   browser authentication for the pinned human, pre-mints the token only in
   the private profile-scoped file, and never embeds the token in Codex
   configuration.
4. Ensure Codex MCP config contains the wrapper, pinned profile, identity path,
   token path, URL, canonical read-only arguments, and the bounded 300-second
   startup timeout. Add the timeout in place when it is the only drift. If an
   older registration is otherwise mismatched, show the exact migration and use
   `--apply --replace-existing` only after the user explicitly reviews it.
5. The wrapper validates the private binding on every token mint, requires
   token-file authentication, renews stale startup state before MCP launch,
   closes stdio after scheduled rotation with an explicit restart-required
   failure, and always starts `mcp-grafana` read-only. Complete a browser login
   if foreground renewal requests it. Do not assume Codex respawns a stopped
   stdio server in the same chat after later background rotation; start a new
   chat or restart Codex.
   Validate locally with
   `mcp-grafana --help`, `codex mcp list`, and
   `codex mcp get <server-name>`.
6. Validate readiness by listing Grafana datasources. Do not retain the
   operational query workflow here. After the server is ready, route metrics,
   logs, dashboards, and trace-tool questions to `$nebius-grafana-query`.
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

## Query Handoff

This skill owns setup readiness, not user outcome queries. Once datasource
listing succeeds, tell the user to ask the observability question normally or
invoke `$nebius-grafana-query`. Do not keep a legacy query path in this
installer.

## Learning Loop

When using this skill, capture durable, reusable, public-safe learnings
in the narrowest appropriate surface only when the task contract allows source edits.
For read-only/report-only work, or when a learning is not public-safe,
evidence-backed, in scope, or free of unverified/vendor-specific claims, do not
edit skill sources; report that it was skipped. Do not capture secrets, private
URLs, customer data, raw logs, or one-off local state.

## Validation

Run safe static and local checks first:

```bash
mcp-grafana --help
install-grafana-mcp-for-nebius/scripts/ensure-local-config.sh \
  --check --user-profile <human_profile>
install-grafana-mcp-for-nebius/scripts/test-run-nebius-grafana-mcp.sh
python3 -B install-grafana-mcp-for-nebius/scripts/test_grafana_skill_contract.py
codex mcp list
codex mcp get <server-name>
```

Live setup validation should list Grafana datasources and stop. Run operational
PromQL, LogQL, dashboard, or trace-tool queries only through
`$nebius-grafana-query` with an explicitly safe target.

## Output Contract

Report:

- Grafana and Nebius docs checked.
- Install and config commands run.
- Whether setup reused existing state or created/updated anything.
- Which human profile was pinned, without reporting its user ID.
- Whether the private identity binding, Codex config, and token-file refresh
  were checked or changed.
- Whether Nebius IAM writes, static-key issuance, or Grafana data-source
  changes were intentionally skipped because they are external-Grafana-only.
- Which Grafana datasources and MCP tools were available.
- Whether MCP access was validated, and what remains unverified.
