# Selected Tavily OAuth Bridge

Tavily remains opt-in. The selected setup uses a pinned `mcp-remote` stdio
bridge to the hosted `https://mcp.tavily.com/mcp/` service. Ordinary `codex`
and `claude` launches start the bridge directly. It reuses credentials and
opens the browser when OAuth is needed; completing login should make tools
available in the same process. There is no application launcher, alias,
SessionStart login hook or package download during startup.

## Storage And Ownership

This explicitly selected Tavily mode uses private **files**, not Keychain.
If the user requires Keychain-only credentials, report that this bridge cannot
meet the requirement; do not silently downgrade, copy native tokens, substitute
API keys or invent a custom credential provider. Other integrations retain
native OAuth storage and their existing policy. Keep Codex's global credential
store keys unchanged.

Resolve the configured product's home: explicit selection then `CODEX_HOME`
or `CLAUDE_CONFIG_DIR`, respectively, then its normal default. Never substitute
one product's home variable for the other. Preserve the native CLI's inherited
profile environment when configuring the ordinary default profile. In Claude,
explicitly setting `CLAUDE_CONFIG_DIR` even to its apparent default can select
a different application/authentication profile; do not add that override merely
because the helper needs an absolute `--agent-home`. Confirm the native scope
and registration before mutation. Store the bridge under
`<agent-home>/mcp/tavily/`: a versioned `runtime/` installation and separate
`auth/mcp-remote-v1/` credentials. Directories are `0700`, credentials `0600`.
Do not read or hash credential contents for a health check. Never put state in
Git, public examples, debug logs, skill caches or a shared credential directory.

`config-codex` owns `scripts/tavily_bridge.py`, this reference and the locked
`assets/tavily-bridge/` manifests and reviewed callback patch. `config-claude` carries byte-identical mirrors;
change the owner first and verify parity. Each copied skill is self-contained
for bridge preparation. The helper never edits native configuration or logs in.

## Prepare Before Registration

Inspect installed native CLI help, the current selected registration and its
scope without printing secret values. Keep unrelated settings and servers.
Missing/foreign/custom definitions are concrete conflicts; migrate only the
selected registration within current authorization. Inspect higher-priority
project, plugin and managed definitions to avoid duplicate connections.

Use existing Node 22.20 or newer and npm, resolving their absolute executable
paths. Validate both the supplied path's ancestry and its resolved executable;
a safe target does not make a replaceable symlink in a public directory safe.
Do not install or upgrade Node implicitly. This helper uses POSIX file
controls; macOS is the live qualification target and other hosts need native
qualification. Before executing, inspect the helper's effects:

```text
python3 -B <SKILL>/scripts/tavily_bridge.py prepare \
  --agent codex|claude --agent-home <ABSOLUTE_AGENT_HOME> \
  --node <ABSOLUTE_NODE> --npm <ABSOLUTE_NPM> [--callback-port PORT]
python3 -B <SKILL>/scripts/tavily_bridge.py check \
  --agent codex|claude --agent-home <ABSOLUTE_AGENT_HOME>
```

These are helper arguments, not public skill flags. `prepare` installs the
exact locked `mcp-remote@0.15.2` release with `npm ci --ignore-scripts --omit=dev`
into an unpublished private staging directory, then applies
`callback-readiness.patch.json` only when exact upstream preimage and patched
postimage hashes match. The patch prepares callback ownership before a browser
redirect, discards only an unused follower verifier, and gives the internal
connection probe the same 360-second maximum. It preserves the upstream token,
PKCE, state, and client-registration handling. This is a locally patched pinned
release, not an unmodified upstream binary. The runtime identity covers both
manifests and the patch; changing either stages a new immutable runtime.
Preparation records integrity/provenance and publishes only after all checks. Unknown drift, partial publication,
unsafe ancestry and another preparation lock fail without replacing them.
An aligned rerun does not run npm, rewrite files or create backups. `check`
does not launch npm/Node, connect, open a browser or read credential contents.

Default callback ports are **37411 for Codex** and **37412 for Claude**.
Custom homes require an explicit distinct `--callback-port`. Reuse a home's
recorded port across sessions, including the Codex CLI and desktop client.
Separate credential stores also need separate ports because the bridge's
sign-in coordination identifies the endpoint and port. An unrelated listener
is a conflict: do not kill it or silently change the established binding.

## Native Configuration

After preparation, the helper's `configuration(home, agent)` returns the
expected public configuration from the checked installation receipt. Call it
from the reviewed skill directory, not code loaded from the target home.
Render actual absolute paths locally; catalog/template placeholders must never
be registered literally. Preserve existing longer timeouts and tool policies.

The command is the absolute Node executable. Arguments are the absolute
`node_modules/mcp-remote/dist/proxy.js` under the versioned runtime, the hosted
endpoint, the recorded callback port, then
`--transport http-only --auth-timeout 300 --silent`. Its only managed environment
entry is `MCP_REMOTE_CONFIG_DIR`, pointing to that home's private `auth` directory.
Never enable `--debug`: authentication diagnostics can contain secrets.

