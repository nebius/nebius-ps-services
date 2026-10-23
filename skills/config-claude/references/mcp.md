# Optional Claude MCP Setup

Read only when MCP inspection or configuration was requested. The catalog in
`assets/mcp-servers.json` is a selection menu, never a default installation set.
It preserves the public provider choices and pinned stdio versions already
reviewed in config-codex; it is not a claim that those pins are the latest.
Verify the selected provider's current instructions before changing a pin.

## Reconciliation

1. Identify selected servers and prerequisites without launching them: `npx`
   for Playwright, Docker for Terraform, `uvx` for MarkItDown; HTTP
   services need native HTTP MCP support. Do not install prerequisites
   implicitly. Check required environment-variable presence without printing
   values; missing variables make that integration incomplete.
2. Inspect existing native user/local/project definitions and plugin ownership.
   Capture CLI inspection output privately in memory and extract only safe
   structural facts; commands such as `claude mcp get` can print credentials.
   Find the actual native configuration path through the installed CLI rather
   than guessing its location for a custom `CLAUDE_CONFIG_DIR`.
3. Keep an equivalent selected server unchanged. Compare transport, executable,
   arguments, endpoint and variable references structurally; a matching name
   alone is insufficient. Preserve existing custom definitions and ask about a
   concrete conflict unless the requested replacement is already authorized.
   Account for project and plugin definitions to avoid duplicate connections.
4. For an authorized missing user server, pass that catalog entry as literal
   JSON to `claude mcp add-json --scope user NAME JSON`. Use an argument array
   when invoking from code; do not interpolate JSON through a shell. Keep
   `${GITHUB_TOKEN}` literal in saved configuration when selected.
   Never expand secret values into command arguments or public files.
5. If replacing a user entry was explicitly authorized, use native scoped
   commands and verify that entry afterward. Do not restore an entire
   `.claude.json` file over Claude's concurrent application/authentication state.
   Run mutations sequentially and reinspect after each. Plugin-owned entries
   remain owned by their plugin.
6. Check native registration and `/mcp` status separately. Registration does
   not prove credentials, server startup, tools, or connection. Do not execute
   a tool with external effects merely to prove the server is connected.

Example for the explicitly selected public documentation server:

```bash
claude mcp add-json --scope user microsoftdocs \
  '{"type":"http","url":"https://learn.microsoft.com/api/mcp"}'
```

The default MCP CLI scope is local, so always specify `--scope user` for this
skill. Claude owns the corresponding application JSON and authentication;
do not put `mcpServers` into `settings.json` or copy Codex TOML fields. OAuth
and secrets stay in their native external flows. [Claude MCP](https://code.claude.com/docs/en/mcp)

## Selected removal

Remove only explicitly selected native registrations. Use
`claude mcp remove --scope user NAME` for this skill's user scope; recheck the
entry and unrelated application state afterward. Native removal of a remote
server also removes its stored OAuth tokens and client registration. Do not
reconstruct application JSON, delete provider accounts or clear shared caches.
An already-absent registration needs no rewrite. Report local credential
cleanup separately from provider-side revocation.

## Provider References

### Nebius documentation

For explicitly selected `nebiusDocs`, preserve any equivalent existing entry
and add a missing entry through the native user-scope command:

```bash
claude mcp add-json --scope user nebiusDocs \
  '{"type":"http","url":"https://docs.nebius.com/mcp"}'
```

This public endpoint needs no tenant credentials. Keep its preference in the
native `CLAUDE.md` managed block, using the template's search/retrieval-first
rule, then Tavily retrieval of official pages, then disclosed official-web
fallback and source citations. The rule may exist without
the optional integration; it does not authorize automatic installation.

Inspect registration and `/mcp` connection status, then use a bounded public
documentation search when connection verification is requested. Do not call
`submit_feedback` as a health check. Keep registration, endpoint reads and
fresh-agent routing evidence separate. The optional
`--require-mcp nebiusDocs --mcp-config PATH` check validates presence and shape,
not endpoint identity or runtime health; compare the selected URL separately.

Sources: [Claude native MCP scopes](https://code.claude.com/docs/en/mcp-quickstart)
and the [public Nebius endpoint](https://docs.nebius.com/mcp).

### Tavily OAuth

For selected Tavily registration, native login and global routing, read
[research-routing.md](research-routing.md). Use user scope, preserve native
credential ownership and verify OAuth separately from the shape-only checker.

### Other providers

- [Playwright MCP](https://github.com/microsoft/playwright-mcp)
- [Terraform MCP](https://github.com/hashicorp/terraform-mcp-server)
- [MarkItDown MCP](https://github.com/microsoft/markitdown/tree/main/packages/markitdown-mcp)
- [Microsoft Learn MCP](https://learn.microsoft.com/en-us/training/support/mcp)
- [GitHub MCP](https://github.com/github/github-mcp-server)
- [OpenAI developer documentation MCP](https://developers.openai.com/resources/docs-mcp)

The structural checker can optionally inspect the selected native application
JSON using `--mcp-config PATH --require-mcp NAME`. It reads the file in memory
and reports presence/shape only. It never starts a server or prints raw values.
