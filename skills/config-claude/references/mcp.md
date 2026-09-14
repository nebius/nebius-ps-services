# Optional Claude MCP Setup

Read only when MCP inspection or configuration was requested. The catalog in
`assets/mcp-servers.json` is a selection menu, never a default installation set.
It preserves the public provider choices and pinned stdio versions already
reviewed in config-codex; it is not a claim that those pins are the latest.
Verify the selected provider's current instructions before changing a pin.

## Reconciliation

1. Identify selected servers and prerequisites without launching them: `npx`
   for Context7/Playwright, Docker for Terraform, `uvx` for MarkItDown; HTTP
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
   `${CONTEXT7_API_KEY}` and `${GITHUB_TOKEN}` literal in saved configuration.
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

## Provider References

- [Context7 environment configuration](https://context7.com/docs/resources/developer)
- [Playwright MCP](https://github.com/microsoft/playwright-mcp)
- [Terraform MCP](https://github.com/hashicorp/terraform-mcp-server)
- [MarkItDown MCP](https://github.com/microsoft/markitdown/tree/main/packages/markitdown-mcp)
- [Microsoft Learn MCP](https://learn.microsoft.com/en-us/training/support/mcp)
- [GitHub MCP](https://github.com/github/github-mcp-server)
- [OpenAI developer documentation MCP](https://developers.openai.com/resources/docs-mcp)

The structural checker can optionally inspect the selected native application
JSON using `--mcp-config PATH --require-mcp NAME`. It reads the file in memory
and reports presence/shape only. It never starts a server or prints raw values.