| Host | Selected changes |
| --- | --- |
| Codex | Patch only `[mcp_servers.tavily]` and its environment table. Remove the old HTTP transport keys. Set `startup_timeout_sec` and `tool_timeout_sec` to at least `360`, preserving larger values and unrelated tool controls. |
| Claude | Use native `claude mcp add-json --scope user tavily JSON` with a structured argument array and the rendered stdio entry. Patch only user settings `env.MCP_TIMEOUT` to at least `"360000"`. Preserve longer tool limits; if the effective tool limit is below `360000`, add a Tavily-only `timeout` override of `360000`. |

`--auth-timeout 300` configures bridge callback/coordination timers; it does
**not** enforce a five-minute deadline while waiting for a browser code. The
native client's six-minute startup maximum bounds the connection attempt.
These are maximum timeouts, not launch delays. Codex's startup setting
is Tavily-specific. Claude's startup maximum affects **all** its MCP connections;
disclose this effect when selecting setup. Settings from other scopes or shell
variables can change effective behavior and require native verification.
Preserving a longer host limit does not increase the patched bridge's fixed
360-second internal connection-probe maximum.

Do not put `mcpServers` in Claude `settings.json` or reconstruct application or
authentication JSON. A selected existing Claude HTTP entry is replaced with
native scoped remove/add commands, sequentially, rereading after each writer.
Native removal deletes that server's old local OAuth tokens and registration;
HTTP rollback may need native login again. Codex's original native credentials
are left intact. After migration, `codex mcp login tavily` is no longer the
authentication path: the stdio bridge owns its login.

## Verification And Recovery

Use the optional `--require-tavily-bridge` switch on the native
`check-local-idempotency.py`; Claude additionally needs `--mcp-config PATH` for
the exact application configuration discovered through its native CLI. The
strict check covers runtime integrity, exact wiring, directory/file modes and
timeouts. A selected Codex entry with `enabled = false` fails this check; leave
intentional disablement untouched and report it rather than silently enabling it. Ordinary checks still allow Tavily's absence; Claude's separate
`--require-mcp tavily` remains presence/shape-only. Full-home failures outside
this selected operation are not permission to bootstrap the rest of the home.

Start a fresh ordinary native client after a configuration change. A static
pass, a listed server, completed OAuth, five discovered tools, one bounded
public search, actual refresh and model routing quality are separate claims.
For cold-login proof, start the native client against an empty disposable store
before any direct bridge probes or status commands that could authenticate it.
Warm startup should reuse credentials. The source-owned macOS regression fixture
uses a loopback OAuth/MCP service and fake browser; it touches no real account.
This is a **manual macOS qualification** command, not part of the default Ubuntu
CI job. Run it for every bridge pin or patch change. The installer and strict
checker unit regressions run in the existing Codex CI suite:

```text
python3 -B <CONFIG_CODEX_SOURCE>/scripts/test_tavily_bridge_oauth.py \
  --node <ABSOLUTE_NODE> --proxy <PREPARED_RUNTIME>/node_modules/mcp-remote/dist/proxy.js
```

It covers immediate callbacks, expired/rejected concurrent sessions, immediate
follower reauthentication, healthy cached startup with the callback port occupied,
and mid-session tool recovery. It records which proxy opened the first browser,
forces its follower to reauthenticate first, and asserts successful callbacks and
exact browser/token-exchange counts. A shortened internal-probe control must fail
before delayed consent; the selected runtime must pass the same delayed case.
The control copies only code and reuses reviewed dependencies read-only; it does
not modify the installed runtime. This fixture is distinct from native/provider proof.
Exercise rejection recovery only in
isolated fixtures after quiescing their writers, never by corrupting a user's
working tokens. Qualify concurrent same-host sessions and both hosts together.

Cancellation, failed browser opening, occupied callback ports and exhausted
timeouts must fail clearly without retry loops or loss of the main client.
Use the host's native reconnect UI or a fresh launch after resolving a concrete
failure. Never clear shared caches, force logouts, revoke grants or replace
credentials merely because a documentation lookup needs Tavily. Natural expiry
and recovery in an existing session remain unverified until observed.

Back up only changing configuration bytes privately, with preimage/concurrency
checks. Roll back only the changed entry and selected timeout if still owned
by this operation. Never restore an entire Claude application-state file.
Keep bridge state unless its deletion is separately selected. Package upgrades
are explicit: review the release, lockfile and continued need for both patch operations, stage a new versioned runtime,
validate it, then change the native entry; never run `npx ...@latest` at launch.

## Sources

- [Tavily MCP](https://docs.tavily.com/documentation/mcp)
- [Codex MCP configuration](https://developers.openai.com/codex/mcp)
- [Claude MCP](https://code.claude.com/docs/en/mcp)
- [Claude environment settings](https://code.claude.com/docs/en/env-vars)
- [Pinned bridge source](https://github.com/punkpeye/mcp-remote/tree/373f7320f6afd1fdb00e0168826a3cea99102241)
