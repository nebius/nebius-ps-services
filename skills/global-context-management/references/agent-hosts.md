# Executing Agent Contract

Read this before shared workflow state, hook, identity or helper operations.
The executing agent is distinct from the product a skill configures.

| Surface | Codex | Claude Code |
| --- | --- | --- |
| Explicit skill | `$name` | `/name`, or `/skills:name` in this plugin |
| Private agent home | `CODEX_HOME`, default `~/.codex` | `CLAUDE_CONFIG_DIR`, default `~/.claude` |
| Local skill catalog | `~/.agents/skills` | `<agent-home>/skills` |
| Hook registration | `<agent-home>/hooks.json` | `hooks` in `<agent-home>/settings.json` |
| Native session source | `CODEX_THREAD_ID` | Native hook `session_id`, plus `agent_id` for workers |
| CLI helper identity | `CODEX_THREAD_ID` | Hook-bound `SKILLS_SESSION_ID` |
| Configured read-only roles | `repo_mapper`, `test_strategist`, `risk_reviewer` | `repo-mapper`, `test-strategist`, `risk-reviewer` |

`<agent-home>` is a documentation placeholder for the selected resolved home,
not a literal directory or a request to set another host's environment variable.
Shared private helper CLIs use `--agent-home`; product-specific configuration
commands may still use `--codex-home` to name their configured target. Existing
serialized field names remain stable. Shared Python helpers use `agent_runtime.agent_home()` and
`native_session_id()`. Claude hooks select `SKILLS_AGENT=claude`, preserve
native prompt identity and bind each worker separately. Never manufacture an
identity, copy an active run between agents, or redirect `CODEX_HOME` to Claude.
Keep stable protocol names such as `CODEX_NEBIUS_*`, `.codex/project-specs.json`,
branch prefixes and `codex-remediation-budget:v1`; their spelling does not
select the executing host.

Install the full catalog and reviewed hooks using the selected native plugin
or `install-skills.sh --agent codex|claude` before stateful workflows. Skills-only
`npx skills` copying does not register hooks. Single copied script skills may
need sibling owners and the shared runtime; missing dependencies fail clearly.
Source and plugin caches are source artifacts, not private runtime state.

Use only tools actually exposed by the current host. Resolve native equivalents
for read, edit, shell, planning and authorized delegation; never treat a tool
name as permission. Verify configured roles and effective restrictions before
spawning. GUI/TUI, subagent, MCP, authentication and sandbox capabilities remain
explicit prerequisites when the workflow requires them. Keep independent
verification, worker/coordinator ownership, commit claims, recovery and Stop
ordering intact. A missing required capability stops that operation without
claiming completion or substituting weaker evidence.

Help with `--help` or `-h` stops after the selected `SKILL.md` loads, before this
reference, tools or other workflow reads. Native explicit-only and internal
coordinator controls remain effective on both hosts; syntax conversion grants
no authority.

Sources: [Codex skills](https://learn.chatgpt.com/docs/build-skills),
[Claude skills](https://code.claude.com/docs/en/skills),
[Claude hooks](https://code.claude.com/docs/en/hooks).
